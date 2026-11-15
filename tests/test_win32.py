"""Every Windows call goes through ``tycho.win32``, fully declared (#298, ADR 19).

Read from the source, so it runs off Windows too. ``tests/test_code_layout.py`` checks
that no other module loads a DLL; this checks that each function Tycho calls on one has
its argument and return types, so a handle is never cut to a 32-bit int.
"""

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src" / "tycho"
DLLS = ("user32", "gdi32", "kernel32", "shell32")


def _called() -> dict[tuple[str, str], str]:
    """Each (DLL, function) the code calls, with a module that calls it."""
    found: dict[tuple[str, str], str] = {}
    for path in sorted(SRC.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        names: dict[str, str] = {}  # local name -> DLL, from `user32 = win32.user32()`
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                for target, value in _pairs(node):
                    if isinstance(target, ast.Name) and (dll := _dll(value)):
                        names[target.id] = dll
        for node in ast.walk(tree):
            if not isinstance(node, ast.Attribute) or node.attr[:1].islower():
                continue
            dll = _dll(node.value) or (
                names.get(node.value.id) if isinstance(node.value, ast.Name) else None
            )
            if dll:
                found.setdefault((dll, node.attr), path.relative_to(SRC).as_posix())
    return found


def _pairs(node: ast.Assign) -> list[tuple[ast.expr, ast.expr]]:
    """Each (target, value) of an assignment, ``a, b = (x, y)`` taken apart."""
    targets, values = node.targets[0], node.value
    if isinstance(targets, ast.Tuple) and isinstance(values, ast.Tuple):
        return list(zip(targets.elts, values.elts, strict=False))
    return [(targets, values)]


def _dll(node: ast.expr) -> str | None:
    """The DLL that ``node`` is: ``win32.user32()``, or ``user32()`` inside win32.py."""
    if not isinstance(node, ast.Call) or node.args:
        return None
    func = node.func
    name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
    return name if name in DLLS else None


def _declared() -> dict[tuple[str, str], set[str]]:
    """What win32.py declares for each function: "argtypes", "restype" or both."""
    tree = ast.parse((SRC / "win32.py").read_text(encoding="utf-8"))
    declared: dict[tuple[str, str], set[str]] = {}
    for typer in (n for n in tree.body if isinstance(n, ast.FunctionDef)):
        if not typer.name.startswith("_type_"):
            continue
        dll = typer.name.removeprefix("_type_")
        for node in ast.walk(typer):
            if (
                isinstance(node, ast.Attribute)
                and node.attr in ("argtypes", "restype")
                and isinstance(node.value, ast.Attribute)
            ):
                declared.setdefault((dll, node.value.attr), set()).add(node.attr)
    return declared


def test_every_windows_function_tycho_calls_is_declared() -> None:
    declared = _declared()
    missing = {
        f"{dll}.{function} (in {module}): {', '.join(sorted({'argtypes', 'restype'} - have))}"
        for (dll, function), module in _called().items()
        if (have := declared.get((dll, function), set())) != {"argtypes", "restype"}
    }
    assert not missing, f"declare these in win32.py's _type_ functions: {sorted(missing)}"


def test_the_check_sees_the_calls() -> None:
    called = _called()
    assert ("user32", "SendInput") in called  # win32.user32().SendInput(...)
    assert ("kernel32", "GetDriveTypeW") in called  # kernel32 = win32.kernel32()
    assert ("user32", "MessageBoxW") in called  # inside win32.py
    assert ("kernel32", "OpenProcess") in called  # user32, kernel32 = (..., ...)


def _hidden(tree: ast.Module) -> list[int]:
    """Lines where a DLL is used out of the check's sight: one kept anywhere but a plain
    name (on ``self``, handed to a helper, #407), or a name bound to one and then used
    other than as ``name.Function`` (#409). Names count within their own function."""
    lines: list[int] = []
    parents = {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}
    for node in ast.walk(tree):
        if not isinstance(node, ast.expr) or not _dll(node):
            continue
        parent = parents[node]
        if isinstance(parent, ast.Tuple):
            parent = parents[parent]
        if not isinstance(parent, ast.Attribute) and not (
            isinstance(parent, ast.Assign)
            and all(isinstance(t, ast.Name | ast.Tuple) for t in parent.targets)
        ):
            lines.append(node.lineno)
    top = [s for s in tree.body if not isinstance(s, ast.FunctionDef | ast.ClassDef)]
    functions = [n.body for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]
    for statements in (top, *functions):
        body = [n for statement in statements for n in ast.walk(statement)]
        bound = {
            target.id
            for node in body
            if isinstance(node, ast.Assign)
            for target, value in _pairs(node)
            if isinstance(target, ast.Name) and _dll(value)
        }
        lines.extend(
            node.lineno
            for node in body
            if isinstance(node, ast.Name)
            and isinstance(node.ctx, ast.Load)
            and node.id in bound
            and not isinstance(parents[node], ast.Attribute)
        )
    return sorted(set(lines))


def test_a_dll_is_used_where_the_check_can_see_it() -> None:
    hidden = [
        f"{path.relative_to(SRC).as_posix()}:{line}"
        for path in sorted(SRC.rglob("*.py"))
        for line in _hidden(ast.parse(path.read_text(encoding="utf-8")))
    ]
    assert not hidden, f"call the DLL inline or bind it to a plain name: {hidden}"


def test_the_check_sees_a_dll_out_of_sight() -> None:
    seen = (
        "def f(g):\n"
        "    u = win32.user32()\n"
        "    u.MessageBoxW(None, 'a', 'b', 0)\n"
        "def h(g):\n"
        "    k = win32.kernel32()\n"
        "    g(k)\n"
        "class A:\n"
        "    def __init__(self):\n"
        "        self.u = win32.user32()\n"
        "def other(u):\n"
        "    return u\n"
    )
    assert _hidden(ast.parse(seen)) == [6, 9]
