"""Joystick buttons through SDL (PySDL2), against a fake SDL: no hardware needed."""

# The fake stands in for PySDL2's untyped ctypes bindings.
# pyright: reportUnknownLambdaType=false, reportUnknownArgumentType=false, reportUnknownMemberType=false, reportUnknownVariableType=false, reportAttributeAccessIssue=false

from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any

import pytest

from tycho import doctor, settings
from tycho.findings import Finding
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
    assert source.describe(second) == "VKB Gladiator (2nd) button 3"  # #623: which of the two


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


def test_the_second_of_two_sticks_is_named_with_that_stick(sdl: FakeSdl) -> None:
    """#623: "joystick button 12" didn't say which of two sticks."""
    from tycho.ptt.devices import describe

    sdl.sticks[0].name, sdl.sticks[1].name = b"VKBsim Gladiator EVO L", b"VKBsim Gladiator EVO R"
    joystick.JoystickSource().close()  # as `tycho check` or the push-to-talk thread does
    second = Binding(DeviceKind.JOYSTICK, 11, f"{GUID}#2")
    assert describe(second) == "VKBsim Gladiator EVO R button 12"


def test_sticks_that_share_a_guid_are_a_check_finding(
    sdl: FakeSdl, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#623: two sticks with one GUID are told apart by plug-in order, so can swap."""

    def game(*_args: object, **_kwargs: object) -> Iterator[Finding]:
        yield from ()
        return None

    monkeypatch.setattr(doctor, "game_findings", game)
    joystick.JoystickSource().close()
    current = settings.Settings(push_to_talk=f"joystick:{GUID}#2:3")
    found = [f for f in doctor.checks(current, quick=True, ask=False) if f.level == "warn"]
    assert any("USB ID" in f.detail and "plugged in" in f.detail for f in found)


def _stick_findings(current: settings.Settings, monkeypatch: pytest.MonkeyPatch) -> list[Finding]:
    def game(*_args: object, **_kwargs: object) -> Iterator[Finding]:
        yield from ()
        return None

    monkeypatch.setattr(doctor, "game_findings", game)
    found = doctor.checks(current, quick=True, ask=True)
    return [f for f in found if f.what.endswith("stick")]


def test_tycho_check_opens_the_sticks_itself_to_find_twins(
    sdl: FakeSdl, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Review of #706 (#710): `tycho check` sees the twins through look_at_sticks().
    current = settings.Settings(push_to_talk=f"joystick:{GUID}#2:2")
    [finding] = _stick_findings(current, monkeypatch)
    assert finding.level == "warn"
    assert "VKB Gladiator" in finding.detail
    assert sdl.closed == 3  # opened, and every stick let go again


def test_tycho_check_goes_on_when_the_sticks_wont_start(
    sdl: FakeSdl, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sdl, "SDL_Init", lambda _flags: -1)
    current = settings.Settings(push_to_talk=f"joystick:{GUID}#2:2")
    assert _stick_findings(current, monkeypatch) == []  # the plain names, no swap note
