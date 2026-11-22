"""Compare web search services on the same Star Citizen questions (#365).

    uv run --group bench python tools/bench/search_compare.py
        [--modes tycho,exa,parallel,perplexity] [--rounds 1] [--limit N]
        [--budget 5] [--judge reference|none] [--out FILE]

Every mode runs through OpenRouter, so one key (``OPENROUTER_API_KEY``) covers all of
them and no new account is needed:

- ``tycho``: Tycho's own ``WebSearch`` as players have it (the game's sites first, then
  the open web with page reads), unchanged, on Haiku through OpenRouter's
  Anthropic-shaped endpoint. OpenRouter passes Anthropic's search and fetch tools
  through, so the requests are the ones Tycho sends to Anthropic.
- ``exa``, ``parallel``, ``perplexity``: one Haiku request with OpenRouter's web plugin
  on that engine. The plugin searches once with the question and puts the results in
  the request; Haiku answers from them with Tycho's open-web brief. No page reads.
  Firecrawl is left out: OpenRouter refuses it under zero data retention, which the
  maintainer's account has.

A judge (Sonnet, through OpenRouter) grades each question's answers side by side,
shuffled, against the reference answers in ``web_references.toml``, with
``web_trial.py``'s brief. Prints each answer, then a summary per mode: grades, median
and slowest time, and cost as OpenRouter billed it. Rows go to
``recordings/search_compare.jsonl``.

Costs money: about 1 to 3 cents a question for each mode, and about 1 cent a question
for the judge. The run stops starting new questions once ``--budget`` dollars are spent,
by OpenRouter's own count of the key's spending, which can lag a few seconds behind.
"""

import argparse
import asyncio
import json
import logging
import os
import statistics
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any, cast

import anthropic
import openai
import web_trial

from tycho.knowledge import web
from tycho.knowledge.web import WebSearch
from tycho.packs import STAR_CITIZEN, built_in

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "recordings" / "search_compare.jsonl"
OPENROUTER = "https://openrouter.ai/api"
MODEL = "anthropic/claude-haiku-5.5"
JUDGE = "anthropic/claude-sonnet-5.5"
ENGINES = ("exa", "parallel", "perplexity")
MODES = ("tycho", *ENGINES)
RESULTS = 5
"""Results a plugin search brings back: OpenRouter's default."""
SEARCH_PROMPT = (
    "Web search results for the player's question follow. Answer from them as your brief "
    "says: never cite, link or name a URL, because the answer is read aloud."
)
"""Replaces the plugin's own lead-in, which asks for markdown links: read aloud, a link
is noise."""
GRADES = web_trial.GRADES


async def _plugin(client: openai.AsyncOpenAI, engine: str, question: str) -> dict[str, Any]:
    today = time.localtime()
    asked = f"[{time.strftime('%d %B %Y', today)}] {question} (Star Citizen)"
    started = time.perf_counter()
    try:
        response = await client.chat.completions.create(
            model=MODEL,
            max_tokens=web.MAX_TOKENS,
            messages=[
                {"role": "system", "content": web.OPEN_BRIEF},
                {"role": "user", "content": asked},
            ],
            extra_body={
                "plugins": [
                    {
                        "id": "web",
                        "engine": engine,
                        "max_results": RESULTS,
                        "search_prompt": SEARCH_PROMPT,
                    }
                ],
                "usage": {"include": True},
                "reasoning": {"effort": "low"},
            },
        )
    except openai.APIError as e:
        return _failed(question, time.perf_counter() - started, type(e).__name__)
    seconds = time.perf_counter() - started
    choice = response.choices[0]
    message: dict[str, Any] = choice.message.model_dump()
    annotations: list[dict[str, Any]] = message.get("annotations") or []
    sites = [str(a.get("url_citation", {}).get("url", "")) for a in annotations]
    usage: dict[str, Any] = response.usage.model_dump() if response.usage else {}
    text = " ".join(str(message.get("content") or "").split())
    return {
        "question": question,
        "seconds": round(seconds, 1),
        "requests": 1,
        "searches": 1,
        "pages": 0,
        "cost": round(float(usage.get("cost") or 0.0), 5),
        "stopped": str(choice.finish_reason or ""),
        "wider": True,
        "sites": [s for s in sites if s],
        "answer": text,
        "failed": not text,
        "prompt_tokens": usage.get("prompt_tokens"),
    }


def _failed(question: str, seconds: float, why: str) -> dict[str, Any]:
    return {
        "question": question,
        "seconds": round(seconds, 1),
        "requests": 1,
        "searches": 0,
        "pages": 0,
        "cost": 0.0,
        "stopped": why,
        "wider": False,
        "sites": [],
        "answer": "",
        "failed": True,
    }


def _summary(mode: str, rows: list[dict[str, Any]]) -> str:
    times = [r["seconds"] for r in rows]
    grades = Counter(r.get("grade", "ungraded") for r in rows)
    graded = " ".join(f"{g} {grades[g]}" for g in (*GRADES, "ungraded") if grades[g])
    over = sum(1 for r in rows if r["searches"] > web.OPEN_USES * 2)
    return (
        f"{mode}: {graded} | {sum(r['failed'] for r in rows)} failed, "
        f"median {statistics.median(times):.1f} s, 90th {_percentile(times, 0.9):.1f} s, "
        f"slowest {max(times):.1f} s, {sum(r['searches'] for r in rows)} searches "
        f"(most {max(r['searches'] for r in rows)} in one, {over} over {web.OPEN_USES * 2}), "
        f"{sum(r['pages'] for r in rows)} pages read, "
        f"${sum(r['cost'] for r in rows):.3f} "
        f"(${statistics.mean(r['cost'] for r in rows):.4f} each)"
    )


def _percentile(values: list[float], share: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(share * len(ordered)))]


async def _spent(key: str) -> float:
    """What the OpenRouter key has spent so far, in dollars: the bill, not an estimate."""
    async with openai.AsyncOpenAI(base_url=f"{OPENROUTER}/v1", api_key=key) as client:
        response = cast(dict[str, Any], await client.get("/key", cast_to=object))
    return float(response["data"]["usage"])


async def main() -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n")[0])
    parser.add_argument("--modes", default=",".join(MODES))
    parser.add_argument("--rounds", type=int, default=1)
    parser.add_argument("--budget", type=float, default=5.0)
    parser.add_argument("--judge", choices=("reference", "none"), default="reference")
    parser.add_argument("--limit", type=int, default=len(web_trial.QUESTIONS))
    parser.add_argument("--out", type=Path, default=OUT)
    args = parser.parse_args()
    modes = [m.strip() for m in args.modes.split(",")]
    if bad := [m for m in modes if m not in MODES]:
        parser.error(f"unknown modes: {', '.join(bad)} (choose from {', '.join(MODES)})")
    key = os.environ.get("OPENROUTER_API_KEY")
    if not key:
        print("Set OPENROUTER_API_KEY first.", file=sys.stderr)
        return 1
    calls = web_trial._Calls()  # pyright: ignore[reportPrivateUsage]
    web_log = logging.getLogger("tycho.knowledge.web")
    web_log.setLevel(logging.DEBUG)
    web_log.addHandler(calls)
    claude = anthropic.AsyncAnthropic(
        base_url=OPENROUTER,
        api_key=key,
        max_retries=2,
        default_headers={"Authorization": f"Bearer {key}"},
    )
    plugins = openai.AsyncOpenAI(base_url=f"{OPENROUTER}/v1", api_key=key, max_retries=2)
    web_trial.JUDGE = JUDGE
    sites = built_in(STAR_CITIZEN).web_sites
    first = await _spent(key)
    results: dict[str, list[dict[str, Any]]] = {m: [] for m in modes}
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("a", encoding="utf-8") as out:
        for round_ in range(1, args.rounds + 1):
            for question in web_trial.QUESTIONS[: args.limit]:
                if (spent := await _spent(key) - first) >= args.budget:
                    print(f"Budget of ${args.budget:.2f} reached (${spent:.2f}): stopping.")
                    break
                rows: list[dict[str, Any]] = []
                for mode in modes:
                    if mode == "tycho":
                        search = WebSearch(claude, model=MODEL, sites=sites, fallback=True)
                        row = await web_trial._ask(search, calls, question)  # pyright: ignore[reportPrivateUsage]
                    else:
                        row = await _plugin(plugins, mode, question)
                    rows.append({"round": round_, "mode": mode, **row})
                if args.judge != "none":
                    grades, _ = await web_trial._judge(claude, question, rows, "reference")  # pyright: ignore[reportPrivateUsage]
                    for row in rows:
                        row.update(grades.get(row["mode"], {"grade": "ungraded", "why": ""}))
                for row in rows:
                    results[row["mode"]].append(row)
                    out.write(json.dumps(row, ensure_ascii=False) + "\n")
                    print(
                        f"[r{round_} {row['mode']:<10}] {row['seconds']:>5.1f} s  "
                        f"{row['searches']}s/{row['pages']}p  ${row['cost']:.4f}  "
                        f"{row.get('grade', '-'):<8} {question}\n"
                        f"        {row['answer']}\n"
                        f"        {', '.join(row['sites'][:3])}\n"
                        f"        judge: {row.get('why', '')}"
                    )
                out.flush()
            else:
                continue
            break
    print()
    for mode, rows in results.items():
        if rows:
            print(_summary(mode, rows))
    print(f"OpenRouter billed ${await _spent(key) - first:.2f} for this run. Answers: {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
