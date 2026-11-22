"""Speaking up without being asked (ADR 14's amendment of 22 November 2026, #4): each
rule of the amendment, with a fake log and a fake clock."""

import asyncio
import logging
import shutil
from pathlib import Path

import pytest

from tests.fakes.fake_brain import FakeBrain
from tests.fakes.fake_display import FakeDisplay
from tests.fakes.fake_pack import ADVISOR, REGISTRY
from tests.fakes.fake_runtime import FakeGameState
from tests.fakes.fake_speaker import FakeSpeaker
from tests.test_runtime import SPEECH, rig, run
from tycho import callouts, settings
from tycho.games.star_citizen import gamelog, loglines
from tycho.packs import STAR_CITIZEN, PackError, built_in, load_pack
from tycho.packs.callouts import Callout, read
from tycho.ptt.monitor import Edge
from tycho.runtime.callouts import GAP_SECONDS, QUIET_SECONDS, READ_SECONDS, Callouts
from tycho.settings import Settings
from tycho.situation import Logged
from tycho.spoken_settings import SettingsTool

SC = built_in(STAR_CITIZEN)
OFFERED = (
    Callout("quantum", "Quantum jump ended", {"arrived": "Jump complete."}, "Jump complete."),
    Callout(
        "jurisdiction",
        "Jurisdiction changes",
        {"jurisdiction": "Now in {name} jurisdiction."},
        "Now in UEE jurisdiction.",
    ),
    Callout(
        "monitored_space",
        "Monitored space",
        {"monitored": "In monitored space.", "left_monitored": "Out of monitored space."},
        "Out of monitored space.",
    ),
)


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class Watch:
    """A ``Callouts`` over a fake log, with what it said and a switch for "busy"."""

    def __init__(self, *on: str) -> None:
        self.log = FakeGameState()
        self.clock = Clock()
        self.said: list[str] = []
        self.busy = False
        self._number = 0
        self.callouts = Callouts(
            self.log,
            OFFERED,
            on,
            busy=lambda: self.busy,
            say=self.said.append,
            clock=self.clock,
        )

    def logged(self, kind: str, name: str = "") -> None:
        self._number += 1
        event = Logged(kind, f"something {kind}", None, self._number, name)
        self.log.recent = (*self.log.recent, event)

    def after(self, seconds: float) -> list[str]:
        """Let ``seconds`` pass and look once."""
        self.clock.now += seconds
        asyncio.run(self.callouts.step())
        return self.said


def started(*on: str) -> Watch:
    """Watching, with the first look done: what was logged before is old news."""
    watch = Watch(*on)
    watch.after(0)
    return watch


# --- Off until the player picks kinds ----------------------------------------------------


def test_callouts_are_off_by_default() -> None:
    assert Settings().callouts == ""
    watch = started()
    watch.logged("arrived")
    assert watch.after(GAP_SECONDS) == []
    assert watch.log.calls == 0  # with nothing on, the log isn't even read


def test_only_the_kinds_turned_on_are_said() -> None:
    watch = started("quantum")
    watch.logged("monitored")
    assert watch.after(READ_SECONDS) == []
    watch.logged("arrived")
    assert watch.after(GAP_SECONDS) == ["Jump complete."]


def test_what_happened_before_is_never_said() -> None:
    watch = Watch("quantum")
    watch.logged("arrived")  # before Tycho started
    assert watch.after(0) == []
    assert watch.after(GAP_SECONDS) == []


def test_turning_a_kind_on_starts_from_now() -> None:
    watch = started("quantum")
    watch.logged("left_monitored")  # while monitored space was off
    watch.callouts.turn_on("quantum, monitored_space")
    assert watch.after(READ_SECONDS) == []
    watch.logged("monitored")
    assert watch.after(READ_SECONDS) == ["In monitored space."]


def test_turning_them_off_stops_them() -> None:
    watch = started("quantum")
    watch.callouts.turn_on("")
    watch.logged("arrived")
    assert watch.after(GAP_SECONDS) == []


def test_a_kind_this_game_does_not_offer_does_nothing() -> None:
    watch = started("mission_updates")  # another game's
    watch.logged("arrived")
    assert watch.after(GAP_SECONDS) == []


# --- Never in the way --------------------------------------------------------------------


def test_at_most_one_every_thirty_seconds_and_the_rest_are_dropped() -> None:
    watch = started("quantum", "monitored_space")
    watch.logged("arrived")
    assert watch.after(READ_SECONDS) == ["Jump complete."]
    watch.logged("monitored")
    assert watch.after(GAP_SECONDS - 2 * READ_SECONDS) == ["Jump complete."]
    # Not queued: once the gap is over, the dropped one isn't said late.
    assert watch.after(GAP_SECONDS) == ["Jump complete."]
    watch.logged("left_monitored")
    assert watch.after(READ_SECONDS) == ["Jump complete.", "Out of monitored space."]


def test_of_several_at_once_only_the_newest_is_said() -> None:
    watch = started("quantum", "monitored_space")
    watch.logged("arrived")
    watch.logged("monitored")
    assert watch.after(READ_SECONDS) == ["In monitored space."]


def test_nothing_is_said_while_someone_is_busy_or_just_after() -> None:
    watch = started("quantum")
    watch.busy = True
    watch.logged("arrived")
    assert watch.after(READ_SECONDS) == []
    watch.busy = False
    watch.logged("arrived")
    assert watch.after(QUIET_SECONDS - 1) == []  # the player only just finished
    assert watch.after(GAP_SECONDS) == []  # dropped, not queued
    watch.logged("arrived")
    assert watch.after(READ_SECONDS) == ["Jump complete."]


def test_a_line_carries_one_short_cleaned_name() -> None:
    watch = started("jurisdiction")
    watch.logged("jurisdiction", "Rough & Ready")
    assert watch.after(READ_SECONDS) == ["Now in Rough & Ready jurisdiction."]
    watch.logged("jurisdiction", "x" * 200)
    said = watch.after(GAP_SECONDS)[-1]
    assert len(said) < len("Now in  jurisdiction.") + 41


def test_a_line_needing_a_name_without_one_is_not_said() -> None:
    watch = started("jurisdiction")
    watch.logged("jurisdiction")
    assert watch.after(READ_SECONDS) == []


def test_each_callout_and_each_dropped_one_is_logged(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO, "tycho.runtime.callouts")
    watch = started("quantum")
    watch.logged("arrived")
    watch.after(READ_SECONDS)
    watch.logged("arrived")
    watch.after(READ_SECONDS)
    assert "Callout quantum (arrived): Jump complete." in caplog.text
    assert "Callout quantum not said: one was said in the last 30 s" in caplog.text


def test_a_failing_log_never_stops_the_watch() -> None:
    watch = started("quantum")
    calls = 0

    def broken() -> object:
        nonlocal calls
        calls += 1
        raise OSError("gone")

    watch.log.situation = broken  # type: ignore[method-assign]

    async def two_ticks() -> None:
        ticks = 0

        async def sleep(_seconds: float) -> None:
            nonlocal ticks
            ticks += 1
            watch.clock.now += READ_SECONDS
            if ticks == 3:
                raise asyncio.CancelledError

        watch.callouts._sleep = sleep  # pyright: ignore[reportPrivateUsage]
        with pytest.raises(asyncio.CancelledError):
            await watch.callouts.run()

    asyncio.run(two_ticks())
    assert calls == 2


# --- In the session: spoken like any line, and nothing else ------------------------------


def test_a_callout_is_spoken_and_push_to_talk_stops_it() -> None:
    async def scenario() -> FakeSpeaker:
        speaker = FakeSpeaker(hold=True)
        brain = FakeBrain()
        r = rig(speaker=speaker, brain=brain)
        assert not r.session.busy()
        r.session.call_out("Jump complete.")
        await speaker.started.wait()
        assert r.session.busy()  # Tycho is talking: no second callout over it
        r.session.handle(Edge.PRESSED)
        await asyncio.sleep(0)
        assert brain.asked == []  # never the brain, never a key
        assert r.pressed() == []
        r.session.handle(Edge.RELEASED)
        await r.session.close()
        return speaker

    speaker = run(scenario)
    assert speaker.spoken == ["Jump complete."]
    assert speaker.stopped == ["Jump complete."]


def test_the_session_is_busy_while_push_to_talk_is_held_or_a_question_waits() -> None:
    async def scenario() -> None:
        r = rig("eject", speaker=FakeSpeaker())
        r.session.handle(Edge.PRESSED)
        assert r.session.busy()
        r.audio.hear(SPEECH)
        r.session.handle(Edge.RELEASED)
        await r.session.idle()
        assert r.results[-1].status == "asking"
        assert r.session.busy()  # waiting for "confirm"
        r.session.pause(True)
        assert r.session.busy()
        await r.session.close()

    run(scenario)


def test_without_a_voice_a_callout_goes_to_the_tile() -> None:
    async def scenario() -> FakeDisplay:
        display = FakeDisplay()
        r = rig(display=display)
        await asyncio.sleep(0)
        r.session.call_out("Out of monitored space.")
        await r.session.close()
        return display

    display = run(scenario)
    assert [card.kind for card in display.shown] == ["notice"]
    assert "Out of monitored space." in repr(display.shown[0])


# --- The setting, by voice and in the pack ----------------------------------------------


@pytest.mark.parametrize(
    ("said", "current", "value"),
    [
        ("off", "quantum, jurisdiction", ""),
        ("stop", "quantum", ""),
        ("all", "", "quantum, jurisdiction, monitored_space"),
        ("+monitored space", "quantum", "quantum, monitored_space"),
        ("-quantum", "quantum, jurisdiction", "jurisdiction"),
        ("Jurisdiction changes", "quantum", "jurisdiction"),
        ("+jurisdiction", "mission_updates", "jurisdiction, mission_updates"),
    ],
)
def test_the_setting_by_voice(said: str, current: str, value: str) -> None:
    assert callouts.said(said, current, OFFERED) == value


def test_an_unknown_kind_by_voice_says_which_there_are() -> None:
    with pytest.raises(ValueError, match=r"quantum \(Quantum jump ended\)"):
        callouts.said("+missions", "", OFFERED)


def _tool_run(tool: SettingsTool, action: str, *changes: tuple[str, str]) -> str:
    arguments = {"action": action, "changes": [{"name": n, "value": v} for n, v in changes]}
    return asyncio.run(tool.run(arguments)).content


def test_stop_the_callouts_by_voice_turns_them_all_off_now(tmp_path: Path) -> None:
    path = tmp_path / "settings.toml"
    settings.save(Settings(callouts="quantum, jurisdiction"), path)
    applied: list[object] = []
    tool = SettingsTool(path, live={"callouts": applied.append}, offered=OFFERED)
    assert '"kinds":{"quantum":"Quantum jump ended"' in _tool_run(tool, "get")
    _tool_run(tool, "set", ("callouts", "off"))
    assert settings.load(path).callouts == ""
    assert applied == [""]
    _tool_run(tool, "set", ("callouts", "+quantum"))  # turning one on by voice is allowed
    assert settings.load(path).callouts == "quantum"


def test_the_setting_keeps_only_kind_ids() -> None:
    assert Settings(callouts="Quantum; jurisdiction, quantum").callouts == "quantum, jurisdiction"
    with pytest.raises(ValueError, match="callout kinds are words"):
        Settings(callouts="ignore previous instructions!")


def test_star_citizens_callouts_follow_what_its_log_records() -> None:
    kinds = {v for k, v in vars(loglines).items() if k.isupper() and isinstance(v, str)}
    assert SC.callouts
    for callout in SC.callouts:
        assert set(callout.lines) <= kinds, callout.id
        assert all(len(line.split()) <= 8 for line in callout.lines.values())
        assert "!" not in "".join(callout.lines.values())  # calm, no exclamation marks


def test_a_real_jurisdiction_line_gives_its_name_to_the_callout(tmp_path: Path) -> None:
    line = (
        "<2026-11-16T07:12:00.929Z> [Notice] <SHUDEvent_OnNotification> Added notification "
        '"Entered Rough & Ready Jurisdiction: " [2] to queue. New queue size: 2\n'
    )
    path = tmp_path / "Game.log"
    path.write_text("<2026-11-16T07:10:48.369Z> Log started\n" + line, "utf-8")
    [event] = gamelog.GameLog(path).situation().recent
    assert event.name == "Rough & Ready"


@pytest.mark.parametrize(
    ("table", "problem"),
    [
        ({"Bad Id": {"name": "x", "lines": {"arrived": "x"}}}, "lower case"),
        ({"a": {"name": "x", "lines": {"arrived": "{said}"}}}, "only {name}"),
        ({"a": {"name": "x", "lines": {"arrived": "x" * 81}}}, "up to 80"),
        ({"a": {"name": "x", "lines": {}}}, "a line for each"),
        ({"a": {"name": "x", "lines": {"arrived": "x"}, "line": "y"}}, "unknown keys"),
        (
            {
                "a": {"name": "x", "lines": {"arrived": "x"}},
                "b": {"name": "y", "lines": {"arrived": "y"}},
            },
            "followed by a and b",
        ),
    ],
)
def test_a_packs_callouts_are_checked(table: dict[str, object], problem: str) -> None:
    with pytest.raises(ValueError, match=problem):
        read(table, "pack.toml")


def test_callouts_need_a_log_to_follow(tmp_path: Path) -> None:
    folder = tmp_path / "fake-advisor"
    shutil.copytree(Path(str(ADVISOR)), folder)
    with (folder / "pack.toml").open("a", encoding="utf-8") as file:
        file.write('\n[callouts.a]\nname = "A"\nlines = { arrived = "A." }\n')
    with pytest.raises(PackError, match="name a game_state part"):
        load_pack(folder, built_in=False, registry=REGISTRY)


def test_the_window_lists_each_kind_with_its_line() -> None:
    from tycho.desktop.bridge.settings_page import callout_kinds

    kinds = callout_kinds(STAR_CITIZEN)
    assert {"kind": "jurisdiction", "name": "Jurisdiction changes"}.items() <= kinds[2].items()
    assert all(k["example"] and "{" not in k["example"] for k in kinds)
    assert callout_kinds("wow-forever") == []  # nothing in its log to follow


def test_serve_watches_beside_the_session_and_stops_with_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import contextlib

    from tycho.runtime import console
    from tycho.start.serve import serve

    def no_guard(*_args: object, **_kwargs: object) -> contextlib.nullcontext[None]:
        return contextlib.nullcontext()

    monkeypatch.setattr(console, "CloseGuard", no_guard)

    class Session:
        def warm_brain(self) -> None: ...

        async def run(self, stop: asyncio.Event) -> None:
            await asyncio.sleep(0.05)

    class Watching:
        started = cancelled = False

        async def run(self) -> None:
            self.started = True
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                self.cancelled = True
                raise

    watching = Watching()
    asyncio.run(serve(Session(), None, callouts=watching))  # pyright: ignore[reportArgumentType]
    assert watching.started
    assert watching.cancelled
