"""The ``owned_blueprints`` look-up (ADR 8's third amendment, #598): which blueprints
the player owns, and adding or taking one off when the player says so.

The game's log fills the list on its own (``owned``). The brain may add one only for
words the player said in this request (``memory.tool.from_said``), as a blueprint a
mission gives in this patch, and take one off only when the player asked to
("forget", "remove"). Everything reads what the player said, not what the brain says
they said. While ``memory`` is off, it does nothing and says so.
"""

import asyncio
import json
from collections.abc import Awaitable, Callable, Mapping

from tycho.brain.heard import heard
from tycho.brain.lookups import LookupResult, failure
from tycho.games.star_citizen import blueprint_names as naming
from tycho.games.star_citizen.owned import Owned, OwnedBlueprints
from tycho.games.star_citizen.wiki import WikiError
from tycho.memory.tool import FORGET, from_said

SHOWN = 60
"""The most names one answer lists; the count is always whole."""
ACTIONS = ("list", "check", "add", "forget")
DESCRIPTION = """\
The crafting blueprints the player owns in Star Citizen, kept on their PC from the \
game's log (each blueprint a mission gave them) and from what they told you. \
action "check" with blueprint as the player said it: "do I have the X blueprint?". \
action "list": "which blueprints have I got?" (say the count, and a few unless asked \
for all). action "add" only when the player says they own a blueprint the list lacks \
("I've got the Killshot Rifle blueprint"), with blueprint in their words; action \
"forget" only when they ask to take one off. Owning a blueprint isn't owning the item: \
it lets them craft it. The list only knows what the log showed on this PC and what \
they said, so "not in your list" rather than "you don't have it"."""


class OwnedTool:
    """Satisfies ``brain.lookups.Lookup``."""

    instant = True
    """It answers at once, so Tycho doesn't say "Checking." first."""

    def __init__(self, owned: OwnedBlueprints, known: Callable[[], Awaitable[list[str]]]) -> None:
        """``known``: every blueprint a mission gives in this patch (the blueprint
        look-up's), to name one the player adds as the game does."""
        self._owned = owned
        self._known = known

    @property
    def name(self) -> str:
        return "owned_blueprints"

    @property
    def description(self) -> str:
        return DESCRIPTION

    @property
    def input_schema(self) -> Mapping[str, object]:
        return {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": list(ACTIONS)},
                "blueprint": {
                    "type": "string",
                    "description": "The item the blueprint makes, as the player said it; "
                    'empty for "list".',
                },
            },
            "required": ["action", "blueprint"],
            "additionalProperties": False,
        }

    async def prepare(self) -> None:
        """Read the game's logs once at start, so the first question is quick and the
        player's earlier blueprints are listed (the amendment's one-time read)."""
        await asyncio.to_thread(self._owned.refresh)

    async def run(self, arguments: Mapping[str, object]) -> LookupResult:
        action = str(arguments.get("action") or "")
        said = " ".join(str(arguments.get("blueprint") or "").split())
        if action not in ACTIONS:
            return failure(f"action must be one of {', '.join(ACTIONS)}")
        if not self._owned.on:
            return _result({"off": "memory is off, so Tycho keeps no blueprint list"})
        owned = await asyncio.to_thread(self._owned.refresh)
        if action == "list":
            names = [o.name for o in owned]
            body: dict[str, object] = {"count": len(names), "blueprints": names[:SHOWN]}
            if len(names) > SHOWN:
                body["more_not_listed"] = len(names) - SHOWN
            return _result(body)
        if not said:
            return failure("say which blueprint")
        if action == "check":
            return _result(_check(said, owned))
        if action == "forget":
            return await self._forget(said, owned)
        return await self._add(said)

    async def _add(self, said: str) -> LookupResult:
        now = heard()
        if now is None or not from_said(said, now.said):
            return failure("add only a blueprint the player just said they own, in their words")
        try:
            known = await self._known()
        except WikiError as e:
            return failure(f"the blueprint list didn't load ({e}); ask them again later")
        names = {n: n for n in known}
        ranked = naming.ranked(said, names)
        if not ranked:
            return _result(
                {
                    "asked": said,
                    "not_found": "no mission in this patch gives a blueprint by that name",
                    "closest": naming.closest(said, names),
                }
            )
        name = ranked[0]
        added = await asyncio.to_thread(self._owned.add, name)
        return _result({"added" if added else "already_listed": name})

    async def _forget(self, said: str, owned: list[Owned]) -> LookupResult:
        now = heard()
        if now is None or not FORGET.search(now.said):
            return failure("take a blueprint off only when the player asks to")
        ranked = naming.ranked(said, {o.name: o for o in owned})
        if not ranked:
            return _result({"asked": said, "not_found": "no such blueprint in the list"})
        removed = await asyncio.to_thread(self._owned.remove, ranked[0])
        return _result({"removed" if removed else "not_found": ranked[0]})


def _check(said: str, owned: list[Owned]) -> dict[str, object]:
    by_name = {o.name: o for o in owned}
    ranked = naming.ranked(said, by_name)
    if not ranked:
        return {"asked": said, "in_list": False, "list_size": len(owned)}
    entry = by_name[ranked[0]]
    body: dict[str, object] = {"blueprint": entry.name, "in_list": True, "from": entry.source}
    if entry.got is not None:
        body["got"] = entry.got.isoformat()
    if len(ranked) > 1:
        body["also_in_list"] = ranked[1:4]
    return body


def _result(body: dict[str, object]) -> LookupResult:
    return LookupResult(json.dumps({**body, "source": "the player's blueprint list on this PC"}))
