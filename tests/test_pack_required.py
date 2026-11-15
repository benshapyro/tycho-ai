"""A pack is always given, and nothing loads one when it's imported (ADR 19, #395).

Before, "no pack given" meant Star Citizen in 13 files, the safety gate among them, and
its lists include ones that loosen a check (ADR 18). A forgotten pack is now a missing
argument, never another game's lists.
"""

import annotationlib
import inspect
import subprocess
import sys
from collections.abc import Callable
from typing import Any

import pytest

from tycho import brain_choice, paths, safety
from tycho.brain import briefing
from tycho.brain.claude import ClaudeBrain
from tycho.brain.decisions import commands_for
from tycho.brain.openrouter import OpenRouterBrain
from tycho.commands import CommandMatcher
from tycho.commands.reserved import reserved_for
from tycho.desktop.commands import Commands
from tycho.input import Presser
from tycho.knowledge.web import WebSearch
from tycho.lexicon import Lexicon
from tycho.packs import built_in
from tycho.runtime import Session
from tycho.runtime.planning import plan_request
from tycho.runtime.routing import from_brain
from tycho.tile.design import accent_for, accent_for_maker

# Each function, and the argument that names the game (or its lists, its words...).
REQUIRED: list[tuple[Callable[..., Any], str]] = [
    (safety.check, "lists"),
    (safety.is_travel, "lists"),
    (safety.is_confirm_grade, "lists"),
    (safety.shares_key_with_danger, "lists"),
    (safety.key_clash, "lists"),
    (safety.switched_off, "lists"),
    (safety.known_facts, "lists"),
    (plan_request, "lists"),
    (from_brain, "lists"),
    (commands_for, "lists"),
    (Session.__init__, "safety_lists"),
    (CommandMatcher.default, "pack"),
    (Lexicon.default, "pack"),
    (reserved_for, "terms"),
    (briefing.instructions, "pack"),
    (briefing.instructions_hash, "pack"),
    (briefing.briefed, "pack"),
    (briefing.bound_actions, "pack"),
    (briefing.offered_actions, "pack"),
    (briefing.game_facts, "pack"),
    (briefing.briefing, "pack"),
    (ClaudeBrain.__init__, "pack"),
    (OpenRouterBrain.__init__, "pack"),
    (brain_choice.load_brain, "pack"),
    (brain_choice.make_brain, "pack"),
    (Commands.__init__, "pack"),
    (Presser.__init__, "game"),
    (WebSearch.__init__, "sites"),
    (accent_for, "tile"),
    (accent_for_maker, "tile"),
    (built_in.__wrapped__, "pack_id"),
    (paths.pack_dir, "pack"),
    (paths.commands_file, "pack"),
    (paths.fleet_file, "pack"),
]


@pytest.mark.parametrize(
    ("function", "argument"), REQUIRED, ids=[f"{f.__qualname__}-{a}" for f, a in REQUIRED]
)
def test_the_game_is_a_required_argument(function: Callable[..., Any], argument: str) -> None:
    signature = inspect.signature(function, annotation_format=annotationlib.Format.FORWARDREF)
    parameter = signature.parameters[argument]
    assert parameter.default is inspect.Parameter.empty, f"{argument} has a default"


def test_no_module_loads_a_pack_when_imported() -> None:
    # A fresh interpreter: this one has loaded packs already. Every module is imported;
    # the ones that need Windows are skipped off it.
    script = """
import importlib, pkgutil, tycho
from tycho.packs import pack
for module in pkgutil.walk_packages(tycho.__path__, "tycho."):
    try:
        importlib.import_module(module.name)
    except (ImportError, AttributeError, OSError):
        pass
print(pack.built_in.cache_info().currsize)
"""
    done = subprocess.run(  # noqa: S603 - this interpreter, running the script above
        [sys.executable, "-c", script], capture_output=True, text=True, check=True
    )
    assert done.stdout.strip() == "0", done.stderr


def test_the_session_s_own_words_are_heard_with_the_game_s_words() -> None:
    # #405: a phrase is compared as that game's session hears it, after its own
    # lexicon, and one game's words never stand in for another's.
    assert reserved_for("konfirmed", ("Confirmed",)) == "question"
    assert reserved_for("konfirmed", built_in("star-citizen").terms) is None
    assert reserved_for("konfirmed", ("Confirmed",)) == "question"
