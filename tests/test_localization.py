"""The game's text for a key, read only when first needed (#598)."""

from pathlib import Path

from tycho.games.star_citizen.localization import lazy_names


def test_names_come_from_the_first_file_there_and_are_read_once(tmp_path: Path) -> None:
    ini = tmp_path / "global.ini"
    ini.write_text("﻿item_Name_qrt_core=Morozov Core\nother,P=Other\n", "utf-8")
    asked: list[str] = []

    def missing() -> Path:
        asked.append("missing")
        return tmp_path / "nowhere.ini"

    def there() -> Path:
        asked.append("there")
        return ini

    names = lazy_names(missing, there)
    assert asked == []  # nothing read until a name is needed
    assert names("item_name_qrt_core") == "Morozov Core"
    assert names("ITEM_NAME_QRT_CORE") == "Morozov Core"
    assert names("no_such_key") == ""
    assert asked == ["missing", "there"]


def test_no_file_means_no_names() -> None:
    assert lazy_names(lambda: None)("item_name_x") == ""
