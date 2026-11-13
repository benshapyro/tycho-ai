"""The web search benchmark's bookkeeping (tools/bench/web_trial.py, #375): what it
counts toward ``--budget``, and how ``--compare`` reads an earlier run."""

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

from tycho.knowledge import web

ROOT = Path(__file__).resolve().parent.parent


def _trial() -> ModuleType:
    name = "web_trial_under_test"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, ROOT / "tools" / "bench" / "web_trial.py")
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


LOGGED = "Web search: 1 searches, 0 pages read, 3 sites, about $0.012, stopped: end_turn"


def test_an_open_web_step_that_logged_no_cost_is_estimated() -> None:
    """#375: a cancelled or failed open-web step cost money but wasn't counted."""
    trial = _trial()
    trying = "Web search: the game's sites said PARTIAL; trying the open web"
    assert trial.unlogged_wider([LOGGED, trying]) == web.OPEN_USES * web.SEARCH_PRICE
    assert trial.unlogged_wider([LOGGED, trying, LOGGED]) == 0.0  # it logged its own
    assert trial.unlogged_wider([LOGGED]) == 0.0  # never went wider


def test_compare_keeps_the_last_answer_of_each_question_and_mode() -> None:
    """#375: a file with several runs merged their grades under one mode."""
    rows = [
        {"round": 1, "question": "q", "mode": "sites", "answer": "old", "grade": "right"},
        {"round": 1, "question": "q", "mode": "sites", "answer": "new"},
        {"round": 1, "question": "q", "mode": "fallback", "answer": "other"},
    ]
    before = _trial().earlier([json.dumps(r) for r in rows])
    answers = {r["mode"]: r["answer"] for r in before[(1, "q")]}
    assert answers == {"sites (before)": "new", "fallback (before)": "other"}
    assert all("grade" not in r for r in before[(1, "q")])
