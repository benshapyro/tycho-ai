"""The whole talking loop with fake hardware: real lexicon, matcher, gate and presser (ADR 11)."""

import asyncio
import logging
import threading
import time
from collections.abc import Callable, Coroutine
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pytest

from tests.fakes.fake_audio import FakeAudioSource
from tests.fakes.fake_input import FakeInputSource
from tests.fakes.fake_keys import FakeFocus, FakeKeySender
from tests.fakes.fake_runtime import FakeFeedback, FakeTranscriber
from tycho import safety
from tycho.commands import CommandMatcher
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


def _action(name: str, keys: str, activation: str = "") -> Action:
    key = KeyInput.parse(keys)
    assert key is not None
    return Action(name=name, actionmap="m", label=name, inputs=(key,), activation=activation)


KEYBINDS = Keybinds(
    [
        _action("v_toggle_landing_system", "n"),
        _action("v_atc_request", "lalt+n"),
        _action("v_eject", "ralt+y"),
        _action("v_toggle_qdrive_engagement", "b"),
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
) -> Rig:
    audio, keys, feedback = FakeAudioSource(RATE), FakeKeySender(), FakeFeedback()
    transcriber = FakeTranscriber(*lines, hold=hold)
    results: list[Result] = []
    session = Session(
        audio=audio,
        push_to_talk=PushToTalk(PTT, sources or []),
        transcriber=transcriber,
        lexicon=_LEXICON,
        matcher=_MATCHER,
        keybinds=lambda: keybinds,
        presser=Presser(keys, focus or FakeFocus(True)),
        feedback=feedback,
        samplerate=RATE,
        log_transcripts=log_transcripts,
        on_result=results.append,
        poll_interval=0.001,
    )
    return Rig(session, audio, keys, feedback, transcriber, results)


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


# --- Requests that must not press anything ----------------------------------------------


@pytest.mark.parametrize(
    ("heard", "status"),
    [
        ("eject", "refused"),  # confirm-grade: refused until spoken confirmation (0.2)
        ("engage quantum", "refused"),
        ("gear down and eject", "refused"),  # one refused part stops the whole chain
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


def test_only_one_tycho_runs_at_a_time() -> None:
    name = rf"Local\tycho-ai-test-{time.monotonic_ns()}"
    with SingleInstance(name), pytest.raises(AlreadyRunning), SingleInstance(name):
        pass
    with SingleInstance(name):  # released on exit, so it can be taken again
        pass
