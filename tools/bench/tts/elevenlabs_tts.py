"""Speak the voice-test lines with ElevenLabs, timing first audio and total.

Uses the player's key from Windows Credential Manager (text-to-speech permission
is enough). Costs about one credit per character.

Usage: uv run python tools/bench/tts/elevenlabs_tts.py OUT_DIR
"""

import argparse
import json
import time
from pathlib import Path

import httpx2

from tycho import credentials

# ElevenLabs default-library voices (ids are public and stable).
VOICES = {
    "george": "JBFqnCBsd6RMkjVDRZzb",
    "daniel": "onwK4e9ZLuTAKqWW03F9",
    "sarah": "EXAVITQu4vr4xnSDxMaL",
    "brian": "nPczCjzI2devNBz1zQrb",
}
# (model, voices): the newest model for every voice, the fast one for comparison.
RUNS = (("eleven_v4_turbo", tuple(VOICES)), ("eleven_flash_v2_5", ("george",)))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("out", type=Path)
    parser.add_argument("--lines", type=Path, default=Path("tools/spike/voice_lines.txt"))
    args = parser.parse_args()

    key = credentials.get("elevenlabs")
    if key is None:
        raise SystemExit(
            "No ElevenLabs key. Run: uv run python -m tycho.credentials set elevenlabs"
        )
    text = args.lines.read_text("utf-8").splitlines()
    lines = [line.strip() for line in text if line.strip() and not line.startswith("#")]
    characters = 0

    with httpx2.Client(headers={"xi-api-key": key}, timeout=60) as client:
        for model, voices in RUNS:
            for voice in voices:
                folder = args.out / f"elevenlabs-{model.removeprefix('eleven_')}-{voice}"
                folder.mkdir(parents=True, exist_ok=True)
                timings: list[dict[str, object]] = []
                done = _done(folder)
                for i, line in enumerate(lines, start=1):
                    if i in done and (folder / f"{i:02d}.mp3").exists():
                        timings.append(done[i])  # already generated; don't pay twice
                        continue
                    url = (
                        f"https://api.elevenlabs.io/v1/text-to-speech/{VOICES[voice]}/stream"
                        "?output_format=mp3_44100_128"
                    )
                    started = time.perf_counter()
                    first = 0.0
                    audio = bytearray()
                    with client.stream("POST", url, json={"text": line, "model_id": model}) as r:
                        if r.status_code != 200:
                            r.read()
                            raise SystemExit(f"{model}/{voice}: {r.status_code} {r.text[:200]}")
                        for chunk in r.iter_bytes():
                            if not first:
                                first = time.perf_counter() - started
                            audio.extend(chunk)
                    total = time.perf_counter() - started
                    (folder / f"{i:02d}.mp3").write_bytes(audio)
                    characters += len(line)
                    timings.append(
                        {
                            "line": i,
                            "text": line,
                            "first_ms": round(first * 1000),
                            "ms": round(total * 1000),
                        }
                    )
                    print(f"{model:18} {voice:7} {i:02d} first {first * 1000:5.0f} ms", flush=True)
                meta = {"engine": "elevenlabs", "model": model, "voice": voice, "lines": timings}
                (folder / "timings.json").write_text(json.dumps(meta, indent=1))
    print(f"\n{characters} characters sent")


def _done(folder: Path) -> dict[int, dict[str, object]]:
    """Timings already recorded in this folder, by line number."""
    path = folder / "timings.json"
    if not path.exists():
        return {}
    data: dict[str, list[dict[str, object]]] = json.loads(path.read_text())
    return {int(str(t["line"])): t for t in data["lines"]}


if __name__ == "__main__":
    main()
