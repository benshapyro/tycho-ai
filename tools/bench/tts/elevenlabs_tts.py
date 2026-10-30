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
    parser.add_argument(
        "--voice", action="append", help="NAME=VOICE_ID; repeatable. Default: the stock set"
    )
    parser.add_argument("--model", default="eleven_v4_turbo", help="with --voice")
    parser.add_argument("--suffix", default="", help="added to folder names, e.g. -character")
    parser.add_argument("--max-line", type=int, default=0, help="only the first N lines")
    args = parser.parse_args()
    runs: tuple[tuple[str, tuple[str, ...]], ...] = RUNS
    if args.voice:
        for item in args.voice:
            name, _, voice_id = item.partition("=")
            VOICES[name] = voice_id
        runs = ((args.model, tuple(v.partition("=")[0] for v in args.voice)),)

    key = credentials.get("elevenlabs")
    if key is None:
        raise SystemExit(
            "No ElevenLabs key. Run: uv run python -m tycho.credentials set elevenlabs"
        )
    text = args.lines.read_text("utf-8").splitlines()
    lines = [line.strip() for line in text if line.strip() and not line.startswith("#")]
    if args.max_line:
        lines = lines[: args.max_line]
    characters = 0

    with httpx2.Client(headers={"xi-api-key": key}, timeout=60) as client:
        for model, voices in runs:
            for voice in voices:
                folder = (
                    args.out / f"elevenlabs-{model.removeprefix('eleven_')}-{voice}{args.suffix}"
                )
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
                    first, total, audio = _speak(client, url, {"text": line, "model_id": model})
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
                    # Save after every line, so an interrupted run never pays twice.
                    meta = {
                        "engine": "elevenlabs",
                        "model": model,
                        "voice": voice,
                        "lines": timings,
                    }
                    (folder / "timings.json").write_text(json.dumps(meta, indent=1))
    print(f"\n{characters} characters sent")


def _speak(client: httpx2.Client, url: str, body: dict[str, str]) -> tuple[float, float, bytes]:
    """Stream one line; return seconds to first audio, total seconds, and the audio.

    Retries dropped connections, which happen on long streams.
    """
    for attempt in range(3):
        started = time.perf_counter()
        first = 0.0
        audio = bytearray()
        try:
            with client.stream("POST", url, json=body) as r:
                if r.status_code != 200:
                    r.read()
                    raise SystemExit(f"{r.status_code} {r.text[:200]}")
                for chunk in r.iter_bytes():
                    if not first:
                        first = time.perf_counter() - started
                    audio.extend(chunk)
            return first, time.perf_counter() - started, bytes(audio)
        except httpx2.RemoteProtocolError, httpx2.ReadError, httpx2.ConnectError:
            if attempt == 2:
                raise
            time.sleep(1 + attempt)
    raise AssertionError("unreachable")


def _done(folder: Path) -> dict[int, dict[str, object]]:
    """Timings already recorded in this folder, by line number."""
    path = folder / "timings.json"
    if not path.exists():
        return {}
    data: dict[str, list[dict[str, object]]] = json.loads(path.read_text())
    return {int(str(t["line"])): t for t in data["lines"]}


if __name__ == "__main__":
    main()
