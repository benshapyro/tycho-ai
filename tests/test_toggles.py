"""Which presses are toggles standing in for a direction (#13, #29)."""

from tycho.commands import Command, resolve
from tycho.commands.resolve import is_toggle
from tycho.keybinds import Action, Keybinds, KeyInput
from tycho.packs import STAR_CITIZEN, built_in
from tycho.runtime.planning import Steps, plan_request, toggle_receipt

SC = built_in(STAR_CITIZEN)


def _bound(name: str, key: str, label: str = "") -> Action:
    found = KeyInput.parse(key)
    assert found is not None
    return Action(name=name, actionmap="spaceship_general", label=label, inputs=(found,))


def test_toggles_swaps_and_mode_cycles_flip_a_state() -> None:
    assert is_toggle(_bound("v_toggle_all_doors", "k"))
    assert is_toggle(_bound("v_lights", "l", "Headlights (Toggle)"))
    assert is_toggle(_bound("v_ifcs_throttle_swap_mode", "c"))
    assert is_toggle(_bound("v_master_mode_cycle_long", "b"))
    assert not is_toggle(_bound("v_target_cycle_attacker_fwd", "t"))  # picks a target
    assert not is_toggle(_bound("v_open_all_doors", "k"))


def test_only_a_fallback_toggle_is_blind() -> None:
    lights_on = Command("lights_on", ("lights on",), ("v_lights_on", "v_lights"))
    toggle = _bound("v_lights", "l", "Headlights (Toggle)")
    assert resolve(lights_on, Keybinds([toggle])).blind
    assert not resolve(lights_on, Keybinds([_bound("v_lights_on", "l"), toggle])).blind
    # "Toggle the lights" asks for the toggle itself: nothing is hidden.
    asked = Command("lights", ("toggle the lights",), ("v_lights",))
    assert not resolve(asked, Keybinds([toggle])).blind


def test_the_plan_names_each_blind_toggle_once() -> None:
    lights_on = Command("lights_on", ("lights on",), ("v_lights_on", "v_lights"))
    keybinds = Keybinds([_bound("v_lights", "l", "Headlights (Toggle)")])
    planned = plan_request((lights_on, lights_on), keybinds, lists=SC.safety)
    assert isinstance(planned, Steps)
    assert planned.toggled == ("Headlights",)


def test_the_receipt_for_one_or_more() -> None:
    assert toggle_receipt(("Headlights",)) == "Toggled Headlights. I can't see which way it went."
    assert toggle_receipt(("Headlights", "Doors")) == (
        "Toggled Headlights and Doors. I can't see which way they went."
    )
