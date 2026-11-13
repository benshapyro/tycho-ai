"""Tycho's settings: one typed, validated schema, saved as TOML (ADR 5, ADR 11).

The schema is the single source of truth. The settings screen will be generated
from ``json_schema()``, voice commands edit it (``spoken_settings``), and power users
can edit the file by hand. Descriptions are written for players, since they become UI
text.

Each field also says whether it can change by voice (``voice``), when a change takes
effect (``applies``), and, if not by voice, how to change it (``how``). A misheard
sentence must never weaken a safeguard or your privacy (ADR 5), so some settings can
only go one way by voice: ``lower_only`` means lower, or off.
"""

import os
import re
import tempfile
import threading
import tomllib
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Literal, cast

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from tycho import paths
from tycho.brain.providers import BrainModel, Provider, choose
from tycho.game.install import Channel
from tycho.ptt.binding import Binding
from tycho.tile.cards import ALL_KINDS
from tycho.voice import voices

CURRENT_VERSION = 1

type Game = Literal["star-citizen", "world-of-warcraft"]
"""The game packs that ship inside Tycho (ADR 18); a test keeps it the same as
``packs.installed()``."""
type Device = Literal["auto", "gpu", "cpu"]
type VoiceRule = Literal["yes", "lower_only", "no"]
type Applies = Literal["now", "restart"]
type HowOften = Literal["sparingly", "often"]
type TileSize = Literal["small", "medium", "large", "extra large"]
type Corner = Literal["top right", "top left", "bottom right", "bottom left"]
type TileColour = Literal[
    "ship", "tycho", "orange", "amber", "green", "teal", "blue", "white", "red", "pink"
]
type CardsAtOnce = Literal["one", "two"]
type CardsStay = Literal["shorter", "normal", "longer"]
type VoiceModel = Literal["the voice's own", "v4 turbo", "flash"]


def _rule(voice: VoiceRule, applies: Applies = "restart", how: str = "") -> dict[str, Any]:
    """A field's voice rule, kept in the schema so the settings screen can show it too."""
    return {"voice": voice, "applies": applies, **({"how": how} if how else {})}


FILE_OR_APP = "in Tycho's window (Settings) or in settings.toml"
FILE_FOR_NOW = "in settings.toml for now; Tycho's setup will change it in the window"

NAME = re.compile(r"[\w .'-]{0,40}")
"""What Tycho may call the player: letters, digits, spaces, apostrophes, hyphens and
full stops, up to 40. It goes into every request to the brain, so nothing else."""


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
        description="The Star Citizen folder that contains LIVE. Empty means Tycho finds it "
        "from the RSI Launcher, the default folder, or common folders on each drive.",
        json_schema_extra=_rule("no", how=FILE_FOR_NOW),
    )
    game_channel: Channel = Field(
        default="LIVE",
        description="Which version of the game you play: LIVE, PTU, EPTU, TECH-PREVIEW or "
        "HOTFIX. Tycho reads that version's keybinds.",
        json_schema_extra=_rule("yes"),
    )
    transcription_device: Device = Field(
        default="auto",
        description="Where speech recognition runs. Auto uses the graphics card when it has "
        "room, otherwise the processor.",
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
    voice: str = Field(
        default="George",
        description="Tycho's ElevenLabs voice: George, Sarah, Lily, Brian, or a voice ID "
        "from your ElevenLabs account. A voice you designed works only with your own key. "
        "If your key can't use it, Tycho speaks as George and says so.",
        json_schema_extra=_rule("no", how=FILE_OR_APP),
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
        description="Let Tycho search Star Citizen sites for news, upcoming patches and new "
        "ships. Each search costs about 1 cent of your Anthropic credit.",
        json_schema_extra=_rule("lower_only", how=FILE_OR_APP),
    )
    web_search_open: bool = Field(
        default=True,
        description="When the Star Citizen sites have no clear answer, let web search look "
        "on any website (guides, forums, Reddit's search results) and read a page, for where "
        "things are and how to do things. Answers from players' posts say so. That search "
        "takes longer (about 11 s, at most 25 s) and costs about 3 cents. Off: the Star "
        "Citizen sites only.",
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
        default="Commander",
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
    tile_size: TileSize = Field(
        default="medium",
        description="How big the Tile's text is. The Tile grows with it.",
        json_schema_extra=_rule("yes", "now"),
    )
    tile_corner: Corner = Field(
        default="top right",
        description="Which corner of the screen the Tile sits in.",
        json_schema_extra=_rule("yes", "now"),
    )
    tile_nudge_in: float = Field(
        default=0.0,
        ge=0.0,
        le=0.4,
        description="How far in from the side the Tile sits, as a share of the screen's "
        "width, to clear a ship's own display.",
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
    tile_colour: TileColour = Field(
        default="ship",
        description="The Tile's highlight colour: the ship's own (from the one you said "
        "you're flying), Tycho's, or one you pick. Warnings keep their own colours.",
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
    tile_cards_at_once: CardsAtOnce = Field(
        default="one",
        description="One card at a time (a kept card shrinks to a strip), or two.",
        json_schema_extra=_rule("yes", "now"),
    )
    tile_cards_stay: CardsStay = Field(
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
    tile_hidden_cards: str = Field(
        default="",
        description="Kinds of card not to show while the Tile is on, separated by commas "
        "(to hide the whole Tile, turn tile off): list, route, recipe, "
        "ship, fleet, steps, headlines, warning, notice, long. Tycho still says them. "
        "Questions waiting on you always show.",
        json_schema_extra=_rule("yes", "now"),
    )
    check_for_updates: bool = Field(
        default=True,
        description="When Tycho starts, ask GitHub whether a newer version is out, and say so. "
        "Tycho never updates itself.",
        json_schema_extra=_rule("lower_only", how=FILE_OR_APP),
    )

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

    @field_validator("tile_hidden_cards")
    @classmethod
    def _card_kinds(cls, value: str) -> str:
        kinds = [k.strip().lower() for k in value.replace(";", ",").split(",") if k.strip()]
        if unknown := [k for k in kinds if k not in ALL_KINDS]:
            raise ValueError(f"no card is called {', '.join(unknown)}")
        return ", ".join(dict.fromkeys(k for k in kinds if k != "confirm"))

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
    """Read settings, or the defaults if there's no file yet.

    A setting this Tycho doesn't know is ignored (``unknown_keys`` names it): it may
    be a typo, or a setting from a newer Tycho sharing the file, and refusing it
    would stop this one starting at all.
    """
    path = path or paths.settings_file()
    data = _read(path)
    if data is None:
        return Settings()
    data = _migrate(data)
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
    if not path.exists():
        return None
    try:
        # utf-8-sig: Notepad and PowerShell can save a byte-order mark, which TOML rejects.
        return tomllib.loads(path.read_text("utf-8-sig"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as e:
        raise SettingsError(f"Couldn't read {path}: {e}") from e


def save(
    settings: Settings, path: Path | None = None, keep: Mapping[str, object] | None = None
) -> Path:
    """Write settings atomically, so an interrupted save never leaves a broken file.

    ``keep`` holds entries this Tycho doesn't know (``unknown_keys``), written back as
    they were: a newer Tycho sharing the file mustn't lose them to an older one's save.
    """
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
            kept.append(f"{name} = {_toml_value(value)}")
        text += "\n".join(kept) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temp = tempfile.mkstemp(dir=path.parent, prefix=".settings.", suffix=".tmp")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(temp, path)
    except BaseException:
        Path(temp).unlink(missing_ok=True)
        raise
    return path


def update(changes: Mapping[str, object], path: Path | None = None) -> Settings:
    """Change some settings in the file and save it: everything else stays as it is now
    in the file, including hand edits since Tycho started and entries it doesn't know.
    Raises ``SettingsError`` if the result isn't valid; nothing is written then."""
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


def to_toml(settings: Settings) -> str:
    """The settings as TOML, each value with its description as a comment. Unset values omitted."""
    lines = ["# Tycho settings. Edit by hand, by voice, or in the settings screen.", ""]
    fields = type(settings).model_fields
    for name, value in settings.model_dump().items():
        if value is None:
            lines.append(f"# {name} = (not set). {fields[name].description}")
            continue
        lines += [f"# {fields[name].description}", f"{name} = {_toml_value(value)}", ""]
    return "\n".join(lines).rstrip() + "\n"


def _toml_value(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int | float):
        return repr(value)
    text = str(value).replace("\\", "\\\\").replace('"', '\\"')
    return f'"{text}"'


def _migrate(data: dict[str, Any]) -> dict[str, Any]:
    """Bring older settings files up to the current version."""
    data = dict(data)
    data.setdefault("version", 1)
    return data
