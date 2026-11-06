"""The app window's bridge and page (ADR 17): what the page can ask, and what it can't."""

from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from tycho import credentials, settings
from tycho.desktop import window
from tycho.desktop.bridge import GROUPS, KEYS, Bridge
from tycho.settings import Settings


@pytest.fixture
def path(tmp_path: Path) -> Path:
    p = tmp_path / "settings.toml"
    settings.save(Settings(), p)
    return p


def _bridge(path: Path, **options: Any) -> tuple[Bridge, list[tuple[str, object]]]:
    applied: list[tuple[str, object]] = []
    bridge = Bridge(apply=lambda n, v: applied.append((n, v)), settings_path=path, **options)
    return bridge, applied


def test_every_setting_has_a_place_in_the_app() -> None:
    shown = [name for names in GROUPS.values() for name in names]
    assert sorted(shown) == sorted(n for n in Settings.model_fields if n != "version")
    assert len(shown) == len(set(shown))


def test_settings_come_with_their_kind_limits_and_voice_rules(path: Path) -> None:
    bridge, _ = _bridge(path)
    rows = {r["name"]: r for g in bridge.settings()["groups"] for r in g["rows"]}  # type: ignore[union-attr]
    assert rows["voice_volume"]["kind"] == "percent"
    assert rows["voice_volume"]["maximum"] == 1.0
    assert rows["confirm_seconds"]["kind"] == "number"
    assert rows["confirm_seconds"]["voice"] == "lower_only"
    assert rows["tile_corner"]["kind"] == "choice"
    assert "bottom left" in rows["tile_corner"]["choices"]
    assert rows["tile"]["kind"] == "switch"
    assert rows["push_to_talk"]["kind"] == "setup"
    assert rows["tile"]["applies"] == "now"


def test_a_change_is_saved_and_applied_at_once(path: Path) -> None:
    bridge, applied = _bridge(path)
    result = bridge.set("voice_volume", 0.4)
    assert result == {
        "ok": True,
        "changed": True,
        "name": "voice_volume",
        "value": 0.4,
        "applies": "now",
    }
    assert settings.load(path).voice_volume == 0.4
    assert applied == [("voice_volume", 0.4)]


def test_one_that_applies_at_restart_is_saved_but_not_applied(path: Path) -> None:
    bridge, applied = _bridge(path)
    assert bridge.set("game_channel", "PTU")["applies"] == "restart"
    assert applied == []


def test_the_app_may_do_what_voice_cant(path: Path) -> None:
    bridge, _ = _bridge(path)
    assert bridge.set("confirm_seconds", 12.0)["ok"]  # voice may only shorten it
    assert bridge.set("keep_transcripts", True)["ok"]  # voice may only turn it off


@pytest.mark.parametrize(
    ("name", "value", "problem"),
    [
        ("voice_volume", 3.0, "less than or equal to 1"),
        ("nonsense", 1, "isn't a setting"),
        ("version", 9, "isn't a setting"),
        ("push_to_talk", "f13", "its own control"),
        ("call_me", "<b>Ace</b>", "up to 40 letters"),
    ],
)
def test_bad_changes_are_refused_in_plain_words(
    path: Path, name: str, value: object, problem: str
) -> None:
    bridge, applied = _bridge(path)
    result = bridge.set(name, value)
    assert result["ok"] is False
    assert problem in str(result["problem"])
    assert "pydantic" not in str(result["problem"])
    assert applied == []
    assert settings.load(path) == Settings()


def test_undo_puts_back_the_last_change(path: Path) -> None:
    bridge, applied = _bridge(path)
    bridge.set("tile_corner", "top left")
    bridge.set("voice_volume", 0.3)
    assert bridge.undo()["name"] == "voice_volume"
    assert settings.load(path).voice_volume == 0.8
    assert bridge.undo()["name"] == "tile_corner"
    assert settings.load(path).tile_corner == "top right"
    assert applied[-1] == ("tile_corner", "top right")
    assert bridge.undo()["ok"] is False


def test_the_same_value_again_isnt_a_change(path: Path) -> None:
    bridge, applied = _bridge(path)
    assert bridge.set("voice_volume", 0.8) == {"ok": True, "changed": False}
    assert applied == []
    assert not bridge.settings()["can_undo"]


def test_push_to_talk_shows_by_its_name(path: Path) -> None:
    settings.save(Settings(push_to_talk="mouse:0x06"), path)
    bridge, _ = _bridge(path)
    rows = {r["name"]: r for g in bridge.settings()["groups"] for r in g["rows"]}  # type: ignore[union-attr]
    assert rows["push_to_talk"]["value"] == "Mouse side (forward)"


# --- keys ------------------------------------------------------------------------------


@pytest.fixture
def vault(monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    stored: dict[str, str] = {}
    monkeypatch.setattr(credentials, "get", stored.get)
    monkeypatch.setattr(credentials, "set_secret", stored.__setitem__)

    def delete(name: str) -> bool:
        return stored.pop(name, None) is not None

    monkeypatch.setattr(credentials, "delete", delete)
    return stored


def _recording(into: list[str]) -> Callable[[str, str], str]:
    def check(name: str, _key: str) -> str:
        into.append(name)
        return ""

    return check


def _answering(problem: str) -> Callable[[str, str], str]:
    def check(_name: str, _key: str) -> str:
        return problem

    return check


GOOD = "sk-ant-api03-" + "a" * 40 + "Wxyz"


def test_a_key_is_checked_then_stored_and_never_comes_back_out(
    path: Path, vault: dict[str, str]
) -> None:
    checked: list[str] = []
    bridge, _ = _bridge(path, check_key=_recording(checked))
    assert bridge.store_key("anthropic", f"  {GOOD}  ") == {"ok": True, "ends": "Wxyz"}
    assert vault["anthropic"] == GOOD
    assert checked == ["anthropic"]
    shown = bridge.keys()
    assert shown["anthropic"] == {"label": "Anthropic", "stored": True, "ends": "Wxyz"}
    assert GOOD not in repr(shown)


def test_a_key_the_provider_refuses_isnt_stored(path: Path, vault: dict[str, str]) -> None:
    bridge, _ = _bridge(path, check_key=_answering("Anthropic refused that key."))
    assert bridge.store_key("anthropic", GOOD) == {
        "ok": False,
        "problem": "Anthropic refused that key.",
    }
    assert vault == {}


@pytest.mark.parametrize(
    ("name", "key", "problem"),
    [
        ("anthropic", "sk_wrong_provider_key_123456789", "starts with sk-ant-"),
        ("elevenlabs", "sk-ant-api03-abcdefghijklmnopqrstuvwx", "starts with sk_"),
        ("anthropic", "sk-ant-short", "cut short"),
        ("openai", GOOD, "isn't a key"),
        ("anthropic", 42, "starts with sk-ant-"),
    ],
)
def test_a_key_that_cant_be_right_isnt_even_checked(
    path: Path, vault: dict[str, str], name: object, key: object, problem: str
) -> None:
    checked: list[str] = []
    bridge, _ = _bridge(path, check_key=_recording(checked))
    result = bridge.store_key(name, key)
    assert result["ok"] is False
    assert problem in str(result["problem"])
    assert checked == []
    assert vault == {}


def test_removing_a_key(path: Path, vault: dict[str, str]) -> None:
    vault["elevenlabs"] = "sk_" + "b" * 30
    bridge, _ = _bridge(path)
    assert bridge.remove_key("elevenlabs") == {"ok": True, "removed": True}
    assert bridge.keys()["elevenlabs"]["stored"] is False
    assert set(bridge.keys()) == set(KEYS)


class _Response:
    def __init__(self, status: int, text: str = "") -> None:
        self.status_code = status
        self.text = text


@pytest.mark.parametrize(
    ("name", "status", "text", "problem"),
    [
        ("anthropic", 200, "", ""),
        ("anthropic", 401, "invalid x-api-key", "refused that key"),
        ("elevenlabs", 401, '{"detail":{"status":"missing_permissions"}}', ""),
        ("elevenlabs", 401, '{"detail":{"status":"invalid_api_key"}}', "refused that key"),
        ("anthropic", 529, "overloaded", "error (529)"),
    ],
)
def test_checking_a_key_with_its_provider(
    monkeypatch: pytest.MonkeyPatch, name: str, status: int, text: str, problem: str
) -> None:
    def get(*_args: object, **_kwargs: object) -> _Response:
        return _Response(status, text)

    monkeypatch.setattr(window.httpx2, "get", get)
    answer = window.check_key(name, "a-key")
    assert (problem in answer) if problem else answer == ""


def test_offline_the_key_isnt_judged(monkeypatch: pytest.MonkeyPatch) -> None:
    def offline(*_a: object, **_k: object) -> None:
        raise window.httpx2.ConnectError("no route")

    monkeypatch.setattr(window.httpx2, "get", offline)
    assert "Couldn't reach" in window.check_key("anthropic", "a-key")


# --- the page --------------------------------------------------------------------------


def test_the_page_is_one_file_with_its_style_and_script_inside() -> None:
    html = window.page()
    assert "/*CSS*/" not in html
    assert "/*JS*/" not in html
    assert "--void: #070a10" in html
    assert "pywebviewready" in html
    assert "http://" not in html.replace("http://www.w3.org", "")  # nothing fetched


def test_the_tile_preview_is_drawn_by_the_tile(path: Path) -> None:
    bridge, _ = _bridge(path)
    on = bridge.tile_preview(640)
    assert on.startswith("data:image/png;base64,")
    settings.update({"tile": False}, path)
    assert bridge.tile_preview(640) != on  # off: the cockpit alone


def test_a_broken_check_doesnt_break_the_status_page(path: Path) -> None:
    def broken() -> list[dict[str, str]]:
        raise RuntimeError("the game folder vanished")

    bridge, _ = _bridge(path, findings=broken)
    assert bridge.status() == [
        {
            "level": "fail",
            "what": "Checks",
            "detail": "couldn't run: the game folder vanished",
            "fix": "",
        }
    ]


def test_nothing_private_is_offered_to_the_page() -> None:
    public = [n for n in dir(Bridge) if not n.startswith("_")]
    assert sorted(public) == sorted(
        [
            "about",
            "keys",
            "remove_key",
            "set",
            "settings",
            "status",
            "store_key",
            "tile_preview",
            "undo",
        ]
    )
