"""The player's own keys, as a look-up the brain can call (#357).

"How do you shoot the guns?" got "Hold the primary fire button": Tycho had the
player's keybinds, but with key actions on the briefing names actions, never keys.
This look-up answers "which key does it" from those keybinds: the game's words for
the action, with its note ("(Hold)"), and the key. It's read-only: it presses
nothing, so the safety gate has nothing to check.

With key actions off the briefing already ends with every key (#392, #437), so the
brain isn't offered it then.
"""

import json
from collections.abc import Callable, Mapping, Sequence

from tycho.brain.briefing import with_note
from tycho.brain.lookups import Lookup, LookupResult, failure
from tycho.input.plan import pressable_input
from tycho.keybinds import Action, Keybinds, key_text
from tycho.text import name_score, normalize

NAME = "player_keys"
MATCHES = 3
"""Actions given for words that name no action: enough for the brain to pick from."""
MAX_ASKED = 6
"""What one call may ask about, so a result stays short."""
FIT = 0.85
"""The least ``name_score`` a label must reach for the words to mean it."""

DESCRIPTION = """\
The player's own keys: which key or mouse button does an action, from their \
keybinds. Call it when they ask how to do something in the game themselves, or \
which key does it, then name the key in a few words ("Left Shift, held"). Pass \
action names from the list, or a few words of what it does when it isn't listed \
("strafe up"). It presses nothing. Not for anything you'd press for them."""

SCHEMA: Mapping[str, object] = {
    "type": "object",
    "properties": {
        "actions": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Action names from the list, or a few words of what each does.",
        }
    },
    "required": ["actions"],
    "additionalProperties": False,
}


class PlayerKeys:
    """The look-up, reading whichever keybinds the brain was last briefed with."""

    name = NAME
    description = DESCRIPTION
    input_schema = SCHEMA
    seconds = 1.0
    """It reads memory only."""

    def __init__(self, keybinds: Callable[[], Keybinds | None]) -> None:
        self._keybinds = keybinds

    async def prepare(self) -> None:
        return None

    async def run(self, arguments: Mapping[str, object]) -> LookupResult:
        asked = arguments.get("actions")
        if not isinstance(asked, list) or not asked:
            return failure("pass one or more action names, or words of what each does")
        keybinds = self._keybinds()
        if keybinds is None or not len(keybinds):
            return LookupResult(json.dumps({"not_found": "Tycho can't read this game's keys"}))
        found: list[dict[str, str]] = []
        missing: list[str] = []
        for wanted in [str(a) for a in asked[:MAX_ASKED] if str(a).strip()]:  # pyright: ignore[reportUnknownVariableType, reportUnknownArgumentType]
            matches = _matches(wanted, keybinds)
            found += [_entry(a) for a in matches]
            if not matches:
                missing.append(wanted)
        result: dict[str, object] = {"keys": list({e["does"]: e for e in found}.values())}
        if missing:
            result["not_found"] = missing
            result["say"] = (
                "Tycho sees no keyboard or mouse key for these: they may be on a stick, "
                "or unbound in the game's keybindings."
            )
        return LookupResult(json.dumps(result))


def keyed(
    lookups: Sequence[Lookup], keys: bool, keybinds: Callable[[], Keybinds | None]
) -> tuple[list[Lookup], dict[str, Lookup]]:
    """The brain's look-ups, with the player's keys added while key actions are on,
    and by name: for each adapter's ``__init__``."""
    extra: list[Lookup] = [PlayerKeys(keybinds)] if keys else []
    every = [*lookups, *extra]
    return every, {lookup.name: lookup for lookup in every}


def _matches(wanted: str, keybinds: Keybinds) -> list[Action]:
    """The bound, labelled actions ``wanted`` means: by name, or best by words."""
    named = keybinds.get(wanted.strip())
    if named is not None:
        return [named] if _key(named) else []
    said = normalize(wanted)
    scored = [
        (name_score(said, normalize(a.label)), a) for a in keybinds if a.label.strip() and _key(a)
    ]
    scored = sorted((s for s in scored if s[0] >= FIT), key=lambda s: -s[0])
    return [a for _, a in scored[:MATCHES]]


def _key(action: Action) -> str:
    key = pressable_input(action)
    return key_text(key) if key is not None else ""


def _entry(action: Action) -> dict[str, str]:
    return {"does": with_note(action), "key": _key(action)}
