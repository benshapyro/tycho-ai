"""Compare ways of searching the web for a player's question (#354, #360).

    uv run python tools/bench/web_trial.py [--modes sites,fallback] [--rounds 2]
        [--budget 5] [--no-judge] [--out FILE]

Asks each question below through ``WebSearch`` in each mode:

- ``sites``: as players have it, the game pack's sites only, one search.
- ``fallback``: ``web_search_open``: the sites first, then the open web with a page read
  when they have no clear answer.
- ``open``: the open web every time (any site, two searches, two page reads).
- ``read``: ``open``, told to always read a page first.

Then a stronger Claude (``JUDGE``, with its own web search) grades each round's answers
to a question side by side, without knowing which mode gave which: right, partial,
wrong, or none (no answer). It prints each answer with its time, searches, pages read,
cost and stop reason, then a summary per mode with the judge's grades, and appends
every row to ``recordings/web_trial.jsonl``.

Costs money: about 1 to 3 cents a search and 2 to 5 cents a judged question. The run
stops starting new work once ``--budget`` dollars are spent (5 by default). Uses the
Anthropic key Tycho stores (``tycho key set anthropic``), or ``ANTHROPIC_API_KEY``.
"""

import argparse
import asyncio
import json
import logging
import os
import random
import re
import statistics
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any

import anthropic

from tycho.brain_choice import anthropic_key
from tycho.knowledge import web
from tycho.knowledge.web import WebSearch

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "recordings" / "web_trial.jsonl"
MODES = ("sites", "fallback", "open", "read")
NAMES = {
    "sites": "Star Citizen sites",
    "fallback": "Sites, then the open web",
    "open": "Open web",
    "read": "Open web, page read",
}
GRADES = ("right", "partial", "wrong", "none")

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
    # News, which the site list was made for: the open web shouldn't do worse here.
    "what's in the next Star Citizen patch",
    # Answers that live in a page's body, a table or a guide's steps, not in a snippet.
    "what are the refining methods and how do their yield and speed compare",
    "what weapons and components does a stock Cutlass Black come with",
    "what are the steps of a box delivery mission from accepting it to getting paid",
    # Where things are.
    "where do I find hadanite",
    "where is the best place to mine quantanium in Stanton",
    "where can I buy medpens in Lorville",
    "where is the cargo center at Seraphim Station",
    "where can I buy a Pembroke suit",
    "where do I sell gold ore",
    # How to do things.
    "how do I get my ship out of impound",
    "how do I do bunker missions",
    "how do the Hathor missions work",
    "how do I open the executive hangar in Pyro",
    "how do I fix my ship not showing at the ASOP terminal",
    "how do I tractor beam boxes into my ship",
    "how do I set my spawn location",
    "how do I install a new quantum drive",
    "how do I share missions with my party",
    "how much does it cost to claim a ship after it blows up",
    # What players think: answers that live in Reddit threads, forums and blogs.
    "what's the best starter ship for making money",
    "is the Drake Corsair worth it for solo players",
    "what do players think of the freight elevator changes",
    "how do I get rid of a crime stat without paying",
    "what's the best mining laser for the Prospector",
    "what armor is best for bunker missions",
    "how do I avoid getting interdicted in Pyro",
    "what's the best way to make money with an Avenger Titan",
    "what's the fastest way to gain reputation with Citizens for Prosperity",
    "how do I start the current in-game event",
    "is the Polaris worth it for a small crew",
)

READ_FIRST = (
    " Always read the most useful page from the results before you answer, even when the "
    "snippets look enough."
)
"""Added to the open web's brief in ``read`` mode."""

JUDGE = "claude-sonnet-5-5"
JUDGE_PRICES = (2.0, 10.0)
"""Per million tokens, input and output."""
JUDGE_BRIEF = """\
You grade answers a voice co-pilot gave a Star Citizen player. Today is {today}; the \
live game is patch 4.10.x. For one question you get several answers, labelled A, B and \
so on, each with the sites it came from. Search the web to check the facts that matter, \
then grade each answer:
- right: answers the question, and what it says is correct as far as you can tell;
- partial: useful but incomplete, or hedged where a clear answer exists;
- wrong: says something that is incorrect or out of date in a way that would mislead;
- none: no answer, or only says it couldn't find one.
Reply with JSON only, no other text: {{"A": {{"grade": "...", "why": "..."}}, ...}}, \
"why" in at most 20 words."""

LOGGED = re.compile(
    r"(\d+) searches, (\d+) pages read, \d+ sites, about \$([\d.]+), stopped: (\w*)"
)


class _Calls(logging.Handler):
    """Keeps the search's own log lines: each request's searches, pages and cost."""

    def __init__(self) -> None:
        super().__init__(logging.DEBUG)
        self.lines: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.lines.append(record.getMessage())


async def _ask(search: WebSearch, calls: _Calls, question: str) -> dict[str, Any]:
    calls.lines = []
    started = time.perf_counter()
    result = await search.run({"query": question})
    seconds = time.perf_counter() - started
    body = json.loads(result.content)
    requests = [m for line in calls.lines if (m := LOGGED.search(line))]
    answer = str(body.get("answer") or body.get("error") or "")
    return {
        "question": question,
        "seconds": round(seconds, 1),
        "requests": len(requests),
        "searches": sum(int(m[1]) for m in requests),
        "pages": sum(int(m[2]) for m in requests),
        "cost": round(sum(float(m[3]) for m in requests), 5),
        "stopped": requests[-1][4] if requests else "",
        "wider": "any site" in str(body.get("source", "")),
        "sites": body.get("sites", []),
        "answer": answer,
        "failed": result.is_error,
    }


async def _judge(
    client: anthropic.AsyncAnthropic, question: str, rows: list[dict[str, Any]]
) -> tuple[dict[str, dict[str, str]], float]:
    """Each row's grade, by mode, and what the judging cost. The answers are shuffled
    and labelled so the judge can't tell which mode gave which."""
    shuffled = random.sample(rows, len(rows))
    labels = [chr(ord("A") + i) for i in range(len(shuffled))]
    answers = "\n\n".join(
        f"{label}: {row['answer'] if not row['failed'] else '(no answer)'}\n"
        f"Sites: {', '.join(row['sites'][:3]) or 'none'}"
        for label, row in zip(labels, shuffled, strict=True)
    )
    message = await client.messages.create(
        model=JUDGE,
        max_tokens=4000,
        system=JUDGE_BRIEF.format(today=time.strftime("%d %B %Y")),
        tools=[{"type": "web_search_20260209", "name": "web_search", "max_uses": 3}],  # pyright: ignore[reportArgumentType]
        output_config={"effort": "medium"},
        messages=[{"role": "user", "content": f"Question: {question}\n\n{answers}"}],
    )
    usage = message.usage
    cost = (usage.input_tokens * JUDGE_PRICES[0] + usage.output_tokens * JUDGE_PRICES[1]) / 1e6
    searches = sum(1 for b in message.content if getattr(b, "type", "") == "server_tool_use")
    cost += 0.01 * searches
    text = "".join(b.text for b in message.content if b.type == "text")
    found = re.search(r"\{.*\}", text, re.DOTALL)
    try:
        graded: dict[str, Any] = json.loads(found[0]) if found else {}
    except json.JSONDecodeError:
        graded = {}
    grades: dict[str, dict[str, str]] = {}
    for label, row in zip(labels, shuffled, strict=True):
        entry: dict[str, Any] = graded.get(label) or {}
        grade = str(entry.get("grade", "")).lower()
        grades[row["mode"]] = {
            "grade": grade if grade in GRADES else "ungraded",
            "why": str(entry.get("why", "")),
        }
    return grades, cost


def _summary(name: str, rows: list[dict[str, Any]]) -> str:
    times = [r["seconds"] for r in rows]
    grades = Counter(r.get("grade", "ungraded") for r in rows)
    graded = " ".join(f"{g} {grades[g]}" for g in (*GRADES, "ungraded") if grades[g])
    return (
        f"{name}: {graded} | {sum(r['failed'] for r in rows)} failed, "
        f"{sum(r['wider'] for r in rows)} went wider, "
        f"median {statistics.median(times):.1f} s, slowest {max(times):.1f} s, "
        f"{sum(r['pages'] for r in rows)} pages read, "
        f"{sum(r['stopped'] == 'max_tokens' for r in rows)} cut off, "
        f"about ${sum(r['cost'] for r in rows):.3f}"
    )


async def main() -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n")[0])
    parser.add_argument("--modes", default="sites,fallback")
    parser.add_argument("--rounds", type=int, default=2)
    parser.add_argument("--budget", type=float, default=5.0)
    parser.add_argument("--no-judge", action="store_true")
    parser.add_argument("--limit", type=int, default=len(QUESTIONS), help="first N questions")
    parser.add_argument("--out", type=Path, default=OUT)
    args = parser.parse_args()
    modes = [m.strip() for m in args.modes.split(",")]
    if bad := [m for m in modes if m not in MODES]:
        parser.error(f"unknown modes: {', '.join(bad)} (choose from {', '.join(MODES)})")
    key = os.environ.get("ANTHROPIC_API_KEY") or anthropic_key()
    if not key:
        print("No Anthropic key: run `uv run tycho key set anthropic` first.", file=sys.stderr)
        return 1
    calls = _Calls()
    web_log = logging.getLogger("tycho.knowledge.web")
    web_log.setLevel(logging.DEBUG)
    web_log.addHandler(calls)
    client = anthropic.AsyncAnthropic(api_key=key, max_retries=2)
    plain_brief = web.OPEN_BRIEF
    spent = 0.0
    results: dict[str, list[dict[str, Any]]] = {m: [] for m in modes}
    agree = [0, 0]
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("a", encoding="utf-8") as out:
        for round_ in range(1, args.rounds + 1):
            for question in QUESTIONS[: args.limit]:
                if spent >= args.budget:
                    print(f"Budget of ${args.budget:.2f} reached: stopping.")
                    break
                rows: list[dict[str, Any]] = []
                for mode in modes:
                    web.OPEN_BRIEF = plain_brief + (READ_FIRST if mode == "read" else "")
                    search = WebSearch(
                        client, open_web=mode in ("open", "read"), fallback=mode == "fallback"
                    )
                    row = {"round": round_, "mode": mode, **await _ask(search, calls, question)}
                    spent += float(row["cost"])
                    rows.append(row)
                if not args.no_judge:
                    grades, cost = await _judge(client, question, rows)
                    spent += cost
                    for row in rows:
                        row.update(grades.get(row["mode"], {"grade": "ungraded", "why": ""}))
                for row in rows:
                    results[row["mode"]].append(row)
                    out.write(json.dumps(row, ensure_ascii=False) + "\n")
                    print(
                        f"[r{round_} {row['mode']}] {row['seconds']:>5.1f} s  "
                        f"{row['searches']}s/{row['pages']}p  ${row['cost']:.3f}  "
                        f"{row['stopped'] or '?':<10} {row.get('grade', '-'):<8} "
                        f"{'wider ' if row['wider'] else ''}{question}\n"
                        f"        {row['answer']}\n"
                        f"        {', '.join(row['sites'][:3])}\n"
                        f"        judge: {row.get('why', '')}"
                    )
                out.flush()
            else:
                continue
            break
    if args.rounds > 1 and not args.no_judge:
        for rows in results.values():
            by_question: dict[str, list[str]] = {}
            for row in rows:
                by_question.setdefault(row["question"], []).append(row.get("grade", ""))
            for grades in by_question.values():
                if len(grades) > 1:
                    agree[0] += len(set(grades)) == 1
                    agree[1] += 1
    print()
    for mode, rows in results.items():
        if rows:
            print(_summary(NAMES[mode], rows))
    if agree[1]:
        print(f"Same grade in every round: {agree[0]}/{agree[1]} question-modes")
    print(f"Spent about ${spent:.2f}. Answers: {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
