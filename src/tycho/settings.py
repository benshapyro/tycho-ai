"""Tycho's settings: one typed, validated schema, saved as TOML (ADR 5, ADR 11).

The schema is the single source of truth: the settings screen comes from it, voice
edits it (``spoken_settings``), players can edit the file. Descriptions are UI text.

Each field also says whether it can change by voice (``voice``), when a change takes
effect (``applies``), and, if not by voice, how to change it (``how``). A misheard
sentence must never weaken a safeguard or your privacy (ADR 5), so some settings can
only go one way by voice: ``lower_only`` means lower, or off.
"""

import re
import threading
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Literal, cast

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from tycho import paths
from tycho.brain.providers import BrainModel, Provider, choose
from tycho.callouts import CalloutKinds
from tycho.ptt.binding import Binding
from tycho.settings_toml import read_toml, to_toml, toml_value, write_atomic
from tycho.tile import values as tiles
from tycho.voice import voices

CURRENT_VERSION = 1

type Game = Literal["star-citizen", "wow-forever"]
"""The game packs that ship inside Tycho (ADR 18): a test checks ``packs.installed()``."""
RENAMED_GAMES = {"world-of-warcraft": "wow-forever"}
"""Pack ids renamed before a release: a file saved on main may say the old one (#349)."""
DEFAULT_CALL_ME = "Commander"
"""What Tycho calls the player unless they or the game pack choose otherwise (#15, #349)."""
type Channel = Literal["LIVE", "PTU", "EPTU", "TECH-PREVIEW", "HOTFIX"]  # Star Citizen's
type Device = Literal["auto", "gpu", "cpu"]
type VoiceRule = Literal["yes", "lower_only", "no"]
type Applies = Literal["now", "restart"]
type HowOften = Literal["sparingly", "often"]
type VoiceModel = Literal["the voice's own", "v4 turbo", "flash"]


def _rule(voice: VoiceRule, applies: Applies = "restart", how: str = "") -> dict[str, Any]:
    """A field's voice rule, kept in the schema so the settings screen can show it too."""
    return {"voice": voice, "applies": applies, **({"how": how} if how else {})}


FILE_OR_APP = "in Tycho's window (Settings) or in settings.toml"
FILE_FOR_NOW = "in settings.toml for now; Tycho's setup will change it in the window"

NAME = re.compile(r"[\w .'-]{0,40}")
"""What Tycho may call the player (40 letters, digits, spaces, ' - .): it goes to the brain."""


class SettingsError(Exception):
    """The settings file couldn't be read, with a message a player can act on."""


class Settings(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    version: int = Field(
        default=CURRENT_VERSION,
        description="Settings file format version.",
        json_schema_extra=_rule("no", how="Tycho sets it"),
    )
    push_to_talk: str | None = Field(
        default=None,
        description="The button you hold while talking to Tycho, e.g. mouse:0x06 for a mouse "
        "side button. Set it by pressing the button in setup.",
        json_schema_extra=_rule("no", how="run `tycho bind` and press the button"),
    )
    microphone: str | None = Field(
        default=None,
        description="Microphone name or number. Empty means the Windows default.",
        # A wrong microphone would leave Tycho unable to hear the request to undo it.
        json_schema_extra=_rule("no", how=FILE_FOR_NOW),
    )
    game: Game = Field(
        default="star-citizen",
        description="The game Tycho helps with: its commands, keybinds, look-ups and what the "
        "brain knows. Changes apply when Tycho restarts.",
        # Keybinds, the briefing and the look-ups are all loaded at start (ADR 18).
        json_schema_extra=_rule("no", how=FILE_OR_APP),
    )
    game_root: Path | None = Field(
        default=None,
        description="For Star Citizen: the folder that contains LIVE. Empty means Tycho "
        "finds it from the RSI Launcher, the default folder, or common folders on each drive.",
        json_schema_extra=_rule("no", how=FILE_FOR_NOW),
    )
    game_channel: Channel = Field(
        default="LIVE",
        description="For Star Citizen: which version of the game you play: LIVE, PTU, EPTU, "
        "TECH-PREVIEW or HOTFIX. Tycho reads that version's keybinds.",
        json_schema_extra=_rule("yes"),
    )
    transcription_device: Device = Field(
        default="auto",
        description="Where speech recognition runs. Auto uses the processor, so the game "
        "keeps the graphics card; the graphics card is faster but shares it with the game.",
        json_schema_extra=_rule("yes"),
    )
    feedback_volume: float = Field(
        default=0.5,
        ge=0.0,
        le=1.0,
        description="Volume of Tycho's done and not-done sounds.",
        json_schema_extra=_rule("yes", "now"),
    )
    voice_volume: float = Field(
        default=0.8,
        ge=0.0,
        le=1.0,
        description="Volume of Tycho's voice. Zero turns it off.",
        json_schema_extra=_rule("yes", "now"),
    )
    duck_game: bool = Field(
        default=True,
        description="Turn the game's sound down while Tycho speaks, and back up after, so you "
        "hear Tycho over engines and combat. Only the game: voice chat is left alone.",
        json_schema_extra=_rule("yes", "now"),
    )
    duck_level: float = Field(
        default=0.3,
        ge=0.0,
        le=1.0,
        description="How loud the game is while Tycho speaks, as a share of where you left it.",
        json_schema_extra=_rule("yes", "now"),
    )
    voice: str = Field(
        default="George",
        description="Tycho's ElevenLabs voice: George, Sarah, Lily, Brian, or a voice ID "
        "from your ElevenLabs account. A voice you designed works only with your own key. "
        "If your key can't use it, Tycho speaks as George and says so.",
        # By voice, only the four names every key can use; an ID needs the app (#355).
        json_schema_extra=_rule("yes", how=FILE_OR_APP),
    )
    voice_model: VoiceModel = Field(
        default="the voice's own",
        description="The ElevenLabs model for the voice. The voice's own is Flash v2.5 for "
        "George (quickest) and v4 Turbo for the rest. Both cost the same.",
        json_schema_extra=_rule("no", how=FILE_OR_APP),
    )
    confirm_seconds: float = Field(
        default=5.0,
        ge=2.0,
        le=15.0,
        description='How long you have to say "confirm" after Tycho asks, in seconds.',
        # Longer means a stray "confirm" later still counts (the author's call, 2026-11-05).
        json_schema_extra=_rule("lower_only", "now", how=FILE_OR_APP),
    )
    key_actions: bool = Field(
        default=True,
        description="Let Tycho press keys for you, through its safety gate. Off makes Tycho "
        "an advisor: it answers, looks things up and shows cards, and presses nothing. Games "
        "whose rules forbid it are always off. Changes apply when Tycho restarts.",
        # Off can only make Tycho do less, so voice may turn it off but never on (#191).
        json_schema_extra=_rule("lower_only", how=FILE_OR_APP),
    )
    keep_transcripts: bool = Field(
        default=False,
        description="Keep a local record of what you said. Off by default.",
        json_schema_extra=_rule("lower_only", how=FILE_OR_APP),
    )
    web_search: bool = Field(
        default=True,
        description="Let Tycho search the web for news, where things are and how to do "
        "things, in games that have web search. With an OpenRouter key, about half a cent a "
        "search through it; otherwise 1 to 3 cents of your Anthropic credit.",
        json_schema_extra=_rule("lower_only", how=FILE_OR_APP),
    )
    web_search_open: bool = Field(
        default=True,
        description="Let web search look on any website (guides, forums, Reddit's search "
        "results), not only the game's own sites, for where things are and how to do things. "
        "Answers from players' posts say so. With only an Anthropic key, it tries the game's "
        "sites first and takes longer (about 10 s). Off: the game's own sites only.",
        json_schema_extra=_rule("lower_only", how=FILE_OR_APP),
    )
    brain_provider: Provider = Field(
        default="anthropic",
        description="Who runs Tycho's brain, for anything the built-in phrasings don't cover: "
        "anthropic (Claude direct, with your Anthropic key) or openrouter (the same Claude "
        "through OpenRouter, with your OpenRouter key). Web search needs an Anthropic key "
        "either way.",
        # It decides who receives what you say: never by a misheard sentence (ADR 5).
        json_schema_extra=_rule("no", how=FILE_OR_APP),
    )
    brain_model: BrainModel = Field(
        default="Claude Haiku 5.5",
        description="The brain's model. Only models that passed Tycho's safety test (no wrong "
        "key presses) can be picked.",
        json_schema_extra=_rule("no", how=FILE_OR_APP),
    )
    brain_seconds: float = Field(
        default=8.0,
        ge=4.0,
        le=15.0,
        description="How long Tycho waits for the brain before saying it couldn't answer, in "
        "seconds. Look-ups and web searches get longer.",
        json_schema_extra=_rule("no", how=FILE_OR_APP),
    )
    call_me: str = Field(
        default=DEFAULT_CALL_ME,
        description="What Tycho calls you: a name, nickname, callsign or rank. Empty means "
        'nothing. Say "call me Ace".',
        json_schema_extra=_rule("yes", "now"),
    )
    call_me_says: str = Field(
        default="",
        description="How Tycho's voice should say it, if it gets it wrong: e.g. Zile for Xyl. "
        "Empty means as written.",
        json_schema_extra=_rule("yes", "now"),
    )
    call_me_how_often: HowOften = Field(
        default="sparingly",
        description="How often Tycho uses it: sparingly (greetings, warnings and "
        "confirmations of big actions) or often.",
        json_schema_extra=_rule("yes", "now"),
    )
    tile: bool = Field(
        default=True,
        description="Show the Tile (on) or hide it (off): the small panel over the game, "
        "for answers better read than heard. Off, Tycho says everything out loud.",
        json_schema_extra=_rule("yes", "now"),
    )
    tile_size: tiles.TileSize = Field(
        default="medium",
        description="How big the Tile's text is. The Tile grows with it.",
        json_schema_extra=_rule("yes", "now"),
    )
    tile_screen: tiles.ScreenName = Field(
        default="game",
        description="Which screen the Tile shows on: game, for the screen the game is on, "
        "or a screen as Windows names it, such as \\\\.\\DISPLAY2 (pick it on the "
        "window's Tile page). If that screen isn't plugged in, the Tile follows the game.",
        json_schema_extra=_rule("no", "now", how=FILE_OR_APP),
    )
    tile_corner: tiles.Corner = Field(
        default="top right",
        description="Which corner of the screen the Tile sits in.",
        json_schema_extra=_rule("yes", "now"),
    )
    tile_nudge_in: float = Field(
        default=0.0,
        ge=0.0,
        le=0.4,
        description="How far in from the side the Tile sits, as a share of the screen's "
        "width, to clear the game's own displays.",
        json_schema_extra=_rule("yes", "now"),
    )
    tile_nudge_down: float = Field(
        default=0.0,
        ge=0.0,
        le=0.6,
        description="How far from the top (or bottom) edge the Tile sits, as a share of the "
        "screen's height.",
        json_schema_extra=_rule("yes", "now"),
    )
    tile_colour: tiles.TileColour = Field(
        default="game",
        description="The Tile's highlight colour: game (the game's own, or in a game where "
        "Tycho knows what you're in, its colour), Tycho's, or one you pick. Warnings keep "
        "their own colours.",
        json_schema_extra=_rule("yes", "now"),
    )
    tile_see_through: float = Field(
        default=0.2,
        ge=0.0,
        le=0.6,
        description="How see-through the Tile's background is. Text never fades.",
        json_schema_extra=_rule("yes", "now"),
    )
    tile_high_contrast: bool = Field(
        default=False,
        description="A solid background and brighter text and lines.",
        json_schema_extra=_rule("yes", "now"),
    )
    tile_cards_at_once: tiles.CardsAtOnce = Field(
        default="one",
        description="One card at a time (the last two shrink to strips to bring back), or two.",
        json_schema_extra=_rule("yes", "now"),
    )
    tile_cards_stay: tiles.CardsStay = Field(
        default="normal",
        description="How long cards stay before they fade.",
        json_schema_extra=_rule("yes", "now"),
    )
    tile_talk_light: bool = Field(
        default=True,
        description="A small light on the Tile while you talk and while Tycho thinks.",
        json_schema_extra=_rule("yes", "now"),
    )
    tile_quiet: bool = Field(
        default=False,
        description="Quiet mode: only questions waiting on you, warnings and notices show.",
        json_schema_extra=_rule("yes", "now"),
    )
    tile_hidden_cards: tiles.CardKinds = Field(
        default="",
        description="Kinds of card not to show while the Tile is on, separated by commas "
        "(to hide the whole Tile, turn tile off), such as list, steps, warning, notice "
        "or long. Tycho still says them. Questions waiting on you always show.",
        json_schema_extra=_rule("yes", "now"),
    )
    callouts: CalloutKinds = Field(
        default="",
        description="Kinds of game event Tycho tells you about without being asked, separated "
        "by commas: only ones the game's log records, in a short fixed line, at most one every "
        '30 seconds, never while you or Tycho are talking. Empty means none. Say "stop the '
        'callouts" to turn them all off.',
        json_schema_extra=_rule("yes", "now"),
    )
    crash_reports: bool | None = Field(
        default=None,
        description="Send a crash report when Tycho crashes: where in Tycho's code it broke, "
        "and the Tycho and Windows versions. Never what was said, keys, names or your files. "
        "Empty means not asked yet, and nothing is sent.",
        json_schema_extra=_rule("lower_only", how=FILE_OR_APP),
    )
    look_at_screen: bool | None = Field(
        default=None,
        description='Let Tycho look at your game window when you ask it to ("take a look"): '
        "one picture of the game window, sent to the brain with your question and never "
        "saved. A camera sound plays each time. Empty means not asked yet, and it's off.",
        json_schema_extra=_rule("lower_only", "now", how=FILE_OR_APP),
    )
    memory: bool = Field(
        default=True,
        description='Let Tycho keep notes you ask it to remember ("remember I like short '
        'answers"), sent to the brain with each request. Nothing is kept unless you ask. Off: '
        "the notes stay on your PC, unused. See them on the Memory page.",
        json_schema_extra=_rule("lower_only", "now", how=FILE_OR_APP),
    )
    keep_history: bool | None = Field(
        default=None,
        description="Keep a history of what you and Tycho said, on this PC only, so you can "
        'ask about it later ("what was yesterday\'s route?"). Kept 30 days; see, search and '
        "delete it on the History page. Empty means not asked yet, and it's off.",
        json_schema_extra=_rule("lower_only", "now", how=FILE_OR_APP),
    )
    check_for_updates: bool = Field(
        default=True,
        description="When Tycho starts, and when its window opens, ask GitHub whether a newer "
        "version is out, and say so. Tycho never updates itself.",
        json_schema_extra=_rule("lower_only", how=FILE_OR_APP),
    )

    @field_validator("game", mode="before")
    @classmethod
    def _renamed_game(cls, value: object) -> object:
        return RENAMED_GAMES.get(value, value) if isinstance(value, str) else value

    @field_validator("tile_colour", mode="before")
    @classmethod
    def _renamed_colour(cls, value: object) -> object:
        """``ship`` was the game's colour's old name (#422): an older settings file keeps it."""
        return "game" if value == "ship" else value

    @field_validator("push_to_talk")
    @classmethod
    def _valid_binding(cls, value: str | None) -> str | None:
        if value is not None:
            Binding.parse(value)  # raises ValueError with a readable message
        return value

    @field_validator("call_me", "call_me_says")
    @classmethod
    def _plain_name(cls, value: str) -> str:
        value = " ".join(value.split())
        if not NAME.fullmatch(value):
            raise ValueError(
                "use up to 40 letters, digits, spaces, apostrophes, hyphens or full stops"
            )
        return value

    @field_validator("voice")
    @classmethod
    def _known_voice(cls, value: str) -> str:
        value = value.strip()
        if not voices.is_voice(value):
            raise ValueError(
                "use George, Sarah, Lily, Brian, The Ship's Mind, or a 20-character "
                "ElevenLabs voice ID"
            )
        return value

    @field_validator("version")
    @classmethod
    def _known_version(cls, value: int) -> int:
        if value > CURRENT_VERSION:
            raise ValueError(
                f"made by a newer Tycho (version {value}); this Tycho reads up to {CURRENT_VERSION}"
            )
        return value

    @model_validator(mode="after")
    def _offered_model(self) -> Settings:
        choose(self.brain_provider, self.brain_model)  # raises ValueError if not offered
        return self

    @property
    def push_to_talk_binding(self) -> Binding | None:
        return Binding.parse(self.push_to_talk) if self.push_to_talk else None


def json_schema() -> dict[str, Any]:
    """The schema the settings screen is built from."""
    return Settings.model_json_schema()


def rule(name: str) -> dict[str, str]:
    """A field's voice rule: ``voice``, ``applies`` and maybe ``how``. Every field has one."""
    # Pydantic types the extras loosely; ``_rule`` makes them, so this is their shape.
    extra = cast("dict[str, str] | None", Settings.model_fields[name].json_schema_extra)
    assert extra is not None  # noqa: S101 - set on every field above; a test checks
    return dict(extra)


def load(path: Path | None = None) -> Settings:
    """Read settings, or the defaults with no file. An unknown setting (a typo, a newer
    Tycho's) is ignored (``unknown_keys``): it mustn't stop this one starting at all."""
    path = path or paths.settings_file()
    data = _read(path)
    if data is None:
        return Settings()
    data = {"version": 1, **data}  # older files had no version
    try:
        return Settings.model_validate(
            {k: v for k, v in data.items() if k in Settings.model_fields}
        )
    except ValidationError as e:
        problems = "; ".join(
            f"{'.'.join(str(p) for p in err['loc']) or 'file'}: {err['msg']}" for err in e.errors()
        )
        raise SettingsError(
            f"{path} has a problem: {problems}. Fix that line, or delete it to use the default."
        ) from e


def unknown_keys(path: Path | None = None) -> tuple[str, ...]:
    """Settings in the file that this Tycho doesn't know, so ``load`` ignores them."""
    try:
        data = _read(path or paths.settings_file())
    except SettingsError:
        return ()
    return tuple(k for k in data or {} if k not in Settings.model_fields)


def _read(path: Path) -> dict[str, Any] | None:
    try:
        return read_toml(path)
    except ValueError as e:
        raise SettingsError(str(e)) from e


def save(
    settings: Settings, path: Path | None = None, keep: Mapping[str, object] | None = None
) -> Path:
    """Write settings atomically. ``keep``: entries this Tycho doesn't know (``unknown_keys``),
    written back as they were, so a newer Tycho sharing the file doesn't lose them."""
    path = path or paths.settings_file()
    text = to_toml(settings)
    if keep:
        kept = ["", "# Not settings this Tycho knows; kept as they were."]
        for name, value in keep.items():
            if not isinstance(value, bool | int | float | str):
                raise SettingsError(
                    f"{name} in {path} isn't a simple value, so Tycho can't rewrite the file "
                    "without losing it. Change the setting in the file instead."
                )
            kept.append(f"{name} = {toml_value(value)}")
        text += "\n".join(kept) + "\n"
    write_atomic(path, text)
    return path


def update(changes: Mapping[str, object], path: Path | None = None) -> Settings:
    """Change some settings in the file; the rest stays as the file has it now, hand edits
    and unknown entries too. ``SettingsError`` if the result isn't valid: nothing written."""
    path = path or paths.settings_file()
    with _WRITING:
        return _update(changes, path)


_WRITING = threading.Lock()
"""``update`` reads, changes and writes the file: two at once would lose one change."""


def _update(changes: Mapping[str, object], path: Path) -> Settings:
    current = load(path)
    try:
        updated = Settings.model_validate({**current.model_dump(), **changes})
    except ValidationError as e:
        raise SettingsError(str(e)) from e
    data = _read(path) or {}
    save(updated, path, keep={k: v for k, v in data.items() if k not in Settings.model_fields})
    return updated
