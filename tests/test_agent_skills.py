"""The skills and plugin that let an AI agent set Tycho up, or write a player's commands:
they must stay true to the real command line and file formats, or the agent goes wrong."""

import json
import re
import tomllib
from pathlib import Path

import pytest

from tycho import app
from tycho.commands import CommandMatcher

ROOT = Path(__file__).resolve().parent.parent
SKILL_FOLDERS = (ROOT / "plugins", ROOT / ".claude" / "skills", ROOT / ".agents" / "skills")
SKILLS = sorted(p for folder in SKILL_FOLDERS for p in folder.rglob("SKILL.md"))
PLAYER_SKILLS = ROOT / "plugins" / "tycho" / "skills"


def _frontmatter(path: Path) -> dict[str, str]:
    text = path.read_text("utf-8")
    match = re.match(r"---\n(.*?)\n---\n", text, re.DOTALL)
    assert match, f"{path} has no frontmatter"
    fields: dict[str, str] = {}
    for line in match.group(1).splitlines():
        name, _, value = line.partition(":")
        fields[name.strip()] = value.strip()
    return fields


def test_there_are_skills_to_check() -> None:
    assert (PLAYER_SKILLS / "tycho-setup" / "SKILL.md") in SKILLS
    assert (PLAYER_SKILLS / "tycho-commands" / "SKILL.md") in SKILLS


@pytest.mark.parametrize("path", SKILLS, ids=lambda p: p.parent.name)
def test_each_skill_is_named_for_its_folder_and_says_when_to_use_it(path: Path) -> None:
    # The Agent Skills format: a lower-case, hyphenated name, and a description of at
    # most 1,024 characters that says when to use the skill.
    fields = _frontmatter(path)
    assert fields.get("name") == path.parent.name
    assert re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)*", fields["name"])
    assert 0 < len(fields.get("description", "")) <= 1024
    assert "Use when" in fields["description"]


def test_the_marketplace_lists_the_plugin_and_its_skills() -> None:
    marketplace = json.loads((ROOT / ".claude-plugin" / "marketplace.json").read_text("utf-8"))
    assert marketplace["name"]
    assert marketplace["owner"]["name"]
    for entry in marketplace["plugins"]:
        folder = (ROOT / entry["source"]).resolve()
        manifest = json.loads((folder / ".claude-plugin" / "plugin.json").read_text("utf-8"))
        assert manifest["name"] == entry["name"]
        assert list((folder / "skills").glob("*/SKILL.md"))


def _commands(text: str) -> list[list[str]]:
    """Every `tycho ...` the text tells an agent to run, as arguments."""
    found: list[list[str]] = []
    patterns = (
        r'`"\$tycho`" ([^`"]+)`?',  # opened in a window: "`"$tycho`" key set anthropic`"
        r"& \$tycho ([^\n#]+)",  # run directly: & $tycho check --quick --json
        r"(?m)^tycho ([a-z][^\n#]*)$",  # in a code block: tycho actions master mode scm
        r"`tycho ([a-z][^`]*)`",  # in prose: `tycho gpu install`
    )
    for pattern in patterns:
        for match in re.finditer(pattern, text):
            arguments = match.group(1).split()
            if arguments and not any("<" in a or "..." in a for a in arguments):
                found.append(arguments)
    return found


@pytest.mark.parametrize("path", SKILLS, ids=lambda p: p.parent.name)
def test_every_tycho_command_a_skill_gives_is_real(path: Path) -> None:
    parser = app.build_parser()
    commands = _commands(path.read_text("utf-8"))
    for arguments in commands:
        try:
            parser.parse_args(arguments)
        except SystemExit as e:
            if e.code != 0:  # --version and --help exit 0
                pytest.fail(f"{path.parent.name} says to run `tycho {' '.join(arguments)}`")


def test_the_setup_skill_gives_commands_to_run() -> None:
    commands = _commands((PLAYER_SKILLS / "tycho-setup" / "SKILL.md").read_text("utf-8"))
    for expected in (["check", "--quick", "--json"], ["bind"], ["model", "download"], ["run"]):
        assert expected in commands


def test_the_commands_skill_examples_load(tmp_path: Path) -> None:
    text = (PLAYER_SKILLS / "tycho-commands" / "SKILL.md").read_text("utf-8")
    blocks = re.findall(r"```toml\n(.*?)```", text, re.DOTALL)
    commands = [b for b in blocks if "[[command]]" in b or "[[sequence]]" in b]
    assert len(commands) == 2
    file = tmp_path / "commands.toml"
    file.write_text("\n".join(commands), "utf-8")
    matcher = CommandMatcher.default(file)
    assert matcher.get("space_mode") is not None
    sequence = matcher.get("full_startup")
    assert sequence is not None
    assert [s.wait_seconds for s in sequence.steps] == [0.0, 1.0, 0.0]
    fleet = next(b for b in blocks if "ships = [" in b)
    assert tomllib.loads(fleet)["ships"] == ["Cutlass Black", "MISC Prospector"]


def test_contributor_skills_are_the_same_for_claude_code_and_codex() -> None:
    # Claude Code reads .claude/skills and Codex reads .agents/skills: one copy each,
    # kept identical. Edit one, then copy it over.
    claude = {
        p.relative_to(ROOT / ".claude" / "skills")
        for p in (ROOT / ".claude" / "skills").rglob("*")
        if p.is_file()
    }
    codex = {
        p.relative_to(ROOT / ".agents" / "skills")
        for p in (ROOT / ".agents" / "skills").rglob("*")
        if p.is_file()
    }
    assert claude == codex
    assert claude
    for path in claude:
        assert (ROOT / ".claude" / "skills" / path).read_bytes() == (
            ROOT / ".agents" / "skills" / path
        ).read_bytes(), f"{path} differs between .claude/skills and .agents/skills"
