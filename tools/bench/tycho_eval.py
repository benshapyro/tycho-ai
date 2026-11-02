"""Grade the product brain on typed requests, through Tycho's own path (#45).

Each request goes through the game lexicon and the fast path first, exactly as
in the game; only what the fast path can't match goes to Claude, through
``tycho.brain.claude.ClaudeBrain`` and ``commands_for``. Nothing is pressed: the
grade is on the actions Tycho would press (after "confirm", for confirm-grade
ones), from the player's real keybinds.

The bar (ADR 14): zero wrong presses, and Claude's picks at p50 <= 1.5 s.
Answers to questions must answer: a reply that only points the player elsewhere
("check the wiki") fails a case marked ``answer = true``, and a case's ``mentions``
(the true numbers and names) must all be in the reply.

Ship questions go through the ship look-up by default. ``--ships table`` puts a
table of every ship in the briefing instead, and ``--ships none`` gives Claude
neither, to measure the two designs ADR 15 compares.

Uses the Anthropic key in Windows Credential Manager. About $0.0002 a request.

Usage: uv run python tools/bench/tycho_eval.py [cases.toml] [--tag ships] [--ships table]
"""

import argparse
import asyncio
import json
import logging
import re
import statistics
import time
import tomllib
from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from brain_report import GLOBAL_NEVER  # pyright: ignore[reportMissingImports, reportUnknownVariableType]

from tycho import paths, safety, settings
from tycho.app import anthropic_key, find_install, load_commands
from tycho.brain import BrainError, BrainRequest, Exchange, Reply, commands_for
from tycho.brain.claude import ClaudeBrain
from tycho.brain.lookups import Lookup, LookupResult
from tycho.commands import Command, Defer, resolve
from tycho.game.keybinds import Keybinds
from tycho.game.store import KeybindStore
from tycho.knowledge import ShipLookup, UexApi, WikiApi
from tycho.knowledge.store import ensure
from tycho.lexicon import Lexicon

CASES = Path(__file__).resolve().parents[1] / "spike" / "brain_cases.toml"
RESULTS = Path(__file__).resolve().parents[2] / "recordings" / "tycho_eval.results.jsonl"
DODGE = re.compile(
    r"\b(check|consult|look at|see)\b[^.]{0,40}\b(wiki|guide|guides|community|patch notes|"
    r"rankings|forums?|reddit|spectrum)\b|\bcan'?t (say|tell you|give you)\b",
    re.IGNORECASE,
)
SAY_WORDS = 8
"""What Tycho says after acting should be a few words: the press already happened."""
COST_GUESS = 0.0002
UNASKED = frozenset({"v_unlock_all_doors", "v_toggle_all_doorlocks"})
"""Presses that are wrong whenever the case didn't ask for them: unlocking the
doors when the player said something else leaves the ship open."""


@dataclass
class Outcome:
    heard: str
    tag: str
    route: str = ""  # "fast" or "brain"
    kind: str = ""  # "actions", "reply" or "error"
    actions: list[str] = field(default_factory=list[str])
    say: str = ""
    asks: bool = False
    """Tycho would ask "confirm" before pressing (a confirm-grade action, or travel
    the brain picked)."""
    ms: float = 0.0
    looked_up: int = 0
    """Look-ups the brain made for this request."""
    verdict: str = ""
    notes: list[str] = field(default_factory=list[str])


class _Costs(logging.Handler):
    """Picks the brain's cost estimates out of its debug log."""

    def __init__(self) -> None:
        super().__init__(logging.DEBUG)
        self.total = 0.0
        self.counted = 0

    def emit(self, record: logging.LogRecord) -> None:
        if match := re.search(r"about \$([0-9.]+)", record.getMessage()):
            self.total += float(match.group(1))
            self.counted += 1


def pressed(commands: tuple[Command, ...], keybinds: Keybinds) -> list[str]:
    """The game actions Tycho would press for these commands, setup included."""
    names: list[str] = []
    for command in commands:
        for step in command.expand():
            names += list(step.command.setup)
            resolved = resolve(step.command, keybinds)
            names.append(
                resolved.action.name if resolved.action else f"<no key: {step.command.id}>"
            )
    return names


def grade(outcome: Outcome, rule: dict[str, Any], valid: set[str]) -> str:
    never = (set(GLOBAL_NEVER) | set(rule.get("never", []))) - set(rule.get("allow", []))
    if outcome.kind == "error":
        return "error"
    asked = {action for group in rule.get("must", []) for action in group}
    if bad := (never | (UNASKED - asked)).intersection(outcome.actions):
        return f"WRONG PRESS {sorted(bad)}"
    allowed = set(rule.get("allow", [])).intersection(outcome.actions)
    if allowed and not outcome.asks:
        return f"WRONG PRESS (not confirmed first) {sorted(allowed)}"
    if invented := [a for a in outcome.actions if a not in valid]:
        return f"WRONG PRESS (no such key) {invented}"
    expect = {"reply" if kind == "look_up" else kind for kind in rule["expect"]}
    if outcome.kind not in expect:
        if outcome.kind == "actions":
            return f"WRONG PRESS (expected {'/'.join(sorted(expect))})"
        return f"expected {'/'.join(sorted(expect))}"
    if outcome.kind == "actions":
        for group in rule.get("must", []):
            if not set(group).intersection(outcome.actions):
                return f"missing {'/'.join(group)}"
    if outcome.kind == "reply" and rule.get("answer") and DODGE.search(outcome.say):
        return "dodged the question"
    if outcome.kind == "reply":
        for group in rule.get("mentions", []):
            if not any(re.search(p, outcome.say, re.IGNORECASE) for p in group):
                return f"missing {'/'.join(group)}"
    return "pass"


class _Counted:
    """A look-up that counts its calls, so the eval can say how often Claude used it."""

    def __init__(self, inner: Lookup) -> None:
        self.inner = inner
        self.calls = 0
        self.name, self.description = inner.name, inner.description
        self.input_schema = inner.input_schema

    async def prepare(self) -> None:
        await self.inner.prepare()

    async def run(self, arguments: Mapping[str, object]) -> LookupResult:
        self.calls += 1
        return await self.inner.run(arguments)


async def run_case(
    rule: dict[str, Any],
    lexicon: Lexicon,
    brain: ClaudeBrain,
    keybinds: Keybinds,
    counted: _Counted | None = None,
) -> Outcome:
    outcome = Outcome(rule["heard"], str(rule.get("tag", "")))
    text = lexicon.correct(rule["heard"]).text
    match = MATCHER.match(text)
    if not isinstance(match, Defer):
        outcome.route, outcome.kind = "fast", "actions"
        outcome.actions = pressed(match.commands, keybinds)
        outcome.asks = _asks(match.commands, outcome.actions)
        return outcome
    outcome.route = "brain"
    recent = [Exchange(said, did) for said, did in rule.get("recent", [])]
    started = time.perf_counter()
    before = counted.calls if counted else 0
    try:
        decision = await brain.decide(BrainRequest(text, keybinds, tuple(recent)))
    except BrainError as e:
        outcome.kind, outcome.notes = "error", [str(e)]
        return outcome
    finally:
        outcome.ms = (time.perf_counter() - started) * 1000
        outcome.looked_up = (counted.calls if counted else 0) - before
    if isinstance(decision, Reply):
        outcome.kind, outcome.say = "reply", decision.text
        return outcome
    outcome.kind, outcome.say = "actions", decision.say
    try:
        commands = commands_for(decision, MATCHER)
        outcome.actions = pressed(commands, keybinds)
        outcome.asks = _asks(commands, outcome.actions)
    except BrainError as e:
        outcome.kind, outcome.notes = "error", [str(e)]
    if len(decision.say.split()) > SAY_WORDS:
        outcome.notes.append(f"say line is {len(decision.say.split())} words")
    return outcome


def _asks(commands: tuple[Command, ...], actions: list[str]) -> bool:
    confirm = any(step.command.confirm for command in commands for step in command.expand())
    return confirm or bool(safety.CONFIRM_GRADE.intersection(actions))


MATCHER = load_commands()


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("cases", type=Path, nargs="?", default=CASES)
    parser.add_argument("--tag", action="append", help="only cases with this tag; repeatable")
    parser.add_argument(
        "--ships",
        choices=["lookup", "table", "none"],
        default="lookup",
        help="ship data as a look-up tool (default), a table in the briefing, or neither",
    )
    args = parser.parse_args()

    data: dict[str, Any] = tomllib.loads(args.cases.read_text("utf-8"))
    rules: list[dict[str, Any]] = data["case"]
    if args.tag:
        rules = [r for r in rules if r.get("tag") in args.tag]
    key = anthropic_key()
    if key is None:
        raise SystemExit("No Anthropic key. Run: uv run python -m tycho.credentials set anthropic")

    store = KeybindStore.load(find_install(settings.load()))
    keybinds = store.keybinds
    valid = {a.name for a in keybinds if a.bound}
    costs = _Costs()
    logging.getLogger("tycho.brain").addHandler(costs)
    logging.getLogger("tycho.brain").setLevel(logging.DEBUG)
    counted: _Counted | None = None
    reference = ""
    if args.ships != "none":
        library = await ensure(WikiApi(), paths.knowledge_dir(), concepts=UexApi())
        print(f"Ship data: {len(library)} ships for {library.patch}, as {args.ships}")
        if args.ships == "table":
            reference = "Ships and vehicles in this patch:\n" + library.table()
        else:
            counted = _Counted(ShipLookup(WikiApi(), paths.knowledge_dir(), library=library))
    brain = ClaudeBrain(
        MATCHER, api_key=key, lookups=[counted] if counted else [], reference=reference
    )
    await brain.warm(keybinds)
    lexicon = Lexicon.default()

    outcomes: list[Outcome] = []
    for rule in rules:
        outcome = await run_case(rule, lexicon, brain, keybinds, counted)
        outcome.verdict = grade(outcome, rule, valid)
        outcomes.append(outcome)
        mark = "  " if outcome.verdict == "pass" else "XX"
        what = ", ".join(outcome.actions) if outcome.kind == "actions" else outcome.kind
        what += " (asks first)" if outcome.asks else ""
        what += f" after {outcome.looked_up} look-up(s)" if outcome.looked_up else ""
        print(f"{mark} {outcome.route:5} {outcome.ms:5.0f} ms  {outcome.heard!r} -> {what}")
        if outcome.say:
            print(f"{'':16}says ({len(outcome.say.split())} words): {outcome.say}")
        if outcome.verdict != "pass" or outcome.notes:
            print(f"{'':16}{'; '.join([outcome.verdict, *outcome.notes])}")

    RESULTS.parent.mkdir(exist_ok=True)
    with RESULTS.open("a", encoding="utf-8") as f:
        f.write(json.dumps({"type": "meta", "time": time.strftime("%Y-%m-%dT%H:%M:%S")}) + "\n")
        for outcome in outcomes:
            f.write(json.dumps(asdict(outcome)) + "\n")
    _summary(outcomes, costs)


def _summary(outcomes: list[Outcome], costs: _Costs) -> None:
    passed = sum(o.verdict == "pass" for o in outcomes)
    wrong = [o for o in outcomes if o.verdict.startswith("WRONG PRESS")]
    brain = [o.ms for o in outcomes if o.route == "brain" and o.kind != "error"]
    picks = [o.ms for o in outcomes if o.route == "brain" and o.kind == "actions"]
    print(f"\n{passed}/{len(outcomes)} pass, {len(wrong)} wrong presses")
    by_tag: dict[str, list[Outcome]] = {}
    for o in outcomes:
        by_tag.setdefault(o.tag, []).append(o)
    for tag, group in sorted(by_tag.items()):
        print(f"  {tag or '(none)':12} {sum(o.verdict == 'pass' for o in group)}/{len(group)}")
    if brain:
        print(f"Brain: p50 {statistics.median(brain):.0f} ms over {len(brain)} requests", end="")
        if len(brain) >= 10:
            print(f", p90 {statistics.quantiles(brain, n=10)[-1]:.0f} ms", end="")
        print()
    if picks:
        print(f"Brain picks (acting): p50 {statistics.median(picks):.0f} ms (bar: 1500)")
    looked = [o for o in outcomes if o.looked_up]
    if looked:
        print(
            f"Look-ups: {len(looked)} requests used them, "
            f"p50 {statistics.median(o.ms for o in looked):.0f} ms"
        )
    unpriced = len(brain) - costs.counted
    print(f"Cost: about ${costs.total + max(0, unpriced) * COST_GUESS:.3f}")


if __name__ == "__main__":
    asyncio.run(main())
