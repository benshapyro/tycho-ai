import json
import logging
from pathlib import Path

import numpy as np
import pytest

from tycho import app, logs
from tycho.game.keybinds import Action, Keybinds, KeyInput
from tycho.input import code_for
from tycho.ptt.binding import Binding, DeviceKind
from tycho.ptt.conflicts import conflicts, game_code

# --- Logging never leaks a key -------------------------------------------------------

FAKE_KEY = "sk-ant-api03-" + "x" * 40


@pytest.mark.parametrize(
    "line",
    [
        f"calling Anthropic with {FAKE_KEY}",
        "headers={'x-api-key': 'abcdef1234567890'}",
        "Authorization: Bearer abcdefghijklmnopqrstu",
        "elevenlabs api_key=sk_0123456789abcdef0123456789abcdef",
        "csk-abcdefghijklmnopqrstuvwxyz012345 rejected",
    ],
)
def test_keys_are_redacted(line: str) -> None:
    out = logs.redact(line)
    assert "[redacted]" in out
    for secret in ("x" * 40, "abcdef1234567890", "abcdefghijklmnopqrstu", "0123456789abcdef0123"):
        assert secret not in out


def test_a_registered_key_is_redacted_whatever_its_shape() -> None:
    logs.register_secret("plainlookingvalue42")
    assert logs.redact("the key plainlookingvalue42 failed") == "the key [redacted] failed"


def test_ordinary_lines_are_untouched() -> None:
    line = "#3 landing_gear done in 312 ms (transcribe 240, correct 4, match 0, gate 0, press 62)"
    assert logs.redact(line) == line


def test_the_log_file_is_json_lines_with_tracebacks_redacted(tmp_path: Path) -> None:
    path = logs.setup(folder=tmp_path)
    log = logging.getLogger("tycho.test")
    try:
        raise RuntimeError(f"bad key {FAKE_KEY}")
    except RuntimeError:
        log.exception("request failed")
    log.info("Keybinds: %d actions", 6012)
    for handler in logging.getLogger("tycho").handlers:
        handler.flush()
        handler.close()
    text = path.read_text("utf-8")
    assert "x" * 40 not in text
    entries = [json.loads(line) for line in text.splitlines()]
    assert entries[0]["level"] == "ERROR"
    assert "RuntimeError" in entries[0]["error"]
    assert entries[1]["message"] == "Keybinds: 6012 actions"
    logging.getLogger("tycho").handlers.clear()


# --- Push-to-talk conflicts --------------------------------------------------------------


def _action(name: str, *keys: str) -> Action:
    inputs = tuple(k for k in (KeyInput.parse(t) for t in keys) if k is not None)
    return Action(name=name, actionmap="m", label=name, inputs=inputs)


KEYBINDS = Keybinds(
    [
        _action("v_toggle_landing_system", "n"),
        _action("v_ping", "mouse5"),
        _action("v_lock_target", "lctrl+mouse5"),  # needs Ctrl too, so no conflict
        _action("v_unbound"),
    ]
)


def test_a_mouse_button_bound_in_game_is_a_conflict() -> None:
    code = game_code(Binding(DeviceKind.MOUSE, 0x06), lambda vk: (0, False))
    assert code is not None
    assert code == code_for("mouse5")
    assert [a.name for a in conflicts(code, KEYBINDS)] == ["v_ping"]


def test_a_keyboard_key_is_compared_by_scan_code() -> None:
    n = code_for("n")
    code = game_code(Binding(DeviceKind.KEYBOARD, 0x4E), lambda vk: (n.value, False))
    assert code is not None
    assert [a.name for a in conflicts(code, KEYBINDS)] == ["v_toggle_landing_system"]


def test_a_free_button_has_no_conflict() -> None:
    code = game_code(Binding(DeviceKind.MOUSE, 0x05), lambda vk: (0, False))
    assert code is not None
    assert conflicts(code, KEYBINDS) == []


def test_joystick_push_to_talk_is_not_compared() -> None:
    assert game_code(Binding(DeviceKind.JOYSTICK, 3, "guid"), lambda vk: (0, False)) is None


# --- The command line ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("peak", "level", "words"),
    [
        (0.0, "fail", "muted"),
        (0.03, "warn", "quiet"),
        (0.4, "ok", "level good"),
        (1.0, "warn", "clipping"),
    ],
)
def test_microphone_level(peak: float, level: str, words: str) -> None:
    clip = np.zeros(1000, dtype=np.float32)
    clip[10] = peak
    finding = app.microphone_level("Headset", clip)
    assert finding.level == level
    assert words in finding.detail


@pytest.mark.parametrize(
    ("setting", "device"), [(None, None), ("", None), ("3", 3), ("Arctis", "Arctis")]
)
def test_microphone_setting(setting: str | None, device: int | str | None) -> None:
    assert app.microphone_device(setting) == device


def test_version(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit):
        app.main(["--version"])
    assert "Tycho" in capsys.readouterr().out


def test_a_broken_settings_file_stops_with_its_message(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("TYCHO_CONFIG_DIR", str(tmp_path))
    (tmp_path / "settings.toml").write_text("feedback_volume = 9\n", encoding="utf-8")
    assert app.main(["check", "--quick"]) == 2
    assert "feedback_volume" in capsys.readouterr().err
