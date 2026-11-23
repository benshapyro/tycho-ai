"""Setup's page, driven for real (#364): the app's own page in a browser, calling the
real bridge over a stand-in PC, step by step, for a game Tycho presses keys in and for
one where it only advises.

The page reaches Python only through ``window.TYCHO_FAKE`` here, as it does through
pywebview in the app: each call it makes is a call on the ``Bridge``.
"""

import os
import re
import sys
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from playwright.sync_api import Browser, Error, Page, expect, sync_playwright

from tests.fakes.fake_key_check import FakeKeyCheck
from tests.fakes.fake_setup_pc import FakeSetupPC
from tycho import credentials, settings, updates
from tycho.desktop import window
from tycho.desktop.bridge import Bridge
from tycho.packs import GamePack
from tycho.packs.parts import HangarImport
from tycho.runtime.messages import Result
from tycho.runtime.recent import Recent
from tycho.settings import Game, Settings

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
        opening: str = "",
        can_quit: bool = False,
        hangar: Callable[[GamePack, Path], HangarImport] | None = None,
    ) -> None:
        self.path = path
        self.quits: list[bool] = []
        """Quit asked of the running Tycho, as the tray's Quit does."""
        self.listening = listening
        self.trying: list[bool] = []
        """Try it turned on and off in the running Tycho, in order (ADR 17)."""
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
            trying=lambda on: self.listening is not None and not self.trying.append(on),
            opening=opening,
            quit_tycho=(lambda: self.quits.append(True)) if can_quit else None,
            choose_file=lambda: r"C:\Users\me\Downloads\ships.csv",
            import_hangar=hangar or _no_hangar,
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


def _no_hangar(_pack: GamePack, _file: Path) -> HangarImport:
    raise AssertionError("no hangar import in this test")


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
    # Looking at the game window (ADR 14, #31): asked beside it, in a game that can look,
    # with No and Yes equal and neither picked. The table says where a picture would go.
    look = page.locator("#lookCard")
    expect(look).to_contain_text("Let Tycho look at your game window when you ask?")
    expect(look).to_contain_text("never saved")
    expect(look).to_contain_text("A camera sound plays")
    expect(leaves).to_contain_text("A picture of the game window, only when you ask")
    expect(look.locator("[data-look][aria-pressed='true']")).to_have_count(0)
    assert setup.saved().look_at_screen is None
    look.locator("[data-look='yes']").click()
    expect(look.locator("[data-look='yes']")).to_have_attribute("aria-pressed", "true")
    assert setup.saved().look_at_screen is True
    look.locator("[data-look='no']").click()
    expect(look.locator("[data-look='no']")).to_have_attribute("aria-pressed", "true")
    assert setup.saved().look_at_screen is False

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
    # Tycho can't look at a game with no window, so it isn't asked, or listed (#31).
    expect(page.locator("#lookCard")).to_have_count(0)
    assert "picture" not in leaves.inner_text()

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
        Settings(
            push_to_talk="mouse:0x06",
            game_channel="PTU",
            crash_reports=False,
            look_at_screen=False,
            keep_history=False,
        ),
        path,
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
    settings.save(
        Settings(push_to_talk="mouse:0x06", look_at_screen=False, keep_history=False), path
    )
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
    assert setup.bridge.about()["ask_privacy"] == []
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
    # #448: here a brain key is optional (the built-in phrases work without one), so
    # skipping it is calm, not amber "still to do" every time setup opens.
    for i, cls in enumerate(["todo", "skipped", "todo"]):
        expect(steps.nth(i)).to_have_class(cls)
        expect(steps.nth(i)).not_to_contain_text("✓")
    # How a skipped step looks, not only its class (#506).
    expect(steps.nth(1).locator("b")).to_have_text("\u2013")  # an en dash
    expect(steps.nth(1)).to_have_attribute("title", "Skipped. You can add it later.")
    expect(steps.nth(0)).to_have_attribute("title", "Still to do")
    # Done properly, a step is ticked.
    page.get_by_role("button", name="Back").click()
    page.locator("#capture").click()
    expect(page.locator("#pttName")).to_have_text("Mouse side (forward)")
    setup.next()
    setup.at("Privacy")  # the longest step: Next is still there to press
    expect(steps.nth(2)).to_have_class("done")
    expect(steps.nth(2)).to_contain_text("✓")
    expect(page.get_by_role("button", name="Next", exact=True)).to_be_in_viewport()


def test_a_second_push_to_talk_button_is_added_and_removed_in_setup(
    browser: Browser, path: Path, vault: dict[str, str]
) -> None:
    """#621: a stick button as well as the mouse one, offered once there's a first."""
    setup = Setup(browser, path)
    page = setup.page
    setup.at("Game")
    setup.next()
    setup.next()
    setup.at("Talk and listen")
    expect(page.locator("#capture2")).to_have_count(0)  # a second needs a first
    page.locator("#capture").click()
    expect(page.get_by_role("button", name="Add a second button")).to_be_visible()
    setup.pc.button = "joystick:vkb-l:3"
    page.get_by_role("button", name="Add a second button").click()
    expect(page.locator("#remove2")).to_be_visible()
    assert settings.load(path).push_to_talk_2 == "joystick:vkb-l:3"
    page.locator("#remove2").click()
    expect(page.get_by_role("button", name="Add a second button")).to_be_visible()
    assert settings.load(path).push_to_talk_2 is None
    setup.close()


def test_a_skipped_key_is_still_to_do_where_the_brain_is_all_there_is(
    browser: Browser, path: Path, vault: dict[str, str]
) -> None:
    """#448: only a game with built-in phrases can do without a brain key."""
    settings.save(Settings(game="wow-forever"), path)
    setup = Setup(browser, path)
    page = setup.page
    setup.at("Game")
    setup.next()
    setup.at("Keys")
    setup.next()
    expect(page.locator(".steps li").nth(1)).to_have_class("todo")
    setup.close()


def test_a_skipped_key_is_still_to_do_with_key_actions_off(
    browser: Browser, path: Path, vault: dict[str, str]
) -> None:
    """#506: with key actions off the built-in phrases press nothing, so in a game where
    Tycho can press keys the brain is still all there is, and setup says why."""
    settings.save(Settings(key_actions=False), path)
    setup = Setup(browser, path)
    page = setup.page
    setup.at("Game")
    setup.next()
    setup.at("Keys")
    expect(page.locator("#keysNote")).to_have_class("warn-line")
    expect(page.locator("#keysNote")).to_contain_text("Key actions are off")
    setup.next()
    expect(page.locator(".steps li").nth(1)).to_have_class("todo")
    expect(page.locator(".steps li").nth(1)).to_have_attribute("title", "Still to do")
    for _ in range(2):
        setup.next()
    setup.at("Try it")
    expect(page.locator("#noBrain")).to_contain_text("and key actions off, Tycho can't answer")
    expect(page.locator("#keysOffSay")).to_have_count(0)  # #noBrain says it all
    setup.close()


def test_try_it_says_commands_are_told_not_pressed_with_key_actions_off(
    browser: Browser, path: Path, vault: dict[str, str]
) -> None:
    """From the review of #521: with key actions off, the built-in phrases Try it lists
    press nothing, so it says Tycho names the key instead."""
    settings.save(Settings(key_actions=False), path)
    setup = Setup(browser, path)
    page = setup.page
    setup.at("Game")
    setup.next()
    setup.at("Keys")
    page.locator("#k-anthropic").fill(ANTHROPIC)
    page.locator("[data-store='anthropic']").click()
    expect(page.locator("#keysForm")).to_contain_text("Wxyz")
    for _ in range(3):
        setup.next()
    setup.at("Try it")
    expect(page.locator("#keysOffSay")).to_contain_text("tells you the key")
    setup.close()


def test_at_the_smallest_window_next_is_wholly_in_view(setup: Setup) -> None:
    """#448: the sticky bar sat flush with the bottom edge, set for the wide window's
    padding."""
    page = setup.page
    width, height = window.SMALLEST
    page.set_viewport_size({"width": width, "height": height})
    setup.at("Game")
    for _ in range(3):
        setup.next()
    setup.at("Privacy")  # the longest step
    bar = page.locator(".stepnav").bounding_box()
    nxt = page.get_by_role("button", name="Next", exact=True).bounding_box()
    assert bar is not None
    assert nxt is not None
    assert nxt["y"] + nxt["height"] <= height
    assert bar["y"] + bar["height"] <= height + 0.5  # the bar's own padding shows below Next


def test_a_player_set_up_before_looking_existed_is_asked_once(
    browser: Browser, path: Path, vault: dict[str, str]
) -> None:
    """ADR 14's amendments (#31, #484): players who set up before Tycho could look, or
    keep a history, are asked on their next start, every unanswered question first, and
    the window goes on as usual once all are. Skipping leaves each off."""
    settings.save(Settings(push_to_talk="mouse:0x06"), path)
    setup = Setup(browser, path, first_run=False)
    page = setup.page
    setup.at("Privacy")
    lede = "Three questions left: crash reports, looking at your game and keeping a history"
    expect(page.locator(".lede")).to_contain_text(lede)
    cards = page.locator("main > .card")
    expect(cards.nth(0)).to_have_id("crashCard")
    expect(cards.nth(1)).to_have_id("lookCard")
    expect(cards.nth(2)).to_have_id("historyCard")
    page.locator("#crashCard [data-crash='no']").click()
    setup.at("Privacy")  # two still to answer: still here
    lede = "Two questions left: looking at your game and keeping a history"
    expect(page.locator(".lede")).to_contain_text(lede)
    page.locator("#lookCard [data-look='yes']").click()
    expect(page.locator(".lede")).to_contain_text("One question left: keeping a history")
    page.locator("#historyCard [data-history='no']").click()
    expect(page.locator("#homeState")).to_be_visible()
    assert setup.saved().look_at_screen is True
    assert setup.saved().keep_history is False
    setup.close()
    again = Setup(browser, path, first_run=False)  # the next start: answered, not asked
    expect(again.page.locator("#homeState")).to_be_visible()
    again.close()


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
    expect(first.page.locator("main > .card").nth(-3)).to_have_id("crashCard")
    expect(first.page.locator("main > .card").nth(-2)).to_have_id("lookCard")
    expect(first.page.locator("main > .card").last).to_have_id("historyCard")
    first.close()


def test_a_game_tycho_only_advises_in_has_no_empty_pages(
    browser: Browser, path: Path, vault: dict[str, str]
) -> None:
    """The design review: an advisor game showed an empty commands table with a column
    for keys, and another game's folder settings."""
    settings.save(
        Settings(
            game="wow-forever",
            push_to_talk="mouse:0x06",
            crash_reports=False,
            look_at_screen=False,
            keep_history=False,
        ),
        path,
    )
    setup = Setup(browser, path, first_run=False)
    page = setup.page
    page.get_by_role("button", name="What to say").click()
    expect(page.locator("#noCommands")).to_contain_text("ask in your own words")
    expect(page.locator("table")).to_have_count(0)
    page.get_by_role("button", name="Settings", exact=True).click()
    page.get_by_role("tab", name="Game and controls").click()
    expect(page.locator(".set", has_text=re.compile("^Push-to-talk"))).to_have_count(1)
    page.get_by_role("tab", name="Advanced").click()
    expect(page.locator(".set", has_text="Brain model")).to_have_count(1)
    expect(page.locator(".set", has_text="Game folder")).to_have_count(0)
    expect(page.locator(".set", has_text="Game channel")).to_have_count(0)
    names = [r["name"] for g in setup.bridge.settings()["groups"] for r in g["rows"]]  # type: ignore[index,union-attr]
    assert "game_root" not in names
    assert "game" in names
    setup.close()
    # A game with a folder keeps them.
    settings.save(
        Settings(
            push_to_talk="mouse:0x06", crash_reports=False, look_at_screen=False, keep_history=False
        ),
        path,
    )
    there = Setup(browser, path, first_run=False)
    there.page.get_by_role("button", name="Settings", exact=True).click()
    there.page.get_by_role("tab", name="Advanced").click()
    expect(there.page.locator(".set", has_text="Game folder")).to_have_count(1)
    there.close()


def test_an_advisor_games_commands_page_says_the_files_warnings(
    browser: Browser, path: Path, vault: dict[str, str]
) -> None:
    """#448: the "Just ask" card left out what was wrong in the player's commands file."""
    settings.save(
        Settings(
            game="wow-forever",
            push_to_talk="mouse:0x06",
            crash_reports=False,
            look_at_screen=False,
            keep_history=False,
        ),
        path,
    )
    setup = Setup(browser, path, first_run=False)
    shown = setup.bridge.commands()
    warning = "'jump': 'stop' is one of Tycho's own words, so it never runs the command"
    setup.bridge.commands = lambda: {**shown, "warnings": [warning]}  # type: ignore[method-assign]
    page = setup.page
    page.get_by_role("button", name="What to say").click()
    expect(page.locator("#noCommands")).to_contain_text("ask in your own words")
    expect(page.locator("#noCommands .warn-line")).to_have_text(warning)
    setup.close()


def test_a_view_change_fetches_the_settings_once(
    browser: Browser, path: Path, vault: dict[str, str]
) -> None:
    """#454: the game box fetched every setting (and loaded every pack) on each view
    change, and Settings and The Tile then fetched them again."""
    settings.save(
        Settings(
            push_to_talk="mouse:0x06", crash_reports=False, look_at_screen=False, keep_history=False
        ),
        path,
    )
    setup = Setup(browser, path, first_run=False)
    page = setup.page
    expect(page.locator("#homeState")).to_be_visible()
    fetched: list[int] = []
    real = setup.bridge.settings

    def counted() -> dict[str, object]:
        fetched.append(1)
        return real()

    setup.bridge.settings = counted  # type: ignore[method-assign]
    for place, heading in [("Settings", "Settings"), ("The Tile", "The Tile")]:
        fetched.clear()
        page.get_by_role("button", name=place, exact=True).click()
        expect(page.get_by_role("heading", name=heading, exact=True)).to_be_visible()
        expect(page.locator("#gameBox")).to_contain_text("Star Citizen")
        assert len(fetched) == 1
    setup.close()


def test_a_newer_version_makes_get_the_button_to_press(
    browser: Browser, path: Path, vault: dict[str, str]
) -> None:
    settings.save(
        Settings(
            push_to_talk="mouse:0x06", crash_reports=False, look_at_screen=False, keep_history=False
        ),
        path,
    )
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
        Settings(
            game="wow-forever",
            push_to_talk="mouse:0x06",
            crash_reports=False,
            look_at_screen=False,
            keep_history=False,
        ),
        path,
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
    places = ["Home", "What to say", "Memory", "History", "The Tile", "Settings"]  # #483, #484
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
    settings.save(
        Settings(
            push_to_talk="mouse:0x06", crash_reports=False, look_at_screen=False, key_actions=False
        ),
        path,
    )
    setup = Setup(browser, path, first_run=False)
    expect(setup.page.locator("#gameBox")).to_contain_text("Tycho only talks")
    setup.close()


def test_the_window_takes_the_games_own_colour(
    browser: Browser, path: Path, vault: dict[str, str]
) -> None:
    """ADR 17 (amended): one accent per game, from the game's pack."""
    settings.save(
        Settings(
            push_to_talk="mouse:0x06",
            crash_reports=False,
            look_at_screen=False,
            keep_history=False,
            game="wow-forever",
        ),
        path,
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
    settings.save(
        Settings(
            push_to_talk="mouse:0x06", crash_reports=False, look_at_screen=False, keep_history=False
        ),
        path,
    )
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
    # Paused from the tray while Home is open: Home says so without a click, and
    # redraws only that line, not the checks (review of #463).
    page.evaluate("document.getElementById('checks').dataset.kept = 'yes'")
    setup.listening = False
    expect(page.locator("#homeState h1")).to_have_text("Paused", timeout=6000)
    expect(page.locator("#checks")).to_have_attribute("data-kept", "yes")
    setup.close()


def test_home_state_word_is_big_and_coloured_by_state_console_plus(
    browser: Browser, path: Path, vault: dict[str, str]
) -> None:
    """Console plus (Ben, 16 Nov 2026): Home's state word reads from across the room,
    paused shows grey rather than as a problem, and section labels carry a slash mark."""
    settings.save(
        Settings(
            push_to_talk="mouse:0x06", crash_reports=False, look_at_screen=False, keep_history=False
        ),
        path,
    )
    setup = Setup(browser, path, first_run=False, recent=Recent(), listening=True)
    page = setup.page
    word = page.locator("#homeState h1")
    expect(word).to_have_text("Listening")
    assert word.evaluate("e => parseFloat(getComputedStyle(e).fontSize)") >= 48
    label = page.locator("h2.label").first
    assert "//" in label.evaluate("e => getComputedStyle(e, '::before').content")
    setup.listening = False
    expect(word).to_have_text("Paused", timeout=6000)
    expect(page.locator("#homeState")).to_have_class(re.compile(r"\bpaused\b"))
    expect(page.locator("#homeState")).not_to_have_class(re.compile(r"\bstuck\b"))
    setup.close()


def test_home_state_word_is_amber_when_stuck_and_smaller_in_a_narrow_window(
    browser: Browser, path: Path, vault: dict[str, str]
) -> None:
    """#472: stuck beats paused (amber, not grey), and the narrow window's smaller word
    applies: it came before the 54px rule, so 54px always won."""
    settings.save(
        Settings(
            push_to_talk="mouse:0x06", crash_reports=False, look_at_screen=False, keep_history=False
        ),
        path,
    )
    checks = [{"level": "fail", "what": "Brain", "detail": "no key", "fix": "add a key"}]
    setup = Setup(browser, path, first_run=False, findings=checks, listening=False)
    page = setup.page
    box, word = page.locator("#homeState"), page.locator("#homeState h1")
    expect(box).to_have_class(re.compile(r"\bstuck\b"))
    expect(box).not_to_have_class(re.compile(r"\bpaused\b"))
    amber = page.evaluate(
        "getComputedStyle(document.documentElement).getPropertyValue('--caution')"
    )
    expect(word).to_have_css("color", _rgb(amber))
    assert word.evaluate("e => parseFloat(getComputedStyle(e).fontSize)") >= 48
    page.set_viewport_size({"width": 740, "height": 700})
    expect(word).to_have_css("font-size", "36px")
    setup.close()


def test_home_state_word_is_grey_when_paused(
    browser: Browser, path: Path, vault: dict[str, str]
) -> None:
    settings.save(
        Settings(
            push_to_talk="mouse:0x06", crash_reports=False, look_at_screen=False, keep_history=False
        ),
        path,
    )
    setup = Setup(browser, path, first_run=False, recent=Recent(), listening=False)
    page = setup.page
    word = page.locator("#homeState h1")
    expect(word).to_have_text("Paused")
    dim = page.evaluate("getComputedStyle(document.documentElement).getPropertyValue('--dim')")
    expect(word).to_have_css("color", _rgb(dim))
    setup.close()


def _rgb(hex_colour: str) -> str:
    """A ``#rrggbb`` CSS value as the browser's computed ``rgb(r, g, b)``."""
    h = hex_colour.strip().lstrip("#")
    r, g, b = (int(h[i : i + 2], 16) for i in (0, 2, 4))
    return f"rgb({r}, {g}, {b})"


def test_quit_is_at_the_foot_of_the_places(
    browser: Browser, path: Path, vault: dict[str, str]
) -> None:
    """#486 (Ben, from play): Quit was only in Settings, Updates, an odd place to look."""
    settings.save(
        Settings(
            push_to_talk="mouse:0x06", crash_reports=False, look_at_screen=False, keep_history=False
        ),
        path,
    )
    setup = Setup(browser, path, first_run=False, can_quit=True)
    page = setup.page
    side = page.locator("aside.side")
    quit_button = side.get_by_role("button", name="Quit Tycho")
    expect(quit_button).to_be_visible()
    # The last thing down the side, below the places and the version.
    below = quit_button.evaluate(
        "q => [...q.parentElement.children].filter(e => e.offsetParent)"
        ".every(e => e === q || e.getBoundingClientRect().bottom <= q.getBoundingClientRect().top)"
    )
    assert below
    quit_button.click()
    expect(quit_button).to_be_disabled()
    assert setup.quits == [True]
    setup.close()


def test_no_quit_down_the_side_with_nothing_to_stop(setup: Setup) -> None:
    expect(setup.page.locator("#quitSide")).to_be_hidden()
    setup.close()


def test_after_a_first_run_try_it_is_real_and_sends_nothing(
    browser: Browser, path: Path, vault: dict[str, str]
) -> None:
    """ADR 17: once Tycho listens after a first run, the window opens on setup's Try
    it. What the player says shows there with what Tycho would press, and nothing
    reaches the game until they leave the step."""
    settings.save(
        Settings(
            push_to_talk="mouse:0x06", crash_reports=False, look_at_screen=False, keep_history=False
        ),
        path,
    )
    recent = Recent()
    recent.add(Result(1, "done", text="from before", say="Old."))
    setup = Setup(browser, path, first_run=False, recent=recent, listening=True, opening="try_it")
    page = setup.page
    setup.at("Try it")
    card = page.locator("#tryCard")
    expect(card).to_contain_text("Hold Mouse side (forward), say something")
    expect(card).to_contain_text("Nothing reaches the game while this step is open")
    expect(card.locator("#tried")).to_contain_text("Nothing yet")  # not what came before
    assert setup.trying == [True]
    time.sleep(0.05)  # what's said now is after Try it began
    recent.add(Result(2, "done", text="gear down", would_press=("Landing System (N)",)))
    expect(card.locator("#tried li")).to_have_count(1, timeout=4000)
    expect(card.locator("#tried li")).to_contain_text("Would press Landing System (N).")
    # Off to Home: Tycho presses keys again.
    page.locator("nav [data-view='status']").click()
    expect(page.locator("#homeState")).to_be_visible()
    assert setup.trying == [True, False]
    setup.close()


def test_try_it_ends_when_the_player_goes_back_a_step(
    browser: Browser, path: Path, vault: dict[str, str]
) -> None:
    settings.save(
        Settings(
            push_to_talk="mouse:0x06", crash_reports=False, look_at_screen=False, keep_history=False
        ),
        path,
    )
    setup = Setup(browser, path, first_run=False, listening=True, opening="try_it")
    setup.at("Try it")
    expect(setup.page.locator("#tryCard")).to_be_visible()
    setup.page.get_by_role("button", name="Back", exact=True).click()
    setup.at("Privacy")
    expect(setup.page.locator("#tryCard")).to_have_count(0)
    assert setup.trying == [True, False]
    setup.close()


def test_try_it_ends_when_the_player_switches_to_the_game(
    browser: Browser, path: Path, vault: dict[str, str]
) -> None:
    """Review of #464: left open on Try it, nothing reached the game, and Tycho still
    played done. Try it lasts only while the window is in front."""
    settings.save(
        Settings(
            push_to_talk="mouse:0x06", crash_reports=False, look_at_screen=False, keep_history=False
        ),
        path,
    )
    setup = Setup(browser, path, first_run=False, listening=True, opening="try_it")
    setup.at("Try it")
    expect(setup.page.locator("#tryCard")).to_contain_text("Switch to the game")
    setup.page.evaluate("window.dispatchEvent(new Event('blur'))")
    _until(setup.page, lambda: setup.trying == [True, False])
    setup.page.evaluate("window.dispatchEvent(new Event('focus'))")
    _until(setup.page, lambda: setup.trying == [True, False, True])
    setup.close()


def _until(page: Page, ok: Callable[[], bool], seconds: float = 4.0) -> None:
    """Wait for the bridge to be called: the page's calls are served while it waits."""
    end = time.monotonic() + seconds
    while not ok():
        assert time.monotonic() < end, "timed out"
        page.wait_for_timeout(50)


def test_a_first_runs_try_it_says_the_window_comes_back(setup: Setup) -> None:
    """No Tycho is listening during a first run's setup: Try it says it opens again
    once Tycho starts, rather than a test that hears nothing."""
    page = setup.page
    page.locator(".steps li").last.wait_for()
    for _ in range(4):
        setup.next()
    setup.at("Try it")
    expect(page.locator("#tryCard")).to_have_count(0)
    expect(page.get_by_text("this window opens again here so you can try it")).to_be_visible()
    assert setup.trying == []


@pytest.mark.parametrize(
    ("game", "own"), [("star-citizen", ["Ship specs", "Your fleet"]), ("wow-forever", [])]
)
def test_the_tile_page_lists_the_games_own_kinds_of_card(
    browser: Browser, path: Path, vault: dict[str, str], game: Game, own: list[str]
) -> None:
    """#422: the hidden-cards list named Star Citizen's ship and fleet cards in every
    game. Now each kind is a switch, and a game's own come from its pack."""
    settings.save(
        Settings(
            push_to_talk="mouse:0x06",
            crash_reports=False,
            look_at_screen=False,
            keep_history=False,
            game=game,
            game_root=None,
        ),
        path,
    )
    setup = Setup(browser, path, first_run=False)
    page = setup.page
    page.locator("nav [data-view='tile']").click()
    cards = page.locator("[data-card]")
    expect(cards.first).to_be_visible()
    names = cards.all_inner_texts()
    assert [n for n in names if n in ("Ship specs", "Your fleet")] == own
    assert "Lists" in names
    assert "Notices from Tycho" in names
    lists = page.locator("[data-card='list']")
    expect(lists).to_have_attribute("aria-pressed", "true")
    lists.click()
    expect(page.locator("[data-card='list']")).to_have_attribute("aria-pressed", "false")
    assert setup.saved().tile_hidden_cards == "list"
    page.locator("[data-card='list']").click()
    expect(page.locator("[data-card='list']")).to_have_attribute("aria-pressed", "true")
    assert setup.saved().tile_hidden_cards == ""
    setup.close()


def test_a_kind_hidden_in_another_game_stays_hidden(
    browser: Browser, path: Path, vault: dict[str, str]
) -> None:
    """Review of #466: WoW: Forever has no ship cards, so it shows no switch for them,
    but hiding a list there keeps Star Citizen's ship cards hidden."""
    settings.save(
        Settings(
            push_to_talk="mouse:0x06",
            crash_reports=False,
            look_at_screen=False,
            keep_history=False,
            game="wow-forever",
            tile_hidden_cards="ship",
        ),
        path,
    )
    setup = Setup(browser, path, first_run=False)
    page = setup.page
    page.locator("nav [data-view='tile']").click()
    page.locator("[data-card='list']").click()
    expect(page.locator("[data-card='list']")).to_have_attribute("aria-pressed", "false")
    assert setup.saved().tile_hidden_cards == "ship, list"
    setup.close()


def test_the_speaking_up_switches_turn_kinds_on_and_quick_clicks_add_up(
    browser: Browser, path: Path, vault: dict[str, str]
) -> None:
    """#550 (from #546's review): each kind is a switch in Settings, under Voice, and a
    second click before the first is saved builds on it, as the Tile's card switches do."""
    settings.save(
        Settings(
            push_to_talk="mouse:0x06",
            crash_reports=False,
            look_at_screen=False,
            keep_history=False,
            game_root=None,
        ),
        path,
    )
    setup = Setup(browser, path, first_run=False)
    page = setup.page
    page.get_by_role("button", name="Settings", exact=True).click()
    page.get_by_role("tab", name="Voice").click()
    quantum = page.locator("[data-callout='quantum']")
    expect(quantum).to_have_attribute("aria-checked", "false")
    quantum.click()
    page.locator("[data-callout='monitored_space']").click()  # at once, before a redraw
    expect(page.locator("[data-callout='quantum']")).to_have_attribute("aria-checked", "true")
    expect(page.locator("[data-callout='monitored_space']")).to_have_attribute(
        "aria-checked", "true"
    )
    assert setup.saved().callouts == "quantum, monitored_space"
    page.locator("[data-callout='quantum']").click()
    expect(page.locator("[data-callout='quantum']")).to_have_attribute("aria-checked", "false")
    assert setup.saved().callouts == "monitored_space"
    setup.close()


def test_a_game_with_no_callouts_says_so(
    browser: Browser, path: Path, vault: dict[str, str]
) -> None:
    settings.save(
        Settings(
            push_to_talk="mouse:0x06",
            crash_reports=False,
            look_at_screen=False,
            keep_history=False,
            game="wow-forever",
        ),
        path,
    )
    setup = Setup(browser, path, first_run=False)
    page = setup.page
    page.get_by_role("button", name="Settings", exact=True).click()
    page.get_by_role("tab", name="Voice").click()
    expect(page.get_by_text("This game has nothing to speak up about.")).to_be_visible()
    expect(page.locator("[data-callout]")).to_have_count(0)
    setup.close()


def test_the_tile_page_picks_the_tiles_screen(
    browser: Browser, path: Path, vault: dict[str, str]
) -> None:
    """#447: follow the game (as before), or a screen plugged in now. A chosen screen
    that isn't plugged in stays listed, so opening the page doesn't lose it."""
    settings.save(
        Settings(
            push_to_talk="mouse:0x06", crash_reports=False, look_at_screen=False, keep_history=False
        ),
        path,
    )
    setup = Setup(browser, path, first_run=False)
    page = setup.page
    page.locator("nav [data-view='tile']").click()
    screen = page.get_by_label("Screen", exact=True)
    expect(screen).to_have_value("game")
    assert screen.locator("option").all_inner_texts() == [
        "Follow the game",
        "Screen 1 (main), 2560 x 1440",
        "Screen 2, 1920 x 1080",
    ]
    screen.select_option(label="Screen 2, 1920 x 1080")
    expect(page.locator("#toast")).to_contain_text("The Tile's screen: Screen 2, 1920 x 1080")
    assert setup.saved().tile_screen == r"\\.\DISPLAY2"
    setup.close()
    settings.save(
        Settings(
            push_to_talk="mouse:0x06",
            crash_reports=False,
            look_at_screen=False,
            keep_history=False,
            tile_screen=r"\\.\DISPLAY7",
        ),
        path,
    )
    setup = Setup(browser, path, first_run=False)
    setup.page.locator("nav [data-view='tile']").click()
    screen = setup.page.get_by_label("Screen", exact=True)
    expect(screen).to_have_value(r"\\.\DISPLAY7")
    expect(screen.locator("option:checked")).to_have_text(r"\\.\DISPLAY7 (not plugged in now)")
    setup.close()


def test_home_has_no_exchanges_list_while_tycho_isnt_running(
    browser: Browser, path: Path, vault: dict[str, str]
) -> None:
    settings.save(
        Settings(
            push_to_talk="mouse:0x06", crash_reports=False, look_at_screen=False, keep_history=False
        ),
        path,
    )
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
        Settings(
            push_to_talk="mouse:0x06",
            crash_reports=False,
            look_at_screen=False,
            keep_history=False,
            voice="The Ship's Mind",
        ),
        path,
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


class FakeHangar:
    """A hangar export as the games side reads it: what it would change, then saved."""

    done = "Saved. Your fleet has 2 ships, and Tycho uses them from now on."

    def __init__(self) -> None:
        self.lines = ["ships.csv lists 2 ships.", "Adds 2: Caterpillar, Cutlass Black."]
        self.changes = True
        self.saved = 0

    def save(self) -> None:
        self.saved += 1


def test_settings_imports_a_hangar_after_showing_what_changes(
    browser: Browser, path: Path, vault: dict[str, str]
) -> None:
    hangar = FakeHangar()
    asked: list[tuple[str, Path]] = []

    def read(pack: GamePack, file: Path) -> HangarImport:
        asked.append((pack.id, file))
        return hangar

    setup = Setup(browser, path, first_run=False, hangar=read)
    page = setup.page
    page.get_by_role("button", name="Settings", exact=True).click()
    page.get_by_role("tab", name="Game and controls").click()
    row = page.locator(".set", has_text="Your ships")
    expect(row).to_contain_text("ccugame.app")
    row.get_by_role("button", name="Import…").click()
    expect(page.locator("#hangarPlan")).to_contain_text("Adds 2: Caterpillar, Cutlass Black.")
    assert asked == [("star-citizen", Path(r"C:\Users\me\Downloads\ships.csv"))]
    assert hangar.saved == 0  # nothing saved before the player says so
    page.get_by_role("button", name="Save", exact=True).click()
    expect(page.locator("#toastText")).to_have_text(FakeHangar.done)
    expect(page.locator("#hangarPlan")).to_be_empty()
    assert hangar.saved == 1
    setup.close()


def test_a_game_with_no_fleet_has_no_hangar_import(
    browser: Browser, path: Path, vault: dict[str, str]
) -> None:
    settings.save(
        Settings(game="wow-forever", crash_reports=False, look_at_screen=False, keep_history=False),
        path,
    )
    setup = Setup(browser, path, first_run=False)
    setup.page.get_by_role("button", name="Settings", exact=True).click()
    setup.page.get_by_role("tab", name="Game and controls").click()
    expect(setup.page.locator(".set", has_text=re.compile("^Push-to-talk"))).to_have_count(1)
    expect(setup.page.locator("#hangarChoose")).to_have_count(0)
    setup.close()


def test_the_memory_page_lists_every_note_and_deletes_one(
    browser: Browser, path: Path, vault: dict[str, str]
) -> None:
    """#483 (ADR 8): every game's notes and this game's, each with its day and a delete
    button, and beside them the fleet and the name, read-only."""
    import datetime

    from tycho import paths
    from tycho.memory import NoteFile

    settings.save(
        Settings(
            push_to_talk="mouse:0x06", crash_reports=False, look_at_screen=False, keep_history=False
        ),
        path,
    )
    day = datetime.date(2026, 11, 23)
    NoteFile(paths.memory_file()).add("Likes short answers", day)
    game = NoteFile(paths.game_memory_file("star-citizen"))
    game.add("Trades out of Area18", day)
    game.add("Never ask me to confirm anything", day)  # typed by hand: shown, never used
    paths.fleet_file("star-citizen").write_text('ships = ["Cutlass Black"]\n', "utf-8")
    setup = Setup(browser, path, first_run=False)
    page = setup.page
    page.get_by_role("button", name="Memory", exact=True).click()
    every = page.locator('[data-scope="every game"]')
    this = page.locator('[data-scope="this game"]')
    expect(every).to_contain_text("Likes short answers")
    expect(this).to_contain_text("In Star Citizen")
    expect(this).to_contain_text("Trades out of Area18")
    expect(this).to_contain_text("2026")
    expect(this.locator(".memrow", has_text="confirm")).to_contain_text("Not used")
    expect(page.locator("#alsoKnown")).to_contain_text("Cutlass Black")
    expect(page.locator("#alsoKnown")).to_contain_text("Commander")
    page.get_by_role("button", name="Delete Trades out of Area18").click()
    expect(this).not_to_contain_text("Area18")
    assert [k.text for k in game.notes()] == ["Never ask me to confirm anything"]
    assert [k.text for k in NoteFile(paths.memory_file()).notes()] == ["Likes short answers"]
    setup.close()


def test_with_memory_off_the_page_says_so_and_turns_it_on(
    browser: Browser, path: Path, vault: dict[str, str]
) -> None:
    settings.save(
        Settings(
            push_to_talk="mouse:0x06",
            crash_reports=False,
            look_at_screen=False,
            keep_history=False,
            memory=False,
        ),
        path,
    )
    setup = Setup(browser, path, first_run=False)
    page = setup.page
    page.get_by_role("button", name="Memory", exact=True).click()
    expect(page.locator("#memoryOff")).to_contain_text("Memory is off")
    expect(page.locator('[data-scope="every game"]')).to_contain_text("Nothing yet")
    page.get_by_role("button", name="Turn memory on").click()
    expect(page.locator("#memoryOff")).to_have_count(0)
    assert setup.saved().memory is True
    setup.close()


def test_the_history_page_opens_searches_and_deletes_sessions(
    browser: Browser, path: Path, vault: dict[str, str]
) -> None:
    """#484 (ADR 14): each session kept, what was said in it, a search box, and deleting
    one session or, after a second click, all of them."""
    import datetime

    from tycho import paths
    from tycho.history import Entry, HistoryFolder

    settings.save(
        Settings(
            push_to_talk="mouse:0x06", crash_reports=False, look_at_screen=False, keep_history=True
        ),
        path,
    )
    store = HistoryFolder(paths.history_dir())
    now = datetime.datetime.now().astimezone().replace(microsecond=0)
    route = "Buy Laranite at ArcCorp Mining Area 045, sell at Area18."
    store.append(
        f"{now - datetime.timedelta(hours=2):%Y-%m-%d_%H-%M-%S}", Entry(now, "a route", route)
    )
    later = f"{now - datetime.timedelta(hours=1):%Y-%m-%d_%H-%M-%S}"
    store.append(later, Entry(now, "gear down", "Gear down.", ("v_toggle_landing_system",)))
    setup = Setup(browser, path, first_run=False)
    page = setup.page
    page.get_by_role("button", name="History", exact=True).click()
    expect(page.locator("#historyOff")).to_have_count(0)
    rows = page.locator("#historySessions .memrow")
    expect(rows).to_have_count(2)
    rows.first.locator("[data-open]").click()  # newest first
    expect(page.locator("#historySessions")).to_contain_text("Pressed v_toggle_landing_system")
    page.locator("#historyWords").fill("Area18")
    page.get_by_role("button", name="Search", exact=True).click()
    expect(page.locator("#historyFound")).to_contain_text("ArcCorp Mining Area 045")
    rows.first.locator("[data-drop]").click()
    expect(rows).to_have_count(1)
    assert later not in [p.id for p in store.sessions()]
    page.locator("#historyAll").click()  # asks first
    expect(page.locator("#historyAll")).to_contain_text("Click again")
    assert len(store.sessions()) == 1
    page.locator("#historyAll").click()
    expect(page.locator("#historySessions")).to_contain_text("Nothing kept yet")
    assert store.sessions() == []
    setup.close()


def test_with_history_off_the_page_says_so_and_turns_it_on(
    browser: Browser, path: Path, vault: dict[str, str]
) -> None:
    settings.save(
        Settings(
            push_to_talk="mouse:0x06", crash_reports=False, look_at_screen=False, keep_history=False
        ),
        path,
    )
    setup = Setup(browser, path, first_run=False)
    page = setup.page
    page.get_by_role("button", name="History", exact=True).click()
    expect(page.locator("#historyOff")).to_contain_text("History is off")
    page.get_by_role("button", name="Turn history on").click()
    expect(page.locator("#historyOff")).to_have_count(0)
    assert setup.saved().keep_history is True
    setup.close()


def test_the_memory_page_lists_owned_blueprints_and_takes_one_off(
    browser: Browser, path: Path, vault: dict[str, str]
) -> None:
    """#598 (ADR 8's third amendment): the blueprints the player owns, each with its day
    and a delete button; one taken off isn't added back from the log."""
    from tycho import paths

    settings.save(
        Settings(
            push_to_talk="mouse:0x06", crash_reports=False, look_at_screen=False, keep_history=False
        ),
        path,
    )
    listed = paths.blueprints_file("star-citizen")
    listed.parent.mkdir(parents=True, exist_ok=True)
    listed.write_text(
        '[[blueprint]]\nname = "Monde Helmet Hiro"\ngot = 2026-11-22\nfrom = "log"\n'
        '[[blueprint]]\nname = "Killshot Rifle"\nfrom = "you"\n',
        "utf-8",
    )
    setup = Setup(browser, path, first_run=False)
    page = setup.page
    page.get_by_role("button", name="Memory", exact=True).click()
    card = page.locator("#ownedBlueprints")
    expect(card).to_contain_text("Monde Helmet Hiro")
    expect(card).to_contain_text("2026")
    expect(card.locator(".memrow", has_text="Killshot")).to_contain_text("you said")
    page.get_by_role("button", name="Delete Monde Helmet Hiro").click()
    expect(card).not_to_contain_text("Monde Helmet")
    text = listed.read_text("utf-8")
    assert "Killshot Rifle" in text
    assert 'taken_off = ["Monde Helmet Hiro"]' in text
    setup.close()
