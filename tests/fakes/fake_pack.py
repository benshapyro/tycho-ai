"""A fake second game for the pack seam (ADR 18): its pack folder is ``tests/packs/fake-game``.

Its keybinds part, ``fake-game``, exists only here: ``REGISTRY`` is the built-in
registry plus it, as a later release would add a real game's reader.
"""

from importlib.resources.abc import Traversable
from pathlib import Path

from tycho.game.keybinds import Action, Keybinds, KeyInput
from tycho.packs import BUILT_IN, GamePack, load_pack

FOLDER: Traversable = Path(__file__).resolve().parents[1] / "packs" / "fake-game"
REGISTRY = BUILT_IN.with_parts("keybinds", "fake-game")


def _action(name: str, key: str | None, label: str, actionmap: str = "fake") -> Action:
    inputs = () if key is None else (KeyInput.parse(key) or KeyInput(key),)
    return Action(name=name, actionmap=actionmap, label=label, inputs=inputs)


class FakeGameKeybinds:
    """Satisfies ``packs.KeybindSource``: Fake Game's keys, as if read from its files."""

    @property
    def keybinds(self) -> Keybinds:
        return Keybinds(
            [
                _action("shield_raise", "g", "Raise Shield"),
                _action("hatch_open", "lctrl+h", "Open Hatch"),
                _action("walk_forward", "w", "Walk Forward"),
                _action("photo_take", "p", "Take Photo", actionmap="photo_mode"),
                _action("self_destruct", "lalt+k", "Self Destruct"),
            ]
        )


def fake_pack(folder: Traversable = FOLDER) -> GamePack:
    """Fake Game's pack, loaded as one from outside Tycho would be."""
    return load_pack(folder, built_in=False, registry=REGISTRY)
