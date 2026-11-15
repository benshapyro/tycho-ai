"""Home's live part (ADR 17, amended 15 Nov 2026): whether Tycho is listening, and the
last exchanges, read only. The exchanges come from memory (``runtime.recent``): the
bridge hands them to the page and nowhere else."""

from dataclasses import asdict

from tycho import settings
from tycho.desktop.bridge.settings_page import _shown  # pyright: ignore[reportPrivateUsage]
from tycho.desktop.bridge.state import State


class HomePage(State):
    def recent(self, button: bool = True) -> dict[str, object]:
        """``listening``: True, False (paused from the tray), or None when no Tycho is
        running behind the window (setup); ``button``: push-to-talk by its name;
        ``exchanges``: the last ones, newest first, each with ``at`` (seconds since
        1970), ``said``, ``did`` and ``ok``. Home's poll asks without ``button``, so it
        reads only memory, not settings.toml."""
        name: object = ""
        if button:
            try:
                name = _shown("push_to_talk", settings.load(self._path).push_to_talk)
            except settings.SettingsError:
                name = ""
        return {
            "listening": self._listening(),
            "button": name or "",
            "exchanges": [asdict(e) for e in self._recent()],
        }
