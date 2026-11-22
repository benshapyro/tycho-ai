"""The player's own keys as a look-up (#357)."""

import asyncio
import json
from typing import Any

from tycho.brain.player_keys import NAME, PlayerKeys, keyed
from tycho.keybinds import Action, Keybinds, KeyInput


def _bound(name: str, label: str, key: str = "", activation: str = "") -> Action:
    parsed = KeyInput.parse(key) if key else None
    return Action(name, "spaceship_weapons", label, (parsed,) if parsed else (), activation)


KEYBINDS = Keybinds(
    [
        _bound("v_attack1_group1", "Fire Weapon Group 1", "mouse1"),
        _bound("v_strafe_up", "Strafe Up", "space"),
        _bound("v_ifcs_boost", "Boost", "lshift", "hold"),
        _bound("v_eject", "Eject"),  # no key
    ]
)


def _ask(*actions: str, keybinds: Keybinds | None = KEYBINDS) -> dict[str, Any]:
    result = asyncio.run(PlayerKeys(lambda: keybinds).run({"actions": list(actions)}))
    assert not result.is_error
    return json.loads(result.content)


def test_an_action_by_name_gets_the_player_s_key_in_the_game_s_words() -> None:
    """Request 25 of a 0.6.0 play session: "how do you shoot the guns on the Idris?"
    got "Hold the primary fire button". Tycho had the player's keybinds."""
    assert _ask("v_attack1_group1") == {"keys": [{"does": "Fire Weapon Group 1", "key": "Mouse 1"}]}


def test_words_find_an_action_the_briefing_doesn_t_list_and_keep_its_note() -> None:
    assert _ask("strafe up")["keys"] == [{"does": "Strafe Up", "key": "Space"}]
    assert _ask("boost")["keys"] == [{"does": "Boost (Hold)", "key": "Left Shift"}]


def test_an_action_with_no_key_says_so_and_nothing_is_made_up() -> None:
    found = _ask("v_eject", "do a barrel roll")
    assert found["keys"] == []
    assert found["not_found"] == ["v_eject", "do a barrel roll"]
    assert "stick" in found["say"]


def test_with_no_keybinds_it_says_tycho_can_t_read_them() -> None:
    assert "not_found" in _ask("boost", keybinds=Keybinds([]))


def test_only_offered_while_key_actions_are_on() -> None:
    """With them off the briefing already lists every key (#392, #437)."""
    assert [lookup.name for lookup in keyed([], True, lambda: None)[0]] == [NAME]
    assert keyed([], False, lambda: None)[0] == []


def test_the_briefing_says_when_to_look_up_a_key() -> None:
    from tycho.brain.briefing import instructions
    from tycho.packs import STAR_CITIZEN, built_in

    text = " ".join(instructions(built_in(STAR_CITIZEN)).split())
    assert "or which key does it, call player_keys" in text


def test_the_briefing_reads_a_misheard_ship_name_as_the_ship() -> None:
    """#509: "how do you shoot the guns on the address?" got "which ship do you mean?"."""
    from tycho.brain.briefing import instructions
    from tycho.packs import STAR_CITIZEN, built_in

    text = " ".join(instructions(built_in(STAR_CITIZEN)).split())
    assert "Ship names are misheard as everyday words too" in text


def test_one_label_in_two_maps_gives_both_keys_with_their_maps() -> None:
    """Review of #516: the vehicle's key replaced the ship's, under the same words."""
    ship = _bound("v_lights", "Headlights (Toggle)", "l")
    car = Action("v_ground_lights", "vehicle_general", "Headlights (Toggle)", ship.inputs)
    found = asyncio.run(PlayerKeys(lambda: Keybinds([ship, car])).run({"actions": ["headlights"]}))
    assert json.loads(found.content)["keys"] == [
        {"does": "Headlights (Toggle) (spaceship weapons)", "key": "L"},
        {"does": "Headlights (Toggle) (vehicle general)", "key": "L"},
    ]


def test_it_answers_at_once_with_no_filler() -> None:
    assert PlayerKeys(lambda: None).instant is True
