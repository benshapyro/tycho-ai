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
import tempfile
import tomllib
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Literal, cast

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from tycho import paths
from tycho.game.install import Channel
from tycho.ptt.binding import Binding

CURRENT_VERSION = 1

type Device = Literal["auto", "gpu", "cpu"]
type VoiceRule = Literal["yes", "lower_only", "no"]
type Applies = Literal["now", "restart"]


def _rule(voice: VoiceRule, applies: Applies = "restart", how: str = "") -> dict[str, Any]:
    """A field's voice rule, kept in the schema so the settings screen can show it too."""
    return {"voice": voice, "applies": applies, **({"how": how} if how else {})}


FILE_OR_APP = "in settings.toml (or the settings screen, when there is one)"


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
        json_schema_extra=_rule("no", how=FILE_OR_APP),
    )
    game_root: Path | None = Field(
        default=None,
        description="The Star Citizen folder that contains LIVE. Empty means Tycho finds it "
        "from the RSI Launcher, the default folder, or common folders on each drive.",
        json_schema_extra=_rule("no", how=FILE_OR_APP),
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
    confirm_seconds: float = Field(
        default=5.0,
        ge=2.0,
        le=15.0,
        description='How long you have to say "confirm" after Tycho asks, in seconds.',
        # Longer means a stray "confirm" later still counts (the author's call, 2026-11-05).
        json_schema_extra=_rule("lower_only", "now", how=FILE_OR_APP),
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

    @field_validator("version")
    @classmethod
    def _known_version(cls, value: int) -> int:
        if value > CURRENT_VERSION:
            raise ValueError(
                f"made by a newer Tycho (version {value}); this Tycho reads up to {CURRENT_VERSION}"
            )
        return value

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
