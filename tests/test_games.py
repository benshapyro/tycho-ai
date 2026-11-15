"""The registry of built-in parts builds them (#396, ADR 19 step 5)."""

import asyncio

import pytest

from tycho import brain_choice
from tycho.games import BUILT_IN, SETTINGS
from tycho.packs import STAR_CITIZEN, Built, Workshop, built_in
from tycho.settings import Settings

SC = built_in(STAR_CITIZEN)


def test_every_look_up_a_built_in_pack_names_has_a_builder() -> None:
    for pack_id in (STAR_CITIZEN, "wow-forever"):
        assert set(built_in(pack_id).parts.lookups) <= set(BUILT_IN.lookups)
    assert SC.parts.game_state in BUILT_IN.game_state


@pytest.mark.parametrize(
    ("named", "built"),
    [
        (["ships", "settings", "web"], ["ships", "settings", "web"]),
        (["ships", "web"], ["ships", SETTINGS, "web"]),
        (["items"], ["items", SETTINGS]),
        ([], [SETTINGS]),
        (["web", "items", "web"], [SETTINGS, "web", "items"]),
    ],
)
def test_the_look_ups_are_built_in_the_pack_s_order_with_settings_always(
    named: list[str], built: list[str]
) -> None:
    assert brain_choice.lookup_order(named) == built


def test_ships_and_the_fleet_share_one_ship_library() -> None:
    workshop = Workshop(SC, Settings())
    ships = BUILT_IN.lookups["ships"](workshop)
    fleet = BUILT_IN.lookups["fleet"](workshop)
    assert ships.lookups
    assert fleet.lookups
    assert workshop.shared("ship_lookup", object) is ships.lookups[0]
    assert fleet.about is not None
    assert fleet.accent is not None
    for part in (ships, fleet):
        for lookup in part.lookups:
            close = getattr(lookup, "close", None)
            if close is not None:
                asyncio.run(close())


def test_web_search_off_builds_nothing() -> None:
    off = Workshop(SC, Settings(web_search=False), search_key=lambda: "never read")
    assert BUILT_IN.lookups["web"](off) == Built()


def test_a_test_s_fake_part_is_known_but_builds_nothing() -> None:
    registry = BUILT_IN.with_parts("lookups", "fake")
    assert "fake" in registry.known("lookups")
    with pytest.raises(NotImplementedError):
        registry.lookups["fake"](Workshop(SC, Settings()))


def test_one_star_citizen_module_loads_only_what_it_needs() -> None:
    # #411: the package re-exported its look-ups, so settings, which needs one type,
    # loaded the ship data, UEX and the Wiki with it.
    import subprocess
    import sys

    script = (
        "import sys, tycho.settings, tycho.games.star_citizen.install\n"
        "print(sorted(m for m in sys.modules if m.startswith('tycho.games.star_citizen.')))"
    )
    done = subprocess.run(  # noqa: S603 - this interpreter, running the script above
        [sys.executable, "-c", script], capture_output=True, text=True, check=True
    )
    assert done.stdout.strip() == "['tycho.games.star_citizen.install']", done.stderr
