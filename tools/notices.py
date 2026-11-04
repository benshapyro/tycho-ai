"""Write THIRD-PARTY-NOTICES.txt into the built app, and fail on a licence it can't ship (#56).

    uv run python tools/notices.py dist/tycho [build/tycho]

It covers every Python distribution in the build: Tycho's runtime dependencies, plus
anything PyInstaller bundled that belongs to an installed distribution, whether as files
beside the exe or as modules packed inside it (read from PyInstaller's PYZ table of
contents in the build folder), so a package pulled in by a hook can't slip past. Then the
parts that aren't Python packages, with their licence texts in full (MIT and BSD need the
notice itself, not a link), and what Tycho downloads later. It also copies NOTICE (the
credits and the not-affiliated-with-CIG notice) beside the exe.

CI runs it after PyInstaller; it exits 1 if anything in the build is GPL, LGPL, AGPL,
non-commercial or has no licence at all, or if it can't see what was bundled.
"""

import ast
import re
import sys
from importlib.metadata import Distribution, distribution, packages_distributions
from pathlib import Path

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

APP = "tycho-ai"
REPO = Path(__file__).resolve().parent.parent
NATIVE_LICENCES = (
    (
        "Python, and the libraries built into it (OpenSSL, libffi, expat, libmpdec, zlib, "
        "bzip2, Zstandard and others)",
        REPO / "packaging" / "licences" / "python.txt",
    ),
    ("PortAudio (libportaudio64bit.dll)", REPO / "packaging" / "licences" / "portaudio.txt"),
)
"""Licence texts for what isn't a Python package, kept in the repo (see their README)."""

FORBIDDEN = re.compile(
    r"\b(A|L)?GPL|General Public|Non-?Commercial|\bNC\b|-NC-|\bSSPL\b|\bBUSL\b", re.IGNORECASE
)
"""Licences the app can't ship: copyleft that would reach Tycho's code, or not for
commercial use. (MPL-2.0 is file-level, so pysdl2-dll's own code under it is fine.)"""

LICENCE_FILE = re.compile(r"(LICEN[CS]E|COPYING|NOTICE)", re.IGNORECASE)

LEFT_OUT = re.compile(r"pysdl2_dll-[^/]+/licenses/libs/(?!SDL2/)", re.IGNORECASE)
"""pysdl2-dll's notices for SDL_mixer, SDL_image and the rest: the build ships SDL2.dll only."""

NOT_PYTHON = """\
Parts that aren't Python packages
=================================

Python 3.14 (python314.dll and the standard library): PSF License Agreement, with
  the licences of the libraries built into it; full texts below
Microsoft Visual C++ Redistributable (vcruntime140*.dll, msvcp140*.dll): Microsoft
  Software License Terms, redistributable under them
  https://visualstudio.microsoft.com/license-terms/
OpenSSL 3 (libcrypto-3-x64.dll, libssl-3-x64.dll): Apache-2.0, the same text as
  Tycho's LICENSE; OpenSSL's notice is in Python's licences below
libffi (libffi-8.dll): MIT; its notice is in Python's licences below
SQLite (sqlite3.dll): public domain  https://sqlite.org/copyright.html
SDL2 (sdl2dll/dll/SDL2.dll): zlib  https://www.libsdl.org/license.php
PortAudio (libportaudio64bit.dll, from sounddevice): MIT; full text below
PyInstaller's bootloader (tycho.exe): GPL-2.0 with an exception that allows
  distributing it with programs under any licence
  https://pyinstaller.org/en/stable/license.html

Downloaded later, not part of this installer
============================================

Parakeet TDT 0.6B v2 speech model, by NVIDIA: CC-BY-4.0. Downloaded on first run.
  https://huggingface.co/nvidia/parakeet-tdt-0.6b-v2
  ONNX conversion by istupakov: https://huggingface.co/istupakov/parakeet-tdt-0.6b-v2-onnx
NVIDIA CUDA and cuDNN libraries: NVIDIA's licence agreements. Downloaded only by
  `tycho gpu install`, from NVIDIA's packages on PyPI.
  https://docs.nvidia.com/cuda/eula/  https://docs.nvidia.com/deeplearning/cudnn/latest/reference/eula.html
"""


def runtime_distributions() -> dict[str, Distribution]:
    """Tycho's dependencies, all the way down, as installed (no extras, this platform)."""
    found: dict[str, Distribution] = {}
    todo = [APP]
    while todo:
        name = canonicalize_name(todo.pop())
        if name in found:
            continue
        found[name] = dist = distribution(name)
        for line in dist.requires or []:
            requirement = Requirement(line)
            if requirement.marker is None or requirement.marker.evaluate({"extra": ""}):
                todo.append(requirement.name)
    return found


def bundled_distributions(folder: Path, build: Path | None = None) -> set[str]:
    """The installed distributions whose top-level modules PyInstaller put in the build:
    files beside the exe, and the modules packed inside it (``build``'s PYZ contents)."""
    owners = packages_distributions()
    internal = folder / "_internal"
    names: set[str] = (
        {p.name.split(".")[0] for p in internal.iterdir()} if internal.is_dir() else set()
    )
    if build is not None:
        names |= packed_modules(build)
    return {canonicalize_name(d) for n in names for d in owners.get(n, [])}


def packed_modules(build: Path) -> set[str]:
    """Top-level names of the modules PyInstaller packed inside the exe (its PYZ)."""
    found: set[str] = set()
    for toc in build.glob("PYZ-*.toc"):
        _, entries = ast.literal_eval(toc.read_text("utf-8"))
        found |= {str(entry[0]).split(".")[0] for entry in entries}
    return found


def licence(dist: Distribution) -> str:
    """Its licence: the SPDX expression, else the licence field, else the classifiers."""
    meta = dist.metadata
    if expression := meta.get("License-Expression"):
        return expression
    field = (meta.get("License") or "").strip()
    if field and "\n" not in field and len(field) < 100:
        return field
    classifiers = [
        c.split(" :: ")[-1] for c in meta.get_all("Classifier") or [] if c.startswith("License")
    ]
    if classifiers:
        return " OR ".join(classifiers)
    return field.splitlines()[0] if field else ""


def allowed(expression: str) -> bool:
    """Whether it can ship. Of alternatives ("MIT OR GPL-2.0"), one allowed one is enough;
    with no licence at all, no."""
    if not expression.strip():
        return False
    return any(not FORBIDDEN.search(part) for part in re.split(r"\s+OR\s+", expression))


def licence_texts(dist: Distribution) -> list[tuple[str, str]]:
    texts: list[tuple[str, str]] = []
    for file in dist.files or []:
        path = str(file).replace("\\", "/")
        if LICENCE_FILE.search(file.name) and not LEFT_OUT.search(path):
            try:
                texts.append((path, Path(str(file.locate())).read_text("utf-8", errors="replace")))
            except OSError:
                continue
    return texts


def write(folder: Path, build: Path | None = None) -> list[str]:
    """Write the notices (and NOTICE) into ``folder``; return the problems (empty if it
    can ship). ``build`` is PyInstaller's work folder for this build, if there is one."""
    problems: list[str] = []
    if build is not None and not list(build.glob("PYZ-*.toc")):
        problems.append(f"no PYZ table of contents in {build}, so the packed modules are unseen")
    dists = runtime_distributions()
    for name in bundled_distributions(folder, build) - dists.keys():
        dists[name] = distribution(name)
    dists.pop(APP, None)

    summary: list[str] = []
    bodies: list[str] = []
    for name in sorted(dists):
        dist = dists[name]
        terms = licence(dist)
        if not allowed(terms):
            problems.append(f"{name} {dist.version}: {terms or 'no licence found'}")
        summary.append(f"  {dist.metadata['Name']} {dist.version}: {terms}")
        texts = licence_texts(dist)
        heading = f"{dist.metadata['Name']} {dist.version} ({terms})"
        if not texts:
            url = dist.metadata.get("Home-page") or ""
            bodies.append(f"{heading}\n{'-' * len(heading)}\nNo licence file shipped. {url}\n")
        for path, text in texts:
            bodies.append(f"{heading}: {path}\n{'-' * len(heading)}\n{text.strip()}\n")

    header = (
        "Tycho includes the following third-party software. Tycho itself is Apache-2.0.\n\n"
        "Python packages\n===============\n\n"
    )
    for heading, path in NATIVE_LICENCES:
        bodies.append(f"{heading}\n{'-' * len(heading)}\n{path.read_text('utf-8').strip()}\n")
    apache = "OpenSSL 3: Apache License 2.0"
    bodies.append(f"{apache}\n{'-' * len(apache)}\n{(REPO / 'LICENSE').read_text('utf-8')}")
    notices = header + "\n".join(summary) + "\n\n" + NOT_PYTHON + "\n\nLicence texts\n"
    notices += "=============\n\n" + "\n\n".join(bodies)
    (folder / "THIRD-PARTY-NOTICES.txt").write_text(notices, encoding="utf-8")
    # With a byte-order mark: Inno Setup reads a text file without one as ANSI, and
    # the ® signs would come out garbled on the installer's page.
    notice = (REPO / "NOTICE").read_text("utf-8")
    (folder / "NOTICE.txt").write_text(notice, encoding="utf-8-sig")
    return problems


def main(argv: list[str]) -> int:
    folder = Path(argv[0]) if argv else Path("dist/tycho")
    build = Path(argv[1]) if len(argv) > 1 else folder.parent.parent / "build" / folder.name
    problems = write(folder, build)
    print(f"Wrote {folder / 'THIRD-PARTY-NOTICES.txt'}")
    for problem in problems:
        print(f"Can't ship: {problem}", file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
