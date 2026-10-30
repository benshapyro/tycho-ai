"""Generate ElevenLabs Voice Design previews for each concept in a designs file.

Each design returns three previews; only the preview text is charged. Nothing is
saved to the account: pick favourites first, then save them as voices.

Usage: uv run python tools/bench/tts/design_voices.py OUT_DIR [--designs FILE]
"""

import argparse
import base64
import json
import tomllib
from pathlib import Path
from typing import Any

import httpx2

from tycho import credentials

ENDPOINT = "https://api.elevenlabs.io/v1/text-to-voice/design"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("out", type=Path)
    parser.add_argument("--designs", type=Path, default=Path("tools/spike/voice_designs.toml"))
    parser.add_argument("--model", default="eleven_ttv_v3")
    args = parser.parse_args()

    key = credentials.get("elevenlabs")
    if key is None:
        raise SystemExit(
            "No ElevenLabs key. Run: uv run python -m tycho.credentials set elevenlabs"
        )
    designs: list[dict[str, Any]] = tomllib.loads(args.designs.read_text("utf-8"))["design"]
    args.out.mkdir(parents=True, exist_ok=True)
    index_path = args.out / "designs.json"
    index: dict[str, Any] = json.loads(index_path.read_text()) if index_path.exists() else {}
    characters = 0

    with httpx2.Client(headers={"xi-api-key": key}, timeout=120) as client:
        for design in designs:
            if design["id"] in index:
                print(f"{design['id']}: already generated, skipping")
                continue
            body = {
                "voice_description": " ".join(design["description"].split()),
                "text": " ".join(design["text"].split()),
                "model_id": args.model,
            }
            response = client.post(ENDPOINT, json=body)
            if response.status_code != 200:
                raise SystemExit(f"{design['id']}: {response.status_code} {response.text[:300]}")
            data: dict[str, Any] = response.json()
            options: list[dict[str, Any]] = []
            for n, preview in enumerate(data["previews"], start=1):
                ext = "mp3" if "mpeg" in preview.get("media_type", "audio/mpeg") else "wav"
                name = f"{design['id']}-{n}.{ext}"
                (args.out / name).write_bytes(base64.b64decode(preview["audio_base_64"]))
                options.append({"file": name, "generated_voice_id": preview["generated_voice_id"]})
            index[design["id"]] = {
                "name": design["name"],
                "description": body["voice_description"],
                "text": data["text"],
                "options": options,
            }
            index_path.write_text(json.dumps(index, indent=1))
            characters += len(body["text"])
            print(f"{design['id']}: {len(options)} previews")
    print(f"\n{characters} preview characters charged")


if __name__ == "__main__":
    main()
