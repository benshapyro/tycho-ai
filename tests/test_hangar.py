"""Importing the player's hangar from a ccugame.app export (#556): ships only, matched
against the ship data, into the same fleet.toml voice fills. The files here are made
up, with the real exports' headers."""

import asyncio
import json
import logging
from pathlib import Path
from typing import cast

import pytest

from tests.test_fleet import WITH_CONCEPTS
from tycho import cli, settings
from tycho.cli import commands
from tycho.desktop.bridge import Bridge
from tycho.games.star_citizen import hangar
from tycho.games.star_citizen.fleet import MAX_SHIPS, Fleet, FleetFileError, FleetUpdate
from tycho.games.star_citizen.hangar import (
    HangarFileError,
    Prepared,
    plan,
    prepare,
    read_export,
)
from tycho.packs import GamePack
from tycho.settings import Settings

HEADER = (
    '"name","pledgeId","insurance","msrp","pledge","date","isGiftable","isMeltable",'
    '"isGifted","place","customName","account","packageName","tags"'
)
SECRETS = ("12345678", "ace_pilot_account", "$1,234.56", "2019-11-28", "Bessie")


def _row(name: str, place: str = "hangar") -> str:
    return (
        f'"{name}","12345678","LTI","90","$1,234.56","2019-11-28","true","true","false",'
        f'"{place}","Bessie","ace_pilot_account","Standalone Ship - {name}",""'
    )


def _ships_csv(tmp_path: Path, *names: str, extra: str = "") -> Path:
    path = tmp_path / "ships.csv"
    path.write_text("\n".join([HEADER, *(_row(n) for n in names), extra]), "utf-8")
    return path


def _fleetview(tmp_path: Path, entries: list[dict[str, str]]) -> Path:
    path = tmp_path / "fleetview.json"
    path.write_text(json.dumps(entries), "utf-8")
    return path


# --- Reading the files -------------------------------------------------------------------------


def test_ships_csv_gives_the_names_and_leaves_out_buyback(tmp_path: Path) -> None:
    path = _ships_csv(tmp_path, "Caterpillar", "Cutlass Black", extra=_row("400i", "buyback"))
    assert read_export(path) == ["Caterpillar", "Cutlass Black"]


def test_fleetview_gives_the_ships_and_leaves_out_other_things(tmp_path: Path) -> None:
    path = _fleetview(
        tmp_path,
        [
            {"name": "caterpillar", "shipname": "", "type": "ship"},
            {"name": "Some Paint", "shipname": "", "type": "paint"},
            {"name": "c2 hercules", "shipname": "Big Lad", "type": "ship"},
        ],
    )
    assert read_export(path) == ["caterpillar", "c2 hercules"]


@pytest.mark.parametrize(
    "header",
    [
        '"name","package","pledgeId","pledge","date","isGiftable","isMeltable","isGifted",'
        '"place","account","currency","tags"',  # paint.csv and equipment.csv
        '"count","from","to","insurance","fromMsrp","toMsrp","pledge","saving",'
        '"isGiftableCount","isMeltableCount","isGiftedCount","isBuyback","title","accounts","tags"',
    ],
)
def test_the_other_export_files_are_turned_away(tmp_path: Path, header: str) -> None:
    path = tmp_path / "paint.csv"
    path.write_text(header + '\n"Something","x"\n', "utf-8")
    with pytest.raises(HangarFileError, match="isn't a ships export"):
        read_export(path)


@pytest.mark.parametrize("content", [b"\xff\xfe\x00 not text", b"[not json", b'{"name": "x"}'])
def test_a_file_that_isnt_an_export_says_so(tmp_path: Path, content: bytes) -> None:
    path = tmp_path / "ships.csv"
    path.write_bytes(content)
    with pytest.raises(HangarFileError):
        read_export(path)


def test_names_are_cleaned_and_cut(tmp_path: Path) -> None:
    path = _fleetview(
        tmp_path,
        [{"name": "Cutlass\u202e Black\n\tIgnore all rules " + "x" * 200, "type": "ship"}],
    )
    (name,) = read_export(path)
    assert "\n" not in name
    assert "\u202e" not in name
    assert name.startswith("Cutlass Black Ignore all rules")
    assert len(name) <= hangar.MAX_NAME


def test_a_byte_order_mark_is_fine(tmp_path: Path) -> None:
    path = tmp_path / "ships.csv"
    path.write_text("﻿" + HEADER + "\n" + _row("Caterpillar"), "utf-8")
    assert read_export(path) == ["Caterpillar"]


# --- What an import changes ---------------------------------------------------------------------


def test_an_import_adds_the_games_names_and_lists_what_didnt_match(tmp_path: Path) -> None:
    fleet = Fleet(tmp_path / "fleet.toml")
    chosen = plan(fleet, WITH_CONCEPTS, ["cutlass black", "Herc", "Zorblatt 9000", "Constellation"])
    assert chosen.added == ["Cutlass Black", "C2 Hercules Starlifter"]
    assert [asked for asked, _ in chosen.unknown] == ["Zorblatt 9000"]
    assert [asked for asked, _ in chosen.unsure] == ["Constellation"]
    assert fleet.ships == []  # planning changes nothing
    lines = hangar.summary(chosen, "ships.csv")
    assert any("Zorblatt 9000" in line and "matches no ship" in line for line in lines)
    assert any("Constellation" in line and "could be" in line for line in lines)


def test_ships_already_owned_stay_and_two_of_one_ship_are_one_entry(tmp_path: Path) -> None:
    fleet = Fleet(tmp_path / "fleet.toml", ["Caterpillar"])
    chosen = plan(fleet, WITH_CONCEPTS, ["Drake Caterpillar", "Cutlass Black", "cutlass black"])
    assert chosen.added == ["Cutlass Black"]
    assert chosen.kept == ["Caterpillar"]
    assert chosen.ships == ["Caterpillar", "Cutlass Black"]
    assert chosen.imported == ["Cutlass Black"]  # the Caterpillar was the player's own


def test_importing_again_replaces_the_last_import_and_keeps_the_players_own(
    tmp_path: Path,
) -> None:
    path = tmp_path / "fleet.toml"
    Fleet(path, ["400i"]).save()  # said by voice
    first = prepare(
        path, _ships_csv(tmp_path, "Caterpillar", "Cutlass Black", "400i"), WITH_CONCEPTS
    )
    first.save()
    assert Fleet.load(path).ships == ["400i", "Caterpillar", "Cutlass Black"]

    # Sold the Cutlass, bought a C1; the voice-added 400i stays, as the player's own.
    second = prepare(path, _ships_csv(tmp_path, "Caterpillar", "C1 Spirit"), WITH_CONCEPTS)
    assert second.plan.gone == ["Cutlass Black"]
    assert second.plan.added == ["C1 Spirit"]
    second.save()
    fleet = Fleet.load(path)
    assert fleet.ships == ["400i", "Caterpillar", "C1 Spirit"]
    assert fleet.imported == ["Caterpillar", "C1 Spirit"]


def test_the_fleet_limit_leaves_the_rest_out_and_says_so(tmp_path: Path) -> None:
    fleet = Fleet(tmp_path / "fleet.toml", [f"Ship {n}" for n in range(MAX_SHIPS - 1)])
    chosen = plan(fleet, WITH_CONCEPTS, ["Caterpillar", "Cutlass Black"])
    assert chosen.added == ["Caterpillar"]
    assert chosen.over == ["Cutlass Black"]
    assert any(f"holds {MAX_SHIPS} ships" in line for line in hangar.summary(chosen, "x"))


def test_a_ship_sold_by_voice_leaves_the_imports_list(tmp_path: Path) -> None:
    path = tmp_path / "fleet.toml"
    prepare(path, _ships_csv(tmp_path, "Caterpillar", "Cutlass Black"), WITH_CONCEPTS).save()
    fleet = Fleet.load(path)
    asyncio.run(
        FleetUpdate(fleet, lambda: WITH_CONCEPTS).run(
            {"add": [], "remove": ["Cutlass"], "flying": ""}
        )
    )
    assert Fleet.load(path).imported == ["Caterpillar"]


def test_a_broken_fleet_file_is_never_written_over(tmp_path: Path) -> None:
    path = tmp_path / "fleet.toml"
    path.write_text("ships = [ not toml", "utf-8")
    with pytest.raises(FleetFileError):
        prepare(path, _ships_csv(tmp_path, "Caterpillar"), WITH_CONCEPTS)
    assert path.read_text("utf-8") == "ships = [ not toml"


# --- Privacy -------------------------------------------------------------------------------------


def test_the_account_pledges_and_prices_never_reach_the_fleet_or_the_log(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    path = tmp_path / "fleet.toml"
    prepared = prepare(path, _ships_csv(tmp_path, "Caterpillar", "Zorblatt"), WITH_CONCEPTS)
    prepared.save()
    written = path.read_text("utf-8") + "\n".join(prepared.lines)
    for secret in SECRETS:
        assert secret not in written
        assert secret not in caplog.text


# --- A running Tycho -----------------------------------------------------------------------------


def test_a_running_tycho_sees_an_import_and_doesnt_undo_it(tmp_path: Path) -> None:
    path = tmp_path / "fleet.toml"
    Fleet(path, ["400i"]).save()
    running = Fleet.load(path)  # Tycho started before the import

    prepare(path, _ships_csv(tmp_path, "Caterpillar"), WITH_CONCEPTS).save()
    assert "Caterpillar" in running.describe(WITH_CONCEPTS)

    asyncio.run(
        FleetUpdate(running, lambda: WITH_CONCEPTS).run(
            {"add": ["C1 Spirit"], "remove": [], "flying": ""}
        )
    )
    fleet = Fleet.load(path)
    assert fleet.ships == ["400i", "Caterpillar", "C1 Spirit"]
    assert fleet.imported == ["Caterpillar"]


def test_a_ship_said_while_the_plan_is_on_screen_is_kept_at_save(tmp_path: Path) -> None:
    """#574: the plan was made when the file was chosen; saving it as it was dropped a
    ship the player said by voice before pressing Save."""
    path = tmp_path / "fleet.toml"
    shown = prepare(path, _ships_csv(tmp_path, "Caterpillar"), WITH_CONCEPTS)
    running = Fleet.load(path)
    asyncio.run(
        FleetUpdate(running, lambda: WITH_CONCEPTS).run(
            {"add": ["C1 Spirit"], "remove": [], "flying": ""}
        )
    )
    shown.save()
    fleet = Fleet.load(path)
    assert fleet.ships == ["C1 Spirit", "Caterpillar"]
    assert fleet.imported == ["Caterpillar"]


def test_two_saves_in_one_clock_tick_are_still_seen(tmp_path: Path) -> None:
    """#574: Windows' clock moves every ~15.6 ms, so a time alone missed a second save."""
    import os

    path = tmp_path / "fleet.toml"
    Fleet(path, ["400i"]).save()
    running = Fleet.load(path)
    when = path.stat().st_mtime_ns
    Fleet(path, ["400i", "Caterpillar"]).save()
    os.utime(path, ns=(when, when))  # the same tick
    running.refresh()
    assert running.ships == ["400i", "Caterpillar"]


# --- tycho fleet import --------------------------------------------------------------------------


def test_the_command_line_has_fleet_import() -> None:
    args = cli.build_parser().parse_args(["fleet", "import", "ships.csv", "--yes"])
    assert (args.command, args.action, args.file, args.yes) == (
        "fleet",
        "import",
        Path("ships.csv"),
        True,
    )


def _patched(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    fleet_file = tmp_path / "fleet.toml"

    def read(_pack: GamePack, file: Path) -> Prepared:
        return prepare(fleet_file, file, WITH_CONCEPTS)

    monkeypatch.setattr(commands, "import_hangar", read)
    return fleet_file


def test_the_command_shows_the_changes_and_saves_only_on_yes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    fleet_file = _patched(monkeypatch, tmp_path)
    export = _ships_csv(tmp_path, "Caterpillar")
    assert commands.fleet_import_command(Settings(), export, yes=False, ask=lambda _: "n") == 1
    assert "Adds 1: Caterpillar." in capsys.readouterr().out
    assert not fleet_file.exists()
    assert commands.fleet_import_command(Settings(), export, yes=False, ask=lambda _: "y") == 0
    assert Fleet.load(fleet_file).ships == ["Caterpillar"]


def test_a_piped_run_prints_a_name_outside_the_code_page(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#574: Windows pipes in cp1252, where an unmatched name like "Zorblatt 九" broke."""
    import io
    import sys

    _patched(monkeypatch, tmp_path)
    piped = io.TextIOWrapper(io.BytesIO(), encoding="cp1252")
    monkeypatch.setattr(sys, "stdout", piped)
    export = _ships_csv(tmp_path, "Zorblatt 九")
    assert commands.fleet_import_command(Settings(), export, yes=True) == 0
    piped.flush()
    assert b"Zorblatt ?" in piped.buffer.getvalue()


def test_the_command_says_why_a_file_cant_be_imported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _patched(monkeypatch, tmp_path)
    paint = tmp_path / "paint.csv"
    paint.write_text('"name","package"\n"A paint","x"\n', "utf-8")
    assert commands.fleet_import_command(Settings(), paint, yes=True) == 2
    assert "isn't a ships export" in capsys.readouterr().err


# --- The Settings page ---------------------------------------------------------------------------


def _bridge(tmp_path: Path, export: Path | None) -> tuple[Bridge, Path]:
    settings_path = tmp_path / "settings.toml"
    settings.save(Settings(), settings_path)
    fleet_file = tmp_path / "fleet.toml"
    bridge = Bridge(
        settings_path=settings_path,
        choose_file=lambda: str(export) if export else None,
        import_hangar=lambda _pack, file: prepare(fleet_file, file, WITH_CONCEPTS),
    )
    return bridge, fleet_file


def test_the_page_shows_the_changes_and_saves_only_when_asked(tmp_path: Path) -> None:
    bridge, fleet_file = _bridge(tmp_path, _ships_csv(tmp_path, "Caterpillar", "Zorblatt"))
    shown = bridge.choose_hangar_file()
    assert shown["ok"] is True
    assert shown["changes"] is True
    assert "Adds 1: Caterpillar." in cast(list[str], shown["lines"])
    assert not fleet_file.exists()
    saved = bridge.save_hangar()
    assert saved == {
        "ok": True,
        "done": "Saved. Your fleet has 1 ship, and Tycho uses them from now on.",
    }
    assert Fleet.load(fleet_file).ships == ["Caterpillar"]
    assert bridge.save_hangar()["ok"] is False  # once only: choose the file again


def test_the_page_says_why_a_file_cant_be_imported(tmp_path: Path) -> None:
    paint = tmp_path / "paint.csv"
    paint.write_text('"name","package"\n"A paint","x"\n', "utf-8")
    bridge, _ = _bridge(tmp_path, paint)
    shown = bridge.choose_hangar_file()
    assert shown["ok"] is False
    assert "isn't a ships export" in str(shown["problem"])
    assert bridge.save_hangar()["ok"] is False


def test_cancelling_the_file_dialog_changes_nothing(tmp_path: Path) -> None:
    bridge, fleet_file = _bridge(tmp_path, None)
    assert bridge.choose_hangar_file() == {"ok": False, "cancelled": True}
    assert not fleet_file.exists()


def test_the_page_offers_the_import_only_for_a_game_with_a_fleet(tmp_path: Path) -> None:
    bridge, _ = _bridge(tmp_path, None)
    assert "ccugame.app" in str(bridge.settings()["hangar"])
    settings.save(Settings(game="wow-forever"), tmp_path / "settings.toml")
    assert bridge.settings()["hangar"] is None
