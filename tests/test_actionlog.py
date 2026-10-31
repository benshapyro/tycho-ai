import json
from pathlib import Path

from tycho.runtime.actionlog import ACTION_LOG_FILE, FileActionLog


def test_each_entry_is_one_json_line_with_a_time(tmp_path: Path) -> None:
    log = FileActionLog(tmp_path)
    log.record({"request": 1, "commands": ["eject"], "status": "asking"})
    log.record({"request": 2, "commands": ["eject"], "status": "done", "confirmed": True})
    log.close()
    lines = (tmp_path / ACTION_LOG_FILE).read_text("utf-8").splitlines()
    entries = [json.loads(line) for line in lines]
    assert [e["request"] for e in entries] == [1, 2]
    assert entries[1]["confirmed"] is True
    assert all("time" in e for e in entries)


def test_keys_never_reach_the_action_log(tmp_path: Path) -> None:
    log = FileActionLog(tmp_path)
    log.record({"request": 1, "said": "my key is sk-ant-api03-abcdefghijklmnop"})
    log.close()
    text = (tmp_path / ACTION_LOG_FILE).read_text("utf-8")
    assert "sk-ant" not in text
    assert "[redacted]" in text


def test_two_logs_do_not_share_a_file(tmp_path: Path) -> None:
    first, second = FileActionLog(tmp_path / "a"), FileActionLog(tmp_path / "b")
    first.record({"request": 1})
    first.close()
    second.close()
    assert (tmp_path / "a" / ACTION_LOG_FILE).read_text("utf-8").strip()
    assert not (tmp_path / "b" / ACTION_LOG_FILE).read_text("utf-8").strip()
