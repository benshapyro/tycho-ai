"""About, the status checks and updates: whether a newer Tycho is out, and opening its
release page. Updates are checked, never installed (#9)."""

import logging
import time
from collections.abc import Mapping

from tycho import __version__, settings, updates
from tycho.desktop.bridge.state import RELEASES_PAGE, UPDATE_SECONDS, State
from tycho.settings import SettingsError

log = logging.getLogger(__name__)


class UpdatesPage(State):
    def about(self) -> dict[str, object]:
        """``first_run``: Tycho is waiting for setup before it starts listening."""
        return {
            "version": __version__,
            "first_run": self._first_run,
            "can_quit": self._quit is not None,
        }

    def quit(self) -> dict[str, object]:
        """Stop Tycho, as Quit on the tray's menu does (#424): the icon can be hidden
        among Windows' other icons, or gone. Any held key is let go first."""
        if self._quit is None:
            return {"ok": False, "problem": "Tycho isn't listening yet, so close this window."}
        log.info("Quit from the window")
        self._quit()
        return {"ok": True}

    def status(self) -> list[Mapping[str, str]]:
        """The same checks as ``tycho check``, quick ones only."""
        try:
            return list(self._findings())
        except Exception as e:  # a broken check must not break the page
            log.exception("The status checks failed")
            return [{"level": "fail", "what": "Checks", "detail": f"couldn't run: {e}", "fix": ""}]

    def check_updates(self, asked: object = True) -> dict[str, object]:
        """Is a newer Tycho out? ``asked`` is the player pressing Check for updates, so
        GitHub is asked even with ``check_for_updates`` off; the window's own check when
        it opens (``asked`` false) honours the setting, and is kept for an hour.

        ``state`` is ``newer``, ``latest``, ``none`` (no release yet), ``off`` or
        ``error``; Tycho never updates itself (#9)."""
        current = __version__
        if asked is not True:
            try:
                if not settings.load(self._path).check_for_updates:
                    return {"state": "off", "current": current}
            except SettingsError:
                return {"state": "off", "current": current}
        now = time.monotonic()
        try:
            if asked is not True and self._checked and now - self._checked[0] < UPDATE_SECONDS:
                release = self._checked[1]  # an hour since the last check, not the clock's
            else:
                release = self._latest_release()
                self._checked = (now, release)
        except updates.RateLimited as e:
            log.debug("GitHub wouldn't say if a newer Tycho is out: %s", e)
            return {
                "state": "error",
                "current": current,
                "problem": "GitHub didn't answer. Try again later.",
            }
        except updates.UpdateError as e:
            log.debug("Couldn't check for a newer Tycho: %s", e)
            return {
                "state": "error",
                "current": current,
                "problem": "Couldn't reach GitHub to check. Is the PC online? Try again later.",
            }
        if release is None:
            return {"state": "none", "current": current}
        if updates.is_newer(release.version, current):
            self._release = release
            return {"state": "newer", "current": current, "latest": release.version}
        return {"state": "latest", "current": current, "latest": release.version}

    def open_release(self) -> dict[str, object]:
        """Open the newer release's page (its installer is there) in the player's browser.
        Only a page of Tycho's own releases: anything else opens the list of releases."""
        found = self._release.url if self._release is not None else ""
        url = found if found.startswith(RELEASES_PAGE + "/") else RELEASES_PAGE
        try:
            self._open_page(url)
        except Exception as e:
            log.warning("Couldn't open the browser: %s", e)
            return {"ok": False, "problem": f"Couldn't open the browser. The page is {url}"}
        return {"ok": True, "url": url}
