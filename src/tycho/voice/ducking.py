"""Turning the game down while Tycho speaks (#570).

Engine noise and combat drowned Tycho out. While a line plays, the game's own volume in
Windows' mixer (the same per-app slider a player can move by hand: ADR 3, nothing in the
game) goes down to ``duck_level`` of where the player left it, then fades back up. A game
can have several sliders (one per sound device, say): each keeps its own level (#578).

The rules, each tested against ``GameVolume``'s fake:

- **Only lower, and always put it back.** The level the player left is saved to disk
  (``paths.ducked_file``) before anything is lowered, and put back when the line ends,
  when push-to-talk stops Tycho, on quit, and at the next start after a crash. Windows
  remembers an app's level after it closes, so a level that couldn't be put back (the
  game had closed) waits on disk for the next chance.
- **The player's own change wins.** If a slider isn't where Tycho left it, the player
  moved it: Tycho forgets that slider's saved level and leaves theirs.
- **Fades,** so nothing clicks: ``DOWN_SECONDS`` down, ``UP_SECONDS`` back up. Lines
  close together (the filler, then the answer) keep it down for ``HOLD_SECONDS`` between.
- **Never in the way.** The line never waits for the fade, and a mixer that fails is
  logged once (until it works again) and left alone.
"""

import asyncio
import contextlib
import json
import logging
from collections.abc import Awaitable, Callable, Coroutine, Mapping
from pathlib import Path
from typing import Protocol

from tycho.runtime.ports import Speaker

log = logging.getLogger(__name__)

DOWN_SECONDS = 0.15
UP_SECONDS = 0.4
HOLD_SECONDS = 0.3
"""After a line ends, how long the game stays down in case another line follows."""
STEP_SECONDS = 0.03
"""How often a fade moves the slider."""
MOVED = 0.02
"""A slider further than this from where Tycho left it was moved by the player."""


type Levels = dict[str, float]
"""Each of the game's sliders in the mixer, by a name that stays the same while the game
restarts, and its level from 0 to 1. ``ALL`` in levels to set means every slider."""

ALL = "*"


class GameVolume(Protocol):
    """The game's own volume in Windows' mixer (``voice/mixer.py`` on Windows; a fake in
    tests). Both block briefly: call them off the event loop."""

    def get(self) -> Levels:
        """Each of the game's sliders now; empty if it has no sound (it isn't running)."""
        ...

    def set(self, levels: Mapping[str, float]) -> bool:
        """Set the sliders named. False if the game has none of them to set."""
        ...


def _scaled(levels: Levels, share: float) -> Levels:
    return {name: level * share for name, level in levels.items()}


class Ducker:
    """Lowers the game while any line plays (``down``, ``up``), and puts it back."""

    def __init__(
        self,
        volume: GameVolume,
        saved: Path,
        *,
        on: bool = True,
        level: float = 0.3,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._volume = volume
        self._saved = saved
        self.on = on
        self.level = level
        self._sleep = sleep
        self._lines = 0
        """Lines playing now."""
        self._original: Levels | None = None
        """The levels the player left, while Tycho has the game lowered."""
        self._left: Levels | None = None
        """Where Tycho last put the sliders."""
        self._lock = asyncio.Lock()
        """One fade at a time, in the order asked."""
        self._fades: set[asyncio.Task[None]] = set()
        """Every fade not yet done, so quitting stops them all (#579)."""
        self._failed = False
        self._stale: Levels = {}
        """Saved levels for sliders the game doesn't have now (it moved to another sound
        device): kept on disk until they can go back (#593)."""
        self._setting: asyncio.Future[bool] | None = None
        """The slider's last move, which may outlive a cancelled fade."""

    def set_on(self, value: object) -> None:
        """``duck_game`` changed by voice or in the window: off puts the game back now."""
        self.on = value is True
        if not self.on and self._lines == 0:
            self._raise_soon(0.0)

    def set_level(self, value: object) -> None:
        """``duck_level`` changed: the next line uses it."""
        if isinstance(value, int | float) and not isinstance(value, bool):
            self.level = min(max(float(value), 0.0), 1.0)

    def down(self) -> None:
        """A line starts: lower the game, without waiting."""
        self._lines += 1  # a fade up stops at its next step and leaves the rest to this
        if self.on:
            self._start(self._lower())

    def up(self, *, at_once: bool = False) -> None:
        """A line ended. ``at_once``: push-to-talk stopped it, so no holding down."""
        self._lines = max(self._lines - 1, 0)
        if self._lines == 0:
            self._raise_soon(0.0 if at_once else HOLD_SECONDS)

    def recover(self) -> None:
        """At start, before any line: put back a level a crash left lowered. Blocks
        briefly; never raises."""
        level = self._load()
        if level is None:
            return
        try:
            now = self._volume.get()
            if not now:
                return  # the game isn't running: the next line tries again
            if not self._volume.set(level):
                self._stale = level  # not these sliders: kept for when they're back
                return
        except Exception as e:
            self._failure(e)
            return
        self._stale = _missing(level, now)
        self._put_back(level)

    async def close(self) -> None:
        """Quitting: stop any fade and put the game back now."""
        for task in list(self._fades):
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        if self._setting is not None:  # a step a cancel left still moving the slider
            await asyncio.wait({self._setting})
        async with self._lock:
            if self._original is not None:
                await self._set(self._original)
                self._forget()

    def _raise_soon(self, delay: float) -> None:
        if self._original is None and not self._saved.exists():
            return
        # Never cancelled mid-fade (a step may be moving the slider): a line that starts
        # stops it between steps (#577's review).
        self._start(self._raise(delay))

    def _start(self, fade: Coroutine[object, object, None]) -> None:
        task = asyncio.get_running_loop().create_task(fade)
        self._fades.add(task)
        task.add_done_callback(self._fades.discard)

    async def _lower(self) -> None:
        async with self._lock:
            if self._lines == 0:  # the line already ended
                return
            if self._original is None:
                await self._put_back_saved()
                now = await self._get()
                if not now or max(now.values()) <= 0.0:
                    return
                self._original = now
                self._save(now)
            else:  # a line soon after the last: from wherever the fade up got to
                now = await self._get()
                if not now or not self._unmoved(now):
                    return
            await self._fade(now, _scaled(self._original, self.level), DOWN_SECONDS)
            if self._lines == 0:  # the line ended while this was getting the level
                self._raise_soon(0.0)

    async def _raise(self, delay: float) -> None:
        if delay:
            await self._sleep(delay)
        async with self._lock:
            if self._lines:
                return  # another line started: it keeps the game down
            if self._original is None:
                await self._put_back_saved()
                return
            now = await self._get()
            if not now:
                return  # the game closed: the level waits on disk for the next chance
            if not self._unmoved(now):
                return
            original = self._original
            if await self._fade(now, original, UP_SECONDS, until=lambda: self._lines > 0):
                # A slider that went while Tycho spoke waits on disk for its level (#595).
                self._stale = {**self._stale, **_missing(original, now)}
                self._forget()

    def _unmoved(self, now: Levels) -> bool:
        """Leaves out each slider the player moved while Tycho spoke: their level wins.
        Whether any slider is still Tycho's to put back."""
        if self._original is None:
            return False
        left = self._left or {}
        moved = {n for n, level in left.items() if n in now and abs(now[n] - level) > MOVED}
        if moved:
            log.info("The game's volume was changed by hand while Tycho spoke: leaving it")
            self._original = {n: v for n, v in self._original.items() if n not in moved}
            self._left = {n: v for n, v in left.items() if n not in moved}
            if not self._original:
                self._forget()
                return False
            self._save(self._original)
        return True

    async def _fade(
        self,
        start: Mapping[str, float],
        end: Levels,
        seconds: float,
        until: Callable[[], bool] = lambda: False,
    ) -> bool:
        """Move each slider in ``end`` from ``start`` to there in steps. Whether they got
        there: it stops early if the game goes, or between steps once ``until`` is true."""
        steps = max(1, round(seconds / STEP_SECONDS))
        for step in range(1, steps + 1):
            levels = {
                n: start.get(n, v) + (v - start.get(n, v)) * step / steps for n, v in end.items()
            }
            if until() or not await self._set(levels):
                return False
            if step < steps:
                await self._sleep(seconds / steps)
        return True

    async def _put_back_saved(self) -> None:
        """A level saved on disk with nothing lowered now: a crash or a closed game left
        it. Put it back if the game is running."""
        level = self._load()
        if level is None:
            return
        now = await self._get()
        if not now:
            return  # not running: try again at the next line or start
        if not await self._set(level):
            self._stale = level  # none of these sliders now: kept on disk (#593)
            return
        self._stale = _missing(level, now)  # some sliders back, the rest kept (#595)
        self._put_back(level)

    def _put_back(self, levels: Levels) -> None:
        loudest = max(levels.values())
        log.info("Put the game's volume back to %d%%, as you left it", round(loudest * 100))
        self._forget()

    async def _get(self) -> Levels:
        try:
            now = await asyncio.to_thread(self._volume.get)
        except Exception as e:
            self._failure(e)
            return {}
        return now

    async def _set(self, levels: Levels) -> bool:
        # Shielded: a cancel can't stop the thread moving the slider, so it mustn't stop
        # Tycho noting where the slider went either (#577's review).
        self._setting = asyncio.ensure_future(asyncio.to_thread(self._volume.set, levels))
        try:
            done = await asyncio.shield(self._setting)
        except asyncio.CancelledError:
            self._left = levels
            raise
        except Exception as e:
            self._failure(e)
            return False
        self._failed = False  # it works again: a later, different failure is logged
        if done:
            self._left = levels
        return done

    def _failure(self, e: Exception) -> None:
        if not self._failed:
            self._failed = True
            log.warning("Couldn't change the game's volume: %s", e)

    def _save(self, levels: Levels) -> None:
        try:
            self._saved.parent.mkdir(parents=True, exist_ok=True)
            noted = {**self._stale, **levels}
            self._saved.write_text(json.dumps({"levels": noted}), "utf-8")
        except OSError as e:
            log.warning("Couldn't note the game's volume: %s", e)

    def _load(self) -> Levels | None:
        try:
            noted = json.loads(self._saved.read_text("utf-8"))
            # {"level": x} from a build before #578: one level for every slider.
            levels = noted["levels"] if "levels" in noted else {ALL: noted["level"]}
            levels = {str(n): v for n, v in levels.items()}
        except FileNotFoundError:
            return None
        except (OSError, ValueError, KeyError, TypeError, AttributeError) as e:
            log.warning("Ignoring the noted game volume: %s", e)
            self._saved.unlink(missing_ok=True)
            return None
        if not levels or not all(_a_level(v) for v in levels.values()) or not any(levels.values()):
            self._saved.unlink(missing_ok=True)
            return None
        return {n: float(v) for n, v in levels.items()}

    def _forget(self) -> None:
        self._original = self._left = None
        if self._stale:
            self._save({})  # what couldn't go back yet stays noted
            return
        with contextlib.suppress(OSError):
            self._saved.unlink(missing_ok=True)


def _missing(levels: Levels, now: Levels) -> Levels:
    """The saved levels for sliders the game doesn't have now: they can't go back yet."""
    return {n: v for n, v in levels.items() if n != ALL and n not in now}


def _a_level(value: object) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool) and 0.0 <= value <= 1.0


class Ducking:
    """A ``Speaker`` that turns the game down while it speaks (#570)."""

    def __init__(self, inner: Speaker, ducker: Ducker) -> None:
        self._inner = inner
        self._ducker = ducker

    async def speak(self, text: str, *, keep: bool = False) -> None:
        self._ducker.down()
        stopped = False
        try:
            await self._inner.speak(text, keep=keep)
        except asyncio.CancelledError:
            stopped = True
            raise
        finally:
            self._ducker.up(at_once=stopped)

    async def warm(self) -> None:
        await self._inner.warm()

    async def close(self) -> None:
        await self._ducker.close()
        await self._inner.close()
