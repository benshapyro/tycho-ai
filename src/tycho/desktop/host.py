"""Running Tycho with a tray icon (ADR 17): the voice loop on its own thread, the main
thread free for the window.

pywebview must run on the main thread, so the voice loop (asyncio: push-to-talk,
speech, the brain, the keys) runs on a thread of its own, unchanged. The main thread
waits for what the tray asks for ("open the window", "quit") and for Ctrl+C. Quitting
sets the voice loop's ``stop`` event from the outside, exactly as Ctrl+C and the
console closing do, so it lets go of any key and shuts down the same way.
"""

import asyncio
import contextlib
import logging
import queue
import threading
from collections.abc import Callable, Coroutine
from typing import Any

log = logging.getLogger(__name__)

type Voice = Callable[[asyncio.Event], Coroutine[Any, Any, None]]
"""The voice loop: runs until the event is set."""

POLL_SECONDS = 0.2
"""How often the main thread wakes, so Ctrl+C is felt at once."""


class Host:
    def __init__(self, voice: Voice) -> None:
        self._voice = voice
        self._requests: queue.Queue[str] = queue.Queue()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._stop: asyncio.Event | None = None
        self._started = threading.Event()
        self._failure: BaseException | None = None
        self._thread = threading.Thread(target=self._run_voice, name="tycho-voice", daemon=True)
        self.windows = 0
        """How many times the window was asked for."""
        self.on_quit: Callable[[], None] = lambda: None
        """Closes the window, if it's open: Tycho is stopping."""

    # From any thread: the tray, or tests.

    def ask(self, request: str) -> None:
        """Ask the main thread to ``"open"`` the window or ``"quit"``."""
        self._requests.put(request)
        if request == "quit":
            self.on_quit()  # the main thread may be inside the window: close it

    def call(self, fn: Callable[[], None]) -> None:
        """Run ``fn`` on the voice loop's thread, e.g. pausing the session."""
        if self._loop is not None:
            self._loop.call_soon_threadsafe(fn)

    # The main thread.

    def run(self, open_window: Callable[[], None] = lambda: None) -> None:
        """Start the voice loop and serve the tray's requests until Tycho stops.
        Re-raises anything that ended the voice loop."""
        self._thread.start()
        try:
            self._started.wait(10.0)
            while self._thread.is_alive():
                try:
                    request = self._requests.get(timeout=POLL_SECONDS)
                except queue.Empty:
                    continue
                if request == "quit":
                    self.stop()
                elif request == "open":
                    self.windows += 1
                    open_window()  # runs until the player closes the window
        except KeyboardInterrupt:
            self.stop()
            raise
        finally:
            self.stop()
            # As long as it takes, as asyncio.run did: the voice loop is still using
            # the microphone, the Tile and the action log, which close after this.
            # A second Ctrl+C interrupts the wait.
            self._thread.join()
        if self._failure is not None:
            raise self._failure

    def stop(self) -> None:
        loop, stop = self._loop, self._stop
        if loop is not None and stop is not None and not loop.is_closed():
            with contextlib.suppress(RuntimeError):  # the loop closed in between
                loop.call_soon_threadsafe(stop.set)

    # The voice thread.

    def _run_voice(self) -> None:
        async def main() -> None:
            self._loop = asyncio.get_running_loop()
            self._stop = asyncio.Event()
            self._started.set()
            await self._voice(self._stop)

        try:
            asyncio.run(main())
        except BaseException as e:  # handed to the main thread, which re-raises it
            log.debug("The voice loop ended with %r", e)
            self._failure = e
        finally:
            self._started.set()
            self.on_quit()  # the voice loop ended: so does the window
