"""Speaking up without being asked (ADR 14's amendment of 22 November 2026, #4): a small
stage beside the session that watches the game's log and says a short fixed line when
something the player chose to hear about happens.

The rules, each tested:
- Nothing until the player turns kinds on (the ``callouts`` setting), and only those.
- Only what the log records, in the pack's fixed line. A line may carry one short name
  from the log, cleaned and cut; nothing from the log reaches the brain this way.
- It only speaks: no key, no "confirm", no brain call.
- At most one every ``GAP_SECONDS``. What happens in between is dropped, not queued.
- Never while the player holds push-to-talk or a request runs, while Tycho talks or waits
  for "confirm", or within ``QUIET_SECONDS`` of any of those. What happens then is
  dropped too. Push-to-talk stops a callout like any line.
- What happened before the player turned a kind on, or before Tycho started, or more
  than ``STALE_SECONDS`` ago by the game's own time, is old news: never said.
- Each callout, and each one dropped, goes in the log with its kind.

The log is read every ``READ_SECONDS`` only while a kind is on; the busy check, which
is in memory, every ``TICK_SECONDS``.
"""

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable, Iterable, Sequence
from datetime import UTC, datetime

from tycho.callouts import turned_on
from tycho.packs.callouts import Callout
from tycho.runtime.ports import GameState
from tycho.situation import Logged

log = logging.getLogger(__name__)

GAP_SECONDS = 30.0
"""At most one callout this often (the amendment)."""
QUIET_SECONDS = 4.0
"""How long after the player or Tycho last spoke, or a question ended, before Tycho may
speak up: "a few seconds after the player last spoke"."""
READ_SECONDS = 1.0
TICK_SECONDS = 0.25
STALE_SECONDS = 60.0
"""An event the game logged longer ago than this is old news, whatever its number: a
read that starts the file again after an error numbers old lines afresh (review of
#546). The game and Tycho run on the same PC, so they share its clock."""
NAME_LIMIT = 40
"""The longest name from the log a line may carry."""


class Callouts:
    def __init__(
        self,
        game_state: GameState,
        offered: Sequence[Callout],
        on: Iterable[str],
        *,
        busy: Callable[[], bool],
        say: Callable[[str], None],
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        utc: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        """``busy``: whether speaking now would talk over someone (``Session.busy``).
        ``say``: speak up (``Session.call_out``). ``on``: the kinds turned on."""
        self._game_state = game_state
        self._by_logged = {logged: c for c in offered for logged in c.lines}
        self._offered = frozenset(c.id for c in offered)
        self._on = frozenset(on) & self._offered
        self._busy, self._say, self._clock, self._sleep = busy, say, clock, sleep
        self._utc = utc
        self._seen: int | None = None
        """The newest event's number already looked at; None: nothing read yet."""
        self._busy_at = -QUIET_SECONDS
        self._said_at = -GAP_SECONDS
        self._read_at = -READ_SECONDS

    def turn_on(self, value: object) -> None:
        """The ``callouts`` setting changed (by voice or in the window): from now on.
        Safe from any thread."""
        on = turned_on(str(value)) & self._offered
        if on - self._on:
            self._seen = None  # what happened before a kind was turned on is old news
        self._on = on
        log.info("Callouts: %s", ", ".join(sorted(on)) or "off")

    async def run(self) -> None:
        """Watch until cancelled. A failure is logged and the watch goes on."""
        while True:
            try:
                await self.step()
            except Exception:
                log.exception("Callouts failed; trying again")
            await self._sleep(TICK_SECONDS)

    async def step(self) -> None:
        """One look: note whether anyone is busy, and read the log if it's time."""
        now = self._clock()
        if self._busy():
            self._busy_at = now
        on = self._on
        if not on:
            self._seen = None
            return
        if now - self._read_at < READ_SECONDS:
            return
        self._read_at = now
        situation = await asyncio.to_thread(self._game_state.situation)
        first, seen = self._seen is None, self._seen or 0
        self._seen = max([seen, *(e.number for e in situation.recent)])
        if first:
            return
        today = self._utc()
        fresh = [
            e
            for e in situation.recent
            if e.number > seen and (e.at is None or (today - e.at).total_seconds() <= STALE_SECONDS)
        ]
        wanted = [e for e in fresh if (c := self._by_logged.get(e.kind)) and c.id in on]
        for event in wanted[:-1]:
            self._dropped(event, "a newer one came with it")
        if wanted:
            self._call_out(wanted[-1], self._clock())

    def _call_out(self, event: Logged, now: float) -> None:
        callout = self._by_logged[event.kind]
        if self._busy() or now - self._busy_at < QUIET_SECONDS:
            return self._dropped(event, "someone was talking")
        if now - self._said_at < GAP_SECONDS:
            return self._dropped(event, f"one was said in the last {GAP_SECONDS:g} s")
        line = callout.lines[event.kind]
        if "{name}" in line:
            name = " ".join(event.name.split())[:NAME_LIMIT].strip()
            if not name:
                return self._dropped(event, "the log gave no name for its line")
            line = line.replace("{name}", name)
        self._said_at = now
        log.info("Callout %s (%s): %s", callout.id, event.kind, line)
        self._say(line)

    def _dropped(self, event: Logged, why: str) -> None:
        log.info("Callout %s not said: %s", self._by_logged[event.kind].id, why)
