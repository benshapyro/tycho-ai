import threading

import pytest

from tests.fakes.fake_keys import FakeClock, FakeFocus, FakeKeySender
from tycho.game.keybinds import Action, ActivationMode, KeyInput
from tycho.input import NotPressable, Presser, UnsupportedKey, code_for, plan

MODES = {
    "tap": ActivationMode("tap", on_press=False, on_release=True, release_threshold=0.25),
    "press": ActivationMode("press"),
    "delayed_press": ActivationMode("delayed_press", press_threshold=0.25),
    "delayed_press_long": ActivationMode("delayed_press_long", press_threshold=1.5),
    "double_tap": ActivationMode("double_tap", multi_tap=2),
    "hold": ActivationMode("hold", on_release=True),
}


def _action(
    keys: str = "n", activation: str = "", label: str = "", mouse: str | None = None
) -> Action:
    inputs = [KeyInput.parse(k) for k in keys.split(",") if k] + (
        [KeyInput.parse(mouse)] if mouse else []
    )
    return Action(
        name="v_test",
        actionmap="m",
        label=label,
        inputs=tuple(i for i in inputs if i is not None),
        activation=activation,
    )


# --- Planning -------------------------------------------------------------------------


def test_modifiers_come_first_and_the_main_key_last() -> None:
    press = plan(_action("ralt+r"), MODES)
    assert press.keys == (code_for("ralt"), code_for("r"))
    assert press.keys[0].extended  # right Alt is an extended key


@pytest.mark.parametrize(
    ("activation", "label", "seconds"),
    [
        ("tap", "", 0.06),  # well inside the 0.25 s tap window
        ("press", "", 0.06),
        ("", "", 0.06),
        ("delayed_press", "", 0.37),  # past the 0.25 s threshold, with margin
        ("delayed_press_long", "", 1.62),
        ("", "Engines - Set to Max (Hold)", 0.6),
        ("hold", "Boost", 0.6),  # only acts while held, so a blip would do nothing
    ],
)
def test_hold_time_follows_the_activation_mode(activation: str, label: str, seconds: float) -> None:
    assert plan(_action("f6", activation, label), MODES).hold_seconds == pytest.approx(seconds)


def test_a_requested_hold_is_used_for_hold_actions_but_never_breaks_a_tap() -> None:
    assert plan(_action("x", "hold"), MODES, hold_seconds=2.0).hold_seconds == 2.0
    assert plan(_action("x", "tap"), MODES, hold_seconds=2.0).hold_seconds == pytest.approx(0.06)


def test_double_tap_actions_press_twice() -> None:
    assert plan(_action("capslock", "double_tap"), MODES).taps == 2


def test_keyboard_is_preferred_over_the_mouse() -> None:
    assert plan(_action("f", mouse="mouse4"), MODES).main == code_for("f")
    assert plan(_action("", mouse="mouse4"), MODES).main == code_for("mouse4")


def test_an_unbound_action_is_not_pressable() -> None:
    with pytest.raises(NotPressable, match="no key"):
        plan(_action(""), MODES)


def test_symbol_key_names_are_understood() -> None:
    assert code_for("]") == code_for("rbracket")


def test_unknown_keys_are_refused() -> None:
    with pytest.raises(UnsupportedKey):
        code_for("hmd_thing")


def test_wheel_notches_are_not_held() -> None:
    assert plan(_action("mwheel_up"), MODES).hold_seconds == 0.0


# --- Pressing -------------------------------------------------------------------------


def _presser(sender: FakeKeySender, focus: FakeFocus | None = None) -> tuple[Presser, FakeClock]:
    clock = FakeClock()
    return Presser(sender, focus or FakeFocus(True), sleep=clock.sleep, clock=clock), clock


def test_a_chord_is_pressed_in_order_and_released_in_reverse() -> None:
    sender = FakeKeySender()
    presser, clock = _presser(sender)
    receipt = presser.press(plan(_action("lalt+n", "tap"), MODES))
    lalt, n = code_for("lalt"), code_for("n")
    assert sender.events == [("down", lalt), ("down", n), ("up", n), ("up", lalt)]
    assert receipt.done
    assert clock.now == pytest.approx(0.06)


def test_double_tap_presses_twice_with_a_gap() -> None:
    sender = FakeKeySender()
    presser, clock = _presser(sender)
    receipt = presser.press(plan(_action("capslock", "double_tap"), MODES))
    assert [kind for kind, _ in sender.events] == ["down", "up", "down", "up"]
    assert receipt.taps_done == 2
    assert clock.now == pytest.approx(0.06 + 0.08 + 0.06)


def test_nothing_is_pressed_unless_the_game_has_focus() -> None:
    sender = FakeKeySender()
    presser, _ = _presser(sender, FakeFocus(False))
    receipt = presser.press(plan(_action("l"), MODES))
    assert receipt.outcome == "no_focus"
    assert sender.events == []


def test_losing_focus_between_taps_stops_cleanly() -> None:
    sender = FakeKeySender()
    presser, _ = _presser(sender, FakeFocus(True, False))
    receipt = presser.press(plan(_action("capslock", "double_tap"), MODES))
    assert receipt.outcome == "no_focus"
    assert receipt.taps_done == 1
    assert sender.held() == set()


def test_cancelling_mid_hold_releases_every_key() -> None:
    cancel = threading.Event()
    sender = FakeKeySender()
    presser, clock = _presser(sender)
    # Cancel as soon as the main key goes down (the second event).
    sender.on_event = lambda: cancel.set() if len(sender.events) == 2 else None
    receipt = presser.press(plan(_action("lalt+f6", "", "(Hold)"), MODES), cancel)
    assert receipt.outcome == "cancelled"
    assert sender.held() == set()
    assert clock.now < 0.05  # stopped promptly, not after the full 0.6 s


def test_an_error_from_windows_still_releases_what_was_pressed() -> None:
    sender = FakeKeySender(fail_on=1)  # the main key fails after the modifier went down
    presser, _ = _presser(sender)
    receipt = presser.press(plan(_action("ralt+r"), MODES))
    assert receipt.outcome == "failed"
    assert sender.held() == set()


def test_wheel_actions_send_one_notch() -> None:
    sender = FakeKeySender()
    presser, _ = _presser(sender)
    presser.press(plan(_action("mwheel_down"), MODES))
    assert sender.events == [("wheel", -1)]
