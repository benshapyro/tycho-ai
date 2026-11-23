"""The tools every brain adapter offers the model, in a neutral shape (ADR 14): the
acts (``run_sequence``, ``run_actions``) and the look-ups. ``show_card`` is the Tile's
(``tile.cards``)."""

from collections.abc import Iterable

from tycho.brain.lookups import Lookup


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
                    "name": {"type": "string", "enum": sequences or [""]},
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
