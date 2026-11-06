"""The build's third-party notices and licence check (#56)."""

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parent.parent


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("notices", ROOT / "tools" / "notices.py")
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["notices"] = module
    spec.loader.exec_module(module)
    return module


notices = _load()


@pytest.mark.parametrize(
    ("expression", "ships"),
    [
        ("MIT", True),
        ("BSD-3-Clause AND 0BSD AND MIT AND Zlib AND CC0-1.0", True),
        ("MPL-2.0", True),  # file-level: pysdl2-dll's own code, unmodified
        ("MIT OR GPL-2.0-or-later", True),  # one allowed alternative is enough
        ("GPL-3.0-only", False),
        ("LGPL-2.1", False),
        ("AGPL-3.0", False),
        ("GNU General Public License v3 (GPLv3)", False),
        ("CC-BY-NC-4.0", False),
        ("Creative Commons Attribution-NonCommercial", False),
        ("", False),  # no licence at all
    ],
)
def test_which_licences_can_ship(expression: str, ships: bool) -> None:
    assert notices.allowed(expression) is ships


def test_the_notices_cover_the_build(tmp_path: Path) -> None:
    (tmp_path / "_internal" / "_pytest").mkdir(parents=True)  # bundled, not a dependency
    assert notices.write(tmp_path) == []
    text = (tmp_path / "THIRD-PARTY-NOTICES.txt").read_text("utf-8")
    for expected in ("onnx-asr", "onnxruntime-gpu", "numpy", "Parakeet", "SDL2", "pytest"):
        assert expected in text
    assert "tycho-ai" not in text.split("Licence texts")[0]  # Tycho isn't third-party
    assert "SDL2_mixer" not in text  # only SDL2.dll ships
    assert "Mozilla Public License" in text  # licence texts are included in full


def test_native_libraries_get_their_licence_texts_in_full(tmp_path: Path) -> None:
    # MIT and BSD need the notice itself, not a link (audit, 2026-11-03).
    assert notices.write(tmp_path) == []
    text = (tmp_path / "THIRD-PARTY-NOTICES.txt").read_text("utf-8")
    for expected in (
        "Ross Bencina and Phil Burk",  # PortAudio
        "PYTHON SOFTWARE FOUNDATION LICENSE VERSION 2",
        "libffi",
        "expat",
        "libmpdec",
        "Apache License",  # OpenSSL 3
    ):
        assert expected in text
    notice = (tmp_path / "NOTICE.txt").read_bytes()
    assert notice.startswith(b"\xef\xbb\xbf")  # Inno Setup reads UTF-8 only with one
    assert "Star Citizen®" in notice.decode("utf-8-sig")
    assert "not affiliated" in notice.decode("utf-8-sig")


def test_modules_packed_inside_the_exe_are_covered(tmp_path: Path) -> None:
    # pytest and pygments were packed into tycho.exe with no notice (audit, 2026-11-03).
    build = tmp_path / "build"
    build.mkdir()
    entries = [("pygments.lexer", "x.py", "PYMODULE"), ("json", "y.py", "PYMODULE")]
    (build / "PYZ-00.toc").write_text(repr(("PYZ-00.pyz", entries)), "utf-8")
    dist = tmp_path / "dist"
    dist.mkdir()
    assert notices.write(dist, build) == []
    assert "Pygments" in (dist / "THIRD-PARTY-NOTICES.txt").read_text("utf-8")


def test_a_build_without_its_table_of_contents_cant_pass(tmp_path: Path) -> None:
    assert notices.write(tmp_path, tmp_path / "missing") != []


def test_a_package_naming_no_licence_is_read_from_its_licence_file() -> None:
    from importlib.metadata import distribution

    # clr_loader (under pywebview) ships the MIT text but names no licence in its metadata.
    terms = notices.licence(distribution("clr_loader"))
    assert terms == "MIT (from its licence file)"
    assert notices.allowed(terms)
