from pathlib import Path

import pytest

from tycho import paths, settings
from tycho.settings import Settings, SettingsError


def test_defaults_when_there_is_no_file(tmp_path: Path) -> None:
    loaded = settings.load(tmp_path / "missing.toml")
    assert loaded == Settings()
    assert loaded.transcription_device == "auto"
    assert loaded.keep_transcripts is False


def test_round_trip_keeps_every_value(tmp_path: Path) -> None:
    original = Settings(
        push_to_talk="mouse:0x06",
        microphone="Headset Microphone (Arctis Pro",
        game_root=Path(r"D:\Games\StarCitizen"),
        transcription_device="cpu",
        feedback_volume=0.25,
        keep_transcripts=True,
    )
    path = settings.save(original, tmp_path / "settings.toml")
    assert settings.load(path) == original


def test_the_file_explains_each_setting(tmp_path: Path) -> None:
    text = settings.to_toml(Settings(push_to_talk="mouse:0x06"))
    assert 'push_to_talk = "mouse:0x06"' in text
    assert "# The button you hold while talking to Tycho" in text
    assert "# microphone = (not set)" in text


@pytest.mark.parametrize(
    ("content", "message"),
    [
        ("feedback_volume = 3.0", "feedback_volume"),
        ("confirm_seconds = 60", "confirm_seconds"),  # a question must not linger
        ('voice = "Shipsmind please"', "voice"),
        ('voice_model = "eleven_v3"', "voice_model"),
        ('transcription_device = "tpu"', "transcription_device"),
        ('push_to_talk = "elbow:1"', "push_to_talk"),
        ("version = 99", "newer Tycho"),
        ("this is not toml", "Couldn't read"),
    ],
)
def test_bad_files_get_a_clear_error(tmp_path: Path, content: str, message: str) -> None:
    path = tmp_path / "settings.toml"
    path.write_text(content, encoding="utf-8")
    with pytest.raises(SettingsError, match=message):
        settings.load(path)


def test_a_bad_value_says_how_to_fix_it(tmp_path: Path) -> None:
    path = tmp_path / "settings.toml"
    path.write_text("feedback_volume = 3.0", encoding="utf-8")
    with pytest.raises(SettingsError, match="delete it to use the default"):
        settings.load(path)


def test_settings_this_tycho_doesnt_know_are_ignored_not_fatal(tmp_path: Path) -> None:
    # A typo, or a setting a newer Tycho saved to the same (roaming) file.
    path = tmp_path / "settings.toml"
    path.write_text("keep_transcripts = true\nvolume = 0.5\nfuture_thing = 1\n", "utf-8")
    assert settings.load(path).keep_transcripts is True
    assert settings.unknown_keys(path) == ("volume", "future_thing")


def test_unknown_keys_is_empty_without_a_readable_file(tmp_path: Path) -> None:
    assert settings.unknown_keys(tmp_path / "missing.toml") == ()
    broken = tmp_path / "broken.toml"
    broken.write_text("this is not toml", "utf-8")
    assert settings.unknown_keys(broken) == ()


def test_files_without_a_version_are_treated_as_version_1(tmp_path: Path) -> None:
    path = tmp_path / "settings.toml"
    path.write_text("keep_transcripts = true\n", encoding="utf-8")
    assert settings.load(path).version == 1


def test_a_file_saved_with_a_byte_order_mark_still_loads(tmp_path: Path) -> None:
    path = tmp_path / "settings.toml"
    path.write_bytes(b'\xef\xbb\xbfpush_to_talk = "mouse:0x06"\n')  # as Notepad may save it
    assert settings.load(path).push_to_talk == "mouse:0x06"


def test_push_to_talk_parses_to_a_binding() -> None:
    binding = Settings(push_to_talk="mouse:0x06").push_to_talk_binding
    assert binding is not None
    assert str(binding) == "mouse:0x06"


def test_schema_has_descriptions_for_the_settings_screen() -> None:
    schema = settings.json_schema()
    for name, field in schema["properties"].items():
        assert field.get("description"), name


def test_paths_follow_overrides(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TYCHO_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("TYCHO_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.delenv("TYCHO_MODELS", raising=False)
    assert paths.settings_file() == tmp_path / "config" / "settings.toml"
    assert paths.models_dir() == tmp_path / "data" / "models"
    assert paths.logs_dir() == tmp_path / "data" / "logs"
    monkeypatch.setenv("TYCHO_MODELS", str(tmp_path / "big"))
    assert paths.models_dir() == tmp_path / "big"


def test_tile_cards_to_hide_are_card_kinds_and_never_the_question() -> None:
    assert (
        Settings(tile_hidden_cards="Headlines; ship, confirm").tile_hidden_cards
        == "headlines, ship"
    )
    with pytest.raises(ValueError, match="no card is called"):
        Settings(tile_hidden_cards="news")


def test_every_tile_setting_changes_now_by_voice() -> None:
    names = [n for n in Settings.model_fields if n == "tile" or n.startswith("tile_")]
    assert len(names) == 13
    for name in names:
        assert settings.rule(name) == {"voice": "yes", "applies": "now"}, name


def test_changes_from_two_threads_at_once_are_both_kept(tmp_path: Path) -> None:
    import threading

    path = tmp_path / "settings.toml"
    settings.save(Settings(), path)
    names = ["tile_quiet", "tile_high_contrast", "keep_transcripts", "tile_talk_light"]
    values = {
        "tile_quiet": True,
        "tile_high_contrast": True,
        "keep_transcripts": True,
        "tile_talk_light": False,
    }
    threads = [
        threading.Thread(target=settings.update, args=({n: values[n]}, path)) for n in names * 5
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    saved = settings.load(path)
    assert all(getattr(saved, n) == values[n] for n in names)


def test_the_voice_is_george_unless_set_and_its_model_never_changes_by_voice() -> None:
    # ADR 9, amended 8 November 2026 (#242): George on Flash, the player's own choice after.
    assert Settings().voice == "George"
    assert Settings().voice_model == "the voice's own"
    assert Settings(voice=" The Ship's Mind ").voice == "The Ship's Mind"
    assert settings.rule("voice_model") == {
        "voice": "no",
        "applies": "restart",
        "how": settings.FILE_OR_APP,
    }
    # The voice itself changes by voice, by name only (#355; spoken_settings.parse).
    assert settings.rule("voice") == {
        "voice": "yes",
        "applies": "restart",
        "how": settings.FILE_OR_APP,
    }


def test_a_file_with_the_old_world_of_warcraft_id_gets_world_of_warcraft_forever(
    tmp_path: Path,
) -> None:
    # The pack was renamed before it shipped (#349); a file saved on main kept the old id.
    path = tmp_path / "settings.toml"
    path.write_text('game = "world-of-warcraft"\n', "utf-8")
    assert settings.load(path).game == "wow-forever"
