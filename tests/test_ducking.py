"""Turning the game down while Tycho speaks (#570, ``voice/ducking.py``)."""

import asyncio
import json
import sys
from pathlib import Path
from typing import Any

import pytest

from tests.fakes.fake_speaker import FakeSpeaker
from tests.fakes.fake_volume import FakeGameVolume
from tycho.voice.ducking import Ducker, Ducking


async def _quick(_seconds: float) -> None:
    await asyncio.sleep(0)


def _ducker(volume: FakeGameVolume, tmp_path: Path, **options: object) -> Ducker:
    return Ducker(volume, tmp_path / "ducked.json", sleep=_quick, **options)  # type: ignore[arg-type]


async def _settle() -> None:
    """Long enough for any fade to finish (they run off the event loop, then go on)."""
    await asyncio.sleep(0.15)


def test_the_game_goes_down_while_tycho_speaks_and_back_after(tmp_path: Path) -> None:
    volume = FakeGameVolume(0.8)
    ducker = _ducker(volume, tmp_path)
    seen: list[float] = []

    class Listening(FakeSpeaker):
        async def speak(self, text: str, *, keep: bool = False) -> None:
            await _settle()  # the fade down runs as the line plays
            seen.append(volume.level)

    async def go() -> None:
        await Ducking(Listening(), ducker).speak("Quantum jump complete.")
        await _settle()

    asyncio.run(go())
    assert seen == [pytest.approx(0.8 * 0.3)]
    assert volume.level == pytest.approx(0.8)
    assert not (tmp_path / "ducked.json").exists()


def test_it_fades_rather_than_jumps(tmp_path: Path) -> None:
    volume = FakeGameVolume(1.0)

    async def go() -> None:
        await Ducking(FakeSpeaker(seconds=0.15), _ducker(volume, tmp_path)).speak("Hi.")
        await _settle()

    asyncio.run(go())
    falling = volume.levels[: volume.levels.index(min(volume.levels)) + 1]
    assert len(falling) > 2
    assert falling == sorted(falling, reverse=True)
    assert max(volume.levels) <= 1.0  # never louder than the player left it


def test_the_level_follows_the_setting(tmp_path: Path) -> None:
    volume = FakeGameVolume(1.0)
    ducker = _ducker(volume, tmp_path, level=0.5)
    ducker.set_level(0.1)

    async def go() -> None:
        await Ducking(FakeSpeaker(seconds=0.15), ducker).speak("Hi.")

    asyncio.run(go())
    assert min(volume.levels) == pytest.approx(0.1)


def test_off_leaves_the_game_alone(tmp_path: Path) -> None:
    volume = FakeGameVolume(1.0)

    async def go() -> None:
        await Ducking(FakeSpeaker(), _ducker(volume, tmp_path, on=False)).speak("Hi.")
        await _settle()

    asyncio.run(go())
    assert volume.levels == []


def test_nothing_happens_when_the_game_isnt_running(tmp_path: Path) -> None:
    volume = FakeGameVolume(running=False)

    async def go() -> None:
        await Ducking(FakeSpeaker(), _ducker(volume, tmp_path)).speak("Hi.")
        await _settle()

    asyncio.run(go())
    assert volume.levels == []
    assert not (tmp_path / "ducked.json").exists()


def test_push_to_talk_stopping_tycho_puts_the_game_back(tmp_path: Path) -> None:
    volume = FakeGameVolume(0.6)
    ducking = Ducking(FakeSpeaker(hold=True), _ducker(volume, tmp_path))

    async def go() -> None:
        line = asyncio.create_task(ducking.speak("A long answer."))
        await _settle()
        assert volume.level == pytest.approx(0.18)
        line.cancel()
        await asyncio.gather(line, return_exceptions=True)
        await _settle()

    asyncio.run(go())
    assert volume.level == pytest.approx(0.6)


def test_the_players_own_change_wins(tmp_path: Path) -> None:
    """Moving the game's slider while Tycho speaks: Tycho leaves it where they put it."""
    volume = FakeGameVolume(1.0)

    class Moving(FakeSpeaker):
        async def speak(self, text: str, *, keep: bool = False) -> None:
            await _settle()
            volume.level = 0.5  # the player, in Windows' mixer

    async def go() -> None:
        await Ducking(Moving(), _ducker(volume, tmp_path)).speak("Hi.")
        await _settle()

    asyncio.run(go())
    assert volume.level == 0.5
    assert not (tmp_path / "ducked.json").exists()


def test_lines_close_together_keep_it_down(tmp_path: Path) -> None:
    """The filler, then the answer: the game doesn't bob up in between."""
    volume = FakeGameVolume(1.0)
    ducker = Ducker(volume, tmp_path / "ducked.json")  # real waits: the hold matters here
    during: list[float] = []

    class Noting(FakeSpeaker):
        async def speak(self, text: str, *, keep: bool = False) -> None:
            await asyncio.sleep(0.3)
            during.extend(volume.levels[len(during) :])

    ducking = Ducking(Noting(), ducker)

    async def go() -> None:
        await ducking.speak("Searching.")
        await asyncio.sleep(0.1)  # the answer comes a moment after the filler ends
        await ducking.speak("Found it.")
        await asyncio.sleep(1.0)

    asyncio.run(go())
    lowest = next(i for i, level in enumerate(during) if level < 0.3 + 1e-6)
    assert all(level == pytest.approx(0.3) for level in during[lowest:])
    assert volume.level == pytest.approx(1.0)


def test_the_level_is_noted_before_it_goes_down(tmp_path: Path) -> None:
    volume = FakeGameVolume(0.7)
    noted: list[object] = []

    class Peeking(FakeSpeaker):
        async def speak(self, text: str, *, keep: bool = False) -> None:
            await _settle()
            noted.append(json.loads((tmp_path / "ducked.json").read_text("utf-8")))

    async def go() -> None:
        await Ducking(Peeking(), _ducker(volume, tmp_path)).speak("Hi.")

    asyncio.run(go())
    assert noted == [{"level": 0.7}]


def test_a_crash_mid_line_is_put_right_at_the_next_start(tmp_path: Path) -> None:
    (tmp_path / "ducked.json").write_text('{"level": 0.9}', "utf-8")
    volume = FakeGameVolume(0.27)  # where the crash left it
    _ducker(volume, tmp_path).recover()
    assert volume.level == 0.9
    assert not (tmp_path / "ducked.json").exists()


def test_a_level_left_while_the_game_was_closed_waits_for_the_game(tmp_path: Path) -> None:
    """Windows keeps a closed game's level, so it's put back once the game runs again."""
    (tmp_path / "ducked.json").write_text('{"level": 0.9}', "utf-8")
    volume = FakeGameVolume(0.27, running=False)
    ducker = _ducker(volume, tmp_path)
    ducker.recover()
    assert (tmp_path / "ducked.json").exists()
    volume.running = True

    async def go() -> None:
        await Ducking(FakeSpeaker(seconds=0.15), ducker).speak("Hi.")
        await _settle()

    asyncio.run(go())
    assert volume.level == pytest.approx(0.9)
    assert min(volume.levels) == pytest.approx(0.27)  # lowered from 0.9, not from 0.27


def test_quitting_mid_line_puts_the_game_back(tmp_path: Path) -> None:
    volume = FakeGameVolume(0.5)
    ducking = Ducking(FakeSpeaker(hold=True), _ducker(volume, tmp_path))

    async def go() -> None:
        line = asyncio.create_task(ducking.speak("A long answer."))
        await _settle()
        await ducking.close()
        line.cancel()
        await asyncio.gather(line, return_exceptions=True)

    asyncio.run(go())
    assert volume.level == 0.5
    assert not (tmp_path / "ducked.json").exists()


def test_turning_it_off_mid_line_puts_the_game_back_when_the_line_ends(tmp_path: Path) -> None:
    volume = FakeGameVolume(1.0)
    ducker = _ducker(volume, tmp_path)

    class Switching(FakeSpeaker):
        async def speak(self, text: str, *, keep: bool = False) -> None:
            await _settle()
            ducker.set_on(False)

    async def go() -> None:
        await Ducking(Switching(), ducker).speak("Hi.")
        await _settle()

    asyncio.run(go())
    assert volume.level == pytest.approx(1.0)


def test_a_broken_mixer_never_stops_the_line(tmp_path: Path) -> None:
    class Broken(FakeGameVolume):
        def get(self) -> float | None:
            raise OSError("no audio devices")

    speaker = FakeSpeaker()

    async def go() -> None:
        await Ducking(speaker, _ducker(Broken(), tmp_path)).speak("Hi.")
        await _settle()

    asyncio.run(go())
    assert speaker.spoken == ["Hi."]


def test_a_noted_level_that_makes_no_sense_is_dropped(tmp_path: Path) -> None:
    (tmp_path / "ducked.json").write_text('{"level": 7}', "utf-8")
    volume = FakeGameVolume(0.4)
    _ducker(volume, tmp_path).recover()
    assert volume.levels == []
    assert not (tmp_path / "ducked.json").exists()


def test_turning_the_game_down_shows_only_for_a_game_with_a_program(tmp_path: Path) -> None:
    """In the window's Voice settings for Star Citizen; WoW: Forever has no program to
    turn down, so not there."""
    from tycho import settings
    from tycho.desktop.bridge import Bridge
    from tycho.desktop.bridge.settings_page import GROUPS
    from tycho.settings import Settings

    assert {"duck_game", "duck_level"} <= set(GROUPS["Voice"])
    path = tmp_path / "settings.toml"
    for game, shown in (("star-citizen", True), ("wow-forever", False)):
        settings.save(Settings(game=game), path)  # type: ignore[arg-type]
        groups: Any = Bridge(settings_path=path).settings()["groups"]
        names = {r["name"] for g in groups for r in g["rows"]}
        assert ({"duck_game", "duck_level"} <= names) is shown


def test_on_by_default_at_30_percent_and_changeable_by_voice() -> None:
    from tycho import settings
    from tycho.settings import Settings
    from tycho.spoken_settings import parse

    assert (Settings().duck_game, Settings().duck_level) == (True, 0.3)
    assert settings.rule("duck_game")["voice"] == "yes"
    assert parse("duck_game", "off", True).value is False
    assert parse("duck_level", "20%", 0.3).value == pytest.approx(0.2)


def test_com_ids_are_read_right() -> None:
    from tycho import win32

    made = win32.guid("BCDE0395-E52F-467C-8E3D-C4579291692E")
    assert (made.Data1, made.Data2, made.Data3) == (0xBCDE0395, 0xE52F, 0x467C)
    assert bytes(made.Data4) == bytes.fromhex("8E3DC4579291692E")


@pytest.mark.skipif(sys.platform != "win32", reason="Windows' mixer")
def test_windows_mixer_finds_no_sound_for_a_program_that_isnt_running() -> None:
    """Walks every output device's audio sessions through COM without crashing."""
    from tycho.voice.mixer import WindowsGameVolume

    volume = WindowsGameVolume("no-such-game-570.exe")
    try:
        assert volume.get() is None
        assert volume.set(0.5) is False
    except OSError:
        pass  # a CI runner with no audio service
