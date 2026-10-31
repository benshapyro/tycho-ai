"""Keyboard and mouse buttons, read with the Win32 ``GetAsyncKeyState`` call.

Polling reads global button state without installing a hook, and works while
the game has focus.
"""

import ctypes

from tycho.ptt.binding import Binding, DeviceKind

_user32 = ctypes.WinDLL("user32", use_last_error=True)

_get_async_key_state = _user32.GetAsyncKeyState
_get_async_key_state.argtypes = [ctypes.c_int]
_get_async_key_state.restype = ctypes.c_short

_map_virtual_key = _user32.MapVirtualKeyW
_map_virtual_key.argtypes = [ctypes.c_uint, ctypes.c_uint]
_map_virtual_key.restype = ctypes.c_uint

_get_key_name_text = _user32.GetKeyNameTextW
_get_key_name_text.argtypes = [ctypes.c_long, ctypes.c_wchar_p, ctypes.c_int]
_get_key_name_text.restype = ctypes.c_int

_HELD = 0x8000
_MAPVK_VK_TO_VSC = 0
_MAPVK_VK_TO_VSC_EX = 4
# Generic Shift, Ctrl and Alt. Their left and right variants are reported instead.
_GENERIC_MODIFIERS = frozenset({0x10, 0x11, 0x12})

MOUSE_BUTTONS = {
    0x01: "Mouse left",
    0x02: "Mouse right",
    0x04: "Mouse middle",
    0x05: "Mouse side (back)",
    0x06: "Mouse side (forward)",
}
LEFT_MOUSE = Binding(DeviceKind.MOUSE, 0x01)


class KeyboardMouseSource:
    """All keyboard keys and mouse buttons."""

    def pressed(self) -> frozenset[Binding]:
        return frozenset(
            _binding_for(vk)
            for vk in range(1, 255)
            if vk not in _GENERIC_MODIFIERS and _get_async_key_state(vk) & _HELD
        )

    def is_pressed(self, binding: Binding) -> bool:
        if binding.kind is DeviceKind.JOYSTICK:
            return False
        return bool(_get_async_key_state(binding.code) & _HELD)


def describe(binding: Binding) -> str:
    """A human-readable name, such as ``F13`` or ``Mouse side (back)``."""
    if binding.kind is DeviceKind.MOUSE:
        return MOUSE_BUTTONS.get(binding.code, f"Mouse button {binding.code:#04x}")
    scan_code = _map_virtual_key(binding.code, _MAPVK_VK_TO_VSC)
    buffer = ctypes.create_unicode_buffer(64)
    if scan_code and _get_key_name_text(scan_code << 16, buffer, len(buffer)):
        return buffer.value
    return f"Key {binding.code:#04x}"


def scan_code(vk: int) -> tuple[int, bool]:
    """A virtual key's scan code, and whether it's an extended key (e.g. right Alt)."""
    code = _map_virtual_key(vk, _MAPVK_VK_TO_VSC_EX)
    return code & 0xFF, (code >> 8) in (0xE0, 0xE1)


def _binding_for(vk: int) -> Binding:
    kind = DeviceKind.MOUSE if vk in MOUSE_BUTTONS else DeviceKind.KEYBOARD
    return Binding(kind, vk)
