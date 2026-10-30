import pytest

import tycho
from tycho.__main__ import main


def test_version_is_set() -> None:
    assert tycho.__version__


def test_main_runs(capsys: pytest.CaptureFixture[str]) -> None:
    main()
    assert "Tycho" in capsys.readouterr().out
