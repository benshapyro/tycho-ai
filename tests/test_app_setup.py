"""The app's setup (ADR 17): the first run and its choices, with stand-ins for the PC."""

from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from tycho import settings
from tycho.desktop import setup
from tycho.desktop.bridge import Bridge, SetupTools
from tycho.settings import Settings

FOUND = setup.Game(True, "LIVE", r"C:\Games\StarCitizen\LIVE")
MISSING = setup.Game(False, "LIVE", problem="Star Citizen LIVE wasn't found")


@pytest.fixture
def path(tmp_path: Path) -> Path:
    p = tmp_path / "settings.toml"
    settings.save(Settings(), p)
    return p


def _tools(**changes: Any) -> SetupTools:
    tools = SetupTools(
        game=lambda current: (
            FOUND if current.game_root or current.game_channel == "PTU" else MISSING
        ),
        root=lambda folder, _channel: folder.rstrip("\\") if "Star" in folder else None,
        capture=lambda _seconds: "mouse:0x06",
        name=lambda _setting: "Mouse side (forward)",
        conflicts=lambda _setting, _current: [],
        microphones=lambda: ["Headset", "Desk mic"],
        level=lambda _mic: {"ok": True, "name": "Headset", "peak": 0.3, "verdict": "good"},
    )
    return replace(tools, **changes)


def _bridge(path: Path, **options: Any) -> Bridge:
    options.setdefault("tools", _tools())
    return Bridge(settings_path=path, **options)


def test_a_fresh_install_needs_setup() -> None:
    assert setup.needs_setup(Settings(), game=lambda _c: FOUND)  # no push-to-talk yet
    assert setup.needs_setup(Settings(push_to_talk="mouse:0x06"), game=lambda _c: MISSING)
    assert not setup.needs_setup(Settings(push_to_talk="mouse:0x06"), game=lambda _c: FOUND)


def test_setup_shows_what_is_chosen_and_what_is_missing(path: Path) -> None:
    state = _bridge(path).setup_state()
    assert state["game"] == {
        "found": False,
        "channel": "LIVE",
        "folder": "",
        "problem": "Star Citizen LIVE wasn't found",
    }
    assert "PTU" in state["channels"]  # type: ignore[operator]
    assert state["push_to_talk"] == {"set": False, "name": "", "conflicts": []}
    assert state["microphone"] == {"setting": "", "devices": ["Headset", "Desk mic"]}
    assert state["ready"] is False


def test_choosing_the_game_folder(path: Path) -> None:
    bridge = _bridge(path, choose_folder=lambda: "D:\\Games\\StarCitizen\\")
    result = bridge.choose_game_folder()
    assert result["ok"] is True
    assert result["game"] == {
        "found": True,
        "channel": "LIVE",
        "folder": FOUND.folder,
        "problem": "",
    }
    assert str(settings.load(path).game_root) == "D:\\Games\\StarCitizen"


def test_a_folder_without_the_game_is_refused(path: Path) -> None:
    bridge = _bridge(path, choose_folder=lambda: "D:\\Photos")
    result = bridge.choose_game_folder()
    assert result == {"ok": False, "problem": "There's no LIVE\\Data.p4k in or under D:\\Photos."}
    assert settings.load(path).game_root is None


def test_cancelling_the_folder_dialog_changes_nothing(path: Path) -> None:
    assert _bridge(path).choose_game_folder() == {"ok": False, "cancelled": True}


def test_choosing_the_channel(path: Path) -> None:
    bridge = _bridge(path)
    result = bridge.set_channel("PTU")
    assert result["ok"] is True
    assert result["game"]["found"] is True  # type: ignore[index]
    assert bridge.set_channel("BETA")["ok"] is False


def test_choosing_push_to_talk_saves_the_button_and_warns_of_a_clash(path: Path) -> None:
    def clash(_setting: str, _current: Settings) -> list[str]:
        return ["Mouse side (forward) is also bound in game (Fire missiles)."]

    tools = _tools(conflicts=clash)
    result = _bridge(path, tools=tools).capture_push_to_talk()
    assert result["ok"] is True
    assert result["name"] == "Mouse side (forward)"
    assert result["conflicts"] == ["Mouse side (forward) is also bound in game (Fire missiles)."]
    assert settings.load(path).push_to_talk == "mouse:0x06"


def test_no_button_pressed_changes_nothing(path: Path) -> None:
    def nothing(_seconds: float) -> str | None:
        return None

    result = _bridge(path, tools=_tools(capture=nothing)).capture_push_to_talk()
    assert result == {"ok": False, "problem": "No button was pressed in 15 seconds."}
    assert settings.load(path).push_to_talk is None


def test_choosing_a_microphone_from_the_list_or_the_default(path: Path) -> None:
    bridge = _bridge(path)
    assert bridge.set_microphone("Desk mic")["ok"] is True
    assert settings.load(path).microphone == "Desk mic"
    assert bridge.set_microphone("")["ok"] is True
    assert settings.load(path).microphone is None
    assert bridge.set_microphone("Not plugged in") == {
        "ok": False,
        "problem": "That microphone isn't connected.",
    }
    assert bridge.set_microphone(7)["ok"] is False


def test_testing_the_microphone_listens_to_the_chosen_one(path: Path) -> None:
    heard: list[str | None] = []

    def level(mic: str | None) -> dict[str, object]:
        heard.append(mic)
        return {"ok": True, "name": "Desk mic", "peak": 0.001, "verdict": "silent"}

    bridge = _bridge(path, tools=_tools(level=level))
    bridge.set_microphone("Desk mic")
    assert bridge.microphone_level()["verdict"] == "silent"
    assert heard == ["Desk mic"]


def test_setup_can_finish_only_with_a_game_and_a_button(path: Path) -> None:
    finished: list[str] = []
    bridge = _bridge(path, finish=lambda: finished.append("start"), first_run=True)
    assert bridge.finish_setup() == {
        "ok": False,
        "problem": "Tycho still needs the game and a push-to-talk button.",
    }
    bridge.set_channel("PTU")  # found, in the stand-in
    bridge.capture_push_to_talk()
    assert bridge.finish_setup() == {"ok": True, "first_run": True}
    assert finished == ["start"]


def test_on_a_first_run_choices_apply_at_start_and_later_at_restart(path: Path) -> None:
    assert _bridge(path, first_run=True).capture_push_to_talk()["applies"] == "start"
    assert _bridge(path).capture_push_to_talk()["applies"] == "restart"


def test_the_page_learns_whether_this_is_a_first_run(path: Path) -> None:
    assert _bridge(path, first_run=True).about()["first_run"] is True
    assert _bridge(path).about()["first_run"] is False


def test_a_setup_choice_can_be_undone(path: Path) -> None:
    bridge = _bridge(path)
    bridge.set_microphone("Desk mic")
    assert bridge.undo()["name"] == "microphone"
    assert settings.load(path).microphone is None


def test_microphones_are_listed_once_without_windows_stand_ins(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tycho.audio import microphone

    devices = [
        {"name": "Microsoft Sound Mapper - Input", "max_input_channels": 2},
        {"name": "Headset Microphone (2- Arctis P", "max_input_channels": 1},
        {"name": "Speakers", "max_input_channels": 0},
        {"name": "Desk mic", "max_input_channels": 1},
        {"name": "Headset Microphone (2- Arctis Pro Wireless)", "max_input_channels": 1},  # WASAPI
    ]

    class Default:
        device = (3, 2)
        hostapi = 0

    monkeypatch.setattr(microphone.sd, "query_devices", lambda: devices)

    def hostapi(_index: int) -> dict[str, list[int]]:
        return {"devices": [0, 1, 2, 3]}

    monkeypatch.setattr(microphone.sd, "query_hostapis", hostapi)
    monkeypatch.setattr(microphone.sd, "default", Default())
    assert microphone.input_devices() == ["Desk mic", "Headset Microphone (2- Arctis P"]
