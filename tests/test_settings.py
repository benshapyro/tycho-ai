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
        ('transcription_device = "tpu"', "transcription_device"),
        ('push_to_talk = "elbow:1"', "push_to_talk"),
        ("volume = 0.5", "volume"),  # unknown key: probably a typo
        ("version = 99", "newer Tycho"),
        ("this is not toml", "Couldn't read"),
    ],
)
def test_bad_files_get_a_clear_error(tmp_path: Path, content: str, message: str) -> None:
    path = tmp_path / "settings.toml"
    path.write_text(content, encoding="utf-8")
    with pytest.raises(SettingsError, match=message):
        settings.load(path)


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
