"""The whole talking loop with fake hardware: real lexicon, matcher, gate and presser (ADR 11)."""

import asyncio
import dataclasses
import logging
import threading
import time
from collections.abc import Callable, Coroutine
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pytest

from tests.fakes.fake_audio import FakeAudioSource
from tests.fakes.fake_brain import FakeBrain
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
from tycho.brain import BrainError, Reply, RunActions, RunSequence
from tycho.commands import CommandMatcher
from tycho.game.gamelog import QUANTUM_ROUTE
from tycho.game.keybinds import Action, ActivationMode, Keybinds, KeyInput
from tycho.input import Presser, code_for
from tycho.lexicon import Lexicon
from tycho.ptt.binding import Binding
from tycho.ptt.monitor import Edge, PushToTalk
from tycho.runtime import AlreadyRunning, Result, Session, SingleInstance

RATE = 16_000
PTT = Binding.parse("mouse:0x06")
SPEECH = np.zeros(RATE // 2, dtype=np.float32)  # half a second; the transcriber is scripted

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
    speaker: FakeSpeaker | None = None,
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
        matcher=_MATCHER,
        keybinds=keybinds_now or (lambda: keybinds),
        presser=Presser(keys, focus or FakeFocus(True)),
        feedback=feedback,
        game_state=game_state,
        action_log=actions,
        brain=brain,
        brain_seconds=brain_seconds,
        speaker=speaker,
        samplerate=RATE,
        log_transcripts=log_transcripts,
        on_result=results.append,
        poll_interval=0.001,
        clock=clock,
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
    r = _conversation("put the wheels out would you", brain=brain)
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
    assert stopped < 0.5  # not the full 1.6 s hold
    assert r.keys.held() == set()


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
    r = _conversation("landing gear", "eject", brain=FakeBrain(), speaker=speaker)
    assert speaker.spoken == []
    assert r.feedback.played == ["done", "ask"]


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


def test_the_voice_connection_is_opened_while_the_brain_thinks() -> None:
    speaker = FakeSpeaker()
    _conversation(
        "what's a good mining ship", brain=FakeBrain(Reply("The Prospector.")), speaker=speaker
    )
    assert speaker.warmed == 1


def test_the_log_says_when_a_line_wasnt_spoken(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO, logger="tycho.runtime.session")
    brain = FakeBrain(RunActions(("v_toggle_landing_system",), "Gear down."))
    r = _conversation("put the wheels out would you", brain=brain, speaker=FakeSpeaker())
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
