"""Running Tycho with a tray icon (ADR 17): the voice loop on its own thread, the main
thread free for the window.

pywebview must run on the main thread, so the voice loop (asyncio: push-to-talk,
speech, the brain, the keys) runs on a thread of its own, unchanged. The main thread
waits for what the tray asks for ("open the window", "quit") and for Ctrl+C. Quitting
sets the voice loop's ``stop`` event from the outside, exactly as Ctrl+C and the
console closing do, so it lets go of any key and shuts down the same way.
"""

import asyncio
import concurrent.futures
import contextlib
import logging
import queue
import sys
import threading
from collections.abc import Callable, Coroutine
from typing import Any

log = logging.getLogger(__name__)

type Voice = Callable[[asyncio.Event], Coroutine[Any, Any, None]]
"""The voice loop: runs until the event is set."""

POLL_SECONDS = 0.2
"""How often the main thread wakes, so Ctrl+C is felt at once."""
APPLY_SECONDS = 2.0
"""How long a change made in the window waits for the voice loop to apply it."""
CLOSE_SECONDS = 0.5
"""How long a restart waits before closing the window: the page's call that asked for
it returns first."""


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
        self.window_open: Callable[[], bool] = lambda: False
        self.focus_window: Callable[[], None] = lambda: None
        self.restarting = False
        """The voice loop stopped to start again, with the settings as saved (#295)."""

    # From any thread: the tray, or tests.

    def ask(self, request: str) -> None:
        """Ask the main thread to ``"open"`` the window or ``"quit"``."""
        if request == "open" and self.window_open():
            self.focus_window()  # already open: bring it forward, don't queue another
            return
        self._requests.put(request)
        if request == "quit":
            self.on_quit()  # the main thread may be inside the window: close it

    def restart(self) -> bool:
        """Stop the voice loop so Tycho starts it again, as after a new game is picked
        (#295). It stops as quitting does, letting go of any key; the window closes, and
        opens again once the new loop is listening. False if nothing is running."""
        if not self._thread.is_alive():
            return False
        self.restarting = True
        self._requests.put("restart")
        threading.Timer(CLOSE_SECONDS, self.on_quit).start()
        return True

    def call(self, fn: Callable[[], None]) -> None:
        """Run ``fn`` on the voice loop's thread, e.g. pausing the session."""
        if self._loop is not None:
            self._loop.call_soon_threadsafe(fn)

    def run_now(self, fn: Callable[[], None], seconds: float = APPLY_SECONDS) -> bool:
        """Run ``fn`` on the voice loop's thread and wait for it. False if the loop isn't
        running; raises what ``fn`` raised, so a caller can say it didn't apply."""
        loop = self._loop
        if loop is None or loop.is_closed() or not self._thread.is_alive():
            return False
        done: concurrent.futures.Future[None] = concurrent.futures.Future()

        def run() -> None:
            try:
                fn()
            except BaseException as e:
                done.set_exception(e)
            else:
                done.set_result(None)

        loop.call_soon_threadsafe(run)
        done.result(timeout=seconds)
        return True

    # The main thread.

    def run(self, open_window: Callable[[], object] = lambda: None) -> None:
        """Start the voice loop and serve the tray's requests until Tycho stops.
        Re-raises anything that ended the voice loop."""
        self._thread.start()
        try:
            with _ctrl_c(lambda: self.ask("quit")):
                self._started.wait(10.0)
                while self._thread.is_alive():
                    try:
                        request = self._requests.get(timeout=POLL_SECONDS)
                    except queue.Empty:
                        continue
                    if request in ("quit", "restart"):
                        self.stop()
                    elif request == "open":
                        self._drop_queued("open")  # clicks while it was opening: one window
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

    def _drop_queued(self, request: str) -> None:
        kept: list[str] = []
        while True:
            try:
                queued = self._requests.get_nowait()
            except queue.Empty:
                break
            if queued != request:
                kept.append(queued)
        for queued in kept:
            self._requests.put(queued)


def _ctrl_c(quit_now: Callable[[], None]) -> contextlib.AbstractContextManager[object]:
    """Ctrl+C while the main thread is inside the window: Python would only feel it once
    the window closed, so Windows' own thread asks Tycho to quit at once."""
    if sys.platform != "win32":
        return contextlib.nullcontext()
    from tycho.runtime.console import CtrlCGuard

    return CtrlCGuard(quit_now)
