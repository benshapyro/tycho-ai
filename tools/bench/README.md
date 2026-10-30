# Benchmarks

Measurement tools from the latency spike ([issue #1](https://github.com/benshapyro/tycho-ai/issues/1)). They are not part of the app. Some call paid APIs with your own keys; each section says which. Results stay in the git-ignored `recordings/` folder.

Install the extra packages these tools need with `uv sync --group bench`.

| Benchmark | Script | Keys |
|---|---|---|
| Speech-to-text | `engines/*.py`, `report.py` | none |
| Fast-path commands | `commands_report.py` | none |
| Language model ("brain") | `brain_report.py` | `anthropic`, optionally `cerebras` |
| Fast router (Jev) | `jev_report.py` | `typesafe` |
| Voices | `tts/elevenlabs_tts.py`, `tts/kokoro_tts.py`, `tts/design_voices.py` | `elevenlabs` for the ElevenLabs scripts |

Store a key with `uv run python -m tycho.credentials set <name>`. The tools that read your keybinds look for the game in the default install folder; set `TYCHO_SC_ROOT` to the folder that contains `LIVE` if yours is elsewhere.

## Speech-to-text

Each engine is a self-contained script with its own isolated environment ([PEP 723](https://peps.python.org/pep-0723/)), so engines with conflicting dependencies never meet, and none of them touch Tycho's own dependencies. The first run of each downloads several GB.

Runs speech-to-text engines on clips recorded with `tools/spike/record.py` and compares accuracy and speed.

```powershell
$r = "recordings/<session>"
uv run --script tools/bench/engines/onnx_asr_engine.py $r                     # Parakeet TDT v2, GPU
uv run --script tools/bench/engines/onnx_asr_engine.py $r --model nemo-parakeet-tdt-0.6b-v3
uv run --script tools/bench/engines/onnx_asr_engine.py $r --device cpu
uv run --script tools/bench/engines/faster_whisper_engine.py $r              # Whisper large-v3-turbo
uv run --script tools/bench/engines/qwen_asr_engine.py $r                    # Qwen3-ASR-1.7B
uv run python tools/bench/report.py $r --misses
uv run python tools/bench/commands_report.py $r      # what each transcript would do, with your keys
```

`commands_report.py` scores every transcript against [`tools/spike/expected.toml`](../spike/expected.toml): **right**, **slow** (handed to the language model when the fast path should have caught it) or **wrong** (the wrong action, or acting when it should have asked). Wrong must stay at zero. It then lists the key each command would press with your own keybinds, read from your game install.

Models go to `%LOCALAPPDATA%\tycho\models`. Set `TYCHO_MODELS` to put them elsewhere, and `UV_CACHE_DIR` and `HF_HOME` to move the package and download caches.

## What's measured

- **WER:** word error rate against the phrase you were asked to say, ignoring case and punctuation.
- **exact:** clips transcribed word-for-word.
- **terms:** game words (ArcCorp, mobiGlas, Cutlass and so on) that survived, ignoring spaces. "Arc Corp" counts as a hit, because a fuzzy matcher can recover it; "Art Corp" doesn't.
- **p50 / p90 ms:** time to transcribe a whole clip after release, median of 3 runs. This is an upper bound for streaming engines, which do most of the work while you're still talking.
- **VRAM MB:** rough, from `nvidia-smi` before and after loading. Noisy when the game is running.
- **game:** whether Star Citizen was running, which matters for both speed and VRAM.

Results are written next to the clips, in `recordings/<session>/results/`, and stay local.

## Language model

```powershell
uv run python tools/bench/brain_report.py tools/spike/brain_cases.toml --runs 2 --effort low
uv run python tools/bench/brain_report.py tools/spike/brain_cases.toml --models claude-haiku-5-5 cerebras:gpt-oss-120b
uv run python tools/bench/brain_report.py recordings/<session>   # the recorded phrases, via a speech-to-text engine's transcripts
```

Each request gets your bound actions as a cached briefing. Answers are graded against [`brain_cases.toml`](../spike/brain_cases.toml): the expected kind of answer (actions, look-up, reply), actions that must be pressed, and actions that must never be (a global list covers eject, self-destruct, quantum and jump engage, jettison, respawn and firing). Nothing is pressed. Every run prints its estimated cost.

## Fast router (Jev)

```powershell
uv run python tools/bench/jev_report.py tools/spike/brain_cases.toml --runs 2
```

Asks Jev, in one request per case, for the mode, the control category, the action in each category, whether the request has several parts, and explicit-danger checks. It logs the raw answers and prints how many requests Jev would handle, and how many it gets wrong, at each confidence threshold.

## Voices

```powershell
uv run python tools/bench/tts/elevenlabs_tts.py recordings/voices                      # the stock voice set
uv run python tools/bench/tts/elevenlabs_tts.py recordings/voices --voice name=VOICE_ID --model eleven_v4_turbo
uv run --script tools/bench/tts/kokoro_tts.py recordings/voices --threads 2              # local, free
uv run python tools/bench/tts/design_voices.py recordings/voices/designs                 # needs a paid ElevenLabs plan
```

Lines come from [`voice_lines.txt`](../spike/voice_lines.txt) and [`voice_lines_character.txt`](../spike/voice_lines_character.txt). ElevenLabs costs about half a credit per character on Flash and v4 Turbo; lines already generated are skipped, so a re-run never pays twice.
