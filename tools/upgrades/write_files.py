# pyright: basic
# It imports each old version's code, whose names and types differ, so strict checks
# can't follow it.

"""Write a player's files the way one Tycho version writes them, using that version's
own code (#612). Run by ``make_fixtures.py`` with an old version's ``src`` first on
``sys.path``, so it must import only what every version since 0.5.0 has, and try the
rest.

    write_files.py <out folder>
"""

import json
import sys
import tomllib
from pathlib import Path
from typing import Any

STRINGS = {
    "push_to_talk": "mouse:0x06",
    "microphone": "Headset Microphone (USB Audio)",
    "game_root": "D:\\Games\\RSI\\StarCitizen",
    "call_me": "Ace",
    "call_me_says": "Ay-ss",
    "voice": "Sarah",
    "tile_hidden_cards": "",
}


def _resolved(spec: dict[str, Any], defs: dict[str, Any]) -> dict[str, Any]:
    """The schema of a field, with a ``$ref`` (an enum's own type) and an optional
    field's ``anyOf`` taken apart."""
    if "$ref" in spec:
        spec = {**spec, **defs[spec["$ref"].rsplit("/", 1)[-1]]}
    for option in spec.get("anyOf", []):
        if option.get("type") != "null":
            spec = {**spec, **_resolved(option, defs)}
    return spec


def _changed(name: str, spec: dict[str, Any], default: object) -> object:
    """A value other than the default that the schema allows, or ``default``."""
    if name in STRINGS:
        return STRINGS[name]
    options = spec.get("enum") or [o["const"] for o in spec.get("oneOf", []) if "const" in o]
    if options:
        others = [o for o in options if o != default]
        return others[-1] if others else default
    kind = spec.get("type")
    if kind == "boolean" or isinstance(default, bool):
        return not default
    if kind in ("number", "integer") and isinstance(default, int | float):
        low = spec.get("minimum", spec.get("exclusiveMinimum", 0))
        high = spec.get("maximum", spec.get("exclusiveMaximum", float(default) * 2 or 1))
        value = (float(low) + float(high)) / 2
        return round(value) if kind == "integer" else round(value, 2)
    return default


def settings_files(out: Path) -> None:
    from tycho import settings

    default = settings.Settings()
    settings.save(default, out / "settings-default.toml")
    schema = settings.Settings.model_json_schema()
    props = schema["properties"]
    dumped = default.model_dump()
    chosen: dict[str, object] = {}
    for name, spec in props.items():
        if name == "version":
            continue
        value = _changed(name, _resolved(spec, schema.get("$defs", {})), dumped.get(name))
        if value == dumped.get(name):
            continue
        try:  # each value on its own: a pair may need each other (brain provider and model)
            settings.Settings.model_validate({**dumped, **chosen, name: value})
        except Exception:  # noqa: S112 - a value this version refuses: leave the default
            continue
        chosen[name] = value
    custom = settings.Settings.model_validate({**dumped, **chosen})
    settings.save(custom, out / "settings-changed.toml")
    if "game" in props:  # 0.7.0 on: the second game
        wow = settings.Settings.model_validate({**dumped, "game": "wow-forever"})
        settings.save(wow, out / "settings-wow.toml")
    try:  # 0.7.0 on: the brain allowlist
        from tycho.brain import providers
    except ImportError:
        return
    offered = [[p, m] for p, models in providers.OFFERED.items() for m in models]
    for i, (provider, model) in enumerate(offered):
        picked = settings.Settings.model_validate(
            {**dumped, "brain_provider": provider, "brain_model": model}
        )
        settings.save(picked, out / f"settings-brain-{i}.toml")


def _built_in_ids() -> tuple[list[str], Any]:
    from tycho.commands import CommandMatcher

    try:
        from tycho.packs import STAR_CITIZEN, built_in

        pack = built_in(STAR_CITIZEN)
        matcher = CommandMatcher.default(pack=pack)  # type: ignore[call-arg]
        check = lambda p: CommandMatcher.default(p, pack=pack)  # noqa: E731
    except ImportError:
        matcher = CommandMatcher.default()  # pyright: ignore[reportCallIssue] - before 0.7.0, no packs
        check = CommandMatcher.default
    plain = [c.id for c in matcher.commands if not c.is_sequence]
    return plain, check


def commands_file(out: Path) -> None:
    """A player's commands.toml in the documented format: a phrase of their own for
    every built-in command, a command of their own and a sequence of the built-in ones."""
    ids, check = _built_in_ids()
    lines = ["# My own phrases for Tycho.", ""]
    for n, command_id in enumerate(ids):
        lines += ["[[phrases]]", f'command = "{command_id}"', f'say = ["my phrase {_word(n)}"]', ""]
    lines += [
        "[[command]]",
        'id = "my_space_mode"',
        'say = ["my space mode"]',
        'actions = ["v_master_mode_set_scm"]',
        "",
        "[[sequence]]",
        'id = "my_startup"',
        'say = ["my startup"]',
        "steps = [",
        f'    "{ids[0]}",',
        f'    {{ command = "{ids[1]}", wait = 1.0 }},',
        '    "my_space_mode",',
        "]",
        "",
    ]
    path = out / "commands.toml"
    path.write_text("\n".join(lines), "utf-8")
    matcher = check(path)  # this version loads it, with no warnings
    if matcher.warnings:
        raise SystemExit(f"{path}: {matcher.warnings}")
    (out / "commands-ids.json").write_text(json.dumps(ids, indent=1) + "\n", "utf-8")


def _word(n: int) -> str:
    """A number as letters ("ax", "bx", ... "aax"), so every phrase is plain words."""
    letters = "abcdefghijklmnopqrstuvwxyz"
    word = ""
    n += 1
    while n:
        n, r = divmod(n - 1, 26)
        word = letters[r] + word
    return word + "x"


def fleet_file(out: Path) -> None:
    try:
        from tycho.games.star_citizen.fleet import Fleet
    except ImportError:
        from tycho.knowledge.fleet import Fleet  # type: ignore[no-redef]
    ships = ["Cutlass Black", "MISC Prospector", "Drake Corsair", "Anvil Carrack"]
    try:
        fleet = Fleet(out / "fleet.toml", ships, imported=ships[2:])  # type: ignore[call-arg]
    except TypeError:
        fleet = Fleet(out / "fleet.toml", ships)
    fleet.save()
    tomllib.loads((out / "fleet.toml").read_text("utf-8"))


def memory_files(out: Path) -> None:
    """0.11 on: the player's notes, every game's and Star Citizen's (#483)."""
    import datetime

    try:
        from tycho.memory import NoteFile
    except ImportError:
        return
    day = datetime.date(2026, 11, 23)
    every = NoteFile(out / "memory.toml")
    every.add("I play with a HOTAS and pedals", day)
    every.add("Call quantum jumps 'spooling'", day)
    NoteFile(out / "game-memory.toml").add("My home base is Area18", day)


def blueprints_file(out: Path) -> None:
    """0.11 on: the blueprints the player owns (#598)."""
    import datetime

    try:
        from tycho.games.star_citizen.owned import OwnedBlueprints
    except ImportError:
        return
    owned = OwnedBlueprints(
        out / "blueprints.toml", lambda: None, today=lambda: datetime.date(2026, 11, 23)
    )
    for name in ("FS-9 LMG", "Arclight Pistol"):
        owned.add(name)
    owned.remove("Arclight Pistol")  # so the file has a name the player took off


def history_file(out: Path) -> None:
    """0.11 on: one play session's history (#484)."""
    import datetime

    try:
        from tycho.history import Entry, HistoryFolder
    except ImportError:
        return
    at = datetime.datetime(2026, 11, 23, 20, 15, tzinfo=datetime.UTC)
    folder = HistoryFolder(out / "history")
    session = "2026-11-23_20-14-00"
    folder.append(
        session, Entry(at, "lower the landing gear", "Gear down.", ("v_toggle_landing_system",))
    )
    folder.append(
        session, Entry(at, "what's the best mining ship", "The Prospector, for one player.")
    )


def ducked_file(out: Path) -> None:
    """0.11 on: the game's volume while Tycho had it turned down (#570)."""
    try:
        from tycho.voice.ducking import Ducker
    except ImportError:
        return

    class Volume:
        def get(self) -> dict[str, float]:
            return {"StarCitizen.exe": 0.8}

        def set(self, levels: object) -> bool:
            return True

    Ducker(Volume(), out / "ducked.json")._save({"StarCitizen.exe": 0.8})  # type: ignore[arg-type]


def main() -> None:
    out = Path(sys.argv[1])
    out.mkdir(parents=True, exist_ok=True)
    settings_files(out)
    commands_file(out)
    fleet_file(out)
    memory_files(out)
    blueprints_file(out)
    history_file(out)
    ducked_file(out)


if __name__ == "__main__":
    main()
