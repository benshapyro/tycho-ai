"""Compare web search with and without the open-web trial (#354, #360).

    uv run python tools/bench/web_trial.py [--only open|sites] [--out FILE]

Asks each question below through ``WebSearch`` twice: as players have it (Star Citizen
sites only, one search), and with ``web_search_open`` (any site, two searches, two page
reads). It prints, for each, how long it took, how many searches ran and pages were
read, the cost, the sites, and the answer, then a summary. It writes every answer to
``recordings/web_trial.jsonl`` for a person (or Claude) to grade: whether a spoken
answer is concrete is counted here, whether it's right needs reading.

Costs money: about 1 cent a search, so a full run is about 30 to 50 cents. Uses the
Anthropic key Tycho stores (``tycho key set anthropic``), or ``ANTHROPIC_API_KEY``.
"""

import argparse
import asyncio
import json
import logging
import os
import re
import statistics
import sys
import time
from pathlib import Path
from typing import Any

import anthropic

from tycho.brain_choice import anthropic_key
from tycho.knowledge.web import WebSearch

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "recordings" / "web_trial.jsonl"

QUESTIONS = (
    # From the 11 Nov play session (#353, #354): five requests, no usable answer.
    "where do I pay off a crime stat at Levski",
    "where is the admin office in Area18",
    "how do I enter a remote turret on an Idris",
    "how do I repair and refuel my ship at a station",
    "where can I buy quantum fuel in Pyro",
    "how do I start salvage missions",
    "where is the refinery at ARC-L1",
    "how do I switch to decoupled mode",
    "where can I sell RMC in Stanton",
    # News, which the site list was made for: the trial shouldn't do worse here.
    "what's in the next Star Citizen patch",
)

EMPTY = re.compile(
    r"\b(couldn'?t find|could not find|no (official |clear )?(word|information|details)|"
    r"nothing (relevant|usable)|not (sure|clear)|unable to)\b",
    re.IGNORECASE,
)
"""An answer that says it found nothing, or hedges: not concrete."""

LOGGED = re.compile(r"(\d+) searches, (\d+) pages read, \d+ sites, about \$([\d.]+)")


class _Calls(logging.Handler):
    """Keeps the search's own log line, which has its searches, pages and cost."""

    def __init__(self) -> None:
        super().__init__(logging.DEBUG)
        self.last = ""

    def emit(self, record: logging.LogRecord) -> None:
        self.last = record.getMessage()


async def _ask(search: WebSearch, calls: _Calls, question: str) -> dict[str, Any]:
    calls.last = ""
    started = time.perf_counter()
    result = await search.run({"query": question})
    seconds = time.perf_counter() - started
    body = json.loads(result.content)
    found = LOGGED.search(calls.last)
    searches, pages, cost = (
        (int(found[1]), int(found[2]), float(found[3])) if found else (0, 0, 0.0)
    )
    answer = str(body.get("answer") or body.get("error") or "")
    return {
        "question": question,
        "seconds": round(seconds, 1),
        "searches": searches,
        "pages": pages,
        "cost": cost,
        "concrete": not result.is_error and not EMPTY.search(answer),
        "sites": body.get("sites", []),
        "answer": answer,
        "failed": result.is_error,
    }


def _summary(name: str, rows: list[dict[str, Any]]) -> str:
    times = [r["seconds"] for r in rows]
    return (
        f"{name}: {sum(r['concrete'] for r in rows)}/{len(rows)} concrete, "
        f"{sum(r['failed'] for r in rows)} failed, median {statistics.median(times):.1f} s, "
        f"slowest {max(times):.1f} s, {sum(r['pages'] for r in rows)} pages read, "
        f"about ${sum(r['cost'] for r in rows):.3f}"
    )


async def main() -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n")[0])
    parser.add_argument("--only", choices=("open", "sites"))
    parser.add_argument("--out", type=Path, default=OUT)
    args = parser.parse_args()
    key = os.environ.get("ANTHROPIC_API_KEY") or anthropic_key()
    if not key:
        print("No Anthropic key: run `uv run tycho key set anthropic` first.", file=sys.stderr)
        return 1
    calls = _Calls()
    web_log = logging.getLogger("tycho.knowledge.web")
    web_log.setLevel(logging.DEBUG)
    web_log.addHandler(calls)
    client = anthropic.AsyncAnthropic(api_key=key, max_retries=0)
    modes = [m for m in ("sites", "open") if args.only in (None, m)]
    results: dict[str, list[dict[str, Any]]] = {}
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("a", encoding="utf-8") as out:
        for mode in modes:
            search = WebSearch(client, open_web=mode == "open")
            rows = results[mode] = []
            for question in QUESTIONS:
                row = {"mode": mode, **await _ask(search, calls, question)}
                rows.append(row)
                out.write(json.dumps(row, ensure_ascii=False) + "\n")
                print(
                    f"[{mode}] {row['seconds']:>5.1f} s  {row['searches']}s/{row['pages']}p  "
                    f"${row['cost']:.3f}  {'OK ' if row['concrete'] else '-- '} {question}\n"
                    f"        {row['answer']}\n        {', '.join(row['sites'][:3])}"
                )
    print()
    for mode, rows in results.items():
        print(_summary("Star Citizen sites" if mode == "sites" else "Open web (trial)", rows))
    print(f"Answers: {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
