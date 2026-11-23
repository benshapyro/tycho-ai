"""Home's live part (ADR 17, amended 15 Nov 2026): whether Tycho is listening, and the
last exchanges, read only. The exchanges come from memory (``runtime.recent``): the
bridge hands them to the page and nowhere else."""

import time
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
                current = settings.load(self._path)
                name = " or ".join(
                    str(_shown(n, getattr(current, n)))
                    for n in ("push_to_talk", "push_to_talk_2")
                    if getattr(current, n)
                )
            except settings.SettingsError:
                name = ""
        return {
            "listening": self._listening(),
            "button": name or "",
            "exchanges": [asdict(e) for e in self._recent()],
        }

    def try_it(self, on: object) -> dict[str, object]:
        """Setup's Try it (ADR 17): on, the running Tycho plans every press as usual
        and sends none, and Home's exchanges say what it would have pressed. ``ok``:
        False while no Tycho is running (a first run's setup). ``since``: from when,
        in seconds since 1970, so the page shows only what was said while trying."""
        if not isinstance(on, bool):
            return {"ok": False, "problem": "Try it is on or off."}
        return {"ok": self._trying(on), "since": time.time()}
