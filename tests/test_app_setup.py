"""The app's setup (ADR 17): the first run and its choices, with stand-ins for the PC."""

from pathlib import Path
from typing import Any

import pytest

from tests.fakes.fake_setup_pc import FOUND, MISSING, FakeSetupPC
from tycho import settings
from tycho.desktop import setup
from tycho.desktop.bridge import Bridge
from tycho.settings import Settings


@pytest.fixture
def path(tmp_path: Path) -> Path:
    p = tmp_path / "settings.toml"
    settings.save(Settings(), p)
    return p


def _bridge(path: Path, pc: FakeSetupPC | None = None, **options: Any) -> Bridge:
    return Bridge(settings_path=path, pc=pc or FakeSetupPC(), **options)


def test_a_fresh_install_needs_setup() -> None:
    pc = FakeSetupPC()
    assert setup.needs_setup(Settings(game_channel="PTU"), pc)  # no push-to-talk yet
    assert setup.needs_setup(Settings(push_to_talk="mouse:0x06"), pc)  # no game
    assert not setup.needs_setup(Settings(push_to_talk="mouse:0x06", game_channel="PTU"), pc)
    assert MISSING.found is False


def test_a_game_that_reads_nothing_from_its_folder_needs_none(path: Path) -> None:
    # World of Warcraft (#300): an advisor pack with no install part. A PC without
    # Star Citizen must still get through setup and start (the review of #335).
    pc = FakeSetupPC()  # no game found
    wow = Settings(game="world-of-warcraft", push_to_talk="mouse:0x06")
    assert not setup.needs_setup(wow, pc)
    assert setup.needs_setup(Settings(push_to_talk="mouse:0x06"), pc)  # Star Citizen still is
    settings.save(wow, path)
    bridge = _bridge(path, pc)
    state = bridge.setup_state()
    assert state["game"]["needed"] is False  # type: ignore[index]
    assert state["game"]["name"] == "World of Warcraft"  # type: ignore[index]
    assert state["ready"] is True
    assert bridge.finish_setup() == {"ok": True, "first_run": False}


def test_setup_shows_what_is_chosen_and_what_is_missing(path: Path) -> None:
    state = _bridge(path).setup_state()
    assert state["game"] == {
        "found": False,
        "channel": "LIVE",
        "folder": "",
        "problem": "Star Citizen LIVE wasn't found",
        "needed": True,
        "name": "Star Citizen",
    }
    assert "PTU" in state["channels"]  # type: ignore[operator]
    assert state["push_to_talk"] == {"set": False, "name": "", "conflicts": []}
    assert state["microphone"] == {"setting": "", "devices": ["Headset, MME", "Desk mic, MME"]}
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
        "needed": True,
        "name": "Star Citizen",
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
    pc = FakeSetupPC()
    pc.clashes = ["Mouse side (forward) is also bound in game (Fire missiles)."]
    result = _bridge(path, pc).capture_push_to_talk()
    assert result["ok"] is True
    assert result["name"] == "Mouse side (forward)"
    assert result["conflicts"] == ["Mouse side (forward) is also bound in game (Fire missiles)."]
    assert settings.load(path).push_to_talk == "mouse:0x06"


def test_no_button_pressed_changes_nothing(path: Path) -> None:
    pc = FakeSetupPC()
    pc.button = None
    result = _bridge(path, pc).capture_push_to_talk()
    assert result == {"ok": False, "problem": "No button was pressed in 15 seconds."}
    assert settings.load(path).push_to_talk is None


def test_choosing_a_microphone_from_the_list_or_the_default(path: Path) -> None:
    bridge = _bridge(path)
    assert bridge.set_microphone("Desk mic, MME")["ok"] is True
    assert settings.load(path).microphone == "Desk mic, MME"
    assert bridge.set_microphone("")["ok"] is True
    assert settings.load(path).microphone is None
    assert bridge.set_microphone("Not plugged in") == {
        "ok": False,
        "problem": "That microphone isn't connected.",
    }
    assert bridge.set_microphone(7)["ok"] is False


def test_testing_the_microphone_listens_to_the_chosen_one(path: Path) -> None:
    pc = FakeSetupPC()
    pc.heard = {"ok": True, "name": "Desk mic", "peak": 0.001, "verdict": "silent"}
    bridge = _bridge(path, pc)
    bridge.set_microphone("Desk mic, MME")
    assert bridge.microphone_level()["verdict"] == "silent"
    assert pc.listened == ["Desk mic, MME"]


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
    bridge.set_microphone("Desk mic, MME")
    undone = bridge.undo()
    assert undone["name"] == "microphone"
    assert undone["label"] == "Microphone"  # the setup view's toast says this (#131)
    assert settings.load(path).microphone is None


def test_a_capture_that_fails_says_why_and_saves_nothing(path: Path) -> None:
    pc = FakeSetupPC()
    pc.capture_fails = RuntimeError("SDL couldn't start")
    result = _bridge(path, pc).capture_push_to_talk()
    assert result["ok"] is False
    assert "SDL couldn't start" in str(result["problem"])  # #130
    assert settings.load(path).push_to_talk == Settings().push_to_talk


def test_the_keybinds_are_read_once_a_while_and_none_without_the_game(
    path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import tycho.app
    from tycho.game.store import KeybindStore

    loads: list[object] = []
    game: list[object] = [object()]

    def find_install(current: Settings) -> object:
        if not game:
            raise FileNotFoundError("no game")
        return game[0]

    class Store:
        keybinds = "the keybinds"

    def load(install: object) -> Store:
        loads.append(install)
        return Store()

    monkeypatch.setattr(tycho.app, "find_install", find_install)
    monkeypatch.setattr(KeybindStore, "load", load)
    monkeypatch.setattr("tycho.desktop.bridge.time.monotonic", lambda: 100.0)  # no 30 s edge
    bridge = _bridge(path)
    assert bridge._keybinds() == "the keybinds"  # pyright: ignore[reportPrivateUsage]
    assert bridge._keybinds() == "the keybinds"  # pyright: ignore[reportPrivateUsage]
    assert len(loads) == 1  # #137: kept, not read again for each redraw
    game.clear()
    settings.update({"game_channel": "PTU"}, path)  # another game: read again
    assert bridge._keybinds() is None  # pyright: ignore[reportPrivateUsage]


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

    def hostapi(_index: int) -> dict[str, object]:
        return {"devices": [0, 1, 2, 3], "name": "MME"}

    monkeypatch.setattr(microphone.sd, "query_hostapis", hostapi)
    monkeypatch.setattr(microphone.sd, "default", Default())
    assert microphone.input_devices() == ["Desk mic, MME", "Headset Microphone (2- Arctis P, MME"]


# --- from the reviews of #129 -----------------------------------------------------------


def test_a_saved_microphone_that_isnt_listed_can_stay(path: Path) -> None:
    settings.save(Settings(microphone="2"), path)  # a device number, from the file
    bridge = _bridge(path)
    assert bridge.set_microphone("2")["ok"] is True  # unchanged: never refused
    assert bridge.set_microphone("USB mic, MME")["ok"] is False


def test_the_slow_look_ups_are_kept_between_redraws(path: Path) -> None:
    pc = FakeSetupPC()
    bridge = _bridge(path, pc)
    bridge.capture_push_to_talk()
    pc.calls.clear()
    for _ in range(3):
        bridge.setup_state()  # Next, Back, a redraw
    assert pc.calls.count("game") == 1  # looked up once, then kept
    assert pc.calls.count("conflicts") == 0  # looked up when the button was saved
    assert pc.calls.count("microphones") <= 1
    bridge.set_channel("PTU")  # a new channel: one new look
    assert pc.calls.count("game") == 2


def test_without_a_window_setup_falls_back_to_the_terminal(monkeypatch: pytest.MonkeyPatch) -> None:
    from tycho import app

    class NoWindow:
        def open(self) -> bool:
            return False

        def close(self) -> None:
            pass

    def no_window(**_options: object) -> NoWindow:
        return NoWindow()

    monkeypatch.setattr(app, "app_window", no_window)
    assert app.first_run() == "no_window"


@pytest.mark.parametrize(
    ("peak", "how"),
    [
        (0.0, "silent"),
        (0.005, "silent"),
        (0.02, "quiet"),
        (0.3, "good"),
        (0.985, "good"),
        (1.0, "clipping"),
    ],
)
def test_setup_and_tycho_check_judge_loudness_alike(peak: float, how: str) -> None:
    import numpy as np

    from tycho import app
    from tycho.audio.clip import loudness

    clip = np.array([0.0, peak, -peak / 2], dtype=np.float32)
    assert loudness(clip)[1] == how
    assert setup.verdict(clip)["verdict"] == how
    check = app.microphone_level("Headset", clip).level
    assert check == {"silent": "fail", "quiet": "warn", "clipping": "warn", "good": "ok"}[how]


class _Window:
    """The first run's window, scripted: it opens, and the player finishes or closes it."""

    def __init__(self, finish: object, finishes: bool) -> None:
        self._finish = finish
        self._finishes = finishes

    def open(self) -> bool:
        if self._finishes:
            self._finish()  # type: ignore[operator]
        return True

    def close(self) -> None:
        pass


@pytest.mark.parametrize(("finishes", "outcome"), [(True, "done"), (False, "closed")])
def test_the_first_run_ends_done_or_closed(
    monkeypatch: pytest.MonkeyPatch, finishes: bool, outcome: str
) -> None:
    from tycho import app

    def window(**options: object) -> _Window:
        return _Window(options["finish"], finishes)

    monkeypatch.setattr(app, "app_window", window)
    monkeypatch.setattr("threading.Timer", _NoTimer)  # no real 0.8 s close
    assert app.first_run() == outcome


class _NoTimer:
    def __init__(self, _seconds: float, _fn: object) -> None:
        pass

    def start(self) -> None:
        pass


def test_the_level_test_and_a_rescan_never_overlap(monkeypatch: pytest.MonkeyPatch) -> None:
    """The real level() and microphones(), with PortAudio's parts replaced by recorders:
    a rescan while the test's stream is open would close it (and PortAudio isn't
    thread-safe)."""
    import importlib
    import threading
    import time as clock

    import numpy as np

    importlib.import_module("tycho.app")  # up front, so the threads race on PortAudio alone
    from tycho.audio import microphone

    open_streams: list[str] = []
    overlapped: list[str] = []
    opened = threading.Event()

    class Stream:
        name = "Headset"

        def __init__(self, device: object = None) -> None:
            pass

        def __enter__(self) -> Stream:
            open_streams.append("level")
            opened.set()
            return self

        def __exit__(self, *_exc: object) -> None:
            open_streams.remove("level")

        def start(self) -> None:
            pass

        def stop(self) -> np.ndarray[tuple[int], np.dtype[np.float32]]:
            return np.zeros(4, dtype=np.float32)

    def rescanning_list(*, rescan: bool = False) -> list[str]:
        opened.wait(0.3)  # give the level test every chance to be listening now
        if open_streams:
            overlapped.append("rescan while the level test listened")
        clock.sleep(0.02)
        return ["Headset, MME"]

    monkeypatch.setattr(microphone, "Microphone", Stream)
    monkeypatch.setattr(microphone, "input_devices", rescanning_list)
    monkeypatch.setattr(setup, "LEVEL_SECONDS", 0.2)
    pc = setup.WindowsPC(rescan=True)
    calls = [lambda: pc.level(None), pc.microphones, pc.microphones, lambda: pc.level(None)]
    threads = [threading.Thread(target=call) for call in calls]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert overlapped == []


def test_leaving_a_capture_stops_it_and_saves_nothing(path: Path) -> None:
    import threading

    pc = FakeSetupPC()
    pc.wait_for_cancel = True
    bridge = _bridge(path, pc)
    answer: dict[str, object] = {}
    waiting = threading.Thread(target=lambda: answer.update(bridge.capture_push_to_talk()))
    waiting.start()
    while pc.cancel is None:
        pass
    assert bridge.cancel_capture() == {"ok": True}  # the player went to another step
    waiting.join(5)
    assert answer == {"ok": False, "cancelled": True}
    assert settings.load(path).push_to_talk is None


def test_starting_tycho_stops_a_capture_still_waiting(path: Path) -> None:
    pc = FakeSetupPC()
    bridge = _bridge(path, pc, first_run=True)
    bridge.set_channel("PTU")
    bridge.capture_push_to_talk()
    assert pc.cancel is not None
    assert not pc.cancel.is_set()
    bridge.finish_setup()
    assert pc.cancel.is_set()


def test_capture_binding_stops_when_cancelled() -> None:
    from tycho.ptt.monitor import capture_binding

    calls = [0]

    def cancelled() -> bool:
        calls[0] += 1
        return calls[0] > 3

    class Nothing:
        def pressed(self) -> frozenset[object]:
            return frozenset()

        def is_pressed(self, _binding: object) -> bool:
            return False

    found = capture_binding([Nothing()], cancelled=cancelled, sleep=lambda _s: None)  # type: ignore[list-item]
    assert found is None
    assert calls[0] == 4
