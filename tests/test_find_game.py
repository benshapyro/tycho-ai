"""Finding the game (#56): settings, the RSI Launcher's log, the default and common folders."""

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from tycho import app, settings
from tycho.game import install as found
from tycho.game.install import GameInstall, launcher_roots, root_from, search_roots
from tycho.settings import Settings, SettingsError


def _game(root: Path, channel: str = "LIVE") -> Path:
    (root / channel).mkdir(parents=True)
    (root / channel / "Data.p4k").write_bytes(b"")
    return root


def _log(*lines: str) -> str:
    """Lines as the launcher writes them: JSON, so each backslash is doubled."""
    return "".join(
        f'{{ "t": "2026-11-02", "[main][info] ": "{line.replace("\\", "\\\\")}"  }},\n'
        for line in lines
    )


def test_the_launcher_log_names_the_folders_most_recent_first(tmp_path: Path) -> None:
    (tmp_path / "log.log").write_text(
        _log(
            r"[Launcher::launch] Launching Star Citizen LIVE from (D:\Games\RSI\StarCitizen\LIVE)",
            r"[InstallPipeline] Installing Star Citizen PTU 4.11.0-ptu.1 at "
            r"E:\Program Files (x86)\StarCitizen (type: install, forceDP: false)",
            r"[Launcher::launch] Launching Star Citizen PTU from "
            r"(E:\Program Files (x86)\StarCitizen\PTU)",
        ),
        encoding="utf-8",
    )
    (tmp_path / "log.old.log").write_text(
        _log(r"[Launcher::launch] Launching Star Citizen LIVE from (C:\Old\StarCitizen\LIVE)"),
        encoding="utf-8",
    )
    assert launcher_roots(tmp_path) == (
        Path(r"E:\Program Files (x86)\StarCitizen"),  # the brackets are kept
        Path(r"D:\Games\RSI\StarCitizen"),
        Path(r"C:\Old\StarCitizen"),  # the older log comes last
    )


def test_no_launcher_log_means_no_folders(tmp_path: Path) -> None:
    assert launcher_roots(tmp_path / "missing") == ()


def test_the_search_order(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TYCHO_SC_ROOT", str(tmp_path / "custom"))
    monkeypatch.setattr(
        found, "launcher_roots", lambda: (tmp_path / "launcher", found.DEFAULT_ROOT)
    )
    monkeypatch.setattr(found, "fixed_drives", lambda: ("D:\\",))
    roots = search_roots()
    assert roots[:3] == (tmp_path / "custom", tmp_path / "launcher", found.DEFAULT_ROOT)
    assert Path(r"D:\Games\StarCitizen") in roots
    assert len(roots) == len({str(r).lower() for r in roots})  # the default isn't repeated


def test_a_game_the_launcher_installed_elsewhere_is_found(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _game(tmp_path / "Games" / "StarCitizen")
    monkeypatch.delenv("TYCHO_SC_ROOT", raising=False)
    monkeypatch.setattr(found, "launcher_roots", lambda: (root,))
    monkeypatch.setattr(found, "fixed_drives", lambda: ())
    assert GameInstall.find() == GameInstall(root / "LIVE")


def test_the_channel_setting_picks_ptu(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = _game(_game(tmp_path), "PTU")
    monkeypatch.setattr(app, "search_roots", lambda: ())
    current = Settings(game_root=root, game_channel="PTU")
    assert app.find_install(current).folder == root / "PTU"
    with pytest.raises(FileNotFoundError, match="No Star Citizen EPTU"):
        app.find_install(current.model_copy(update={"game_channel": "EPTU"}))


def test_any_level_of_folder_will_do(tmp_path: Path) -> None:
    root = _game(tmp_path / "Roberts Space Industries" / "StarCitizen")
    assert root_from(root / "LIVE") == root
    assert root_from(root) == root
    assert root_from(root.parent) == root
    assert root_from(tmp_path) is None
    assert root_from(root, "PTU") is None


def test_asking_retries_then_gives_up(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = _game(tmp_path / "StarCitizen")
    answers = iter([r"C:\nowhere", f'"{root / "LIVE"}"'])  # quoted, as Explorer copies it
    assert app.ask_game_root("LIVE", lambda _: next(answers)) == root
    assert "no LIVE\\Data.p4k" in capsys.readouterr().out

    assert app.ask_game_root("LIVE", lambda _: "") is None  # Enter skips
    assert app.ask_game_root("LIVE", lambda _: str(tmp_path / "x")) is None  # three misses

    def closed(_: str) -> str:
        raise EOFError

    assert app.ask_game_root("LIVE", closed) is None


def test_check_saves_the_folder_it_was_given(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _game(tmp_path / "StarCitizen")
    monkeypatch.setenv("TYCHO_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setattr(app, "search_roots", lambda: ())
    monkeypatch.setattr(app.sys, "stdin", SimpleNamespace(isatty=lambda: True))

    def answer(channel: str) -> Path:
        return root

    monkeypatch.setattr(app, "ask_game_root", answer)
    # Only the game check matters here: run the checks until they report the game.
    for finding in app._checks(Settings(), quick=True):  # pyright: ignore[reportPrivateUsage]
        if finding.what == "Star Citizen":
            assert finding.level == "ok"
            assert "saved to" in finding.detail
            break
    assert settings.load().game_root == root


def test_a_channel_must_be_one_the_game_has(tmp_path: Path) -> None:
    path = tmp_path / "settings.toml"
    path.write_text('game_channel = "PTU"\n', encoding="utf-8")
    assert settings.load(path).game_channel == "PTU"
    path.write_text('game_channel = "BETA"\n', encoding="utf-8")
    with pytest.raises(SettingsError, match="game_channel"):
        settings.load(path)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows drives")
def test_fixed_drives_include_the_system_drive() -> None:
    assert "C:\\" in found.fixed_drives()
