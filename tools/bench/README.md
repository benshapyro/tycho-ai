# Speech-to-text benchmark

Part of the latency spike ([issue #1](https://github.com/benshapyro/ultimo/issues/1)). Runs speech-to-text engines on clips recorded with `tools/spike/record.py` and compares accuracy and speed.

## Run

Each engine is a self-contained script with its own isolated environment ([PEP 723](https://peps.python.org/pep-0723/)), so engines with conflicting dependencies never meet, and none of them touch Ultimo's own dependencies. The first run of each downloads several GB.

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

Models go to `%LOCALAPPDATA%\ultimo\models`. Set `ULTIMO_MODELS` to put them elsewhere, and `UV_CACHE_DIR` and `HF_HOME` to move the package and download caches.

## What's measured

- **WER:** word error rate against the phrase you were asked to say, ignoring case and punctuation.
- **exact:** clips transcribed word-for-word.
- **terms:** game words (ArcCorp, mobiGlas, Cutlass and so on) that survived, ignoring spaces. "Arc Corp" counts as a hit, because a fuzzy matcher can recover it; "Art Corp" doesn't.
- **p50 / p90 ms:** time to transcribe a whole clip after release, median of 3 runs. This is an upper bound for streaming engines, which do most of the work while you're still talking.
- **VRAM MB:** rough, from `nvidia-smi` before and after loading. Noisy when the game is running.
- **game:** whether Star Citizen was running, which matters for both speed and VRAM.

Results are written next to the clips, in `recordings/<session>/results/`, and stay local.
