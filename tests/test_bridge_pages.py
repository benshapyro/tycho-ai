"""The bridge split by page (#399): still one object, and still exactly what the page
calls, nothing more."""

import re
from pathlib import Path

from tycho.desktop import bridge
from tycho.desktop.bridge import Bridge

PAGE = Path(bridge.__file__).resolve().parents[1]


def test_the_page_calls_exactly_what_the_bridge_offers() -> None:
    # pywebview hands the page every public attribute: one too many is a way in.
    called = {
        name
        for path in PAGE.rglob("*")
        if path.suffix in {".js", ".html"}
        for name in re.findall(r"\bapi\.([a-z_]+)\(", path.read_text(encoding="utf-8"))
    }
    offered = {name for name in dir(Bridge) if not name.startswith("_")}
    assert called == offered


def test_each_page_has_a_module_of_its_own() -> None:
    # #399: bridge.py was 708 lines; now a package, its calls grouped by page.
    from tycho.desktop.bridge.commands_page import CommandsPage
    from tycho.desktop.bridge.history_page import HistoryPage
    from tycho.desktop.bridge.memory_page import MemoryPage
    from tycho.desktop.bridge.settings_page import SettingsPage
    from tycho.desktop.bridge.setup_page import SetupPage
    from tycho.desktop.bridge.tile_page import TilePage
    from tycho.desktop.bridge.updates_page import UpdatesPage

    assert Bridge.set is SettingsPage.set
    assert Bridge.commands is CommandsPage.commands
    assert Bridge.finish_setup is SetupPage.finish_setup
    assert Bridge.tile_preview is TilePage.tile_preview
    assert Bridge.check_updates is UpdatesPage.check_updates
    assert Bridge.forget_note is MemoryPage.forget_note
    assert Bridge.delete_history is HistoryPage.delete_history
