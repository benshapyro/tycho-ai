"""What the app window's page can ask of Tycho (ADR 17): the only way in, and narrow.

pywebview calls these methods from the page's JavaScript, each on a thread of its own,
with JSON values. Everything here checks what it's given, as the voice tool does, and
answers in plain words a page can show. pywebview exposes every public attribute of
this object to the page, so everything that isn't a call for the page starts with ``_``.

A change goes through ``settings.update`` and the same live callbacks as one made by
voice (``apply``), so it takes effect at once and lands in settings.toml. The app may
do what voice can't (a longer confirm time, transcripts on): its controls are the
player's own hands, not a sentence that might have been misheard.

Keys never come back out: the page sends one once, and learns only whether one is
stored and its last four characters.

Updates are checked, never installed: the page learns whether a newer Tycho is out, and
can ask for its release page to open in the browser. The page never names a web address.
"""

from tycho.desktop.bridge.home_page import HomePage
from tycho.desktop.bridge.memory_page import MemoryPage
from tycho.desktop.bridge.setup_page import SetupPage
from tycho.desktop.bridge.tile_page import TilePage
from tycho.desktop.bridge.updates_page import UpdatesPage


class Bridge(SetupPage, UpdatesPage, TilePage, HomePage, MemoryPage):
    """The page's one way in: every page's calls on one object (#399). Setup's page
    builds on the Settings and Commands pages, so they come with it."""
