"""The Jev re-test's grading (tools/bench/jev_report.py, #24), on recorded answers.
Free: nothing here calls TypeSafe."""

import importlib.util
import json
import random
import sys
import tomllib
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from tycho.game.keybinds import Action

ROOT = Path(__file__).resolve().parent.parent
CASES = ROOT / "src" / "tycho" / "packs" / "star-citizen" / "eval" / "cases.toml"


def _jev() -> ModuleType:
    name = "jev_report_under_test"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, ROOT / "tools" / "bench" / "jev_report.py")
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


def _answers(
    mode: str = "command",
    confidence: float = 0.95,
    category: str = "flight_and_ship",
    action: str = "v_lights",
    action_confidence: float = 0.95,
) -> dict[str, Any]:
    return {
        "mode": {"choice": mode, "confidence": confidence},
        "category": {"choice": category, "confidence": 0.95},
        "multi_action": {"noul": 0.0},
        f"act_{category}": {"choice": action, "confidence": action_confidence},
    }


def _result(case: str, ms: float = 100.0, **answers: Any) -> Any:
    return _jev().Result(case, 0, ms, _answers(**answers))


def test_jev_is_asked_the_case_file_s_modes() -> None:
    """The report compares Jev's mode with each case's label, so the names must match."""
    _jev()
    from brain_report import MODES  # pyright: ignore[reportMissingImports, reportUnknownVariableType]

    assert set(_jev().MODES) == set(MODES)  # pyright: ignore[reportUnknownArgumentType]


def test_the_case_file_has_every_mode_a_router_must_tell_apart() -> None:
    """#24: squadmate chatter, unclear requests, planning and coaching, not only commands."""
    modes = [c["mode"] for c in tomllib.loads(CASES.read_text("utf-8"))["case"]]
    for mode in _jev().MODES:
        assert modes.count(mode) >= 5, mode


def test_other_defers_to_the_brain() -> None:
    """A pick of "other" is no pick: Tycho hands the request to Claude."""
    j = _jev()
    kind, actions = j.decide(_answers(action=j.OTHER), j.Thresholds())
    assert (kind, actions) == ("defer", [])


@pytest.mark.parametrize(
    ("mode", "kind"),
    [("command", "actions"), ("not_for_tycho", "reply"), ("coach", "defer"), ("planning", "defer")],
)
def test_what_tycho_would_do_with_each_mode(mode: str, kind: str) -> None:
    j = _jev()
    assert j.decide(_answers(mode=mode), j.Thresholds())[0] == kind


def test_mode_scores_count_right_wrong_and_confident_wrong() -> None:
    j = _jev()
    modes = {"gear down": "command", "nice shot": "not_for_tycho", "brb": "not_for_tycho"}
    results = [
        _result("gear down", mode="command"),
        _result("nice shot", mode="command", confidence=0.92),  # confidently wrong
        _result("brb", mode="chat", confidence=0.75),  # wrong, less sure
        j.Result("brb", 1, 0.0, error="500 boom"),  # failed requests aren't graded
    ]
    scores = j.mode_scores(results, modes)
    assert (scores["command"].cases, scores["command"].right) == (1, 1)
    chatter = scores["not_for_tycho"]
    assert (chatter.cases, chatter.right) == (2, 0)
    assert chatter.confident_wrong == {0.7: 2, 0.9: 1}
    assert chatter.called == {"command": 1, "chat": 1}
    assert j.false_commands(results, modes, 0.9) == [("nice shot", 0.92)]


def test_answers_logged_before_the_rename_still_grade() -> None:
    j = _jev()
    result = _result("brb", mode="not_for_assistant")
    assert j.mode_scores([result], {"brb": "not_for_tycho"})["not_for_tycho"].right == 1


def test_latency_reports_p50_and_p90() -> None:
    j = _jev()
    results = [_result(f"c{i}", ms=float(ms)) for i, ms in enumerate(range(10, 110, 10))]
    assert j.latency(results) == (55.0, 90.0, 100.0)
    assert j.latency([]) is None


def test_shuffling_is_repeatable_and_keeps_every_option() -> None:
    j = _jev()
    actions = [Action(f"v_a{i}", "spaceship_general", f"Action {i}", ()) for i in range(12)]
    questions, _ = j.build_questions(actions)
    first = j.shuffled(questions, j.request_rng(1, 0, "gear down"))
    again = j.shuffled(questions, j.request_rng(1, 0, "gear down"))
    other = j.shuffled(questions, j.request_rng(2, 0, "gear down"))
    act = "act_flight_and_ship"
    assert list(first[act]["criteria"]) == list(again[act]["criteria"])
    assert list(first[act]["criteria"]) != list(other[act]["criteria"])
    assert first[act]["criteria"] == questions[act]["criteria"]  # same options, same text
    assert first["multi_action"] == questions["multi_action"]


def test_first_option_bias_counts_picks_of_whatever_came_first() -> None:
    j = _jev()
    rows: list[Any] = []
    for i, first in enumerate(["v_lights", "v_lights", "v_doors", "v_gear"]):
        r = _result(f"c{i}")
        r.first = {"mode": "chat", "category": "power", "act_flight_and_ship": first}
        r.options = {"mode": 8, "category": 9, "act_flight_and_ship": 4}
        rows.append(r)
    rows.append(_result("unshuffled"))  # a fixed order says nothing about bias
    bias = j.first_option_bias(rows)
    assert (bias["action"].first, bias["action"].answers) == (2, 4)
    assert bias["action"].chance == pytest.approx(0.25)
    assert (bias["mode"].first, bias["mode"].answers) == (0, 4)


def test_each_action_is_described_with_what_it_is_not_and_how_it_is_said() -> None:
    j = _jev()
    actions = [
        Action("v_power_throttle_max", "spaceship_movement", "Throttle Max", ()),
        Action("v_ifcs_throttle_swap_mode", "spaceship_movement", "Cruise Control (Toggle)", ()),
    ]
    examples = j.phrasings(["v_power_throttle_max | full throttle; max throttle; full speed"])
    questions, home = j.build_questions(actions, examples)
    options = questions["act_flight_and_ship"]["criteria"]
    assert options["v_power_throttle_max"] == (
        'Throttle Max. Not cruise control, and not power to engines. Said like: "full '
        'throttle"; "max throttle"; "full speed".'
    )
    assert options["v_ifcs_throttle_swap_mode"].startswith("Cruise Control (Toggle). Not full")
    assert options[j.OTHER] == j.OTHER_TEXT
    assert j.OTHER not in home


def test_a_results_log_from_before_the_shuffle_replays(tmp_path: Path) -> None:
    j = _jev()
    log = tmp_path / "jev.results.jsonl"
    old = {"case": "brb", "run": 0, "ms": 120.0, "answers": _answers(), "error": ""}
    log.write_text(json.dumps(old) + "\n", "utf-8")
    [result] = j.replay(log)
    assert (result.case, result.seed, result.first) == ("brb", None, {})


def test_the_seeded_order_is_the_same_on_every_pc() -> None:
    """A string seed hashes the same everywhere (no PYTHONHASHSEED), so a run can be repeated."""
    j = _jev()
    assert j.request_rng(1, 0, "gear down").random() == random.Random("1:0:gear down").random()  # noqa: S311


def test_the_report_prints_from_recorded_answers(capsys: pytest.CaptureFixture[str]) -> None:
    _jev()
    from brain_report import Case  # pyright: ignore[reportMissingImports, reportUnknownVariableType]

    j = _jev()
    cases = [
        Case(
            "lights",
            "lights",
            {"mode": "command", "expect": ["actions"], "must": [["v_lights"]]},
            "",
        ),
        Case("nice shot", "nice shot", {"mode": "not_for_tycho", "expect": ["reply"]}, "chatter"),
    ]
    shuffled = _result("lights")
    shuffled.seed, shuffled.first = 3, {"mode": "command"}
    shuffled.options = {"mode": 8}
    results = [shuffled, _result("nice shot", mode="command", action="v_eject")]
    j.report(results, cases, {"v_lights", "v_eject"})
    out = capsys.readouterr().out
    assert "not commands, called a command at 0.9+: 1" in out
    assert "'nice shot'" in out
    assert "latency: p50" in out
    assert "First-option bias" in out
