"""Time and grade the language model on requests the fast path hands over.

Sends each transcript (after the game lexicon) to Claude with the player's bound
actions and two tools, then records how long it takes to decide and whether
the decision matches tools/spike/brain_expected.toml. Nothing is pressed.

The API key comes from Windows Credential Manager:
    uv run python -m ultimo.secrets set anthropic

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
from anthropic.types.beta import BetaMessage, BetaTextBlockParam, BetaToolParam

from ultimo import secrets
from ultimo.game.install import GameInstall
from ultimo.game.keybinds import Keybinds
from ultimo.game.store import KeybindStore
from ultimo.lexicon import Lexicon

EXPECTED = Path(__file__).resolve().parents[1] / "spike" / "brain_expected.toml"
MODELS = ("claude-haiku-5-5", "claude-sonnet-5-5", "claude-opus-5-5")
# Models with server-side refusal fallback ("default" form); Haiku has none.
FALLBACK_MODELS = frozenset({"claude-sonnet-5-5", "claude-opus-5-5"})
FALLBACK_BETA = "server-side-fallback-2026-07-01"
# USD per million tokens: input, output, cache read. Cache writes cost 1.25x input.
PRICES = {
    "claude-haiku-5-5": (0.10, 0.50, 0.01),
    "claude-sonnet-5-5": (2.00, 10.00, 0.20),
    "claude-opus-5-5": (4.00, 20.00, 0.20),
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

TOOLS: list[BetaToolParam] = [
    {
        "name": "run_actions",
        "description": "Press one or more game actions, in order, using the player's own keys.",
        "eager_input_streaming": True,
        "input_schema": {
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
        },
    },
    {
        "name": "look_up",
        "description": "Look up live game data that you don't know reliably.",
        "eager_input_streaming": True,
        "input_schema": {
            "type": "object",
            "properties": {
                "topic": {
                    "type": "string",
                    "enum": ["trade", "location", "route", "ship", "mining", "other"],
                },
                "query": {"type": "string", "description": "What to look up, in plain words."},
            },
            "required": ["topic", "query"],
        },
    },
]

type Kind = Literal["actions", "look_up", "reply", "error"]


@dataclass
class Call:
    model: str
    phrase: str
    heard: str
    run: int
    kind: Kind = "error"
    actions: list[str] = field(default_factory=list[str])
    detail: str = ""
    first_block_ms: float = 0.0
    """Until the first content block of any kind (thinking counts)."""
    decision_ms: float = 0.0
    """Until the tool call or reply is complete: when Ultimo could act."""
    usage: dict[str, int] = field(default_factory=dict[str, int])
    verdict: str = ""

    @property
    def cost(self) -> float:
        price_in, price_out, price_cache = PRICES[self.model]
        u = self.usage
        return (
            u.get("input_tokens", 0) * price_in
            + u.get("cache_creation_input_tokens", 0) * price_in * 1.25
            + u.get("cache_read_input_tokens", 0) * price_cache
            + u.get("output_tokens", 0) * price_out
        ) / 1_000_000


def action_list(keybinds: Keybinds) -> str:
    lines: dict[str, str] = {}
    for action in keybinds:
        if action.bound and action.label and action.name not in lines:
            keys = " or ".join(str(i) for i in action.inputs if i.pressable)
            lines[action.name] = f"{action.name} | {action.label} | {keys}"
    return "\n".join(sorted(lines.values()))


def ask(
    client: anthropic.Anthropic, model: str, system: str, text: str, effort: str
) -> tuple[BetaMessage, float, float]:
    system_blocks: list[BetaTextBlockParam] = [
        {"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}
    ]
    fallback = model in FALLBACK_MODELS
    started = time.perf_counter()
    first_block = 0.0
    with client.beta.messages.stream(
        model=model,
        max_tokens=2048,
        system=system_blocks,
        tools=TOOLS,
        messages=[{"role": "user", "content": text}],
        output_config={"effort": effort},  # pyright: ignore[reportArgumentType]
        betas=[FALLBACK_BETA] if fallback else anthropic.omit,
        fallbacks="default" if fallback else anthropic.omit,  # pyright: ignore[reportArgumentType]
    ) as stream:
        for event in stream:
            if event.type == "content_block_start" and not first_block:
                first_block = time.perf_counter() - started
        message = stream.get_final_message()
    return message, first_block * 1000, (time.perf_counter() - started) * 1000


def classify(call: Call, message: BetaMessage, valid: set[str]) -> None:
    if message.stop_reason == "refusal":
        call.kind, call.detail = "error", "refusal"
        return
    for block in message.content:
        if block.type != "tool_use":
            continue
        # Tool input is untrusted model output (and unvalidated, with eager streaming).
        tool_input: dict[str, Any] = cast(dict[str, Any], block.input)
        if block.name == "run_actions":
            names = _string_list(tool_input.get("actions"))
            if names is None:
                call.kind, call.detail = "error", f"invalid input {json.dumps(tool_input)}"
                return
            call.kind, call.actions = "actions", names
            unknown = [a for a in names if a not in valid]
            call.detail = str(tool_input.get("say", ""))
            if unknown:
                call.detail = f"UNKNOWN ACTIONS {unknown}; {call.detail}"
            return
        if block.name == "look_up":
            call.kind = "look_up"
            call.detail = json.dumps(tool_input)
            return
    call.kind = "reply"
    call.detail = " ".join(b.text for b in message.content if b.type == "text").strip()


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

    key = secrets.get("anthropic")
    if key is None:
        raise SystemExit(
            "No Anthropic key stored. Run: uv run python -m ultimo.secrets set anthropic"
        )
    client = anthropic.Anthropic(api_key=key)

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
    lexicon = Lexicon.default()
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
                        message, call.first_block_ms, call.decision_ms = ask(
                            client, model, system, text, args.effort
                        )
                        call.usage = {
                            k: int(v or 0)
                            for k, v in message.usage.model_dump().items()
                            if k.endswith("tokens") and isinstance(v, int | None)
                        }
                        classify(call, message, valid)
                    except anthropic.APIStatusError as e:
                        call.kind, call.detail = "error", f"{e.status_code} {e.message}"
                    except anthropic.APIConnectionError as e:
                        call.kind, call.detail = "error", f"connection: {e}"
                    call.verdict = grade(call, rule, valid)
                    calls.append(call)
                    log.write(json.dumps(call.__dict__ | {"cost": call.cost}) + "\n")
                    mark = "ok " if call.verdict == "pass" else "BAD"
                    print(
                        f"{mark} {model:18} run{run} {call.decision_ms:6.0f} ms  {phrase[:40]:40}"
                        f" -> {call.kind}: {call.actions or call.detail[:60]}"
                        + ("" if call.verdict == "pass" else f"  [{call.verdict}]")
                    )

    print(
        f"\n{'model':18} {'pass':>6} {'cold ms':>8} {'warm p50':>9} {'warm p90':>9}"
        f" {'1st block':>9} {'cache hit':>9} {'$/request':>10}"
    )
    for model in args.models:
        mine = [c for c in calls if c.model == model]
        warm = sorted(c.decision_ms for c in mine if c.run > 0) or [0.0]
        cold = [c.decision_ms for c in mine if c.run == 0]
        cached = sum(c.usage.get("cache_read_input_tokens", 0) for c in mine)
        prompt = cached + sum(
            c.usage.get("input_tokens", 0) + c.usage.get("cache_creation_input_tokens", 0)
            for c in mine
        )
        print(
            f"{model:18} {sum(c.verdict == 'pass' for c in mine):>3}/{len(mine):<2}"
            f" {statistics.median(cold or [0.0]):8.0f} {statistics.median(warm):9.0f}"
            f" {warm[min(len(warm) - 1, round(0.9 * (len(warm) - 1)))]:9.0f}"
            f" {statistics.median(c.first_block_ms for c in mine):9.0f}"
            f" {cached / max(prompt, 1):9.0%} {statistics.mean(c.cost for c in mine):10.5f}"
        )
    print(f"\nTotal spent: ${sum(c.cost for c in calls):.3f}. Log: {out}")


if __name__ == "__main__":
    main()
