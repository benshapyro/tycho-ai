import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from tycho.game.keybinds import Keybinds, KeyInput, apply_profile, parse_defaults
from tycho.game.layout import Addition, LayoutError, build, outstanding, write

DEFAULTS = """<profile>
  <actionmap name="spaceship_general">
    <action name="v_toggle_all_doors" keyboard=" "/>
    <action name="v_close_all_doors" keyboard=" "/>
    <action name="v_open_all_doors" keyboard=" "/>
  </actionmap>
  <actionmap name="vehicle_general">
    <action name="v_open_all_doors" keyboard=" "/>
  </actionmap>
  <actionmap name="lights_controller">
    <action name="v_lights" keyboard="l"/>
    <action name="v_lights_on" keyboard=" "/>
  </actionmap>
</profile>"""

PROFILE = """<ActionMaps>
<ActionProfiles version="1" optionsVersion="2" rebindVersion="2" profileName="default">
  <options type="keyboard" instance="1"/>
  <modifiers/>
  <actionmap name="spaceship_general">
    <action name="v_toggle_all_doors"><rebind input="kb1_ralt+i"/></action>
    <action name="v_close_all_doors"><rebind input="js2_button25"/></action>
  </actionmap>
</ActionProfiles></ActionMaps>"""


def _actions() -> list[object]:
    defaults = parse_defaults(ET.fromstring(DEFAULTS), {})  # noqa: S314
    return list(apply_profile(defaults, ET.fromstring(PROFILE)))  # noqa: S314


def _build(*additions: Addition) -> ET.Element:
    return build(ET.fromstring(PROFILE), additions, _actions(), "Tycho_test")  # type: ignore[arg-type]  # noqa: S314


def _reread(root: ET.Element) -> Keybinds:
    defaults = parse_defaults(ET.fromstring(DEFAULTS), {})  # noqa: S314
    return Keybinds(apply_profile(defaults, root))


def test_adds_bindings_and_keeps_existing_ones() -> None:
    root = _build(Addition("v_close_all_doors", "rctrl+u"), Addition("v_lights_on", "rctrl+l"))
    keybinds = _reread(root)
    close = keybinds.get("v_close_all_doors")
    assert close is not None
    assert [str(i) for i in close.inputs] == ["rctrl+u"]
    toggle = keybinds.get("v_toggle_all_doors")
    assert toggle is not None
    assert [str(i) for i in toggle.inputs] == ["ralt+i"]
    # The stick binding survives alongside the new keyboard key.
    assert 'input="js2_button25"' in ET.tostring(root, encoding="unicode")


@pytest.mark.parametrize("key", ["ralt+f4", "lalt+f4", "ralt+tab", "rctrl+escape", "ralt+space"])
def test_windows_shortcuts_are_refused(key: str) -> None:
    with pytest.raises(LayoutError, match="Windows shortcut"):
        _build(Addition("v_lights_on", key))


def test_key_names_ignore_case() -> None:
    assert KeyInput.parse("ralt+K") == KeyInput.parse("RAlt+k")


def test_additions_already_in_place_are_skipped() -> None:
    first = Addition("v_lights_on", "rctrl+l")
    later = Addition("v_close_all_doors", "rctrl+u")
    applied = list(_reread(_build(first)))
    assert outstanding([first, later], applied) == [later]  # a grown list applies again safely
    assert outstanding([Addition("v_lights_on", "rctrl+k")], applied)  # a different key isn't


def test_an_action_in_several_contexts_is_bound_in_all_of_them() -> None:
    root = _build(Addition("v_open_all_doors", "rctrl+i"))
    maps = {
        m.get("name")
        for m in root.iter("actionmap")
        for a in m.iter("action")
        if a.get("name") == "v_open_all_doors"
    }
    assert maps == {"spaceship_general", "vehicle_general"}


def test_profile_is_in_the_export_format() -> None:
    root = _build(Addition("v_lights_on", "rctrl+l"))
    assert root.tag == "ActionMaps"
    assert root.get("profileName") == "Tycho_test"
    header = root.find("CustomisationUIHeader")
    assert header is not None
    assert header.get("label") == "Tycho_test"
    devices = root.find("CustomisationUIHeader/devices")
    assert devices is not None
    assert [d.tag for d in devices] == ["keyboard", "mouse", "joystick"]  # js2 is used


@pytest.mark.parametrize(
    ("additions", "message"),
    [
        ([Addition("v_lights_on", "l")], "already used by v_lights"),
        ([Addition("v_lights_on", "ralt+i")], "already used"),
        ([Addition("v_toggle_all_doors", "rctrl+j")], "already has a keyboard binding"),
        ([Addition("v_lights_on", "rctrl+k"), Addition("v_open_all_doors", "rctrl+k")], "both"),
        ([Addition("v_made_up", "rctrl+z")], "isn't an action"),
    ],
)
def test_clashes_are_refused(additions: list[Addition], message: str) -> None:
    with pytest.raises(LayoutError, match=message):
        _build(*additions)


def test_write_never_overwrites(tmp_path: Path) -> None:
    root = _build(Addition("v_lights_on", "rctrl+l"))
    path = write(root, tmp_path)
    assert path.name == "layout_Tycho_test_exported.xml"
    with pytest.raises(FileExistsError):
        write(root, tmp_path)
