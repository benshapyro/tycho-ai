"""Getting the brain ready as push-to-talk goes down (#21).

The player holds the button for a second or more before the brain is asked. Two waits
can be paid in that time instead of after it:

- **The connection.** The HTTP client closes a connection after 5 s idle, and in play
  requests are minutes apart, so nearly every request opened a new one (DNS, TCP and
  TLS: about 0.1-0.3 s from a home PC). A free, tiny request opens it while the player
  talks, and the brain's request uses it.
- **The cache.** The briefing is cached for an hour (``claude``'s docstring). After a
  longer break the first request wrote it again, about 3 s slower. Past
  ``CACHE_STALE_SECONDS`` the press writes it again instead, as the start-up warm-up
  does: the same request, billed as a cache write and no output.

``prime`` never blocks and never raises: it starts one task, and a failure is logged.
"""

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable

log = logging.getLogger(__name__)

IDLE_SECONDS = 4.0
"""A connection idle this long may be closed by the time the request goes (httpx closes
them at 5 s), so a press opens one."""
CACHE_STALE_SECONDS = 50 * 60.0
"""The cache lasts an hour from its last use: past this, a press writes it again."""
SETTLE_SECONDS = 2.0
"""How long a request waits for a cache write a press started (#533). Claude serves the
cache only once the write has begun answering, so a request sent alongside it misses and
writes it again. The write began a second or more before the request, at the press."""


def now() -> float:
    """The clock priming goes by (tests set their own)."""
    return time.monotonic()


class Primer:
    """Remembers when the brain last talked to its provider, and on a press opens the
    connection (``connect``) or writes the cache again (``rewarm``), whichever is due.
    ``rewarm`` returns False when there's nothing to warm yet (no keybinds briefed)."""

    def __init__(
        self,
        connect: Callable[[], Awaitable[None]],
        rewarm: Callable[[], Awaitable[bool]],
        clock: Callable[[], float] | None = None,
    ) -> None:
        self._connect = connect
        self._rewarm = rewarm
        self._clock = clock or (lambda: now())
        self._talked = -float("inf")
        """When the connection was last used."""
        self._cached = -float("inf")
        """When the cache was last read or written."""
        self._task: asyncio.Task[None] | None = None
        self._writing = False
        """``_task`` writes the cache (rather than opening a connection)."""
        self._busy = 0
        """Warm-ups running outside priming (the start-up one): a press waits for them."""
        self._warmups: set[asyncio.Future[None]] = set()
        """Each running ``warming``'s end, which a request waits for as it does a press's
        write (#540)."""

    def used(self, *, cache: bool = True) -> None:
        """A request to the provider just went, or one ended with an answer: the
        connection is open, and (unless ``cache`` is False) the cache's hour starts
        again. A request that failed or was cancelled reads no cache (#533)."""
        now = self._clock()
        self._talked = now
        if cache:
            self._cached = now

    def prime(self) -> None:
        """Push-to-talk went down: start getting ready, if anything's due. Runs on the
        event loop."""
        if self._busy or (self._task is not None and not self._task.done()):
            return
        now = self._clock()
        if now - self._cached > CACHE_STALE_SECONDS:
            work, what = self._rewarm_cache, "write the cache"
        elif now - self._talked > IDLE_SECONDS:
            work, what = self._open, "open the connection"
        else:
            return
        self._writing = work == self._rewarm_cache
        self._task = asyncio.get_running_loop().create_task(self._run(work, what))

    async def warming[T](self, warm_up: Callable[[], Awaitable[T]]) -> T:
        """Run a warm-up from outside priming (at start, or after the keybinds change):
        a press meanwhile starts nothing, so the cache isn't written twice (#533). Only
        the cache write: the look-ups' loading, which can take minutes after a patch,
        leaves presses free to open the connection."""
        self._busy += 1
        done = asyncio.get_running_loop().create_future()
        self._warmups.add(done)
        try:
            return await warm_up()
        finally:
            self._busy -= 1
            self._warmups.discard(done)
            done.set_result(None)

    async def going(self, seconds: float = SETTLE_SECONDS) -> None:
        """A request is about to go. It first waits up to ``seconds`` for a cache write
        a press started (#533), or the start-up or keybinds warm-up (#540), so it reads
        the cache instead of writing it again; then its connection counts as used. Call
        ``used`` once its first answer starts arriving (#540), and again when it has its
        answer."""
        writes: set[asyncio.Future[None]] = set(self._warmups)
        task = self._task
        if task is not None and not task.done() and self._writing:
            writes.add(task)
        if writes:
            await asyncio.wait(writes, timeout=seconds)  # never cancels them
        self.used(cache=False)

    async def _rewarm_cache(self) -> None:
        if await self._rewarm():
            self.used()

    async def _open(self) -> None:
        await self._connect()
        self.used(cache=False)

    @staticmethod
    async def _run(work: Callable[[], Awaitable[None]], what: str) -> None:
        try:
            await work()
        except Exception as e:  # the request itself will say what's wrong
            log.debug("Couldn't %s for the brain ahead of a request: %s", what, e)

    async def close(self) -> None:
        """Stop a priming still running."""
        if self._task is not None and not self._task.done():
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
