"""Test TypeSafe's Jev as a fast router and single-action picker, on the typed case file.

One request per case asks, in parallel: which mode, which category of controls,
which action within every category, whether it's more than one request, and
whether it explicitly asks for each dangerous action. Tycho would then act on
Jev's answer only when it's confident, and hand everything else to Claude.

Raw answers are logged, so thresholds can be swept without new requests.
The API key comes from Windows Credential Manager:
    uv run python -m tycho.credentials set typesafe

Usage: uv run python tools/bench/jev_report.py tools/spike/brain_cases.toml [--runs 2]
"""

import argparse
import json
import statistics
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx2
from brain_report import GLOBAL_NEVER, Call, grade, load_cases

from tycho import credentials
from tycho.game.install import GameInstall
from tycho.game.keybinds import Action
from tycho.game.store import KeybindStore
from tycho.lexicon import Lexicon

ENDPOINT = "https://api.typesafe.ai/v1/systemone"
MODEL = "jev-1.14.0"

# The game's control groups (actionmaps), merged into categories a player would recognise.
CATEGORIES: dict[str, tuple[str, tuple[str, ...]]] = {
    "flight_and_ship": (
        "Flying and the ship itself: movement, throttle, landing gear, quantum, doors, "
        "lights, seat, docking, requesting landing",
        (
            *("spaceship_movement", "spaceship_quantum", "spaceship_docking"),
            *("spaceship_general", "seat_general", "vehicle_general", "lights_controller"),
            *("vehicle_driver", "default"),
        ),
    ),
    "power": (
        "Power: turning ship systems on or off, and moving power between engines, "
        "shields and weapons",
        ("spaceship_power",),
    ),
    "weapons_and_defense": (
        "Guns, missiles, gimbals, countermeasures (decoys, noise), turrets",
        (
            *("spaceship_weapons", "spaceship_missiles", "spaceship_defensive"),
            *("turret_movement", "turret_advanced"),
        ),
    ),
    "targeting_and_comms": (
        "Selecting, cycling, pinning or unlocking targets, hailing, radar ping, scanning",
        (
            *("spaceship_targeting", "spaceship_targeting_advanced"),
            *("spaceship_target_hailing", "spaceship_radar", "spaceship_scanning"),
        ),
    ),
    "mining_salvage_tractor": (
        "Mining lasers, salvage heads and tractor beams",
        ("spaceship_mining", "spaceship_salvage", "tractor_beam"),
    ),
    "cockpit_screens": (
        "mobiGlas, star map, cockpit screens (MFDs), look behind, invites and notifications",
        ("spaceship_hud", "vehicle_mfd", "spaceship_view", "ui_notification"),
    ),
    "on_foot": (
        "The character on foot or in EVA: walking, weapons in hand, helmet, flashlight, "
        "inventory, emotes, respawning",
        (
            *("player", "prone", "player_choice", "zero_gravity_eva"),
            *("zero_gravity_traversal", "incapacitated", "player_emotes"),
        ),
    ),
    "cameras_and_vr": (
        "Camera views, spectator and director modes, head tracking, VR",
        ("view_director_mode", "spectator", "player_input_optical_tracking"),
    ),
}

MODES = {
    "command": "Wants a game control pressed now",
    "lookup": "Asks for live game information: prices, locations, routes, distances, ship or "
    "item details",
    "chat": "Talks to the assistant without asking for an action or information",
    "planning": "Wants help planning a multi-step task, like a trade run or loadout",
    "settings": "Wants to change the assistant itself: its voice, microphone, how much it talks",
    "unclear": "Asks the assistant to do something, but it's unclear what",
    "not_for_assistant": "Not addressed to the assistant: talking to someone else, or to "
    "themselves",
}

DANGER = {
    "v_eject": "Does the pilot explicitly order an ejection from the ship right now?",
    "v_self_destruct": "Does the pilot explicitly order the ship to self-destruct right now?",
    "v_toggle_qdrive_engagement": "Does the pilot explicitly order the quantum drive to "
    "engage (jump) right now?",
    "v_jettison_volatile_cargo": "Does the pilot explicitly order the cargo to be jettisoned?",
}

KIND_FOR_MODE = {
    "command": "actions",
    "lookup": "look_up",
    "chat": "reply",
    "unclear": "reply",
    "not_for_assistant": "reply",
}


@dataclass
class Thresholds:
    mode: float = 0.7
    category: float = 0.7
    action: float = 0.7
    multi: float = 0.3
    danger: float = 0.9


@dataclass
class Result:
    case: str
    run: int
    ms: float
    answers: dict[str, Any] = field(default_factory=dict[str, Any])
    error: str = ""


def build_questions(actions: Sequence[Action]) -> tuple[dict[str, Any], dict[str, str]]:
    """The Jev questions, and which category each action belongs to."""
    by_map = {m: cat for cat, (_, maps) in CATEGORIES.items() for m in maps}
    per_category: dict[str, dict[str, str]] = {cat: {} for cat in CATEGORIES}
    home: dict[str, str] = {}
    for action in actions:
        category = by_map.get(action.actionmap)
        if category is None or action.name in home:
            continue
        per_category[category][action.name] = action.label
        home[action.name] = category

    questions: dict[str, Any] = {
        "mode": {
            "type": "choice",
            "instructions": "What does the pilot want from their ship's AI co-pilot?",
            "criteria": MODES,
        },
        "category": {
            "type": "choice",
            "instructions": "Which group of game controls is the pilot asking about?",
            "criteria": {cat: desc for cat, (desc, _) in CATEGORIES.items()}
            | {"none": "No game control is requested"},
        },
        "multi_action": {
            "type": "noul",
            "instructions": "Does the pilot ask for more than one distinct control action?",
        },
    }
    for category, options in per_category.items():
        questions[f"act_{category}"] = {
            "type": "choice",
            "instructions": "Which single game control does the pilot want pressed?",
            "criteria": options,
        }
    for action, text in DANGER.items():
        questions[f"danger_{action}"] = {"type": "noul", "instructions": text}
    return questions, home


def decide(answers: dict[str, Any], t: Thresholds) -> tuple[str, list[str]]:
    """What Tycho would do with Jev's answers: a kind, or "defer" to Claude."""
    mode = answers["mode"]
    if mode["confidence"] < t.mode:
        return "defer", []
    kind = KIND_FOR_MODE.get(mode["choice"])
    if kind is None:
        return "defer", []  # planning and settings go elsewhere
    if kind != "actions":
        return kind, []
    category = answers["category"]
    if category["choice"] == "none" or category["confidence"] < t.category:
        return "defer", []
    if answers["multi_action"]["noul"] >= t.multi:
        return "defer", []
    pick = answers[f"act_{category['choice']}"]
    if pick["confidence"] < t.action:
        return "defer", []
    action = pick["choice"]
    if action in GLOBAL_NEVER:
        danger = answers.get(f"danger_{action}", {"noul": 0.0})
        if danger["noul"] < t.danger:
            return "defer", []
    return "actions", [action]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("cases", type=Path)
    parser.add_argument("--runs", type=int, default=2)
    args = parser.parse_args()

    key = credentials.get("typesafe")
    if key is None:
        raise SystemExit("No TypeSafe key. Run: uv run python -m tycho.credentials set typesafe")
    cases, _ = load_cases(args.cases, engine="")
    keybinds = KeybindStore.load(GameInstall.find()).keybinds
    bound = [a for a in keybinds if a.bound and a.label]
    questions, home = build_questions(bound)
    valid = set(home)
    lexicon = Lexicon.default()
    sizes = {k: len(v["criteria"]) for k, v in questions.items() if v["type"] == "choice"}
    print(f"{len(cases)} cases; {len(valid)} actions; option counts {sizes}\n")

    out = Path(__file__).resolve().parents[2] / "recordings" / "jev.results.jsonl"
    results: list[Result] = []
    with (
        httpx2.Client(headers={"Authorization": f"Bearer {key}"}, timeout=30) as client,
        out.open("a", encoding="utf-8") as log,
    ):
        for run in range(args.runs):
            for case in cases:
                corrected = lexicon.correct(case.heard)
                state: dict[str, Any] = {"pilot_said": corrected.text}
                if corrected.hints:
                    state["possible_game_terms"] = [
                        f'"{h.heard}" may mean {h.term}' for h in corrected.hints
                    ]
                body = {"model": MODEL, "state": state, "questions": questions}
                result = Result(case.key, run, 0.0)
                started = time.perf_counter()
                try:
                    response = client.post(ENDPOINT, json=body)
                    result.ms = (time.perf_counter() - started) * 1000
                    if response.status_code != 200:
                        result.error = f"{response.status_code} {response.text[:200]}"
                    else:
                        result.answers = response.json()["answers"]
                except httpx2.HTTPError as e:
                    result.error = f"{type(e).__name__}: {e}"
                results.append(result)
                log.write(json.dumps(result.__dict__) + "\n")
                if result.error:
                    print(f"ERR run{run} {case.key!r}: {result.error}")
                    if run == 0 and len(results) == 1:
                        raise SystemExit("First request failed; stopping.")

    rules = {c.key: c for c in cases}
    ok = [r for r in results if not r.error]
    print(f"\n{len(ok)}/{len(results)} requests succeeded")
    if not ok:
        return
    times = sorted(r.ms for r in ok)
    print(
        f"latency: p50 {statistics.median(times):.0f} ms, "
        f"p90 {times[round(0.9 * (len(times) - 1))]:.0f} ms, max {times[-1]:.0f} ms"
    )

    # Mode accuracy: does Jev's mode fit what the case expects?
    fits = sum(
        KIND_FOR_MODE.get(r.answers["mode"]["choice"], "defer") in rules[r.case].rule["expect"]
        for r in ok
    )
    print(f"mode fits the expected kind of response: {fits}/{len(ok)}")

    print(f"\n{'threshold':>9} {'handled':>8} {'right':>6} {'WRONG':>6} {'deferred':>9}")
    for level in (0.5, 0.6, 0.7, 0.8, 0.9, 0.95):
        t = Thresholds(mode=level, category=level, action=level)
        handled = right = wrong = 0
        for r in ok:
            kind, actions = decide(r.answers, t)
            if kind == "defer":
                continue
            handled += 1
            call = Call("jev", r.case, r.case, r.run, kind=kind, actions=actions)  # pyright: ignore[reportArgumentType]
            verdict = grade(call, rules[r.case].rule, valid)
            right += verdict == "pass"
            wrong += verdict != "pass"
        print(f"{level:>9} {handled:>8} {right:>6} {wrong:>6} {len(ok) - handled:>9}")

    t = Thresholds()
    print(f"\nAt {t.mode}: wrong decisions and close calls")
    for r in ok:
        if r.run:
            continue
        kind, actions = decide(r.answers, t)
        rule = rules[r.case].rule
        mode = r.answers["mode"]
        pick = r.answers.get(f"act_{r.answers['category']['choice']}", {})
        detail = (
            f"mode {mode['choice']} {mode['confidence']:.2f} | "
            f"cat {r.answers['category']['choice']} {r.answers['category']['confidence']:.2f} | "
            f"act {pick.get('choice')} {pick.get('confidence', 0):.2f} | "
            f"multi {r.answers['multi_action']['noul']:.2f}"
        )
        if kind == "defer":
            continue
        call = Call("jev", r.case, r.case, 0, kind=kind, actions=actions)  # pyright: ignore[reportArgumentType]
        verdict = grade(call, rule, valid)
        if verdict != "pass":
            print(f"  WRONG [{rules[r.case].tag}] {r.case!r}: {kind} {actions} ({verdict})")
            print(f"         {detail}")
    print(f"\nLog: {out}")


if __name__ == "__main__":
    main()
