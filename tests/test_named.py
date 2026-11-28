"""Actions a pack lists as ``named`` press only when the player's words single them out
(brain/named.py, #648).

In play, 25 Nov 2026, and again in the PC eval on 28 Nov: "switch laser tighten" pressed
Toggle Laser Beam (High / low), the mining laser's module, while the briefing said to ask."""

import pytest

from tycho.brain.briefing import offered_actions, spoken
from tycho.brain.heard import Heard
from tycho.brain.named import WHICH_ONE, unnamed
from tycho.brain.unasked import unasked
from tycho.commands import CommandMatcher
from tycho.keybinds import Action, Keybinds, KeyInput
from tycho.packs import STAR_CITIZEN, built_in

SC = built_in(STAR_CITIZEN)
MATCHER = CommandMatcher.default(pack=SC)
MODULE = "v_mining_use_permanent_modifier"
FIRE = "v_toggle_mining_laser_fire"
TYPE = "v_toggle_mining_laser_type"
# Ben's binds and labels, from his exported keybinds (22 Nov 2026).
KEYBINDS = Keybinds(
    [
        Action(name, "spaceship_mining", label, inputs=(KeyInput(key),))
        for name, label, key in [
            (FIRE, "Fire Mining Laser (Toggle)", "mouse1"),
            (TYPE, "Switch Mining Laser (Toggle)", "j"),
            (MODULE, "Toggle Laser Beam (High / low)", "4"),
            ("v_increase_mining_throttle", "Increase Mining Laser Power", "maxis_z"),
            ("tractor_beam_increase_distance", "Increase Tractor Beam Distance", "k"),
            ("v_deploy_landing_system", "Landing System (Deploy)", "n"),
        ]
    ]
)
WORDS = spoken(offered_actions(KEYBINDS, MATCHER, SC), MATCHER)
NAMED = SC.safety.named


def _why(said: str, picked: str) -> str:
    return unasked(Heard(said, ""), (picked,), WORDS, NAMED)


def test_the_pack_lists_the_mining_laser_actions() -> None:
    assert {FIRE, TYPE, MODULE} <= NAMED


@pytest.mark.parametrize("picked", [MODULE, FIRE, TYPE])
@pytest.mark.parametrize("said", ["switch laser tighten", "try switching the beam"])
def test_words_tycho_cant_place_never_press_a_mining_laser_action(said: str, picked: str) -> None:
    """Fails on the old code: nothing checked these words, so the model's pick pressed."""
    assert _why(said, picked) == WHICH_ONE


@pytest.mark.parametrize(
    ("said", "picked"),
    [
        ("turn on the mining laser", FIRE),  # its fire, its type, or its module?
        ("mining laser on", FIRE),
        ("toggle the beam", MODULE),  # the tractor beam has "beam" too
    ],
)
def test_words_another_action_has_too_ask_which(said: str, picked: str) -> None:
    assert _why(said, picked) == WHICH_ONE


@pytest.mark.parametrize(
    ("said", "picked"),
    [
        ("fire the mining laser", FIRE),
        ("switch the mining laser", TYPE),
        ("switch laser type", TYPE),
        ("toggle the laser beam", MODULE),
        ("set the laser beam to low", MODULE),
        ("ok, laser beam high please", MODULE),
        ("use the permanent modifier", MODULE),
    ],
)
def test_words_that_single_the_action_out_press_it(said: str, picked: str) -> None:
    assert _why(said, picked) == ""


def test_an_action_the_pack_doesnt_list_isnt_checked() -> None:
    assert _why("get our wheels down mate", "v_deploy_landing_system") == ""
    assert unnamed("switch laser tighten", (MODULE,), WORDS, frozenset()) == ""


def test_a_yes_to_tychos_own_offer_still_presses() -> None:
    heard = Heard("yes", "said: Want me to toggle the laser beam?")
    assert unasked(heard, (MODULE,), WORDS, NAMED) == ""
