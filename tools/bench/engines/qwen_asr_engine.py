# /// script
# requires-python = ">=3.14"
# dependencies = [
#     "qwen-asr==0.0.6",
#     "torch==2.15.0",
#     "numpy",
#     "soundfile",
# ]
#
# [tool.uv.sources]
# torch = { index = "pytorch-cu132" }
#
# [[tool.uv.index]]
# name = "pytorch-cu132"
# url = "https://download.pytorch.org/whl/cu132"
# explicit = true
# ///
"""Qwen3-ASR through its transformers backend.

uv run --script tools/bench/engines/qwen_asr_engine.py recordings/<session>
    [--model Qwen/Qwen3-ASR-0.6B] [--device cpu]
"""

import numpy as np
import torch
from _harness import Transcribe, models_dir, parse_args, run
from huggingface_hub import snapshot_download
from qwen_asr import Qwen3ASRModel


def main() -> None:
    args = parse_args(default_model="Qwen/Qwen3-ASR-1.7B")

    def load() -> Transcribe:
        path = snapshot_download(args.model, local_dir=models_dir() / args.model)
        model = Qwen3ASRModel.from_pretrained(
            path,
            dtype=torch.bfloat16 if args.device == "cuda" else torch.float32,
            device_map="cuda:0" if args.device == "cuda" else "cpu",
            max_new_tokens=128,
        )

        def transcribe(audio: np.ndarray) -> str:
            return model.transcribe(audio=(audio, 16_000), language="English")[0].text

        return transcribe

    run(args, "qwen-asr", load)


if __name__ == "__main__":
    main()
