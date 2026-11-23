"""Write the upgrade fixtures (#612): each player file as each released Tycho wrote it,
into ``tests/fixtures/upgrades/<version>/``, for ``tests/test_upgrades.py``.

Each version's own code writes its files (``write_files.py``), from a git worktree of
its tag; ``main`` is the code here, for the version not released yet.

    uv run python tools/upgrades/make_fixtures.py main            # a saved file changed shape
    uv run python tools/upgrades/make_fixtures.py --this 0.11.0   # in the release's bump PR
    uv run python tools/upgrades/make_fixtures.py v0.10.0         # an old tag, from git

The files are committed; nothing rewrites a released version's folder.
"""

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests" / "fixtures" / "upgrades"
WRITER = Path(__file__).with_name("write_files.py")


def write(version: str, *, this: bool = False) -> Path:
    """``this``: the code here is ``version``'s (the bump PR), so no worktree."""
    out = FIXTURES / ("main" if version == "main" else version.removeprefix("v"))
    if version != "main" and out.exists():
        raise SystemExit(f"{out} is there already: a released version's files never change")
    if version == "main" or this:
        shutil.rmtree(out, ignore_errors=True)
        _run(ROOT / "src", out)
        return out
    with tempfile.TemporaryDirectory() as temp:
        tree = Path(temp) / "tree"
        _git("worktree", "add", "--detach", str(tree), version)
        try:
            _run(tree / "src", out)
        finally:
            _git("worktree", "remove", "--force", str(tree))
    return out


def _git(*args: str) -> None:
    subprocess.run(["git", *args], check=True, cwd=ROOT)  # noqa: S603, S607 - git, as you'd run it


def _run(src: Path, out: Path) -> None:
    # That version's ``src`` first, so its own code writes the files.
    code = f"import sys; sys.path.insert(0, {str(src)!r}); sys.argv = ['w', {str(out)!r}]; "
    code += f"exec(open({str(WRITER)!r}, encoding='utf-8').read())"
    subprocess.run([sys.executable, "-c", code], check=True, cwd=src.parent)  # noqa: S603 - our code


if __name__ == "__main__":
    if sys.argv[1:2] == ["--this"]:
        print(write(sys.argv[2], this=True))
    else:
        for name in sys.argv[1:] or ["main"]:
            print(write(name))
