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
                targets = node.targets[0]
                values = node.value
                pairs = (
                    zip(targets.elts, values.elts, strict=False)
                    if isinstance(targets, ast.Tuple) and isinstance(values, ast.Tuple)
                    else [(targets, values)]
                )
                for target, value in pairs:
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
