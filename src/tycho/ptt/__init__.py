"""Push-to-talk on any keyboard key, mouse button or joystick button."""

from tycho.ptt.binding import Binding, DeviceKind
from tycho.ptt.monitor import Edge, InputSource, PushToTalk, capture_binding

__all__ = ["Binding", "DeviceKind", "Edge", "InputSource", "PushToTalk", "capture_binding"]
