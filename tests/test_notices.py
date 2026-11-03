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
