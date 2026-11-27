"""The sticks Tycho last saw, and matching one to the game's own numbering (#622, #623).

``JoystickSource`` tells sticks apart by SDL GUID, which comes from the USB IDs, and
records here each stick it opens, with its name. That names a stick binding for people
("VKBsim Gladiator EVO R button 12", not "joystick button 12") without changing how the
setting is saved, and tells when two sticks share a GUID: they're told apart only by
the order they were plugged in, so after a replug or restart push-to-talk can move to
the other stick.

The game numbers sticks its own way (1, 2...) and names each with its DirectInput
product GUID, "{0200231D-0000-0000-0000-504944564944}": the product and vendor USB IDs,
then "PIDVID". Both GUIDs carry the USB IDs, so a stick is matched by those, then by
name. Identical sticks match every stick of theirs in the game: a warning about either
is the safe answer, since Tycho can't tell which is which.
"""

import re
import threading
from collections.abc import Collection, Mapping

from tycho.findings import TYCHO
from tycho.ptt.binding import Binding, DeviceKind

_lock = threading.Lock()
_seen: dict[str, str] = {}
"""Every stick at the last look, by Tycho's id for it (a GUID, "#2" on a second
identical one), with its name."""

_PRODUCT_GUID = re.compile(r"\{([0-9A-Fa-f]{8})-0000-0000-0000-504944564944\}")
_SDL_GUID = re.compile(r"[0-9a-f]{32}")

SWAP_HELP = (
    "Your stick's own setup tool can give each stick its own USB ID (VKB's config tool "
    "can), and then they can't swap."
)


def saw(names: Mapping[str, str]) -> None:
    """The sticks plugged in now, by id, with their names: from ``JoystickSource``."""
    with _lock:
        _seen.clear()
        _seen.update(names)


def name(device: str) -> str | None:
    """The name of the stick with this id, if Tycho has seen it plugged in."""
    with _lock:
        return _seen.get(device)


def guid(device: str) -> str:
    """The SDL GUID in a stick's id: "<guid>#2" is the second stick with that GUID."""
    return device.partition("#")[0]


def doubled() -> dict[str, list[str]]:
    """Each GUID shared by more than one stick plugged in, with those sticks' names."""
    with _lock:
        seen = dict(_seen)
    shared: dict[str, list[str]] = {}
    for device, stick in seen.items():
        shared.setdefault(guid(device), []).append(stick)
    return {g: names for g, names in shared.items() if len(names) > 1}


def swap_note(binding: Binding) -> str | None:
    """What to tell the player when their push-to-talk stick has a twin that can take
    its place after a replug or restart (#623); None if it hasn't."""
    if binding.kind is not DeviceKind.JOYSTICK or binding.device is None:
        return None
    names = doubled().get(guid(binding.device))
    if names is None:
        return None
    return (
        f"{len(names)} of your sticks look the same to Windows ({_both(names)}), so Tycho "
        "tells them apart only by the order they were plugged in. After a replug or "
        f"restart, push-to-talk can move to the other one. {SWAP_HELP} Then choose the "
        f"button again with `{TYCHO} bind`."
    )


def usb_ids(sdl_guid: str) -> tuple[int, int] | None:
    """The vendor and product USB IDs in an SDL GUID, or None if it doesn't carry them."""
    text = guid(sdl_guid).lower()
    if not _SDL_GUID.fullmatch(text):
        return None
    data = bytes.fromhex(text)
    vendor = int.from_bytes(data[4:6], "little")
    product = int.from_bytes(data[8:10], "little")
    if not vendor or data[6:8] != b"\0\0" or data[10:12] != b"\0\0":
        return None  # a GUID made from the stick's name, not its USB IDs
    return vendor, product


def product_usb_ids(product: str) -> tuple[int, int] | None:
    """The vendor and product USB IDs in the game's name for a stick, or None."""
    match = _PRODUCT_GUID.search(product)
    if match is None:
        return None
    value = int(match[1], 16)
    return value & 0xFFFF, value >> 16


def product_name(product: str) -> str:
    """The game's name for a stick without its GUID: "VKBsim Gladiator EVO R"."""
    return " ".join(re.sub(r"\{[^}]*\}", " ", product).split())


def game_sticks(binding: Binding, sticks: Mapping[int, str]) -> Collection[int] | None:
    """The game's numbers for the stick ``binding`` is on: every stick of the game's with
    the same USB IDs, or else the same name. None when Tycho can't tell (the game names
    no stick, or none matches): then any of them may be it."""
    if binding.device is None:
        return None
    ids = usb_ids(binding.device)
    found = [n for n, product in sticks.items() if ids and product_usb_ids(product) == ids]
    if not found and (seen := name(binding.device)):
        found = [n for n, product in sticks.items() if _same(product_name(product), seen)]
    return found or None


def _same(a: str, b: str) -> bool:
    return bool(a) and " ".join(a.split()).casefold() == " ".join(b.split()).casefold()


def _both(names: list[str]) -> str:
    if len(set(names)) == 1:
        return f"both {names[0]}" if len(names) == 2 else f"each {names[0]}"
    return ", ".join(names[:-1]) + f" and {names[-1]}"
