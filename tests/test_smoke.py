import pytest

import ultimo
from ultimo.__main__ import main


def test_version_is_set() -> None:
    assert ultimo.__version__


def test_main_runs(capsys: pytest.CaptureFixture[str]) -> None:
    main()
    assert "Ultimo" in capsys.readouterr().out
