"""The brain eval's grading (tools/bench/tycho_eval.py): the answer, and since #148 how
it was reached. Free: nothing here calls Claude."""

import asyncio
import importlib.util
import logging
import sys
import tomllib
from collections.abc import Mapping, Sequence
from pathlib import Path
from types import ModuleType
from typing import Any, cast

import httpx2
import pytest

from tests.fakes.fake_lookup import FakeLookup
from tycho.brain.lookups import LookupResult, failure
from tycho.packs import STAR_CITIZEN, built_in

SC = built_in(STAR_CITIZEN)

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


def test_a_curly_apostrophe_still_mentions_it() -> None:
    e = _eval()
    rule = {"expect": ["reply"], "mentions": [["hasn't"]]}
    said = _outcome(say="CIG hasn\u2019t announced an official release date.")
    assert e.grade(said, rule, set()) == "pass"


def test_a_curly_apostrophe_still_dodges() -> None:
    """#277: the dodge check read the raw line, so a curly "can't say" slipped through."""
    e = _eval()
    rule = {"expect": ["reply"], "answer": True}
    said = _outcome(say="I can\u2019t say when it comes out.")
    assert e.grade(said, rule, set()) == "dodged the question"


@pytest.mark.parametrize(
    "say",
    [
        "This is a squad callout, not a command for me. Press nothing. Copy.",
        "This is chat for the squad, not a request for me, so I'm just acknowledging it. Copy.",
        "The player trailed off mid-request, so there's nothing to act on yet.",
        "This is chat aimed at the squad or the room, not for me, so no action. Copy.",
        "This sounds like squad chat, not a request for me, so I'll just acknowledge it. Copy.",
        "This is chat to me, not a request, so a short friendly reply. Glad you're enjoying it.",
        # #392: the note sent with the request, read aloud. It passed.
        'The player\'s request is "head lights" with their own key bound: Headlights on L. '
        "That's a game command. Your headlights are on L.",
        "The bracketed line says Headlights on L.",
        "The player's own keys for this: Headlights on L.",
    ],
)
def test_a_reply_that_explains_itself_fails(say: str) -> None:
    """The #192 eval after #278: Haiku read its reasoning aloud, and these passed."""
    e = _eval()
    assert e.grade(_outcome(say=say), {"expect": ["reply"]}, set()).startswith("narrated")


@pytest.mark.parametrize(
    "say",
    [
        "Copy.",
        "Acknowledged.",
        "Go ahead, Commander. What do you need?",
        "There's no action bound for that.",
        "In 4.10 the player can claim a ship at any terminal.",
        "You're welcome, Commander.",
        "That's not a command I know.",
        "It's 2 Gm, so a short hop.",
        "Glad you're enjoying it, Commander. Take a moment and soak it in.",
        # #437: a good answer naming the player's key, which the old pattern failed.
        "Your own key for this is N.",
    ],
)
def test_a_plain_reply_is_not_narrated(say: str) -> None:
    e = _eval()
    assert e.grade(_outcome(say=say), {"expect": ["reply"]}, set()) == "pass"


def test_the_kraken_case_takes_a_right_answer_worded_another_way() -> None:
    """The #192 eval after #280: "I don't have an official release date" failed."""
    e = _eval()
    data = tomllib.loads(
        (ROOT / "src" / "tycho" / "packs" / "star-citizen" / "eval" / "cases.toml").read_text(
            "utf-8"
        )
    )
    rule = next(c for c in data["case"] if c["heard"] == "when is the Kraken coming out")
    said = _outcome(say="The Kraken is in concept. I don't have an official release date for it.")
    assert e.grade(said, rule, set()) == "pass"


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
    "mode",
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
    "max_words",
    "key_for",
    "notes",
    "every_game_notes",
    "notes_has",
    "notes_lack",
    "notes_unchanged",
    "never_say",
    "unbound",
    "picture",
    "may_look",
    "offers_look",
}


@pytest.mark.parametrize("pack", ["star-citizen", "wow-forever"])
def test_every_case_uses_only_keys_the_eval_reads(pack: str) -> None:
    """A misspelt key ("max_call") would be ignored, and its check silently skipped."""
    data = tomllib.loads(
        (ROOT / "src" / "tycho" / "packs" / pack / "eval" / "cases.toml").read_text("utf-8")
    )
    unknown = {(c["heard"], k) for c in data["case"] for k in c if k not in KNOWN}
    assert not unknown
    assert set(data) <= {"case", "never_say", "say_words"}  # the file's own keys


def test_a_line_said_with_a_press_is_a_few_words() -> None:
    # docs/voice-and-tone.md (#363): the cases file's say_words limits the say line.
    e = _eval()
    rule = {"expect": ["actions"], "must": [["v_lights"]]}
    long = "Lights are on now, and I have also had a look at everything else for you."
    pressed = _outcome(kind="actions", actions=["v_lights"], say=long)
    assert e.grade(pressed, rule, {"v_lights"}) == "pass"  # no limit, as before
    verdict = e.grade(pressed, rule, {"v_lights"}, say_words=15)
    assert verdict.startswith("said 16 words, at most 15")
    short = _outcome(kind="actions", actions=["v_lights"], say="Lights on.")
    assert e.grade(short, rule, {"v_lights"}, say_words=15) == "pass"
    # A reply isn't a say line: the file's limit doesn't apply to it.
    assert e.grade(_outcome(say=long), {"expect": ["reply"]}, set(), say_words=15) == "pass"


def test_a_case_can_limit_its_reply_s_words() -> None:
    e = _eval()
    reply = _outcome(say="No. Skyborne can't be Paladins.")
    assert e.grade(reply, {"expect": ["reply"], "max_words": 5}, set()) == "pass"
    verdict = e.grade(reply, {"expect": ["reply"], "max_words": 4}, set())
    assert verdict == 'said 5 words, at most 4: "No. Skyborne can\'t be Paladins."'
    # The case's limit is for a reply; a press it also allows is the file's business.
    pressed = _outcome(kind="actions", actions=["v_lights"], say="Lights on, as you asked.")
    rule = {"expect": ["actions", "reply"], "max_words": 2}
    assert e.grade(pressed, rule, {"v_lights"}) == "pass"


def test_every_case_says_what_the_player_wants() -> None:
    """#24: a router is graded on each case's mode (ADR 10), so none may be missing."""
    _eval()  # puts tools/bench on the path
    from brain_report import MODES  # pyright: ignore[reportMissingImports, reportUnknownVariableType]

    data = tomllib.loads(
        (ROOT / "src" / "tycho" / "packs" / "star-citizen" / "eval" / "cases.toml").read_text(
            "utf-8"
        )
    )
    assert not [c["heard"] for c in data["case"] if c.get("mode") not in MODES]


def test_planning_cases_accept_a_look_up() -> None:
    """#257: brain_report and jev_report grade look_up apart from reply, and a trade
    plan rightly starts with one."""
    data = tomllib.loads(
        (ROOT / "src" / "tycho" / "packs" / "star-citizen" / "eval" / "cases.toml").read_text(
            "utf-8"
        )
    )
    planning = [c for c in data["case"] if c["mode"] == "planning"]
    assert planning
    assert all("look_up" in c["expect"] for c in planning)


def test_the_add_command_skill_writes_cases_with_a_mode() -> None:
    """#256: a case without one makes the eval refuse the whole file."""
    for folder in (".claude", ".agents"):
        skill = ROOT / folder / "skills" / "add-voice-command" / "SKILL.md"
        assert '`mode = "command"`' in skill.read_text("utf-8")


def test_the_model_benchmarks_refuse_a_case_with_no_mode(tmp_path: Path) -> None:
    _eval()  # puts tools/bench on the path
    from brain_report import load_cases  # pyright: ignore[reportMissingImports, reportUnknownVariableType]

    cases_file = tmp_path / "cases.toml"
    cases_file.write_text('[[case]]\nheard = "gear down"\nexpect = ["actions"]\n', "utf-8")
    with pytest.raises(SystemExit, match="gear down"):
        load_cases(cases_file, engine="", results=tmp_path)


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
    level = logger.level
    logger.addHandler(costs)
    logger.setLevel(logging.DEBUG)
    try:
        rule = {"heard": "xyzzy plugh frobnicate", "expect": ["reply", "actions"]}
        outcome = asyncio.run(
            e.run_case(
                rule,
                Lexicon.default(pack=SC),
                _ScriptedBrain(replies, decision),
                _keybinds(),
                None,
                [],
                costs,
                lists=SC.safety,
            )
        )
    finally:
        logger.removeHandler(costs)
        logger.setLevel(level)
    assert outcome.route == "brain"
    assert outcome.calls == calls


class _NoBrain:
    async def decide(self, request: object) -> object:
        raise AssertionError("say that again reached the brain")


def test_say_that_again_never_reaches_the_brain() -> None:
    """#222: the session replays the last answer itself."""
    from tycho.lexicon import Lexicon

    e = _eval()
    rule = {"heard": "say that again", "expect": ["say_again"], "max_calls": 0}
    outcome = asyncio.run(
        e.run_case(rule, Lexicon.default(pack=SC), _NoBrain(), _keybinds(), lists=SC.safety)
    )
    assert (outcome.route, outcome.kind, outcome.calls) == ("session", "say_again", 0)
    assert e.grade(outcome, rule, set()) == "pass"


def test_the_say_that_again_cases_are_in_the_eval() -> None:
    data = tomllib.loads(
        (ROOT / "src" / "tycho" / "packs" / "star-citizen" / "eval" / "cases.toml").read_text(
            "utf-8"
        )
    )
    assert any(c["expect"] == ["say_again"] for c in data["case"])


def test_the_model_benchmarks_leave_out_the_say_that_again_cases(tmp_path: Path) -> None:
    """Review of #225: jev_report and brain_report grade a model, which never sees them."""
    _eval()  # puts tools/bench on the path
    from brain_report import load_cases  # pyright: ignore[reportMissingImports, reportUnknownVariableType]

    cases_file = tmp_path / "cases.toml"
    cases_file.write_text(
        '[[case]]\nheard = "say that again"\nmode = "command"\nexpect = ["say_again"]\n\n'
        '[[case]]\nheard = "gear down"\nmode = "command"\nexpect = ["actions"]\n',
        "utf-8",
    )
    cases, _ = load_cases(cases_file, engine="", results=tmp_path)  # pyright: ignore[reportUnknownVariableType]
    assert [c.heard for c in cases] == ["gear down"]  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]


def _keybinds() -> object:
    from tests.test_runtime import KEYBINDS

    return KEYBINDS


def test_the_model_benchmarks_load_the_real_case_file(tmp_path: Path) -> None:
    """#228: a duplicate case made brain_report and jev_report exit before running.
    #235: the results file goes where it's told, not into the checkout's recordings/."""
    _eval()  # puts tools/bench on the path
    from brain_report import load_cases  # pyright: ignore[reportMissingImports, reportUnknownVariableType]

    results = tmp_path / "recordings"
    cases, out = load_cases(  # pyright: ignore[reportUnknownVariableType]
        ROOT / "src" / "tycho" / "packs" / "star-citizen" / "eval" / "cases.toml",
        engine="",
        results=results,
    )
    assert cases
    assert out == results / "star-citizen.cases.results.jsonl"  # each pack its own (#323)
    assert results.is_dir()


def test_only_a_pack_s_cases_are_named_after_their_pack(tmp_path: Path) -> None:
    _eval()  # puts tools/bench on the path
    from brain_report import results_name  # pyright: ignore[reportMissingImports, reportUnknownVariableType]

    pack = tmp_path / "some-folder"
    (pack / "eval").mkdir(parents=True)
    (pack / "pack.toml").write_text('id = "some-game"\n', "utf-8")
    assert results_name(pack / "eval" / "cases.toml") == "some-game.cases"  # its id (#342)
    assert results_name(tmp_path / "brain_cases.toml") == "brain_cases"
    (tmp_path / "eval").mkdir()
    assert results_name(tmp_path / "eval" / "cases.toml") == "cases"  # no pack.toml above


# --- Across models (#192) ---------------------------------------------------------------


def _run(provider: str, verdicts: list[str], cost: float = 0.01, calls: int = 1) -> Any:
    e = _eval()
    outcomes = [
        _outcome(heard=f"case {n}", verdict=v, calls=calls, ms=1000.0 + n)
        for n, v in enumerate(verdicts)
    ]
    costs = e._Costs()
    costs.add(cost, "reported")
    return e.Run(provider, "some/model", outcomes, costs)


def test_the_eval_takes_a_provider_and_a_model() -> None:
    e = _eval()
    args = e._arguments(["--provider", "openrouter", "--model", "deepseek/deepseek-v4"])
    assert (args.provider, args.model, args.per_hour) == ("openrouter", "deepseek/deepseek-v4", 30)
    assert e._arguments([]).provider == "anthropic"
    with pytest.raises(SystemExit):
        e._arguments(["--provider", "openrouter"])  # which model?
    assert e._arguments(["--adapter-check"]).no_web  # neither side searches the web


def test_the_eval_pins_a_host_only_through_openrouter() -> None:
    # #267: --host and --sort go to OpenRouter's provider routing.
    e = _eval()
    model = ["--provider", "openrouter", "--model", "openai/gpt-oss-120b"]
    args = e._arguments([*model, "--host", "cerebras", "--host", "groq"])
    assert (args.host, args.sort) == (["cerebras", "groq"], None)
    assert e._arguments([*model, "--sort", "latency"]).sort == "latency"
    with pytest.raises(SystemExit):
        # #275: whether a sort reorders pinned hosts isn't documented, so not both.
        e._arguments([*model, "--host", "cerebras", "--sort", "latency"])
    assert e._arguments([]).host == []
    with pytest.raises(SystemExit):
        e._arguments(["--host", "cerebras"])  # Claude directly has no hosts to pick
    with pytest.raises(SystemExit):
        e._arguments(["--provider", "openrouter", "--model", "x/y", "--sort", "fastest"])


def test_each_result_says_which_host_served_it() -> None:
    # #267: the host is read from each call's log line, case by case, and counted.
    # #275: the lines are the ones a real OpenRouterBrain logs, so a change to their
    # format fails here instead of leaving the hosts quietly empty.
    from tests.fakes.fake_chat import FakeChat, text, tool_call
    from tycho.brain.openrouter import OpenRouterBrain
    from tycho.commands import CommandMatcher
    from tycho.keybinds import Keybinds
    from tycho.lexicon import Lexicon

    def served(host: str, chunks: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
        return [{**c, "provider": host} for c in chunks]

    def brain(*calls: Sequence[Mapping[str, Any]]) -> OpenRouterBrain:
        model = "openai/gpt-oss-120b"
        return OpenRouterBrain(
            CommandMatcher.default(pack=SC), model=model, client=FakeChat(*calls), pack=SC
        )

    act = {"actions": ["v_toggle_landing_system"], "say": "Gear."}
    e = _eval()
    costs = e._Costs()  # pyright: ignore[reportPrivateUsage]
    logger = logging.getLogger("tycho.brain")
    level = logger.level
    logger.addHandler(costs)
    logger.setLevel(logging.DEBUG)
    rule = {"heard": "xyzzy plugh frobnicate", "expect": ["actions"]}
    try:
        asyncio.run(brain(served("Groq", text("Ready."))).warm(cast(Keybinds, _keybinds())))
        outcomes = [
            asyncio.run(
                e.run_case(
                    rule, Lexicon.default(pack=SC), b, _keybinds(), None, [], costs, lists=SC.safety
                )
            )
            for b in (
                brain(served("Cerebras", text("Copy."))),
                brain(served("Cerebras", tool_call("run_actions", act))),
            )
        ]
    finally:
        logger.removeHandler(costs)
        logger.setLevel(level)
    assert [o.hosts for o in outcomes] == [["Cerebras"], ["Cerebras"]]
    assert costs.hosts == ["Groq", "Cerebras", "Cerebras"]
    pinned = e.route(["cerebras"])  # what the eval sends (#497)
    summary = e.metrics(e.Run("openrouter", "openai/gpt-oss-120b", outcomes, costs, pinned))
    assert summary["hosts"] == {"Cerebras": 2}  # the warm-up isn't a case
    assert summary["route"] == pinned
    assert e._pinned(pinned) == " (on cerebras only)"  # pyright: ignore[reportPrivateUsage]


def test_costs_say_where_they_came_from() -> None:
    e = _eval()
    costs = e._Costs()
    for message in (
        "Brain replied in 900 ms: 1000 in, 20 out, 800 cached, about $0.00042 (reported)",
        "Brain replied in 900 ms: 1000 in, 20 out, 800 cached, about $0.00010 (price list)",
        "Brain replied in 900 ms: 1 in, 1 out, 0 cached, 0 cache write, about $0.00020",
        "Brain acted in 700 ms, generation gen-123",
        "Brain acted in 700 ms, generation ?",
    ):
        costs.emit(logging.makeLogRecord({"msg": message}))
    assert costs.sources == {"reported": 1, "price list": 1, "estimate": 1}
    assert costs.total == pytest.approx(0.00072)
    assert costs.replies == 3
    assert costs.generations == ["gen-123"]
    assert costs.unreported == 1  # the act with no generation id to look its cost up by


def test_a_call_with_no_cost_is_counted_not_dropped() -> None:
    e = _eval()
    costs = e._Costs()
    for message in (
        "Brain replied in 900 ms: no usage reported",
        "Brain replied in 900 ms: 1000 in, 20 out, 800 cached",  # no price list either
        "Brain warmed up in 300 ms: no usage reported",
        "The brain chose to act after a web search; not pressing",  # not a call
    ):
        costs.emit(logging.makeLogRecord({"msg": message}))
    assert (costs.unreported, costs.counted) == (3, 0)


def test_a_failed_web_search_isnt_a_call_with_no_cost() -> None:
    # #264: "Web search failed" carries no cost, and the request most likely cost nothing.
    e = _eval()
    costs = e._Costs()
    for message in (
        "Web search failed: Overloaded",
        "Web search in 2100 ms: 900 in, 80 out, 3 sites",  # answered, no price: counted
        "Web search 'kraken price' in 2100 ms: 900 in, 80 out, 3 sites, about $0.01100",
    ):
        costs.emit(logging.makeLogRecord({"msg": message}))
    assert (costs.unreported, costs.counted) == (1, 1)


def test_generation_costs_that_cant_be_found_are_kept_and_reported(
    capsys: pytest.CaptureFixture[str],
) -> None:
    # OpenRouter gives gen-1's cost; gen-2 never has one; gen-3 has none on the first ask.
    e = _eval()
    asked: dict[str, int] = {}

    def handler(request: httpx2.Request) -> httpx2.Response:
        generation = request.url.params["id"]
        asked[generation] = asked.get(generation, 0) + 1
        if generation == "gen-1":
            return httpx2.Response(200, json={"data": {"total_cost": 0.0003}})
        if generation == "gen-3" and asked[generation] > 1:
            return httpx2.Response(200, json={"data": {"total_cost": 0.0001}})
        return httpx2.Response(404, json={"error": {"message": "not found"}})

    costs = e._Costs()
    costs.generations = ["gen-1", "gen-2", "gen-3"]
    asyncio.run(e.generation_costs(costs, "sk-or-v1-test", httpx2.MockTransport(handler), 0))
    assert costs.generations == ["gen-2"]
    assert costs.total == pytest.approx(0.0004)
    assert asked == {"gen-1": 1, "gen-2": 3, "gen-3": 2}
    assert costs.looked_up == {"gen-1": 0.0003, "gen-3": 0.0001}  # for the traces (#317)
    summary = e.metrics(e.Run("openrouter", "some/model", [_outcome()], costs))
    assert summary["calls_without_cost"] == 1
    assert summary["cost_sources"] == {"reported": 2, "not reported": 1}
    e._summary([_outcome(verdict="pass")], costs, summary)
    assert "1 call(s) had no cost reported" in capsys.readouterr().out


def test_cost_per_request_and_per_hour_of_play() -> None:
    e = _eval()
    summary = e.metrics(_run("openrouter", ["pass", "pass", "WRONG PRESS ['v_eject']", "x"]), 60)
    assert summary["passed"] == 2
    assert summary["wrong_presses"] == 1
    assert summary["model"] == "some/model"
    assert summary["cost_per_request"] == pytest.approx(0.0025)
    assert summary["cost_per_hour"] == pytest.approx(0.15)


def test_key_clashes_are_counted_apart_from_wrong_presses() -> None:
    """#494: a clash on the player's keybinds is the same for every model."""
    e = _eval()
    clash = f"{e.KEY_CLASH} ['v_self_destruct'] (refused: on backspace)"
    summary = e.metrics(_run("anthropic", ["pass", clash, "WRONG PRESS ['v_eject']"]))
    assert summary["wrong_presses"] == 1
    assert summary["key_clashes"] == 1
    assert summary["passed"] == 1


def test_the_price_list_gives_prices_per_token() -> None:
    e = _eval()
    listed = [
        {"id": "other/model", "pricing": {"prompt": "1", "completion": "1"}},
        {
            "id": "some/model",
            "pricing": {"prompt": "0.0000001", "completion": "0.0000005", "input_cache_read": ""},
        },
    ]
    prices = e.prices_for(listed, "some/model")
    assert (prices.prompt, prices.completion, prices.cache_read) == (1e-7, 5e-7, None)
    assert e.prices_for(listed, "missing/model") is None


def test_a_variant_takes_its_models_prices() -> None:
    # ":nitro" isn't in the list, so its runs had no prices to fall back on (#268).
    e = _eval()
    listed = [{"id": "some/model", "pricing": {"prompt": "0.0000001", "completion": "0.0000005"}}]
    prices = e.prices_for(listed, "some/model:nitro")
    assert prices is not None
    assert prices.prompt == 1e-7
    assert e.prices_for(listed, "missing/model:nitro") is None


def test_the_adapter_check_says_close_when_the_runs_agree() -> None:
    e = _eval()
    direct, routed = _run("anthropic", ["pass"] * 20), _run("openrouter", ["pass"] * 20)
    lines = e.compare(direct, routed)
    assert lines[-1].startswith("Adapter check: close")
    assert not any("differ" in line for line in lines)


@pytest.mark.parametrize(
    ("verdicts", "calls"),
    [
        (["pass"] * 18 + ["missing v_eject"] * 2, 1),  # 10 points apart
        (["pass"] * 19 + ["WRONG PRESS ['v_eject']"], 1),  # any wrong press
        (["pass"] * 20, 2),  # many more calls a request
    ],
)
def test_the_adapter_check_says_far_apart(verdicts: list[str], calls: int) -> None:
    e = _eval()
    lines = e.compare(_run("anthropic", ["pass"] * 20), _run("openrouter", verdicts, calls=calls))
    assert lines[-1].startswith("Adapter check: FAR APART")


# --- Running off the PC (#262) -----------------------------------------------------------


def test_keybinds_go_through_a_file_unchanged(tmp_path: Path) -> None:
    from tycho.keybinds import Action, ActivationMode, Keybinds, KeyInput

    e = _eval()
    keybinds = Keybinds(
        [
            Action("v_toggle_landing_system", "spaceship_movement", "Landing", (KeyInput("n"),)),
            Action(
                "v_eject",
                "seat_general",
                "Eject",
                (KeyInput("y", ("ralt",)), KeyInput("mouse4")),
                activation="delayed_press",
                customized=True,
                multi_tap=2,
            ),
            Action("v_unbound", "m", "", ()),
        ],
        {"delayed_press": ActivationMode("delayed_press", on_hold=True, press_threshold=0.25)},
    )
    path = tmp_path / "keybinds.json"
    own = '[[command]]\nid = "gear_down"\nphrases = ["wheels out"]\n'
    e.export_keybinds(keybinds, path, "LIVE 123-456", own)
    loaded, game, commands = e.load_keybinds(path)
    assert list(loaded) == list(keybinds)
    assert loaded.modes == keybinds.modes
    assert game.startswith("LIVE 123-456")
    assert commands == own


def test_a_keybinds_file_from_another_format_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "keybinds.json"
    path.write_text('{"format": 99, "actions": []}', "utf-8")
    with pytest.raises(SystemExit, match="export it again"):
        _eval().load_keybinds(path)


@pytest.mark.parametrize(
    "text",
    [
        "{not json",
        '{"format": 1, "modes": {}}',  # no actions
        '{"format": 1, "actions": []}',  # no modes
        '{"format": 1, "actions": [{"name": "v_eject"}], "modes": {}}',  # no inputs
        '{"format": 1, "actions": [{"inputs": [], "colour": "red"}], "modes": {}}',
        "[1, 2]",
        '{"format": 1, "actions": [{"name": "v_eject", "inputs": ["kb1_a"]}], "modes": {}}',
        '{"format": 1, "actions": [{"name": "v_eject", "inputs": [[1]]}], "modes": {}}',
    ],
)
def test_a_broken_keybinds_file_says_export_it_again(tmp_path: Path, text: str) -> None:
    # #265: no raw JSONDecodeError or KeyError traceback.
    path = tmp_path / "keybinds.json"
    path.write_text(text, "utf-8")
    with pytest.raises(SystemExit, match="export it again"):
        _eval().load_keybinds(path)


def test_the_eval_takes_a_keybinds_file() -> None:
    e = _eval()
    args = e._arguments(["--keybinds", "binds.json"])
    assert args.keybinds == Path("binds.json")
    assert e._arguments(["--export-keybinds", "out.json"]).export_keybinds == Path("out.json")
    assert e._arguments([]).keybinds is None


def test_keys_come_from_the_environment_only_when_none_is_stored(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    e = _eval()
    secret = "sk-or-v1-from-the-environment-0000"  # noqa: S105 - a made-up key
    monkeypatch.setenv("OPENROUTER_API_KEY", secret)
    assert e.eval_key("openrouter", lambda: "sk-or-v1-stored-key-1111") == (
        "sk-or-v1-stored-key-1111"
    )
    assert e.eval_key("openrouter", lambda: None) == secret
    out = capsys.readouterr().out
    assert "OPENROUTER_API_KEY" in out
    assert secret not in out  # never printed
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert e.eval_key("anthropic", lambda: None) is None
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-​pasted-with-a-zero-width-space")
    assert e.eval_key("anthropic", lambda: None) is None


def test_an_environment_key_is_scrubbed_from_logs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tycho import logs

    secret = "sk-ant-environment-only-key-2222"  # noqa: S105 - a made-up key
    monkeypatch.setenv("ANTHROPIC_API_KEY", secret)
    _eval().eval_key("anthropic", lambda: None)
    assert secret not in logs.redact(f"key={secret}")


def test_the_runtime_imports_without_windows() -> None:
    # A cloud thread runs the eval on Linux, which imports the runtime (#262): loading
    # Windows' kernel32 must wait until a Tycho actually starts.
    import subprocess

    code = (
        "import ctypes\n"
        "def no_windows(*a, **k): raise AttributeError('no WinDLL here')\n"
        "ctypes.WinDLL = no_windows\n"
        "import tycho.runtime.instance\n"
    )
    done = subprocess.run(  # noqa: S603 - this Python, our own code
        [sys.executable, "-c", code], capture_output=True, text=True, timeout=60, check=False
    )
    assert done.returncode == 0, done.stderr


def _act(actions: tuple[str, ...], keybinds: object, heard: str = "xyzzy plugh frobnicate") -> Any:
    from tycho.brain import RunActions
    from tycho.lexicon import Lexicon

    e = _eval()
    rule = {"heard": heard, "expect": ["actions"]}
    brain = _ScriptedBrain(0, RunActions(actions, "Done."))
    return asyncio.run(e.run_case(rule, Lexicon.default(pack=SC), brain, keybinds, lists=SC.safety))


def _with(*actions: tuple[str, str]) -> object:
    from tests.test_runtime import KEYBINDS, MODES, _action  # pyright: ignore[reportPrivateUsage]
    from tycho.keybinds import Keybinds

    return Keybinds([*KEYBINDS, *(_action(n, k) for n, k in actions)], MODES)


def test_the_eval_asks_first_wherever_the_safety_gate_does() -> None:
    """#55: the eval plans through the session's own gate. A dangerous action the
    confirm-grade list doesn't name yet is still asked about (the danger-word backstop),
    so allowing it isn't graded a wrong press."""
    e = _eval()
    keybinds = _with(("v_jettison_all_cargo", "j"))
    outcome = _act(("v_jettison_all_cargo",), keybinds)
    assert outcome.actions == ["v_jettison_all_cargo"]
    assert outcome.asks
    rule = {"expect": ["actions"], "allow": ["v_jettison_all_cargo"]}
    assert e.grade(outcome, rule, {"v_jettison_all_cargo"}) == "pass"


def test_a_pick_the_gate_refuses_is_still_graded_and_noted() -> None:
    """A key it shares with Eject: Tycho presses nothing, but the eval grades the pick."""
    keybinds = _with(("v_lights_toggle", "ralt+y"))  # Eject's key in the test keybinds
    outcome = _act(("v_lights_toggle",), keybinds)
    assert outcome.actions == ["v_lights_toggle"]
    assert any("would press nothing (refused" in note for note in outcome.notes)


def test_an_allowed_pick_refused_for_a_key_clash_is_its_own_outcome() -> None:
    # Eject on the same key as Self Destruct: the gate refuses it, so the plan never
    # "asks" (#311). The pick is right and every model would hit the same clash, so it's
    # a key clash, not a wrong press and not a pass (#494).
    e = _eval()
    keybinds = _with(("v_self_destruct", "ralt+y"))  # Eject's key in the test keybinds
    outcome = _act(("v_eject",), keybinds)
    assert outcome.actions == ["v_eject"]
    assert outcome.refused.startswith("refused")
    assert outcome.clash == "v_eject"
    verdict = e.grade(outcome, {"expect": ["actions"], "allow": ["v_eject"]}, {"v_eject"})
    assert verdict.startswith(e.KEY_CLASH)
    assert not verdict.startswith("WRONG PRESS")
    assert "not confirmed first" not in verdict
    # A pick the case forbids is still a wrong press, clash or not.
    never = e.grade(outcome, {"expect": ["actions"], "never": ["v_eject"]}, {"v_eject"})
    assert never.startswith("WRONG PRESS ['v_eject']; the gate refused it (refused")


def test_a_clash_excuses_only_the_allowed_press_that_clashed() -> None:
    """From the review of #498: an extra pick the case doesn't ask for isn't excused."""
    e = _eval()
    outcome = _outcome(
        kind="actions",
        actions=["v_lights_toggle", "v_eject"],
        refused="refused: Eject is on ralt+y",
        clash="v_eject",
    )
    rule: dict[str, Any] = {"expect": ["actions"], "allow": ["v_eject"]}
    assert e.grade(outcome, rule, {"v_eject", "v_lights_toggle"}).startswith("WRONG PRESS")
    rule["must"] = [["v_lights_toggle"]]
    assert e.grade(outcome, rule, {"v_eject", "v_lights_toggle"}).startswith(e.KEY_CLASH)
    outcome.clash = "v_lights_toggle"  # the clash was on a press the case doesn't allow
    assert e.grade(outcome, rule, {"v_eject", "v_lights_toggle"}).startswith("WRONG PRESS")


def test_a_fast_path_command_with_no_key_bound_is_not_a_wrong_press() -> None:
    """#508: "enter remote turret" takes the fast path to an action with no key on these
    keybinds. Tycho presses nothing and says so; nobody picked a key."""
    from tycho.lexicon import Lexicon

    e = _eval()
    rule: dict[str, Any] = {"heard": "enter remote turret", "expect": ["actions", "reply"]}
    outcome = asyncio.run(
        e.run_case(
            rule, Lexicon.default(pack=SC), _ScriptedBrain(0, None), _with(), lists=SC.safety
        )
    )
    assert outcome.route == "fast"
    assert outcome.kind == "no_key"
    assert outcome.refused.startswith("no_key")
    rule["must"] = [["v_enter_remote_turret_1"]]
    verdict = e.grade(outcome, rule, set())
    assert verdict == "pass"
    outcome.verdict = verdict  # metrics counts verdicts, so the check below can fail (#512)
    summary = e.metrics(e.Run("anthropic", "some/model", [outcome], e._Costs()))
    assert summary["wrong_presses"] == 0
    # The fast path matching another unbound command doesn't pass (#512).
    wrong = e.grade(outcome, {**rule, "must": [["v_some_other_action"]]}, set())
    assert wrong.startswith("missing v_some_other_action")
    assert "WRONG" not in wrong
    # A case that wants only a press doesn't pass on a missing key, but isn't a wrong one.
    only = e.grade(outcome, {"expect": ["actions"]}, set())
    assert only.startswith("expected actions")
    assert "WRONG" not in only


def test_a_clash_on_a_command_with_setup_presses_is_still_a_clash() -> None:
    """From the review of #500: a refused command's setup presses are Tycho's, not picks
    the model made, so they don't turn a key clash into a wrong press."""
    e = _eval()
    outcome = _outcome(
        kind="actions",
        actions=["v_master_mode_set_nav", "v_toggle_qdrive_engagement"],
        setup=["v_master_mode_set_nav"],
        refused="refused: on mouse1",
        clash="v_toggle_qdrive_engagement",
    )
    rule: dict[str, Any] = {"expect": ["actions"], "allow": ["v_toggle_qdrive_engagement"]}
    valid = {"v_master_mode_set_nav", "v_toggle_qdrive_engagement"}
    assert e.grade(outcome, rule, valid).startswith(e.KEY_CLASH)
    outcome.clash = "v_master_mode_set_nav"  # the clash on the setup press itself
    assert e.grade(outcome, rule, valid).startswith(e.KEY_CLASH)


def test_a_step_with_no_pressable_key_before_a_clash_is_not_a_clash() -> None:
    """From the review of #498: plan_request refuses "no_key" at the first step, so the
    clash on the second never decides anything."""
    from tycho.commands import Command

    e = _eval()
    keybinds = _with(("v_self_destruct", "ralt+y"), ("v_lights_toggle", "notakey"))
    outcome = _act(("v_lights_toggle", "v_eject"), keybinds)
    assert outcome.refused.startswith("no_key")
    assert outcome.clash == ""
    commands = (
        Command(id="lights", say=(), actions=("v_lights_toggle",)),
        Command(id="eject", say=(), actions=("v_eject",)),
    )
    assert e._refused_for_clash(commands, keybinds, frozenset(), SC.safety) == ""
    assert e._refused_for_clash(commands[1:], keybinds, frozenset(), SC.safety) == "v_eject"


def test_an_allowed_pick_refused_for_another_reason_is_still_a_wrong_press() -> None:
    e = _eval()
    outcome = _outcome(kind="actions", actions=["v_eject"], refused="refused: not now")
    verdict = e.grade(outcome, {"expect": ["actions"], "allow": ["v_eject"]}, {"v_eject"})
    assert verdict.startswith("WRONG PRESS (the gate refused it: refused: not now")


def test_a_pick_with_a_key_of_its_own_is_not_marked_a_clash() -> None:
    outcome = _act(("v_eject",), _with())
    assert not outcome.refused
    assert not outcome.clash


def test_the_fast_path_is_planned_through_the_gate_too() -> None:
    from tycho.lexicon import Lexicon

    e = _eval()
    rule = {"heard": "eject", "expect": ["actions"]}
    outcome = asyncio.run(
        e.run_case(rule, Lexicon.default(pack=SC), _NoBrain(), _keybinds(), lists=SC.safety)
    )
    assert (outcome.route, outcome.actions, outcome.asks) == ("fast", ["v_eject"], True)


def test_the_tiles_words_never_reach_the_brain_while_it_is_on() -> None:
    from tycho.lexicon import Lexicon

    e = _eval()
    rule = {"heard": "keep that up", "expect": ["reply"]}
    outcome = asyncio.run(
        e.run_case(rule, Lexicon.default(pack=SC), _NoBrain(), _keybinds(), lists=SC.safety)
    )
    assert (outcome.route, outcome.kind) == ("session", "pin")


# --- Advisor packs: Tycho presses nothing, and never says it did (#326) -----------------


@pytest.mark.parametrize(
    "say",
    [
        "Mounting up.",
        "Done.",
        "On it!",
        "Hearthing.",
        "Casting now.",
        "I've opened your map.",
        "I cast it.",
    ],
)
def test_a_reply_that_claims_a_press_fails_with_keys_off(say: str) -> None:
    e = _eval()
    outcome = _outcome(say=say)
    verdict = e.grade(outcome, {"expect": ["reply"]}, set(), keys_off=True)
    assert verdict.startswith("claimed a press with keys off")
    # With keys on, the same words aren't a claim the eval checks here.
    assert e.grade(outcome, {"expect": ["reply"]}, set()) == "pass"


@pytest.mark.parametrize(
    "say",
    [
        "I don't press keys in this game. Your mount key summons it.",
        "Opening the map is M by default, and the zone map is shift M.",
        "Casting while moving only works for instant spells.",
        "Okay, back soon.",
        "Casting is faster with more haste.",
        "Jumping puzzles reward a toy.",
        "Not unless I cast it, and I can't.",
    ],
)
def test_a_reply_that_says_how_passes_with_keys_off(say: str) -> None:
    e = _eval()
    assert e.grade(_outcome(say=say), {"expect": ["reply"]}, set(), keys_off=True) == "pass"


def test_a_press_case_passes_on_a_plain_reply_with_keys_off() -> None:
    e = _eval()
    rule = {"expect": ["actions"], "must": [["v_toggle_landing_system"]]}
    said = _outcome(say="Key actions are off, so lower your gear yourself: N by default.")
    assert e.grade(said, rule, set()) == "expected actions"
    assert e.grade(said, rule, set(), keys_off=True) == "pass"
    claimed = _outcome(say="Done.")
    assert e.grade(claimed, rule, set(), keys_off=True).startswith("claimed a press")


def test_with_keys_off_the_eval_tells_the_brain_the_player_s_own_key() -> None:
    # #386 review: the eval grades the path players take, so it sends own_keys too.
    from tycho.brain import Reply
    from tycho.lexicon import Lexicon
    from tycho.packs import as_run, built_in

    e = _eval()
    asked: list[Any] = []

    class Brain:
        async def decide(self, request: Any) -> Any:
            asked.append(request)
            return Reply("I don't press keys here.")

    lists = as_run(built_in("star-citizen"), key_actions=False).safety
    rule = {"heard": "landing gear", "expect": ["reply"]}
    asyncio.run(e.run_case(rule, Lexicon.default(pack=SC), Brain(), _keybinds(), lists=lists))
    (request,) = asked
    assert request.own_keys.endswith(" on N")


def test_with_keys_off_the_eval_counts_replies_that_name_the_player_s_key() -> None:
    # #392: "hit the brakes" passed with "check your keybinds". A measure, not a verdict.
    e = _eval()
    keybinds = _keybinds()
    rule = {"heard": "gear", "expect": ["actions"], "must": [["v_toggle_landing_system"]]}
    assert e.names_key("I don't press keys here. Your landing gear is on N.", rule, keybinds)
    assert e.names_key("Check your keybinds for the landing gear.", rule, keybinds) is False
    assert e.names_key("Not now.", rule, keybinds) is False  # "N" inside a word isn't the key
    assert e.names_key("On N.", {"expect": ["reply"]}, keybinds) is None
    assert e.names_key("On N.", {"must": [["v_not_bound"]]}, keybinds) is None


def test_keys_off_runs_the_pack_as_advisor() -> None:
    e = _eval()
    args = e._arguments(["--keys-off", "--no-web"])
    assert args.keys_off
    from tycho.packs import as_run, built_in

    assert as_run(built_in(args.pack), key_actions=False).safety.keys_off


def test_an_advisor_pack_is_offered_only_the_look_ups_it_names() -> None:
    from tycho.packs import built_in

    e = _eval()
    pack = built_in("wow-forever")
    wanted = {*pack.parts.lookups, "settings"}
    tools = {name: FakeLookup(name=name) for name in ("ships", "trade", "settings", "web")}
    offered = e.pack_lookups(wanted, [*tools.items(), ("items", None)])
    assert offered == [tools["settings"]]
    assert e.pack_lookups({"ships", "settings"}, list(tools.items())) == [
        tools["ships"],
        tools["settings"],
    ]


def test_a_reply_with_another_game_s_words_fails() -> None:
    # World of Warcraft: Forever's cases fail "quantum" and "Commander" (#349).
    import re

    e = _eval()
    never = [re.compile(r"\bquantum\b", re.IGNORECASE), re.compile(r"\bCommander\b")]
    leaked = _outcome(say="Enjoy the drink, Commander.")
    verdict = e.grade(leaked, {"expect": ["reply"]}, set(), keys_off=True, never_say=never)
    assert verdict.startswith("said 'Commander', from another game")
    fine = _outcome(say="Take the boat from Menethil Harbor.")
    assert e.grade(fine, {"expect": ["reply"]}, set(), keys_off=True, never_say=never) == "pass"
    # Without the list, the old verdict stands.
    assert e.grade(leaked, {"expect": ["reply"]}, set(), keys_off=True) == "pass"


def test_a_case_s_own_never_say_fails_its_line() -> None:
    # #545: with only the doors' toggle, the line mustn't say they're open.
    e = _eval()
    rule = {
        "expect": ["actions"],
        "must": [["v_lights_on"], ["v_toggle_all_doors"]],
        "never_say": [r"\bopen(s|ed|ing)?\b(?!/)"],
    }
    valid = {"v_lights_on", "v_toggle_all_doors"}
    actions = ["v_lights_on", "v_toggle_all_doors"]
    claimed = _outcome(kind="actions", actions=actions, say="Lights on, doors open.")
    verdict = e.grade(claimed, rule, valid)
    assert verdict.startswith("said 'open', which this case rules out")
    fine = _outcome(kind="actions", actions=actions, say="Lights on, Open/Close Doors toggled.")
    assert e.grade(fine, rule, valid) == "pass"


def test_a_case_can_unbind_actions() -> None:
    from tycho.keybinds import Action, Keybinds, KeyInput

    e = _eval()
    door = Action("v_open_all_doors", "spaceship_general", "Open All Doors", (KeyInput("o"),))
    toggle = Action("v_toggle_all_doors", "spaceship_general", "", (KeyInput("p"),))
    keybinds = Keybinds([door, toggle])
    assert e.without(keybinds, []) is keybinds
    left = e.without(keybinds, ["v_open_all_doors"])
    assert not left.get("v_open_all_doors").bound  # pyright: ignore[reportOptionalMemberAccess]
    assert left.get("v_toggle_all_doors").bound  # pyright: ignore[reportOptionalMemberAccess]
    assert keybinds.get("v_open_all_doors").bound  # pyright: ignore[reportOptionalMemberAccess]
    with pytest.raises(SystemExit, match="v_open_all_door"):
        e.without(keybinds, ["v_open_all_door"])  # misspelt: it would unbind nothing


def test_a_case_grades_the_offer_to_look() -> None:
    # #548: the offer is the exact words a "yes" answers, and only where it should be.
    e = _eval()
    offer = _outcome(say="I can't see it from here. Want me to take a look?")
    wants = {"expect": ["reply"], "offers_look": True}
    assert e.grade(offer, wants, set()) == "pass"
    near = _outcome(say="I can't see it. Should I take a look?")
    assert e.grade(near, wants, set()) == "didn't offer to look"
    never = {"expect": ["reply"], "offers_look": False}
    assert e.grade(offer, never, set()).startswith("offered to look")
    assert e.grade(near, never, set()) == "pass"
    assert e.grade(offer, {"expect": ["reply"]}, set()) == "pass"  # not graded unless asked


def test_every_picture_a_case_names_is_drawn() -> None:
    e = _eval()
    data = tomllib.loads(
        (ROOT / "src" / "tycho" / "packs" / "star-citizen" / "eval" / "cases.toml").read_text(
            "utf-8"
        )
    )
    names = {c["picture"] for c in data["case"] if "picture" in c}
    assert names
    for name in names:
        seen = e.picture(name)
        assert max(seen.width, seen.height) <= 1568  # shrunk, as a real look is
        assert seen.media_type == "image/jpeg"
    with pytest.raises(SystemExit, match="no picture called"):
        e.picture("planett")


def test_cases_that_must_search_are_left_out_without_web_search() -> None:
    """#353's cases need ``web_search``: a run without it (OpenRouter, ``--no-web``)
    would fail them for want of the tool, not for what the brain did."""
    rules: list[dict[str, Any]] = [
        {"heard": "lights", "expect": ["actions"]},
        {"heard": "where is the fines terminal", "tools": ["web_search"]},
        {"heard": "ship", "tools": ["ship_lookup"]},
    ]
    kept, left_out = _eval().without_search(rules)
    assert [r["heard"] for r in kept] == ["lights", "ship"]
    assert left_out == 1


def test_the_star_citizen_cases_that_must_search_are_tagged() -> None:
    """#353: the search-first cases exist, so a run with web search grades them."""
    cases = tomllib.loads(
        (ROOT / "src/tycho/packs/star-citizen/eval/cases.toml").read_text("utf-8")
    )["case"]
    searching = [c for c in cases if "web_search" in c.get("tools", [])]
    assert len(searching) >= 3
    assert all(c["tag"] == "search_first" for c in searching)


def test_the_model_benchmark_asks_for_the_hour_long_cache_tycho_does() -> None:
    """#489: Tycho asks Anthropic to keep its briefing an hour (claude.py, and
    openrouter.py since #487), so the benchmark's cache-write costs must match."""
    _eval()  # puts brain_report on the path
    from brain_report import Brains  # pyright: ignore[reportMissingImports, reportUnknownVariableType]

    sent: dict[str, Any] = {}

    class Stop(Exception):
        pass

    class Messages:
        def stream(self, **kwargs: Any) -> Any:
            sent.update(kwargs)
            raise Stop

    class Client:
        class beta:  # the client's own attribute name
            messages = Messages()

    brains = Brains()
    brains._anthropic = Client()  # pyright: ignore[reportAttributeAccessIssue, reportPrivateUsage]
    with pytest.raises(Stop):
        brains.ask("claude-haiku-5-5", "the briefing", "lights on", "low")
    assert sent["system"][0]["cache_control"] == {"type": "ephemeral", "ttl": "1h"}


@pytest.mark.parametrize(
    ("say", "sentences"),
    [
        ("Which sounds?", 0),
        ("Which sounds, the engine or the music?", 0),
        ("Do you mean the engine? Or the music?", 2),
        ("The Hammerhead takes 8. It's a gunship. Want its weapons?", 3),
        ("Lights on.", 0),
        ("It's 3.5 km away, isn't it?", 0),  # a decimal point isn't a sentence end
        ("Hmm... which one?", 0),  # from the review of #500
        ("Which one, e.g. the engine or the music?", 0),
        ("Fuel, ammo, etc. Want the rest?", 2),  # from the review of #510 (#512)
        ("Fuel, ammo, etc. or the rest?", 0),
    ],
)
def test_a_question_longer_than_a_sentence_is_measured(say: str, sentences: int) -> None:
    """#430: a clarifying question is one sentence; the eval counts the ones that aren't."""
    assert _eval().long_question(say) == sentences


def test_a_number_on_the_card_counts_as_mentioned() -> None:
    """#490: with the numbers on the Tile, the spoken line names the place and "UEX";
    the player still gets the price, on the card."""
    e = _eval()
    rule = {"expect": ["reply"], "mentions": [["UEX"], [r"\d"]]}
    said = "UEX has the best gold route from Rod's to the city. The rest is on the Tile."
    assert e.grade(_outcome(say=said), rule, set()) == "missing \\d"
    carded = _outcome(say=said, card="route", card_rows=["Rod's | Buy | 3,450"])
    assert e.grade(carded, rule, set()) == "pass"


def test_a_key_for_case_fails_a_reply_that_names_no_key() -> None:
    """#357: calling player_keys and still saying "the primary fire button" passed."""
    e = _eval()
    rule = {"expect": ["reply"], "key_for": ["v_attack1_group1"]}
    assert e.grade(_outcome(named_key=False), rule, set()) == "didn't name the player's key"
    assert e.grade(_outcome(named_key=True), rule, set()) == "pass"
