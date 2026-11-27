"""Words that name an action without asking for it are never pressed (brain/unasked.py).

From the trick phrasings eval, 27 Nov 2026: both adapters pressed for 10 to 12 of 70."""

import pytest

from tycho.brain.briefing import offered_actions, spoken
from tycho.brain.decisions import Exchange
from tycho.brain.heard import Heard
from tycho.brain.refusals import ANSWER_IN_WORDS
from tycho.brain.unasked import HOW_THINGS_ARE, NOT_NOW, SOMEONE_ELSE, unasked
from tycho.commands import CommandMatcher
from tycho.keybinds import Action, Keybinds, KeyInput
from tycho.packs import STAR_CITIZEN, built_in

SC = built_in(STAR_CITIZEN)
MATCHER = CommandMatcher.default(pack=SC)
KEYBINDS = Keybinds(
    [
        Action(name, "spaceship_movement", label, inputs=(KeyInput("n"),))
        for name, label in [
            ("v_deploy_landing_system", "Landing System (Deploy)"),
            ("v_retract_landing_system", "Landing System (Retract)"),
            ("v_weapon_countermeasure_decoy_launch", "Launch Decoy"),
        ]
    ]
)
GEAR = ("v_deploy_landing_system",)
DOORS = ("v_open_all_doors",)


def _why(said: str, picked: tuple[str, ...] = DOORS, before: str = "") -> str:
    return unasked(Heard(said, before), picked)


@pytest.mark.parametrize(
    ("said", "picked", "why"),
    [
        ("Mike, lower your gear", GEAR, SOMEONE_ELSE),
        ("hey Sam, close your doors", DOORS, SOMEONE_ELSE),
        ("he said open the doors", DOORS, SOMEONE_ELSE),
        ("the tutorial says press N to lower the gear", GEAR, SOMEONE_ELSE),
        ("my buddy keeps telling me to lower the gear", GEAR, SOMEONE_ELSE),
        ("he's opening the doors", DOORS, SOMEONE_ELSE),
        ("the doors are open", DOORS, HOW_THINGS_ARE),
        ("we're in quantum mode now", ("v_set_quantum_mode",), HOW_THINGS_ARE),
        ("the power's on", ("v_power_toggle",), HOW_THINGS_ARE),
        ("if we get jumped, pop decoys", ("v_weapon_countermeasure_decoy_launch",), NOT_NOW),
        ("once we're out of quantum lower the gear", GEAR, NOT_NOW),
        ("when we land, open the doors", DOORS, NOT_NOW),
        ("when should I lower the gear", GEAR, ANSWER_IN_WORDS),
        ("is my ship flight ready", ("v_flightready",), ANSWER_IN_WORDS),
        ("can I open the doors while flying", DOORS, ANSWER_IN_WORDS),
        ("are the lights on", ("v_lights_on",), ANSWER_IN_WORDS),
        ("should we lower the gear", GEAR, ANSWER_IN_WORDS),
    ],
)
def test_words_that_dont_ask_for_the_act_are_refused(
    said: str, picked: tuple[str, ...], why: str
) -> None:
    assert _why(said, picked) == why


@pytest.mark.parametrize(
    ("said", "picked"),
    [
        ("open the doors", DOORS),
        ("Okay, open the doors", DOORS),
        ("Right, lower the gear", GEAR),
        ("I said open the doors", DOORS),
        ("can you open the doors", DOORS),
        ("can we get the doors open", DOORS),
        ("can I get the lights on", ("v_lights_on",)),
        ("if you can, lower the gear", GEAR),
        ("the doors are locked, open them", DOORS),
        ("it's dark in here, lights on", ("v_lights_on",)),
        ("they're on us, decoys", ("v_weapon_countermeasure_decoy_launch",)),
        ("is the gear down? Lower it", GEAR),
        ("where's my landing gear, it's not down", GEAR),
        ("let's lower the gear", GEAR),
    ],
)
def test_a_request_is_let_through(said: str, picked: tuple[str, ...]) -> None:
    assert _why(said, picked) == ""


@pytest.mark.parametrize(
    ("said", "picked"),
    [
        ("They're shooting at us, flares", ("v_weapon_countermeasure_decoy_launch",)),
        ("We're under fire, pop flares", ("v_weapon_countermeasure_decoy_launch",)),
        ("We're landing, gear down", ("v_deploy_landing_system",)),
        ("We're on the pad, gear up", ("v_retract_landing_system",)),
    ],
)
def test_a_short_request_in_the_games_words_after_a_break_is_let_through(
    said: str, picked: tuple[str, ...]
) -> None:
    """Review of #718: "flares" and "gear" name no part of the actions' names, so the
    words players use for an action (its label, the fast path's phrases) count too."""
    words = spoken(offered_actions(KEYBINDS, MATCHER, SC), MATCHER)
    assert unasked(Heard(said), picked, words) == ""
    assert unasked(Heard(said), picked) != ""  # without them, it was refused


def test_a_yes_to_tychos_offer_is_let_through() -> None:
    before = Exchange("x", "said: Want me to open the doors?").outcome
    assert _why("is that even a question, do it", DOORS, before) == ""


def test_no_words_heard_refuse_nothing() -> None:
    assert unasked(None, DOORS) == ""
