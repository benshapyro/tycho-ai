"""Check what each transcript would do: fast-path command, or hand to the language model.

Runs the game lexicon and the command matcher over the phrases as written and
over every engine's transcripts, scores them against tools/spike/expected.toml,
then shows which key each command would press with the player's own keybinds.

Usage: uv run python tools/bench/commands_report.py recordings/<session> [--channel LIVE]
"""

import argparse
import json
import time
import tomllib
from pathlib import Path

from ultimo.commands import CommandMatcher, Defer, Match, resolve
from ultimo.game.install import GameInstall
from ultimo.game.store import KeybindStore
from ultimo.lexicon import Lexicon

EXPECTED = Path(__file__).resolve().parents[1] / "spike" / "expected.toml"


def outcome(result: Match | Defer) -> str:
    if isinstance(result, Defer):
        return "brain"
    return "+".join(c.id for c in result.commands)


def grade(got: str, acceptable: list[str]) -> str:
    """right; slow (deferred, but the fast path was expected); wrong (acted wrongly)."""
    if got in acceptable:
        return "right"
    return "slow" if got == "brain" else "wrong"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("session", type=Path)
    parser.add_argument("--channel", default="LIVE")
    args = parser.parse_args()

    expected: dict[str, list[str]] = tomllib.loads(EXPECTED.read_text("utf-8"))
    lexicon, matcher = Lexicon.default(), CommandMatcher.default()

    sources: dict[str, dict[str, str]] = {"phrases as written": {p: p for p in expected}}
    for path in sorted((args.session / "results").glob("*.jsonl")):
        rows = [json.loads(line) for line in path.read_text("utf-8").splitlines()]
        clips = {r["phrase"]: r["text"] for r in rows if r["type"] == "clip"}
        if clips:
            label = next(r["label"] for r in rows if r["type"] == "meta")
            sources[label] = clips

    print(f"{'source':42} {'right':>6} {'slow':>5} {'wrong':>6}  ms/phrase")
    problems: list[str] = []
    for label, texts in sources.items():
        counts = {"right": 0, "slow": 0, "wrong": 0}
        started = time.perf_counter()
        for phrase, acceptable in expected.items():
            text = lexicon.correct(texts.get(phrase, "")).text
            got = outcome(matcher.match(text))
            verdict = grade(got, acceptable)
            counts[verdict] += 1
            if verdict != "right":
                problems.append(f"  {verdict:5} {label[:30]:30} {text!r} -> {got}")
        ms = (time.perf_counter() - started) * 1000 / len(expected)
        print(f"{label:42} {counts['right']:>6} {counts['slow']:>5} {counts['wrong']:>6}  {ms:.2f}")
    if problems:
        print("\nNot right:")
        print("\n".join(problems))

    store = KeybindStore(GameInstall.find(args.channel))
    print(f"\nWith your keybinds ({len(store.keybinds)} actions):")
    seen: set[str] = set()
    for acceptable in expected.values():
        for command_id in acceptable[0].split("+"):
            command = next((c for c in matcher.commands if c.id == command_id), None)
            if command is None or command.id in seen:
                continue
            seen.add(command.id)
            r = resolve(command, store.keybinds)
            if r.action is None:
                print(f"  {command.id:24} NO KEY BOUND (tried {', '.join(command.actions)})")
                continue
            keys = " or ".join(str(i) for i in r.action.inputs if i.pressable)
            note = "  (toggle fallback: state unknown)" if r.fallback else ""
            mode = f" [{r.action.activation}]" if r.action.activation else ""
            print(f"  {command.id:24} {keys:14}{mode:14} {r.action.label}{note}")


if __name__ == "__main__":
    main()
