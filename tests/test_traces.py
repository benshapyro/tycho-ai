"""Brain traces (#282): one JSON line per brain request, for Claude sessions to read."""

# pyright: reportPrivateUsage=false

import asyncio
import json
import re
from pathlib import Path
from typing import Any, cast

import pytest

from tests import test_brain as claude_fakes
from tests import test_openrouter as openrouter_fakes
from tests.fakes.fake_chat import FakeChat, tool_call
from tests.fakes.fake_lookup import FakeLookup
from tools.bench import traces as reader
from tycho import logs, paths
from tycho.brain import BrainError, BrainRequest, Reply, RunActions
from tycho.brain.claude import ClaudeBrain
from tycho.brain.traces import ENV, FILE, TraceFile

KEYBINDS = claude_fakes.KEYBINDS


def _lines(traces: TraceFile) -> list[dict[str, Any]]:
    traces.close()
    if not traces.path.exists():
        return []
    return [json.loads(line) for line in traces.path.read_text("utf-8").splitlines()]


def _claude(client: object, traces: TraceFile | None, *lookups: FakeLookup) -> ClaudeBrain:
    return ClaudeBrain(
        claude_fakes.MATCHER, client=cast(Any, client), lookups=lookups, traces=traces
    )


def test_a_request_with_a_look_up_writes_one_line_saying_what_happened(tmp_path: Path) -> None:
    traces = TraceFile(tmp_path / FILE, run="test run")
    client = claude_fakes._Calls(
        claude_fakes._looks_up(), ([], claude_fakes._final("RSI makes it."))
    )
    brain = _claude(client, traces, FakeLookup())
    decided = asyncio.run(brain.decide(BrainRequest("who makes the Andromeda", KEYBINDS)))
    assert decided == Reply("RSI makes it.")

    (line,) = _lines(traces)
    assert line["provider"] == "anthropic"
    assert line["model"] == "claude-haiku-5-5"
    assert re.fullmatch(r"[0-9a-f]{12}", line["briefing"])  # a hash, never the briefing
    assert line["text"] == "who makes the Andromeda"
    assert line["run"] == "test run"
    assert line["source"] == "play"
    assert line["version"]
    assert line["outcome"] == "reply"
    assert line["decided"] == {"say": "RSI makes it."}
    assert line["stopped"] == "answered (end_turn)"
    first, second = line["calls"]
    assert [t["name"] for t in first["tools"]] == ["ship_lookup"]
    assert json.loads(first["tools"][0]["arguments"]) == {"name": "Andromeda"}
    assert first["stop"] == "tool_use"
    assert second["tokens"] == {"input": 20, "output": 8, "cache_read": 3000, "cache_write": 0}
    assert second["cost"] > 0
    assert second["first_ms"] is None  # this call's stream had no events before its end
    (look,) = line["lookups"]
    assert look["name"] == "ship_lookup"
    assert look["found"]
    assert not look["error"]
    assert look["chars"] == len('{"name": "Constellation Andromeda", "maker": "RSI"}')
    assert line["ms"] >= look["ms"] >= look["at_ms"]


def test_an_act_ends_the_line_at_the_act(tmp_path: Path) -> None:
    traces = TraceFile(tmp_path / FILE)
    gear = {"actions": ["v_toggle_landing_system"], "say": "Gear."}
    client = claude_fakes._Client([claude_fakes._tool_stop("run_actions", gear)])
    decided = asyncio.run(_claude(client, traces).decide(BrainRequest("gear", KEYBINDS)))
    assert decided == RunActions(("v_toggle_landing_system",), "Gear.")
    (line,) = _lines(traces)
    assert line["outcome"] == "actions"
    assert line["decided"] == {"actions": ["v_toggle_landing_system"], "say": "Gear."}
    assert line["stopped"] == "act"
    (call,) = line["calls"]
    assert call["stop"] == "act"  # the stream was left there, so no usage came
    assert call["first_ms"] is not None
    assert call["tools"][0]["name"] == "run_actions"


def test_a_failed_request_is_traced_too(tmp_path: Path) -> None:
    traces = TraceFile(tmp_path / FILE)
    client = claude_fakes._Client([], claude_fakes._final("", stop_reason="end_turn"))
    with pytest.raises(BrainError):
        asyncio.run(_claude(client, traces).decide(BrainRequest("hmm", KEYBINDS)))
    (line,) = _lines(traces)
    assert line["outcome"] == "error"
    assert line["stopped"].startswith("BrainError: no answer")


def test_the_openrouter_brain_writes_the_same_lines(tmp_path: Path) -> None:
    traces = TraceFile(tmp_path / FILE)
    served = [
        {**chunk, "provider": "Cerebras"}
        for chunk in tool_call("run_actions", openrouter_fakes.GEAR)
    ]
    brain = openrouter_fakes._brain(FakeChat(served), traces=traces)
    asyncio.run(brain.decide(BrainRequest("gear down", openrouter_fakes.KEYBINDS)))
    (line,) = _lines(traces)
    assert line["provider"] == "openrouter"
    assert line["model"] == openrouter_fakes.MODEL
    assert line["outcome"] == "actions"
    assert line["calls"][0]["host"] == "Cerebras"


def test_no_key_or_absolute_path_reaches_a_line(tmp_path: Path) -> None:
    key = "sk-ant-api03-" + "x" * 40
    logs.register_secret(key)
    traces = TraceFile(tmp_path / FILE)
    gear = {"actions": ["v_toggle_landing_system"], "say": f"Gear. {paths.logs_dir()}"}
    client = claude_fakes._Client([claude_fakes._tool_stop("run_actions", gear)])
    said = f"my key is {key}, my fleet is in {Path.home() / 'notes.txt'} and {paths.config_dir()}"
    asyncio.run(_claude(client, traces).decide(BrainRequest(said, KEYBINDS)))
    traces.close()
    text = traces.path.read_text("utf-8")
    assert key not in text
    for place in (Path.home(), paths.config_dir(), paths.data_dir()):
        assert str(place) not in text
        assert json.dumps(str(place))[1:-1] not in text
    line = json.loads(text)
    assert "~/notes.txt" in line["text"].replace("\\", "/")
    assert "<config>" in line["text"]
    assert "<data>" in line["decided"]["say"]


def test_tracing_is_off_unless_asked_for(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(ENV, raising=False)
    assert TraceFile.from_env() is None
    for off in ("", "0", "no"):
        monkeypatch.setenv(ENV, off)
        assert TraceFile.from_env() is None
    monkeypatch.setenv(ENV, "1")
    traces = TraceFile.from_env()
    assert traces is not None
    assert traces.path == paths.logs_dir() / FILE


def test_without_traces_nothing_is_written() -> None:
    gear = {"actions": ["v_toggle_landing_system"], "say": "Gear."}
    client = claude_fakes._Client([claude_fakes._tool_stop("run_actions", gear)])
    asyncio.run(_claude(client, None).decide(BrainRequest("gear", KEYBINDS)))
    assert not (paths.logs_dir() / FILE).exists()


def test_the_app_traces_only_when_tycho_trace_is_on(monkeypatch: pytest.MonkeyPatch) -> None:
    from tycho import brain_choice

    monkeypatch.delenv(ENV, raising=False)
    assert brain_choice._traces() is None  # pyright: ignore[reportPrivateUsage]
    monkeypatch.setenv(ENV, "1")
    traces = brain_choice._traces()  # pyright: ignore[reportPrivateUsage]
    assert traces is not None
    assert traces.source == "play"


# --- Reading them: tools/bench/traces.py ------------------------------------------------


def _line(
    case: str, run: str, outcome: str = "reply", ms: float = 900, **more: Any
) -> dict[str, Any]:
    call: dict[str, Any] = {
        "at_ms": 0,
        "first_ms": 300,
        "tools": [],
        "stop": "end_turn",
        "cost": 0.0002,
    }
    return {
        "run": run,
        "case": case,
        "text": case,
        "provider": "anthropic",
        "model": "claude-haiku-5-5",
        "outcome": outcome,
        "ms": ms,
        "calls": [call],
        "lookups": [],
        "decided": {},
        **more,
    }


def test_the_reader_summarises_the_last_run_and_lists_its_wrong_presses(tmp_path: Path) -> None:
    file = tmp_path / "traces.jsonl"
    looked = {"name": "trade_routes", "took_ms": 400, "error": True}
    lines = [
        _line("old case", "run 1"),
        _line("gear down", "run 2", "actions", 700, decided={"actions": ["v_gear"]}),
        _line("best route", "run 2", "reply", 2400, lookups=[looked]),
        _line("eject", "run 2", "error", 100, stopped="BrainError: no answer"),
    ]
    file.write_text("\n".join(json.dumps(x) for x in lines) + "\n{not whole", "utf-8")
    results = tmp_path / "results.jsonl"
    results.write_text(
        "\n".join(
            json.dumps(x)
            for x in (
                {"type": "meta", "traces": "run 1"},
                {"heard": "old case", "verdict": "WRONG PRESS ['v_eject']"},
                {"type": "meta", "traces": "run 2"},
                {"heard": "gear down", "verdict": "pass"},
                {"heard": "eject", "verdict": "WRONG PRESS (not confirmed first) ['v_eject']"},
            )
        ),
        "utf-8",
    )
    loaded = reader.load(file)
    assert reader.runs(loaded) == [("run 1", 1), ("run 2", 3)]
    run, chosen = reader.pick(loaded)
    assert run == "run 2"
    assert len(chosen) == 3
    text = "\n".join(reader.summary(chosen, run, wrong=reader.wrong_presses(results, run)))
    assert "3 brain requests" in text
    assert "actions 1" in text
    assert "error 1" in text
    assert "trade_routes 1x" in text
    assert "came back as errors: 1" in text
    assert "Error: 'eject': BrainError: no answer" in text
    assert "Wrong presses in the eval: 1" in text
    assert "'old case'" not in text  # another run's
    slowest = text[text.index("Slowest:") :]
    assert slowest.index("2400 ms") < slowest.index("700 ms")  # slowest first


def test_the_reader_diffs_two_runs_case_by_case() -> None:
    old = [_line("gear down", "a", "actions", decided={"actions": ["v_gear"]}), _line("hi", "a")]
    new = [_line("gear down", "b", "reply"), _line("hi", "b"), _line("new", "b")]
    text = "\n".join(reader.diff(old, new, "a", "b"))
    assert "'gear down'" in text
    assert "was actions ['v_gear']" in text
    assert "now reply" in text
    assert "'hi'" not in text  # unchanged
    assert "Changed: 1 of 2" in text
    assert "Only in b: 1" in text


def test_a_run_can_be_named_by_its_start() -> None:
    lines = [_line("x", "2026-11-12T01:00:00 haiku"), _line("y", "2026-11-12T02:00:00 haiku")]
    assert reader.pick(lines, "2026-11-12T01")[0] == "2026-11-12T01:00:00 haiku"
    with pytest.raises(SystemExit):
        reader.pick(lines, "2026-11-12")  # two runs start so
