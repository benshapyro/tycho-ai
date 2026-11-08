"""The whole talking loop with fake hardware: real lexicon, matcher, gate and presser (ADR 11)."""

import asyncio
import dataclasses
import logging
import threading
import time
from collections.abc import Callable, Coroutine
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from tests.fakes.fake_audio import FakeAudioSource
from tests.fakes.fake_brain import FakeBrain
from tests.fakes.fake_display import FakeDisplay
from tests.fakes.fake_input import FakeInputSource
from tests.fakes.fake_keys import FakeFocus, FakeKeySender
from tests.fakes.fake_runtime import (
    FakeActionLog,
    FakeFeedback,
    FakeGameState,
    FakeTranscriber,
)
from tests.fakes.fake_speaker import FakeSpeaker
from tycho import safety
from tycho.brain import BrainError, BrainRequest, Decision, Reply, RunActions, RunSequence
from tycho.commands import CommandMatcher
from tycho.game.gamelog import QUANTUM_ROUTE
from tycho.game.keybinds import Action, ActivationMode, Keybinds, KeyInput
from tycho.input import Presser, code_for
from tycho.lexicon import Lexicon
from tycho.ptt.binding import Binding
from tycho.ptt.monitor import Edge, PushToTalk
from tycho.runtime import AlreadyRunning, Result, Session, SingleInstance
from tycho.runtime.session import CHECKING, FILLERS, KEEP_CHARACTERS
from tycho.tile import Card, Row

RATE = 16_000
PTT = Binding.parse("mouse:0x06")
# Half a second of steady sound: never quiet, so the tail runs its full length and
# nothing is trimmed. The transcriber is scripted, so what it says doesn't matter.
SPEECH = np.full(RATE // 2, 0.1, dtype=np.float32)
SILENCE = np.zeros(RATE // 10, dtype=np.float32)  # a tenth of a second

MODES = {
    "press": ActivationMode("press"),
    "delayed_press_long": ActivationMode("delayed_press_long", press_threshold=1.5),
}


def _action(name: str, keys: str, activation: str = "", actionmap: str = "m") -> Action:
    key = KeyInput.parse(keys)
    assert key is not None
    return Action(name=name, actionmap=actionmap, label=name, inputs=(key,), activation=activation)


KEYBINDS = Keybinds(
    [
        _action("v_toggle_landing_system", "n"),
        _action("v_atc_request", "lalt+n"),
        _action("v_eject", "ralt+y"),
        _action("v_toggle_qdrive_engagement", "b"),
        _action("v_master_mode_set_nav", "ralt+b"),
        _action("v_set_quantum_mode", "rctrl+q"),
    ],
    MODES,
)

_MATCHER = CommandMatcher.default()
_LEXICON = Lexicon.default()


@dataclass
class Rig:
    session: Session
    audio: FakeAudioSource
    keys: FakeKeySender
    feedback: FakeFeedback
    transcriber: FakeTranscriber
    results: list[Result] = field(default_factory=list[Result])
    actions: FakeActionLog = field(default_factory=FakeActionLog)

    def say(self) -> None:
        """Hold push-to-talk, speak, let go."""
        self.session.handle(Edge.PRESSED)
        self.audio.hear(SPEECH)
        self.session.handle(Edge.RELEASED)

    def pressed(self) -> list[str]:
        return [f"{kind}:{code}" for kind, code in self.keys.events]


def rig(
    *lines: str | Exception,
    focus: FakeFocus | None = None,
    keybinds: Keybinds = KEYBINDS,
    hold: threading.Event | None = None,
    log_transcripts: bool = False,
    sources: list[FakeInputSource] | None = None,
    game_state: FakeGameState | None = None,
    keybinds_now: Callable[[], Keybinds] | None = None,
    clock: Callable[[], float] = time.monotonic,
    brain: FakeBrain | None = None,
    brain_seconds: float = 8.0,
    search_seconds: float = 12.0,
    looking_seconds: float = 7.0,
    looking_limit: float = 15.0,
    speaker: FakeSpeaker | None = None,
    matcher: CommandMatcher = _MATCHER,
    address: Callable[[], str] = lambda: "",
    display: FakeDisplay | None = None,
) -> Rig:
    audio, keys, feedback = FakeAudioSource(RATE), FakeKeySender(), FakeFeedback()
    actions = FakeActionLog()
    transcriber = FakeTranscriber(*lines, hold=hold)
    results: list[Result] = []
    session = Session(
        audio=audio,
        push_to_talk=PushToTalk(PTT, sources or []),
        transcriber=transcriber,
        lexicon=_LEXICON,
        matcher=matcher,
        keybinds=keybinds_now or (lambda: keybinds),
        presser=Presser(keys, focus or FakeFocus(True)),
        feedback=feedback,
        game_state=game_state,
        action_log=actions,
        brain=brain,
        brain_seconds=brain_seconds,
        search_seconds=search_seconds,
        looking_seconds=looking_seconds,
        looking_limit=looking_limit,
        speaker=speaker,
        samplerate=RATE,
        log_transcripts=log_transcripts,
        on_result=results.append,
        poll_interval=0.001,
        clock=clock,
        address=address,
        display=display,
    )
    return Rig(session, audio, keys, feedback, transcriber, results, actions)


def run[T](test: Callable[[], Coroutine[Any, Any, T]]) -> T:
    return asyncio.run(asyncio.wait_for(test(), timeout=10))


def _down_up(name: str) -> list[str]:
    code = code_for(name)
    return [f"down:{code}", f"up:{code}"]


# --- Requests that work ----------------------------------------------------------------


def test_a_command_presses_the_players_key_and_plays_done() -> None:
    async def scenario() -> Result | None:
        r = rig("landing gear")
        r.say()
        result = await r.session.idle()
        assert r.pressed() == _down_up("n")
        assert r.feedback.played == ["done"]
        await r.session.close()
        return result

    result = run(scenario)
    assert result is not None
    assert result.status == "done"
    assert result.pressed == ("v_toggle_landing_system",)
    assert set(result.timings) == {"tail", "transcribe", "correct", "match", "gate", "press"}
    assert result.total_ms >= sum(result.timings.values()) - 0.01


def test_a_chain_presses_every_part_in_order() -> None:
    async def scenario() -> Rig:
        r = rig("gear down and request landing")
        r.say()
        await r.session.idle()
        await r.session.close()
        return r

    r = run(scenario)
    alt, n = code_for("lalt"), code_for("n")
    assert r.pressed() == [*_down_up("n"), f"down:{alt}", f"down:{n}", f"up:{n}", f"up:{alt}"]
    assert r.results[0].pressed == ("v_toggle_landing_system", "v_atc_request")


def test_the_lexicon_corrects_what_was_heard_before_matching() -> None:
    async def scenario() -> Result | None:
        r = rig("fly to micro tech")
        r.say()
        result = await r.session.idle()
        await r.session.close()
        return result

    result = run(scenario)
    assert result is not None
    assert result.heard == "fly to micro tech"
    assert result.text == "fly to microTech"
    assert result.status == "not_understood"  # navigation is the brain's job


# --- Asking first: confirm-grade requests (ADR 13) -------------------------------------


def _conversation(*lines: str, **options: Any) -> Rig:
    """Say each line on its own push-to-talk, waiting for each to finish."""

    async def scenario() -> Rig:
        r = rig(*lines, **options)
        for _ in lines:
            r.say()
            await r.session.idle()
        await r.session.close()
        return r

    return run(scenario)


def test_eject_asks_first_and_presses_nothing() -> None:
    r = _conversation("eject")
    assert r.results[0].status == "asking"
    assert 'say "confirm"' in r.results[0].detail
    assert r.keys.events == []
    assert r.feedback.played == ["ask"]


def test_confirm_carries_out_the_request() -> None:
    r = _conversation("eject", "confirm")
    assert [x.status for x in r.results] == ["asking", "done"]
    assert r.results[1].pressed == ("v_eject",)
    assert r.results[1].confirmed
    alt, y = code_for("ralt"), code_for("y")
    assert r.pressed() == [f"down:{alt}", f"down:{y}", f"up:{y}", f"up:{alt}"]
    assert r.feedback.played == ["ask", "done"]


def test_a_chain_with_a_confirm_grade_part_waits_as_a_whole() -> None:
    r = _conversation("gear down and eject", "affirmative")
    assert r.results[0].status == "asking"
    assert r.results[1].pressed == ("v_toggle_landing_system", "v_eject")


@pytest.mark.parametrize("reply", ["cancel", "negative", "belay that", "never mind"])
def test_cancel_drops_the_question(reply: str) -> None:
    r = _conversation("eject", reply, "confirm")
    assert [x.status for x in r.results] == ["asking", "done", "not_understood"]
    assert r.results[2].detail == "nothing to confirm"
    assert r.keys.events == []
    assert r.feedback.played == ["ask", "done", "not_done"]
    assert r.results[1].commands == ("cancel",)
    assert r.results[1].detail == "cancelled #1 (eject)"


def test_saying_anything_else_drops_the_question() -> None:
    r = _conversation("eject", "landing gear", "confirm")
    assert [x.status for x in r.results] == ["asking", "done", "not_understood"]
    assert r.pressed() == _down_up("n")  # the gear, never the eject


def test_a_slip_of_the_button_keeps_the_question() -> None:
    r = _conversation("eject", "", "confirm")
    assert [x.status for x in r.results] == ["asking", "empty", "done"]


def _ask_then_confirm_after(seconds: float) -> Rig:
    now = [100.0]

    async def scenario() -> Rig:
        r = rig("eject", "confirm", clock=lambda: now[0])
        r.say()
        await r.session.idle()
        now[0] += seconds
        r.say()
        await r.session.idle()
        await r.session.close()
        return r

    return run(scenario)


def test_the_question_expires() -> None:
    assert _ask_then_confirm_after(4.9).results[1].status == "done"
    late = _ask_then_confirm_after(5.1)
    assert late.results[1].status == "not_understood"
    assert late.results[1].detail == "the question expired"
    assert late.keys.events == []


def test_confirm_with_nothing_asked_does_nothing() -> None:
    r = _conversation("confirm")
    assert r.results[0].status == "not_understood"
    assert r.results[0].detail == "nothing to confirm"
    assert r.keys.events == []


def test_a_confirmation_checks_the_keybinds_again() -> None:
    # Between the question and "confirm", the player puts another dangerous action on eject's key.
    later = Keybinds([*KEYBINDS, _action("v_self_destruct", "ralt+y")], MODES)
    current = [KEYBINDS]

    async def scenario() -> Rig:
        r = rig("eject", "confirm", keybinds_now=lambda: current[0])
        r.say()
        await r.session.idle()
        current[0] = later
        r.say()
        await r.session.idle()
        await r.session.close()
        return r

    r = run(scenario)
    assert r.results[1].status == "refused"
    assert "v_self_destruct" in r.results[1].detail
    assert r.keys.events == []


def test_every_request_that_matched_is_in_the_action_log() -> None:
    r = _conversation("eject", "confirm", "sing me a song", "landing gear")
    keydowns = [entry.pop("keydown_ms", None) for entry in r.actions.entries]
    assert keydowns[0] is None  # asked, pressed nothing
    assert all(isinstance(ms, int) and ms >= 0 for ms in keydowns[1:])
    assert r.actions.entries == [
        {
            "request": 1,
            "commands": ["eject"],
            "status": "asking",
            "pressed": [],
            "confirmed": False,
            "detail": r.results[0].detail,
        },
        {
            "request": 2,
            "commands": ["eject"],
            "status": "done",
            "pressed": ["v_eject"],
            "confirmed": True,
        },
        {
            "request": 4,
            "commands": ["landing_gear"],
            "status": "done",
            "pressed": ["v_toggle_landing_system"],
            "confirmed": False,
        },
    ]  # "sing me a song" matched nothing, so it isn't an action


def test_the_time_to_the_first_key_down_is_measured(caplog: pytest.LogCaptureFixture) -> None:
    # The 0.3 s target is about when the game sees the key, not when the tap ends (#58).
    caplog.set_level(logging.INFO, logger="tycho.runtime.session")
    r = _conversation("prepare for landing")  # two steps, a beat apart
    result = r.results[0]
    assert result.keydown_ms is not None
    assert result.keydown_ms < result.total_ms  # the first key, not the last
    assert "key down at" in caplog.text
    assert _conversation("sing me a song").results[0].keydown_ms is None


def test_the_action_log_says_what_was_said_only_if_transcripts_are_kept() -> None:
    assert "said" not in _conversation("landing gear").actions.entries[0]
    kept = _conversation("landing gear", log_transcripts=True)
    assert kept.actions.entries[0]["said"] == "landing gear"


# --- Sequences (ADR 14) -----------------------------------------------------------------


def test_a_sequence_presses_its_steps_in_order() -> None:
    r = _conversation("prepare for landing")
    result = r.results[0]
    assert result.status == "done"
    assert result.commands == ("prepare_to_land",)  # logged as what was asked for
    assert result.pressed == ("v_atc_request", "v_toggle_landing_system")


def test_a_sequence_with_a_step_that_has_no_key_presses_nothing() -> None:
    r = _conversation("startup")  # flight ready has no key in this rig
    assert r.results[0].status == "no_key"
    assert "flight_ready" in r.results[0].detail
    assert r.keys.events == []


# --- The brain (ADR 14) ------------------------------------------------------------------


def test_what_the_fast_path_misses_goes_to_the_brain() -> None:
    brain = FakeBrain(RunActions(("v_toggle_landing_system",), "Gear down."))
    r = _conversation("put the wheels out would you", brain=brain, log_transcripts=True)
    result = r.results[0]
    assert result.status == "done"
    assert result.by_brain
    assert result.commands == ("landing_gear",)  # the action ran as the fast path's command
    assert result.say == "Gear down."
    assert r.pressed() == _down_up("n")
    assert brain.asked[0].text == "put the wheels out would you"
    assert r.actions.entries[0]["by"] == "brain"
    assert r.actions.entries[0]["tycho_said"] == "Gear down."


def test_the_fast_path_never_asks_the_brain() -> None:
    brain = FakeBrain()
    r = _conversation("landing gear", brain=brain)
    assert r.results[0].status == "done"
    assert not r.results[0].by_brain
    assert brain.asked == []


def test_a_reply_presses_nothing() -> None:
    r = _conversation("what's a good mining ship", brain=FakeBrain(Reply("The Prospector.")))
    result = r.results[0]
    assert result.status == "replied"
    assert result.say == "The Prospector."
    assert r.keys.events == []
    assert r.feedback.played == ["done"]
    assert r.actions.entries == []  # nothing was done, so it isn't an action


def test_the_brain_can_run_a_sequence() -> None:
    brain = FakeBrain(RunSequence("prepare_to_land", "Bringing us in."))
    r = _conversation("take us down to the pad", brain=brain)
    assert r.results[0].commands == ("prepare_to_land",)
    assert r.results[0].pressed == ("v_atc_request", "v_toggle_landing_system")


def test_when_the_brain_fails_tycho_says_so_and_presses_nothing() -> None:
    r = _conversation("do the thing", brain=FakeBrain(BrainError("can't reach Claude")))
    assert r.results[0].status == "failed"
    assert "can't reach Claude" in r.results[0].detail
    assert r.keys.events == []
    assert r.feedback.played == ["not_done"]


def test_a_brain_that_takes_too_long_is_given_up_on() -> None:
    r = _conversation("do the thing", brain=FakeBrain(hang=True), brain_seconds=0.05)
    assert r.results[0].status == "failed"
    assert "took too long" in r.results[0].detail


def test_a_confirm_grade_pick_from_the_brain_still_asks() -> None:
    brain = FakeBrain(RunActions(("v_eject",), "Ejecting."))
    r = _conversation("get me out of this thing", "confirm", brain=brain)
    assert [x.status for x in r.results] == ["asking", "done"]
    assert r.results[1].pressed == ("v_eject",)
    assert r.results[1].by_brain  # the confirmation carries out the brain's pick
    assert r.results[1].say == "Ejecting."
    assert len(brain.asked) == 1  # "confirm" itself never goes to the brain


def test_travel_the_brain_picked_asks_first_and_then_runs_as_quantum_engage() -> None:
    brain = FakeBrain(RunActions(("v_toggle_qdrive_engagement",), "Engaging."))
    r = _conversation("lets get out of here", "confirm", brain=brain)
    assert r.results[0].status == "asking"
    assert r.results[0].commands == ("quantum_engage",)
    assert r.results[1].pressed == (
        "v_master_mode_set_nav",
        "v_set_quantum_mode",
        "v_toggle_qdrive_engagement",
    )


def test_recent_exchanges_go_to_the_brain_for_follow_ups() -> None:
    brain = FakeBrain(Reply("Which door?"), RunActions(("v_atc_request",), "Requested."))
    r = _conversation("landing gear", "sort the door out", "call them again", brain=brain)
    first, second = brain.asked
    assert [e.said for e in first.recent] == ["landing gear"]
    assert "landing_gear" in first.recent[0].outcome
    assert [e.said for e in second.recent] == ["landing gear", "sort the door out"]
    assert second.recent[1].outcome == "said: Which door?"
    assert r.results[2].status == "done"


def test_after_half_a_minute_of_quiet_the_brain_starts_fresh() -> None:
    now = [100.0]
    brain = FakeBrain(Reply("Hello."), Reply("Hello again."))

    async def scenario() -> Rig:
        r = rig("hi there", "you again", clock=lambda: now[0], brain=brain)
        r.say()
        await r.session.idle()
        now[0] += 31
        r.say()
        await r.session.idle()
        await r.session.close()
        return r

    run(scenario)
    assert brain.asked[1].recent == ()


def test_without_a_brain_unmatched_requests_are_not_understood() -> None:
    r = _conversation("put the wheels out would you")
    assert r.results[0].status == "not_understood"


# --- Requests that must not press anything ----------------------------------------------


@pytest.mark.parametrize(
    ("heard", "status"),
    [
        ("sing me a song", "not_understood"),  # for the brain, which arrives in 0.3
        ("don't lower the gear", "not_understood"),  # negations always defer
        ("open the doors", "no_key"),  # neither door action is bound here
        ("", "empty"),
    ],
)
def test_nothing_is_pressed(heard: str, status: str) -> None:
    async def scenario() -> Rig:
        r = rig(heard)
        r.say()
        await r.session.idle()
        await r.session.close()
        return r

    r = run(scenario)
    assert r.results[0].status == status
    assert r.keys.events == []
    assert r.feedback.played == ["not_done"]


def test_without_game_focus_tycho_listens_but_does_not_press() -> None:
    async def scenario() -> Rig:
        r = rig("landing gear", focus=FakeFocus(False))
        r.say()
        await r.session.idle()
        await r.session.close()
        return r

    r = run(scenario)
    assert r.results[0].status == "no_focus"
    assert r.transcriber.calls == 1
    assert r.keys.events == []
    assert r.feedback.played == ["not_done"]


def test_a_slip_of_the_finger_is_ignored_without_transcribing() -> None:
    async def scenario() -> Rig:
        r = rig("landing gear")
        r.session.handle(Edge.PRESSED)
        r.audio.hear(SPEECH[: RATE // 10])
        r.session.handle(Edge.RELEASED)
        await r.session.idle()
        await r.session.close()
        return r

    r = run(scenario)
    assert r.results[0].status == "empty"
    assert r.transcriber.calls == 0


def test_a_failure_is_reported_and_the_loop_carries_on() -> None:
    async def scenario() -> Rig:
        r = rig(RuntimeError("model crashed"), "landing gear")
        r.say()
        await r.session.idle()
        r.say()
        await r.session.idle()
        await r.session.close()
        return r

    r = run(scenario)
    assert [x.status for x in r.results] == ["failed", "done"]
    assert "model crashed" in r.results[0].detail
    assert r.feedback.played == ["not_done", "done"]


def test_audio_just_after_release_is_kept() -> None:
    async def scenario() -> Rig:
        r = rig("landing gear")
        r.session.handle(Edge.PRESSED)
        r.audio.hear(SPEECH)
        r.session.handle(Edge.RELEASED)
        r.audio.hear(SPEECH[: RATE // 10])  # the end of the last word, still arriving
        await r.session.idle()
        await r.session.close()
        return r

    r = run(scenario)
    assert r.transcriber.seconds == [pytest.approx(0.6)]


def test_the_tail_ends_at_once_if_the_player_had_finished() -> None:
    async def scenario() -> Rig:
        r = rig("landing gear")
        r.session.handle(Edge.PRESSED)
        r.audio.hear(SPEECH)
        r.audio.hear(SILENCE)  # they stopped talking, then let go
        r.session.handle(Edge.RELEASED)
        await r.session.idle()
        await r.session.close()
        return r

    r = run(scenario)
    assert r.results[0].status == "done"
    assert r.results[0].timings["tail"] < 50  # not the full 150 ms


def test_only_the_speech_is_transcribed() -> None:
    async def scenario() -> Rig:
        r = rig("landing gear")
        r.session.handle(Edge.PRESSED)
        for _ in range(5):
            r.audio.hear(SILENCE)  # half a second before they start talking
        r.audio.hear(SPEECH)
        r.audio.hear(SILENCE)
        r.session.handle(Edge.RELEASED)
        await r.session.idle()
        await r.session.close()
        return r

    r = run(scenario)
    assert r.transcriber.seconds == [pytest.approx(0.5 + 0.08 + 0.08)]  # plus the margins


# --- Barge-in and shutdown --------------------------------------------------------------


def test_pressing_again_during_the_tail_starts_afresh() -> None:
    async def scenario() -> Rig:
        r = rig("request landing")
        r.say()
        r.session.handle(Edge.PRESSED)  # straight away, before the tail is over
        r.audio.hear(SPEECH)
        r.session.handle(Edge.RELEASED)
        await r.session.idle()
        await r.session.close()
        return r

    r = run(scenario)
    assert [x.status for x in r.results] == ["done"]  # the first never got started
    assert r.transcriber.seconds == [pytest.approx(0.5)]  # only the second request's audio


def test_pressing_again_cancels_the_request_being_transcribed() -> None:
    async def scenario() -> Rig:
        hold = threading.Event()
        r = rig("landing gear", "request landing", hold=hold)
        r.say()
        await asyncio.to_thread(r.transcriber.started.wait, 5)
        r.session.handle(Edge.PRESSED)  # barge in
        hold.set()
        r.audio.hear(SPEECH)
        r.session.handle(Edge.RELEASED)
        await r.session.idle()
        await r.session.close()
        return r

    r = run(scenario)
    assert [x.status for x in r.results] == ["cancelled", "done"]
    assert r.results[1].pressed == ("v_atc_request",)
    assert "v_toggle_landing_system" not in r.results[1].pressed
    assert r.feedback.played == ["done"]  # no "not done" for a barge-in


def _long_press_rig(lines: str = "landing gear") -> tuple[Rig, threading.Event]:
    """A rig whose gear key is a 1.6 s delayed press, and an event set once it's held."""
    keybinds = Keybinds([_action("v_toggle_landing_system", "n", "delayed_press_long")], MODES)
    r = rig(lines, "request landing", keybinds=keybinds)
    key_down = threading.Event()
    r.keys.on_event = key_down.set
    return r, key_down


def test_pressing_again_stops_a_key_sequence_and_lets_go() -> None:
    async def scenario() -> tuple[Rig, float]:
        r, key_down = _long_press_rig()
        r.say()
        await asyncio.to_thread(key_down.wait, 5)
        started = time.monotonic()
        r.session.handle(Edge.PRESSED)
        await r.session.idle()
        stopped = time.monotonic() - started
        r.session.handle(Edge.RELEASED)  # a too-short clip; ignored
        await r.session.close()
        return r, stopped

    r, stopped = run(scenario)
    assert r.results[0].status == "cancelled"
    assert stopped < 1.2  # well short of the full 1.6 s hold, even on a slow runner
    assert r.keys.held() == set()
    # The key went down before the cancel, so the log says so (audit, 2026-11-03).
    assert r.results[0].pressed == ("v_toggle_landing_system",)


def test_shutdown_doesnt_wait_for_tycho_to_finish_talking() -> None:
    speaker = FakeSpeaker(hold=True)  # a line that never ends on its own

    async def scenario() -> None:
        r = rig(
            "what's a good mining ship", brain=FakeBrain(Reply("The Prospector.")), speaker=speaker
        )
        r.say()
        await speaker.started.wait()
        await asyncio.wait_for(r.session.close(), 2)

    run(scenario)
    assert speaker.stopped == ["The Prospector."]
    assert speaker.closed


def test_push_to_talk_keeps_working_after_a_joystick_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("tycho.runtime.session.POLL_RETRY_SECONDS", 0.001)

    class Flaky:
        binding = PTT

        def __init__(self) -> None:
            self.calls = 0

        def poll(self) -> Edge | None:
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("Invalid joystick device number")  # what SDL can raise
            return Edge.PRESSED if self.calls == 2 else None

    async def scenario() -> bool:
        r = rig()
        r.session._ptt = Flaky()  # pyright: ignore[reportAttributeAccessIssue, reportPrivateUsage]
        stop = asyncio.Event()
        running = asyncio.create_task(r.session.run(stop))
        listening = False
        for _ in range(500):
            if listening := r.session._listening:  # pyright: ignore[reportPrivateUsage]
                break
            await asyncio.sleep(0.002)
        stop.set()
        await running
        return listening

    assert run(scenario)  # the press after the error still started listening


def test_shutdown_releases_a_held_key() -> None:
    async def scenario() -> Rig:
        r, key_down = _long_press_rig()
        r.say()
        await asyncio.to_thread(key_down.wait, 5)
        await r.session.close()
        return r

    r = run(scenario)
    assert r.keys.held() == set()
    assert r.results[0].status == "cancelled"
    assert r.results[0].pressed == ("v_toggle_landing_system",)


def test_after_close_push_to_talk_is_ignored() -> None:
    async def scenario() -> Rig:
        r = rig("landing gear")
        await r.session.close()
        r.say()
        return r

    r = run(scenario)
    assert r.results == []
    assert r.transcriber.calls == 0


def test_run_polls_the_real_button_and_stops_cleanly() -> None:
    async def scenario() -> Rig:
        button = FakeInputSource()
        r = rig("landing gear", sources=[button])
        stop = asyncio.Event()
        runner = asyncio.create_task(r.session.run(stop))
        await asyncio.sleep(0.05)
        button.held.add(PTT)
        await asyncio.sleep(0.05)
        r.audio.hear(SPEECH)
        button.held.discard(PTT)
        await asyncio.to_thread(r.transcriber.started.wait, 5)  # the poller saw the release
        await r.session.idle()
        stop.set()
        await runner
        return r

    r = run(scenario)
    assert r.results[0].status == "done"
    assert r.pressed() == _down_up("n")


# --- Logging ---------------------------------------------------------------------------


@pytest.mark.parametrize("keep", [False, True])
def test_what_was_said_is_logged_only_if_the_player_allows_it(
    keep: bool, caplog: pytest.LogCaptureFixture
) -> None:
    async def scenario() -> None:
        r = rig("landing gear", log_transcripts=keep)
        r.say()
        await r.session.idle()
        await r.session.close()

    with caplog.at_level(logging.INFO, logger="tycho.runtime"):
        run(scenario)
    line = next(m for m in caplog.messages if m.startswith("#1"))
    assert ("landing gear" in line) is keep
    assert "done" in line
    assert "transcribe" in line


# --- The gate and the single instance --------------------------------------------------


def test_the_gate_catches_dangerous_actions_it_has_never_seen() -> None:
    assert safety.is_confirm_grade("v_eject")
    assert safety.is_confirm_grade("v_eject_seat_v2")  # a renamed action after a patch
    assert safety.is_confirm_grade("v_self_destruct_now")
    assert not safety.is_confirm_grade("v_toggle_landing_system")
    assert not safety.is_confirm_grade("v_lights")


@pytest.mark.parametrize(
    "name",
    [
        "weapon_change_firemode",  # choosing a fire mode isn't firing
        "v_weapon_staggered_fire_on",
        "v_weapon_launch_missile_cinematic",  # the missile camera
        "v_target_cycle_attacker_fwd",  # targeting whoever attacks you
    ],
)
def test_the_backstop_spares_harmless_lookalikes(name: str) -> None:
    assert not safety.is_confirm_grade(name)


def test_only_one_tycho_runs_at_a_time() -> None:
    name = rf"Local\tycho-ai-test-{time.monotonic_ns()}"
    with SingleInstance(name), pytest.raises(AlreadyRunning), SingleInstance(name):
        pass
    with SingleInstance(name):  # released on exit, so it can be taken again
        pass


def _engage(game_state: FakeGameState | None, keybinds: Keybinds = KEYBINDS) -> Rig:
    async def scenario() -> Rig:
        r = rig("engage quantum", keybinds=keybinds, game_state=game_state)
        r.say()
        await r.session.idle()
        await r.session.close()
        return r

    return run(scenario)


def test_quantum_engage_sets_nav_and_quantum_mode_then_engages() -> None:
    assert not safety.is_confirm_grade("v_toggle_qdrive_engagement")
    started = time.monotonic()
    r = _engage(FakeGameState(QUANTUM_ROUTE))
    assert r.results[0].status == "done"
    assert r.results[0].pressed == (
        "v_master_mode_set_nav",
        "v_set_quantum_mode",
        "v_toggle_qdrive_engagement",
    )
    assert r.pressed()[-2:] == _down_up("b")
    assert time.monotonic() - started >= 1.2  # waited for the switch to NAV


def test_jumps_are_routine_not_confirm_grade() -> None:
    assert not safety.is_confirm_grade("v_toggle_jump_request")


@pytest.mark.parametrize("game_state", [None, FakeGameState()], ids=["no log", "nothing logged"])
def test_quantum_engage_needs_nothing_from_the_log(game_state: FakeGameState | None) -> None:
    r = _engage(game_state, Keybinds(LEFT_CLICK, MODES))
    assert r.results[0].status == "done"
    assert r.pressed()[-2:] == _down_up("mouse1")


def test_on_foot_firing_is_refused_unless_the_command_accepts_it() -> None:
    engage = next(c for c in CommandMatcher.default().commands if c.id == "quantum_engage")
    assert engage.accepted_risks == ("player",)
    keybinds = Keybinds(LEFT_CLICK, MODES)
    click = keybinds.get("v_toggle_qdrive_engagement")
    assert click is not None
    assert safety.check(engage, click, keybinds).allowed
    unaccepted = dataclasses.replace(engage, accepted_risks=())
    verdict = safety.check(unaccepted, click, keybinds)
    assert not verdict.allowed
    assert "attack1" in verdict.reason


# Left click engages the quantum drive in quantum mode, but also fires.
LEFT_CLICK = [
    _action("v_toggle_qdrive_engagement", "mouse1", actionmap="spaceship_quantum"),
    _action("v_master_mode_set_nav", "ralt+b", actionmap="spaceship_movement"),
    _action("v_set_quantum_mode", "rctrl+q", actionmap="seat_general"),
    _action("attack1", "mouse1", actionmap="player"),
    _action("v_weapon_preset_attack", "mouse1", actionmap="spaceship_weapons"),
    _action("v_toggle_mining_laser_fire", "mouse1", actionmap="spaceship_mining"),
    _action("v_toggle_jump_request", "mouse1", actionmap="spaceship_movement"),
]


def test_left_click_is_safe_once_nav_quantum_mode_and_the_seat_rule_out_firing() -> None:
    r = _engage(FakeGameState(QUANTUM_ROUTE), Keybinds(LEFT_CLICK, MODES))
    assert r.results[0].status == "done"
    assert r.pressed()[-2:] == _down_up("mouse1")


def test_a_shared_key_still_dangerous_after_setup_is_never_pressed() -> None:
    eject = _action("v_eject", "mouse1", actionmap="spaceship_general")
    r = _engage(FakeGameState(QUANTUM_ROUTE), Keybinds([*LEFT_CLICK, eject], MODES))
    assert r.results[0].status == "refused"
    assert "v_eject" in r.results[0].detail
    assert r.keys.events == []


def test_a_setup_key_is_checked_before_its_own_mode_change() -> None:
    # The NAV key is also missile launch. Pressing NAV is what turns missiles off, so
    # NAV itself is pressed while missiles still fire: it must be refused.
    missiles = _action("v_weapon_launch_missile", "ralt+b", actionmap="spaceship_missiles")
    r = _engage(FakeGameState(QUANTUM_ROUTE), Keybinds([*LEFT_CLICK, missiles], MODES))
    assert r.results[0].status == "refused"
    assert "v_weapon_launch_missile" in r.results[0].detail
    assert r.keys.events == []


def test_a_players_file_cant_take_eject_out_of_confirming(tmp_path: Path) -> None:
    file = tmp_path / "commands.toml"
    file.write_text(
        '[[command]]\nid = "eject"\nsay = ["bail"]\nactions = ["v_eject"]\nconfirm = false\n'
        '[[sequence]]\nid = "bye"\nsay = ["goodbye ship"]\nsteps = ["eject"]\n',
        "utf-8",
    )
    matcher = CommandMatcher.default(file)
    for name in ("eject", "bye"):
        command = matcher.get(name)
        assert command is not None
        for step in command.expand():
            action = KEYBINDS.get("v_eject")
            assert action is not None
            assert safety.check(step.command, action, KEYBINDS).ask


def test_setup_needs_its_own_keys() -> None:
    keybinds = Keybinds([_action("v_toggle_qdrive_engagement", "b")], MODES)
    r = _engage(FakeGameState(QUANTUM_ROUTE), keybinds)
    assert r.results[0].status == "no_key"
    assert "v_master_mode_set_nav" in r.results[0].detail
    assert r.keys.events == []


def test_confirming_never_unlocks_a_shared_dangerous_key() -> None:
    eject = next(c for c in CommandMatcher.default().commands if c.id == "eject")
    keybinds = Keybinds([_action("v_eject", "ralt+y"), _action("v_self_destruct", "ralt+y")])
    action = keybinds.get("v_eject")
    assert action is not None
    assert safety.check(eject, action, Keybinds([action])).ask
    assert safety.check(eject, action, Keybinds([action]), confirmed=True).allowed
    shared = safety.check(eject, action, keybinds, confirmed=True)
    assert not shared.allowed
    assert not shared.ask


# --- The voice (ADR 14) ------------------------------------------------------------------

_WITH_LIGHTS = Keybinds([*KEYBINDS, _action("v_lights", "l")], MODES)


def test_the_brains_reply_is_spoken_instead_of_the_done_sound() -> None:
    speaker = FakeSpeaker()
    r = _conversation(
        "what's a good mining ship", brain=FakeBrain(Reply("The Prospector.")), speaker=speaker
    )
    assert speaker.spoken == ["The Prospector."]
    assert r.feedback.played == []


def test_what_the_brain_did_is_spoken_after_the_press() -> None:
    speaker = FakeSpeaker()
    brain = FakeBrain(RunActions(("v_lights",), "Lights."))
    r = _conversation("make it dark in here", brain=brain, speaker=speaker, keybinds=_WITH_LIGHTS)
    assert r.results[0].pressed == ("v_lights",)
    assert speaker.spoken == ["Lights."]
    assert r.feedback.played == []


def test_tycho_doesnt_talk_over_what_the_game_announces() -> None:
    speaker = FakeSpeaker()
    brain = FakeBrain(RunActions(("v_toggle_landing_system",), "Gear down."))
    r = _conversation("put the wheels out would you", brain=brain, speaker=speaker)
    assert r.results[0].announced
    assert speaker.spoken == []
    assert r.feedback.played == ["done"]


def test_a_sequence_the_game_announces_throughout_is_quiet_too() -> None:
    speaker = FakeSpeaker()
    brain = FakeBrain(RunSequence("prepare_to_land", "Bringing us in."))
    r = _conversation("take us down to the pad", brain=brain, speaker=speaker)
    assert speaker.spoken == []
    assert r.feedback.played == ["done"]


def test_the_fast_path_keeps_its_sounds() -> None:
    speaker = FakeSpeaker()
    r = _conversation("landing gear", "landing gear", brain=FakeBrain(), speaker=speaker)
    assert speaker.spoken == []
    assert r.feedback.played == ["done", "done"]


def test_a_question_says_what_it_would_do_in_the_gates_words() -> None:
    speaker = FakeSpeaker()
    brain = FakeBrain(RunActions(("v_eject",), "Just tidying up."))  # a misleading line
    r = _conversation("sort it out", brain=brain, speaker=speaker)
    assert r.results[0].status == "asking"
    assert speaker.spoken == ["v_eject? Say confirm."]  # the action's own label, not the brain's
    assert r.feedback.played == []


def test_a_question_ends_with_what_the_player_is_called() -> None:
    """#15: confirmations of big actions use the player's name."""
    speaker = FakeSpeaker()
    _conversation("eject", speaker=speaker, address=lambda: "Ace")
    assert speaker.spoken == ["v_eject? Say confirm, Ace."]


def test_without_a_voice_a_question_is_the_tone() -> None:
    r = _conversation("eject")
    assert r.results[0].question == "v_eject? Say confirm."
    assert r.feedback.played == ["ask"]


def test_if_the_question_cant_be_spoken_the_tone_plays_instead() -> None:
    r = _conversation("eject", speaker=FakeSpeaker(fail="offline"))
    assert r.feedback.played == ["ask"]


def test_tychos_lines_are_only_logged_with_transcripts_on(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger="tycho.runtime.session")
    brain = FakeBrain(RunActions(("v_toggle_landing_system",), "Calling you Ace."))
    r = _conversation("put the wheels out would you", brain=brain, speaker=FakeSpeaker())
    assert "Calling you Ace" not in caplog.text
    assert "tycho_said" not in r.actions.entries[0]


def _console_only(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [r.getMessage() for r in caplog.records if getattr(r, "console_only", False)]


def test_with_no_voice_replies_are_shown_but_not_kept(caplog: pytest.LogCaptureFixture) -> None:
    # Without ElevenLabs the answer was thrown away (audit, 2026-11-03).
    caplog.set_level(logging.INFO, logger="tycho.runtime.session")
    r = _conversation("what's a good mining ship", brain=FakeBrain(Reply("The Prospector.")))
    assert r.results[0].status == "replied"
    assert _console_only(caplog) == ["Tycho: The Prospector."]


def test_with_no_voice_a_question_is_shown(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO, logger="tycho.runtime.session")
    _conversation("eject")
    assert len(_console_only(caplog)) == 1
    assert _console_only(caplog)[0].startswith("Tycho asks: ")


def test_with_no_voice_and_transcripts_kept_a_reply_is_logged_once(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger="tycho.runtime.session")
    _conversation(
        "what's a good mining ship", brain=FakeBrain(Reply("The Prospector.")), log_transcripts=True
    )
    assert _console_only(caplog) == []
    assert caplog.text.count("The Prospector.") == 1


def test_a_refused_pick_isnt_spoken_as_if_it_were_done() -> None:
    speaker = FakeSpeaker()
    brain = FakeBrain(RunActions(("v_lights",), "Lights."))
    r = _conversation("make it dark in here", brain=brain, speaker=speaker)  # no key for lights
    assert r.results[0].status == "no_key"
    assert speaker.spoken == []
    assert r.feedback.played == ["not_done"]


def test_when_speech_fails_tycho_plays_the_done_sound() -> None:
    speaker = FakeSpeaker(fail="out of ElevenLabs credits")
    r = _conversation(
        "what's a good mining ship", brain=FakeBrain(Reply("The Prospector.")), speaker=speaker
    )
    assert r.feedback.played == ["done"]


def test_push_to_talk_stops_tycho_talking() -> None:
    speaker = FakeSpeaker(hold=True)

    async def scenario() -> None:
        r = rig(
            "what's a good mining ship",
            "landing gear",
            brain=FakeBrain(Reply("The Prospector is the usual start.")),
            speaker=speaker,
        )
        r.say()
        await speaker.started.wait()  # idle() would wait for the line to end, and it never does
        assert speaker.spoken == ["The Prospector is the usual start."]
        r.session.handle(Edge.PRESSED)
        await asyncio.sleep(0)
        assert speaker.stopped == ["The Prospector is the usual start."]
        r.audio.hear(SPEECH)
        r.session.handle(Edge.RELEASED)
        await r.session.idle()
        assert r.results[1].status == "done"
        await r.session.close()

    run(scenario)
    assert speaker.closed


def test_stopping_waits_for_the_brains_warm_up_and_closes_it() -> None:
    """#60: the warm-up was cancelled but never awaited, and the brain never closed."""

    class SlowWarm(FakeBrain):
        cancelled = False

        async def warm(self, keybinds: Keybinds) -> None:
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                await asyncio.sleep(0.01)  # cleaning up after itself
                self.cancelled = True
                raise

    brain = SlowWarm()

    async def scenario() -> None:
        r = rig(brain=brain)
        stop = asyncio.Event()
        running = asyncio.create_task(r.session.run(stop))
        await asyncio.sleep(0.01)
        stop.set()
        await running

    run(scenario)
    assert brain.cancelled  # finished before run returned
    assert brain.closed


def test_the_brain_is_warmed_again_for_new_keybinds() -> None:
    """#60: after a rebind, the first request was about 3 s cold."""
    brain = FakeBrain()
    current = [KEYBINDS]

    async def scenario() -> None:
        r = rig(brain=brain, keybinds_now=lambda: current[0])
        stop = asyncio.Event()
        running = asyncio.create_task(r.session.run(stop))
        await asyncio.sleep(0.01)
        current[0] = _WITH_LIGHTS
        r.session.warm_brain()
        await asyncio.sleep(0.01)
        stop.set()
        await running
        r.session.warm_brain()  # after closing: nothing

    run(scenario)
    assert brain.warmed_with == [KEYBINDS, _WITH_LIGHTS]


def test_the_voice_connection_is_opened_while_the_brain_thinks() -> None:
    speaker = FakeSpeaker()
    _conversation(
        "what's a good mining ship", brain=FakeBrain(Reply("The Prospector.")), speaker=speaker
    )
    assert speaker.warmed == 1


def test_the_log_says_when_a_line_wasnt_spoken(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO, logger="tycho.runtime.session")
    brain = FakeBrain(RunActions(("v_toggle_landing_system",), "Gear down."))
    r = _conversation(
        "put the wheels out would you", brain=brain, speaker=FakeSpeaker(), log_transcripts=True
    )
    assert "Tycho (not spoken: the game announces it): Gear down." in caplog.text
    assert r.actions.entries[0]["spoken"] is False


def test_kept_transcripts_show_what_was_heard_before_correction(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger="tycho.runtime.session")
    r = _conversation("fly to micro tech", log_transcripts=True)
    result = r.results[0]
    assert result.text != result.heard  # the lexicon corrected "micro tech"
    assert f'"{result.text}" (heard "fly to micro tech")' in caplog.text


def test_a_web_search_gets_a_filler_line_and_the_answer_waits_for_it() -> None:
    speaker = FakeSpeaker(seconds=0.05)  # the filler is still playing when the answer comes
    brain = FakeBrain(Reply("4.11 is on the roadmap for Q4."), search=True)
    _conversation("whats in the next patch", brain=brain, speaker=speaker)
    filler = speaker.spoken[0]
    assert filler in FILLERS
    assert filler in speaker.kept  # kept as audio, so it starts at once next time
    assert speaker.spoken == [filler, "4.11 is on the roadmap for Q4."]
    assert speaker.stopped == []  # not cut off


def test_a_look_up_says_its_checking_and_the_answer_follows() -> None:
    """Play session: a price question was silent for 5 s, then answered."""
    speaker = FakeSpeaker(seconds=0.05)
    brain = FakeBrain(Reply("Rat's Nest pays the most."), Reply("Area 18."), look=True)
    _conversation("best place to sell gold", "and laranite", brain=brain, speaker=speaker)
    assert speaker.spoken == [CHECKING[0], "Rat's Nest pays the most.", CHECKING[1], "Area 18."]
    assert CHECKING[0] in speaker.kept  # kept as audio: the voice service is paid once


def test_the_filler_lines_take_turns() -> None:
    speaker = FakeSpeaker()
    brain = FakeBrain(Reply("One."), Reply("Two."), search=True)
    _conversation("whats new", "any news", brain=brain, speaker=speaker)
    assert speaker.spoken == [FILLERS[0], "One.", FILLERS[1], "Two."]


def test_a_web_search_gets_longer_than_the_usual_deadline() -> None:
    class SlowSearch(FakeBrain):
        async def decide(self, request: BrainRequest) -> Decision:
            # Says it's searching at once: a sleep first left 50 ms between that and
            # brain_seconds, which a slow runner could miss (audit, 2026-11-03).
            if request.waiting is not None:
                request.waiting()
            await asyncio.sleep(0.6)  # well past brain_seconds, well inside search_seconds
            return Reply("4.11 is on the roadmap.")

    speaker = FakeSpeaker()
    r = _conversation(
        "whats in the next patch",
        brain=SlowSearch(),
        speaker=speaker,
        brain_seconds=0.2,
        search_seconds=3.0,
    )
    assert r.results[0].status == "replied"
    assert speaker.spoken[-1] == "4.11 is on the roadmap."


class _SlowLookUp(FakeBrain):
    """A look-up starts at once, then Claude answers after ``after`` seconds."""

    def __init__(self, after: float) -> None:
        super().__init__()
        self._after = after

    async def decide(self, request: BrainRequest) -> Decision:
        if request.looking is not None:
            request.looking()
        await asyncio.sleep(self._after)
        return Reply("Rod's Fuel pays the most.")


def test_a_look_up_gets_longer_than_the_usual_deadline() -> None:
    """Play session (#150): two look-ups in a row ran past 8 s, after "Checking.",
    and ended in the error sound."""
    r = _conversation(
        "best place to get lindinium",
        brain=_SlowLookUp(0.6),
        speaker=FakeSpeaker(),
        brain_seconds=0.2,
        looking_seconds=3.0,
    )
    assert r.results[0].status == "replied"


def test_a_look_up_never_shortens_the_deadline() -> None:
    r = _conversation(
        "best place to get lindinium",
        brain=_SlowLookUp(0.3),
        speaker=FakeSpeaker(),
        brain_seconds=1.0,
        looking_seconds=0.05,  # shorter than what's left: the usual deadline holds
    )
    assert r.results[0].status == "replied"


def test_a_look_up_still_has_a_deadline() -> None:
    r = _conversation(
        "best place to get lindinium",
        brain=_SlowLookUp(5.0),
        speaker=FakeSpeaker(),
        brain_seconds=0.1,
        looking_seconds=0.2,
    )
    assert r.results[0].status == "failed"


class _TwoLookUps(FakeBrain):
    """A look-up at once, a second after ``gap``, then the answer after ``after``."""

    def __init__(self, gap: float, after: float, search_first: bool = False) -> None:
        super().__init__()
        self._gap, self._after, self._search_first = gap, after, search_first

    async def decide(self, request: BrainRequest) -> Decision:
        if self._search_first and request.waiting is not None:
            request.waiting()
        assert request.looking is not None
        request.looking()
        await asyncio.sleep(self._gap)
        request.looking()
        await asyncio.sleep(self._after)
        return Reply("Lindinium sells best at Orison.")


def test_a_second_look_up_moves_the_deadline_again_and_says_checking_once() -> None:
    """Review of #166: the play session's second look-up came 4.6 s in."""
    speaker = FakeSpeaker()
    r = _conversation(
        "where can I get lindinium and the rest",
        brain=_TwoLookUps(gap=0.3, after=0.3),
        speaker=speaker,
        brain_seconds=0.1,
        looking_seconds=0.4,  # 0.3 + 0.3 is past the first look-up's 0.4
    )
    assert r.results[0].status == "replied"
    assert sum(line in CHECKING for line in speaker.spoken) == 1


def test_look_ups_stop_at_the_limit() -> None:
    r = _conversation(
        "where can I get lindinium and the rest",
        brain=_TwoLookUps(gap=0.3, after=0.3),
        speaker=FakeSpeaker(),
        brain_seconds=0.1,
        looking_seconds=0.4,
        looking_limit=0.45,
    )
    assert r.results[0].status == "failed"


def test_a_look_up_never_shortens_a_web_searchs_deadline() -> None:
    r = _conversation(
        "whats new and where do I sell gold",
        brain=_TwoLookUps(gap=0.2, after=0.3, search_first=True),
        speaker=FakeSpeaker(),
        brain_seconds=0.1,
        search_seconds=2.0,
        looking_seconds=0.05,
    )
    assert r.results[0].status == "replied"


class _LookThenSearch(FakeBrain):
    """The real order (#169): a look-up, then a web search, then a slow answer."""

    async def decide(self, request: BrainRequest) -> Decision:
        assert request.looking is not None
        assert request.waiting is not None
        request.looking()
        await asyncio.sleep(0.05)
        request.waiting()
        await asyncio.sleep(1.0)
        return Reply("4.11 is on the roadmap.")


def test_a_search_after_look_ups_still_stops_at_the_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import tycho.runtime.session

    monkeypatch.setattr(tycho.runtime.session, "SEARCH_LIMIT", 0.5)
    r = _conversation(
        "whats new and where do I sell gold",
        brain=_LookThenSearch(),
        speaker=FakeSpeaker(),
        brain_seconds=0.1,
        search_seconds=3.0,  # would run to about 3 s without the limit
    )
    assert r.results[0].status == "failed"


def test_short_lines_are_kept_and_long_ones_arent() -> None:
    speaker = FakeSpeaker()
    long = "The Prospector, or the MOLE if you have a crew to fill it."
    brain = FakeBrain(Reply("The Prospector."), Reply(long))
    _conversation("best mining ship", "and for a group", brain=brain, speaker=speaker)
    assert speaker.spoken == ["The Prospector.", long]
    assert speaker.kept == ["The Prospector."]
    assert len(long) > KEEP_CHARACTERS


# --- A player's confirm and requires on a sequence (audit, 2026-11-03) -------------------


def _careful(tmp_path: Path, extra: str) -> CommandMatcher:
    file = tmp_path / "commands.toml"
    file.write_text(
        '[[sequence]]\nid = "careful_landing"\nsay = ["careful landing"]\n'
        f'{extra}\nsteps = ["landing_gear", "request_landing"]\n',
        "utf-8",
    )
    return CommandMatcher.default(file)


def test_a_confirm_sequence_asks_once_then_runs_every_step(tmp_path: Path) -> None:
    r = _conversation("careful landing", "confirm", matcher=_careful(tmp_path, "confirm = true"))
    assert [x.status for x in r.results] == ["asking", "done"]
    assert r.results[0].pressed == ()
    assert r.results[1].pressed == ("v_toggle_landing_system", "v_atc_request")


def test_a_sequence_that_needs_a_fact_presses_nothing_without_it(tmp_path: Path) -> None:
    matcher = _careful(tmp_path, 'requires = ["quantum_route_set"]')
    r = _conversation("careful landing", matcher=matcher, game_state=FakeGameState())
    assert r.results[0].status == "refused"
    assert r.keys.events == []


def test_a_failed_warm_up_goes_to_tychos_log(caplog: pytest.LogCaptureFixture) -> None:
    # Not asyncio's "Task exception was never retrieved" on stderr (audit, 2026-11-03).
    from tycho.runtime import session

    async def fails() -> None:
        raise RuntimeError("no network")

    async def scenario() -> None:
        task = asyncio.create_task(fails())
        task.add_done_callback(session._log_failure)  # pyright: ignore[reportPrivateUsage]
        await asyncio.gather(task, return_exceptions=True)

    caplog.set_level(logging.ERROR, logger="tycho.runtime.session")
    asyncio.run(scenario())
    assert "Warming up failed: no network" in caplog.text


# --- A chain stops at the first press that doesn't happen (audit, 2026-11-03) ------------


def test_a_chain_stops_when_the_game_loses_focus_part_way() -> None:
    r = _conversation("prepare for landing", focus=FakeFocus(True, False))
    result = r.results[0]
    assert result.status == "no_focus"
    assert result.pressed == ("v_atc_request",)  # the second step never went
    assert r.keys.held() == set()


def test_a_chain_stops_when_windows_refuses_a_press_part_way() -> None:
    async def scenario() -> Rig:
        r = rig("prepare for landing")
        r.keys.fail_on = 4  # Left Alt+N went down and up; the second step's N fails
        r.say()
        await r.session.idle()
        await r.session.close()
        return r

    r = run(scenario)
    result = r.results[0]
    assert result.status == "failed"
    assert result.pressed == ("v_atc_request",)
    assert r.keys.held() == set()


# --- The Tile (ADR 16) -------------------------------------------------------------

_SELL = Card("list", title="Best places to sell", rows=(Row("Everus Harbor", value="3,048"),))


def test_the_talk_light_follows_push_to_talk() -> None:
    display = FakeDisplay()
    _conversation("landing gear", display=display)
    assert display.calls[:3] == ["listening", "thinking", "idle"]
    assert display.light is None


def test_a_reply_with_a_card_puts_it_on_the_tile_and_says_the_line() -> None:
    display, speaker = FakeDisplay(), FakeSpeaker()
    brain = FakeBrain(Reply("Best is Everus Harbor. More on the Tile.", _SELL))
    r = _conversation("where to sell laranite", display=display, brain=brain, speaker=speaker)
    assert r.results[0].card == _SELL
    assert display.shown == [_SELL]
    assert speaker.spoken == ["Best is Everus Harbor. More on the Tile."]


def test_a_question_shows_its_countdown_and_answering_takes_it_down() -> None:
    display = FakeDisplay()
    _conversation("eject", "confirm", display=display, address=lambda: "Ace")
    asks = [c for c in display.calls if c.startswith("ask")]
    assert asks == ["ask v_eject? Say confirm, Ace. (5 s)"]
    assert display.calls.index("answered") > display.calls.index(asks[0])


def test_keep_that_up_pins_the_card_and_clear_takes_it_down() -> None:
    display = FakeDisplay()
    brain = FakeBrain(Reply("Best is Everus Harbor.", _SELL))
    r = _conversation(
        "where to sell laranite", "keep that up", "clear", display=display, brain=brain
    )
    assert [x.status for x in r.results] == ["replied", "done", "done"]
    assert [x.commands for x in r.results[1:]] == [("tile_pin",), ("tile_clear",)]
    assert display.board.view() == ()
    assert r.feedback.played[-2:] == ["done", "done"]


def test_nothing_to_keep_is_not_done() -> None:
    r = _conversation("keep that up", display=FakeDisplay())
    assert r.results[0].status == "not_understood"
    assert r.feedback.played == ["not_done"]


def test_show_me_that_puts_the_last_answer_up_to_read() -> None:
    display = FakeDisplay()
    brain = FakeBrain(Reply("Quantum fuel is a tank of its own; a jump uses it by distance."))
    _conversation("how does quantum fuel work", "show me that", display=display, brain=brain)
    assert [c.kind for c in display.shown] == ["long"]
    assert display.shown[0].body == (
        "Quantum fuel is a tank of its own; a jump uses it by distance.",
    )
    assert display.board.view()[0].pinned


def test_show_me_that_brings_back_the_last_card_kept() -> None:
    display = FakeDisplay()
    brain = FakeBrain(Reply("Best is Everus Harbor.", _SELL))
    _conversation("where to sell laranite", "clear", "show me that", display=display, brain=brain)
    assert display.calls[-2] == "show list pinned"


def test_with_the_tile_off_its_words_go_to_the_brain_as_usual() -> None:
    brain = FakeBrain(Reply("The Tile is off."))
    r = _conversation("keep that up", display=FakeDisplay(on=False), brain=brain)
    assert r.results[0].status == "replied"
    assert [x.text for x in brain.asked] == ["keep that up"]


def test_clear_the_target_is_still_the_games() -> None:
    display = FakeDisplay()
    r = _conversation("clear the target", display=display)
    assert "clear" not in display.calls
    assert r.results[0].commands != ("tile_clear",)


def test_a_barge_in_leaves_the_light_on_for_the_new_request() -> None:
    hold = threading.Event()
    display = FakeDisplay()

    async def scenario() -> None:
        r = rig("where to sell laranite", "landing gear", hold=hold, display=display)
        r.say()
        await asyncio.sleep(0.05)
        r.session.handle(Edge.PRESSED)  # talking again before the first is done
        await asyncio.sleep(0.05)
        assert display.light == "listening"
        hold.set()
        r.audio.hear(SPEECH)
        r.session.handle(Edge.RELEASED)
        await r.session.idle()
        await r.session.close()

    run(scenario)


# --- Pausing from the tray (ADR 17) -------------------------------------------------


def test_paused_push_to_talk_does_nothing() -> None:
    async def scenario() -> Rig:
        r = rig("landing gear")
        r.session.pause(True)
        r.say()
        await r.session.idle()
        assert r.session.paused
        await r.session.close()
        return r

    r = run(scenario)
    assert r.results == []
    assert r.keys.events == []


def test_resuming_listens_again() -> None:
    async def scenario() -> Rig:
        r = rig("landing gear")
        r.session.pause(True)
        r.session.pause(False)
        r.say()
        await r.session.idle()
        await r.session.close()
        return r

    assert [x.status for x in run(scenario).results] == ["done"]


def test_a_press_held_when_pausing_still_ends_normally() -> None:
    async def scenario() -> Rig:
        r = rig("landing gear")
        r.session.handle(Edge.PRESSED)
        r.session.pause(True)  # paused while the button is down
        r.audio.hear(SPEECH)
        r.session.handle(Edge.RELEASED)
        await r.session.idle()
        await r.session.close()
        return r

    r = run(scenario)
    assert [x.status for x in r.results] == ["done"]


def test_paused_push_to_talk_still_stops_what_is_running() -> None:
    hold = threading.Event()

    async def scenario() -> Rig:
        r = rig("landing gear", hold=hold)
        r.say()
        await asyncio.sleep(0.05)
        r.session.pause(True)
        r.session.handle(Edge.PRESSED)  # "stop": as push-to-talk always is
        hold.set()
        await r.session.idle()
        await r.session.close()
        return r

    r = run(scenario)
    assert [x.status for x in r.results] == ["cancelled"]
    assert r.keys.events == []


def test_pausing_mid_request_leaves_the_light_until_it_ends() -> None:
    display = FakeDisplay()

    async def scenario() -> None:
        r = rig("landing gear", display=display)
        r.session.handle(Edge.PRESSED)
        r.session.pause(True)
        assert display.light == "listening"  # still recording
        r.audio.hear(SPEECH)
        r.session.handle(Edge.RELEASED)
        await r.session.idle()
        assert display.light is None  # the request ended, and with it the light
        await r.session.close()

    run(scenario)
