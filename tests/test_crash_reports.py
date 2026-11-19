"""Crash reports, only if the player says yes (ADR 4's amendment, #199): what a report
holds, what never leaves the PC, and saying no deletes what's waiting."""

import json
import threading
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any, cast
from urllib.parse import urlsplit

import httpx2
import pytest

from tycho import crash_reports, logs, paths, settings
from tycho.crash_reports import Context, build, envelope, scrub, send_waiting
from tycho.desktop.bridge import Bridge
from tycho.settings import Settings
from tycho.spoken_settings import Refused, check_rule, parse

CONTEXT = Context(pack="star-citizen", provider="anthropic")
DSN = "https://publickey@o1.ingest.sentry.io/42"
KEY = "sk-ant-api03-PLANTEDKEYplantedkeyPLANTEDKEY"
TRANSCRIPT = "request landing at port olisar please"
NAME = "Zyleth"
USER_PATH = r"C:\Users\zyleth\Documents\secret plans.txt"


def _crash() -> BaseException:
    """An error raised in Tycho's own code (``settings``), whose message and locals hold
    everything that must never be sent."""
    said, key, folder = TRANSCRIPT, KEY, USER_PATH  # locals a careless report would take
    with pytest.raises(settings.SettingsError, match="secret plans") as raised:
        settings.update({"voice_volume": f"{said} {key} {NAME} {folder}"}, paths.settings_file())
    return raised.value  # the message holds it all: a report must not


@pytest.fixture
def report() -> dict[str, Any]:
    logs.register_secret(KEY)
    found = scrub(build(_crash(), CONTEXT), [NAME])
    assert found is not None
    return found


# --- What a report holds, and what it never does ------------------------------------------


def test_a_report_holds_only_the_allowlist(report: dict[str, Any]) -> None:
    assert set(report) == {
        "kind",
        "error",
        "frames",
        "tycho",
        "windows",
        "pack",
        "provider",
        "time",
    }
    assert report["error"] == "tycho.settings.SettingsError"
    assert report["pack"] == "star-citizen"
    assert report["provider"] == "anthropic"


def test_nothing_said_no_key_name_or_path_is_in_a_report(report: dict[str, Any]) -> None:
    text = json.dumps(report).lower()
    for planted in (TRANSCRIPT, "olisar", KEY.lower(), NAME.lower(), "zyleth", "secret plans"):
        assert planted not in text, planted
    assert "\\" not in text
    assert "/" not in text


def test_tycho_frames_say_where_and_anyone_else_s_only_the_package(
    report: dict[str, Any],
) -> None:
    ours = [f for f in report["frames"] if "line" in f]
    assert ours
    assert all(f["module"].startswith("tycho") for f in ours)
    assert ours[-1]["module"] == "tycho.settings"
    assert ours[-1]["function"] == "_update"
    others = [f for f in report["frames"] if "line" not in f]
    assert all(set(f) == {"module"} for f in others)


@pytest.mark.parametrize(
    "change",
    [
        {"error": r"C:\Users\zyleth\x.py"},
        {"windows": "Windows 11 /home/zyleth"},
        {"pack": "Star Citizen!"},
        {"message": "anything extra"},
        {"frames": [{"module": "tycho.x", "function": "f", "line": 1, "locals": "x"}]},
        {"frames": [{"module": "plugin", "function": "f", "line": 1}]},
        {"frames": [{"module": "tycho.x", "function": "f", "line": True}]},
    ],
)
def test_scrub_drops_a_report_with_anything_unexpected(
    report: dict[str, Any], change: dict[str, Any]
) -> None:
    assert scrub({**report, **change}, []) is None


def test_scrub_drops_a_report_with_a_name_or_a_key_in_it(report: dict[str, Any]) -> None:
    named = {**report, "frames": [{"module": "tycho.zyleth", "function": "f", "line": 1}]}
    assert scrub(named, [NAME]) is None
    keyed = {**report, "error": "x.sk-ant-abcdefgh12345678"}
    assert scrub(keyed, []) is None


def test_the_windows_user_name_is_never_sent(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("USERNAME", "zyleth")
    assert "zyleth" in crash_reports.never_sent()


# --- Sending: only while the player says yes -------------------------------------------------


@pytest.fixture
def waiting(report: dict[str, Any]) -> Path:
    crash_reports.keep(report)
    return paths.crash_reports_dir()


class Posts:
    def __init__(self, status: int = 200, error: Exception | None = None) -> None:
        self.calls: list[tuple[str, Mapping[str, str], bytes]] = []
        self.status, self.error = status, error

    def __call__(self, url: str, headers: Mapping[str, str], body: bytes) -> int:
        self.calls.append((url, headers, body))
        if self.error:
            raise self.error
        return self.status


def test_waiting_reports_are_sent_once_and_deleted(waiting: Path) -> None:
    post = Posts()
    assert send_waiting(DSN, lambda: True, [NAME], post=post) == 1
    assert len(post.calls) == 1
    assert not list(waiting.glob("*.json"))


def test_with_reports_off_nothing_is_sent_and_waiting_ones_are_deleted(waiting: Path) -> None:
    post = Posts()
    assert send_waiting(DSN, lambda: False, [NAME], post=post) == 0
    assert not post.calls
    assert not list(waiting.glob("*.json"))


def test_offline_keeps_the_report_for_next_time(waiting: Path) -> None:
    post = Posts(error=httpx2.ConnectError("offline"))
    assert send_waiting(DSN, lambda: True, [], post=post) == 0
    assert len(list(waiting.glob("*.json"))) == 1


def test_a_report_changed_on_disk_is_checked_again_and_dropped(waiting: Path) -> None:
    path = next(waiting.glob("*.json"))
    data = json.loads(path.read_text("utf-8"))
    path.write_text(json.dumps({**data, "message": TRANSCRIPT}), "utf-8")
    post = Posts()
    send_waiting(DSN, lambda: True, [], post=post)
    assert not post.calls
    assert not path.exists()


def test_reports_waiting_are_capped(report: dict[str, Any]) -> None:
    for _ in range(crash_reports.MAX_WAITING + 3):
        crash_reports.keep(report)
    assert len(list(paths.crash_reports_dir().glob("*.json"))) == crash_reports.MAX_WAITING


def test_the_envelope_goes_to_the_project_with_no_ip_or_machine_name(
    report: dict[str, Any],
) -> None:
    url, headers, body = envelope(report, DSN)
    assert url == "https://o1.ingest.sentry.io/api/42/envelope/"
    assert "sentry_key=publickey" in headers["X-Sentry-Auth"]
    event = json.loads(body.decode().splitlines()[2])
    assert event["user"] == {"ip_address": None}
    assert "server_name" not in event
    assert "extra" not in event
    assert "breadcrumbs" not in event
    assert event["exception"]["values"][0]["type"] == "tycho.settings.SettingsError"


# --- Starting, and the crash hook ------------------------------------------------------------


@pytest.fixture
def hooks() -> Iterator[None]:
    import sys

    before = sys.excepthook, threading.excepthook
    yield
    sys.excepthook, threading.excepthook = before
    crash_reports._keep_crash = None  # pyright: ignore[reportPrivateUsage]


@pytest.mark.parametrize("setting", [None, False])
def test_not_asked_or_no_deletes_what_waits_and_keeps_nothing(
    waiting: Path, setting: bool | None, hooks: None
) -> None:
    import sys

    hook = sys.excepthook
    assert not crash_reports.start(setting, lambda: True, CONTEXT, dsn=DSN)
    assert not list(waiting.glob("*.json"))
    assert sys.excepthook is hook


def test_without_a_sentry_project_nothing_starts(hooks: None) -> None:
    assert not crash_reports.start(True, lambda: True, CONTEXT, dsn="")


def _crash_in_a_thread() -> None:
    def boom() -> None:
        settings.update({"voice_volume": TRANSCRIPT}, paths.settings_file())

    thread = threading.Thread(target=boom)
    thread.start()
    thread.join()


@pytest.mark.filterwarnings("ignore::pytest.PytestUnhandledThreadExceptionWarning")
def test_a_crash_is_kept_while_the_player_says_yes(hooks: None) -> None:
    allowed = [True]
    crash_reports.install(lambda: allowed[0], CONTEXT, [NAME])
    _crash_in_a_thread()
    kept = list(paths.crash_reports_dir().glob("*.json"))
    assert len(kept) == 1
    assert TRANSCRIPT not in kept[0].read_text("utf-8")
    allowed[0] = False
    _crash_in_a_thread()
    assert len(list(paths.crash_reports_dir().glob("*.json"))) == 1


# --- The setting: off until asked, and only ever off by voice ------------------------------


def test_the_setting_is_not_asked_until_the_player_answers() -> None:
    assert Settings().crash_reports is None
    assert settings.rule("crash_reports")["voice"] == "lower_only"


def test_by_voice_crash_reports_never_turn_on_even_before_being_asked() -> None:
    on = parse("crash_reports", "on", None).value
    assert on is True
    with pytest.raises(Refused):
        check_rule("crash_reports", None, on)  # never asked, so off: on needs the app
    check_rule("crash_reports", None, parse("crash_reports", "off", None).value)
    check_rule("crash_reports", True, False)


def test_saying_no_in_the_app_deletes_what_waits(waiting: Path, tmp_path: Path) -> None:
    path = tmp_path / "settings.toml"
    settings.save(Settings(crash_reports=True), path)
    bridge = Bridge(apply=lambda _n, _v: True, settings_path=path)
    assert bridge.set("crash_reports", False)["ok"]
    assert not list(waiting.glob("*.json"))


def test_the_app_shows_crash_reports_as_a_privacy_switch(tmp_path: Path) -> None:
    path = tmp_path / "settings.toml"
    settings.save(Settings(), path)
    groups = cast(list[dict[str, Any]], Bridge(settings_path=path).settings()["groups"])
    privacy = next(g for g in groups if g["name"] == "Privacy")
    rows = cast(list[dict[str, object]], privacy["rows"])
    row = next(r for r in rows if r["name"] == "crash_reports")
    assert row["kind"] == "switch"
    assert row["value"] is None
    assert row["voice"] == "lower_only"


# --- From #425's review (#429) ---------------------------------------------------------------


def test_a_crash_caught_on_its_way_out_is_kept_too(hooks: None) -> None:
    """The Start menu's Tycho catches every error to show a message box, so the hook
    never sees it: it hands the error over instead."""
    crash_reports.keep_crash(_crash())  # not installed: nothing
    assert not paths.crash_reports_dir().exists()
    crash_reports.install(lambda: True, CONTEXT, [NAME])
    crash_reports.keep_crash(_crash())
    assert len(list(paths.crash_reports_dir().glob("*.json"))) == 1


def test_tycho_frames_keep_their_line_when_the_code_s_file_name_is_relative() -> None:
    """PyInstaller makes co_filename relative (tycho\\runtime\\x.py): the module's own
    __file__ still says it's Tycho's."""
    code = compile("def boom():\n    raise ValueError('x')\n", r"tycho\fake.py", "exec")
    space: dict[str, Any] = {"__name__": "tycho.fake", "__file__": crash_reports.__file__}
    exec(code, space)  # noqa: S102
    with pytest.raises(ValueError, match="x") as raised:
        space["boom"]()
    frames = build(raised.value, CONTEXT)["frames"]
    assert {"module": "tycho.fake", "function": "boom", "line": 2} in frames


def test_a_short_user_name_inside_a_longer_word_keeps_the_report(report: dict[str, Any]) -> None:
    benched = {**report, "frames": [{"module": "tycho.bench", "function": "sample", "line": 1}]}
    assert scrub(benched, ["ben", "sam"]) is not None
    named = {**report, "frames": [{"module": "tycho.ben", "function": "f", "line": 1}]}
    assert scrub(named, ["ben"]) is None


def test_saying_no_by_voice_deletes_what_waits(waiting: Path, tmp_path: Path) -> None:
    import asyncio

    from tycho.spoken_settings import SettingsTool

    path = tmp_path / "settings.toml"
    settings.save(Settings(crash_reports=True), path)
    tool = SettingsTool(path)
    asyncio.run(tool.run({"action": "set", "changes": [{"name": "crash_reports", "value": "off"}]}))
    assert settings.load(path).crash_reports is False
    assert not list(waiting.glob("*.json"))


# --- From #431's review (#432) ---------------------------------------------------------------


@pytest.mark.parametrize("name", ["田中", "Влад", "Zoë"])
def test_names_outside_ascii_are_checked_too(report: dict[str, Any], name: str) -> None:
    named = {**report, "frames": [{"module": "tycho.x", "function": name, "line": 1}]}
    assert scrub(named, [name]) is None
    assert scrub(report, [name]) is not None


def test_the_start_menu_app_keeps_a_report_of_the_crash_it_shows(
    hooks: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    import ctypes
    import sys

    from tests.test_start_menu import _entry_point  # pyright: ignore[reportPrivateUsage]
    from tycho import cli
    from tycho.start import run as start_run

    def crashes(_argv: list[str]) -> int:
        raise _crash()

    class User32:
        @staticmethod
        def MessageBoxW(_owner: object, _text: str, _title: str, _flags: int) -> int:
            return 1

    import logging

    crash_reports.install(lambda: True, CONTEXT, [NAME])
    before = list(logging.getLogger("tycho").handlers)
    monkeypatch.setattr(logging.getLogger("tycho"), "handlers", list(before))  # restored (#434)
    monkeypatch.setattr(cli, "main", crashes)
    monkeypatch.setattr(start_run, "WINDOWED", False)
    monkeypatch.setattr(ctypes, "windll", type("Windll", (), {"user32": User32})(), raising=False)
    monkeypatch.setattr(sys, "stdout", sys.stdout)
    monkeypatch.setattr(sys, "stderr", sys.stderr)
    try:
        assert _entry_point().windowed([]) == 1
        kept = list(paths.crash_reports_dir().glob("*.json"))
        assert len(kept) == 1
        assert "tycho.settings.SettingsError" in kept[0].read_text("utf-8")
    finally:  # even when an assert fails (#445)
        for added in set(logging.getLogger("tycho").handlers) - set(before):
            added.close()  # logs.setup's file handler, in this test's folder


def test_a_name_inside_an_unspaced_word_is_found(report: dict[str, Any]) -> None:
    """#434: "田中" inside "田中さん" is still the name."""
    named = {**report, "frames": [{"module": "tycho.x", "function": "田中さん", "line": 1}]}
    assert scrub(named, ["田中"]) is None


# --- The real project (#450) -----------------------------------------------------------------


def test_tycho_s_project_is_a_sentry_dsn_with_no_secret_key() -> None:
    from tests.conftest import REAL_DSN

    parts = urlsplit(REAL_DSN)
    assert parts.scheme == "https"
    assert parts.hostname is not None
    assert parts.hostname.endswith(".ingest.us.sentry.io")
    assert parts.username  # the public key only
    assert parts.password is None
    assert parts.path.strip("/").isdigit()


def test_tests_never_send_to_tycho_s_project(hooks: None) -> None:
    """conftest blanks DSN, so a test that turns reports on can't reach Sentry."""
    assert not crash_reports.start(True, lambda: True, CONTEXT)
