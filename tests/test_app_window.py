"""The app window's bridge and page (ADR 17): what the page can ask, and what it can't."""

from pathlib import Path
from typing import Any

import httpx2
import pytest

from tests.fakes.fake_key_check import FakeKeyCheck
from tycho import __version__, app, credentials, settings, updates
from tycho.desktop import window
from tycho.desktop.bridge import GROUPS, KEYS, Bridge
from tycho.desktop.keys import ProviderKeyCheck
from tycho.settings import Settings


@pytest.fixture
def path(tmp_path: Path) -> Path:
    p = tmp_path / "settings.toml"
    settings.save(Settings(), p)
    return p


def _bridge(path: Path, **options: Any) -> tuple[Bridge, list[tuple[str, object]]]:
    applied: list[tuple[str, object]] = []

    def apply(name: str, value: object) -> bool:
        applied.append((name, value))
        return True

    bridge = Bridge(apply=apply, settings_path=path, **options)
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
        "label": "Voice volume",
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


GOOD = "sk-ant-api03-" + "a" * 40 + "Wxyz"


def test_a_key_is_checked_then_stored_and_never_comes_back_out(
    path: Path, vault: dict[str, str]
) -> None:
    check = FakeKeyCheck()
    bridge, _ = _bridge(path, key_check=check)
    result = bridge.store_key("anthropic", f"  {GOOD}  ")
    assert result == {
        "ok": True,
        "ends": "Wxyz",
        "applies": "restart",
    }  # the brain is made at start
    assert vault["anthropic"] == GOOD
    assert check.checked == ["anthropic"]
    shown = bridge.keys()
    assert shown["anthropic"] == {"label": "Anthropic", "stored": True, "ends": "Wxyz"}
    assert GOOD not in repr(shown)


def test_a_key_the_provider_refuses_isnt_stored(path: Path, vault: dict[str, str]) -> None:
    bridge, _ = _bridge(path, key_check=FakeKeyCheck("Anthropic refused that key."))
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
        ("elevenlabs", "sk_" + "a" * 30 + "\u200b", "invisible one"),  # #246
        ("anthropic", "sk-ant-api03-" + "a" * 30 + "\u200b", "invisible one"),  # #248
        ("anthropic", "sk-ant-short", "cut short"),
        ("openrouter", GOOD, "starts with sk-or-"),  # #112
        ("openai", GOOD, "isn't a key"),
        ("anthropic", 42, "starts with sk-ant-"),
    ],
)
def test_a_key_that_cant_be_right_isnt_even_checked(
    path: Path, vault: dict[str, str], name: object, key: object, problem: str
) -> None:
    check = FakeKeyCheck()
    bridge, _ = _bridge(path, key_check=check)
    result = bridge.store_key(name, key)
    assert result["ok"] is False
    assert problem in str(result["problem"])
    assert check.checked == []
    assert vault == {}


@pytest.mark.parametrize(
    ("name", "key"),
    [("anthropic", GOOD + "\u200b"), ("elevenlabs", "sk_" + "b" * 30 + "\u200b")],
)
def test_a_stored_key_with_a_hidden_character_shows_as_unusable(
    path: Path, vault: dict[str, str], name: str, key: str
) -> None:
    # Stored before the window refused such keys, Tycho ignores it (#246, #248): the
    # Keys page says so instead of "Stored", and shows no ending with the character in it.
    vault[name] = key
    bridge, _ = _bridge(path)
    shown = bridge.keys()[name]
    assert shown["stored"] is True  # so the page offers Remove
    assert shown["unusable"] is True
    assert shown["ends"] == ""
    assert "invisible" in str(shown["problem"])
    assert "\u200b" not in repr(shown)


def test_an_openrouter_key_is_checked_and_stored_from_the_window(
    path: Path, vault: dict[str, str]
) -> None:
    # The brain's other provider (#112).
    check = FakeKeyCheck()
    bridge, _ = _bridge(path, key_check=check)
    key = "sk-or-v1-" + "c" * 60
    assert bridge.store_key("openrouter", key)["ok"] is True
    assert vault["openrouter"] == key
    assert check.checked == ["openrouter"]
    assert bridge.keys()["openrouter"]["label"] == "OpenRouter"


def test_the_brain_settings_are_on_their_own_tab(path: Path) -> None:
    bridge, _ = _bridge(path)
    assert GROUPS["Brain"] == ("brain_provider", "brain_model", "brain_seconds")
    rows = {r["name"]: r for g in bridge.settings()["groups"] for r in g["rows"]}  # type: ignore[union-attr]
    assert rows["brain_provider"]["choices"] == ["anthropic", "openrouter"]
    assert rows["brain_model"]["choices"] == ["Claude Haiku 5.5"]
    assert rows["brain_seconds"]["kind"] == "number"
    for name in GROUPS["Brain"]:
        assert rows[name]["voice"] == "no"
    assert bridge.set("brain_provider", "openrouter")["applies"] == "restart"


def test_the_keys_page_marks_an_unusable_key() -> None:
    page = (Path(__file__).parents[1] / "src/tycho/desktop/app/app.js").read_text("utf-8")
    assert "k.unusable" in page
    assert ">Unusable<" in page


def test_the_setup_keys_note_names_openrouter_as_the_brains_other_choice() -> None:
    """#316: the note named only Anthropic, beside the OpenRouter key field."""
    page = (Path(__file__).parents[1] / "src/tycho/desktop/app/app.js").read_text("utf-8")
    assert "an OpenRouter key does the same with the brain set to OpenRouter" in page


def test_removing_a_key(path: Path, vault: dict[str, str]) -> None:
    vault["elevenlabs"] = "sk_" + "b" * 30
    bridge, _ = _bridge(path)
    assert bridge.remove_key("elevenlabs") == {"ok": True, "removed": True, "applies": "restart"}
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
        ("openrouter", 200, "", ""),  # #112
        ("openrouter", 401, "No auth credentials found", "OpenRouter refused that key"),
    ],
)
def test_checking_a_key_with_its_provider(name: str, status: int, text: str, problem: str) -> None:
    def get(*_args: object, **_kwargs: object) -> _Response:
        return _Response(status, text)

    answer = ProviderKeyCheck(get).check(name, "a-key")
    assert (problem in answer) if problem else answer == ""


@pytest.mark.parametrize(
    ("key", "credits", "problem"),
    [
        (
            '{"data": {"limit": null, "limit_remaining": null}}',
            '{"data": {"total_credits": 10, "total_usage": 3.5}}',
            "",
        ),
        ('{"data": {"limit": 5, "limit_remaining": 0}}', "{}", "reached its spending limit"),
        (
            '{"data": {"limit_remaining": null}}',
            '{"data": {"total_credits": 5, "total_usage": 5.0}}',
            "no credit left",
        ),
        ("not json", "not json", ""),  # an odd answer says nothing: the key check passed
    ],
)
def test_an_openrouter_key_with_nothing_left_to_spend_says_so(
    key: str, credits: str, problem: str
) -> None:
    """#315: OpenRouter's key check answers 200 for a key with no credit left."""

    def get(url: str, **_kwargs: object) -> _Response:
        return _Response(200, credits if url.endswith("/credits") else key)

    answer = ProviderKeyCheck(get).credit("openrouter", "a-key")
    assert (problem in answer) if problem else answer == ""
    assert ProviderKeyCheck(get).credit("anthropic", "a-key") == ""


def test_offline_the_key_isnt_judged() -> None:
    def offline(*_a: object, **_k: object) -> _Response:
        raise httpx2.ConnectError("no route")

    assert "Couldn't reach" in ProviderKeyCheck(offline).check("anthropic", "a-key")


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
            "add_phrase",
            "cancel_capture",
            "capture_push_to_talk",
            "check_updates",
            "choose_game_folder",
            "commands",
            "finish_setup",
            "keys",
            "microphone_level",
            "open_release",
            "remove_phrase",
            "remove_key",
            "set",
            "set_channel",
            "set_microphone",
            "settings",
            "setup_state",
            "status",
            "store_key",
            "tile_preview",
            "undo",
        ]
    )


# --- from the review of #127 -----------------------------------------------------------


def test_a_change_nothing_running_can_take_applies_at_restart(path: Path) -> None:
    bridge = Bridge(apply=lambda _n, _v: False, settings_path=path)
    assert bridge.set("voice_volume", 0.4)["applies"] == "restart"
    assert settings.load(path).voice_volume == 0.4  # saved all the same


def test_a_change_that_fails_to_apply_says_so(path: Path) -> None:
    def broken(_name: str, _value: object) -> bool:
        raise RuntimeError("the Tile's window is gone")

    bridge = Bridge(apply=broken, settings_path=path)
    assert bridge.set("tile_corner", "top left")["applies"] == "restart"


def test_a_locked_credential_store_shows_on_the_keys_tab(
    path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def locked(_name: str) -> str | None:
        raise credentials.CredentialStoreError("Windows Credential Manager is locked")

    monkeypatch.setattr(credentials, "get", locked)
    monkeypatch.setattr(credentials, "delete", locked)
    bridge, _ = _bridge(path)
    assert bridge.keys()["anthropic"]["problem"] == "Windows Credential Manager is locked"
    assert bridge.remove_key("anthropic") == {
        "ok": False,
        "problem": "Windows Credential Manager is locked",
    }


def _tile_box(data_url: str) -> tuple[int, int, int, int]:
    """Where the Tile's bright card is in a preview: its bounding box at 1280 x 720."""
    import base64
    import io

    from PIL import Image

    image = Image.open(io.BytesIO(base64.b64decode(data_url.split(",", 1)[1]))).convert("L")
    box = image.point([255 if v > 200 else 0 for v in range(256)]).getbbox()
    assert box is not None
    return box


def test_the_preview_is_the_tile_at_its_real_size_and_place(path: Path) -> None:
    from tycho.tile.display import look
    from tycho.tile.window import Screen

    bridge, _ = _bridge(path)
    preview = bridge.tile_preview(1280)  # half of 2560: the Tile at half its size
    real = look(settings.load(path), Screen(0, 0, 2560, 1440), "#9fd8ff")
    width = _tile_box(preview)[2] - _tile_box(preview)[0]
    assert abs(width - 400 * real.scale / 2) < 12


def test_the_preview_follows_the_nudges_and_the_ship(path: Path) -> None:
    bridge, _ = _bridge(path, ship_accent=lambda: "#4fb3ff")
    before = bridge.tile_preview(1280)
    settings.update({"tile_nudge_down": 0.3}, path)
    after = bridge.tile_preview(1280)
    assert _tile_box(after)[1] > _tile_box(before)[1] + 150  # moved down
    blue = Bridge(settings_path=path, ship_accent=lambda: "#4fb3ff").tile_preview(640)
    orange = Bridge(settings_path=path, ship_accent=lambda: "#f5901e").tile_preview(640)
    assert blue != orange


def test_the_windows_checks_never_ask_in_the_terminal(monkeypatch: pytest.MonkeyPatch) -> None:
    def no_game(_current: Settings) -> None:
        raise FileNotFoundError("no game")

    def asked(_channel: str) -> None:
        raise AssertionError("the window's checks asked a question in the terminal")

    monkeypatch.setattr(app, "find_install", no_game)
    monkeypatch.setattr(app, "ask_game_root", asked)
    monkeypatch.setattr(app.sys.stdin, "isatty", lambda: True)
    findings = list(app._checks(Settings(), quick=True, ask=False))  # pyright: ignore[reportPrivateUsage]
    assert any(f.what == "Star Citizen" and f.level == "fail" for f in findings)


@pytest.mark.parametrize("starts", [True, False])
def test_closing_the_window_stops_a_capture(monkeypatch: pytest.MonkeyPatch, starts: bool) -> None:
    from types import SimpleNamespace

    class Page:
        def __init__(self) -> None:
            self.cancelled = 0

        def cancel_capture(self) -> dict[str, object]:
            self.cancelled += 1
            return {"ok": True}

    def start(**_options: object) -> None:
        if not starts:
            raise RuntimeError("no WebView2")

    def create_window(*_args: object, **_options: object) -> object:
        return object()

    def import_module(_name: str) -> SimpleNamespace:
        return SimpleNamespace(create_window=create_window, start=start)

    # Only the window module's own importlib: the page is still read from the package.
    monkeypatch.setattr(window, "importlib", SimpleNamespace(import_module=import_module))
    page = Page()
    assert window.AppWindow(page).open() is starts
    assert page.cancelled == 1


def test_the_window_comes_to_the_front_once_shown(monkeypatch: pytest.MonkeyPatch) -> None:
    """Trying the installed app: a second start opened the window behind the others,
    flashing on the taskbar. Once shown, it's raised as the tray's click raises it."""
    from types import SimpleNamespace

    raised: list[str] = []

    class Shown:
        def __init__(self) -> None:
            self.handlers: list[Any] = []

        def __iadd__(self, handler: Any) -> Shown:
            self.handlers.append(handler)
            return self

    class Window:
        def __init__(self) -> None:
            self.events = SimpleNamespace(shown=Shown())

        def restore(self) -> None:
            raised.append("restore")

        def show(self) -> None:
            raised.append("show")

        @property
        def on_top(self) -> bool:
            return False

        @on_top.setter
        def on_top(self, value: bool) -> None:
            raised.append(f"on_top={value}")

    made: list[Window] = []

    def create_window(*_args: object, **_options: object) -> Window:
        made.append(Window())
        return made[-1]

    def start(**_options: object) -> None:
        for handler in made[-1].events.shown.handlers:
            handler()  # pywebview calls it once the window is on screen

    def import_module(_name: str) -> SimpleNamespace:
        return SimpleNamespace(create_window=create_window, start=start)

    monkeypatch.setattr(window, "importlib", SimpleNamespace(import_module=import_module))
    assert window.AppWindow(SimpleNamespace()).open() is True
    assert raised == ["restore", "show", "on_top=True", "on_top=False"]


# --- checking for updates (Ben: 0.4.0 never said 0.5 or 0.6 was out) -------------------


NEWER = updates.Release("99.0.0", "https://github.com/benshapyro/tycho-ai/releases/tag/v99.0.0")


def _releases(*answers: updates.Release | Exception | None) -> tuple[Any, list[int]]:
    asked: list[int] = []

    def latest() -> updates.Release | None:
        asked.append(1)
        answer = answers[min(len(asked), len(answers)) - 1]
        if isinstance(answer, Exception):
            raise answer
        return answer

    return latest, asked


def test_check_for_updates_says_a_newer_tycho_is_out(path: Path) -> None:
    latest, _ = _releases(NEWER)
    bridge, _ = _bridge(path, latest_release=latest)
    assert bridge.check_updates() == {"state": "newer", "current": __version__, "latest": "99.0.0"}


def test_check_for_updates_says_when_this_is_the_latest(path: Path) -> None:
    same = updates.Release(__version__, NEWER.url)
    bridge, _ = _bridge(path, latest_release=_releases(same)[0])
    assert bridge.check_updates()["state"] == "latest"
    bridge, _ = _bridge(path, latest_release=_releases(None)[0])
    assert bridge.check_updates()["state"] == "none"


def test_offline_the_check_says_so_in_plain_words(path: Path) -> None:
    bridge, _ = _bridge(path, latest_release=_releases(updates.UpdateError("timed out"))[0])
    answer = bridge.check_updates()
    assert answer["state"] == "error"
    assert "GitHub" in str(answer["problem"])
    assert "timed out" not in str(answer["problem"])


def test_the_windows_own_check_honours_the_setting_and_the_button_doesnt(path: Path) -> None:
    latest, asked = _releases(NEWER)
    bridge, _ = _bridge(path, latest_release=latest)
    settings.update({"check_for_updates": False}, path)
    assert bridge.check_updates(False) == {"state": "off", "current": __version__}
    assert asked == []  # no request to GitHub at all
    assert bridge.check_updates(True)["state"] == "newer"  # the player asked
    assert asked == [1]


def test_the_windows_own_check_asks_github_once_an_hour(path: Path) -> None:
    latest, asked = _releases(None, NEWER)
    bridge, _ = _bridge(path, latest_release=latest)
    assert bridge.check_updates(False)["state"] == "none"
    assert bridge.check_updates(False)["state"] == "none"  # kept
    assert asked == [1]
    assert bridge.check_updates(True)["state"] == "newer"  # the button always asks
    assert bridge.check_updates(False)["state"] == "newer"  # and refreshes what's kept
    assert asked == [1, 1]


def test_the_windows_own_check_is_kept_an_hour_from_when_it_asked(
    path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#376: it was kept per clock hour, so a check at 10:59 asked again at 11:00."""
    from tycho.desktop import bridge as bridge_module

    now = [3599.0]
    monkeypatch.setattr(bridge_module.time, "monotonic", lambda: now[0])
    latest, asked = _releases(None, NEWER)
    bridge, _ = _bridge(path, latest_release=latest)
    assert bridge.check_updates(False)["state"] == "none"
    now[0] = 3601.0  # a new clock hour, two seconds later
    assert bridge.check_updates(False)["state"] == "none"
    assert asked == [1]
    now[0] = 3599.0 + 3600.0
    assert bridge.check_updates(False)["state"] == "newer"
    assert asked == [1, 1]


def test_a_rate_limit_says_try_later_not_offline(path: Path) -> None:
    """#376: a GitHub rate limit asked whether the PC was online."""
    bridge, _ = _bridge(
        path, latest_release=_releases(updates.RateLimited("GitHub answered 403"))[0]
    )
    answer = bridge.check_updates()
    assert answer["problem"] == "GitHub didn't answer. Try again later."


def test_the_updates_tab_shows_the_get_button_from_the_last_check() -> None:
    """#376: the notice opened the Updates tab with no "Get Tycho X" button, and a check
    that failed left the button on "Checking…"."""
    page = (Path(__file__).parents[1] / "src/tycho/desktop/app/app.js").read_text("utf-8")
    assert '${lastUpdate.state === "newer" ? "" : "hidden"}' in page
    assert "lastUpdate = u;" in page
    assert "} finally {\n        b.disabled = false;" in page


def test_open_release_opens_only_tychos_own_release_page(path: Path) -> None:
    opened: list[str] = []
    elsewhere = updates.Release("99.0.0", "https://example.com/not-tycho")
    for release, expected in (
        (NEWER, NEWER.url),
        (elsewhere, "https://github.com/benshapyro/tycho-ai/releases"),
    ):
        bridge, _ = _bridge(path, latest_release=_releases(release)[0], open_page=opened.append)
        assert bridge.open_release() == {
            "ok": True,
            "url": "https://github.com/benshapyro/tycho-ai/releases",
        }
        bridge.check_updates()
        assert bridge.open_release() == {"ok": True, "url": expected}
    assert opened[-1] == "https://github.com/benshapyro/tycho-ai/releases"


def test_a_browser_that_wont_open_says_where_to_go(path: Path) -> None:
    def broken(_url: str) -> bool:
        raise OSError("no browser")

    bridge, _ = _bridge(path, latest_release=_releases(NEWER)[0], open_page=broken)
    bridge.check_updates()
    answer = bridge.open_release()
    assert answer["ok"] is False
    assert NEWER.url in str(answer["problem"])


def test_the_page_has_a_check_for_updates_button_and_a_notice() -> None:
    html = window.page()
    assert 'id="updateNotice"' in html
    assert "Check for updates" in html
    assert "check_updates(false)" in html  # the window's own check when it opens
