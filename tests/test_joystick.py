"""Joystick buttons through SDL (PySDL2), against a fake SDL: no hardware needed."""

# The fake stands in for PySDL2's untyped ctypes bindings.
# pyright: reportUnknownLambdaType=false, reportUnknownArgumentType=false, reportUnknownMemberType=false, reportUnknownVariableType=false, reportAttributeAccessIssue=false

from types import SimpleNamespace
from typing import Any

import pytest

from tycho.ptt import joystick
from tycho.ptt.binding import Binding, DeviceKind

GUID = "03000000231d00002701000000000000"


class FakeSdl:
    """Two identical sticks (the same GUID) and a third; ``held`` buttons are down."""

    SDL_HINT_JOYSTICK_ALLOW_BACKGROUND_EVENTS = b"hint"
    SDL_INIT_JOYSTICK = 1
    SDL_JOYDEVICEADDED = 0x605
    SDL_JOYDEVICEREMOVED = 0x606

    def __init__(self) -> None:
        self.sticks = [
            SimpleNamespace(guid=GUID, name=b"VKB Gladiator"),
            SimpleNamespace(guid=GUID, name=b"VKB Gladiator"),
            SimpleNamespace(guid="other", name=None),
        ]
        self.held: set[tuple[int, int]] = set()
        self.hints: dict[bytes, bytes] = {}
        self.events: list[int] = []
        self.closed = 0

    def SDL_SetHint(self, name: bytes, value: bytes) -> None:
        self.hints[name] = value

    def SDL_Init(self, flags: int) -> int:
        return 0

    def SDL_QuitSubSystem(self, flags: int) -> None:
        pass

    def SDL_NumJoysticks(self) -> int:
        return len(self.sticks)

    def SDL_JoystickOpen(self, index: int) -> Any:
        return SimpleNamespace(index=index)

    def SDL_JoystickClose(self, stick: Any) -> None:
        self.closed += 1

    def SDL_JoystickName(self, stick: Any) -> bytes | None:
        return self.sticks[stick.index].name

    def SDL_JoystickNumButtons(self, stick: Any) -> int:
        return 4

    def SDL_JoystickGetButton(self, stick: Any, button: int) -> int:
        return int((stick.index, button) in self.held)

    def SDL_Event(self) -> Any:
        return SimpleNamespace(type=0)

    def SDL_PollEvent(self, pointer: Any) -> int:
        if not self.events:
            return 0
        pointer._obj.type = self.events.pop(0)
        return 1

    def SDL_GetError(self) -> bytes:
        return b""


@pytest.fixture
def sdl(monkeypatch: pytest.MonkeyPatch) -> FakeSdl:
    fake = FakeSdl()
    monkeypatch.setattr(joystick, "sdl2", fake)
    monkeypatch.setattr(joystick, "_PLUGGED", (fake.SDL_JOYDEVICEADDED, fake.SDL_JOYDEVICEREMOVED))
    monkeypatch.setattr(joystick, "_guid", lambda stick: fake.sticks[stick.index].guid)
    monkeypatch.setattr(joystick.ctypes, "byref", lambda event: SimpleNamespace(_obj=event))
    return fake


def test_buttons_on_identical_sticks_are_told_apart(sdl: FakeSdl) -> None:
    source = joystick.JoystickSource()
    assert sdl.hints == {b"hint": b"1"}  # keeps reading while the game has focus
    assert source.names == {
        GUID: "VKB Gladiator",
        f"{GUID}#2": "VKB Gladiator",
        "other": "Joystick",
    }
    sdl.held = {(1, 2)}
    second = Binding(DeviceKind.JOYSTICK, 2, f"{GUID}#2")
    assert source.pressed() == frozenset({second})
    assert source.is_pressed(second)
    assert not source.is_pressed(Binding(DeviceKind.JOYSTICK, 2, GUID))
    assert not source.is_pressed(Binding(DeviceKind.JOYSTICK, 9, f"{GUID}#2"))  # no such button
    assert source.describe(second) == "VKB Gladiator button 3"


def test_plugging_a_stick_in_or_out_is_noticed(sdl: FakeSdl) -> None:
    source = joystick.JoystickSource()
    sdl.sticks.pop()
    sdl.events = [sdl.SDL_JOYDEVICEREMOVED]
    source.pressed()
    assert "other" not in source.names
    assert sdl.closed == 3  # the old handles are closed before reopening


def test_sdl_failing_to_start_is_a_clear_error(
    sdl: FakeSdl, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sdl, "SDL_Init", lambda flags: -1)
    monkeypatch.setattr(sdl, "SDL_GetError", lambda: b"no joystick support")
    with pytest.raises(joystick.JoystickError, match="no joystick support"):
        joystick.JoystickSource()
