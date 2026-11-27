"""Star Citizen's part of ``tycho check``, and the question that finds its folder.

Out of ``app.py`` (#397): ``app`` reaches it only through ``tycho.games``.
"""

from collections.abc import Callable, Generator, Iterator
from pathlib import Path

from tycho import paths, settings
from tycho.commands import CommandMatcher, on_stick, resolve
from tycho.findings import Finding
from tycho.games.star_citizen.fleet import Fleet
from tycho.games.star_citizen.install import GameInstall, root_from, search_roots
from tycho.games.star_citizen.store import KeybindStore
from tycho.packs.parts import GameDataError
from tycho.settings import Settings


def find_install(current: Settings) -> GameInstall:
    roots = search_roots()
    if (given := current.game_root) is not None:
        # Any level will do, as when setup asks: LIVE itself, StarCitizen or Roberts
        # Space Industries. "...\StarCitizen\LIVE" in the file was looked for as
        # "...\LIVE\LIVE" and never found.
        roots = (root_from(given, current.game_channel) or given, *roots)
    return GameInstall.find(current.game_channel, roots=roots)


def ask_game_root(channel: str, ask: Callable[[str], str] = input) -> Path | None:
    """Ask where the game is, accepting the channel folder, StarCitizen or Roberts Space
    Industries; None if the player skips or gives up."""
    print(f"       Where is Star Citizen? Paste the folder that contains {channel}, or press")
    print("       Enter to skip. (In the RSI Launcher: Settings, Games, Library Folder.)")
    for _ in range(3):
        try:
            answer = ask("       > ").strip().strip('"')
        except EOFError:
            return None
        if not answer:
            return None
        if (root := root_from(Path(answer), channel)) is not None:
            return root
        print(f"       There's no {channel}\\Data.p4k in or under {answer}. Try again?")
    return None


def fleet_findings(pack_id: str) -> Iterator[Finding]:
    """A problem with the player's fleet file, if it has one."""
    if problem := Fleet.load(paths.fleet_file(pack_id)).problem:
        yield Finding("warn", "Your fleet", problem)


def game_findings(
    current: Settings, matcher: CommandMatcher, *, ask: bool
) -> Generator[Finding, None, KeybindStore | None]:
    """The game's folder (asking for it, with ``ask``, if it isn't found), the keybinds
    and Game.log. Returns the keybinds, if they could be read."""
    store: KeybindStore | None = None
    try:
        try:
            install = find_install(current)
            yield Finding("ok", "Star Citizen", str(install.folder))
        except FileNotFoundError:
            # Not found anywhere Tycho looks: ask, if someone's there to answer.
            root = ask_game_root(current.game_channel) if ask else None
            if root is None:
                raise
            current = current.model_copy(update={"game_root": root})
            saved = settings.save(current)
            install = find_install(current)
            yield Finding("ok", "Star Citizen", f"{install.folder} (saved to {saved})")
        store = KeybindStore.load(install)
        keyless = [
            c
            for c in matcher.commands
            if not c.is_sequence and resolve(c, store.keybinds).action is None
        ]
        detail = f"{len(store.keybinds)} actions"
        # On a stick but not a key: bound, just not where Tycho can press it (#622).
        stick = {c.id for c in keyless if on_stick(c, store.keybinds) is not None}
        unbound = [c.id for c in keyless if c.id not in stick]
        on_sticks = [c.id for c in keyless if c.id in stick]
        if unbound:
            yield Finding(
                "warn",
                "Keybinds",
                f"{detail}; no key for {len(unbound)} voice command(s): {', '.join(unbound)}",
            )
        if on_sticks:
            yield Finding(
                "warn",
                "Keybinds",
                f"{len(on_sticks)} voice command(s) on your stick only, which Tycho can't "
                f"press: {', '.join(on_sticks)}. Give each a keyboard key too if you want "
                "Tycho to press it.",
            )
        if not unbound and not on_sticks:
            yield Finding("ok", "Keybinds", f"{detail}; every voice command has a key")
        log_file = install.game_log
        if log_file.exists():
            yield Finding("ok", "Game.log", str(log_file))
        else:
            yield Finding("warn", "Game.log", f"not found at {log_file}; the game writes it")
    except FileNotFoundError as e:
        yield Finding(
            "fail",
            "Star Citizen",
            str(e),
            fix=f'set game_root = "<the folder holding Data.p4k>" in {paths.settings_file()}',
        )
    except GameDataError as e:
        yield Finding("fail", "Keybinds", f"couldn't read them: {e}")
    return store
