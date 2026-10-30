import pytest

from tycho.ptt.binding import Binding, DeviceKind


@pytest.mark.parametrize(
    "binding",
    [
        Binding(DeviceKind.KEYBOARD, 0x7C),
        Binding(DeviceKind.MOUSE, 0x05),
        Binding(DeviceKind.JOYSTICK, 3, "03000000231d00000201000000000000"),
        Binding(DeviceKind.JOYSTICK, 0, "03000000231d00000201000000000000#2"),
    ],
)
def test_round_trips_through_text(binding: Binding) -> None:
    assert Binding.parse(str(binding)) == binding


def test_text_form_is_readable() -> None:
    assert str(Binding(DeviceKind.MOUSE, 0x05)) == "mouse:0x05"


def test_parse_is_case_and_space_tolerant() -> None:
    assert Binding.parse(" Keyboard:0x7c ") == Binding(DeviceKind.KEYBOARD, 0x7C)


@pytest.mark.parametrize(
    "text",
    ["gamepad:0x01", "keyboard:", "keyboard:zz", "joystick:3", "joystick::3", "mouse"],
)
def test_rejects_malformed_text(text: str) -> None:
    with pytest.raises(ValueError, match="binding"):
        Binding.parse(text)


def test_joystick_bindings_need_a_device() -> None:
    with pytest.raises(ValueError, match="device"):
        Binding(DeviceKind.JOYSTICK, 1)


def test_keyboard_bindings_must_not_have_a_device() -> None:
    with pytest.raises(ValueError, match="device"):
        Binding(DeviceKind.KEYBOARD, 1, "x")


def test_codes_must_be_non_negative() -> None:
    with pytest.raises(ValueError, match="non-negative"):
        Binding(DeviceKind.MOUSE, -1)
