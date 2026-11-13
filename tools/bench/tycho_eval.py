"""Grade the product brain on typed requests, through Tycho's own path (#45).

Each request goes through the game lexicon and the fast path first, exactly as
in the game; only what the fast path can't match goes to Claude, through
``tycho.brain.claude.ClaudeBrain`` and ``commands_for``. Nothing is pressed: the
grade is on the actions Tycho would press (after "confirm", for confirm-grade
ones), from the player's real keybinds. Routing and planning are the session's own
(``tycho.runtime.routing`` and ``planning``, #55), so the eval can't grade stale logic:
whether Tycho asks first is the safety gate's answer.

The bar (ADR 14): zero wrong presses, and Claude's picks at p50 <= 1.5 s.
Answers to questions must answer: a reply that only points the player elsewhere
("check the wiki") fails a case marked ``answer = true``, and a case's ``mentions``
(the true numbers and names) must all be in the reply.

Settings requests ("turn your voice down") go through the settings tool, on a scratch
settings file each case sets up ("settings", and "before" for changes already made);
"settings_after", "settings_below" and "settings_unchanged" grade what it holds after.

"Say that again" never reaches the brain: the session replays its last answer
(#222), and a case expecting "say_again" checks that.

The Tile is on, as it is by default (``--no-tile`` turns it off). "card" lists the
kinds of card a case may show, and "no_card" says it must show none (#2).

Ship questions go through the ship look-up by default. ``--ships table`` puts a
table of every ship in the briefing instead, and ``--ships none`` gives Claude
neither, to measure the two designs ADR 15 compares.

Other models go through OpenRouter (#192): ``--provider openrouter --model <id>``,
with the OpenRouter key. ``--adapter-check`` runs Haiku directly and through OpenRouter
and compares them. The summary gives the cost a brain request and an hour of play.
``--host cerebras`` (repeatable, tried in order, no fallback) pins who serves the model,
or ``--sort latency`` lets OpenRouter pick the fastest (#267); not both. Each result says which
host answered each call (``hosts``), and the summary counts them.

Uses the keys in Windows Credential Manager. About $0.0002 a request on Haiku.

It runs off the PC too, in a cloud thread (#262): ``--export-keybinds <file>`` on the PC
writes the player's keybinds (and own commands) to a file, free, and ``--keybinds <file>``
reads them instead of the game. Where Credential Manager has no key, the eval (and only
the eval: Tycho never reads keys from the environment) takes ANTHROPIC_API_KEY and
OPENROUTER_API_KEY from the environment. Keys are never printed.

Usage: uv run python tools/bench/tycho_eval.py [cases.toml] [--tag ships] [--ships table]
       [--provider openrouter --model <id>] [--host cerebras] [--sort latency]
       [--adapter-check] [--per-hour 30]
       [--keybinds <file>] [--export-keybinds <file>]
"""

import argparse
import asyncio
import json
import logging
import os
import re
import statistics
import sys
import tempfile
import time
import tomllib
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, cast

import anthropic
import httpx2
import traces as trace_reader  # pyright: ignore[reportMissingImports]
from brain_report import GLOBAL_NEVER  # pyright: ignore[reportMissingImports, reportUnknownVariableType]

from tycho import credentials, logs, paths, safety, settings
from tycho.address import Address
from tycho.app import NO_KEYBINDS, find_install, load_commands
from tycho.brain import BrainError, BrainRequest, Exchange, Reply, providers
from tycho.brain.briefing import instructions_hash
from tycho.brain.claude import MODEL, ClaudeBrain
from tycho.brain.lookups import Lookup, LookupResult
from tycho.brain.openrouter import SORTS, OpenRouterBrain, Prices, route
from tycho.brain.rounds import NO_PRESS_AFTER_SEARCH
from tycho.brain.traces import TraceFile
from tycho.brain_choice import anthropic_key, openrouter_key
from tycho.commands import Command, CommandMatcher, resolve
from tycho.game.keybinds import Action, ActivationMode, Keybinds, KeyInput
from tycho.game.store import KeybindStore
from tycho.knowledge import ShipLookup, UexApi, WikiApi
from tycho.knowledge.fleet import Fleet, FleetUpdate
from tycho.knowledge.item_lookup import ItemLookup
from tycho.knowledge.mining import MiningLookup
from tycho.knowledge.store import ensure
from tycho.knowledge.trade_lookup import trade_lookups
from tycho.knowledge.web import WebSearch
from tycho.lexicon import Lexicon
from tycho.packs import STAR_CITIZEN, GamePack, Safety, built_in
from tycho.runtime import routing
from tycho.runtime.planning import Questions, Refusal, Steps, plan_request
from tycho.runtime.routing import NotUnderstood, Picked, Reserved, from_brain
from tycho.spoken_settings import SettingsTool

PACKS = Path(__file__).resolve().parents[2] / "src" / "tycho" / "packs"


def cases_file(pack: str = STAR_CITIZEN) -> Path:
    """A game pack's own eval cases (ADR 18): ``eval/cases.toml`` in its folder."""
    return PACKS / pack / "eval" / "cases.toml"


CASES = cases_file()
RESULTS = Path(__file__).resolve().parents[2] / "recordings" / "tycho_eval.results.jsonl"
TRACES = RESULTS.with_name("tycho_eval.traces.jsonl")
"""One line per brain request, the same as play writes with TYCHO_TRACE=1, each with its
case (#282): read them with tools/bench/traces.py."""
DODGE = re.compile(
    r"\b(check|consult|look at|see)\b[^.]{0,40}\b(wiki|guide|guides|community|patch notes|"
    r"rankings|forums?|reddit|spectrum)\b|\bcan'?t (say|tell you|give you)\b",
    re.IGNORECASE,
)
NARRATED = re.compile(
    r"\bnot (a|an) (request|command|question)(?= for me|,)|\bsquad callout\b|"
    r"\bso a short (friendly |simple )?(reply|answer)\b|"
    r"\b(this|that) (is|sounds like) (squad |room )?chat\b|\bchat (to|for) (me|the squad)\b|"
    r"\bthe player (is|was|said|asked|wants|trailed|didn't)\b|\bjust acknowledg|"
    r"\bso (there's )?no action\b|\bpress(ing)? nothing\b|\bcall(ing)? no tool\b",
    re.IGNORECASE,
)
"""A reply that explains itself or talks about the player, which the player hears: "This
is chat for the squad, not a request for me, so I'm just acknowledging it. Copy." (the
#192 eval after #278, four replies graded as passes)."""
SAY_WORDS = 8
"""What Tycho says after acting should be a few words: the press already happened."""
COST_GUESS = 0.0002
CALL_LOGGED = re.compile(r"Brain (replied|acted|warmed up)\b|Web search\b.* in \d+ ms\b")
"""The debug lines each call to a model logs, with its cost when it's known. A web
search counts only when it answered: "Web search failed" is a warning with no cost,
and the request it made most likely cost nothing (#264)."""
HOST = re.compile(r"\[host: ([^\]]+)\]$")
"""Who served a call through OpenRouter, at the end of its line (#267)."""
CLAIMED = re.compile(
    r"^(done|on it|mounting up|hearthing|queued( up)?|(casting|jumping|queuing|drinking|"
    r"targeting|attacking|opening it) now)[.!]?$|"
    r"^I (just )?(pressed|opened|cast|mounted|queued|jumped|targeted|drank)\b|"
    r"\bI('ve| have) (just )?(pressed|opened|cast|mounted|queued|jumped|targeted|drunk|drank)\b",
    re.IGNORECASE,
)
"""A reply that says Tycho did something in the game, where it presses nothing (advisor
mode, #326): "Mounting up." would tell the player it happened when nothing did."""
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
    refused: str = ""
    """Why the safety gate would refuse the pick, pressing nothing; empty if it wouldn't.
    A refusal outranks a question, so a refused pick never "asks" (#311)."""
    ms: float = 0.0
    looked_up: int = 0
    """Look-ups the brain made for this request."""
    calls: int = 0
    """Calls to Claude for this request: each is about 1-1.5 s (#148)."""
    tools: list[str] = field(default_factory=list[str])
    """The look-ups it used, in order."""
    errors: list[str] = field(default_factory=list[str])
    """Look-ups that came back as errors, which Claude tends to retry."""
    searched: bool = False
    """Whether the brain searched the web for this request."""
    card: str = ""
    """The kind of card it showed on the Tile, if any."""
    card_rows: list[str] = field(default_factory=list[str])
    hosts: list[str] = field(default_factory=list[str])
    """Who served each call through OpenRouter, in order ("Cerebras"), where it said (#267)."""
    verdict: str = ""
    notes: list[str] = field(default_factory=list[str])


class _Costs(logging.Handler):
    """Picks the brain's cost estimates out of its debug log."""

    def __init__(self) -> None:
        super().__init__(logging.DEBUG)
        self.total = 0.0
        self.counted = 0
        self.replies = 0
        """Calls to the model that ended in a message (an act ends its call early)."""
        self.sources: dict[str, int] = {}
        """Where each counted cost came from: "reported" by OpenRouter, its "price
        list", or Tycho's own "estimate" from Anthropic's prices."""
        self.generations: list[str] = []
        """OpenRouter calls that ended early at an act: their cost is looked up after.
        Those still here after ``generation_costs`` had no cost reported."""
        self.looked_up: dict[str, float] = {}
        """Each generation's cost ``generation_costs`` found, by its id, for the traces."""
        self.unreported = 0
        """Calls that said nothing of their cost (no usage, no price, no generation id):
        the total is too low by these, and the summary says so (#261)."""
        self.hosts: list[str] = []
        """Who served each call that said so, in order (#267)."""

    def emit(self, record: logging.LogRecord) -> None:
        message = record.getMessage()
        generation = re.search(r"generation (\S+)", message)
        if match := re.search(r"about \$([0-9.]+)(?: \(([a-z ]+)\))?", message):
            self.add(float(match.group(1)), match.group(2) or "estimate")
        elif generation and generation.group(1) != "?":
            self.generations.append(generation.group(1))
        elif CALL_LOGGED.match(message):
            self.unreported += 1
        if message.startswith("Brain replied"):
            self.replies += 1
        if CALL_LOGGED.match(message) and (host := HOST.search(message)):
            self.hosts.append(host.group(1))

    @property
    def missing(self) -> int:
        """Calls whose cost isn't in the total."""
        return self.unreported + len(self.generations)

    def add(self, cost: float, source: str) -> None:
        self.total += cost
        self.counted += 1
        self.sources[source] = self.sources.get(source, 0) + 1


def pressed(commands: tuple[Command, ...], keybinds: Keybinds) -> list[str]:
    """The game actions picked for these commands, setup included, whether or not the
    gate would let them be pressed."""
    names: list[str] = []
    for command in commands:
        for step in command.expand():
            names += list(step.command.setup)
            resolved = resolve(step.command, keybinds)
            names.append(
                resolved.action.name if resolved.action else f"<no key: {step.command.id}>"
            )
    return names


def grade(
    outcome: Outcome,
    rule: dict[str, Any],
    valid: set[str],
    *,
    keys_off: bool = False,
    never_say: Sequence[re.Pattern[str]] = (),
) -> str:
    """The case's verdict. ``keys_off``: Tycho presses nothing in this game (advisor mode,
    #191), so a reply that claims it did something fails (#326). ``never_say``: the
    cases file's words from another game ("quantum" in World of Warcraft), which fail
    any case whose spoken line says one (#349)."""
    never = (set(GLOBAL_NEVER) | set(rule.get("never", []))) - set(rule.get("allow", []))
    if outcome.kind == "error":
        return "error"
    asked = {action for group in rule.get("must", []) for action in group}
    # A pick the gate refuses still fails (the gate is the backstop, not the bar), but
    # the verdict names the refusal, not a missing confirm (#311).
    gate = f"; the gate refused it ({outcome.refused})" if outcome.refused else ""
    if bad := (never | (UNASKED - asked)).intersection(outcome.actions):
        return f"WRONG PRESS {sorted(bad)}{gate}"
    allowed = set(rule.get("allow", [])).intersection(outcome.actions)
    if allowed and outcome.refused:
        return f"WRONG PRESS (the gate refused it: {outcome.refused}) {sorted(allowed)}"
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
    said = plain(outcome.say)
    if NARRATED.search(said):
        return f"narrated its reply: {outcome.say!r}"
    if found := next((p.search(said) for p in never_say if p.search(said)), None):
        return f"said {found.group(0)!r}, from another game: {outcome.say!r}"
    if keys_off and outcome.kind == "reply" and CLAIMED.search(said.strip()):
        return f"claimed a press with keys off: {outcome.say!r}"
    if outcome.kind == "reply" and rule.get("answer") and DODGE.search(said):
        return "dodged the question"
    if outcome.kind == "reply":
        for group in rule.get("mentions", []):
            if not any(re.search(p, said, re.IGNORECASE) for p in group):
                return f"missing {'/'.join(group)}"
    if rule.get("no_card") and outcome.card:
        return f"showed a {outcome.card} card, expected none"
    if (kinds := rule.get("card")) and outcome.card not in kinds:
        return f"showed {outcome.card or 'no card'}, expected {'/'.join(kinds)}"
    return grade_path(outcome, rule)


CURLY = str.maketrans({"\u2018": "'", "\u2019": "'", "\u201c": '"', "\u201d": '"'})


def plain(say: str) -> str:
    """Curly quotes as straight ones, so a case's "hasn't" matches "hasn\u2019t" (#271:
    GPT-6 Luna's right answer on the Kraken failed on its apostrophe)."""
    return say.translate(CURLY)


def grade_path(outcome: Outcome, rule: dict[str, Any]) -> str:
    """How the answer was reached (#148): a right answer after three tries is slow,
    and passed only by luck. "max_calls", "tools" (each must be used), "no_tools"
    (none may be) and "no_errors"."""
    if (most := rule.get("max_calls")) is not None and outcome.calls > most:
        return f"took {outcome.calls} calls to Claude, expected at most {most}"
    for name in rule.get("tools", []):
        if name not in outcome.tools:
            return f"didn't use {name} ({', '.join(outcome.tools) or 'no look-ups'})"
    if used := set(rule.get("no_tools", [])).intersection(outcome.tools):
        return f"used {', '.join(sorted(used))}, expected not to"
    if rule.get("no_errors") and outcome.errors:
        return f"look-up errors: {'; '.join(outcome.errors)}"
    return "pass"


def grade_fleet(fleet: Fleet, rule: dict[str, Any]) -> str:
    """What a case says the fleet should hold afterwards: "fleet_has", "fleet_lacks",
    and "flying_after"."""
    for name in rule.get("fleet_has", []):
        if name not in fleet.ships:
            return f"fleet lacks {name} ({fleet.ships})"
    for name in rule.get("fleet_lacks", []):
        if name in fleet.ships:
            return f"fleet still has {name}"
    if "flying_after" in rule and fleet.flying != rule["flying_after"]:
        return f"flying {fleet.flying!r}, expected {rule['flying_after']!r}"
    return "pass"


def grade_settings(path: Path, start: settings.Settings, rule: dict[str, Any]) -> str:
    """What a case says the settings should hold afterwards: "settings_after" (exact),
    "settings_below" (lower than the value given), and "settings_unchanged"."""
    now = settings.load(path)
    if rule.get("settings_unchanged") and now != start:
        changed = {k: v for k, v in now.model_dump().items() if getattr(start, k) != v}
        return f"settings changed: {changed}"
    for name, expected in rule.get("settings_after", {}).items():
        value = getattr(now, name)
        same = (
            abs(value - expected) < 1e-6
            if isinstance(value, float) and isinstance(expected, int | float)
            else value == expected
        )
        if not same:
            return f"{name} is {value!r}, expected {expected!r}"
    for name, limit in rule.get("settings_below", {}).items():
        if not getattr(now, name) < limit:
            return f"{name} is {getattr(now, name)!r}, expected below {limit!r}"
    return "pass"


class _Counted:
    """A look-up that counts its calls and notes each one in ``journal`` (its name,
    and its error if it failed), so the eval can grade the path (#148)."""

    def __init__(self, inner: Lookup, journal: list[tuple[str, str]] | None = None) -> None:
        self.inner = inner
        self.calls = 0
        self.journal = journal if journal is not None else []
        self.name, self.description = inner.name, inner.description
        self.input_schema = inner.input_schema

    def __getattr__(self, name: str) -> Any:
        # from_the_web, seconds, instant: as the look-up it stands for.
        return getattr(self.inner, name)

    async def prepare(self) -> None:
        await self.inner.prepare()

    async def run(self, arguments: Mapping[str, object]) -> LookupResult:
        self.calls += 1
        try:
            result = await self.inner.run(arguments)
        except BaseException as e:
            # Timed out (run_lookup cancels it) or raised: the brain gets an error either way.
            self.journal.append((self.name, f"{type(e).__name__}: {e}"[:160] or "cancelled"))
            raise
        self.journal.append((self.name, result.content[:160] if result.is_error else ""))
        return result


async def run_case(
    rule: dict[str, Any],
    lexicon: Lexicon,
    brain: ClaudeBrain | OpenRouterBrain,
    keybinds: Keybinds,
    counted: _Counted | None = None,
    journal: list[tuple[str, str]] | None = None,
    costs: _Costs | None = None,
    matcher: CommandMatcher | None = None,
    *,
    tile: bool = True,
    lists: Safety | None = None,
) -> Outcome:
    """One case through Tycho's path. ``lists`` are the pack's safety lists (Star
    Citizen's when None)."""
    outcome = Outcome(rule["heard"], str(rule.get("tag", "")))
    text = lexicon.correct(rule["heard"]).text
    matcher = matcher or MATCHER
    # The session's own routing (#55), so the eval grades the path the words really take.
    routed = routing.route(text, matcher, tile=tile)
    if isinstance(routed, Reserved):  # the session answers these itself ("say that again", #222)
        outcome.route, outcome.kind = "session", routed.word
        return outcome
    if isinstance(routed, NotUnderstood):  # only without a brain, which the eval always has
        outcome.kind, outcome.notes = "error", [routed.reason]
        return outcome
    if isinstance(routed, Picked):
        outcome.route = "fast"
        _plan(outcome, routed.commands, keybinds, lists)
        return outcome
    outcome.route = "brain"
    recent = [Exchange(said, did) for said, did in rule.get("recent", [])]
    started = time.perf_counter()
    before = counted.calls if counted else 0
    journal = journal if journal is not None else []
    journal.clear()
    replies = costs.replies if costs else 0
    hosts = len(costs.hosts) if costs else 0
    try:

        def searching() -> None:
            outcome.searched = True

        decision = await brain.decide(
            BrainRequest(text, keybinds, tuple(recent), waiting=searching)
        )
    except BrainError as e:
        outcome.kind, outcome.notes = "error", [str(e)]
        return outcome
    finally:
        outcome.ms = (time.perf_counter() - started) * 1000
        outcome.tools = [name for name, _ in journal]
        outcome.errors = [f"{name}: {error}" for name, error in journal if error]
        outcome.looked_up = max((counted.calls if counted else 0) - before, len(journal))
        outcome.calls = (costs.replies if costs else 0) - replies
        outcome.hosts = costs.hosts[hosts:] if costs else []
    if isinstance(decision, Reply):
        outcome.kind, outcome.say = "reply", decision.text
        if decision.card is not None or decision.text == NO_PRESS_AFTER_SEARCH:
            outcome.calls += 1  # show_card (or an act after a search) ended its call early
        if decision.card is not None:
            outcome.card = decision.card.kind
            outcome.card_rows = [
                " | ".join(p for p in (r.text, r.detail, r.value) if p) for r in decision.card.rows
            ] + list(decision.card.body)
        return outcome
    outcome.say = decision.say
    outcome.calls += 1  # the call that chose the act ended early, so it logged no reply
    try:
        picked = from_brain(decision, matcher, lists)
    except BrainError as e:
        outcome.kind, outcome.notes = "error", [str(e)]
    else:
        assert isinstance(picked, Picked)  # noqa: S101 - a Reply returned above
        _plan(outcome, picked.commands, keybinds, lists)
    if len(decision.say.split()) > SAY_WORDS:
        outcome.notes.append(f"say line is {len(decision.say.split())} words")
    return outcome


def _plan(
    outcome: Outcome,
    commands: tuple[Command, ...],
    keybinds: Keybinds,
    lists: Safety | None = None,
) -> None:
    """What Tycho would press for these commands, through the session's own planning and
    safety gate (#55): after "confirm" if the gate asks first, with every fact the game
    state can report present (the most it could ever press).

    A request the gate refuses presses nothing in the game, but the eval still grades
    what was picked: a pick the gate had to stop is a wrong pick, and the gate is the
    backstop, not the bar. The refusal is noted."""
    outcome.kind = "actions"
    facts = safety.known_facts(lists)
    outcome.asks = isinstance(plan_request(commands, keybinds, facts, lists=lists), Questions)
    planned = plan_request(commands, keybinds, facts, confirmed=True, lists=lists)
    if isinstance(planned, Steps):
        outcome.actions = list(planned.actions)
        return
    outcome.actions = pressed(commands, keybinds)
    if isinstance(planned, Refusal):  # never Questions: it's planned as confirmed
        outcome.refused = f"{planned.status}: {planned.detail}"
        outcome.notes.append(f"Tycho would press nothing ({outcome.refused})")


MATCHER = load_commands()
LIVE: dict[str, Callable[[object], None]] = {
    name: lambda _value: None
    for name in settings.Settings.model_fields
    if settings.rule(name)["applies"] == "now"
}
"""As tycho run wires them, so the brain hears "applies now" where the player would."""


PROVIDERS = ("anthropic", "openrouter")
OPENROUTER_HAIKU = providers.OPENROUTER_HAIKU
"""Haiku through OpenRouter, as players get it (#112), for the adapter check."""
PER_HOUR = 30.0
"""Brain requests in an hour of play: a placeholder until the play logs measure it (#192)."""
OPENROUTER = "https://openrouter.ai/api/v1"


@dataclass
class Run:
    provider: str
    model: str
    outcomes: list[Outcome]
    costs: _Costs
    route: dict[str, object] = field(default_factory=dict[str, object])
    """OpenRouter's ``provider`` field the run sent: the pinned hosts or sort (#267)."""


ENVIRONMENT = {"anthropic": "ANTHROPIC_API_KEY", "openrouter": "OPENROUTER_API_KEY"}
"""Where the eval looks for a key when Credential Manager has none (#262): a cloud thread,
whose keys are environment secrets. Tycho itself never reads keys from the environment."""


def eval_key(name: str, stored: Callable[[], str | None]) -> str | None:
    """The stored key, else the environment's. Never printed: it's registered with the
    log redactor, and only where it came from is said."""
    key = stored()
    if key is None and (key := os.environ.get(ENVIRONMENT[name], "").strip() or None):
        if not credentials.usable(key):
            print(f"{ENVIRONMENT[name]} {credentials.UNUSABLE}; not using it.")
            return None
        logs.register_secret(key)
        print(f"The {name} key is from the environment ({ENVIRONMENT[name]}).")
    return key


KEYBINDS_FORMAT = 1


def export_keybinds(keybinds: Keybinds, path: Path, game: str = "", commands: str = "") -> None:
    """The player's keybinds as JSON, for an eval that can't read the game (#262).
    ``commands`` is the text of their own commands.toml, so the fast path matches as
    on their PC. Nothing secret is in either."""
    data = {
        "format": KEYBINDS_FORMAT,
        "exported": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "game": game,
        "actions": [
            {
                **{k: v for k, v in asdict(action).items() if k != "inputs"},
                "inputs": [[i.key, *i.modifiers] for i in action.inputs],
            }
            for action in keybinds
        ],
        "modes": {name: asdict(mode) for name, mode in keybinds.modes.items()},
        "commands": commands,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=1), "utf-8")


def _inputs(entry: dict[str, Any]) -> list[list[str]]:
    """An action's inputs, each a list of strings (key, then modifiers): a bare string
    would unpack letter by letter (#288)."""
    inputs: object = entry["inputs"]
    if not isinstance(inputs, list):
        raise TypeError("an action's inputs must be a list")
    checked: list[list[str]] = []
    for each in cast(list[object], inputs):
        parts = cast(list[object], each) if isinstance(each, list) else []
        if not parts or not all(isinstance(p, str) for p in parts):
            raise TypeError("each input must be a list of strings")
        checked.append([str(p) for p in parts])
    return checked


def load_keybinds(path: Path) -> tuple[Keybinds, str, str]:
    """Keybinds written by ``export_keybinds``, the game build they came from, and the
    player's own commands (empty if they have none)."""
    refused = f"{path} isn't a keybinds file this eval reads; export it again."
    try:
        data: dict[str, Any] = json.loads(path.read_text("utf-8"))
        if not isinstance(data, dict) or data.get("format") != KEYBINDS_FORMAT:  # pyright: ignore[reportUnnecessaryIsInstance]
            raise SystemExit(refused)
        actions = [
            Action(
                **{k: v for k, v in entry.items() if k != "inputs"},
                inputs=tuple(KeyInput(key, tuple(mods)) for key, *mods in _inputs(entry)),
            )
            for entry in data["actions"]
        ]
        modes = {name: ActivationMode(**mode) for name, mode in data["modes"].items()}
    except (ValueError, KeyError, TypeError, AttributeError) as e:
        # Broken JSON, a missing key or an entry of the wrong shape (#265).
        raise SystemExit(f"{refused} ({type(e).__name__}: {e})") from None
    game = f"{data.get('game') or 'an unknown build'}, exported {data.get('exported', '?')}"
    return Keybinds(actions, modes), game, str(data.get("commands") or "")


def _export(path: Path) -> None:
    """``--export-keybinds``: free, reads only the game's files and the player's own."""
    install = find_install(settings.load())
    store = KeybindStore.load(install)
    stamp = install.data_p4k.stat()
    game = f"{install.channel} {stamp.st_size}-{int(stamp.st_mtime)}"
    own = paths.commands_file()
    commands = own.read_text("utf-8-sig") if own.is_file() else ""
    export_keybinds(store.keybinds, path, game, commands)
    bound = sum(a.bound for a in store.keybinds)
    print(f"Wrote {bound} bound actions from {game} to {path}", end="")
    print(", with your own commands." if commands else ".")


def _matcher(commands: str, pack: GamePack | None = None) -> CommandMatcher:
    """The fast path with a keybinds file's own commands, as on the player's PC."""
    if not commands:
        return CommandMatcher.default(pack=pack)
    with tempfile.TemporaryDirectory() as folder:
        own = Path(folder) / "commands.toml"
        own.write_text(commands, "utf-8")
        return CommandMatcher.default(own, pack)


def _arguments(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "cases", type=Path, nargs="?", help="a cases file (default: the pack's eval/cases.toml)"
    )
    parser.add_argument(
        "--pack",
        default=STAR_CITIZEN,
        help="the game pack whose cases, briefing, commands and safety lists to use (ADR 18)",
    )
    parser.add_argument(
        "--keybinds",
        type=Path,
        help="read the keybinds from a file --export-keybinds wrote, not the game (#262)",
    )
    parser.add_argument(
        "--export-keybinds",
        type=Path,
        metavar="FILE",
        help="write your keybinds and own commands to FILE for a run elsewhere, then stop (free)",
    )
    parser.add_argument("--tag", action="append", help="only cases with this tag; repeatable")
    parser.add_argument(
        "--ships",
        choices=["lookup", "table", "none"],
        default="lookup",
        help="ship data as a look-up tool (default), a table in the briefing, or neither",
    )
    parser.add_argument("--no-web", action="store_true", help="don't offer web search")
    parser.add_argument("--no-tile", action="store_true", help="don't offer Tile cards")
    parser.add_argument(
        "--provider",
        choices=PROVIDERS,
        default="anthropic",
        help="Claude directly (default), or any model through OpenRouter (#192)",
    )
    parser.add_argument(
        "--model",
        help=f"the model id: {MODEL} by default for anthropic; required for openrouter, "
        f"e.g. {OPENROUTER_HAIKU}",
    )
    parser.add_argument(
        "--per-hour",
        type=float,
        default=PER_HOUR,
        help=f"brain requests in an hour of play, for the cost an hour (a placeholder "
        f"{PER_HOUR:g} until play logs measure it)",
    )
    parser.add_argument(
        "--effort",
        choices=["low", "medium", "high"],
        help="through OpenRouter only: how hard a model that reasons should (default: "
        "as its provider runs it)",
    )
    parser.add_argument(
        "--host",
        action="append",
        default=[],
        metavar="SLUG",
        help="through OpenRouter only: pin who serves the model, by OpenRouter's slug "
        "(cerebras, groq); repeatable, tried in order, never falling back (#267)",
    )
    parser.add_argument(
        "--sort",
        choices=SORTS,
        help="through OpenRouter only: let it pick the host by this (latency: the fastest)",
    )
    parser.add_argument(
        "--adapter-check",
        action="store_true",
        help="run Haiku directly, then through OpenRouter (--model overrides "
        f"{OPENROUTER_HAIKU}), with no web search, and compare the two",
    )
    args = parser.parse_args(argv)
    if args.cases is None:
        args.cases = cases_file(args.pack)
    if args.adapter_check:
        args.no_web = True  # the OpenRouter brain has none, so neither side gets it
    elif args.provider == "openrouter" and not args.model:
        parser.error("--provider openrouter needs --model, e.g. " + OPENROUTER_HAIKU)
    elif args.provider != "openrouter" and (args.host or args.sort):
        parser.error("--host and --sort pick who serves a model through OpenRouter")
    elif args.host and args.sort:
        # OpenRouter doesn't say whether a sort reorders pinned hosts, so a run would
        # measure an order nobody can be sure of (#275).
        parser.error("--host and --sort don't go together: pin the hosts or let it sort")
    return args


async def main() -> None:
    # Cards hold "→" and the like: a console or file in Windows' own code page can't
    # print them, and the run stopped halfway (6 November 2026).
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")
    args = _arguments()
    if args.export_keybinds:
        _export(args.export_keybinds)
        return
    if args.adapter_check:
        direct = await run(args, "anthropic", MODEL)
        routed = await run(args, "openrouter", args.model or OPENROUTER_HAIKU)
        print()
        for line in compare(direct, routed, args.per_hour):
            print(line)
        return
    provider = args.provider
    await run(args, provider, args.model or MODEL)


async def run(args: argparse.Namespace, provider: str, model: str) -> Run:
    """Every case through one brain; prints each, appends them to the results, and
    prints the summary."""
    pack = built_in(args.pack)
    if pack.parts.keybinds not in (None, STAR_CITIZEN):
        raise SystemExit(f"{pack.name}: the eval can only read Star Citizen's keybinds so far")
    if pack.parts.keybinds is None and args.keybinds:
        raise SystemExit(f"{pack.name} has no keybinds (an advisor pack): drop --keybinds")
    briefed = instructions_hash(pack)
    print(f"Game: {pack.name}, pack {pack.version} (briefing {briefed})")
    data: dict[str, Any] = tomllib.loads(args.cases.read_text("utf-8"))
    rules: list[dict[str, Any]] = data["case"]
    never_say = [re.compile(p, re.IGNORECASE) for p in data.get("never_say", [])]
    if args.tag:
        rules = [r for r in rules if r.get("tag") in args.tag]
    pinned = route(args.host, args.sort) if provider == "openrouter" else {}
    print(f"\nBrain: {model} through {provider}{_pinned(pinned)}")
    needs_anthropic = provider == "anthropic" or not args.no_web
    key = eval_key("anthropic", anthropic_key) if needs_anthropic else None
    routed = eval_key("openrouter", openrouter_key) if provider == "openrouter" else None
    if provider == "openrouter" and routed is None:
        raise SystemExit(
            "No OpenRouter key. Run: uv run python -m tycho.credentials set openrouter "
            "(or, off the PC, set OPENROUTER_API_KEY)"
        )
    if needs_anthropic and key is None:
        raise SystemExit(
            "No Anthropic key. Run: uv run tycho key set anthropic (or, off the PC, set "
            "ANTHROPIC_API_KEY)"
        )

    matcher = load_commands(pack)
    if pack.parts.keybinds is None:  # an advisor pack (#191): no game folder, no keys
        keybinds = NO_KEYBINDS
    elif args.keybinds:
        keybinds, game, own = load_keybinds(args.keybinds)
        matcher = _matcher(own, pack)
        print(f"Keybinds: {args.keybinds} ({game}){', with own commands' if own else ''}")
    else:
        keybinds = KeybindStore.load(find_install(settings.load())).keybinds
    valid = {a.name for a in keybinds if a.bound}
    costs = _Costs()
    for name in ("tycho.brain", "tycho.knowledge.web"):  # the search is its own request
        logging.getLogger(name).addHandler(costs)
        logging.getLogger(name).setLevel(logging.DEBUG)
    counted: _Counted | None = None
    reference = ""
    library = None
    wanted = {*pack.parts.lookups, "settings"}  # as tycho run offers them (ADR 18)
    if args.ships != "none" and "ships" in wanted:
        library = await ensure(WikiApi(), paths.knowledge_dir(), concepts=UexApi())
        print(f"Ship data: {len(library)} ships for {library.patch}, as {args.ships}")
        if args.ships == "table":
            reference = "Ships and vehicles in this patch:\n" + library.table()
        else:
            counted = _Counted(ShipLookup(WikiApi(), paths.knowledge_dir(), library=library))
    web: list[Lookup] = []
    if "web" not in wanted:
        print(f"Web search: {pack.name} has none")
    elif provider == "openrouter" and not args.no_web:
        print("Web search: not offered. The OpenRouter brain has none (it's Anthropic's tool).")
    elif not args.no_web:
        web = [WebSearch(anthropic.AsyncAnthropic(api_key=key))]
    if not web:
        rules, left_out = without_search(rules)
        if left_out:
            print(f"Left out {left_out} case(s) that must search the web")
    # A fleet in a scratch file, set by each case ("fleet", "flying"): never the player's own.
    scratch = Path(tempfile.mkdtemp())
    fleet = Fleet(scratch / "fleet.toml")
    # Settings too: a fresh tool per case, so undo never reaches into an earlier case.
    settings_file = scratch / "settings.toml"
    journal: list[tuple[str, str]] = []
    if counted is not None:
        counted.journal = journal
    settings_tool = _Counted(SettingsTool(settings_file, live=LIVE), journal)
    lookups = pack_lookups(
        wanted,
        [
            ("ships", counted),
            ("fleet", _Counted(FleetUpdate(fleet, lambda: library), journal)),
            ("items", _Counted(ItemLookup(WikiApi()), journal)),
            ("mining", _Counted(MiningLookup(WikiApi()), journal)),
            *(("trade", _Counted(lookup, journal)) for lookup in trade_lookups(UexApi())),
            ("settings", settings_tool),
            *(("web", _Counted(lookup, journal)) for lookup in web),
        ],
    )

    # As tycho run does: the fleet, then what to call the player (#15), by the pack's
    # default (#349): no player has chosen a name here.
    address = Address.for_pack(pack).header()

    def about() -> str:
        return "\n".join(p for p in (fleet.describe(library), address) if p)

    def cards() -> bool:
        return not args.no_tile

    label = f"{time.strftime('%Y-%m-%dT%H:%M:%S')} {model} {pack.id} {pack.version} {briefed}"
    traces = TraceFile(TRACES, source="eval", run=label)
    brain: ClaudeBrain | OpenRouterBrain
    if provider == "openrouter":
        prices = await price_list(model)
        print(f"Prices: {'from OpenRouter' if prices else 'not found; only reported costs'}")
        brain = OpenRouterBrain(
            matcher,
            model=model,
            api_key=routed,
            lookups=lookups,
            reference=reference,
            about=about,
            cards=cards,
            prices=prices,
            effort=args.effort,
            hosts=args.host,
            sort=args.sort,
            traces=traces,
            pack=pack,
        )
    else:
        brain = ClaudeBrain(
            matcher,
            api_key=key,
            model=model,
            lookups=lookups,
            reference=reference,
            about=about,
            cards=cards,
            traces=traces,
            pack=pack,
        )
    await brain.warm(keybinds)
    lexicon = Lexicon.default(pack)

    outcomes: list[Outcome] = []
    try:
        for rule in rules:
            fleet.ships, fleet.flying = list(rule.get("fleet", [])), str(rule.get("flying", ""))
            start = settings.Settings.model_validate(rule.get("settings", {}))
            settings.save(start, settings_file)
            settings_tool.inner = SettingsTool(settings_file, live=LIVE)
            if before := rule.get("before"):
                changes = [{"name": n, "value": v} for n, v in before]
                await settings_tool.inner.run({"action": "set", "changes": changes})
                start = settings.load(settings_file)
            traces.case = rule["heard"]
            outcome = await run_case(
                rule,
                lexicon,
                brain,
                keybinds,
                counted,
                journal,
                costs,
                matcher,
                tile=cards(),
                lists=pack.safety,
            )
            outcome.verdict = grade(
                outcome, rule, valid, keys_off=bool(pack.safety.keys_off), never_say=never_say
            )
            if outcome.verdict == "pass":
                outcome.verdict = grade_fleet(fleet, rule)
            if outcome.verdict == "pass":
                outcome.verdict = grade_settings(settings_file, start, rule)
            outcomes.append(outcome)
            _print(outcome)
    finally:
        await brain.close()
        traces.close()
        for name in ("tycho.brain", "tycho.knowledge.web"):
            logging.getLogger(name).removeHandler(costs)

    if routed is not None and costs.generations:
        await generation_costs(costs, routed)
        # Their lines were written as each case ended, with no cost (#317).
        trace_reader.add_costs(TRACES, traces.run, costs.looked_up)
    done = Run(provider, model, outcomes, costs, pinned)
    summary = metrics(done, args.per_hour)
    RESULTS.parent.mkdir(exist_ok=True)
    with RESULTS.open("a", encoding="utf-8") as f:
        meta = {"type": "meta", "time": time.strftime("%Y-%m-%dT%H:%M:%S")}
        meta |= {"provider": provider, "model": model, "route": pinned, "traces": traces.run}
        meta |= {"pack": pack.id, "pack_version": pack.version, "briefing": briefed}
        f.write(json.dumps(meta) + "\n")
        for outcome in outcomes:
            f.write(json.dumps({**asdict(outcome), "provider": provider, "model": model}) + "\n")
        f.write(json.dumps({"type": "summary", **summary}) + "\n")
    _summary(outcomes, costs, summary)
    return done


def without_search(rules: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    """The cases that can run with no web search, and how many were left out: a case
    whose ``tools`` must include ``web_search`` (#353) can't pass without it."""
    kept = [r for r in rules if "web_search" not in r.get("tools", [])]
    return kept, len(rules) - len(kept)


def pack_lookups(wanted: set[str], parts: list[tuple[str, Lookup | None]]) -> list[Lookup]:
    """The look-ups the pack names, in order, as ``brain_choice`` gives them to tycho run:
    an advisor pack with none (World of Warcraft: Forever) gets only the settings tool."""
    return [lookup for part, lookup in parts if part in wanted and lookup is not None]


def _pinned(pinned: Mapping[str, object]) -> str:
    """The route in words, for the run's first line."""
    words: list[str] = []
    if isinstance(order := pinned.get("order"), list):
        hosts = cast("list[object]", order)
        words.append(f"on {' then '.join(str(h) for h in hosts)} only")
    if sort := pinned.get("sort"):
        words.append(f"by {sort}")
    return f" ({', '.join(words)})" if words else ""


def _print(outcome: Outcome) -> None:
    mark = "  " if outcome.verdict == "pass" else "XX"
    what = ", ".join(outcome.actions) if outcome.kind == "actions" else outcome.kind
    what += " (asks first)" if outcome.asks else ""
    what += f" after {', '.join(outcome.tools)}" if outcome.tools else ""
    what += f" ({outcome.calls} calls)" if outcome.route == "brain" else ""
    what += " after a web search" if outcome.searched else ""
    what += f" on {', '.join(dict.fromkeys(outcome.hosts))}" if outcome.hosts else ""
    print(f"{mark} {outcome.route:5} {outcome.ms:5.0f} ms  {outcome.heard!r} -> {what}")
    if outcome.say:
        print(f"{'':16}says ({len(outcome.say.split())} words): {outcome.say}")
    if outcome.card:
        print(f"{'':16}card ({outcome.card}): {' / '.join(outcome.card_rows)}")
    if outcome.verdict != "pass" or outcome.notes:
        print(f"{'':16}{'; '.join([outcome.verdict, *outcome.notes])}")


async def price_list(model: str) -> Prices | None:
    """The model's prices from OpenRouter's public list, for replies that don't report
    their cost. Free, and needs no key."""
    try:
        async with httpx2.AsyncClient(timeout=10.0) as http:
            response = await http.get(f"{OPENROUTER}/models")
            response.raise_for_status()
            listed: list[dict[str, Any]] = response.json().get("data", [])
    except (httpx2.HTTPError, ValueError) as e:
        print(f"Couldn't read OpenRouter's price list: {e}")
        return None
    return prices_for(listed, model)


def prices_for(listed: list[dict[str, Any]], model: str) -> Prices | None:
    """``Prices`` for a model in OpenRouter's list (prices per token, as strings). A
    variant such as ``:nitro`` isn't listed, so it takes its model's prices (#268)."""
    listed_ids = {entry.get("id") for entry in listed}
    if model not in listed_ids:
        model = model.split(":", 1)[0]
    for entry in listed:
        if entry.get("id") == model:
            pricing: dict[str, Any] = entry.get("pricing") or {}
            try:
                read = pricing.get("input_cache_read")
                return Prices(
                    prompt=float(pricing["prompt"]),
                    completion=float(pricing["completion"]),
                    cache_read=float(read) if read not in (None, "") else None,
                )
            except KeyError, ValueError:
                return None
    return None


async def generation_costs(
    costs: _Costs,
    key: str,
    transport: httpx2.AsyncBaseTransport | None = None,
    pause: float = 1.0,
) -> None:
    """The cost OpenRouter reports for each call that ended early at an act. It can
    take a moment to be ready, so each is asked for up to three times. Those it never
    gives stay in ``costs.generations``, so the summary counts them (#261)."""
    headers = {"Authorization": f"Bearer {key}"}
    missing: list[str] = []
    async with httpx2.AsyncClient(
        base_url=OPENROUTER, timeout=10.0, headers=headers, transport=transport
    ) as http:
        for generation in costs.generations:
            for attempt in range(3):
                if attempt:
                    await asyncio.sleep(pause * attempt)
                try:
                    response = await http.get("/generation", params={"id": generation})
                    if response.status_code == 200:
                        data: dict[str, Any] = response.json().get("data") or {}
                        cost = data.get("total_cost")
                        if isinstance(cost, int | float):
                            costs.add(float(cost), "reported")
                            costs.looked_up[generation] = float(cost)
                            break
                except httpx2.HTTPError, ValueError:
                    pass
            else:
                missing.append(generation)
    costs.generations = missing


def _percentile(values: list[float], n: int) -> float | None:
    if len(values) < 10:
        return None
    return statistics.quantiles(values, n=10)[n - 1]


def metrics(run: Run, per_hour: float = PER_HOUR) -> dict[str, Any]:
    """What one run measured, for the summary, the results file and comparing runs."""
    outcomes, costs = run.outcomes, run.costs
    brain = [o for o in outcomes if o.route == "brain"]
    answered = [o.ms for o in brain if o.kind != "error"]
    picks = [o.ms for o in brain if o.kind == "actions"]
    unpriced = max(0, len(brain) - costs.counted) if run.provider == "anthropic" else 0
    total = costs.total + unpriced * COST_GUESS
    per_request = total / len(brain) if brain else 0.0
    return {
        "provider": run.provider,
        "model": run.model,
        "route": dict(run.route),
        "hosts": dict(Counter(h for o in brain for h in o.hosts).most_common()),
        "cases": len(outcomes),
        "passed": sum(o.verdict == "pass" for o in outcomes),
        "wrong_presses": sum(o.verdict.startswith("WRONG PRESS") for o in outcomes),
        "brain_requests": len(brain),
        "errors": sum(o.kind == "error" for o in brain),
        "p50_ms": statistics.median(answered) if answered else None,
        "p90_ms": _percentile(answered, 9),
        "acting_p50_ms": statistics.median(picks) if picks else None,
        "acting_p90_ms": _percentile(picks, 9),
        "mean_calls": statistics.mean(o.calls for o in brain) if brain else None,
        "cost": total,
        "cost_sources": dict(costs.sources)
        | ({"guessed": unpriced} if unpriced else {})
        | ({"not reported": costs.missing} if costs.missing else {}),
        "calls_without_cost": costs.missing,
        "cost_per_request": per_request,
        "per_hour": per_hour,
        "cost_per_hour": per_request * per_hour,
    }


def _summary(outcomes: list[Outcome], costs: _Costs, summary: dict[str, Any]) -> None:
    print(
        f"\n{summary['passed']}/{summary['cases']} pass, {summary['wrong_presses']} wrong presses"
    )
    by_tag: dict[str, list[Outcome]] = {}
    for o in outcomes:
        by_tag.setdefault(o.tag, []).append(o)
    for tag, group in sorted(by_tag.items()):
        print(f"  {tag or '(none)':12} {sum(o.verdict == 'pass' for o in group)}/{len(group)}")
    if summary["p50_ms"] is not None:
        print(
            f"Brain: p50 {summary['p50_ms']:.0f} ms over {summary['brain_requests']} requests",
            end="",
        )
        if summary["p90_ms"] is not None:
            print(f", p90 {summary['p90_ms']:.0f} ms", end="")
        print()
    if summary["acting_p50_ms"] is not None:
        print(f"Brain picks (acting): p50 {summary['acting_p50_ms']:.0f} ms (bar: 1500)")
    if searched := [o for o in outcomes if o.searched]:
        print(
            f"Web searches: {len(searched)} requests, "
            f"p50 {statistics.median(o.ms for o in searched):.0f} ms"
        )
    looked = [o for o in outcomes if o.looked_up]
    if looked:
        print(
            f"Look-ups: {len(looked)} requests used them, "
            f"p50 {statistics.median(o.ms for o in looked):.0f} ms"
        )
    if (calls := [o.calls for o in outcomes if o.route == "brain"]) and calls:
        retried = sum(1 for o in outcomes if o.errors)
        print(
            f"Calls to the model: mean {statistics.mean(calls):.2f} a request, "
            f"most {max(calls)}; {retried} request(s) hit a look-up error"
        )
    if hosts := summary["hosts"]:
        print("Served by: " + ", ".join(f"{host} ({n} calls)" for host, n in hosts.items()))
    sources = ", ".join(f"{n} {source}" for source, n in summary["cost_sources"].items())
    print(f"Cost: about ${summary['cost']:.3f} ({sources or 'none counted'})")
    print(
        f"Cost a brain request: about ${summary['cost_per_request']:.5f}; an hour of play: "
        f"about ${summary['cost_per_hour']:.3f} at {summary['per_hour']:g} brain requests an hour"
        + (" (a placeholder: --per-hour)" if summary["per_hour"] == PER_HOUR else "")
    )
    if costs.missing:
        print(
            f"{costs.missing} call(s) had no cost reported, so the cost above is too low by theirs"
        )


FAR_PASS = 0.05
"""The adapter check fails if pass rates differ by more than this share of the cases..."""
FAR_CALLS = 0.3
"""...or the mean calls a request by more than this, or wrong presses at all."""


def compare(direct: Run, routed: Run, per_hour: float = PER_HOUR) -> list[str]:
    """The adapter check: the same model directly and through OpenRouter. Far apart
    means the adapter is wrong, not the model."""
    a, b = metrics(direct, per_hour), metrics(routed, per_hour)

    def show(value: Any, form: str) -> str:
        return "-" if value is None else format(value, form)

    rows = [
        ("pass", "passed", "d"),
        ("wrong presses", "wrong_presses", "d"),
        ("errors", "errors", "d"),
        ("mean calls", "mean_calls", ".2f"),
        ("p50 ms", "p50_ms", ".0f"),
        ("acting p50 ms", "acting_p50_ms", ".0f"),
        ("cost a request $", "cost_per_request", ".5f"),
        ("cost an hour $", "cost_per_hour", ".3f"),
    ]
    head = f"{direct.provider}: {direct.model}"
    lines = [f"{'':18}{head:>34}  {routed.provider}: {routed.model}"]
    lines += [f"{label:18}{show(a[k], form):>34}  {show(b[k], form)}" for label, k, form in rows]
    differ = [
        f"  {x.heard!r}: {x.verdict} ({x.kind}) vs {y.verdict} ({y.kind})"
        for x, y in zip(direct.outcomes, routed.outcomes, strict=False)
        if (x.verdict == "pass") != (y.verdict == "pass") or x.actions != y.actions
    ]
    if differ:
        lines += [f"{len(differ)} case(s) differ:", *differ]
    far = (
        a["wrong_presses"] != b["wrong_presses"]
        or abs(a["passed"] - b["passed"]) > FAR_PASS * max(1, a["cases"])
        or abs((a["mean_calls"] or 0) - (b["mean_calls"] or 0)) > FAR_CALLS
    )
    lines.append(
        "Adapter check: FAR APART. Look at the adapter before measuring other models."
        if far
        else "Adapter check: close. The adapter is fine; other models can be measured."
    )
    return lines


if __name__ == "__main__":
    asyncio.run(main())
