"""Compare speech-to-text results for one recording session.

Usage: uv run python tools/bench/report.py recordings/<session> [--misses]
"""

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ultimo.eval import ClipScore, score_clip

# Game words a generic model is likely to get wrong. Matched ignoring spaces and case.
GAME_TERMS = frozenset(
    {"arccorp", "microtech", "hurston", "quantanium", "mobiglas", "cutlass", "qt", "pips"}
)


@dataclass(frozen=True, slots=True)
class Clip:
    phrase: str
    text: str
    ms: float
    score: ClipScore


@dataclass(frozen=True, slots=True)
class EngineResult:
    meta: dict[str, Any]
    clips: list[Clip]

    @property
    def wer(self) -> float:
        words = sum(c.score.reference_words for c in self.clips)
        return sum(c.score.errors for c in self.clips) / max(words, 1)

    @property
    def exact(self) -> int:
        return sum(c.score.exact for c in self.clips)

    @property
    def terms(self) -> tuple[int, int]:
        expected = sum(len(c.score.terms_expected) for c in self.clips)
        missed = sum(len(c.score.terms_missed) for c in self.clips)
        return expected - missed, expected

    def latency(self, quantile: float) -> float:
        times = sorted(c.ms for c in self.clips)
        return times[min(len(times) - 1, round(quantile * (len(times) - 1)))]


def load(path: Path) -> EngineResult:
    meta: dict[str, Any] = {}
    clips: list[Clip] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        row: dict[str, Any] = json.loads(line)
        if row["type"] == "meta":
            meta = row
        else:
            score = score_clip(row["phrase"], row["text"], GAME_TERMS)
            clips.append(Clip(row["phrase"], row["text"], float(row["ms"]), score))
    return EngineResult(meta, clips)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("session", type=Path)
    parser.add_argument("--misses", action="store_true", help="list every imperfect transcript")
    args = parser.parse_args()

    loaded = [load(p) for p in sorted((args.session / "results").glob("*.jsonl"))]
    results = [r for r in loaded if r.clips]  # skip runs that are still going or crashed
    results.sort(key=lambda r: (r.wer, r.latency(0.5)))

    header = f"{'engine':52} {'WER':>6} {'exact':>6} {'terms':>6} {'p50 ms':>7} {'p90 ms':>7}"
    print(header + f" {'load s':>7} {'VRAM MB':>8} game")
    print("-" * (len(header) + 22))
    for r in results:
        hit, total = r.terms
        vram = r.meta.get("vram_mb")
        print(
            f"{r.meta['label']:52} {r.wer:6.1%} {r.exact:>3}/{len(r.clips):<2}"
            f" {hit:>2}/{total:<3} {r.latency(0.5):7.0f} {r.latency(0.9):7.0f}"
            f" {r.meta['load_seconds']:7.1f} {'?' if vram is None else vram:>8}"
            f" {'yes' if r.meta.get('game_running') else 'no'}"
        )
    if args.misses:
        for r in results:
            print(f"\n{r.meta['label']}")
            for c in r.clips:
                if not c.score.exact:
                    print(f"  {c.phrase!r:55} -> {c.text!r}")


if __name__ == "__main__":
    main()
