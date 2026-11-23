"""Looking at the game window when the player asks (ADR 14's amendment, #31).

Off until the player says yes; only when they ask ("take a look", or "yes" to the
brain's offer); the game window only, with a camera sound each time; the picture goes
to the brain with that request and nowhere else; and a look never presses a key.
"""

import asyncio
import io
import json
import re
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from PIL import Image

from tests.fakes.fake_brain import FakeBrain
from tests.fakes.fake_pack import REGISTRY
from tests.fakes.fake_runtime import FakeCamera
from tests.fakes.fake_setup_pc import FakeSetupPC
from tests.fakes.fake_speaker import FakeSpeaker
from tests.test_packs import _copy, _edit  # pyright: ignore[reportPrivateUsage]
from tests.test_runtime import SC, Rig, rig, run
from tycho import settings
from tycho.brain import BrainRequest, Exchange, Reply, RunActions
from tycho.brain.claude import history
from tycho.brain.looking import LOOK_OFFER, MAY_OFFER, SEEN, openai_turns
from tycho.brain.rounds import Turn, traced
from tycho.brain.traces import TraceFile
from tycho.desktop import setup
from tycho.desktop.bridge import Bridge
from tycho.desktop.bridge.settings_page import GROUPS
from tycho.eyes import LONGEST, MOST_PIXELS, CaptureError, Picture, shrink
from tycho.feedback import SoundFeedback, camera_sound
from tycho.keybinds import Keybinds
from tycho.packs import PackError, built_in, load_pack
from tycho.runtime.conversation import would_speak
from tycho.runtime.look import NOT_SEEN, OFF, Eyes, asks_to_look, only_a_look, says_yes
from tycho.runtime.messages import Result
from tycho.runtime.routing import NotUnderstood, Picked, ToBrain, route
from tycho.settings import Settings
from tycho.spoken_settings import DESCRIPTION, Refused, check_rule, parse

PICTURE = Picture(b"\xff\xd8 a jpeg", 160, 90)


def _eyes(camera: FakeCamera | None = None) -> Eyes:
    return Eyes(SC.look, camera or FakeCamera(PICTURE))


def _talk(*lines: str, **options: object) -> Rig:
    """Each line on its own push-to-talk, waiting for each to finish."""

    async def scenario() -> Rig:
        r = rig(*lines, **options)  # type: ignore[arg-type]
        for _ in lines:
            r.say()
            await r.session.idle()
        await r.session.close()
        return r

    return run(scenario)


# --- The setting: off until asked, and only ever off by voice ------------------------------


def test_looking_is_off_until_the_player_answers_and_only_turns_off_by_voice() -> None:
    assert Settings().look_at_screen is None
    rule = settings.rule("look_at_screen")
    assert (rule["voice"], rule["applies"]) == ("lower_only", "now")
    on = parse("look_at_screen", "on", None).value
    with pytest.raises(Refused):
        check_rule("look_at_screen", None, on)  # never asked: on needs the app
    with pytest.raises(Refused):
        check_rule("look_at_screen", False, on)
    check_rule("look_at_screen", True, parse("look_at_screen", "off", True).value)
    assert "stop looking at my screen" in DESCRIPTION


def test_star_citizen_can_look_and_world_of_warcraft_can_not() -> None:
    assert SC.window == "StarCitizen.exe"
    assert "take a look" in SC.look
    assert built_in("wow-forever").look == ()
    assert setup.can_look("star-citizen")
    assert not setup.can_look("wow-forever")


def test_a_pack_with_look_phrases_needs_a_window(tmp_path: Path) -> None:
    folder = _copy(tmp_path)
    _edit(
        folder,
        "pack.toml",
        'window = "FakeGame.exe"\n',
        'window = "FakeGame.exe"\nlook = ["peek"]\n',
    )
    assert load_pack(folder, built_in=False, registry=REGISTRY).look == ("peek",)
    _edit(folder, "pack.toml", 'mode = "keys"', 'mode = "advisor"\nwhy = "No keys here."')
    _edit(folder, "pack.toml", 'window = "FakeGame.exe"\n', "")
    (folder / "commands.toml").unlink()
    _edit(folder, "pack.toml", 'keybinds = "fake-game"\n', "")
    with pytest.raises(PackError, match=re.escape("look needs a window")):
        load_pack(folder, built_in=False, registry=REGISTRY)


# --- What asks for a look: the code decides, never the model ---------------------------------


@pytest.mark.parametrize(
    "said",
    [
        "take a look",
        "Take a look, is this contract worth it?",
        "what am I looking at",
        "What's on my screen?",
        "have a look at this ship",
    ],
)
def test_the_packs_phrases_ask_for_a_look_anywhere_in_what_was_said(said: str) -> None:
    assert asks_to_look(said, SC.look)


@pytest.mark.parametrize(
    "said",
    [
        "don't look at my screen",
        "stop looking at my screen",
        "never look at my screen",
        "turn off looking at my screen",
        "look up the price of gold",
        "lookout",
        "is this contract worth it",
    ],
)
def test_other_words_and_saying_not_to_never_ask_for_a_look(said: str) -> None:
    assert not asks_to_look(said, SC.look)


@pytest.mark.parametrize(
    "said",
    [
        "take a look, is my shield off?",
        "take a look, is this not a good deal?",
        "Is it not worth it? Take a look.",
        "have a look without the HUD clutter",
    ],
)
def test_a_not_or_off_away_from_the_look_phrase_doesnt_cancel_it(said: str) -> None:
    """#554: only a negation just before the phrase, in the same clause, turns it around."""
    assert asks_to_look(said, SC.look)


def test_saying_not_to_look_in_more_words_still_isnt_a_look() -> None:
    assert not asks_to_look("I don't want you to look at my screen", SC.look)
    assert not asks_to_look("please never take a look", SC.look)


def test_only_a_plain_yes_answers_the_offer() -> None:
    assert says_yes("Yes")
    assert says_yes("yeah go ahead") is False  # a whole utterance, from the list
    assert says_yes("go ahead")
    assert not says_yes("yes, lower the gear")


def test_a_look_goes_to_the_brain_before_the_fast_path_could_press() -> None:
    """ "take a look" with a command in it never presses: it's a question for the brain."""
    from tests.test_runtime import _MATCHER  # pyright: ignore[reportPrivateUsage]

    assert isinstance(route("landing gear", _MATCHER), Picked)
    looked = route("landing gear, take a look", _MATCHER, look=True)
    assert looked == ToBrain("asked to look", look=True)
    assert isinstance(route("take a look", _MATCHER, look=True, brain=False), NotUnderstood)


# --- In the session --------------------------------------------------------------------------


def test_asked_to_look_with_it_on_takes_one_picture_and_sends_it_with_the_question() -> None:
    camera = FakeCamera(PICTURE)
    brain = FakeBrain(Reply("That's a Cutlass Black."))
    speaker = FakeSpeaker()
    r = _talk(
        "take a look, what ship is that",
        brain=brain,
        eyes=_eyes(camera),
        looking=True,
        speaker=speaker,
    )
    assert camera.taken == 1
    assert r.feedback.played[0] == "camera"  # heard as it's taken, before the answer
    (asked,) = brain.asked
    assert asked.picture is PICTURE
    result = r.results[0]
    assert (result.status, result.commands, result.say) == (
        "replied",
        ("look",),
        "That's a Cutlass Black.",
    )
    assert speaker.spoken == ["That's a Cutlass Black."]
    assert r.actions.entries[0]["commands"] == ["look"]  # logged as a look, never the picture
    assert "jpeg" not in json.dumps(r.actions.entries, default=str)


def test_asked_to_look_with_it_off_says_so_and_takes_and_sends_nothing() -> None:
    camera = FakeCamera(PICTURE)
    brain = FakeBrain()
    speaker = FakeSpeaker()
    r = _talk("take a look", brain=brain, eyes=_eyes(camera), looking=False, speaker=speaker)
    assert camera.taken == 0
    assert brain.asked == []
    assert "camera" not in r.feedback.played
    result = r.results[0]
    assert (result.status, result.commands) == ("refused", ("look",))
    assert speaker.spoken == [OFF]
    assert "Settings, Privacy" in OFF


@pytest.mark.parametrize(
    "said",
    [
        "can you take a look at what Laranite sells for in Area 18?",
        "have a look at my fleet, which ship has the most cargo?",
    ],
)
def test_with_looking_off_a_question_that_says_take_a_look_is_still_answered(said: str) -> None:
    camera = FakeCamera(PICTURE)
    brain = FakeBrain(Reply("About 27 aUEC a unit."))
    speaker = FakeSpeaker()
    r = _talk(said, brain=brain, eyes=_eyes(camera), looking=False, speaker=speaker)
    assert camera.taken == 0
    (asked,) = brain.asked
    assert asked.picture is None
    assert asked.text.lower().startswith(said.lower()[:12])
    assert r.results[0].commands == ()
    assert speaker.spoken == ["About 27 aUEC a unit."]


def test_with_the_game_window_gone_a_question_that_says_take_a_look_is_still_answered() -> None:
    camera = FakeCamera(CaptureError("no StarCitizen.exe window is open"))
    brain = FakeBrain(Reply("About 27 aUEC a unit."))
    said = "take a look at what Laranite sells for in Area 18"
    r = _talk(said, brain=brain, eyes=_eyes(camera), looking=True)
    (asked,) = brain.asked
    assert asked.picture is None
    assert r.results[0].status == "replied"


@pytest.mark.parametrize(
    ("said", "only"),
    [
        ("take a look", True),
        ("Tycho, take a look please", True),
        ("can you have a look", True),
        ("yes", True),
        ("what am I looking at?", True),
        ("take a look at the price of Laranite", False),
        ("have a look at my fleet", False),
    ],
)
def test_only_a_request_that_is_all_a_look_counts_as_only_a_look(said: str, only: bool) -> None:
    assert only_a_look(said, SC.look) is only


def test_asked_only_to_look_with_it_off_hears_how_to_turn_it_on() -> None:
    brain = FakeBrain()
    speaker = FakeSpeaker()
    _talk("Tycho, take a look please", brain=brain, eyes=_eyes(), looking=False, speaker=speaker)
    assert brain.asked == []
    assert speaker.spoken == [OFF]


def test_without_the_game_window_nothing_is_sent_and_the_player_hears_why() -> None:
    camera = FakeCamera(CaptureError("no StarCitizen.exe window is open"))
    brain = FakeBrain()
    speaker = FakeSpeaker()
    r = _talk("take a look", brain=brain, eyes=_eyes(camera), looking=True, speaker=speaker)
    assert brain.asked == []
    assert "camera" not in r.feedback.played  # nothing was taken
    assert r.results[0].status == "failed"
    assert speaker.spoken == [NOT_SEEN]


def test_a_look_never_presses_even_if_the_brain_picks_keys() -> None:
    brain = FakeBrain(RunActions(("v_toggle_landing_system",), "Gear down."))
    r = _talk("take a look and put the gear down", brain=brain, eyes=_eyes(), looking=True)
    assert r.pressed() == []
    assert r.results[0].status == "refused"
    assert "didn't press anything" in r.results[0].say


def test_a_picture_is_never_kept_for_the_next_request() -> None:
    brain = FakeBrain(Reply("A Cutlass."), Reply("About 1.2 million aUEC."))
    _talk("take a look", "how much is it", brain=brain, eyes=_eyes(), looking=True)
    first, second = brain.asked
    assert first.picture is PICTURE
    assert second.picture is None
    assert second.recent[0].said == "take a look"  # the words follow up, not the picture


def test_yes_to_the_brains_offer_is_a_look_and_only_soon_after_it() -> None:
    now = [100.0]
    offer = f"I can't tell from here. {LOOK_OFFER}"
    brain = FakeBrain(Reply(offer), Reply("Worth it: 40k for a short hop."), Reply("Hello."))
    camera = FakeCamera(PICTURE)

    async def scenario() -> Rig:
        r = rig(
            "is this contract worth it",
            "yes",
            "yes",
            brain=brain,
            eyes=_eyes(camera),
            looking=True,
            clock=lambda: now[0],
        )
        for wait in (0, 0, 31):
            now[0] += wait
            r.say()
            await r.session.idle()
        await r.session.close()
        return r

    r = run(scenario)
    asked = brain.asked
    assert asked[0].picture is None
    assert asked[0].may_look
    assert asked[1].picture is PICTURE  # "yes" just after the offer
    assert camera.taken == 1
    assert asked[2].picture is None  # half a minute later, "yes" is only a word
    assert r.results[1].commands == ("look",)


def test_yes_after_another_request_doesnt_answer_the_offer() -> None:
    """#553: "landing gear" (silent, fast path) in between, then "ok" within the
    follow-up window: no picture, though the offer is still the last spoken line."""
    now = [100.0]
    brain = FakeBrain(Reply(f"I can't tell from here. {LOOK_OFFER}"), Reply("Okay."))
    camera = FakeCamera(PICTURE)

    async def scenario() -> Rig:
        r = rig(
            "is this contract worth it",
            "landing gear",
            "ok",
            brain=brain,
            eyes=_eyes(camera),
            looking=True,
            clock=lambda: now[0],
        )
        for wait in (0, 10, 15):
            now[0] += wait
            r.say()
            await r.session.idle()
        await r.session.close()
        return r

    r = run(scenario)
    assert r.results[1].commands != ("look",)
    assert camera.taken == 0
    assert all(asked.picture is None for asked in brain.asked)


def test_yes_more_than_30_s_after_the_offer_isnt_a_look_even_after_a_follow_up() -> None:
    from tycho.runtime.conversation import Conversation

    now = [0.0]
    talk = Conversation(clock=lambda: now[0])
    talk.heard(Result(1, "replied", text="is it worth it", say=f"Hm. {LOOK_OFFER}", by_brain=True))
    now[0] += 20
    assert talk.offered_look()
    now[0] += 11
    assert not talk.offered_look()


@pytest.mark.parametrize(
    "error", [ImportError("winrt"), AttributeError("frame"), ValueError("buffer"), RuntimeError()]
)
def test_any_capture_error_says_it_cant_see_the_game_window(error: Exception) -> None:
    """#552: not only CaptureError: the player always hears why, and nothing is sent."""
    brain = FakeBrain()
    speaker = FakeSpeaker()
    r = _talk(
        "take a look", brain=brain, eyes=_eyes(FakeCamera(error)), looking=True, speaker=speaker
    )
    assert brain.asked == []
    assert r.results[0].status == "failed"
    assert speaker.spoken == [NOT_SEEN]


def test_the_brain_may_offer_a_look_only_with_looking_on_in_a_game_that_can() -> None:
    for eyes, looking, may in ((_eyes(), True, True), (_eyes(), False, False), (None, True, False)):
        brain = FakeBrain(Reply("Hi."))
        _talk("what is a cutlass", brain=brain, eyes=eyes, looking=looking)
        assert brain.asked[0].may_look is may


def test_turning_looking_on_in_the_app_applies_to_the_next_request() -> None:
    camera = FakeCamera(PICTURE)
    brain = FakeBrain(Reply("Seen."))

    async def scenario() -> Rig:
        r = rig("take a look", "take a look", brain=brain, eyes=_eyes(camera), looking=False)
        r.say()
        await r.session.idle()
        r.session.set_looking(True)
        r.say()
        await r.session.idle()
        await r.session.close()
        return r

    r = run(scenario)
    assert [x.status for x in r.results] == ["refused", "replied"]
    assert camera.taken == 1


def test_tycho_s_own_lines_about_a_look_are_spoken() -> None:
    off = Result(1, "refused", commands=("look",), say=OFF)
    assert would_speak(off)
    assert not would_speak(Result(1, "refused", commands=("landing_gear",), say="x"))


# --- What the brain is sent -------------------------------------------------------------------


def test_the_picture_goes_in_this_request_s_turn_only_with_what_to_make_of_it() -> None:
    import datetime

    day = datetime.date(2026, 11, 22)
    earlier = [Exchange("hi", "said: Hello.")]
    *_, turn = history(earlier, "take a look", day, picture=PICTURE)
    content: Any = turn["content"]
    assert [part["type"] for part in content] == ["text", "image", "text"]  # header, picture, words
    assert SEEN in content[0]["text"]
    assert content[1]["source"]["media_type"] == "image/jpeg"
    (offered,) = history([], "is this worth it", day, may_look=True)
    offer: Any = offered["content"]
    assert MAY_OFFER in offer[0]["text"]
    (plain,) = history([], "is this worth it", day)
    assert "look" not in json.dumps(plain).lower()


def test_openrouter_gets_the_picture_as_a_data_url() -> None:
    import datetime

    turns = openai_turns(history([], "take a look", datetime.date(2026, 11, 22), picture=PICTURE))
    parts = turns[0]["content"]
    image = parts[1]
    assert image["type"] == "image_url"
    assert image["image_url"]["url"].startswith("data:image/jpeg;base64,")
    assert parts[0]["type"] == parts[2]["type"] == "text"


def test_a_trace_records_the_pictures_size_never_the_picture(tmp_path: Path) -> None:
    traces = TraceFile(tmp_path / "traces.jsonl")
    request = BrainRequest("take a look", Keybinds([]), picture=PICTURE)
    turn = Turn.of(request)
    turn.trace = traces.start("anthropic", "haiku", "briefing", request.text, 0)

    async def decide() -> Reply:
        return Reply("A Cutlass.")

    asyncio.run(traced(traces, turn, decide()))
    traces.close()
    line = json.loads((tmp_path / "traces.jsonl").read_text("utf-8"))
    assert line["picture"] == PICTURE.size
    assert "jpeg" not in json.dumps(line)


def test_a_picture_s_bytes_never_reach_a_log_line() -> None:
    assert repr(PICTURE.data[:8])[2:-1] not in repr(PICTURE)
    assert "data=" not in repr(PICTURE)
    assert "Picture" not in repr(BrainRequest("x", Keybinds([]), picture=PICTURE))


# --- The picture itself, and its sound ---------------------------------------------------------


def test_a_big_frame_is_made_small_enough_to_send_and_a_small_one_is_kept() -> None:
    big = shrink(Image.new("RGB", (3840, 2160), "navy"))
    assert max(big.width, big.height) <= LONGEST
    assert big.width * big.height <= MOST_PIXELS
    assert abs(big.width / big.height - 16 / 9) < 0.01
    assert Image.open(io.BytesIO(big.data)).format == "JPEG"
    small = shrink(Image.new("RGBA", (800, 600), "teal"))
    assert (small.width, small.height) == (800, 600)


def test_a_window_with_no_size_gives_no_picture() -> None:
    with pytest.raises(CaptureError):
        shrink(Image.new("RGB", (0, 0)))


class _Player:
    def __init__(self) -> None:
        self.played: list[float] = []

    def play(self, samples: np.ndarray[tuple[int], np.dtype[np.float32]], rate: int) -> None:
        self.played.append(float(np.max(np.abs(samples))))


def test_the_camera_sound_plays_even_with_the_sounds_turned_off() -> None:
    assert 0.08 < len(camera_sound()) / 48_000 < 0.15
    player = _Player()
    feedback = SoundFeedback(player, volume=0.0)
    feedback.done()  # silent, as asked
    feedback.camera()  # never silent (ADR 14)
    assert len(player.played) == 1
    assert player.played[0] > 0.1


# --- Setup and the window ----------------------------------------------------------------------


def test_players_set_up_before_looking_are_asked_once_in_a_game_that_can_look() -> None:
    assert setup.unasked(Settings(crash_reports=False)) == ("look_at_screen",)
    assert setup.unasked(Settings(crash_reports=False, look_at_screen=False)) == ()
    assert setup.unasked(Settings(crash_reports=False, game="wow-forever")) == ()
    assert setup.unasked(Settings()) == ("crash_reports", "look_at_screen")


def test_the_privacy_step_lists_the_picture_only_where_tycho_can_look(tmp_path: Path) -> None:
    path = tmp_path / "settings.toml"
    settings.save(Settings(look_at_screen=True), path)
    state = Bridge(settings_path=path, pc=FakeSetupPC()).setup_state()
    assert state["can_look"] is True
    assert state["look_at_screen"] is True
    leaves: Any = state["leaves"]
    rows = [r for r in leaves if "picture" in r["what"]]
    assert len(rows) == 1
    assert "On now" in rows[0]["when"]
    settings.save(Settings(game="wow-forever"), path)
    state = Bridge(settings_path=path, pc=FakeSetupPC()).setup_state()
    assert state["can_look"] is False
    leaves = state["leaves"]
    assert not [r for r in leaves if "picture" in r["what"]]


def test_looking_is_a_privacy_setting_shown_only_for_a_game_that_can_look(tmp_path: Path) -> None:
    assert "look_at_screen" in GROUPS["Privacy"]
    path = tmp_path / "settings.toml"
    for game, shown in (("star-citizen", True), ("wow-forever", False)):
        settings.save(Settings(game=game), path)  # type: ignore[arg-type]
        groups: Any = Bridge(settings_path=path).settings()["groups"]
        names = [r["name"] for g in groups for r in g["rows"]]
        assert ("look_at_screen" in names) is shown


def test_the_app_turns_looking_on_in_the_running_tycho_at_once(tmp_path: Path) -> None:
    path = tmp_path / "settings.toml"
    settings.save(Settings(), path)
    applied: list[tuple[str, object]] = []
    bridge = Bridge(settings_path=path, apply=lambda n, v: applied.append((n, v)) is None)
    answer = bridge.set("look_at_screen", True)
    assert answer["applies"] == "now"
    assert applied == [("look_at_screen", True)]
    assert settings.load(path).look_at_screen is True
