import pytest

import tycho
from tycho.__main__ import main


def test_version_is_set() -> None:
    assert tycho.__version__


def test_help_lists_the_commands(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exited:
        main(["--help"])
    assert exited.value.code == 0
    out = capsys.readouterr().out
    for command in ("run", "check", "bind", "sounds"):
        assert command in out
