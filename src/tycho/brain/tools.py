"""The tools every brain adapter offers the model, in a neutral shape (ADR 14): the
acts (``run_sequence``, ``run_actions``) and the look-ups. ``show_card`` is the Tile's
(``tile.cards``)."""

import json
from collections.abc import Iterable

from tycho.brain.lookups import Lookup

NAMES_BUDGET = 1_000
"""The most JSON ``run_sequence``'s list of names may be, in characters. The acts are
strict, and Anthropic refuses every request when their grammar is too large (#638); a
player's own sequences go in that list, and nothing caps how many they write (#640).
Past this, the name is any text: the briefing lists the sequences, and
``decisions.parse_tool_call`` still runs only one of them, so a wrong name presses
nothing."""


def names_listed(sequences: list[str]) -> bool:
    """Whether ``run_sequence`` lists the sequences' names (``NAMES_BUDGET``)."""
    return len(json.dumps(sequences)) <= NAMES_BUDGET


def _sequence_name(sequences: list[str]) -> dict[str, object]:
    if names_listed(sequences):
        return {"type": "string", "enum": sequences or [""]}
    return {"type": "string", "description": "A sequence's name, exactly as listed."}


def tool_specs(
    sequences: list[str], lookups: Iterable[Lookup] = (), *, keys: bool = True
) -> list[dict[str, object]]:
    """Every tool but ``show_card`` (name, description, schema) for each adapter to shape.
    Without ``keys`` (advisor mode, #191): only look-ups, no ``run_actions`` or sequences."""
    looks: list[dict[str, object]] = [
        {
            "name": lookup.name,
            "description": lookup.description,
            "input_schema": dict(lookup.input_schema),
        }
        for lookup in lookups
    ]
    if not keys:
        return looks
    return [
        {
            "name": "run_sequence",
            "description": "Run one of the player's named sequences.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "name": _sequence_name(sequences),
                    "say": {"type": "string", "description": "A short spoken line."},
                },
                "required": ["name", "say"],
                "additionalProperties": False,
            },
        },
        {
            "name": "run_actions",
            "description": "Press game actions, in order, with the player's own keys.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "actions": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Action names from the list, e.g. v_flightready.",
                    },
                    "say": {
                        "type": "string",
                        "description": (  # what a toggle did can't be seen (#545)
                            'A short spoken line. For a toggle ("Open/Close Doors"), say you'
                            " toggled it, never which way: you can't see it."
                        ),
                    },
                },
                "required": ["actions", "say"],
                "additionalProperties": False,
            },
        },
        *looks,
    ]
