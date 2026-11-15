"""Setup's page, driven for real (#364): the app's own page in a browser, calling the
real bridge over a stand-in PC, step by step, for a game Tycho presses keys in and for
one where it only advises.

The page reaches Python only through ``window.TYCHO_FAKE`` here, as it does through
pywebview in the app: each call it makes is a call on the ``Bridge``.
"""

import os
import re
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from playwright.sync_api import Browser, Error, Page, expect, sync_playwright

from tests.fakes.fake_key_check import FakeKeyCheck
from tests.fakes.fake_setup_pc import FakeSetupPC
from tycho import credentials, settings, updates
from tycho.desktop import window
from tycho.desktop.bridge import Bridge
from tycho.runtime.messages import Result
from tycho.runtime.recent import Recent
from tycho.settings import Settings

STEPS = ["Game", "Keys", "Talk and listen", "Privacy", "Try it"]
ANTHROPIC = "sk-ant-api03-" + "a" * 40 + "Wxyz"
ELEVENLABS = "sk_" + "b" * 40
# In a test the bridge is this proxy: every call goes to Python and comes back as JSON.
FAKE = """<script>window.TYCHO_FAKE = new Proxy({}, {
  get: (_, name) => (...args) => window.tychoCall(name, args) });</script>"""
CHROMIUM = Path("/opt/pw-browsers/chromium")
"""Where a cloud session keeps its browser; Windows (and CI) uses the PC's own Edge."""


@pytest.fixture(scope="module")
def browser() -> Iterator[Browser]:
    with sync_playwright() as playwright:
        try:
            if sys.platform == "win32":
                found = playwright.chromium.launch(channel="msedge")
            elif CHROMIUM.is_file():
                found = playwright.chromium.launch(executable_path=str(CHROMIUM))
            else:
                found = playwright.chromium.launch()
        except Error as e:
            if os.environ.get("CI"):
                raise  # CI must run these: a skip there would hide a broken page
            pytest.skip(f"no browser to drive the page with: {e}")
        yield found
        found.close()


@pytest.fixture
def vault(monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    stored: dict[str, str] = {}
    monkeypatch.setattr(credentials, "get", stored.get)
    monkeypatch.setattr(credentials, "set_secret", stored.__setitem__)

    def delete(name: str) -> bool:
        return stored.pop(name, None) is not None

    monkeypatch.setattr(credentials, "delete", delete)
    return stored


class Setup:
    """The page on setup, over a real bridge."""

    def __init__(
        self,
        browser: Browser,
        path: Path,
        *,
        first_run: bool = True,
        findings: list[dict[str, str]] | None = None,
        recent: Recent | None = None,
        listening: bool | None = None,
    ) -> None:
        self.path = path
        self.listening = listening
        self.pc = FakeSetupPC()
        self.finished: list[bool] = []
        self.restarts: list[bool] = []
        self.bridge = Bridge(
            settings_path=path,
            pc=self.pc,
            key_check=FakeKeyCheck(),
            choose_folder=lambda: r"C:\Games\StarCitizen",
            finish=lambda: self.finished.append(True),
            first_run=first_run,
            restart=lambda: bool(self.restarts.append(True)) or True,
            latest_release=lambda: None,
            findings=lambda: findings or [],
            recent=recent.exchanges if recent is not None else list,
            listening=lambda: self.listening,
        )
        self.page: Page = browser.new_page()
        self.errors: list[str] = []
        self.page.on("pageerror", lambda e: self.errors.append(str(e)))
        self.page.expose_function("tychoCall", self._call)  # pyright: ignore[reportUnknownMemberType]
        self.page.set_content(window.page().replace("</head>", FAKE + "</head>"))

    def _call(self, name: str, args: list[Any]) -> Any:
        assert not name.startswith("_")
        return getattr(self.bridge, name)(*args)

    def at(self, step: str) -> None:
        """Wait until setup is on ``step``: the page draws after each bridge call."""
        expect(self.page.locator(".steps li.now")).to_contain_text(step)

    def next(self) -> None:
        self.page.get_by_role("button", name="Next", exact=True).click()

    def saved(self) -> Settings:
        return settings.load(self.path)

    def close(self) -> None:
        self.page.close()
        assert self.errors == []


@pytest.fixture
def path(tmp_path: Path) -> Path:
    p = tmp_path / "settings.toml"
    settings.save(Settings(), p)
    return p


@pytest.fixture
def setup(browser: Browser, path: Path, vault: dict[str, str]) -> Iterator[Setup]:
    opened = Setup(browser, path)
    yield opened
    opened.close()


def test_a_first_run_for_a_game_tycho_presses_keys_in(setup: Setup, vault: dict[str, str]) -> None:
    page = setup.page
    expect(page.get_by_role("heading", name="Welcome to Tycho")).to_be_visible()
    assert [li.split("\n")[-1] for li in page.locator(".steps li").all_inner_texts()] == STEPS

    # 1. The game first: both games offered, and the chosen one in its pack's own words.
    setup.at("Game")
    expect(page.locator("[data-game]")).to_have_count(2)
    expect(page.locator("[data-game='star-citizen']")).to_have_attribute("aria-pressed", "true")
    card = page.locator("#packCard")
    expect(card).to_contain_text("Tycho in Star Citizen")
    expect(card).to_contain_text("presses your own Star Citizen keybinds")
    expect(card).to_contain_text("Trade prices and routes")
    # Key actions are on, with why they're safe, and can be turned off here.
    switch = card.get_by_role("switch", name="Let Tycho press keys")
    expect(switch).to_have_attribute("aria-checked", "true")
    switch.click()
    expect(card.get_by_role("switch")).to_have_attribute("aria-checked", "false")
    assert setup.saved().key_actions is False
    card.get_by_role("switch").click()
    expect(card.get_by_role("switch")).to_have_attribute("aria-checked", "true")
    # Its folder isn't found: the pack says where to look, and the folder can be chosen.
    expect(card).to_contain_text("Star Citizen LIVE wasn't found")
    expect(card).to_contain_text("The RSI Launcher shows it under Settings, Games")
    card.get_by_role("button", name="Choose the folder…").click()
    expect(card).to_contain_text("Found Star Citizen LIVE in")
    assert str(setup.saved().game_root) == r"C:\Games\StarCitizen"

    # 2. Keys, with what each costs. One is checked and stored; none is ever shown again.
    setup.next()
    setup.at("Keys")
    expect(page.locator("ul.costs")).to_contain_text("About $0.0002 a request")
    expect(page.locator("ul.costs")).to_contain_text("0.5 credits a character")
    page.locator("#k-anthropic").fill(ANTHROPIC)
    page.locator("[data-store='anthropic']").click()
    expect(page.locator("#keysForm")).to_contain_text("Wxyz")
    assert vault["anthropic"] == ANTHROPIC
    assert ANTHROPIC not in page.content()

    # 3. Talk and listen: push-to-talk, the microphone and the voice on one page.
    setup.next()
    setup.at("Talk and listen")
    expect(page.locator("#pttName")).to_have_text("None yet")
    page.get_by_role("button", name="Choose a button").click()
    expect(page.locator("#pttName")).to_have_text("Mouse side (forward)")
    assert setup.saved().push_to_talk == "mouse:0x06"

    # The microphone, and how it sounds.
    page.locator("#mic").select_option("Desk mic, MME")
    page.get_by_role("button", name="Test: say something").click()
    expect(page.locator("#micMsg")).to_have_text("Good level: Tycho will hear you clearly.")
    assert setup.saved().microphone == "Desk mic, MME"

    # The voice: without an ElevenLabs key it says so, and the choice is still kept.
    expect(page.locator("#noVoice")).to_contain_text("No ElevenLabs key yet")
    expect(page.locator("[data-voice='George']")).to_have_attribute("aria-pressed", "true")
    page.locator("[data-voice='Sarah']").click()
    expect(page.locator("[data-voice='Sarah']")).to_have_attribute("aria-pressed", "true")
    assert setup.saved().voice == "Sarah"

    # 4. What leaves the PC: the brain, the voice, web search, and the pack's own look-ups.
    setup.next()
    setup.at("Privacy")
    leaves = page.locator("table.leaves")
    expect(page.locator(".card").first).to_contain_text("Your voice never does")
    for who in ("Anthropic (Claude)", "ElevenLabs", "Anthropic (web search)", "GitHub"):
        expect(leaves).to_contain_text(who)
    expect(leaves).to_contain_text("your bound actions with their keys")
    expect(leaves).to_contain_text("to UEX Corp")
    # Crash reports (ADR 4): asked here, with what's sent and what never is beside it.
    # No and Yes are equal, and neither is picked until the player picks.
    crash = page.locator("#crashCard")
    expect(crash).to_contain_text("Send crash reports to help fix Tycho?")
    expect(crash).to_contain_text("where in Tycho's own code it happened")
    expect(crash).to_contain_text("Your keys")
    expect(crash.locator("[data-crash][aria-pressed='true']")).to_have_count(0)
    assert setup.saved().crash_reports is None
    crash.locator("[data-crash='yes']").click()
    expect(crash.locator("[data-crash='yes']")).to_have_attribute("aria-pressed", "true")
    assert setup.saved().crash_reports is True
    crash.locator("[data-crash='no']").click()
    expect(crash.locator("[data-crash='no']")).to_have_attribute("aria-pressed", "true")
    expect(crash.locator("[data-crash='yes']")).to_have_attribute("aria-pressed", "false")
    assert setup.saved().crash_reports is False

    # 5. What to say first, from the pack, and Start.
    setup.next()
    setup.at("Try it")
    expect(page.locator("ul.say li")).to_have_text(
        ["“gear down”", "“lights on”", "“how much cargo does the Caterpillar hold?”"]
    )
    expect(page.locator("ul.tips")).to_contain_text("Borderless Window")
    expect(page.locator("#todo")).to_have_count(0)
    expect(page.locator("#noBrain")).to_have_count(0)
    page.get_by_role("button", name="Start Tycho").click()
    expect(page.get_by_role("heading", name="Starting Tycho…")).to_be_visible()
    assert setup.finished == [True]


def test_a_first_run_for_a_game_tycho_only_advises_in(setup: Setup, vault: dict[str, str]) -> None:
    page = setup.page
    # 1. Pick the other game. Nothing about the first game is left on the page.
    page.locator("[data-game='wow-forever']").click()
    expect(page.locator("[data-game='wow-forever']")).to_have_attribute("aria-pressed", "true")
    assert setup.saved().game == "wow-forever"
    card = page.locator("#packCard")
    expect(card).to_contain_text("Tycho in World of Warcraft: Forever")
    # Advisor only, with the pack's why; no switch can turn key presses on.
    expect(page.locator("#packMode")).to_contain_text("Advisor only: Tycho presses no keys")
    expect(page.locator("#packMode")).to_contain_text("Blizzard's rules")
    expect(card.get_by_role("switch")).to_have_count(0)
    # It needs no folder (#341 item 4): nothing to choose, and Next works.
    expect(page.locator("#packFolder")).to_have_text(
        "✓ Tycho needs nothing from the game's folder."
    )
    expect(page.locator("#folder")).to_have_count(0)
    assert "Star Citizen" not in card.inner_text()
    assert "RSI" not in page.locator("main").inner_text()

    # 2. Keys: an advisor answers nothing without a key for the brain, and says so.
    setup.next()
    setup.at("Keys")
    expect(page.locator(".card .warn-line")).to_contain_text(
        "needs an Anthropic or OpenRouter key to answer in World of Warcraft: Forever"
    )
    page.locator("#k-elevenlabs").fill(ELEVENLABS)
    page.locator("[data-store='elevenlabs']").click()
    expect(page.locator("#keysForm")).to_contain_text(ELEVENLABS[-4:])

    # 3. Push-to-talk can be skipped; the voice is offered plainly, as the key is stored.
    setup.next()
    setup.at("Talk and listen")
    expect(page.locator(".card").first).to_contain_text("Tycho can't start without one")
    expect(page.locator("#noVoice")).to_have_count(0)
    page.locator("[data-voice='Lily']").click()
    expect(page.locator("[data-voice='Lily']")).to_have_attribute("aria-pressed", "true")

    # 4. What leaves the PC: no keybinds, no web search and no look-ups for this game.
    setup.next()
    setup.at("Privacy")
    leaves = page.locator("table.leaves")
    expect(leaves).to_contain_text("What you said, as text")
    assert "bound actions" not in leaves.inner_text()
    assert "web search" not in leaves.inner_text()
    assert "UEX" not in leaves.inner_text()
    # A player who never answers the crash question sends nothing: it stays unanswered.
    expect(page.locator("#crashCard [data-crash][aria-pressed='true']")).to_have_count(0)

    # 5. The skipped button is what's missing: Start waits, and a button goes back for it.
    setup.next()
    setup.at("Try it")
    expect(page.locator("ul.say li")).to_have_count(3)
    expect(page.locator("ul.say")).to_contain_text("Skyborne")
    expect(page.locator("#noBrain")).to_contain_text("Tycho can't answer in World of Warcraft")
    expect(page.locator("#todo")).to_contain_text("a push-to-talk button")
    expect(page.get_by_role("button", name="Start Tycho")).to_be_disabled()
    page.get_by_role("button", name="Go to Talk and listen").click()
    setup.at("Talk and listen")
    page.get_by_role("button", name="Choose a button").click()
    expect(page.locator("#pttName")).to_have_text("Mouse side (forward)")
    for _ in range(2):
        setup.next()
    setup.at("Try it")
    expect(page.locator("#todo")).to_have_count(0)
    page.get_by_role("button", name="Start Tycho").click()
    expect(page.get_by_role("heading", name="Starting Tycho…")).to_be_visible()
    assert setup.finished == [True]
    assert setup.saved().game == "wow-forever"
    assert setup.saved().voice == "Lily"
    assert setup.saved().crash_reports is None


def test_picking_another_game_in_settings_shows_its_own_setup_first(
    browser: Browser, path: Path, vault: dict[str, str]
) -> None:
    settings.save(
        Settings(push_to_talk="mouse:0x06", game_channel="PTU", crash_reports=False), path
    )
    setup = Setup(browser, path, first_run=False)
    page = setup.page
    page.get_by_role("button", name="Settings", exact=True).click()
    page.get_by_role("tab", name="Game and controls").click()
    page.locator("[data-pick='game'][data-v='wow-forever']").click()
    # Not switched yet: the new game's pack says what Tycho does there.
    expect(page.get_by_role("heading", name="Set up World of Warcraft: Forever")).to_be_visible()
    assert setup.saved().game == "star-citizen"
    assert setup.restarts == []
    expect(page.locator("#packMode")).to_contain_text("Advisor only: Tycho presses no keys")
    expect(page.locator("#packFolder")).to_contain_text("needs nothing from the game's folder")
    expect(page.locator("ul.say li")).to_have_count(3)
    # Back changes nothing.
    page.get_by_role("button", name="Back").click()
    expect(page.get_by_role("heading", name="Settings")).to_be_visible()
    assert setup.saved().game == "star-citizen"
    # Use it: saved, and Tycho restarts with it (#295).
    page.locator("[data-pick='game'][data-v='wow-forever']").click()
    page.get_by_role("button", name="Use World of Warcraft: Forever").click()
    expect(page.locator("#toastText")).to_contain_text("Tycho restarts with it now")
    assert setup.saved().game == "wow-forever"
    assert setup.restarts == [True]
    # And back to a game with key actions: its folder and its key presses are shown first.
    page.locator("[data-pick='game'][data-v='star-citizen']").click()
    expect(page.get_by_role("heading", name="Set up Star Citizen")).to_be_visible()
    expect(page.locator("#packMode")).to_contain_text("presses your own keybinds there")
    expect(page.locator("#packFolder")).to_contain_text("Found in")
    setup.close()


def test_a_player_set_up_before_crash_reports_is_asked_once(
    browser: Browser, path: Path, vault: dict[str, str]
) -> None:
    """ADR 4: players who set up before crash reports existed are asked on their next
    start, on setup's Privacy step, and once they answer the window goes on as usual."""
    settings.save(Settings(push_to_talk="mouse:0x06"), path)
    setup = Setup(browser, path, first_run=False)
    page = setup.page
    setup.at("Privacy")
    expect(page.locator(".lede")).to_contain_text("One question left: crash reports")
    crash = page.locator("#crashCard")
    expect(crash.locator("[data-crash][aria-pressed='true']")).to_have_count(0)
    crash.locator("[data-crash='no']").click()
    expect(page.locator("#homeState")).to_be_visible()
    assert setup.saved().crash_reports is False
    setup.close()
    again = Setup(browser, path, first_run=False)  # the next start: answered, not asked
    expect(again.page.locator("#homeState")).to_be_visible()
    again.close()


def test_the_first_run_asks_crash_reports_in_its_own_place(
    browser: Browser, path: Path, vault: dict[str, str]
) -> None:
    setup = Setup(browser, path, first_run=True)
    assert setup.bridge.about()["ask_crash_reports"] is False
    expect(setup.page.get_by_role("heading", name="Welcome to Tycho")).to_be_visible()
    setup.at("Game")
    setup.close()


def test_a_skipped_step_is_not_ticked_and_next_stays_in_view(setup: Setup) -> None:
    """The design review: setup ticked every step walked past, done or not, and Next sat
    below the window's edge on its first screen."""
    page = setup.page
    page.set_viewport_size({"width": 1120, "height": 800})  # the window's own size
    setup.at("Game")
    expect(page.get_by_role("button", name="Next", exact=True)).to_be_in_viewport()
    for _ in range(3):  # past the game (not found), the keys (none) and push-to-talk (none)
        setup.next()
    setup.at("Privacy")
    steps = page.locator(".steps li")
    for i in range(3):
        expect(steps.nth(i)).to_have_class("todo")
        expect(steps.nth(i)).not_to_contain_text("✓")
    # Done properly, a step is ticked.
    page.get_by_role("button", name="Back").click()
    page.locator("#capture").click()
    expect(page.locator("#pttName")).to_have_text("Mouse side (forward)")
    setup.next()
    setup.at("Privacy")  # the longest step: Next is still there to press
    expect(steps.nth(2)).to_have_class("done")
    expect(steps.nth(2)).to_contain_text("✓")
    expect(page.get_by_role("button", name="Next", exact=True)).to_be_in_viewport()


def test_the_one_crash_question_comes_before_the_table(
    browser: Browser, path: Path, vault: dict[str, str]
) -> None:
    settings.save(Settings(push_to_talk="mouse:0x06"), path)
    setup = Setup(browser, path, first_run=False)
    setup.at("Privacy")
    cards = setup.page.locator("main > .card")
    expect(cards.first).to_have_id("crashCard")
    setup.close()
    first = Setup(browser, path, first_run=True)  # the first run reads the table first
    for _ in range(3):
        first.next()
    first.at("Privacy")
    expect(first.page.locator("main > .card").last).to_have_id("crashCard")
    first.close()


def test_a_game_tycho_only_advises_in_has_no_empty_pages(
    browser: Browser, path: Path, vault: dict[str, str]
) -> None:
    """The design review: an advisor game showed an empty commands table with a column
    for keys, and another game's folder settings."""
    settings.save(
        Settings(game="wow-forever", push_to_talk="mouse:0x06", crash_reports=False), path
    )
    setup = Setup(browser, path, first_run=False)
    page = setup.page
    page.get_by_role("button", name="What to say").click()
    expect(page.locator("#noCommands")).to_contain_text("ask in your own words")
    expect(page.locator("table")).to_have_count(0)
    page.get_by_role("button", name="Settings", exact=True).click()
    page.get_by_role("tab", name="Game and controls").click()
    expect(page.locator(".set", has_text="Push-to-talk")).to_have_count(1)
    page.get_by_role("tab", name="Advanced").click()
    expect(page.locator(".set", has_text="Brain model")).to_have_count(1)
    expect(page.locator(".set", has_text="Game folder")).to_have_count(0)
    expect(page.locator(".set", has_text="Game channel")).to_have_count(0)
    names = [r["name"] for g in setup.bridge.settings()["groups"] for r in g["rows"]]  # type: ignore[index,union-attr]
    assert "game_root" not in names
    assert "game" in names
    setup.close()
    # A game with a folder keeps them.
    settings.save(Settings(push_to_talk="mouse:0x06", crash_reports=False), path)
    there = Setup(browser, path, first_run=False)
    there.page.get_by_role("button", name="Settings", exact=True).click()
    there.page.get_by_role("tab", name="Advanced").click()
    expect(there.page.locator(".set", has_text="Game folder")).to_have_count(1)
    there.close()


def test_a_newer_version_makes_get_the_button_to_press(
    browser: Browser, path: Path, vault: dict[str, str]
) -> None:
    settings.save(Settings(push_to_talk="mouse:0x06", crash_reports=False), path)
    setup = Setup(browser, path, first_run=False)
    page = setup.page
    page.get_by_role("button", name="Settings", exact=True).click()
    page.get_by_role("tab", name="Updates").click()
    expect(page.locator("#getRow")).to_be_hidden()  # nothing newer: Check is the one
    expect(page.locator("#checkUpdates")).to_have_class(re.compile(r"\bprimary\b"))
    setup.bridge._latest_release = lambda: updates.Release("99.0.0", "https://example.invalid")  # pyright: ignore[reportPrivateUsage]
    page.locator("#checkUpdates").click()
    expect(page.locator("#getUpdate")).to_have_class(re.compile(r"\bprimary\b"))
    expect(page.locator("#checkUpdates")).not_to_have_class(re.compile(r"\bprimary\b"))
    # And when the page is drawn again, knowing it.
    page.get_by_role("tab", name="Keys").click()
    page.get_by_role("tab", name="Updates").click()
    expect(page.locator("#getUpdate")).to_have_class(re.compile(r"\bprimary\b"))
    expect(page.locator("#checkUpdates")).not_to_have_class(re.compile(r"\bprimary\b"))
    setup.close()


def test_the_places_are_down_the_side_and_home_shows_one_thing_to_fix(
    browser: Browser, path: Path, vault: dict[str, str]
) -> None:
    """ADR 17 as amended (direction B): Home, What to say, The Tile and Settings down the
    left, the game under them, at most one thing to fix on Home, and the Tile on its own."""
    settings.save(
        Settings(game="wow-forever", push_to_talk="mouse:0x06", crash_reports=False), path
    )
    checks = [
        {"level": "ok", "what": "Microphone", "detail": "found", "fix": ""},
        {"level": "warn", "what": "Voice", "detail": "the Windows voice", "fix": "add a key"},
        {"level": "fail", "what": "Brain", "detail": "no key", "fix": "tycho key set"},
    ]
    setup = Setup(browser, path, first_run=False, findings=checks)
    page = setup.page
    nav = page.locator("nav[aria-label='Places'] button")
    # Each place by its name; Home and What to say may carry a count once checks come in.
    places = ["Home", "What to say", "The Tile", "Settings"]
    expect(nav).to_have_text([re.compile(rf"^{p}\b") for p in places])
    expect(page.locator("#gameBox")).to_contain_text("World of Warcraft: Forever")
    expect(page.locator("#gameBox")).to_contain_text("Tycho only talks")
    # Home: no brain key here, so there's something to fix, and only one shows.
    expect(page.locator("#homeState")).to_be_visible()
    expect(page.locator("#oneFix")).to_have_count(1)
    expect(page.locator("#oneFix")).to_contain_text("Brain")  # the failure before the warning
    expect(page.locator("#statusBadge")).to_have_text("2 !")
    expect(page.locator("#checks")).to_be_hidden()
    page.locator("#showChecks").click()
    expect(page.locator("#checks .check").first).to_be_visible()
    # The Tile has its own place, and Settings no longer has a Tile tab.
    page.get_by_role("button", name="The Tile").click()
    expect(page.get_by_role("heading", name="The Tile")).to_be_visible()
    expect(page.locator("#tilePreview")).to_have_attribute("src", re.compile("^data:image"))
    page.get_by_role("button", name="Settings", exact=True).click()
    expect(page.get_by_role("tab", name="The Tile")).to_have_count(0)
    expect(page.get_by_role("tab")).to_have_text(
        ["Voice", "Game and controls", "Safety", "Privacy", "Keys", "Updates", "Advanced"]
    )
    # Switch game opens the game choice in Settings.
    page.locator("#switchGame").click()
    expect(page.get_by_role("tab", name="Game and controls")).to_have_attribute(
        "aria-selected", "true"
    )
    # Run again with something missing, the places stay: Home is a click away.
    page.get_by_role("button", name="Home").click()
    page.locator("#redo").click()
    setup.at("Game")
    expect(nav.first).to_be_visible()
    nav.first.click()
    expect(page.locator("#homeState")).to_be_visible()
    setup.close()


def test_the_game_box_says_tycho_only_talks_with_key_actions_off(
    browser: Browser, path: Path, vault: dict[str, str]
) -> None:
    settings.save(Settings(push_to_talk="mouse:0x06", crash_reports=False, key_actions=False), path)
    setup = Setup(browser, path, first_run=False)
    expect(setup.page.locator("#gameBox")).to_contain_text("Tycho only talks")
    setup.close()


def test_the_window_takes_the_games_own_colour(
    browser: Browser, path: Path, vault: dict[str, str]
) -> None:
    """ADR 17 (amended): one accent per game, from the game's pack."""
    settings.save(
        Settings(push_to_talk="mouse:0x06", crash_reports=False, game="wow-forever"), path
    )
    setup = Setup(browser, path, first_run=False)
    expect(setup.page.locator("#gameBox")).to_contain_text("World of Warcraft")
    accent = "getComputedStyle(document.documentElement).getPropertyValue('--acc').trim()"
    assert setup.page.evaluate(accent) == "#f0c674"
    setup.close()


def test_home_says_its_listening_and_shows_the_last_exchanges_from_memory(
    browser: Browser, path: Path, vault: dict[str, str]
) -> None:
    """ADR 17 (amended): Home says in one line whether Tycho is listening and which
    button to hold, and lists the last exchanges, newest first, as they come in."""
    settings.save(Settings(push_to_talk="mouse:0x06", crash_reports=False), path)
    recent = Recent()
    recent.add(Result(1, "done", text="gear down", say="Landing gear down."))
    setup = Setup(browser, path, first_run=False, recent=recent, listening=True)
    page = setup.page
    expect(page.locator("#homeState h1")).to_have_text("Listening")
    expect(page.locator("#homeState")).to_contain_text("Hold Mouse side (forward) and talk.")
    expect(page.locator("#recent li")).to_have_count(1)
    expect(page.locator("#recent li").first).to_contain_text("“gear down”")
    expect(page.locator("#recent li").first).to_contain_text("Landing gear down.")
    # A new one shows without a click, on top.
    recent.add(Result(2, "no_focus", text="lights on"))
    expect(page.locator("#recent li")).to_have_count(2, timeout=6000)
    expect(page.locator("#recent li").first).to_contain_text("The game wasn't in front")
    expect(page.locator("#recent li.no")).to_have_count(1)
    # Paused from the tray while Home is open: Home says so without a click.
    setup.listening = False
    expect(page.locator("#homeState h1")).to_have_text("Paused", timeout=6000)
    setup.close()


def test_home_has_no_exchanges_list_while_tycho_isnt_running(
    browser: Browser, path: Path, vault: dict[str, str]
) -> None:
    settings.save(Settings(push_to_talk="mouse:0x06", crash_reports=False), path)
    setup = Setup(browser, path, first_run=False)
    expect(setup.page.locator("#homeState h1")).to_have_text("Ready")
    expect(setup.page.locator("#recent")).to_have_count(0)
    setup.close()


def test_setup_run_again_shows_another_game_before_switching(
    browser: Browser, path: Path, vault: dict[str, str]
) -> None:
    """#435: run again from Home, picking another game in setup restarted Tycho at once
    and closed the window mid-setup, with no word on what Tycho does there."""
    settings.save(
        Settings(push_to_talk="mouse:0x06", crash_reports=False, voice="The Ship's Mind"), path
    )
    setup = Setup(browser, path, first_run=False)
    page = setup.page
    page.locator("#redo").click()
    setup.at("Game")
    page.locator("[data-game='wow-forever']").click()
    expect(page.get_by_role("heading", name="Set up World of Warcraft: Forever")).to_be_visible()
    assert setup.saved().game == "star-citizen"
    assert setup.restarts == []
    page.get_by_role("button", name="Back").click()
    setup.at("Game")  # back in setup, where it was
    # #435: a voice of the player's own account isn't "a voice of your own" twice over.
    setup.next()
    setup.next()
    setup.at("Talk and listen")
    expect(page.locator("#voiceNow")).to_have_text(
        "Now: The Ship's Mind. Pick one of these to change it."
    )
    setup.close()
