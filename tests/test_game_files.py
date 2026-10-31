import os
import struct
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

import pytest

from tests.fakes.cryxml_writer import encode
from tycho.game import cryxml
from tycho.game.install import GameInstall
from tycho.game.keybinds import Keybinds, KeyInput, apply_profile, parse_defaults
from tycho.game.localization import parse_global_ini, resolve
from tycho.game.p4k import P4kArchive
from tycho.game.store import GameDataError, KeybindStore

DEFAULTS = """<profile>
  <actionmap name="spaceship_movement">
    <action name="v_toggle_landing_system" activationMode="tap" keyboard="n" joystick="button12"
            UILabel="@ui_Gear"/>
    <action name="v_deploy_landing_system" keyboard=" " UILabel="@ui_GearDown"/>
  </actionmap>
  <actionmap name="spaceship_general">
    <action name="v_toggle_all_doors" keyboard=" " UILabel="@ui_Doors"/>
    <action name="v_close_all_doors" keyboard="lalt+k"/>
  </actionmap>
  <actionmap name="ui">
    <action name="ui_enter"><keyboard><inputdata input="enter"/><inputdata input="np_enter"/>
    </keyboard></action>
    <action name="prone_roll"><keyboard activationMode="double_tap" input="q"/></action>
    <action name="v_fire" keyboard="mouse1" mouse="mouse4"/>
  </actionmap>
</profile>"""

PROFILE = """<ActionMaps><ActionProfiles profileName="default">
  <actionmap name="spaceship_general">
    <action name="v_toggle_all_doors"><rebind input="kb1_ralt+i"/></action>
    <action name="v_close_all_doors"><rebind input="js2_button25"/></action>
  </actionmap>
  <actionmap name="spaceship_movement">
    <action name="v_toggle_landing_system"><rebind input="kb1_ "/></action>
  </actionmap>
</ActionProfiles></ActionMaps>"""

INI = "﻿ui_Gear=Landing System (Toggle)\nui_GearDown,P=Landing System (Deploy)\nui_Doors=Doors\n"


def test_cryxml_round_trip() -> None:
    root = _without_whitespace(ET.fromstring(DEFAULTS))  # noqa: S314
    assert ET.tostring(cryxml.decode(encode(root))) == ET.tostring(root)


def test_cryxml_passes_plain_xml_through() -> None:
    assert cryxml.decode(b"<a b='1'/>").get("b") == "1"


@pytest.mark.parametrize(
    ("text", "parsed"),
    [
        ("n", KeyInput("n")),
        ("ralt+r", KeyInput("r", ("ralt",))),
        ("f6+lalt", KeyInput("f6", ("lalt",))),
        ("kb1_ralt+i", KeyInput("i", ("ralt",))),
        ("kb1_ ", None),
        (" ", None),
        ("lctrl", KeyInput("lctrl")),
    ],
)
def test_key_input_parse(text: str, parsed: KeyInput | None) -> None:
    assert KeyInput.parse(text) == parsed


def test_mouse_and_axes() -> None:
    assert KeyInput("mouse1").device == "mouse"
    assert not KeyInput("maxis_z").pressable


def test_global_ini_and_labels() -> None:
    strings = parse_global_ini(INI)
    assert resolve("@ui_GearDown", strings) == "Landing System (Deploy)"
    assert resolve("@ui_Missing", strings) == ""


def _keybinds() -> Keybinds:
    defaults = parse_defaults(ET.fromstring(DEFAULTS), parse_global_ini(INI))  # noqa: S314
    return Keybinds(apply_profile(defaults, ET.fromstring(PROFILE)))  # noqa: S314


def test_defaults_in_every_format() -> None:
    defaults = Keybinds(parse_defaults(ET.fromstring(DEFAULTS), {}))  # noqa: S314
    assert [str(i) for i in defaults.get("ui_enter").inputs] == ["enter", "np_enter"]  # type: ignore[union-attr]
    roll = defaults.get("prone_roll")
    assert roll is not None
    assert roll.activation == "double_tap"
    fire = defaults.get("v_fire")
    assert fire is not None
    assert [str(i) for i in fire.inputs] == ["mouse1", "mouse4"]


def test_player_keyboard_rebind_replaces_the_default() -> None:
    doors = _keybinds().get("v_toggle_all_doors")
    assert doors is not None
    assert [str(i) for i in doors.inputs] == ["ralt+i"]
    assert doors.customized
    assert doors.label == "Doors"


def test_joystick_rebind_keeps_the_keyboard_key() -> None:
    close = _keybinds().get("v_close_all_doors")
    assert close is not None
    assert [str(i) for i in close.inputs] == ["lalt+k"]


def test_clearing_a_keyboard_binding_unbinds_it() -> None:
    gear = _keybinds().get("v_toggle_landing_system")
    assert gear is not None
    assert not gear.bound


def _fake_install(tmp_path: Path) -> GameInstall:
    install = GameInstall(tmp_path / "LIVE")
    install.folder.mkdir()
    with zipfile.ZipFile(install.data_p4k, "w", compression=zipfile.ZIP_ZSTANDARD) as archive:
        archive.writestr("Data/Libs/Config/defaultProfile.xml", encode(ET.fromstring(DEFAULTS)))  # noqa: S314
        archive.writestr("Data/Localization/english/global.ini", INI.encode())
    return install


def test_p4k_reads_entries_regardless_of_case_and_slashes(tmp_path: Path) -> None:
    install = _fake_install(tmp_path)
    archive = P4kArchive(install.data_p4k)
    data = archive.read_file(r"data\localization\ENGLISH\global.ini")
    assert data == INI.encode()
    with pytest.raises(FileNotFoundError):
        archive.read_file("Data/missing.xml")


def test_store_loads_caches_and_reloads_on_change(tmp_path: Path) -> None:
    install = _fake_install(tmp_path)
    install.actionmaps.parent.mkdir(parents=True)
    install.actionmaps.write_text(PROFILE, encoding="utf-8")
    cache = tmp_path / "cache"

    store = KeybindStore.load(install, cache_dir=cache)
    doors = store.keybinds.get("v_toggle_all_doors")
    assert doors is not None
    assert str(doors.inputs[0]) == "ralt+i"
    assert doors.label == "Doors"
    assert any(cache.glob("defaultProfile-*.xml"))
    assert not store.refresh()  # nothing changed

    install.actionmaps.write_text(PROFILE.replace("ralt+i", "lctrl+o"), encoding="utf-8")
    stat = install.actionmaps.stat()
    os.utime(install.actionmaps, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000_000))
    assert store.refresh()
    doors = store.keybinds.get("v_toggle_all_doors")
    assert doors is not None
    assert str(doors.inputs[0]) == "lctrl+o"


def test_store_works_without_a_player_profile(tmp_path: Path) -> None:
    store = KeybindStore.load(_fake_install(tmp_path), cache_dir=tmp_path / "cache")
    gear = store.keybinds.get("v_toggle_landing_system")
    assert gear is not None
    assert str(gear.inputs[0]) == "n"


def test_store_keeps_old_keybinds_if_the_profile_is_half_written(tmp_path: Path) -> None:
    install = _fake_install(tmp_path)
    install.actionmaps.parent.mkdir(parents=True)
    install.actionmaps.write_text(PROFILE, encoding="utf-8")
    store = KeybindStore.load(install, cache_dir=tmp_path / "cache")
    install.actionmaps.write_text(PROFILE[:40], encoding="utf-8")
    assert not store.refresh()
    assert store.keybinds.get("v_toggle_all_doors") is not None


def test_find_install_reports_where_it_looked(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="TYCHO_SC_ROOT"):
        GameInstall.find(roots=(tmp_path,))


def test_find_install_honours_the_root_variable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    install = _fake_install(tmp_path)
    monkeypatch.setenv("TYCHO_SC_ROOT", str(tmp_path))
    assert GameInstall.find() == install


def test_truncated_archive_raises_instead_of_hanging(tmp_path: Path) -> None:
    install = _fake_install(tmp_path)
    data = install.data_p4k.read_bytes()
    eocd = data.rfind(b"PK\x05\x06")
    # Point the central directory past the end of the file, as a half-patched archive might.
    broken = data[: eocd + 16] + (len(data) + 1000).to_bytes(4, "little") + data[eocd + 20 :]
    install.data_p4k.write_bytes(broken)
    with pytest.raises(ValueError, match="past the end"):
        P4kArchive(install.data_p4k).find(["Data/Libs/Config/defaultProfile.xml"])


def test_cut_off_central_directory_raises(tmp_path: Path) -> None:
    install = _fake_install(tmp_path)
    data = install.data_p4k.read_bytes()
    eocd = data.rfind(b"PK\x05\x06")
    cd_size = int.from_bytes(data[eocd + 12 : eocd + 16], "little")
    # Claim a smaller directory than there is: the last record is cut off part way.
    broken = data[: eocd + 12] + (cd_size - 10).to_bytes(4, "little") + data[eocd + 16 :]
    install.data_p4k.write_bytes(broken)
    with pytest.raises(ValueError, match="truncated"):
        P4kArchive(install.data_p4k).find(["missing.xml"])


def test_corrupt_cache_is_rebuilt(tmp_path: Path) -> None:
    install = _fake_install(tmp_path)
    cache = tmp_path / "cache"
    KeybindStore.load(install, cache_dir=cache)
    (cached,) = cache.glob("defaultProfile-*.xml")
    cached.write_text("<profile><actionmap", encoding="utf-8")  # cut off mid-write
    store = KeybindStore.load(install, cache_dir=cache)
    assert store.keybinds.get("v_toggle_landing_system") is not None


def test_caches_from_old_builds_are_pruned(tmp_path: Path) -> None:
    install = _fake_install(tmp_path)
    cache = tmp_path / "cache"
    cache.mkdir()
    old = cache / "defaultProfile-1-2.xml"
    old.write_text("<profile/>", encoding="utf-8")
    KeybindStore.load(install, cache_dir=cache)
    assert not old.exists()


def test_missing_game_data_is_a_clear_error(tmp_path: Path) -> None:
    install = GameInstall(tmp_path / "LIVE")
    with pytest.raises(GameDataError, match="not found"):
        KeybindStore.load(install, cache_dir=tmp_path / "cache")


def test_refresh_keeps_last_good_keybinds_when_the_game_files_vanish(tmp_path: Path) -> None:
    install = _fake_install(tmp_path)
    store = KeybindStore.load(install, cache_dir=tmp_path / "cache")
    install.data_p4k.unlink()  # e.g. mid-patch
    assert not store.refresh()
    assert store.last_error is not None
    assert store.keybinds.get("v_toggle_landing_system") is not None


def test_double_tap_bindings_say_how_many_presses() -> None:
    defaults = """<profile><actionmap name="m">
      <action name="a" keyboard="x" activationMode="double_tap"/>
      <action name="b" keyboard="y" multiTap="3"/>
      <action name="c" keyboard="z"/>
    </actionmap></profile>"""
    profile = """<ActionMaps><ActionProfiles><actionmap name="m">
      <action name="c"><rebind input="kb1_q" multiTap="2"/></action>
    </actionmap></ActionProfiles></ActionMaps>"""
    keybinds = Keybinds(
        apply_profile(parse_defaults(ET.fromstring(defaults), {}), ET.fromstring(profile))  # noqa: S314
    )
    taps = {name: keybinds.get(name).taps for name in "abc"}  # type: ignore[union-attr]
    assert taps == {"a": 2, "b": 3, "c": 2}


def test_keyboard_and_mouse_share_one_slot() -> None:
    # Seen in game: a keyboard rebind took quantum engage off its default left click.
    defaults = """<profile><actionmap name="m">
      <action name="engage" mouse="mouse1"/>
    </actionmap></profile>"""
    profile = """<ActionMaps><ActionProfiles><actionmap name="m">
      <action name="engage"><rebind input="kb1_ralt+q"/><rebind input="js1_button3"/></action>
    </actionmap></ActionProfiles></ActionMaps>"""
    action = Keybinds(
        apply_profile(parse_defaults(ET.fromstring(defaults), {}), ET.fromstring(profile))  # noqa: S314
    ).get("engage")
    assert action is not None
    assert [str(i) for i in action.inputs] == ["ralt+q"]


def test_mouse_rebinds_and_activation_overrides() -> None:
    defaults = """<profile><actionmap name="m">
      <action name="fire" keyboard="f" mouse="mouse1" activationMode="press"/>
    </actionmap></profile>"""
    profile = """<ActionMaps><ActionProfiles><actionmap name="m">
      <action name="fire"><rebind input="mo1_mouse4" activationMode="hold"/></action>
    </actionmap></ActionProfiles></ActionMaps>"""
    action = Keybinds(
        apply_profile(parse_defaults(ET.fromstring(defaults), {}), ET.fromstring(profile))  # noqa: S314
    ).get("fire")
    assert action is not None
    assert [str(i) for i in action.inputs] == ["mouse4"]  # the default "f" goes with it
    assert action.activation == "hold"


def test_modifier_only_bindings_are_canonical() -> None:
    assert KeyInput.parse("lalt+lctrl") == KeyInput.parse("lctrl+lalt")


@pytest.mark.parametrize(
    "data",
    [
        b"CryXmlB\x00" + b"\x00" * 10,  # header cut short
        b"CryXmlB\x00" + struct.pack("<9I", 0, 9999, 5, 0, 0, 0, 0, 0, 0),  # table past the end
    ],
)
def test_malformed_cryxml_raises_value_error(data: bytes) -> None:
    with pytest.raises(ValueError, match="CryXmlB"):
        cryxml.decode(data)


def _without_whitespace(element: ET.Element) -> ET.Element:
    for e in element.iter():
        if e.text is not None and not e.text.strip():
            e.text = None
        e.tail = None
    return element
