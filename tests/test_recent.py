"""Home's last exchanges, kept in memory only (ADR 17, amended 15 Nov 2026)."""

from pathlib import Path

import pytest

from tycho import settings
from tycho.desktop.bridge import Bridge
from tycho.runtime import recent as recent_module
from tycho.runtime.messages import Result
from tycho.runtime.recent import SIZE, Exchange, Recent, exchange


def test_what_the_player_said_and_what_tycho_did() -> None:
    assert exchange(Result(1, "done", text="gear  down", say="Gear down."), 5.0) == Exchange(
        5.0, "gear down", "Gear down.", True
    )
    assert exchange(Result(1, "done", text="lights on"), 5.0) == Exchange(
        5.0, "lights on", "Done.", True
    )
    asking = Result(1, "asking", text="self destruct", question="Self destruct? Say confirm.")
    assert exchange(asking, 0.0) == Exchange(
        0.0, "self destruct", "Self destruct? Say confirm.", True
    )
    refused = exchange(Result(1, "refused", text="eject", detail="Not while landed."), 0.0)
    assert refused == Exchange(0.0, "eject", "Not while landed.", False)
    assert exchange(Result(1, "no_key", text="flares"), 0.0) == Exchange(
        0.0, "flares", "No key is bound for that.", False
    )


def test_nothing_said_isnt_an_exchange() -> None:
    assert exchange(Result(1, "empty"), 0.0) is None
    assert exchange(Result(1, "cancelled", text="gear"), 0.0) is None


def test_only_the_last_ones_are_kept_newest_first() -> None:
    clock = iter(range(100))
    kept = Recent(clock=lambda: float(next(clock)))
    for n in range(SIZE + 5):
        kept.add(Result(n, "done", text=f"say {n}"))
    shown = kept.exchanges()
    assert len(shown) == SIZE
    assert shown[0].said == f"say {SIZE + 4}"
    assert shown[-1].said == "say 5"


def test_nothing_is_written_anywhere() -> None:
    """Memory only: the module opens no file and imports no logging."""
    source = Path(recent_module.__file__).read_text("utf-8")
    assert "open(" not in source
    assert "logging" not in source


def test_the_bridge_hands_them_to_the_page(tmp_path: object) -> None:
    kept = Recent(clock=lambda: 7.0)
    kept.add(Result(1, "replied", text="what's my cargo", say="96 SCU."))
    bridge = Bridge(recent=kept.exchanges, listening=lambda: True, latest_release=lambda: None)
    shown = bridge.recent()
    assert shown["listening"] is True
    assert shown["exchanges"] == [
        {"at": 7.0, "said": "what's my cargo", "did": "96 SCU.", "ok": True}
    ]
    assert Bridge(latest_release=lambda: None).recent()["listening"] is None


def test_details_meant_for_the_log_stay_out_of_home() -> None:
    """Review of #459: a no_key or failed result's detail is action ids and error text."""
    no_key = Result(1, "no_key", text="gear", detail="landing_gear: none of v_toggle has a key")
    assert exchange(no_key, 0.0) == Exchange(0.0, "gear", "No key is bound for that.", False)
    failed = Result(1, "failed", text="gear", detail="KeyError: 'x'")
    assert exchange(failed, 0.0) == Exchange(0.0, "gear", "Something went wrong.", False)


def test_homes_poll_doesnt_read_the_settings_file(monkeypatch: pytest.MonkeyPatch) -> None:
    """Review of #459: the poll every few seconds reads memory only."""

    def no_reading(*_args: object) -> None:
        raise AssertionError("the poll read settings.toml")

    monkeypatch.setattr(settings, "load", no_reading)
    bridge = Bridge(listening=lambda: True, latest_release=lambda: None)
    assert bridge.recent(False) == {"listening": True, "button": "", "exchanges": []}
