# Benchmarks

Measurement tools from the latency spike ([issue #1](https://github.com/benshapyro/tycho-ai/issues/1)). They are not part of the app. Some call paid APIs with your own keys; each section says which. Results stay in the git-ignored `recordings/` folder.

Install the extra packages these tools need with `uv sync --group bench`.

| Benchmark | Script | Keys |
|---|---|---|
| Speech-to-text | `engines/*.py`, `report.py` | none |
| Fast-path commands | `commands_report.py` | none |
| Speech model's graphics memory | `gpu_memory.py` | none |
| Look-up times (live UEX and Wiki API) | `lookup_times.py` | none |
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

### Graphics memory

```powershell
uv run python tools/bench/gpu_memory.py $r               # with Star Citizen closed
uv run python tools/bench/gpu_memory.py $r --with-game   # with it open, to see it share the card
```

Loads Parakeet on the graphics card with ONNX Runtime's default CUDA settings and with Tycho's lean ones (`LEAN_CUDA` in `src/tycho/stt/parakeet.py`), each in a process of its own, and reports load time, warm latency (p50, p90) and the card's memory in use above where it started. Measured on 2026-11-04 with the game closed: 3.5 GB with the defaults, 3.0 GB lean, the same speed and the same transcripts. A third setting, `split`, also runs the preprocessor and the small decoder on the processor (`SMALL_PARTS_ON_CPU`): 2.8 GB, but slower with the card idle (p50 298 ms against 240). It's off until a run with `--with-game` shows whether it helps when the decoder's many small GPU calls would wait behind the game's frames.

## What's measured

- **WER:** word error rate against the phrase you were asked to say, ignoring case and punctuation.
- **exact:** clips transcribed word-for-word.
- **terms:** game words (ArcCorp, mobiGlas, Cutlass and so on) that survived, ignoring spaces. "Arc Corp" counts as a hit, because a fuzzy matcher can recover it; "Art Corp" doesn't.
- **p50 / p90 ms:** time to transcribe a whole clip after release, median of 3 runs. This is an upper bound for streaming engines, which do most of the work while you're still talking.
- **VRAM MB:** rough, from `nvidia-smi` before and after loading. Noisy when the game is running.
- **game:** whether Star Citizen was running, which matters for both speed and VRAM.

Results are written next to the clips, in `recordings/<session>/results/`, and stay local.

## The product brain (#45)

```powershell
uv run python tools/bench/tycho_eval.py                 # every case in tools/spike/brain_cases.toml
uv run python tools/bench/tycho_eval.py --tag log       # only the cases from play logs
uv run python tools/bench/tycho_eval.py --provider openrouter --model deepseek/deepseek-v4
uv run python tools/bench/tycho_eval.py --adapter-check  # Haiku directly, then through OpenRouter
```

Runs each case through Tycho's own path: the lexicon, the fast path, then `ClaudeBrain` and `commands_for`, with your real keybinds. Grades what Tycho would press (after "confirm"), fails any dangerous press that wouldn't be asked about first, and fails answers to questions marked `answer = true` that only point elsewhere ("check the wiki"). The bar is zero wrong presses and Claude's picks at p50 <= 1.5 s. It also grades how each answer was reached (#148): every request's calls to Claude (about 1-1.5 s each), the look-ups it used, and any that came back as errors, which Claude tends to retry. A case can set `max_calls`, `tools`, `no_tools` and `no_errors`, and the summary gives the mean calls a request. About 2 cents a run. Add a case whenever a play log shows a miss.

Across models (#192): `--provider openrouter --model <id>` runs the same cases through `OpenRouterBrain`, with the OpenRouter key (`uv run tycho key set openrouter`). It has no web search, so web cases fail as such. Each results line names the provider and model, and the run ends with a `summary` line. Cost comes from OpenRouter's own report for each call (an act ends its call early, so its cost is looked up afterwards by the generation's id), or from OpenRouter's price list when a reply reports none; the summary says how many came from where. Cost an hour of play is the cost a brain request times `--per-hour` (30 brain requests an hour, a placeholder until play logs measure it). `--adapter-check` runs Haiku directly and through OpenRouter with no web search and compares them: far apart means the adapter is wrong, not the model. Paid: ask first.

Off the PC, in a cloud thread (#262): export the keybinds once on the PC, for free, then point the eval at the file. The export holds your bound actions, the game's activation modes and your own `commands.toml`, and nothing secret.

```powershell
uv run python tools/bench/tycho_eval.py --export-keybinds recordings/keybinds.json   # on the PC
uv run python tools/bench/tycho_eval.py --keybinds keybinds.json --adapter-check      # anywhere
```

Where Credential Manager has no key, the eval takes `ANTHROPIC_API_KEY` and `OPENROUTER_API_KEY` from the environment (the cloud environment's secrets). Only the eval does this: Tycho itself keeps keys in Credential Manager. Keys are never printed. The Wiki API (`api.star-citizen.wiki`), UEX (`api.uexcorp.uk`) and `openrouter.ai` must be allowed in the cloud environment's network access. A cost OpenRouter never reports is counted in the summary ("N call(s) had no cost reported") rather than dropped (#261).

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

Since [#24](https://github.com/benshapyro/tycho-ai/issues/24), each action is described by what it does, what it isn't for and how it's said, with an "other" way out, and every choice's options come in an order shuffled from `--seed` (`--no-shuffle` for a fixed order). The report gives Jev's mode accuracy against each case's `mode` label (ADR 10), how often it is confident and wrong, the non-commands it called a command, how often it picks whichever option came first, and latency p50 and p90 for the router ([#193](https://github.com/benshapyro/tycho-ai/issues/193)). Re-grade a log without new requests with `--replay recordings/jev.results.jsonl`. For the re-test, do two runs with different seeds.

## Voices

```powershell
uv run python tools/bench/tts/elevenlabs_tts.py recordings/voices                      # the stock voice set
uv run python tools/bench/tts/elevenlabs_tts.py recordings/voices --voice name=VOICE_ID --model eleven_v4_turbo
uv run --script tools/bench/tts/kokoro_tts.py recordings/voices --threads 2              # local, free
uv run python tools/bench/tts/design_voices.py recordings/voices/designs                 # needs a paid ElevenLabs plan
```

Lines come from [`voice_lines.txt`](../spike/voice_lines.txt) and [`voice_lines_character.txt`](../spike/voice_lines_character.txt). ElevenLabs costs about half a credit per character on Flash and v4 Turbo; lines already generated are skipped, so a re-run never pays twice.
