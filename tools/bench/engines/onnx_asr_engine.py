# /// script
# requires-python = ">=3.14"
# dependencies = [
#     "onnx-asr[hub]==0.12.0",
#     "onnxruntime-gpu[cuda,cudnn]==1.31.1",
#     "numpy",
#     "soundfile",
# ]
# ///
"""NeMo Parakeet models through ONNX Runtime (no PyTorch).

uv run --script tools/bench/engines/onnx_asr_engine.py recordings/<session>
    [--model nemo-parakeet-tdt-0.6b-v3] [--device cpu]
"""

import onnx_asr
import onnxruntime as ort
from _harness import Transcribe, models_dir, parse_args, run
from huggingface_hub import snapshot_download

REPOS = {
    "nemo-parakeet-tdt-0.6b-v2": "istupakov/parakeet-tdt-0.6b-v2-onnx",
    "nemo-parakeet-tdt-0.6b-v3": "istupakov/parakeet-tdt-0.6b-v3-onnx",
}


def main() -> None:
    args = parse_args(default_model="nemo-parakeet-tdt-0.6b-v2")

    def load() -> Transcribe:
        repo = REPOS[args.model]
        path = snapshot_download(
            repo, local_dir=models_dir() / repo, ignore_patterns=["*int8*", "*fp16*"]
        )
        providers = ["CPUExecutionProvider"]
        if args.device == "cuda":
            ort.preload_dlls()  # CUDA and cuDNN come from pip wheels, not a system install
            providers.insert(0, "CUDAExecutionProvider")
        model = onnx_asr.load_model(args.model, path, providers=providers)
        return lambda audio: str(model.recognize(audio, sample_rate=16_000))

    run(args, "onnx-asr", load)


if __name__ == "__main__":
    main()
