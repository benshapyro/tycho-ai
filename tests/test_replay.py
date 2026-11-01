"""Replay: the real recordings through real Parakeet and the whole loop, to a fake keyboard.

Run on demand and before releases (ADR 11): ``uv run pytest -m replay -s``.
Needs the speech model (``tycho check`` downloads it) and a recordings/ session
from tools/spike/record.py; skipped otherwise, so CI never runs it.
"""

import asyncio
import json
import statistics
import tomllib
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from tests.fakes.fake_audio import FakeAudioSource
from tests.fakes.fake_keys import FakeFocus, FakeKeySender
from tests.fakes.fake_runtime import FakeFeedback
from tycho import paths
from tycho.commands import CommandMatcher
from tycho.eval.wer import score_clip
from tycho.game.install import GameInstall
from tycho.game.keybinds import Keybinds
from tycho.game.store import GameDataError, KeybindStore
from tycho.input import Presser
from tycho.lexicon import Lexicon
from tycho.ptt.binding import Binding
from tycho.ptt.monitor import Edge, PushToTalk
from tycho.runtime import Result, Session
from tycho.stt import PARAKEET_V2, ModelStore, choose_device, cuda_available, gpu_free_mb
from tycho.stt.parakeet import SAMPLERATE, ParakeetTranscriber

sf = pytest.importorskip("soundfile")  # the bench group; the suite must run without it

pytestmark = pytest.mark.replay

ROOT = Path(__file__).resolve().parents[1]
EXPECTED = ROOT / "tools" / "spike" / "expected.toml"


def _latest_session() -> Path:
    sessions = sorted(
        p for p in (ROOT / "recordings").glob("20*") if (p / "manifest.jsonl").is_file()
    )
    if not sessions:
        pytest.skip("no recordings; make some with tools/spike/record.py")
    return sessions[-1]


def _clips(session: Path) -> list[dict[str, Any]]:
    lines = (session / "manifest.jsonl").read_text("utf-8").splitlines()
    return [json.loads(line) for line in lines if line.strip()]


def _audio(path: Path) -> np.ndarray[Any, np.dtype[np.float32]]:
    samples, rate = sf.read(path, dtype="float32")  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
    assert rate == SAMPLERATE, f"{path} is {rate} Hz"
    return np.asarray(samples, dtype=np.float32)


@pytest.fixture(scope="module")
def transcriber() -> ParakeetTranscriber:
    store = ModelStore(paths.models_dir())
    if not store.ready(PARAKEET_V2) and store.missing(PARAKEET_V2):
        pytest.skip(f"the speech model isn't in {store.folder(PARAKEET_V2)}; run tycho check")
    choice = choose_device("auto", cuda=cuda_available(), free_mb=gpu_free_mb())
    print(f"\nParakeet on the {choice.device}: {choice.reason}")
    return ParakeetTranscriber.load(store.folder(PARAKEET_V2), choice.device)


def test_accuracy_matches_the_benchmark(transcriber: ParakeetTranscriber) -> None:
    session = _latest_session()
    device = "cuda" if transcriber.device == "gpu" else "cpu"
    bench_file = session / "results" / f"onnx-asr_{PARAKEET_V2.name}_{device}.jsonl"
    if not bench_file.is_file():
        pytest.skip(f"no benchmark result to compare with: {bench_file.name}")
    bench = {
        row["file"]: row["text"]
        for row in map(json.loads, bench_file.read_text("utf-8").splitlines())
        if row.get("type") == "clip"
    }
    ours = bench_errors = words = 0
    for clip in _clips(session):
        text = transcriber.transcribe(_audio(session / clip["file"]), SAMPLERATE)
        score = score_clip(clip["phrase"], text)
        ours += score.errors
        bench_errors += score_clip(clip["phrase"], bench[clip["file"]]).errors
        words += score.reference_words
    print(f"Word errors: {ours}/{words} (benchmark {bench_errors}/{words})")
    assert ours <= bench_errors


def test_the_loop_does_what_each_recording_asks(transcriber: ParakeetTranscriber) -> None:
    session = _latest_session()
    try:
        keybinds = KeybindStore.load(GameInstall.find()).keybinds
    except (FileNotFoundError, GameDataError) as e:
        pytest.skip(f"needs the game's keybinds: {e}")
    expected: dict[str, list[str]] = tomllib.loads(EXPECTED.read_text("utf-8"))
    results = asyncio.run(_replay(session, transcriber, keybinds))

    wrong: list[str] = []
    for clip, result in results:
        allowed = expected.get(clip["phrase"])
        if allowed is None:
            continue
        got = "brain" if result.status == "not_understood" else "+".join(result.commands)
        if got not in allowed:
            wrong.append(f"{clip['phrase']!r}: heard {result.heard!r}, got {got} ({result.status})")
        elif got != "brain" and result.status not in ("done", "no_key", "refused"):
            wrong.append(f"{clip['phrase']!r}: matched but {result.status} ({result.detail})")

    for stage in ("transcribe", "correct", "match", "gate", "press"):
        times = [r.timings[stage] for _, r in results if stage in r.timings]
        if times:
            print(f"{stage:>10}: median {statistics.median(times):6.1f} ms, max {max(times):6.1f}")
    assert not wrong, "\n".join(wrong)


async def _replay(
    session: Path, transcriber: ParakeetTranscriber, keybinds: Keybinds
) -> list[tuple[dict[str, Any], Result]]:
    out: list[tuple[dict[str, Any], Result]] = []
    for clip in _clips(session):
        audio = FakeAudioSource(SAMPLERATE)
        loop = Session(
            audio=audio,
            push_to_talk=PushToTalk(Binding.parse("mouse:0x06"), []),
            transcriber=transcriber,
            lexicon=Lexicon.default(),
            matcher=CommandMatcher.default(),
            keybinds=lambda: keybinds,
            presser=Presser(FakeKeySender(), FakeFocus(True)),
            feedback=FakeFeedback(),
        )
        loop.handle(Edge.PRESSED)
        audio.hear(_audio(session / clip["file"]))
        loop.handle(Edge.RELEASED)
        result = await loop.idle()
        await loop.close()
        assert result is not None
        out.append((clip, result))
    return out
