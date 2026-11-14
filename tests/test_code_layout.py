"""The code layout's guard rails (ADR 19, #393): a 500-line limit and the import rules.

Both read the source and never import it. Today's breaks are listed below with what
each is allowed: a listed file may shrink, never grow, and an entry has to go once its
break is fixed, so the lists only get shorter. They're empty when the #362 refactor ends.
"""

import ast
from collections.abc import Iterator
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src" / "tycho"

LIMIT = 500

# Files over the limit today, with their length as a ceiling (ADR 19). Shrink, never grow.
OVER_LIMIT = {
    "app.py": 1508,
    "runtime/session.py": 851,
    "desktop/bridge.py": 709,
    "tile/draw.py": 622,
    "brain/openrouter.py": 607,
    "knowledge/items.py": 566,
    "commands/matcher.py": 537,
    "knowledge/ships.py": 519,
    "settings.py": 506,
}

# Star Citizen's code, wherever it is today. Step 6 (#397) moves it all to
# tycho.games.star_citizen, and this becomes that one name.
STAR_CITIZEN = (
    "tycho.game",
    "tycho.games.star_citizen",
    *(
        f"tycho.knowledge.{name}"
        for name in (
            "fleet",
            "item_lookup",
            "items",
            "mining",
            "refining",
            "ship_lookup",
            "ships",
            "store",
            "trade",
            "trade_lookup",
            "uex",
            "wiki",
        )
    ),
)
# The one list of built-in parts may name Star Citizen's builders (ADR 19).
MAY_IMPORT_STAR_CITIZEN = ("tycho.games",)

# The top of the program: the command line and starting Tycho. Only __main__ and the
# command line import them. tycho.app is today's of both; step 7 (#398) splits it.
TOP = ("tycho.app", "tycho.cli", "tycho.start")
MAY_IMPORT_TOP = ("tycho.__main__", "tycho.cli")

# Anything that loads a DLL. Only tycho.win32 may (#298).
DLL_LOADERS = frozenset(
    {"WinDLL", "windll", "CDLL", "cdll", "OleDLL", "oledll", "PyDLL", "pydll", "LibraryLoader"}
)

# Today's breaks: (the importing module, what it imports). Remove an entry when you fix it.
STAR_CITIZEN_BREAKS = {
    ("tycho.app", "tycho.game.gamelog"),
    ("tycho.app", "tycho.game.install"),
    ("tycho.app", "tycho.game.keybinds"),
    ("tycho.app", "tycho.game.store"),
    ("tycho.app", "tycho.knowledge.fleet"),
    ("tycho.brain.briefing", "tycho.game.keybinds"),
    ("tycho.brain.claude", "tycho.game.keybinds"),
    ("tycho.brain.decisions", "tycho.game.keybinds"),
    ("tycho.brain.openrouter", "tycho.game.keybinds"),
    ("tycho.brain_choice", "tycho.knowledge.fleet"),
    ("tycho.brain_choice", "tycho.knowledge.item_lookup"),
    ("tycho.brain_choice", "tycho.knowledge.mining"),
    ("tycho.brain_choice", "tycho.knowledge.ships"),
    ("tycho.brain_choice", "tycho.knowledge.trade_lookup"),
    ("tycho.commands.resolve", "tycho.game.keybinds"),
    ("tycho.desktop.bridge", "tycho.game.keybinds"),
    ("tycho.desktop.bridge", "tycho.game.store"),
    ("tycho.desktop.commands", "tycho.game.keybinds"),
    ("tycho.desktop.setup", "tycho.game.install"),
    ("tycho.desktop.setup", "tycho.game.store"),
    ("tycho.input.plan", "tycho.game.keybinds"),
    ("tycho.knowledge", "tycho.knowledge.ship_lookup"),
    ("tycho.knowledge", "tycho.knowledge.ships"),
    ("tycho.knowledge", "tycho.knowledge.trade_lookup"),
    ("tycho.knowledge", "tycho.knowledge.uex"),
    ("tycho.knowledge", "tycho.knowledge.wiki"),
    ("tycho.packs.parts", "tycho.game.keybinds"),
    ("tycho.ptt.conflicts", "tycho.game.keybinds"),
    ("tycho.runtime.planning", "tycho.game.keybinds"),
    ("tycho.runtime.ports", "tycho.game.keybinds"),
    ("tycho.runtime.routing", "tycho.game.keybinds"),
    ("tycho.runtime.session", "tycho.game.keybinds"),
    ("tycho.safety", "tycho.game.keybinds"),
    ("tycho.settings", "tycho.game.install"),
}
TOP_BREAKS = {
    ("tycho.desktop.bridge", "tycho.app"),
    ("tycho.desktop.setup", "tycho.app"),
}
DLL_BREAKS = {
    "tycho.app",
    "tycho.game.install",
    "tycho.input.windows",
    "tycho.ptt.windows",
    "tycho.runtime.console",
    "tycho.runtime.instance",
}


def _files() -> Iterator[Path]:
    yield from sorted(SRC.rglob("*.py"))


def _name(path: Path) -> str:
    parts = ("tycho", *path.relative_to(SRC).with_suffix("").parts)
    return ".".join(parts[:-1] if parts[-1] == "__init__" else parts)


MODULES = {_name(path): path for path in _files()}


def _within(name: str, packages: tuple[str, ...]) -> str | None:
    """The package of ``packages`` that ``name`` is, or is inside."""
    return next((p for p in packages if name == p or name.startswith(p + ".")), None)


def _imports(name: str, tree: ast.Module) -> Iterator[str]:
    """Every tycho module ``name`` imports, at the top or inside a function."""
    package = name if MODULES[name].name == "__init__.py" else name.rpartition(".")[0]
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            yield from (alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = ".".join(package.split(".")[: len(package.split(".")) - node.level + 1])
                base = f"{base}.{node.module}" if node.module else base
            else:
                base = node.module or ""
            for alias in node.names:
                # `from tycho.knowledge import ships` imports a module; `from x import f` doesn't.
                full = f"{base}.{alias.name}"
                yield full if full in MODULES else base


def _trees() -> Iterator[tuple[str, ast.Module]]:
    for name, path in MODULES.items():
        yield name, ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _length(path: Path) -> int:
    return len(path.read_text(encoding="utf-8").splitlines())


def test_no_module_is_over_the_limit() -> None:
    over = {
        path.relative_to(SRC).as_posix(): _length(path)
        for path in _files()
        if _length(path) > LIMIT
    }
    grown = {
        name: f"{length} lines, over {OVER_LIMIT.get(name, LIMIT)}"
        for name, length in over.items()
        if length > OVER_LIMIT.get(name, LIMIT)
    }
    assert not grown, f"split these, or move code out (ADR 19's {LIMIT}-line limit): {grown}"


def test_the_over_limit_list_only_shrinks() -> None:
    for name, ceiling in OVER_LIMIT.items():
        path = SRC / name
        assert path.exists(), f"{name} is gone: take it off OVER_LIMIT"
        length = _length(path)
        assert length > LIMIT, f"{name} is {length} lines now: take it off OVER_LIMIT"
        assert length <= ceiling, f"{name} grew to {length} lines (ceiling {ceiling})"


def _star_citizen_breaks() -> set[tuple[str, str]]:
    found: set[tuple[str, str]] = set()
    for name, tree in _trees():
        if _within(name, STAR_CITIZEN) or _within(name, MAY_IMPORT_STAR_CITIZEN):
            continue
        found |= {(name, i) for i in _imports(name, tree) if _within(i, STAR_CITIZEN)}
    return found


def _top_breaks() -> set[tuple[str, str]]:
    found: set[tuple[str, str]] = set()
    for name, tree in _trees():
        if _within(name, MAY_IMPORT_TOP):
            continue
        for imported in _imports(name, tree):
            top = _within(imported, TOP)
            if top and _within(name, (top,)) != top:
                found.add((name, top))
    return found


def _loads_a_dll(tree: ast.Module) -> bool:
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr in DLL_LOADERS:
            return True
        if (
            isinstance(node, ast.ImportFrom)
            and node.module == "ctypes"
            and any(alias.name in DLL_LOADERS for alias in node.names)
        ):
            return True
    return False


def _dll_breaks() -> set[str]:
    return {name for name, tree in _trees() if name != "tycho.win32" and _loads_a_dll(tree)}


def test_only_star_citizen_s_own_code_imports_it() -> None:
    new = _star_citizen_breaks() - STAR_CITIZEN_BREAKS
    assert not new, f"these reach into Star Citizen's code (ADR 19): {sorted(new)}"


def test_only_the_command_line_imports_the_top() -> None:
    new = _top_breaks() - TOP_BREAKS
    assert not new, f"these import the command line or start-up (ADR 19): {sorted(new)}"


def test_only_win32_loads_a_dll() -> None:
    new = _dll_breaks() - DLL_BREAKS
    assert not new, f"load DLLs through tycho.win32 (#298): {sorted(new)}"


def test_the_lists_of_breaks_only_shrink() -> None:
    fixed = (
        (STAR_CITIZEN_BREAKS - _star_citizen_breaks())
        | (TOP_BREAKS - _top_breaks())
        | {(name, "a DLL") for name in DLL_BREAKS - _dll_breaks()}
    )
    assert not fixed, f"fixed, so take these off the lists in this file: {sorted(fixed)}"


def test_the_checks_see_a_break() -> None:
    # The rules' own check: each catches what it's for, so an empty result means something.
    tree = ast.parse(
        "import ctypes\n"
        "from tycho.game.keybinds import Action\n"
        "def f():\n"
        "    from tycho import app\n"
        "    return ctypes.WinDLL('user32')\n"
    )
    imports = set(_imports("tycho.safety", tree))
    assert "tycho.game.keybinds" in imports
    assert "tycho.app" in imports
    assert _loads_a_dll(tree)
    assert not _loads_a_dll(ast.parse("import ctypes\nx = ctypes.sizeof(ctypes.c_int)\n"))
