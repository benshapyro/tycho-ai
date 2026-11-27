"""Push-to-talk on a stick: which stick, two that can swap (#623), and the game's own
stick bindings, warned about and never pressed (#622)."""

import xml.etree.ElementTree as ET
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest

from tests.fakes.fake_setup_pc import FakeSetupPC
from tycho import doctor, settings
from tycho.brain.briefing import ON_STICK, unbound_commands
from tycho.commands import Command, CommandMatcher, on_stick
from tycho.desktop.bridge import Bridge, setup_page
from tycho.findings import TYCHO, Finding
from tycho.games.star_citizen.keybinds import (
    apply_profile,
    parse_defaults,
    parse_sticks,
    without_sticks,
)
from tycho.input.plan import NotPressable, plan
from tycho.keybinds import Action, Keybinds, KeyInput, StickInput
from tycho.packs import STAR_CITIZEN, built_in
from tycho.ptt import sticks
from tycho.ptt.binding import Binding, DeviceKind
from tycho.ptt.conflicts import conflict_warnings
from tycho.ptt.devices import describe, describe_all
from tycho.runtime.planning import Refusal, plan_request

SC = built_in(STAR_CITIZEN)

LEFT = "030000001d230000ff01000000000000"
"""An SDL GUID for a stick with VKB's vendor ID (0x231D) and product 0x01FF."""
RIGHT = "030000001d2300000002000000000000"
"""The same maker's product 0x0200."""
RIGHT_PRODUCT = " VKBsim Gladiator EVO R    {0200231D-0000-0000-0000-504944564944}"
LEFT_PRODUCT = " VKBsim Gladiator EVO L    {01FF231D-0000-0000-0000-504944564944}"

DEFAULTS = """<profile>
  <actionmap name="spaceship_movement">
    <action name="v_toggle_landing_system" keyboard="n" joystick="button12" UILabel="Gear"/>
    <action name="v_lock_all_doors" keyboard=" " UILabel="Lock All Doors"/>
    <action name="v_ping" keyboard="tab"/>
  </actionmap>
</profile>"""

PROFILE = f"""<ActionMaps><ActionProfiles profileName="default">
  <options type="keyboard" instance="1" Product="Keyboard {{6F1D2B61-D5A0-11CF-BFC7-44455354}}"/>
  <options type="joystick" instance="1" Product="{LEFT_PRODUCT}"/>
  <options type="joystick" instance="2" Product="{RIGHT_PRODUCT}"/>
  <actionmap name="spaceship_movement">
    <action name="v_toggle_landing_system">
      <rebind input="js1_ "/><rebind input="js2_button5"/>
    </action>
    <action name="v_lock_all_doors"><rebind input="js2_button12"/></action>
    <action name="v_ping"><rebind input="js1_hat1_up"/></action>
  </actionmap>
</ActionProfiles></ActionMaps>"""


@pytest.fixture
def keybinds() -> Keybinds:
    defaults = parse_defaults(ET.fromstring(DEFAULTS), {})  # noqa: S314
    profile = ET.fromstring(PROFILE)  # noqa: S314
    return Keybinds(apply_profile(defaults, profile), sticks=parse_sticks(profile))


@dataclass(frozen=True)
class _Store:
    keybinds: Keybinds


def _store(keybinds: Keybinds) -> _Store:
    return _Store(keybinds)


# --- Reading the game's stick bindings (#622) ----------------------------------------------


def test_a_player_with_no_stick_gets_no_default_stick_binds() -> None:
    """Review of #706: the default profile's joystick inputs count only once the game
    names a stick, so a keyboard-only player isn't told "that's on your stick"."""
    defaults = parse_defaults(ET.fromstring(DEFAULTS), {})  # noqa: S314
    no_stick = ET.fromstring("<ActionMaps><ActionProfiles/></ActionMaps>")  # noqa: S314
    gear = Keybinds(apply_profile(defaults, no_stick)).get("v_toggle_landing_system")
    assert gear is not None
    assert gear.stick_inputs == ()
    assert [a.stick_inputs for a in without_sticks(defaults)] == [(), (), ()]


def test_stick_bindings_are_read_per_stick_and_kept_apart_from_keys(keybinds: Keybinds) -> None:
    gear = keybinds.get("v_toggle_landing_system")
    assert gear is not None
    assert [str(k) for k in gear.inputs] == ["n"]  # a stick rebind leaves the key
    # The default's button12 was stick 1's, and "js1_ " cleared it; stick 2 is added.
    assert gear.stick_inputs == (StickInput(2, "button5"),)
    doors = keybinds.get("v_lock_all_doors")
    assert doors is not None
    assert doors.inputs == ()
    assert doors.stick_inputs == (StickInput(2, "button12"),)
    assert keybinds.sticks == {1: LEFT_PRODUCT.strip(), 2: RIGHT_PRODUCT.strip()}


def test_only_a_plain_button_counts_as_a_button() -> None:
    assert StickInput(1, "button12").button == 12
    assert StickInput(1, "hat1_up").button is None
    assert StickInput(1, "x").button is None
    assert StickInput(1, "button3+button5").button is None


def test_a_stick_only_action_is_never_pressed(keybinds: Keybinds) -> None:
    """Zero wrong presses: a stick binding never becomes a key press."""
    doors = keybinds.get("v_lock_all_doors")
    assert doors is not None
    assert not doors.bound
    with pytest.raises(NotPressable):
        plan(doors, {})
    lock = Command("doors_lock", ("lock the doors",), ("v_lock_all_doors",))
    planned = plan_request((lock,), keybinds, lists=SC.safety)
    assert isinstance(planned, Refusal)
    assert planned.status == "no_key"


def test_a_stick_only_action_is_said_to_be_on_the_stick(keybinds: Keybinds) -> None:
    lock = Command("doors_lock", ("lock the doors",), ("v_lock_all_doors",))
    assert on_stick(lock, keybinds) is not None
    gear = Command("landing_gear", ("gear",), ("v_toggle_landing_system",))
    assert on_stick(gear, keybinds) is None  # it has a key: Tycho presses that
    nowhere = Keybinds([Action("v_lock_all_doors", "m", "Lock All Doors", ())])
    assert on_stick(lock, nowhere) is None  # truly unbound


def test_the_brain_is_told_a_command_is_on_the_stick(keybinds: Keybinds) -> None:
    lines = unbound_commands(keybinds, CommandMatcher.default(pack=SC))
    lock = next(line for line in lines if line.startswith("doors_lock |"))
    assert lock.endswith(f"| Lock All Doors{ON_STICK}")


# --- Push-to-talk on a stick button bound in game (#622) -----------------------------------


def test_usb_ids_come_from_both_kinds_of_guid() -> None:
    assert sticks.usb_ids(RIGHT) == (0x231D, 0x0200)
    assert sticks.usb_ids(f"{RIGHT}#2") == (0x231D, 0x0200)
    assert sticks.usb_ids("05000000566b65726c6974000000000") is None  # not a GUID
    assert sticks.product_usb_ids(RIGHT_PRODUCT) == (0x231D, 0x0200)
    assert sticks.product_name(RIGHT_PRODUCT) == "VKBsim Gladiator EVO R"


def test_a_stick_button_bound_in_game_is_a_conflict(keybinds: Keybinds) -> None:
    sticks.saw({LEFT: "VKBsim Gladiator EVO L", RIGHT: "VKBsim Gladiator EVO R"})
    warnings = conflict_warnings(Binding(DeviceKind.JOYSTICK, 11, RIGHT), _store(keybinds))
    assert warnings == [
        "VKBsim Gladiator EVO R button 12 is also bound in game (Lock All Doors). The game "
        f"sees the press too, so choose another button with `{TYCHO} bind`, or unbind it in game."
    ]


def test_the_same_button_on_the_other_stick_is_no_conflict(keybinds: Keybinds) -> None:
    # Button 12 is bound on the right stick (the game's 2) only.
    assert conflict_warnings(Binding(DeviceKind.JOYSTICK, 11, LEFT), _store(keybinds)) == []


def test_a_stick_matched_by_name_when_its_guid_has_no_usb_ids(keybinds: Keybinds) -> None:
    named = "05000000566b65726c69740000000000"
    sticks.saw({named: "VKBsim Gladiator EVO R"})
    warnings = conflict_warnings(Binding(DeviceKind.JOYSTICK, 11, named), _store(keybinds))
    assert len(warnings) == 1
    assert "is also bound in game (Lock All Doors)" in warnings[0]


def test_a_stick_tycho_cant_match_is_checked_against_every_stick(keybinds: Keybinds) -> None:
    """The safer choice: when Tycho can't tell which of the game's sticks it is, a button
    bound on any of them is warned about, as "may"."""
    unknown = "03000000aaaa0000bbbb000000000000"
    warnings = conflict_warnings(Binding(DeviceKind.JOYSTICK, 11, unknown), _store(keybinds))
    assert len(warnings) == 1
    assert warnings[0].startswith("joystick button 12 may be bound in game too (Lock All Doors)")
    lone = Keybinds(list(keybinds))  # actionmaps.xml named no stick at all
    assert conflict_warnings(Binding(DeviceKind.JOYSTICK, 11, RIGHT), _store(lone))


def test_check_warns_about_a_stick_button_bound_in_game(
    keybinds: Keybinds, monkeypatch: pytest.MonkeyPatch
) -> None:
    def game(*_args: object, **_kwargs: object) -> Iterator[Finding]:
        yield from ()
        return _store(keybinds)

    monkeypatch.setattr(doctor, "game_findings", game)
    current = settings.Settings(push_to_talk=f"joystick:{RIGHT}:11")
    found = [f for f in doctor.checks(current, quick=True, ask=False) if "conflict" in f.what]
    assert [f.level for f in found] == ["warn"]
    assert "Lock All Doors" in found[0].detail


# --- Which stick, and two that can swap (#623) ---------------------------------------------


def test_a_button_on_the_second_of_two_sticks_is_named_with_that_stick() -> None:
    sticks.saw({LEFT: "VKBsim Gladiator EVO L", f"{LEFT}#2": "VKBsim Gladiator EVO R"})
    second = Binding(DeviceKind.JOYSTICK, 11, f"{LEFT}#2")
    assert describe(second) == "VKBsim Gladiator EVO R button 12"
    first = Binding(DeviceKind.JOYSTICK, 2, LEFT)
    assert describe_all([first, second]) == (
        "VKBsim Gladiator EVO L button 3 or VKBsim Gladiator EVO R button 12"
    )


def test_twins_with_one_name_are_told_apart_by_order() -> None:
    sticks.saw({LEFT: "VKB Gladiator", f"{LEFT}#2": "VKB Gladiator"})
    assert describe(Binding(DeviceKind.JOYSTICK, 0, f"{LEFT}#2")) == "VKB Gladiator (2nd) button 1"
    assert describe(Binding(DeviceKind.JOYSTICK, 0, LEFT)) == "VKB Gladiator (1st) button 1"


def test_a_stick_not_plugged_in_keeps_the_plain_name() -> None:
    assert describe(Binding(DeviceKind.JOYSTICK, 11, RIGHT)) == "joystick button 12"


def test_sticks_that_share_a_guid_give_a_check_finding(monkeypatch: pytest.MonkeyPatch) -> None:
    def game(*_args: object, **_kwargs: object) -> Iterator[Finding]:
        yield from ()
        return None

    monkeypatch.setattr(doctor, "game_findings", game)
    sticks.saw({LEFT: "VKBsim Gladiator EVO L", f"{LEFT}#2": "VKBsim Gladiator EVO R"})
    current = settings.Settings(push_to_talk="mouse:0x06", push_to_talk_2=f"joystick:{LEFT}#2:11")
    found = [f for f in doctor.checks(current, quick=True, ask=False) if f.what.endswith("stick")]
    assert len(found) == 1
    assert found[0].level == "warn"
    assert "VKBsim Gladiator EVO L and VKBsim Gladiator EVO R" in found[0].detail
    assert "own USB ID" in found[0].detail
    assert found[0].fix == sticks.SWAP_HELP


def test_sticks_with_their_own_guids_give_no_swap_finding() -> None:
    sticks.saw({LEFT: "VKBsim Gladiator EVO L", RIGHT: "VKBsim Gladiator EVO R"})
    assert sticks.swap_note(Binding(DeviceKind.JOYSTICK, 11, RIGHT)) is None
    assert sticks.swap_note(Binding(DeviceKind.MOUSE, 0x06)) is None


def test_keyboard_keys_are_unchanged_by_stick_bindings(keybinds: Keybinds) -> None:
    ping = keybinds.get("v_ping")
    assert ping is not None
    assert ping.inputs == (KeyInput("tab"),)
    assert ping.stick_inputs == (StickInput(1, "hat1_up"),)


def test_setups_clash_warning_is_looked_at_again_once_the_sticks_are_seen(
    tmp_path: Path,
) -> None:
    # Review of #706 (#710): setup drew before push-to-talk opened the sticks, and kept
    # "joystick button 12" for the window's lifetime.
    pc = FakeSetupPC()
    bridge = Bridge(pc=pc, settings_path=tmp_path / "settings.toml")
    current = settings.Settings(push_to_talk=f"joystick:{RIGHT}:11")
    ptt = f"joystick:{RIGHT}:11"
    pc.clashes = [f"{describe(Binding.parse(ptt))} may be bound in game too"]
    assert bridge._conflicts(ptt, current) == ["joystick button 12 may be bound in game too"]  # pyright: ignore[reportPrivateUsage]
    sticks.saw({RIGHT: "VKBsim Gladiator EVO R"})
    pc.clashes = [f"{describe(Binding.parse(ptt))} may be bound in game too"]
    assert bridge._conflicts(ptt, current) == [  # pyright: ignore[reportPrivateUsage]
        "VKBsim Gladiator EVO R button 12 may be bound in game too"
    ]
    assert pc.calls.count("conflicts") == 2
    bridge._conflicts(ptt, current)  # pyright: ignore[reportPrivateUsage]
    assert pc.calls.count("conflicts") == 2  # still kept while nothing changed


def test_setup_notes_a_push_to_talk_stick_with_a_twin() -> None:
    sticks.saw({LEFT: "VKBsim Gladiator EVO L", f"{LEFT}#2": "VKBsim Gladiator EVO R"})
    [note] = setup_page._stick_notes(f"joystick:{LEFT}#2:11")  # pyright: ignore[reportPrivateUsage]
    assert "VKBsim Gladiator EVO L and VKBsim Gladiator EVO R" in note
    assert setup_page._stick_notes(f"joystick:{RIGHT}:11") == []  # pyright: ignore[reportPrivateUsage]
    assert setup_page._stick_notes(None) == []  # pyright: ignore[reportPrivateUsage]
