"""Turning the game down while Tycho speaks (#570).

Engine noise and combat drowned Tycho out. While a line plays, the game's own volume in
Windows' mixer (the same per-app slider a player can move by hand: ADR 3, nothing in the
game) goes down to ``duck_level`` of where the player left it, then fades back up.

The rules, each tested against ``GameVolume``'s fake:

- **Only lower, and always put it back.** The level the player left is saved to disk
  (``paths.ducked_file``) before anything is lowered, and put back when the line ends,
  when push-to-talk stops Tycho, on quit, and at the next start after a crash. Windows
  remembers an app's level after it closes, so a level that couldn't be put back (the
  game had closed) waits on disk for the next chance.
- **The player's own change wins.** If the slider isn't where Tycho left it, the player
  moved it: Tycho forgets the saved level and leaves theirs.
- **Fades,** so nothing clicks: ``DOWN_SECONDS`` down, ``UP_SECONDS`` back up. Lines
  close together (the filler, then the answer) keep it down for ``HOLD_SECONDS`` between.
- **Never in the way.** The line never waits for the fade, and a mixer that fails is
  logged once and left alone.
"""

import asyncio
import contextlib
import json
import logging
from collections.abc import Awaitable, Callable
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


class GameVolume(Protocol):
    """The game's own volume in Windows' mixer, from 0 to 1 (``voice/mixer.py`` on
    Windows; a fake in tests). Both block briefly: call them off the event loop."""

    def get(self) -> float | None:
        """The game's level now, or None if it has no sound (it isn't running)."""
        ...

    def set(self, level: float) -> bool:
        """Set the game's level. False if it has no sound to set."""
        ...


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
        self._original: float | None = None
        """The level the player left, while Tycho has the game lowered."""
        self._left: float | None = None
        """Where Tycho last put the slider."""
        self._lock = asyncio.Lock()
        """One fade at a time, in the order asked."""
        self._lowering: asyncio.Task[None] | None = None
        self._raising: asyncio.Task[None] | None = None
        self._failed = False

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
        self._lines += 1
        if self._raising is not None:
            self._raising.cancel()  # it carries on down from wherever the fade got to
            self._raising = None
        if self.on:
            self._lowering = asyncio.get_running_loop().create_task(self._lower())

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
            if self._volume.get() is None or not self._volume.set(level):
                return  # the game isn't running: the next line tries again
        except Exception as e:
            self._failure(e)
            return
        log.info("Put the game's volume back to %d%%, as you left it", round(level * 100))
        self._forget()

    async def close(self) -> None:
        """Quitting: stop any fade and put the game back now."""
        for task in (self._lowering, self._raising):
            if task is not None and not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        async with self._lock:
            if self._original is not None:
                await self._set(self._original)
                self._forget()

    def _raise_soon(self, delay: float) -> None:
        if self._original is None and not self._saved.exists():
            return
        if self._raising is not None:
            self._raising.cancel()
        self._raising = asyncio.get_running_loop().create_task(self._raise(delay))

    async def _lower(self) -> None:
        async with self._lock:
            if self._lines == 0:  # the line already ended
                return
            if self._original is None:
                await self._put_back_saved()
                now = await self._get()
                if now is None or now <= 0.0:
                    return
                self._original = now
                self._save(now)
            else:  # a line soon after the last: from wherever the fade up got to
                now = await self._get()
                if now is None or self._moved(now):
                    return
            await self._fade(now, self._original * self.level, DOWN_SECONDS)

    async def _raise(self, delay: float) -> None:
        if delay:
            await self._sleep(delay)
        async with self._lock:
            if self._original is None:
                await self._put_back_saved()
                return
            now = await self._get()
            if now is None:
                return  # the game closed: the level waits on disk for the next chance
            if self._moved(now):
                return
            await self._fade(now, self._original, UP_SECONDS)
            self._forget()

    def _moved(self, now: float) -> bool:
        """Whether the player moved the slider: if so, their level wins."""
        if self._left is None or abs(now - self._left) <= MOVED:
            return False
        log.info("The game's volume was changed by hand while Tycho spoke: leaving it")
        self._forget()
        return True

    async def _fade(self, start: float, end: float, seconds: float) -> None:
        steps = max(1, round(seconds / STEP_SECONDS))
        for step in range(1, steps + 1):
            if not await self._set(start + (end - start) * step / steps):
                return
            if step < steps:
                await self._sleep(seconds / steps)

    async def _put_back_saved(self) -> None:
        """A level saved on disk with nothing lowered now: a crash or a closed game left
        it. Put it back if the game is running."""
        level = self._load()
        if level is None:
            return
        if await self._get() is None:
            return  # not running: try again at the next line or start
        await self._set(level)
        log.info("Put the game's volume back to %d%%, as you left it", round(level * 100))
        self._forget()

    async def _get(self) -> float | None:
        try:
            return await asyncio.to_thread(self._volume.get)
        except Exception as e:
            self._failure(e)
            return None

    async def _set(self, level: float) -> bool:
        try:
            done = await asyncio.to_thread(self._volume.set, level)
        except Exception as e:
            self._failure(e)
            return False
        if done:
            self._left = level
        return done

    def _failure(self, e: Exception) -> None:
        if not self._failed:
            self._failed = True
            log.warning("Couldn't change the game's volume: %s", e)

    def _save(self, level: float) -> None:
        try:
            self._saved.parent.mkdir(parents=True, exist_ok=True)
            self._saved.write_text(json.dumps({"level": level}), "utf-8")
        except OSError as e:
            log.warning("Couldn't note the game's volume: %s", e)

    def _load(self) -> float | None:
        try:
            level = json.loads(self._saved.read_text("utf-8"))["level"]
        except FileNotFoundError:
            return None
        except (OSError, ValueError, KeyError, TypeError) as e:
            log.warning("Ignoring the noted game volume: %s", e)
            self._saved.unlink(missing_ok=True)
            return None
        if not isinstance(level, int | float) or not 0.0 < level <= 1.0:
            self._saved.unlink(missing_ok=True)
            return None
        return float(level)

    def _forget(self) -> None:
        self._original = self._left = None
        with contextlib.suppress(OSError):
            self._saved.unlink(missing_ok=True)


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
