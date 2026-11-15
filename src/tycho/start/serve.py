"""Running the session until it's stopped, rereading the keybinds as the player changes
them in game. Out of ``app.py`` (#398)."""

import asyncio
import logging
from collections.abc import Callable
from typing import TYPE_CHECKING

from tycho.packs import GameKeys

if TYPE_CHECKING:
    from tycho.runtime import Session

log = logging.getLogger("tycho")

KEYBIND_REFRESH_SECONDS = 2.0
"""How often to look for keybinds the player changed in game."""


async def serve(
    session: Session, store: GameKeys | None, stop: asyncio.Event | None = None
) -> None:
    """Run the session until ``stop``, Ctrl+C, or the console window closing. ``store``
    is reread now and then, if the pack has keybinds."""
    from tycho.runtime.console import CloseGuard

    stop = stop or asyncio.Event()
    loop = asyncio.get_running_loop()
    refresher = asyncio.create_task(
        _refresh_keybinds(store, session.warm_brain) if store is not None else stop.wait()
    )

    def request_stop() -> None:
        loop.call_soon_threadsafe(stop.set)

    with CloseGuard(request_stop):
        try:
            await session.run(stop)
        finally:
            refresher.cancel()
            await asyncio.gather(refresher, return_exceptions=True)


async def _refresh_keybinds(store: GameKeys, changed: Callable[[], None]) -> None:
    """Reread the keybinds now and then; ``changed`` is called when they change, to warm
    the brain's cache for the new briefing (#60), once they've settled: the game writes
    the file once per rebind, so a player rebinding several keys would warm it each time
    (#352). The new keys are used at once either way."""
    last_error: str | None = None
    settling = False
    while True:
        await asyncio.sleep(KEYBIND_REFRESH_SECONDS)
        if await asyncio.to_thread(store.refresh):
            settling = True
        elif settling:
            settling = False
            log.info("Keybinds changed in game; using the new ones")
            changed()
        if store.last_error and store.last_error != last_error:
            log.warning(
                "Couldn't reread keybinds (%s); keeping the last good ones", store.last_error
            )
        last_error = store.last_error
