"""The brain eval's grading (tools/bench/tycho_eval.py): the answer, and since #148 how
it was reached. Free: nothing here calls Claude."""

import asyncio
import importlib.util
import sys
import tomllib
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from tests.fakes.fake_lookup import FakeLookup
from tycho.brain.lookups import LookupResult, failure

ROOT = Path(__file__).resolve().parent.parent


def _eval() -> ModuleType:
    name = "tycho_eval_under_test"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, ROOT / "tools" / "bench" / "tycho_eval.py")
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    bench = str(ROOT / "tools" / "bench")
    if bench not in sys.path:
        sys.path.insert(0, bench)  # it imports brain_report from beside it, as a script does
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        del sys.modules[name]
        raise
    return module


def _outcome(**fields: Any) -> Any:
    e = _eval()
    outcome = e.Outcome("what's the best route for gold", "trade", route="brain", kind="reply")
    outcome.say = "UEX has HDMS-Anderson to Area 18 at 6,640 a SCU."
    for key, value in fields.items():
        setattr(outcome, key, value)
    return outcome


@pytest.mark.parametrize(
    ("fields", "rule", "verdict"),
    [
        ({"calls": 2, "tools": ["trade_routes"]}, {"max_calls": 2}, "pass"),
        ({"calls": 4}, {"max_calls": 2}, "took 4 calls to Claude, expected at most 2"),
        ({"tools": ["commodity_prices"]}, {"tools": ["trade_routes"]}, "didn't use trade_routes"),
        ({"tools": ["web_search"]}, {"no_tools": ["web_search"]}, "used web_search"),
        ({"errors": ["trade_routes: name a place"]}, {"no_errors": True}, "look-up errors"),
        ({"errors": ["trade_routes: name a place"]}, {}, "pass"),  # only when asked
    ],
)
def test_the_path_is_graded(fields: dict[str, Any], rule: dict[str, Any], verdict: str) -> None:
    e = _eval()
    rule = {"expect": ["reply"], **rule}
    assert e.grade(_outcome(**fields), rule, set()).startswith(verdict)


class _Failing:
    """A look-up that answers with an error, as the real ones do (they never raise)."""

    name = "trade_routes"
    description = "routes"
    input_schema: dict[str, object] = {"type": "object"}  # noqa: RUF012

    async def prepare(self) -> None:
        pass

    async def run(self, arguments: object) -> LookupResult:
        return failure("name a place")


def test_a_counted_look_up_notes_its_errors_and_stands_in_for_the_real_one() -> None:
    e = _eval()
    journal: list[tuple[str, str]] = []
    web = FakeLookup("web_search", '{"answer": "4.11"}', from_the_web=True)
    counted = e._Counted(web, journal)  # pyright: ignore[reportPrivateUsage]
    assert counted.from_the_web is True  # the brain treats it as the web search still
    asyncio.run(counted.run({"query": "next patch"}))
    failing = e._Counted(_Failing(), journal)  # pyright: ignore[reportPrivateUsage]
    asyncio.run(failing.run({"start": ""}))
    assert [name for name, _ in journal] == ["web_search", "trade_routes"]
    assert journal[0][1] == ""
    assert journal[1][1]  # the error, kept for the report


KNOWN = {
    "heard",
    "expect",
    "must",
    "never",
    "allow",
    "answer",
    "mentions",
    "tag",
    "recent",
    "fleet",
    "flying",
    "fleet_has",
    "fleet_lacks",
    "flying_after",
    "settings",
    "before",
    "settings_after",
    "settings_below",
    "settings_unchanged",
    "card",
    "no_card",
    "max_calls",
    "tools",
    "no_tools",
    "no_errors",
}


def test_every_case_uses_only_keys_the_eval_reads() -> None:
    """A misspelt key ("max_call") would be ignored, and its check silently skipped."""
    data = tomllib.loads((ROOT / "tools" / "spike" / "brain_cases.toml").read_text("utf-8"))
    unknown = {(c["heard"], k) for c in data["case"] for k in c if k not in KNOWN}
    assert not unknown


class _Slow(_Failing):
    async def run(self, arguments: object) -> LookupResult:
        await asyncio.sleep(10)
        return failure("never")


def test_a_look_up_that_times_out_is_noted_as_an_error() -> None:
    """Review of #161: run_lookup cancels it, so it never reached the journal."""
    from tycho.brain.lookups import run_lookup

    e = _eval()
    journal: list[tuple[str, str]] = []
    slow = e._Counted(_Slow(), journal)  # pyright: ignore[reportPrivateUsage]
    result = asyncio.run(run_lookup(slow, {}, seconds=0.01))
    assert result.is_error
    assert [name for name, _ in journal] == ["trade_routes"]
    assert journal[0][1]


class _ScriptedBrain:
    """Logs "Brain replied" as ClaudeBrain does for each call that ends in a message,
    then decides: ``replies`` such calls before the decision."""

    def __init__(self, replies: int, decision: object) -> None:
        self.replies = replies
        self.decision = decision

    async def decide(self, request: object) -> object:
        import logging

        for _ in range(self.replies):
            logging.getLogger("tycho.brain.claude").debug("Brain replied in 1 ms: about $0.0001")
        return self.decision


@pytest.mark.parametrize(
    ("replies", "kind", "calls"),
    [
        (1, "reply", 1),  # an answer in words: one call, logged
        (1, "card", 2),  # a look-up, then show_card, which ends its call unlogged
        (0, "act", 1),  # an act ends its only call unlogged
    ],
)
def test_run_case_counts_calls_to_claude(replies: int, kind: str, calls: int) -> None:
    """Review of #161: the counting itself wasn't tested."""
    import logging

    from tycho.brain import Reply, RunActions
    from tycho.lexicon import Lexicon
    from tycho.tile.cards import from_tool

    e = _eval()
    card = from_tool({"say": "x", "kind": "list", "title": "Gold", "rows": [{"text": "Area18"}]})
    decision = {
        "reply": Reply("Gold sells best at Area 18."),
        "card": Reply("Gold sells best at Area 18.", card),
        "act": RunActions(("v_toggle_landing_system",), "Gear."),
    }[kind]
    costs = e._Costs()  # pyright: ignore[reportPrivateUsage]
    logger = logging.getLogger("tycho.brain")
    logger.addHandler(costs)
    logger.setLevel(logging.DEBUG)
    try:
        rule = {"heard": "xyzzy plugh frobnicate", "expect": ["reply", "actions"]}
        outcome = asyncio.run(
            e.run_case(
                rule,
                Lexicon.default(),
                _ScriptedBrain(replies, decision),
                _keybinds(),
                None,
                [],
                costs,
            )
        )
    finally:
        logger.removeHandler(costs)
    assert outcome.route == "brain"
    assert outcome.calls == calls


def _keybinds() -> object:
    from tests.test_runtime import KEYBINDS

    return KEYBINDS
