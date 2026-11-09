import json
import logging
from pathlib import Path
from types import SimpleNamespace
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


def test_tycho_actions_finds_names_by_label_and_by_what_players_say(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    # For writing commands.toml with an agent: the game's names, and Tycho's own phrases.
    scm = Action(
        name="v_master_mode_set_scm",
        actionmap="spaceship_movement",
        label="Set Master Mode to SCM",
        inputs=tuple(k for k in [KeyInput.parse("ralt+v")] if k is not None),
    )
    gear = _action("v_toggle_landing_system")
    store = SimpleNamespace(keybinds=Keybinds([scm, gear]))

    def install(current: settings.Settings) -> object:
        return object()

    def load(found: object) -> SimpleNamespace:
        return store

    monkeypatch.setattr(app, "find_install", install)
    monkeypatch.setattr(app.KeybindStore, "load", load)
    assert app.main(["actions", "master", "mode"]) == 0
    out = capsys.readouterr().out
    assert "v_master_mode_set_scm  |  Set Master Mode to SCM  |  ralt+v" in out
    assert app.main(["actions", "gear", "down"]) == 0  # no label says "gear"
    assert "landing_gear_down: v_deploy_landing_system" in capsys.readouterr().out
    assert app.main(["actions", "zzqq"]) == 1


def test_a_keybind_change_warms_the_brain_again(monkeypatch: pytest.MonkeyPatch) -> None:
    """#60: ``_refresh_keybinds`` never asked for a warm-up, so the first request after a
    rebind was about 3 s cold. Only a change asks."""
    import asyncio

    monkeypatch.setattr(app, "KEYBIND_REFRESH_SECONDS", 0.0)
    changes = iter([False, True, False])
    store = SimpleNamespace(refresh=lambda: next(changes, False), last_error=None)
    warmed: list[bool] = []

    async def scenario() -> None:
        changed = asyncio.Event()

        def warm() -> None:
            warmed.append(True)
            changed.set()

        refresher = asyncio.create_task(
            app._refresh_keybinds(store, warm)  # pyright: ignore[reportPrivateUsage, reportArgumentType]
        )
        await changed.wait()
        await asyncio.sleep(0.01)  # the third read, unchanged, asks for nothing
        refresher.cancel()

    asyncio.run(asyncio.wait_for(scenario(), 5))
    assert warmed == [True]


# --- The voice (#242) ------------------------------------------------------------------


def _voice_check(monkeypatch: pytest.MonkeyPatch, availability: str, reason: str = "") -> list[str]:
    from tycho.voice import voices

    asked: list[str] = []

    def check(key: str, voice: voices.Voice) -> voices.Checked:
        asked.append(voice.id)
        return voices.Checked(availability, reason)  # pyright: ignore[reportArgumentType]

    monkeypatch.setattr(app, "elevenlabs_key", lambda: "sk_test")
    monkeypatch.setattr(voices, "check", check)
    monkeypatch.setattr(app, "_voice_now", None)
    return asked


def test_a_missing_voice_falls_back_to_george_on_flash_before_the_first_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    from tycho.voice import voices
    from tycho.voice.elevenlabs import ElevenLabsSpeaker

    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    asked = _voice_check(monkeypatch, "missing", "The Ship's Mind isn't available to your key")
    told: list[str] = []
    with caplog.at_level(logging.INFO, logger="tycho"):
        speaker = app.load_speaker(
            settings.Settings(voice="The Ship's Mind"), on_fallback=told.append
        )
    assert asked == [voices.SHIPS_MIND]
    assert isinstance(speaker, ElevenLabsSpeaker)
    assert speaker.voice == voices.DEFAULT
    assert "George on Flash v2.5" in caplog.text
    (said,) = told
    assert "George on Flash v2.5" in said
    # The window's status shows what the running Tycho chose, without asking again.
    finding = app.voice_finding(settings.Settings(voice="The Ship's Mind"), quick=True)
    assert finding.level == "warn"
    assert asked == [voices.SHIPS_MIND]


def test_the_start_log_names_the_voice_actually_used(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    from tycho.voice.elevenlabs import ElevenLabsSpeaker

    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    _voice_check(monkeypatch, "available")
    told: list[str] = []
    with caplog.at_level(logging.INFO, logger="tycho"):
        speaker = app.load_speaker(settings.Settings(voice="Sarah"), on_fallback=told.append)
    assert isinstance(speaker, ElevenLabsSpeaker)
    assert speaker.voice.name == "Sarah"
    assert "Voice: ElevenLabs, Sarah on v4 Turbo" in caplog.text
    assert "Ship's Mind" not in caplog.text
    assert told == []


def test_a_voice_that_couldnt_be_checked_is_kept(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A key allowed only text-to-speech can't read voices; the first line still falls back.
    from tycho.voice.elevenlabs import ElevenLabsSpeaker

    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    _voice_check(monkeypatch, "unknown", "your key isn't allowed to read voices")
    speaker = app.load_speaker(settings.Settings(voice="The Ship's Mind"))
    assert isinstance(speaker, ElevenLabsSpeaker)
    assert speaker.voice.name == "The Ship's Mind"


@pytest.mark.parametrize(
    ("availability", "level", "words"),
    [
        ("available", "ok", "ElevenLabs, George on Flash v2.5"),
        ("missing", "warn", "so Tycho speaks as George on Flash v2.5"),
    ],
)
def test_tycho_check_names_the_voice_and_warns_if_its_missing(
    monkeypatch: pytest.MonkeyPatch, availability: str, level: str, words: str
) -> None:
    asked = _voice_check(monkeypatch, availability, "The Ship's Mind isn't available to your key")
    voice = "George" if availability == "available" else "The Ship's Mind"
    finding = app.voice_finding(settings.Settings(voice=voice), quick=False)
    assert (finding.what, finding.level) == ("Voice", level)
    assert words in finding.detail
    assert len(asked) == 1
    # The quick checks (the window's) never ask ElevenLabs.
    quick = app.voice_finding(settings.Settings(voice=voice), quick=True)
    assert quick.level == "ok"
    assert len(asked) == 1


def test_a_stored_voice_key_with_a_hidden_character_is_no_key_not_a_failed_start(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    # httpx2 encodes headers when the client is built: a zero-width space pasted with an
    # older key stopped Tycho starting (#246, the #247 review).
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))

    def stored(name: str) -> str:
        return "sk_" + "a" * 30 + "\u200b"

    monkeypatch.setattr(app.credentials, "get", stored)
    doing: list[str] = []
    with caplog.at_level(logging.WARNING, logger="tycho"):
        assert app.load_speaker(settings.Settings(), doing=doing.append) is None
    assert "invisible" in caplog.text
    assert doing == []  # nothing to check, so the starting star doesn't say so


def test_the_starting_star_says_when_the_voice_is_being_checked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    _voice_check(monkeypatch, "available")
    doing: list[str] = []
    assert app.load_speaker(settings.Settings(), doing=doing.append) is not None
    assert doing == ["Checking your voice"]
    assert app.load_speaker(settings.Settings(voice_volume=0), doing=doing.append) is None
    assert doing == ["Checking your voice"]


def test_a_stored_anthropic_key_with_a_hidden_character_is_no_key(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Stored before `tycho key set` refused such a key, it went on to the SDK, which can't
    # put it in a header (#248, from the #247 review). Now it counts as no key, with a warning.
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))

    def stored(name: str) -> str:
        return "sk-ant-api03-" + "a" * 30 + "\u200b"

    monkeypatch.setattr(app.credentials, "get", stored)
    with caplog.at_level(logging.WARNING, logger="tycho"):
        assert app.anthropic_key() is None
    assert "Anthropic key has a character keys don't have" in caplog.text
    assert "tycho key set anthropic" in caplog.text
    monkeypatch.setattr(app, "find_install", _no_game)
    app.main(["check", "--quick", "--json"])
    brain = {f["what"]: f for f in json.loads(capsys.readouterr().out)["findings"]}["Brain"]
    assert brain["level"] == "warn"


@pytest.mark.parametrize(
    ("name", "key", "what"),
    [
        ("anthropic", "sk-ant-api03-" + "a" * 30 + "\u200b", "Brain"),
        ("elevenlabs", "sk_" + "a" * 30 + "\u200b", "Voice"),
    ],
)
def test_tycho_check_says_a_stored_key_is_unusable_not_missing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    name: str,
    key: str,
    what: str,
) -> None:
    # "no Anthropic key" sent the player looking for a key that was there: the fix is to
    # store it again (#250).
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))

    def stored(asked: str) -> str | None:
        return key if asked == name else None

    monkeypatch.setattr(app.credentials, "get", stored)
    monkeypatch.setattr(app, "find_install", _no_game)
    app.main(["check", "--quick", "--json"])
    finding = {f["what"]: f for f in json.loads(capsys.readouterr().out)["findings"]}[what]
    assert finding["level"] == "warn"
    assert "stored but unusable" in finding["detail"]
    assert "no " + ("Anthropic" if name == "anthropic" else "ElevenLabs") not in finding["detail"]
    assert f"key set {name}" in finding["fix"]
