"""Time and grade language models on requests the fast path hands over.

Sends each transcript (after the game lexicon) to a model with the player's
bound actions and two tools, then records how long it takes to decide and
whether the decision matches tools/spike/brain_expected.toml. Nothing is pressed.

Models are named by provider: "claude-haiku-5-5" (Anthropic), or
"cerebras:gpt-oss-120b" (any OpenAI-compatible provider in PROVIDERS).
API keys come from Windows Credential Manager:
    uv run python -m ultimo.credentials set anthropic
    uv run python -m ultimo.credentials set cerebras

Usage: uv run python tools/bench/brain_report.py recordings/<session>
    [--engine onnx-asr:nemo-parakeet-tdt-0.6b-v2:cuda] [--models claude-haiku-5-5 ...]
    [--runs 2] [--effort low]
"""

import argparse
import json
import statistics
import time
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, cast

import anthropic
import openai
from anthropic.types.beta import BetaTextBlockParam, BetaToolParam
from openai.types.chat import ChatCompletionToolParam

from ultimo import credentials
from ultimo.game.install import GameInstall
from ultimo.game.keybinds import Keybinds
from ultimo.game.store import KeybindStore
from ultimo.lexicon import Lexicon

EXPECTED = Path(__file__).resolve().parents[1] / "spike" / "brain_expected.toml"
MODELS = ("claude-haiku-5-5", "claude-sonnet-5-5", "claude-opus-5-5")
# Claude models with server-side refusal fallback ("default" form); Haiku has none.
FALLBACK_MODELS = frozenset({"claude-sonnet-5-5", "claude-opus-5-5"})
FALLBACK_BETA = "server-side-fallback-2026-07-01"
# OpenAI-compatible providers: name -> (base URL, credential name).
PROVIDERS = {"cerebras": ("https://api.cerebras.ai/v1", "cerebras")}
# USD per million tokens: input, output, cache read, cache write.
# Cerebras rates are from third-party listings (Oct 2026); check the provider's page.
PRICES = {
    "claude-haiku-5-5": (0.10, 0.50, 0.01, 0.125),
    "claude-sonnet-5-5": (2.00, 10.00, 0.20, 2.50),
    "claude-opus-5-5": (4.00, 20.00, 0.20, 5.00),
    "cerebras:gpt-oss-120b": (0.35, 0.75, 0.35, 0.35),
    "cerebras:qwen-3.8-27b": (0.99, 1.49, 0.99, 0.99),
}

INSTRUCTIONS = """\
You are Ultimo, a voice co-pilot inside the space game Star Citizen. The player \
speaks to you mid-flight; you hear a speech-to-text transcript of what they said.

The transcript may contain recognition errors, especially in game names. A note \
may list words that could be game terms; use the context to decide.

For each request, do exactly one of these:
- Press game actions with run_actions, choosing only from the action list below, \
in the order they should happen. Prefer a specific action ("Open All Doors") over \
a toggle when both are listed.
- Call look_up when the answer needs live game data: prices, locations, routes, \
distances, ship and item stats.
- Reply in one short sentence when neither fits: for example, to ask what the \
player means. You have no memory of earlier requests in this session, so \
"do that again" or "the other one" can't be acted on; ask.

Never guess at actions for an unclear request. Never eject, self-destruct, or \
engage quantum travel unless the player clearly asked for exactly that. Keep \
anything you say under 15 words: the player is flying.

The player's bound actions (name | what it does | key):
"""

RUN_ACTIONS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "actions": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Action names from the list, e.g. v_flightready.",
        },
        "say": {"type": "string", "description": "A short spoken confirmation."},
    },
    "required": ["actions", "say"],
}
LOOK_UP_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "topic": {
            "type": "string",
            "enum": ["trade", "location", "route", "ship", "mining", "other"],
        },
        "query": {"type": "string", "description": "What to look up, in plain words."},
    },
    "required": ["topic", "query"],
}
TOOL_SPECS = (
    ("run_actions", "Press one or more game actions, in order, using the player's own keys.",
     RUN_ACTIONS_SCHEMA),
    ("look_up", "Look up live game data that you don't know reliably.", LOOK_UP_SCHEMA),
)  # fmt: skip
CLAUDE_TOOLS: list[BetaToolParam] = [
    {"name": n, "description": d, "input_schema": s, "eager_input_streaming": True}
    for n, d, s in TOOL_SPECS
]
OPENAI_TOOLS: list[ChatCompletionToolParam] = [
    {"type": "function", "function": {"name": n, "description": d, "parameters": s}}
    for n, d, s in TOOL_SPECS
]

type Kind = Literal["actions", "look_up", "reply", "error"]


@dataclass
class Usage:
    input: int = 0
    output: int = 0
    cache_read: int = 0
    cache_write: int = 0


@dataclass
class Reply:
    """A provider's answer, normalised."""

    tool_calls: list[tuple[str, dict[str, Any]]]
    text: str
    refused: bool
    first_ms: float
    total_ms: float
    usage: Usage


@dataclass
class Call:
    model: str
    phrase: str
    heard: str
    run: int
    kind: Kind = "error"
    actions: list[str] = field(default_factory=list[str])
    detail: str = ""
    first_ms: float = 0.0
    """Until the first output of any kind (thinking counts). 0 if not measured."""
    decision_ms: float = 0.0
    """Until the tool call or reply is complete: when Ultimo could act."""
    usage: Usage = field(default_factory=Usage)
    verdict: str = ""

    @property
    def cost(self) -> float:
        price_in, price_out, price_read, price_write = PRICES.get(self.model, (0, 0, 0, 0))
        u = self.usage
        return (
            u.input * price_in
            + u.cache_write * price_write
            + u.cache_read * price_read
            + u.output * price_out
        ) / 1_000_000


class Brains:
    """One client per provider, created on first use."""

    def __init__(self) -> None:
        self._anthropic: anthropic.Anthropic | None = None
        self._openai: dict[str, openai.OpenAI] = {}

    def ask(self, model: str, system: str, text: str, effort: str) -> Reply:
        if ":" in model:
            provider, name = model.split(":", 1)
            return self._ask_openai(provider, name, system, text, effort)
        return self._ask_claude(model, system, text, effort)

    def _ask_claude(self, model: str, system: str, text: str, effort: str) -> Reply:
        if self._anthropic is None:
            self._anthropic = anthropic.Anthropic(api_key=_key("anthropic"))
        system_blocks: list[BetaTextBlockParam] = [
            {"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}
        ]
        fallback = model in FALLBACK_MODELS
        started = time.perf_counter()
        first = 0.0
        with self._anthropic.beta.messages.stream(
            model=model,
            max_tokens=2048,
            system=system_blocks,
            tools=CLAUDE_TOOLS,
            messages=[{"role": "user", "content": text}],
            output_config={"effort": effort},  # pyright: ignore[reportArgumentType]
            betas=[FALLBACK_BETA] if fallback else anthropic.omit,
            fallbacks="default" if fallback else anthropic.omit,  # pyright: ignore[reportArgumentType]
        ) as stream:
            for event in stream:
                if event.type == "content_block_start" and not first:
                    first = time.perf_counter() - started
            message = stream.get_final_message()
        total = time.perf_counter() - started
        calls = [
            (b.name, cast(dict[str, Any], b.input)) for b in message.content if b.type == "tool_use"
        ]
        text_out = " ".join(b.text for b in message.content if b.type == "text").strip()
        u = message.usage
        usage = Usage(
            input=u.input_tokens,
            output=u.output_tokens,
            cache_read=u.cache_read_input_tokens or 0,
            cache_write=u.cache_creation_input_tokens or 0,
        )
        refused = message.stop_reason == "refusal"
        return Reply(calls, text_out, refused, first * 1000, total * 1000, usage)

    def _ask_openai(self, provider: str, model: str, system: str, text: str, effort: str) -> Reply:
        if provider not in self._openai:
            base_url, credential = PROVIDERS[provider]
            self._openai[provider] = openai.OpenAI(api_key=_key(credential), base_url=base_url)
        client = self._openai[provider]
        started = time.perf_counter()
        response = client.chat.completions.create(
            model=model,
            max_completion_tokens=2048,
            messages=[{"role": "system", "content": system}, {"role": "user", "content": text}],
            tools=OPENAI_TOOLS,
            parallel_tool_calls=False,
            reasoning_effort=cast(Any, effort) if "gpt-oss" in model else openai.omit,
        )
        total = time.perf_counter() - started
        choice = response.choices[0]
        calls: list[tuple[str, dict[str, Any]]] = []
        for tool_call in choice.message.tool_calls or []:
            if tool_call.type != "function":
                continue
            try:
                parsed: object = json.loads(tool_call.function.arguments or "{}")
            except json.JSONDecodeError:
                parsed = {"_unparseable": tool_call.function.arguments}
            arguments = cast(dict[str, Any], parsed) if isinstance(parsed, dict) else {}
            calls.append((tool_call.function.name, arguments))
        u = response.usage
        cached = 0
        if u is not None and u.prompt_tokens_details is not None:
            cached = u.prompt_tokens_details.cached_tokens or 0
        usage = Usage(
            input=(u.prompt_tokens - cached) if u else 0,
            output=u.completion_tokens if u else 0,
            cache_read=cached,
        )
        refused = choice.finish_reason == "content_filter"
        return Reply(
            calls, (choice.message.content or "").strip(), refused, 0.0, total * 1000, usage
        )


def _key(name: str) -> str:
    key = credentials.get(name)
    if key is None:
        raise SystemExit(
            f"No {name} key stored. Run: uv run python -m ultimo.credentials set {name}"
        )
    return key


def action_list(keybinds: Keybinds) -> str:
    lines: dict[str, str] = {}
    for action in keybinds:
        if action.bound and action.label and action.name not in lines:
            keys = " or ".join(str(i) for i in action.inputs if i.pressable)
            lines[action.name] = f"{action.name} | {action.label} | {keys}"
    return "\n".join(sorted(lines.values()))


def classify(call: Call, reply: Reply, valid: set[str]) -> None:
    """Turn a reply into a kind of response. Tool input is untrusted model output."""
    if reply.refused:
        call.kind, call.detail = "error", "refusal"
        return
    for name, tool_input in reply.tool_calls:
        if name == "run_actions":
            names = _string_list(tool_input.get("actions"))
            if names is None:
                call.kind, call.detail = "error", f"invalid input {json.dumps(tool_input)}"
                return
            call.kind, call.actions = "actions", names
            call.detail = str(tool_input.get("say", ""))
            if unknown := [a for a in names if a not in valid]:
                call.detail = f"UNKNOWN ACTIONS {unknown}; {call.detail}"
            return
        if name == "look_up":
            call.kind, call.detail = "look_up", json.dumps(tool_input)
            return
    call.kind, call.detail = "reply", reply.text


def _string_list(value: object) -> list[str] | None:
    if not isinstance(value, list):
        return None
    items = cast(list[object], value)
    if not all(isinstance(item, str) for item in items):
        return None
    return [str(item) for item in items]


def grade(call: Call, rule: dict[str, Any], valid: set[str]) -> str:
    never = set(rule.get("never", []))
    if call.kind == "error":
        return "error"
    if bad := never.intersection(call.actions):
        return f"FORBIDDEN {sorted(bad)}"
    if any(a not in valid for a in call.actions):
        return "invented action"
    if call.kind not in rule["expect"]:
        return f"expected {'/'.join(rule['expect'])}"
    if call.kind == "actions":
        for group in rule.get("must", []):
            if not set(group).intersection(call.actions):
                return f"missing {'/'.join(group)}"
    return "pass"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("session", type=Path)
    parser.add_argument("--engine", default="onnx-asr:nemo-parakeet-tdt-0.6b-v2:cuda")
    parser.add_argument("--models", nargs="+", default=list(MODELS))
    parser.add_argument(
        "--runs", type=int, default=2, help="the first run per model warms the cache"
    )
    parser.add_argument("--effort", default="low", choices=["low", "medium", "high"])
    args = parser.parse_args()

    expected: dict[str, dict[str, Any]] = tomllib.loads(EXPECTED.read_text("utf-8"))
    transcripts: dict[str, str] = {}
    for path in (args.session / "results").glob("*.jsonl"):
        rows = [json.loads(line) for line in path.read_text("utf-8").splitlines()]
        if rows and rows[0].get("label") == args.engine:
            transcripts = {r["phrase"]: r["text"] for r in rows if r["type"] == "clip"}
    if not transcripts:
        raise SystemExit(f"No transcripts from {args.engine} in {args.session / 'results'}")

    keybinds = KeybindStore(GameInstall.find()).keybinds
    actions = action_list(keybinds)
    valid = {line.split(" | ")[0] for line in actions.splitlines()}
    system = INSTRUCTIONS + actions
    lexicon, brains = Lexicon.default(), Brains()
    print(f"{len(valid)} bound actions in the briefing; transcripts from {args.engine}\n")

    calls: list[Call] = []
    out = args.session / "results" / "brain.jsonl"
    with out.open("a", encoding="utf-8") as log:
        for model in args.models:
            for run in range(args.runs):
                for phrase, rule in expected.items():
                    corrected = lexicon.correct(transcripts.get(phrase, phrase))
                    text = corrected.text
                    if corrected.hints:
                        notes = "; ".join(f'"{h.heard}" may mean {h.term}' for h in corrected.hints)
                        text += f"\n\n(Note: {notes}.)"
                    call = Call(model, phrase, corrected.text, run)
                    try:
                        reply = brains.ask(model, system, text, args.effort)
                        call.first_ms, call.decision_ms, call.usage = (
                            reply.first_ms,
                            reply.total_ms,
                            reply.usage,
                        )
                        classify(call, reply, valid)
                    except (anthropic.APIStatusError, openai.APIStatusError) as e:
                        call.kind, call.detail = "error", f"{e.status_code} {e.message}"
                    except (anthropic.APIConnectionError, openai.APIConnectionError) as e:
                        call.kind, call.detail = "error", f"connection: {e}"
                    call.verdict = grade(call, rule, valid)
                    calls.append(call)
                    record = call.__dict__ | {"usage": call.usage.__dict__, "cost": call.cost}
                    log.write(json.dumps(record) + "\n")
                    mark = "ok " if call.verdict == "pass" else "BAD"
                    print(
                        f"{mark} {model:22} run{run} {call.decision_ms:6.0f} ms  {phrase[:38]:38}"
                        f" -> {call.kind}: {call.actions or call.detail[:60]}"
                        + ("" if call.verdict == "pass" else f"  [{call.verdict}]")
                    )

    print(
        f"\n{'model':22} {'pass':>6} {'cold ms':>8} {'warm p50':>9} {'warm p90':>9}"
        f" {'cache hit':>9} {'$/request':>10}"
    )
    for model in args.models:
        mine = [c for c in calls if c.model == model]
        warm = sorted(c.decision_ms for c in mine if c.run > 0) or [0.0]
        cold = [c.decision_ms for c in mine if c.run == 0]
        cached = sum(c.usage.cache_read for c in mine)
        prompt = cached + sum(c.usage.input + c.usage.cache_write for c in mine)
        print(
            f"{model:22} {sum(c.verdict == 'pass' for c in mine):>3}/{len(mine):<2}"
            f" {statistics.median(cold or [0.0]):8.0f} {statistics.median(warm):9.0f}"
            f" {warm[min(len(warm) - 1, round(0.9 * (len(warm) - 1)))]:9.0f}"
            f" {cached / max(prompt, 1):9.0%} {statistics.mean(c.cost for c in mine):10.5f}"
        )
    print(f"\nTotal spent: ${sum(c.cost for c in calls):.3f} (estimated). Log: {out}")


if __name__ == "__main__":
    main()
