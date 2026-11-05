"""Settings by voice (#111): the rules come from the schema, a change is saved without
losing anything else in the file, and some settings only go down or off by voice."""

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from tycho import settings
from tycho.settings import Settings, SettingsError
from tycho.spoken_settings import (
    UNDO_SECONDS,
    Parsed,
    Refused,
    SettingsTool,
    check_rule,
    parse,
    voice_names,
)


def _run(tool: SettingsTool, action: str, *changes: tuple[str, str]) -> tuple[bool, Any]:
    arguments: dict[str, object] = {
        "action": action,
        "changes": [{"name": n, "value": v} for n, v in changes],
    }
    result = asyncio.run(tool.run(arguments))
    return result.is_error, json.loads(result.content)


def _file(tmp_path: Path, text: str = "") -> Path:
    path = tmp_path / "settings.toml"
    path.write_text(text, "utf-8")
    return path


# --- The rules live in the schema -------------------------------------------------------------


def test_every_setting_has_a_voice_rule() -> None:
    for name in Settings.model_fields:
        rule = settings.rule(name)
        assert rule["voice"] in ("yes", "lower_only", "no"), name
        assert rule["applies"] in ("now", "restart"), name
        if rule["voice"] != "yes":
            assert rule.get("how"), f"{name} must say how to change it instead"


def test_the_rules_are_in_the_schema_for_the_settings_screen() -> None:
    schema = settings.json_schema()["properties"]
    assert schema["voice_volume"]["voice"] == "yes"
    assert schema["keep_transcripts"]["voice"] == "lower_only"


def test_safety_and_privacy_settings_only_go_down_or_off_by_voice() -> None:
    """The author's calls (2026-11-05): a misheard sentence never weakens a safeguard."""
    for name in ("confirm_seconds", "keep_transcripts", "web_search", "check_for_updates"):
        assert settings.rule(name)["voice"] == "lower_only", name


def test_some_settings_never_change_by_voice() -> None:
    for name in ("push_to_talk", "microphone", "game_root", "version"):
        assert settings.rule(name)["voice"] == "no", name
        assert name not in voice_names()


# --- Reading what was said --------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "said", "current", "expected"),
    [
        ("voice_volume", "40%", 0.8, 0.4),
        ("voice_volume", "40", 0.8, 0.4),
        ("voice_volume", "0.4", 0.8, 0.4),
        ("voice_volume", "-10%", 0.8, 0.7),
        ("voice_volume", "+0.1", 0.5, 0.6),
        ("voice_volume", "max", 0.5, 1.0),
        ("voice_volume", "mute", 0.5, 0.0),
        ("confirm_seconds", "3", 5.0, 3.0),
        ("confirm_seconds", "-2", 5.0, 3.0),
        ("confirm_seconds", "4 seconds", 5.0, 4.0),
        ("web_search", "off", True, False),
        ("keep_transcripts", "no", True, False),
        ("game_channel", "ptu", "LIVE", "PTU"),
        ("transcription_device", "CPU", "auto", "cpu"),
    ],
)
def test_values_are_read_as_the_settings_own_type(
    name: str, said: str, current: object, expected: object
) -> None:
    assert parse(name, said, current).value == expected


def test_a_value_out_of_range_comes_to_the_nearest_allowed_and_says_so() -> None:
    assert parse("voice_volume", "150%", 0.5) == Parsed(1.0, "the nearest allowed is 100%")
    assert parse("confirm_seconds", "1", 5.0) == Parsed(2.0, "the nearest allowed is 2")


@pytest.mark.parametrize(
    ("name", "said"),
    [("voice_volume", "loud-ish"), ("web_search", "maybe"), ("game_channel", "beta")],
)
def test_a_value_that_makes_no_sense_is_refused(name: str, said: str) -> None:
    with pytest.raises(Refused):
        parse(name, said, getattr(Settings(), name))


def test_lower_only_settings_refuse_going_up_by_voice() -> None:
    with pytest.raises(Refused, match="only goes down or off"):
        check_rule("keep_transcripts", False, True)
    with pytest.raises(Refused, match="only goes down or off"):
        check_rule("confirm_seconds", 5.0, 10.0)
    check_rule("confirm_seconds", 5.0, 3.0)  # shorter is fine
    check_rule("web_search", True, False)  # off is fine


def test_a_setting_that_never_changes_by_voice_says_how_instead() -> None:
    with pytest.raises(Refused, match="tycho bind"):
        check_rule("push_to_talk", None, "mouse:0x06")


# --- The tool ---------------------------------------------------------------------------------


def test_a_change_is_saved_and_said_in_percent(tmp_path: Path) -> None:
    path = _file(tmp_path)
    error, result = _run(SettingsTool(path), "set", ("voice_volume", "40%"))
    assert not error
    assert result["changed"] == [
        {"name": "voice_volume", "from": "80%", "to": "40%", "takes_effect": "when Tycho restarts"}
    ]
    assert result["undo"]
    assert settings.load(path).voice_volume == 0.4


def test_a_now_setting_changes_the_running_session(tmp_path: Path) -> None:
    applied: list[object] = []
    tool = SettingsTool(_file(tmp_path), live={"voice_volume": applied.append})
    _, result = _run(tool, "set", ("voice_volume", "-30%"))
    assert applied == [0.5]
    assert result["changed"][0]["takes_effect"] == "now"


def test_a_failed_live_change_is_still_saved_for_the_next_start(tmp_path: Path) -> None:
    def broken(_: object) -> None:
        raise RuntimeError("no audio device")

    path = _file(tmp_path)
    _, result = _run(
        SettingsTool(path, live={"voice_volume": broken}), "set", ("voice_volume", "0.3")
    )
    assert result["changed"][0]["takes_effect"] == "when Tycho restarts"
    assert settings.load(path).voice_volume == 0.3


def test_turning_a_privacy_setting_on_is_refused_and_the_file_is_untouched(
    tmp_path: Path,
) -> None:
    text = "web_search = false\n"
    path = _file(tmp_path, text)
    error, result = _run(SettingsTool(path), "set", ("web_search", "on"))
    assert not error
    assert "changed" not in result
    assert "settings.toml" in result["problems"][0]["problem"]
    assert path.read_text("utf-8") == text


def test_good_changes_are_made_even_when_another_in_the_request_is_refused(
    tmp_path: Path,
) -> None:
    path = _file(tmp_path)
    _, result = _run(
        SettingsTool(path), "set", ("confirm_seconds", "10"), ("feedback_volume", "20%")
    )
    assert [c["name"] for c in result["changed"]] == ["feedback_volume"]
    assert result["problems"][0]["name"] == "confirm_seconds"
    assert settings.load(path).confirm_seconds == 5.0


def test_asking_for_what_its_already_set_to_changes_nothing(tmp_path: Path) -> None:
    _, result = _run(SettingsTool(_file(tmp_path)), "set", ("voice_volume", "80%"))
    assert "changed" not in result
    assert "already" in result["problems"][0]["problem"]


def test_undo_puts_back_the_last_change_even_one_that_only_goes_down(tmp_path: Path) -> None:
    """Undo only restores what the player had, so it may switch web search back on."""
    path = _file(tmp_path)
    tool = SettingsTool(path)
    _run(tool, "set", ("voice_volume", "40%"))
    _run(tool, "set", ("web_search", "off"))
    _, result = _run(tool, "undo")
    assert result["undone"][0] == {
        "name": "web_search",
        "from": False,
        "to": True,
        "takes_effect": "when Tycho restarts",
    }
    _run(tool, "undo")
    assert settings.load(path) == Settings()
    error, result = _run(tool, "undo")
    assert error
    assert "no recent change" in result["error"]


def test_undo_only_means_a_recent_change(tmp_path: Path) -> None:
    """Minutes later, "undo" more likely means the last key press: the change stays."""
    now = [1000.0]
    path = _file(tmp_path)
    tool = SettingsTool(path, clock=lambda: now[0])
    _run(tool, "set", ("voice_volume", "40%"))
    now[0] += UNDO_SECONDS + 1
    error, result = _run(tool, "undo")
    assert error
    assert "no recent change" in result["error"]
    assert settings.load(path).voice_volume == 0.4


def test_get_lists_the_settings_with_how_each_can_change(tmp_path: Path) -> None:
    _, result = _run(SettingsTool(_file(tmp_path, "voice_volume = 0.25\n")), "get")
    found = result["settings"]
    assert "version" not in found
    assert found["voice_volume"] == {"value": "25%", "by_voice": "yes"}
    assert found["microphone"] == {"value": "not set", "by_voice": "no"}


def test_a_broken_settings_file_is_reported_and_not_written(tmp_path: Path) -> None:
    path = _file(tmp_path, "voice_volume = [broken\n")
    error, result = _run(SettingsTool(path), "set", ("voice_volume", "40%"))
    assert error
    assert "settings.toml has a problem" in result["error"]
    assert path.read_text("utf-8") == "voice_volume = [broken\n"


def test_the_schema_suits_strict_tool_use() -> None:
    def check(node: object) -> None:
        if isinstance(node, dict):
            node_dict: dict[str, Any] = node  # pyright: ignore[reportUnknownVariableType]
            if node_dict.get("type") == "object":
                assert node_dict["additionalProperties"] is False
                assert set(node_dict["required"]) == set(node_dict["properties"])
            for value in node_dict.values():
                check(value)

    schema: dict[str, Any] = dict(SettingsTool().input_schema)
    check(schema)
    names = schema["properties"]["changes"]["items"]["properties"]["name"]["enum"]
    assert set(names) == set(voice_names())


# --- Saving without losing anything ----------------------------------------------------------


def test_an_update_keeps_hand_edits_and_settings_it_doesnt_know(tmp_path: Path) -> None:
    path = _file(tmp_path, 'microphone = "Arctis"\nfrom_a_newer_tycho = 3\n')
    settings.update({"voice_volume": 0.5}, path)
    assert settings.load(path).microphone == "Arctis"
    assert settings.load(path).voice_volume == 0.5
    assert settings.unknown_keys(path) == ("from_a_newer_tycho",)
    assert "from_a_newer_tycho = 3" in path.read_text("utf-8")


def test_an_update_refuses_to_drop_something_it_cant_write_back(tmp_path: Path) -> None:
    text = "[from_a_newer_tycho]\nvalue = 1\n"
    path = _file(tmp_path, text)
    with pytest.raises(SettingsError, match="isn't a simple value"):
        settings.update({"voice_volume": 0.5}, path)
    assert path.read_text("utf-8") == text


def test_an_invalid_update_writes_nothing(tmp_path: Path) -> None:
    path = _file(tmp_path, "voice_volume = 0.3\n")
    with pytest.raises(SettingsError):
        settings.update({"voice_volume": 7.0}, path)
    assert path.read_text("utf-8") == "voice_volume = 0.3\n"


@pytest.mark.parametrize(
    ("name", "said", "value"),
    [
        ("tile_size", "extra large", "extra large"),
        ("tile_corner", "top left", "top left"),
        ("tile_see_through", "40%", 0.4),
        ("tile_see_through", "max", 0.6),
        ("tile", "off", False),
        ("tile_colour", "orange", "orange"),
    ],
)
def test_tile_settings_by_voice(name: str, said: str, value: object) -> None:
    current = getattr(Settings(), name)
    assert parse(name, said, current).value == value
