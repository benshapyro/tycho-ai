"""Tycho's settings: one typed, validated schema, saved as TOML (ADR 5, ADR 11).

The schema is the single source of truth. The settings screen will be generated
from ``json_schema()``, voice commands will edit it, and power users can edit the
file by hand. Descriptions are written for players, since they become UI text.
"""

import os
import tempfile
import tomllib
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from tycho import paths
from tycho.ptt.binding import Binding

CURRENT_VERSION = 1

type Device = Literal["auto", "gpu", "cpu"]


class SettingsError(Exception):
    """The settings file couldn't be read, with a message a player can act on."""


class Settings(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    version: int = Field(default=CURRENT_VERSION, description="Settings file format version.")
    push_to_talk: str | None = Field(
        default=None,
        description="The button you hold while talking to Tycho, e.g. mouse:0x06 for a mouse "
        "side button. Set it by pressing the button in setup.",
    )
    microphone: str | None = Field(
        default=None, description="Microphone name or number. Empty means the Windows default."
    )
    game_root: Path | None = Field(
        default=None,
        description="The Star Citizen folder that contains LIVE, if it isn't in the default place.",
    )
    transcription_device: Device = Field(
        default="auto",
        description="Where speech recognition runs. Auto uses the graphics card when it has "
        "room, otherwise the processor.",
    )
    feedback_volume: float = Field(
        default=0.5, ge=0.0, le=1.0, description="Volume of Tycho's done and not-done sounds."
    )
    voice_volume: float = Field(
        default=0.8, ge=0.0, le=1.0, description="Volume of Tycho's voice. Zero turns it off."
    )
    confirm_seconds: float = Field(
        default=5.0,
        ge=2.0,
        le=15.0,
        description='How long you have to say "confirm" after Tycho asks, in seconds.',
    )
    keep_transcripts: bool = Field(
        default=False, description="Keep a local record of what you said. Off by default."
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


def load(path: Path | None = None) -> Settings:
    """Read settings, or the defaults if there's no file yet."""
    path = path or paths.settings_file()
    if not path.exists():
        return Settings()
    try:
        # utf-8-sig: Notepad and PowerShell can save a byte-order mark, which TOML rejects.
        data = tomllib.loads(path.read_text("utf-8-sig"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as e:
        raise SettingsError(f"Couldn't read {path}: {e}") from e
    data = _migrate(data)
    try:
        return Settings.model_validate(data)
    except ValidationError as e:
        problems = "; ".join(
            f"{'.'.join(str(p) for p in err['loc']) or 'file'}: {err['msg']}" for err in e.errors()
        )
        raise SettingsError(f"{path} has a problem: {problems}") from e


def save(settings: Settings, path: Path | None = None) -> Path:
    """Write settings atomically, so an interrupted save never leaves a broken file."""
    path = path or paths.settings_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temp = tempfile.mkstemp(dir=path.parent, prefix=".settings.", suffix=".tmp")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as f:
            f.write(to_toml(settings))
        os.replace(temp, path)
    except BaseException:
        Path(temp).unlink(missing_ok=True)
        raise
    return path


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
