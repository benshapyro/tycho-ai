"""The tools Claude direct is sent stay under Anthropic's strict-grammar limit.

Anthropic compiles every ``strict`` tool into one grammar and refuses the whole request
when it's too large: "The compiled grammar is too large, which would cause performance
issues." The blueprint tools (#599, #607) made 18 strict tools and every request failed,
so the brain didn't start (#638), and no test saw it: the fake client takes anything. These
build the tools as ``tycho run`` does, with every look-up Star Citizen names, and check
what's strict and how big it is, with no network and no key."""

import asyncio
import json
from collections.abc import Iterator
from typing import Any, cast

import pytest

from tycho import brain_choice
from tycho.brain.claude import STRICT, ClaudeBrain
from tycho.brain.lookups import Lookup
from tycho.brain.tools import NAMES_BUDGET, names_listed, tool_specs
from tycho.commands import CommandMatcher
from tycho.games import BUILT_IN
from tycho.packs import STAR_CITIZEN, Workshop, built_in
from tycho.settings import Settings
from tycho.tile.cards import show_card

SC = built_in(STAR_CITIZEN)

STRICT_BUDGET = 3_000
"""The most JSON the strict tools' schemas may add up to, in characters. Measured on
24 Nov 2026 through Anthropic, sending this list with more or fewer tools strict: about
6,250 characters of strict schemas worked and about 6,600 failed. This leaves room for
a player's own sequences, which go in ``run_sequence``'s list of names."""


@pytest.fixture
def lookups() -> Iterator[list[Lookup]]:
    """Every look-up Star Citizen's brain gets, web search included, as ``load_brain``
    builds them."""
    workshop = Workshop(SC, Settings(), search_key=lambda: "sk-ant-test")
    order = brain_choice.lookup_order(SC.parts.lookups)
    built = [lookup for name in order for lookup in BUILT_IN.lookups[name](workshop).lookups]
    yield built
    for lookup in built:
        if (close := getattr(lookup, "close", None)) is not None:
            asyncio.run(close())


def _sent(lookups: list[Lookup]) -> list[dict[str, Any]]:
    """The tools as the first request sends them, the Tile's card included."""
    brain = ClaudeBrain(
        CommandMatcher.default(pack=SC),
        client=cast(Any, object()),
        lookups=lookups,
        cards=lambda: True,
        pack=SC,
    )
    return [*cast(list[dict[str, Any]], brain._tools), dict(brain._show_card)]  # pyright: ignore[reportPrivateUsage]


def test_only_the_acts_are_strict(lookups: list[Lookup]) -> None:
    tools = _sent(lookups)
    assert len(tools) > 10  # every look-up is there, so the test means something
    assert {t["name"] for t in tools if t["strict"]} == STRICT
    assert {"run_actions", "run_sequence", "show_card"} == STRICT  # press safety, ADR 16


def test_the_strict_tools_fit_anthropic_s_grammar_limit(lookups: list[Lookup]) -> None:
    strict = [t for t in _sent(lookups) if t["strict"]]
    size = sum(len(json.dumps(t["input_schema"])) for t in strict)
    assert size <= STRICT_BUDGET, (
        f"strict schemas are {size} characters; Anthropic refused about 6,600. "
        "Make a look-up non-strict, or simplify a schema, before adding more."
    )


def test_every_tool_still_says_strict_before_its_schema(lookups: list[Lookup]) -> None:
    """#576: the cache is the tools as sent, keys in order, ``strict`` true or false."""
    for tool in _sent(lookups):
        assert list(tool) == ["name", "description", "strict", "input_schema"]


def _strict_size(sequences: list[str], lookups: list[Lookup]) -> int:
    """The strict schemas' size, as ``ClaudeBrain`` marks them (``STRICT``)."""
    tools: list[dict[str, Any]] = [*tool_specs(sequences, lookups), show_card(SC.tile.cards)]
    return sum(len(json.dumps(t["input_schema"])) for t in tools if t["name"] in STRICT)


def _sequence_tool(sequences: list[str], lookups: list[Lookup]) -> dict[str, Any]:
    (tool,) = [t for t in tool_specs(sequences, lookups) if t["name"] == "run_sequence"]
    return cast(dict[str, Any], tool)


@pytest.mark.parametrize("times", [1, 10, 60])
def test_a_player_s_many_sequences_still_fit(lookups: list[Lookup], times: int) -> None:
    # #640: every name went in run_sequence's enum, so a long commands.toml could
    # bring #638 back for that player: every brain request refused.
    name = "my_own_sequence_number_0000"
    count = times * (NAMES_BUDGET // (len(name) + 4) + 1)  # past the budget, #702
    names = [f"{name[:-4]}{n:04}" for n in range(count)]
    assert _strict_size(names, lookups) <= STRICT_BUDGET
    assert "run_sequence" in STRICT  # still strict: "say" is always there
    tool = _sequence_tool(names, lookups)
    assert not names_listed(names)
    assert "enum" not in tool["input_schema"]["properties"]["name"]


def test_a_few_sequences_are_still_listed_by_name(lookups: list[Lookup]) -> None:
    names = ["dock_and_power", "emergency_exit", "my_own_one"]
    assert _sequence_tool(names, lookups)["input_schema"]["properties"]["name"]["enum"] == names
