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
    "runtime/session.py": 851,
    "desktop/bridge.py": 708,
    "tile/draw.py": 622,
    "brain/openrouter.py": 607,
    "games/star_citizen/items.py": 566,
    "commands/matcher.py": 537,
    "games/star_citizen/ships.py": 519,
    "settings.py": 506,
}

# Star Citizen's code, all in one package since #397.
STAR_CITIZEN = ("tycho.games.star_citizen",)
# The one list of built-in parts may name Star Citizen's builders (ADR 19).
MAY_IMPORT_STAR_CITIZEN = ("tycho.games",)

# The top of the program: the command line and starting Tycho. Only __main__ and the
# command line import them. tycho.app is today's of both; step 7 (#398) splits it.
TOP = ("tycho.app", "tycho.cli", "tycho.start")
MAY_IMPORT_TOP = ("tycho.__main__", "tycho.cli", "tycho.app")  # app: until it becomes cli

# Loading a DLL: calling one of these, or reading one of the loaders. Only tycho.win32
# may (#298). A type hint such as ``-> ctypes.WinDLL`` loads nothing, so it's allowed.
DLL_CLASSES = frozenset({"WinDLL", "CDLL", "OleDLL", "PyDLL", "LibraryLoader"})
DLL_LOADERS = frozenset({"windll", "cdll", "oledll", "pydll"})

# Today's breaks: (the importing module, what it imports). Remove an entry when you fix it.
STAR_CITIZEN_BREAKS = set[tuple[str, str]]()  # none since #397
TOP_BREAKS = set[tuple[str, str]]()  # none since #398's first PR
DLL_BREAKS = set[str]()  # none since #298


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
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            yield from (alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            base = _base(name, node)
            for alias in node.names:
                # `from tycho.knowledge import web` imports a module; `from x import f` doesn't.
                full = f"{base}.{alias.name}"
                yield full if full in MODULES else _origin(base, alias.name)


def _base(name: str, node: ast.ImportFrom) -> str:
    """The module ``from ... import`` reads from, inside the module ``name``."""
    if not node.level:
        return node.module or ""
    package = name if MODULES[name].name == "__init__.py" else name.rpartition(".")[0]
    base = ".".join(package.split(".")[: len(package.split(".")) - node.level + 1])
    return f"{base}.{node.module}" if node.module else base


def _origin(package: str, name: str, depth: int = 0) -> str:
    """Where a name imported from ``package`` comes from: the module its ``__init__``
    took it from (``from tycho.games.star_citizen import UexApi`` reaches its ``uex``),
    through relative imports and packages that pass it on, or the package itself."""
    path = MODULES.get(package)
    if path is None or path.name != "__init__.py" or depth > 5:
        return package
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if not isinstance(node, ast.ImportFrom):
            continue
        for alias in node.names:
            if (alias.asname or alias.name) == name:
                base = _base(package, node)
                full = f"{base}.{alias.name}"
                return full if full in MODULES else _origin(base, alias.name, depth + 1)
    return package


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
    """Whether a module calls a DLL class or reads a loader. A class passed along
    uncalled (``functools.partial(ctypes.WinDLL, ...)``) isn't seen: nothing does that,
    and catching every bare name would also catch type hints."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            called = node.func
            name = called.attr if isinstance(called, ast.Attribute) else getattr(called, "id", "")
            if name in DLL_CLASSES:
                return True
        if isinstance(node, ast.Attribute) and node.attr in DLL_LOADERS:
            return True
        if (
            isinstance(node, ast.ImportFrom)
            and node.module == "ctypes"
            and any(alias.name in DLL_LOADERS | {"*"} for alias in node.names)
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
        "from tycho.games.star_citizen.keybinds import Action\n"
        "def f():\n"
        "    from tycho import app\n"
        "    return ctypes.WinDLL('user32')\n"
    )
    imports = set(_imports("tycho.safety", tree))
    assert "tycho.games.star_citizen.keybinds" in imports
    assert "tycho.app" in imports
    assert _loads_a_dll(tree)
    assert not _loads_a_dll(ast.parse("import ctypes\nx = ctypes.sizeof(ctypes.c_int)\n"))
    assert not _loads_a_dll(ast.parse("import ctypes\nu: ctypes.WinDLL = win32.user32()\n"))
    assert _loads_a_dll(ast.parse("import ctypes\nctypes.windll.user32.MessageBoxW()\n"))
    # A name re-exported by a package is traced to the module it comes from.
    assert _origin("tycho.packs", "GamePack") == "tycho.packs.pack"
