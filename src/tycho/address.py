"""What Tycho calls the player (#15, architecture 4.8): "Commander" by default, or a
name, callsign or rank they choose, or nothing. A game pack may set its own default
(``call_me`` in pack.toml, #349): "Commander" is a Star Citizen title.

The brain hears it in each request's header (``header``), not in the cached briefing,
so a change by voice applies to the next request. Tycho's own confirm question ends
with it ("Eject? Say confirm, Ace."). ``spoken`` swaps in how to say it just before the
voice speaks, so the console and logs keep the spelling the player chose.
"""

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

from tycho.runtime.ports import Speaker
from tycho.settings import DEFAULT_CALL_ME, HowOften, Settings

if TYPE_CHECKING:
    from tycho.packs import GamePack


@dataclass(slots=True)
class Address:
    name: str = DEFAULT_CALL_ME
    says: str = ""
    """How to say ``name`` if the voice gets it wrong; empty means as written."""
    how_often: HowOften = "sparingly"

    @classmethod
    def from_settings(cls, current: Settings, pack: GamePack | None = None) -> Address:
        """What the settings say to call the player, in ``pack``'s game: while they keep
        Tycho's default name, the pack's own default takes its place (#349)."""
        return cls(
            default_name(current.call_me, pack), current.call_me_says, current.call_me_how_often
        )

    @classmethod
    def for_pack(cls, pack: GamePack | None) -> Address:
        """The default address in ``pack``'s game, as for a player who never chose one."""
        return cls(default_name(DEFAULT_CALL_ME, pack))

    def set(self, setting: str, value: object) -> None:
        """Apply one ``call_me`` setting changed by voice."""
        if setting == "call_me" and isinstance(value, str):
            self.name = value
        elif setting == "call_me_says" and isinstance(value, str):
            self.says = value
        elif setting == "call_me_how_often" and value in ("sparingly", "often"):
            self.how_often = "often" if value == "often" else "sparingly"
        else:
            raise ValueError(f"{setting} can't be {value!r}")

    def header(self) -> str:
        """The line for the brain's request header."""
        if not self.name:
            return "[Use no name or title for the player.]"
        when = (
            "now and then, as a crewmate would"
            if self.how_often == "often"
            else "sparingly: greetings, warnings and confirmations of big actions, "
            "never every sentence"
        )
        return f'[Call the player "{self.name}", {when}.]'

    def spoken(self, text: str) -> str:
        """``text`` as the voice should say it: the name swapped for how to say it."""
        if not self.name or not self.says:
            return text
        return re.sub(rf"\b{re.escape(self.name)}\b", self.says, text, flags=re.IGNORECASE)


def default_name(call_me: str, pack: GamePack | None) -> str:
    """``call_me``, or the pack's own default if it's Tycho's default (#349). The settings
    file stores the default like any choice, so a player who picked "Commander"
    themselves gets the pack's default too, and can choose another name."""
    if pack is not None and pack.call_me is not None and call_me == DEFAULT_CALL_ME:
        return pack.call_me
    return call_me


class Pronouncing:
    """A ``Speaker`` that says the player's name the way they asked (#15)."""

    def __init__(self, inner: Speaker, address: Address) -> None:
        self._inner = inner
        self._address = address

    async def speak(self, text: str, *, keep: bool = False) -> None:
        await self._inner.speak(self._address.spoken(text), keep=keep)

    async def warm(self) -> None:
        await self._inner.warm()

    async def close(self) -> None:
        await self._inner.close()
