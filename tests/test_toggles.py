"""Which presses are toggles standing in for a direction (#13, #29)."""

from tycho.brain import RunActions, commands_for
from tycho.commands import Command, CommandMatcher, resolve
from tycho.keybinds import Action, Keybinds, KeyInput
from tycho.packs import STAR_CITIZEN, built_in
from tycho.packs.pack import Safety
from tycho.runtime.planning import Steps, plan_request, toggle_name, toggle_receipt

SC = built_in(STAR_CITIZEN)


def _bound(name: str, key: str, label: str = "") -> Action:
    found = KeyInput.parse(key)
    assert found is not None
    return Action(name=name, actionmap="spaceship_general", label=label, inputs=(found,))


def _flips(action: Action, lists: Safety = SC.safety) -> bool:
    return lists.flips(action.name, action.label)


def test_toggles_swaps_and_mode_cycles_flip_a_state() -> None:
    assert _flips(_bound("v_toggle_all_doors", "k"))
    assert _flips(_bound("v_lights", "l", "Headlights (Toggle)"))
    assert _flips(_bound("v_ifcs_throttle_swap_mode", "c"))
    assert _flips(_bound("v_master_mode_cycle_long", "b"))
    assert not _flips(_bound("v_target_cycle_attacker_fwd", "t"))  # picks a target
    assert not _flips(_bound("v_open_all_doors", "k"))


def test_the_pack_says_what_a_toggle_is() -> None:
    # Core code knows no game's words (ADR 19): a pack that lists none has no toggles.
    assert not _flips(_bound("v_toggle_all_doors", "k"), Safety())
    assert _flips(_bound("flip_it", "k"), Safety(toggles=("flip",)))


def _toggled(command: Command, keybinds: Keybinds) -> tuple[str, ...]:
    planned = plan_request((command,), keybinds, lists=SC.safety)
    assert isinstance(planned, Steps)
    return planned.toggled


def test_only_a_fallback_toggle_is_blind() -> None:
    lights_on = Command("lights_on", ("lights on",), ("v_lights_on", "v_lights"))
    toggle = _bound("v_lights", "l", "Headlights (Toggle)")
    assert _toggled(lights_on, Keybinds([toggle])) == ("Headlights",)
    assert _toggled(lights_on, Keybinds([_bound("v_lights_on", "l"), toggle])) == ()
    # "Toggle the lights" asks for the toggle itself: nothing is hidden.
    asked = Command("lights", ("toggle the lights",), ("v_lights",))
    assert _toggled(asked, Keybinds([toggle])) == ()
    assert resolve(lights_on, Keybinds([toggle])).for_direction


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


def test_with_other_presses_the_receipt_follows_the_line() -> None:
    receipt = "Toggled Doors. I can't see which way it went."
    assert toggle_receipt(("Doors",), "Lights on.", others=True) == f"Lights on. {receipt}"
    assert toggle_receipt(("Doors",), "Doors open.") == receipt  # only the toggle
    assert toggle_receipt(("Doors",), "", others=True) == receipt


def test_the_plan_knows_when_something_else_was_pressed() -> None:
    doors = Command("doors_open", ("open the doors",), ("v_open_all_doors", "v_toggle_all_doors"))
    lights = Command("lights_on", ("lights on",), ("v_lights_on",))
    keybinds = Keybinds([_bound("v_toggle_all_doors", "k"), _bound("v_lights_on", "l")])
    alone = plan_request((doors,), keybinds, lists=SC.safety)
    both = plan_request((lights, doors), keybinds, lists=SC.safety)
    assert isinstance(alone, Steps)
    assert isinstance(both, Steps)
    assert not alone.others
    assert both.others
    assert both.toggled == ("All doors",)


def test_unlabelled_toggles_get_plain_names() -> None:
    assert toggle_name(_bound("v_toggle_all_doors", "k")) == "All doors"
    assert toggle_name(_bound("v_power_toggle_shields", "k")) == "Power shields"
    assert toggle_name(_bound("v_lights", "l", "Headlights (Toggle)")) == "Headlights"
    assert toggle_name(_bound("v_toggle", "k")) == "Toggle"  # nothing else to say


def test_a_brain_picked_fallback_is_marked_blind() -> None:
    matcher = CommandMatcher.default(pack=SC)
    (doors,) = commands_for(RunActions(("v_toggle_all_doors",)), matcher, lists=SC.safety)
    assert doors.blind  # a fallback of "open the doors": which way is unseen
    (own,) = commands_for(RunActions(("v_open_all_doors",)), matcher, lists=SC.safety)
    assert not own.blind  # a command's own action sets the direction
    (bare,) = commands_for(RunActions(("v_some_new_action",)), matcher, lists=SC.safety)
    assert not bare.blind  # nobody's fallback
