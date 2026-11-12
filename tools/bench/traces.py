"""Read brain traces (#282): what the brain did for each request, and why.

    uv run python tools/bench/traces.py runs [FILE]
    uv run python tools/bench/traces.py summary [FILE] [--run RUN] [--results FILE]
    uv run python tools/bench/traces.py diff [FILE] --run OLD --run NEW
    uv run python tools/bench/traces.py diff OLD_FILE NEW_FILE

FILE is the eval's ``recordings/tycho_eval.traces.jsonl`` by default; play writes
``traces.jsonl`` in the logs folder when ``TYCHO_TRACE=1``. A file can hold several runs
(each eval run, each play session): ``runs`` lists them, and ``summary`` and ``diff``
take the last one unless ``--run`` names another (a unique prefix will do).

``summary`` counts requests by outcome and gives the times, calls, look-ups, errors and
cost, with the slowest requests. With the eval's results file it also lists the cases
graded as wrong presses, from the same run. ``diff`` compares two runs case by case
(the request's words in play), so a change to the brain shows what it changed.

Free: it only reads files.
"""

import argparse
import json
import statistics
import sys
from collections import Counter
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any, cast

ROOT = Path(__file__).resolve().parents[2]
EVAL_TRACES = ROOT / "recordings" / "tycho_eval.traces.jsonl"
EVAL_RESULTS = ROOT / "recordings" / "tycho_eval.results.jsonl"
PER_HOUR = 30
"""Brain requests in an hour of play, as the eval assumes until play traces measure it."""
SLOWEST = 5

type Line = dict[str, Any]


def load(path: Path) -> list[Line]:
    """Every trace line in a file, skipping any that aren't whole (a line still being
    written, or cut by a crash)."""
    lines: list[Line] = []
    for text in path.read_text("utf-8").splitlines():
        try:
            line = json.loads(text)
        except json.JSONDecodeError:
            continue
        if isinstance(line, dict):
            lines.append(line)  # pyright: ignore[reportUnknownArgumentType]
    return lines


def runs(lines: Iterable[Line]) -> list[tuple[str, int]]:
    """Each run in the file, in the order it started, with its number of requests."""
    counted = Counter(str(line.get("run", "")) for line in lines)
    return list(counted.items())


def pick(lines: Sequence[Line], run: str | None = None) -> tuple[str, list[Line]]:
    """One run's lines: the last run, or the one ``run`` names or starts with."""
    names = [name for name, _ in runs(lines)]
    if not names:
        raise SystemExit("No traces in that file.")
    if run is None:
        chosen = names[-1]
    else:
        matching = [name for name in names if name == run] or [
            name for name in names if name.startswith(run)
        ]
        if len(matching) != 1:
            found = "no run" if not matching else f"{len(matching)} runs"
            raise SystemExit(f"{found} matches {run!r}; see: traces.py runs")
        chosen = matching[0]
    return chosen, [line for line in lines if str(line.get("run", "")) == chosen]


def key(line: Line) -> str:
    """What pairs a request across runs: the eval's case, or the words in play."""
    return str(line.get("case") or line.get("text", ""))


def part(line: Line, name: str) -> list[Line]:
    """A list of objects in a line ("calls", "lookups", "tools"), or none."""
    return cast(list[Line], line.get(name) or [])


def calls(line: Line) -> list[Line]:
    return part(line, "calls")


def tools(line: Line) -> list[str]:
    """Every tool call the request made, in order."""
    return [str(t.get("name")) for c in calls(line) for t in part(c, "tools")]


def times(lines: Iterable[Line]) -> list[float]:
    return [float(line.get("ms") or 0) for line in lines]


def first_tokens(lines: Iterable[Line]) -> list[float]:
    return [t for line in lines if (t := first_token(line)) is not None]


def cost(line: Line) -> float | None:
    """The request's cost, when every call that ended with a reply said; calls that
    ended at an act never report theirs, so those are left out and counted."""
    known = [c["cost"] for c in calls(line) if isinstance(c.get("cost"), int | float)]
    return sum(known) if known else None


def first_token(line: Line) -> float | None:
    """Milliseconds to the first piece of the first call's stream."""
    made = calls(line)
    if not made or made[0].get("first_ms") is None:
        return None
    return float(made[0]["first_ms"]) - float(made[0].get("at_ms") or 0)


def percentile(values: Sequence[float], n: int) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, round(n / 100 * (len(ordered) - 1)))]


def _ms(value: float | None) -> str:
    return "-" if value is None else f"{value:.0f} ms"


def wrong_presses(results: Path, run: str) -> list[tuple[str, str]]:
    """The cases the eval graded as wrong presses in the run that wrote these traces."""
    block: list[Line] = []
    inside = False
    for line in load(results):
        if line.get("type") == "meta":
            inside = line.get("traces") == run
            continue
        if inside and "verdict" in line:
            block.append(line)
    return [
        (str(r.get("heard", "")), str(r["verdict"]))
        for r in block
        if str(r.get("verdict", "")).startswith("WRONG PRESS")
    ]


def summary(
    lines: Sequence[Line],
    run: str,
    per_hour: float = PER_HOUR,
    wrong: Sequence[tuple[str, str]] | None = None,
) -> list[str]:
    """A run in a few lines an agent can read at a glance."""
    out = [f"Run {run}: {len(lines)} brain requests"]
    if not lines:
        return out
    models = Counter(f"{line.get('provider', '?')}/{line.get('model', '?')}" for line in lines)
    out.append("Models: " + ", ".join(f"{m} ({n})" for m, n in models.most_common()))
    outcomes = Counter(str(line.get("outcome", "?")) for line in lines)
    out.append("Outcomes: " + ", ".join(f"{o} {n}" for o, n in outcomes.most_common()))
    took, firsts = times(lines), first_tokens(lines)
    out.append(
        f"Time: p50 {_ms(percentile(took, 50))}, p90 {_ms(percentile(took, 90))}; "
        f"first token p50 {_ms(percentile(firsts, 50))}"
    )
    made = [len(calls(line)) for line in lines]
    out.append(f"Calls a request: mean {statistics.fmean(made):.2f}, most {max(made)}")
    looks = [look for line in lines for look in part(line, "lookups")]
    if looks:
        by_name: dict[str, list[float]] = {}
        for look in looks:
            by_name.setdefault(str(look.get("name")), []).append(float(look.get("took_ms") or 0))
        shown = ", ".join(
            f"{name} {len(t)}x ~{statistics.fmean(t):.0f} ms"
            for name, t in sorted(by_name.items(), key=lambda item: -len(item[1]))
        )
        out.append(f"Look-ups: {shown}")
        failed = [look for look in looks if look.get("error")]
        if failed:
            out.append(
                f"Look-ups that came back as errors: {len(failed)} "
                f"({', '.join(sorted({str(look.get('name')) for look in failed}))})"
            )
    errors = [line for line in lines if line.get("outcome") == "error"]
    for line in errors:
        out.append(f"Error: {key(line)!r}: {line.get('stopped', '')}")
    costs = [c for line in lines if (c := cost(line)) is not None]
    unpriced = sum(1 for line in lines for c in calls(line) if c.get("cost") is None)
    if costs:
        each = statistics.fmean(costs)
        out.append(
            f"Cost: ${sum(costs):.4f} in all, ${each:.5f} a request, "
            f"about ${each * per_hour:.3f} an hour at {per_hour:g} requests"
            + (f" ({unpriced} call(s) reported no cost)" if unpriced else "")
        )
    out.append("Slowest:")
    for line in sorted(lines, key=lambda line: -float(line.get("ms") or 0))[:SLOWEST]:
        used = tools(line)
        out.append(
            f"  {_ms(line.get('ms'))}  {key(line)!r}: {line.get('outcome')}, "
            f"{len(calls(line))} call(s){', ' + ' > '.join(used) if used else ''}"
        )
    if wrong is not None:
        out.append(f"Wrong presses in the eval: {len(wrong) or 'none'}")
        out.extend(f"  {heard!r}: {verdict}" for heard, verdict in wrong)
    return out


def _shape(line: Line) -> str:
    """What a request did, in a few words: its outcome and its tools in order."""
    decided = cast(Line, line.get("decided") or {})
    what = decided.get("actions") or decided.get("sequence") or decided.get("card") or ""
    used = " > ".join(tools(line)) or "no tools"
    return f"{line.get('outcome')}{f' {what}' if what else ''} via {used}"


def diff(old: Sequence[Line], new: Sequence[Line], old_name: str, new_name: str) -> list[str]:
    """What changed between two runs, request by request, then overall."""
    before = {key(line): line for line in old}
    after = {key(line): line for line in new}
    out = [f"Diff: {old_name} -> {new_name}"]
    changed = 0
    for name in sorted(before.keys() & after.keys()):
        a, b = before[name], after[name]
        was, now = _shape(a), _shape(b)
        if was != now:
            changed += 1
            out.append(f"  {name!r}\n    was {was}\n    now {now}")
    out.append(f"Changed: {changed} of {len(before.keys() & after.keys())} requests in both")
    if only := sorted(before.keys() - after.keys()):
        out.append(f"Only in {old_name}: {len(only)}")
    if only := sorted(after.keys() - before.keys()):
        out.append(f"Only in {new_name}: {len(only)}")
    out.append(f"time p50: {_ms(percentile(times(old), 50))} -> {_ms(percentile(times(new), 50))}")
    out.append(
        f"first token p50: {_ms(percentile(first_tokens(old), 50))} -> "
        f"{_ms(percentile(first_tokens(new), 50))}"
    )
    mean_calls = [
        statistics.fmean([len(calls(x)) for x in lines]) if lines else 0.0 for lines in (old, new)
    ]
    out.append(f"calls a request: {mean_calls[0]:.2f} -> {mean_calls[1]:.2f}")
    return out


def _arguments(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    commands = parser.add_subparsers(dest="command", required=True)
    listing = commands.add_parser("runs", help="list the runs in a trace file")
    listing.add_argument("file", nargs="?", type=Path, default=EVAL_TRACES)
    summing = commands.add_parser("summary", help="summarise one run")
    summing.add_argument("file", nargs="?", type=Path, default=EVAL_TRACES)
    summing.add_argument("--run", help="the run, or the start of its name (default: last)")
    summing.add_argument(
        "--results", type=Path, help="the eval's results file, to list its wrong presses"
    )
    summing.add_argument("--per-hour", type=float, default=PER_HOUR)
    comparing = commands.add_parser("diff", help="compare two runs")
    comparing.add_argument("files", nargs="*", type=Path, help="one file, or old then new")
    comparing.add_argument("--run", action="append", default=[], help="old, then new")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _arguments(argv)
    if args.command == "runs":
        for name, count in runs(load(args.file)):
            print(f"{name}  ({count} requests)")
        return 0
    if args.command == "summary":
        run, lines = pick(load(args.file), args.run)
        wrong = wrong_presses(args.results, run) if args.results else None
        print("\n".join(summary(lines, run, args.per_hour, wrong)))
        return 0
    files: list[Path] = args.files or [EVAL_TRACES]
    if len(files) > 2 or len(args.run) > 2:
        raise SystemExit("diff takes one file with two --run, or two files")
    if len(files) == 2:
        old_name, old = pick(load(files[0]), args.run[0] if args.run else None)
        new_name, new = pick(load(files[1]), args.run[1] if len(args.run) > 1 else None)
    else:
        if len(args.run) != 2:
            raise SystemExit("diff with one file needs --run OLD --run NEW")
        lines = load(files[0])
        old_name, old = pick(lines, args.run[0])
        new_name, new = pick(lines, args.run[1])
    print("\n".join(diff(old, new, old_name, new_name)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
