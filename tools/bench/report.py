"""Compare speech-to-text results for one recording session.

Every engine is scored twice: on its raw transcripts, and after the game
lexicon has corrected them ("+lex"). The lexicon is also run over the correct
phrases, where any change it makes is a false alarm.

Usage: uv run python tools/bench/report.py recordings/<session> [--misses]
"""

import argparse
import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ultimo.eval import ClipScore, score_clip
from ultimo.lexicon import Lexicon

# Game words a generic model is likely to get wrong. Matched ignoring spaces and case.
GAME_TERMS = frozenset(
    {"arccorp", "microtech", "hurston", "quantanium", "mobiglas", "cutlass", "qt", "pips"}
)


@dataclass(frozen=True, slots=True)
class Clip:
    phrase: str
    text: str
    corrected: str
    ms: float
    raw: ClipScore
    lex: ClipScore


@dataclass(frozen=True, slots=True)
class EngineResult:
    meta: dict[str, Any]
    clips: list[Clip]

    def wer(self, lex: bool) -> float:
        scores = [c.lex if lex else c.raw for c in self.clips]
        return sum(s.errors for s in scores) / max(sum(s.reference_words for s in scores), 1)

    def exact(self, lex: bool) -> int:
        return sum((c.lex if lex else c.raw).exact for c in self.clips)

    def terms(self, lex: bool) -> str:
        scores = [c.lex if lex else c.raw for c in self.clips]
        expected = sum(len(s.terms_expected) for s in scores)
        missed = sum(len(s.terms_missed) for s in scores)
        return f"{expected - missed}/{expected}"

    def latency(self, quantile: float) -> float:
        times = sorted(c.ms for c in self.clips)
        return times[min(len(times) - 1, round(quantile * (len(times) - 1)))]


def load(path: Path, lexicon: Lexicon) -> EngineResult:
    meta: dict[str, Any] = {}
    clips: list[Clip] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        row: dict[str, Any] = json.loads(line)
        if row["type"] == "meta":
            meta = row
            continue
        phrase, text = row["phrase"], row["text"]
        corrected = lexicon.correct(text).text
        clips.append(
            Clip(
                phrase,
                text,
                corrected,
                float(row["ms"]),
                score_clip(phrase, text, GAME_TERMS),
                score_clip(phrase, corrected, GAME_TERMS),
            )
        )
    return EngineResult(meta, clips)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("session", type=Path)
    parser.add_argument("--misses", action="store_true", help="list every imperfect transcript")
    args = parser.parse_args()

    lexicon = Lexicon.default()
    loaded = [load(p, lexicon) for p in sorted((args.session / "results").glob("*.jsonl"))]
    results = [r for r in loaded if r.clips]  # skip runs that are still going or crashed
    results.sort(key=lambda r: (r.wer(lex=True), r.latency(0.5)))

    print(
        f"{'engine':42} {'WER':>6} {'+lex':>6} {'exact':>6} {'+lex':>6} {'terms':>6} {'+lex':>6}"
        f" {'p50 ms':>7} {'p90 ms':>7} {'VRAM MB':>8} game"
    )
    print("-" * 122)
    for r in results:
        vram = r.meta.get("vram_mb")
        n = len(r.clips)
        print(
            f"{r.meta['label']:42} {r.wer(False):6.1%} {r.wer(True):6.1%}"
            f" {r.exact(False):>3}/{n:<2} {r.exact(True):>3}/{n:<2}"
            f" {r.terms(False):>6} {r.terms(True):>6}"
            f" {r.latency(0.5):7.0f} {r.latency(0.9):7.0f} {'?' if vram is None else vram:>8}"
            f" {'yes' if r.meta.get('game_running') else 'no'}"
        )

    phrases = sorted({c.phrase for r in results for c in r.clips})
    started = time.perf_counter()
    alarms = [(p, lexicon.correct(p)) for p in phrases]
    per_phrase_ms = (time.perf_counter() - started) * 1000 / max(len(phrases), 1)
    alarms = [(p, c) for p, c in alarms if c.replacements]
    print(
        f"\nLexicon: {len(lexicon)} terms, {per_phrase_ms:.2f} ms per phrase,"
        f" {len(alarms)} false alarm(s) on the {len(phrases)} correct phrases"
    )
    for phrase, corrected in alarms:
        print(f"  {phrase!r} -> {corrected.text!r}")

    if args.misses:
        for r in results:
            print(f"\n{r.meta['label']}")
            for c in r.clips:
                if not c.lex.exact:
                    fixed = "" if c.corrected == c.text else f"  (lex: {c.corrected!r})"
                    print(f"  {c.phrase!r:50} -> {c.text!r}{fixed}")


if __name__ == "__main__":
    main()
