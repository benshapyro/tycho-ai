"""Turning the game down while Tycho speaks (#570, ``voice/ducking.py``)."""

import asyncio
import contextlib
import json
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest

from tests.fakes.fake_speaker import FakeSpeaker
from tests.fakes.fake_volume import FakeGameVolume
from tycho.voice.ducking import ALL, Ducker, Ducking, Levels


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
    assert noted == [{"levels": {"game": 0.7}}]


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
        def get(self) -> Levels:
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
        assert volume.get() == {}
        assert volume.set({ALL: 0.5}) is False
    except OSError:
        pass  # a CI runner with no audio service


def test_a_line_starting_mid_fade_up_still_puts_the_game_back_after(tmp_path: Path) -> None:
    """#577's review: a line that began while the slider was being moved left Tycho's
    note one step behind, read as the player's own change, and the game stayed down."""
    import threading

    volume = FakeGameVolume(1.0)
    slow = threading.Event()
    slow.set()

    class Slow(FakeGameVolume):
        """Windows finishes a move before it answers a read, as one mixer would."""

        moving = 0
        done = threading.Condition()

        def get(self) -> Levels:
            with self.done:
                self.done.wait_for(lambda: self.moving == 0)
            return volume.get()

        def set(self, levels: Mapping[str, float]) -> bool:
            with self.done:
                self.moving += 1
            slow.wait()
            try:
                return volume.set(levels)
            finally:
                with self.done:
                    self.moving -= 1
                    self.done.notify_all()

    ducker = Ducker(Slow(), tmp_path / "ducked.json")  # real waits, as in play
    ducking = Ducking(FakeSpeaker(seconds=0.3), ducker)

    async def go() -> None:
        await ducking.speak("Checking.")
        await asyncio.sleep(0.4)  # the hold, then the fade up has begun
        slow.clear()  # a step is moving the slider as the next line starts
        await asyncio.sleep(0.1)  # the fade's next step is stuck moving it
        line = asyncio.create_task(ducking.speak("Found it."))
        await asyncio.sleep(0.05)
        slow.set()
        await line
        await asyncio.sleep(1.2)

    asyncio.run(go())
    assert volume.level == pytest.approx(1.0)
    assert not (tmp_path / "ducked.json").exists()


def test_a_line_that_ends_before_the_game_went_down_still_puts_it_back(tmp_path: Path) -> None:
    """#577's review: a line that failed at once left the game down until the next."""
    import time

    class SlowToRead(FakeGameVolume):
        def get(self) -> Levels:
            time.sleep(0.05)  # Windows walking every device's sessions
            return super().get()

    volume = SlowToRead(0.8)
    ducker = _ducker(volume, tmp_path)

    class Failing(FakeSpeaker):
        async def speak(self, text: str, *, keep: bool = False) -> None:
            await asyncio.sleep(0)  # the lowering has started reading the level
            raise OSError("can't reach ElevenLabs")

    async def go() -> None:
        with contextlib.suppress(OSError):
            await Ducking(Failing(), ducker).speak("Hi.")
        await _settle()

    asyncio.run(go())
    assert volume.level == pytest.approx(0.8)


def test_each_of_the_games_sliders_goes_back_to_its_own_level(tmp_path: Path) -> None:
    """#578: a game with two sliders in the mixer (one turned down by the player) got
    both put back to the louder one's level."""
    volume = FakeGameVolume(sliders={"speakers": 0.9, "headset": 0.4})
    seen: list[Levels] = []

    class Listening(FakeSpeaker):
        async def speak(self, text: str, *, keep: bool = False) -> None:
            await _settle()
            seen.append(dict(volume.sliders))

    async def go() -> None:
        await Ducking(Listening(), _ducker(volume, tmp_path)).speak("Hi.")
        await _settle()

    asyncio.run(go())
    assert seen == [{"speakers": pytest.approx(0.27), "headset": pytest.approx(0.12)}]
    assert volume.sliders == {"speakers": pytest.approx(0.9), "headset": pytest.approx(0.4)}


def test_a_slider_the_player_moves_stays_and_the_others_go_back(tmp_path: Path) -> None:
    volume = FakeGameVolume(sliders={"speakers": 1.0, "headset": 0.5})

    class Moving(FakeSpeaker):
        async def speak(self, text: str, *, keep: bool = False) -> None:
            await _settle()
            volume.sliders["headset"] = 0.05  # the player, in Windows' mixer

    async def go() -> None:
        await Ducking(Moving(), _ducker(volume, tmp_path)).speak("Hi.")
        await _settle()

    asyncio.run(go())
    assert volume.sliders == {"speakers": pytest.approx(1.0), "headset": 0.05}
    assert not (tmp_path / "ducked.json").exists()


def test_a_crash_is_put_right_slider_by_slider(tmp_path: Path) -> None:
    (tmp_path / "ducked.json").write_text('{"levels": {"a": 0.9, "b": 0.0}}', "utf-8")
    volume = FakeGameVolume(sliders={"a": 0.27, "b": 0.0})
    _ducker(volume, tmp_path).recover()
    assert volume.sliders == {"a": 0.9, "b": 0.0}
    assert not (tmp_path / "ducked.json").exists()


def test_a_mixer_failure_after_it_worked_again_is_logged_too(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """#579: after one failure, a later and different one was never logged."""
    failing: list[str] = ["no audio devices"]

    class Flaky(FakeGameVolume):
        def get(self) -> Levels:
            if failing:
                raise OSError(failing.pop())
            return super().get()

    volume = Flaky(1.0)
    ducking = Ducking(FakeSpeaker(), _ducker(volume, tmp_path))

    async def go() -> None:
        await ducking.speak("One.")
        await _settle()
        await ducking.speak("Two.")  # works
        await _settle()
        failing.append("device unplugged")
        await ducking.speak("Three.")
        await _settle()

    with caplog.at_level("WARNING"):
        asyncio.run(go())
    warned = [r.getMessage() for r in caplog.records if "game's volume" in r.getMessage()]
    assert any("no audio devices" in w for w in warned)
    assert any("device unplugged" in w for w in warned)


def test_quitting_stops_every_fade(tmp_path: Path) -> None:
    """#579: quitting cancelled only the latest fade up."""
    volume = FakeGameVolume(1.0)
    ducker = Ducker(volume, tmp_path / "ducked.json")  # real waits, so fades overlap
    ducking = Ducking(FakeSpeaker(seconds=0.05), ducker)

    async def go() -> None:
        for _ in range(3):
            await ducking.speak("Hi.")
        await ducking.close()
        assert all(task.done() for task in ducker._fades)  # pyright: ignore[reportPrivateUsage]

    asyncio.run(go())
    assert volume.level == pytest.approx(1.0)
