"""A PC for setup, scripted. Satisfies ``tycho.desktop.setup.SetupPC``."""

import threading

from tycho.desktop.setup import Game
from tycho.settings import Settings

FOUND = Game(True, "LIVE", r"C:\Games\StarCitizen\LIVE")
MISSING = Game(False, "LIVE", problem="Star Citizen LIVE wasn't found")


class FakeSetupPC:
    def __init__(self) -> None:
        self.button: str | None = "mouse:0x06"
        self.clashes: list[str] = []
        self.devices = ["Headset, MME", "Desk mic, MME"]
        self.heard: dict[str, object] = {
            "ok": True,
            "name": "Headset",
            "peak": 0.3,
            "verdict": "good",
        }
        self.calls: list[str] = []
        self.wait_for_cancel = False
        self.cancel: threading.Event | None = None
        self.listened: list[str | None] = []

    def game(self, current: Settings) -> Game:
        self.calls.append("game")
        return FOUND if current.game_root or current.game_channel == "PTU" else MISSING

    def root(self, folder: str, channel: str) -> str | None:
        return folder.rstrip("\\") if "Star" in folder else None

    def capture(self, seconds: float, cancel: threading.Event) -> str | None:
        self.cancel = cancel
        if self.wait_for_cancel:
            cancel.wait(5)
            return None
        return self.button

    def name(self, setting: str) -> str:
        return "Mouse side (forward)"

    def conflicts(self, setting: str, current: Settings) -> list[str]:
        self.calls.append("conflicts")
        return list(self.clashes)

    def microphones(self) -> list[str]:
        self.calls.append("microphones")
        return list(self.devices)

    def level(self, microphone: str | None) -> dict[str, object]:
        self.listened.append(microphone)
        return dict(self.heard)
