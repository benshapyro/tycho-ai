"""The update check (#9): says when a newer Tycho is out, and never gets in the way."""

from typing import Any

import httpx2
import pytest

from tycho import app, updates
from tycho.updates import Release, UpdateError

URL = "https://github.com/benshapyro/tycho-ai/releases/tag/v1.2.0"
RELEASE: dict[str, Any] = {
    "tag_name": "v1.2.0",
    "html_url": URL,
    "draft": False,
    "prerelease": False,
}


def answer(data: Any) -> Any:
    def get(url: str) -> Any:
        assert url == updates.LATEST_URL
        return data

    return get


def fail(url: str) -> Any:
    raise UpdateError("offline")


@pytest.mark.parametrize(
    ("text", "key"),
    [
        ("1.2.3", (1, 2, 3)),
        ("v1.2.3", (1, 2, 3)),
        ("1.2", (1, 2)),
        ("1.2.0", (1, 2)),
        ("0.0.0+unknown", (0,)),
        ("1.2.0rc1", None),
        ("1.2.0-beta", None),
        ("nightly", None),
    ],
)
def test_version_key(text: str, key: tuple[int, ...] | None) -> None:
    assert updates.version_key(text) == key


@pytest.mark.parametrize(
    ("latest", "current", "newer"),
    [
        ("1.2.0", "1.1.9", True),
        ("1.10.0", "1.9.0", True),  # numbers, not text
        ("1.2", "1.2.0", False),
        ("1.2.0", "1.2.0", False),
        ("1.1.0", "1.2.0", False),
        ("2.0.0rc1", "1.0.0", False),  # pre-releases aren't offered
        ("1.0.0", "garbage", False),
    ],
)
def test_is_newer(latest: str, current: str, newer: bool) -> None:
    assert updates.is_newer(latest, current) is newer


def test_a_newer_release_is_found() -> None:
    release = updates.newer_release("1.1.0", answer(RELEASE))
    assert release == Release("1.2.0", URL)


def test_the_same_version_or_no_release_is_nothing() -> None:
    assert updates.newer_release("1.2.0", answer(RELEASE)) is None
    assert updates.newer_release("1.0.0", answer(None)) is None  # none published yet


@pytest.mark.parametrize("flag", ["draft", "prerelease"])
def test_drafts_and_prereleases_are_skipped(flag: str) -> None:
    assert updates.newer_release("1.0.0", answer({**RELEASE, flag: True})) is None


def test_an_odd_answer_is_an_update_error() -> None:
    with pytest.raises(UpdateError):
        updates.newer_release("1.0.0", answer({"message": "rate limited"}))
    with pytest.raises(UpdateError):
        updates.newer_release("1.0.0", answer(["not", "a", "dict"]))


def test_announcing_runs_in_the_background_and_says_so() -> None:
    said: list[str] = []
    updates.announce_in_background("1.0.0", answer(RELEASE), said.append).join(5)
    assert said == [f"Tycho 1.2.0 is out (you have 1.0.0): {URL}"]


def test_announcing_is_quiet_when_offline_or_current() -> None:
    said: list[str] = []
    updates.announce_in_background("1.0.0", fail, said.append).join(5)
    updates.announce_in_background("1.2.0", answer(RELEASE), said.append).join(5)
    assert said == []


REAL_CLIENT = httpx2.Client


def _client_answering(response: httpx2.Response) -> Any:
    def make(**kwargs: Any) -> httpx2.Client:
        return REAL_CLIENT(transport=httpx2.MockTransport(lambda _: response), **kwargs)

    return make


def test_latest_json_over_http(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        updates.httpx2, "Client", _client_answering(httpx2.Response(200, json=RELEASE))
    )
    assert updates.latest_json(updates.LATEST_URL) == RELEASE

    monkeypatch.setattr(updates.httpx2, "Client", _client_answering(httpx2.Response(404)))
    assert updates.latest_json(updates.LATEST_URL) is None

    monkeypatch.setattr(updates.httpx2, "Client", _client_answering(httpx2.Response(500)))
    with pytest.raises(UpdateError):
        updates.latest_json(updates.LATEST_URL)


def test_check_reports_a_newer_version(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(app, "__version__", "1.0.0")
    finding = app.update_finding(answer(RELEASE))
    assert finding.level == "warn"
    assert "1.2.0" in finding.detail
    assert app.update_finding(answer(RELEASE | {"tag_name": "v1.0.0"})).detail == (
        "1.0.0 is the latest"
    )
    none_yet = app.update_finding(answer(None))
    assert none_yet.level == "ok"
    assert "no release published yet" in none_yet.detail
    offline = app.update_finding(fail)
    assert offline.level == "ok"
    assert "couldn't check" in offline.detail
