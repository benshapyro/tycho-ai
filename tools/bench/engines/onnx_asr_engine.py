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
    [--model nemo-parakeet-tdt-0.6b-v3] [--device cpu] [--int8]

--int8 loads the 8-bit copy of the model, about a quarter of the size (#188).
"""

import sys

import onnx_asr
import onnxruntime as ort
from _harness import Transcribe, models_dir, parse_args, run
from huggingface_hub import snapshot_download

REPOS = {
    "nemo-parakeet-tdt-0.6b-v2": "istupakov/parakeet-tdt-0.6b-v2-onnx",
    "nemo-parakeet-tdt-0.6b-v3": "istupakov/parakeet-tdt-0.6b-v3-onnx",
}


def main() -> None:
    int8 = "--int8" in sys.argv
    if int8:
        sys.argv.remove("--int8")
    args = parse_args(default_model="nemo-parakeet-tdt-0.6b-v2")
    if int8 and not args.label:
        args.label = f"onnx-asr:{args.model}-int8:{args.device}"

    def load() -> Transcribe:
        repo = REPOS[args.model]
        skip = ["*fp16*"] if int8 else ["*int8*", "*fp16*"]
        path = snapshot_download(repo, local_dir=models_dir() / repo, ignore_patterns=skip)
        providers = ["CPUExecutionProvider"]
        if args.device == "cuda":
            ort.preload_dlls()  # CUDA and cuDNN come from pip wheels, not a system install
            providers.insert(0, "CUDAExecutionProvider")
        model = onnx_asr.load_model(
            args.model, path, quantization="int8" if int8 else None, providers=providers
        )
        return lambda audio: str(model.recognize(audio, sample_rate=16_000))

    run(args, "onnx-asr", load)


if __name__ == "__main__":
    main()
