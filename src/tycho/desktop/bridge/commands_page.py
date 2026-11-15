"""The Commands page: every command, its phrases and keys, and the player's own phrases,
for the chosen game (#295)."""

import logging
import time
from typing import cast

from tycho import paths, settings
from tycho.desktop.bridge.state import KEYBINDS_SECONDS, State
from tycho.desktop.commands import Commands
from tycho.keybinds import Keybinds
from tycho.packs import STAR_CITIZEN, GamePack, PackError, as_run, built_in
from tycho.settings import Settings

log = logging.getLogger(__name__)


class CommandsPage(State):
    def commands(self) -> dict[str, object]:
        """Every command and sequence: phrases, the player's own, keys, what to bind."""
        return self._commands().rows()

    def add_phrase(self, command_id: object, phrase: object) -> dict[str, object]:
        """One more phrase for a command, in the player's commands.toml."""
        return self._commands().add_phrase(command_id, phrase)

    def remove_phrase(self, command_id: object, phrase: object) -> dict[str, object]:
        """Take one of the player's own phrases away."""
        return self._commands().remove_phrase(command_id, phrase)

    def _commands(self) -> Commands:
        """The Commands page for the game the settings pick, and its own file (#295)."""
        if self._given_commands is not None:
            return self._given_commands
        pack = self._game_pack(settings.load(self._path))
        return Commands(paths.commands_file(pack.id), self._keybinds, pack)

    def _game_pack(self, current: Settings) -> GamePack:
        """The chosen game, as the player's ``key_actions`` runs it (#191)."""
        try:
            return as_run(built_in(current.game), key_actions=current.key_actions)
        except PackError:  # tycho check and the status page say why
            log.exception("The chosen game's pack didn't load")
            return as_run(built_in(STAR_CITIZEN), key_actions=current.key_actions)

    def _keybinds(self) -> Keybinds | None:
        """The player's keybinds, for the Commands page: kept a while, since reading them
        parses the game's files, and they change only when the player rebinds in game."""
        current = settings.load(self._path)
        if self._game_pack(current).parts.keybinds is None:
            return None  # a game Tycho never presses keys in has none to read (#191)
        # The game too: once two packs read keybinds, a switch mustn't show the last
        # game's keys until the cache runs out (#331).
        key = (
            current.game,
            current.game_channel,
            str(current.game_root),
            time.monotonic() // KEYBINDS_SECONDS,
        )

        def read() -> Keybinds | None:
            from tycho.games import GameDataError, find_install, load_keys

            try:
                return load_keys(find_install(current)).keybinds
            except FileNotFoundError, GameDataError, OSError:
                return None

        return cast("Keybinds | None", self._cached("keybinds", key, read))
