"""Test TypeSafe's Jev as a fast router and single-action picker, on the typed case file.

One request per case asks, in parallel: which mode, which category of controls,
which action within every category, whether it's more than one request, and
whether it explicitly asks for each dangerous action. Tycho would then act on
Jev's answer only when it's confident, and hand everything else to Claude.

The re-test in #24 grades both jobs a router would have (ADR 10):
- picking a mode, against each case's ``mode`` label: accuracy per mode, and how
  often Jev is confident and wrong, worst of all when it calls something a command;
- picking a single action, from descriptions (what it does, what it isn't for, how
  it's said), with an "other" way out, in an option order shuffled per request
  from ``--seed``, so a lean towards the first option shows up.
It also reports latency, for the router (#193).

Raw answers are logged, so the report can be re-graded without new requests
(``--replay``). The API key comes from Windows Credential Manager:
    uv run python -m tycho.credentials set typesafe

Usage: uv run python tools/bench/jev_report.py tools/spike/brain_cases.toml [--runs 2] [--seed 1]
Again, from the log: add --replay recordings/jev.results.jsonl
"""

import argparse
import json
import random
import statistics
import time
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx2
from brain_report import GLOBAL_NEVER, Call, Case, grade, load_cases
from brain_report import MODES as CASE_MODES

from tycho import credentials
from tycho.brain.briefing import command_phrasings
from tycho.commands.matcher import CommandMatcher
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

# The case file's modes (brain_report.MODES), as Jev is asked them.
MODES = {
    "command": "Wants a game control pressed now",
    "lookup": "Asks for one piece of game information: prices, locations, routes, distances, "
    "ship or item details",
    "chat": "Talks to the assistant, or asks its opinion, without asking for an action",
    "planning": "Wants help planning a multi-step task, like a trade run or a mining trip",
    "coach": "Wants to learn how something in the game works, or be walked through it",
    "settings": "Wants to change the assistant itself: its voice, sounds, what it calls the "
    "pilot, how long it waits",
    "not_for_tycho": "Not addressed to the assistant: talking to squadmates, someone in the "
    "room, or themselves",
    "unclear": "Asks the assistant to do something, but it's unclear what",
}
# Older logs called it this.
MODE_ALIASES = {"not_for_assistant": "not_for_tycho"}

DANGER = {
    "v_eject": "Does the pilot explicitly order an ejection from the ship right now?",
    "v_self_destruct": "Does the pilot explicitly order the ship to self-destruct right now?",
    "v_toggle_qdrive_engagement": "Does the pilot explicitly order the quantum drive to "
    "engage (jump) right now?",
    "v_jettison_volatile_cargo": "Does the pilot explicitly order the cargo to be jettisoned?",
}

# What an action isn't for, where a neighbour is easy to pick instead (the first re-test's
# wrong picks were all throttle labels). Hand-kept; names with no key are left out.
NOT_FOR = {
    "v_power_throttle_max": "Not cruise control, and not power to engines",
    "v_power_throttle_min": "Not the space brake, and not turning power off",
    "v_ifcs_throttle_swap_mode": "Not full or zero throttle: only switches cruise control",
    "v_ifcs_throttle_set_sticky": "Not full throttle: only turns cruise control on",
    "v_ifcs_throttle_set_normal": "Not zero throttle: only turns cruise control off",
    "v_space_brake": "Not cutting the throttle to zero",
    "v_power_toggle": "Not moving power between engines, shields and weapons",
    "v_power_set_on": "Not moving power between engines, shields and weapons",
    "v_power_set_off": "Not cutting the throttle; never for an unclear request",
    "v_toggle_qdrive_engagement": "Not quantum mode or spooling: it jumps now",
    "v_toggle_quantum_mode": "Not jumping: only switches to quantum mode",
    "v_eject": "Not leaving the seat, and not opening doors",
    "v_emergency_exit": "Not leaving the seat normally",
    "v_self_destruct": "Never for an unclear request",
    "v_jettison_volatile_cargo": "Not unloading cargo at a station",
    "v_weapon_toggle_launch_missile": "Not arming missiles: it fires one",
    "v_weapon_increase_max_missiles": "Not firing: only arms more missiles",
    "v_toggle_all_doors": "Not locking or unlocking doors",
    "v_lock_all_doors": "Not opening or closing doors",
    "v_atc_request": "Not opening the ship's own doors",
}
EXAMPLES = 3
"""Phrasings per action, as the brain's briefing gives them (briefing.BRIEFED_PHRASES)."""

OTHER = "other"
OTHER_TEXT = "None of these: the pilot wants a control not listed here, or no control at all"

KIND_FOR_MODE = {
    "command": "actions",
    "lookup": "look_up",
    "chat": "reply",
    "unclear": "reply",
    "not_for_tycho": "reply",
}
"""What Tycho would do on Jev's mode alone. Planning, coach and settings go to the brain."""

CONFIDENT = (0.7, 0.9)
"""Thresholds the mode report counts confident wrong answers at."""


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
    seed: int | None = None
    first: dict[str, str] = field(default_factory=dict[str, str])
    """Each choice question's first option, as shown: to measure first-option bias."""
    options: dict[str, int] = field(default_factory=dict[str, int])


def describe(label: str, examples: Sequence[str], not_for: str) -> str:
    """An action as Jev sees it: what it does, what it isn't for, and how it's said."""
    text = label.strip().rstrip(".") + "."
    if not_for:
        text += f" {not_for}."
    if examples:
        text += " Said like: " + "; ".join(f'"{e}"' for e in examples[:EXAMPLES]) + "."
    return text


def phrasings(lines: Iterable[str]) -> dict[str, list[str]]:
    """Example phrasings per action, from the briefing's "action | a; b; c" lines."""
    found: dict[str, list[str]] = {}
    for line in lines:
        name, _, said = line.partition(" | ")
        found.setdefault(name, []).extend(s for s in said.split("; ") if s)
    return found


def build_questions(
    actions: Sequence[Action], examples: Mapping[str, Sequence[str]] | None = None
) -> tuple[dict[str, Any], dict[str, str]]:
    """The Jev questions, and which category each action belongs to."""
    examples = examples or {}
    by_map = {m: cat for cat, (_, maps) in CATEGORIES.items() for m in maps}
    per_category: dict[str, dict[str, str]] = {cat: {} for cat in CATEGORIES}
    home: dict[str, str] = {}
    for action in actions:
        category = by_map.get(action.actionmap)
        if category is None or action.name in home:
            continue
        per_category[category][action.name] = describe(
            action.label, examples.get(action.name, ()), NOT_FOR.get(action.name, "")
        )
        home[action.name] = category

    questions: dict[str, Any] = {
        "mode": {
            "type": "choice",
            "instructions": "What does the pilot want from their ship's AI co-pilot?",
            "criteria": dict(MODES),
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
            "criteria": options | {OTHER: OTHER_TEXT},
        }
    for action, text in DANGER.items():
        questions[f"danger_{action}"] = {"type": "noul", "instructions": text}
    return questions, home


def shuffled(questions: dict[str, Any], rng: random.Random) -> dict[str, Any]:
    """The questions with every choice's options in a new order; the rest unchanged."""
    out: dict[str, Any] = {}
    for name, question in questions.items():
        if question["type"] == "choice":
            items = list(question["criteria"].items())
            rng.shuffle(items)
            question = {**question, "criteria": dict(items)}
        out[name] = question
    return out


def request_rng(seed: int, run: int, case: str) -> random.Random:
    """The same order for the same seed, run and case, on any PC."""
    return random.Random(f"{seed}:{run}:{case}")  # noqa: S311 (option order, not secrets)


def mode_of(answers: Mapping[str, Any]) -> tuple[str, float]:
    mode = answers["mode"]
    choice = str(mode["choice"])
    return MODE_ALIASES.get(choice, choice), float(mode["confidence"])


def decide(answers: dict[str, Any], t: Thresholds) -> tuple[str, list[str]]:
    """What Tycho would do with Jev's answers: a kind, or "defer" to Claude."""
    mode, confidence = mode_of(answers)
    if confidence < t.mode:
        return "defer", []
    kind = KIND_FOR_MODE.get(mode)
    if kind is None:
        return "defer", []  # planning, coach and settings go elsewhere
    if kind != "actions":
        return kind, []
    category = answers["category"]
    if category["choice"] == "none" or category["confidence"] < t.category:
        return "defer", []
    if answers["multi_action"]["noul"] >= t.multi:
        return "defer", []
    pick = answers[f"act_{category['choice']}"]
    if pick["choice"] == OTHER or pick["confidence"] < t.action:
        return "defer", []
    action = pick["choice"]
    if action in GLOBAL_NEVER:
        danger = answers.get(f"danger_{action}", {"noul": 0.0})
        if danger["noul"] < t.danger:
            return "defer", []
    return "actions", [action]


# --- Grading, from logged answers: no requests ---------------------------------------


@dataclass
class ModeScore:
    cases: int = 0
    right: int = 0
    confident_wrong: dict[float, int] = field(default_factory=dict[float, int])
    called: Counter[str] = field(default_factory=Counter[str])
    """What Jev called this mode's cases when it was wrong."""


def mode_scores(
    results: Iterable[Result], modes: Mapping[str, str], confident: Sequence[float] = CONFIDENT
) -> dict[str, ModeScore]:
    """Per expected mode: how many cases, how many Jev got right, and its wrong answers
    at or above each confidence in ``confident``."""
    scores = {mode: ModeScore(confident_wrong=dict.fromkeys(confident, 0)) for mode in MODES}
    for r in results:
        if r.error or r.case not in modes:
            continue
        expected = modes[r.case]
        got, confidence = mode_of(r.answers)
        score = scores[expected]
        score.cases += 1
        if got == expected:
            score.right += 1
            continue
        score.called[got] += 1
        for level in confident:
            if confidence >= level:
                score.confident_wrong[level] += 1
    return scores


def false_commands(
    results: Iterable[Result], modes: Mapping[str, str], level: float
) -> list[tuple[str, float]]:
    """Cases that aren't commands which Jev confidently called one: the misroute that
    could press a key (ADR 10: when unsure, talk, never act)."""
    found: list[tuple[str, float]] = []
    for r in results:
        if r.error or modes.get(r.case, "command") == "command":
            continue
        got, confidence = mode_of(r.answers)
        if got == "command" and confidence >= level:
            found.append((r.case, confidence))
    return found


def percentile(sorted_ms: Sequence[float], share: float) -> float:
    return sorted_ms[round(share * (len(sorted_ms) - 1))]


def latency(results: Iterable[Result]) -> tuple[float, float, float] | None:
    """p50, p90 and the slowest request, in milliseconds."""
    times = sorted(r.ms for r in results if not r.error)
    if not times:
        return None
    return statistics.median(times), percentile(times, 0.9), times[-1]


@dataclass
class FirstOption:
    answers: int = 0
    first: int = 0
    chance: float = 0.0
    """How often the first option would be picked by chance: the mean of 1/options."""


def first_option_bias(results: Iterable[Result]) -> dict[str, FirstOption]:
    """How often Jev picks whichever option came first, for the mode, the category and
    the action in the category it picked; only from shuffled requests."""
    found = {q: FirstOption() for q in ("mode", "category", "action")}
    for r in results:
        if r.error or not r.first:
            continue
        picked = r.answers["category"]["choice"]
        asked = {"mode": "mode", "category": "category"}
        if picked != "none":
            asked["action"] = f"act_{picked}"
        for label, question in asked.items():
            if question not in r.first or question not in r.answers:
                continue
            stat = found[label]
            stat.chance = (stat.chance * stat.answers + 1 / r.options[question]) / (
                stat.answers + 1
            )
            stat.answers += 1
            stat.first += r.answers[question]["choice"] == r.first[question]
    return found


def action_sweep(
    results: Sequence[Result], rules: Mapping[str, Case], valid: set[str]
) -> list[tuple[float, int, int, int, int]]:
    """Per threshold: requests handled, right, wrong, and deferred to Claude."""
    rows: list[tuple[float, int, int, int, int]] = []
    ok = [r for r in results if not r.error and r.case in rules]
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
        rows.append((level, handled, right, wrong, len(ok) - handled))
    return rows


def report(results: Sequence[Result], cases: Sequence[Case], valid: set[str]) -> None:
    rules = {c.key: c for c in cases}
    modes = {c.key: str(c.rule["mode"]) for c in cases}
    ok = [r for r in results if not r.error and r.case in rules]
    print(f"\n{len(ok)}/{len(results)} requests succeeded")
    if not ok:
        return
    seeds = sorted({str(r.seed) for r in ok})
    print(f"shuffle seeds: {', '.join(seeds)}")
    if timing := latency(ok):
        print(f"latency: p50 {timing[0]:.0f} ms, p90 {timing[1]:.0f} ms, max {timing[2]:.0f} ms")

    print("\nMode, against each case's label")
    head = "".join(f" {'WRONG@' + str(level):>9}" for level in CONFIDENT)
    print(f"{'mode':>14} {'cases':>6} {'right':>6} {'share':>6}{head}  called instead")
    total = right = 0
    for mode, score in mode_scores(ok, modes).items():
        total, right = total + score.cases, right + score.right
        share = f"{score.right / score.cases:.0%}" if score.cases else "-"
        wrong = "".join(f" {score.confident_wrong[level]:>9}" for level in CONFIDENT)
        instead = ", ".join(f"{m} {n}" for m, n in score.called.most_common(3))
        print(f"{mode:>14} {score.cases:>6} {score.right:>6} {share:>6}{wrong}  {instead}")
    print(f"{'all':>14} {total:>6} {right:>6} {right / total:>6.0%}")
    for level in CONFIDENT:
        wrongly = false_commands(ok, modes, level)
        print(f"not commands, called a command at {level}+: {len(wrongly)}")
        for case, confidence in sorted(set(wrongly))[:10]:
            print(f"  [{modes[case]}] {case!r} {confidence:.2f}")

    bias = first_option_bias(ok)
    if any(b.answers for b in bias.values()):
        print("\nFirst-option bias (shuffled requests): picked the first option shown")
        for label, b in bias.items():
            if b.answers:
                print(
                    f"  {label:>8}: {b.first}/{b.answers} ({b.first / b.answers:.0%}), "
                    f"chance {b.chance:.0%}"
                )

    print("\nSingle actions")
    print(f"{'threshold':>9} {'handled':>8} {'right':>6} {'WRONG':>6} {'deferred':>9}")
    for level, handled, good, bad, deferred in action_sweep(ok, rules, valid):
        print(f"{level:>9} {handled:>8} {good:>6} {bad:>6} {deferred:>9}")

    t = Thresholds()
    print(f"\nAt {t.mode}: wrong decisions")
    for r in ok:
        kind, actions = decide(r.answers, t)
        if kind == "defer":
            continue
        rule = rules[r.case].rule
        call = Call("jev", r.case, r.case, r.run, kind=kind, actions=actions)  # pyright: ignore[reportArgumentType]
        verdict = grade(call, rule, valid)
        if verdict == "pass":
            continue
        mode, confidence = mode_of(r.answers)
        category = r.answers["category"]
        pick = r.answers.get(f"act_{category['choice']}", {})
        print(f"  WRONG run{r.run} [{rules[r.case].tag}] {r.case!r}: {kind} {actions} ({verdict})")
        print(
            f"         mode {mode} {confidence:.2f} | "
            f"cat {category['choice']} {category['confidence']:.2f} | "
            f"act {pick.get('choice')} {pick.get('confidence', 0):.2f} | "
            f"multi {r.answers['multi_action']['noul']:.2f}"
        )


def replay(log: Path) -> list[Result]:
    rows = [json.loads(line) for line in log.read_text("utf-8").splitlines() if line.strip()]
    return [Result(**row) for row in rows]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("cases", type=Path)
    parser.add_argument("--runs", type=int, default=2)
    parser.add_argument(
        "--seed", type=int, default=0, help="option order; each run and case gets its own"
    )
    parser.add_argument("--no-shuffle", action="store_true", help="options in a fixed order")
    parser.add_argument("--replay", type=Path, help="grade a results log again, no requests")
    args = parser.parse_args()

    if set(MODES) != set(CASE_MODES):
        raise SystemExit("jev_report's modes and the case file's differ")
    cases, _ = load_cases(args.cases, engine="")
    keybinds = KeybindStore.load(GameInstall.find()).keybinds
    bound = [a for a in keybinds if a.bound and a.label]
    examples = phrasings(command_phrasings(keybinds, CommandMatcher.default()))
    questions, home = build_questions(bound, examples)
    valid = set(home)
    if args.replay:
        report(replay(args.replay), cases, valid)
        return

    key = credentials.get("typesafe")
    if key is None:
        raise SystemExit("No TypeSafe key. Run: uv run python -m tycho.credentials set typesafe")
    lexicon = Lexicon.default()
    sizes = {k: len(v["criteria"]) for k, v in questions.items() if v["type"] == "choice"}
    print(f"{len(cases)} cases; {len(valid)} actions; option counts {sizes}\n")

    out = Path(__file__).resolve().parents[2] / "recordings" / "jev.results.jsonl"
    out.parent.mkdir(exist_ok=True)
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
                asked = questions
                result = Result(case.key, run, 0.0)
                if not args.no_shuffle:
                    asked = shuffled(questions, request_rng(args.seed, run, case.key))
                    result.seed = args.seed
                    choices = {k: v for k, v in asked.items() if v["type"] == "choice"}
                    result.first = {k: next(iter(v["criteria"])) for k, v in choices.items()}
                    result.options = {k: len(v["criteria"]) for k, v in choices.items()}
                body = {"model": MODEL, "state": state, "questions": asked}
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

    report(results, cases, valid)
    print(f"\nLog: {out}")


if __name__ == "__main__":
    main()
