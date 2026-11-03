# /// script
# requires-python = ">=3.14"
# dependencies = [
#     "onnx-asr==0.12.0",
#     "onnxruntime-directml==1.24.4",
#     "numpy",
#     "soundfile",
# ]
# ///
"""Parakeet through ONNX Runtime's DirectML build, which runs on NVIDIA, AMD and
Intel graphics cards: is it fast enough to be the default (#56)?

Uses the model Tycho already downloaded. Compare with the CUDA build:

uv run --script tools/bench/engines/onnx_asr_directml_engine.py recordings/<session>
uv run --script tools/bench/engines/onnx_asr_engine.py recordings/<session> --device cuda
"""

import onnx_asr
import onnxruntime as ort
from _harness import Transcribe, models_dir, parse_args, run

MODEL = "nemo-parakeet-tdt-0.6b-v2"
FOLDER = "istupakov/parakeet-tdt-0.6b-v2-onnx"


def main() -> None:
    args = parse_args(default_model=MODEL)

    def load() -> Transcribe:
        providers = ["CPUExecutionProvider"]
        if args.device != "cpu":
            providers.insert(0, "DmlExecutionProvider")
        model = onnx_asr.load_model(MODEL, models_dir() / FOLDER, providers=providers)
        session = model.asr._encoder  # pyright: ignore[reportAttributeAccessIssue] - onnx-asr internal
        print(f"ONNX Runtime {ort.__version__}, encoder on {session.get_providers()[0]}")
        return lambda audio: str(model.recognize(audio, sample_rate=16_000))

    if args.device == "cuda":  # the harness's default: here it means the graphics card
        args.device = "directml"
    run(args, "onnx-asr", load)


if __name__ == "__main__":
    main()
