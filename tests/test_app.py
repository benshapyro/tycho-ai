import json
import logging
from pathlib import Path
from typing import Literal

import numpy as np
import pytest

from tycho import app, logs, settings
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


def test_console_only_lines_never_reach_the_log_file(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Tycho's reply with no voice: the player reads it, but it isn't kept unless
    # they keep transcripts.
    path = logs.setup(folder=tmp_path)
    log = logging.getLogger("tycho.test")
    log.info("Tycho: Try the Prospector.", extra=logs.CONSOLE_ONLY)
    log.info("kept")
    for handler in logging.getLogger("tycho").handlers:
        handler.flush()
        handler.close()
    assert "Try the Prospector" in capsys.readouterr().err
    assert [json.loads(line)["message"] for line in path.read_text("utf-8").splitlines()] == [
        "kept"
    ]
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
    import importlib.metadata

    with pytest.raises(SystemExit):
        app.main(["--version"])
    # The version is written once, in pyproject.toml, and read back from the package.
    assert capsys.readouterr().out.strip() == f"Tycho {importlib.metadata.version('tycho-ai')}"


def test_a_broken_settings_file_stops_with_its_message(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("TYCHO_CONFIG_DIR", str(tmp_path))
    (tmp_path / "settings.toml").write_text("feedback_volume = 9\n", encoding="utf-8")
    assert app.main(["check", "--quick"]) == 2
    assert "feedback_volume" in capsys.readouterr().err


def test_a_broken_commands_file_never_stops_tycho_starting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv("TYCHO_CONFIG_DIR", str(tmp_path))
    (tmp_path / "commands.toml").write_text("[[sequence]\n", "utf-8")
    with caplog.at_level(logging.WARNING, logger="tycho"):
        matcher = app.load_commands()
    assert matcher.get("startup") is not None  # the built-in commands still load
    assert "commands file can't be used" in caplog.text


def test_the_players_commands_file_is_used(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TYCHO_CONFIG_DIR", str(tmp_path))
    (tmp_path / "commands.toml").write_text(
        '[[sequence]]\nid = "wake"\nsay = ["wake up"]\nsteps = ["flight_ready"]\n', "utf-8"
    )
    assert app.load_commands().get("wake") is not None


def test_an_unknown_setting_is_a_note_not_a_stop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # A setting from a newer Tycho sharing the file (or a typo) mustn't stop this one.
    monkeypatch.setenv("TYCHO_CONFIG_DIR", str(tmp_path))
    (tmp_path / "settings.toml").write_text("from_the_future = true\n", encoding="utf-8")

    def gpu(action: str) -> int:
        return 0

    monkeypatch.setattr(app, "gpu_command", gpu)
    assert app.main(["gpu", "status"]) == 0
    assert "from_the_future in settings.toml isn't a setting" in capsys.readouterr().err
    findings = app._checks(settings.load(), quick=True)  # pyright: ignore[reportPrivateUsage]
    first, second = next(findings), next(findings)
    assert first.what == second.what == "Settings"
    assert second.level == "warn"
    assert "from_the_future" in second.detail


def test_a_commands_file_with_a_byte_order_mark_is_used(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # PowerShell 5.1's Set-Content -Encoding utf8 always writes one.
    monkeypatch.setenv("TYCHO_CONFIG_DIR", str(tmp_path))
    text = '[[sequence]]\nid = "wake"\nsay = ["wake up"]\nsteps = ["flight_ready"]\n'
    (tmp_path / "commands.toml").write_text(text, "utf-8-sig")
    assert app.load_commands().get("wake") is not None


def test_check_warns_about_broken_player_files_without_failing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # run starts anyway with either file broken, so check mustn't say it can't.
    monkeypatch.setenv("TYCHO_CONFIG_DIR", str(tmp_path))
    (tmp_path / "commands.toml").write_text("[[sequence]\n", "utf-8")
    (tmp_path / "fleet.toml").write_text("ships = [ not toml", "utf-8")
    seen: dict[str, app.Finding] = {}
    for finding in app._checks(settings.load(), quick=True):  # pyright: ignore[reportPrivateUsage]
        seen[finding.what] = finding
        if finding.what == "Your fleet":
            break
    assert seen["Your commands"].level == "warn"
    assert seen["Your fleet"].level == "warn"
    assert "won't change it" in seen["Your fleet"].detail


@pytest.mark.parametrize("allowed", [True, False])
def test_check_for_updates_false_never_asks_github(
    monkeypatch: pytest.MonkeyPatch, allowed: bool
) -> None:
    # The documented opt-out had no test (audit, 2026-11-03).
    asked: list[str] = []

    def announce(current: str) -> None:
        asked.append("run")

    def finding() -> app.Finding:
        asked.append("check")
        return app.Finding("ok", "Updates", "stub")

    def focus() -> app.Finding:
        return app.Finding("ok", "Focus", "stub")

    monkeypatch.setattr(app.updates, "announce_in_background", announce)
    monkeypatch.setattr(app, "update_finding", finding)
    monkeypatch.setattr(app, "focus_finding", focus)
    current = settings.Settings(check_for_updates=allowed)
    app.start_update_check(current)
    checked = [f.what for f in app._live_checks(current)]  # pyright: ignore[reportPrivateUsage]
    assert asked == (["run", "check"] if allowed else [])
    assert checked == (["Updates", "Focus"] if allowed else ["Focus"])


def test_tycho_credits_names_the_sources_and_says_its_unofficial(
    capsys: pytest.CaptureFixture[str],
) -> None:
    # ADR 15's credits screen, and CIG's fankit disclaimer (audit, 2026-11-03).
    assert app.main(["credits"]) == 0
    out = capsys.readouterr().out
    for expected in ("api.star-citizen.wiki", "UEX Corp", "starcitizen.tools", "CC-BY-4.0"):
        assert expected in out
    assert "not affiliated with the" in out


@pytest.mark.parametrize(
    ("device", "driver", "hint", "level", "says"),
    [
        ("gpu", 617, None, "ok", "graphics card"),
        ("cpu", None, None, "ok", "no NVIDIA card"),  # nothing to do about it
        ("cpu", 617, "Run: tycho gpu install", "warn", "tycho gpu install"),
        ("cpu", 617, None, "warn", "short of memory"),  # a card, but no room on it
    ],
)
def test_check_says_how_to_move_speech_to_the_graphics_card(
    monkeypatch: pytest.MonkeyPatch,
    device: Literal["gpu", "cpu"],
    driver: int | None,
    hint: str | None,
    level: str,
    says: str,
) -> None:
    import tycho.stt
    from tycho.stt import DeviceChoice

    def choice(current: settings.Settings) -> DeviceChoice:
        return DeviceChoice(device, "short of memory")

    monkeypatch.setattr(app, "device_choice", choice)
    monkeypatch.setattr(app, "gpu_hint", lambda: hint)
    monkeypatch.setattr(tycho.stt, "nvidia_driver", lambda: driver)
    finding = app.speech_finding(settings.Settings())
    assert finding.level == level
    assert says in finding.detail


def test_check_json_is_one_document_with_a_fix_for_each_problem(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    # For agents setting Tycho up: no reading of the printed text (agent setup, 2026-11-04).
    monkeypatch.setattr(app, "find_install", _no_game)
    assert app.main(["check", "--quick", "--json"]) == 1  # no button, no game: not ready
    document = json.loads(capsys.readouterr().out)
    assert document["ready"] is False
    by_what = {f["what"]: f for f in document["findings"]}
    assert by_what["Push-to-talk"]["level"] == "fail"
    assert by_what["Push-to-talk"]["fix"].endswith("tycho bind")
    assert by_what["Brain"]["fix"].endswith("tycho key set anthropic")
    assert "game_root" in by_what["Star Citizen"]["fix"]
    assert by_what["Settings"]["fix"] is None


def _no_game(current: settings.Settings) -> object:
    raise FileNotFoundError("Star Citizen isn't installed in any folder Tycho knows")


def test_tycho_model_status_and_download(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    from tycho.stt import ModelStore

    monkeypatch.setattr(app.paths, "models_dir", lambda: Path("nowhere-yet"))
    assert app.main(["model", "status"]) == 1
    assert "tycho model download" in capsys.readouterr().out
    fetched: list[str] = []

    def ensure(self: ModelStore, spec: object, progress: object = None) -> Path:
        fetched.append("ensure")
        return Path("nowhere-yet")

    monkeypatch.setattr(ModelStore, "ensure", ensure)
    assert app.main(["model", "download"]) == 0
    assert fetched == ["ensure"]
    assert "Done." in capsys.readouterr().out
