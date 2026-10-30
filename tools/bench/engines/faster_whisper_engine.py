# /// script
# requires-python = ">=3.14"
# dependencies = [
#     "faster-whisper==1.2.1",
#     "nvidia-cublas-cu12",
#     "nvidia-cudnn-cu12==9.*",
#     "numpy",
#     "soundfile",
# ]
# ///
"""Whisper through CTranslate2 (faster-whisper).

uv run --script tools/bench/engines/faster_whisper_engine.py recordings/<session>
    [--model large-v3-turbo] [--device cpu]
"""

import os
import sysconfig
from pathlib import Path

from _harness import Transcribe, models_dir, parse_args, run


def _add_cuda_dlls() -> None:
    """Make the CUDA libraries from the nvidia-* wheels findable on Windows."""
    nvidia = Path(sysconfig.get_paths()["purelib"]) / "nvidia"
    for bin_dir in nvidia.glob("*/bin"):
        os.add_dll_directory(str(bin_dir))
        os.environ["PATH"] = f"{bin_dir}{os.pathsep}{os.environ['PATH']}"


def main() -> None:
    args = parse_args(default_model="large-v3-turbo")

    def load() -> Transcribe:
        if args.device == "cuda":
            _add_cuda_dlls()
        from faster_whisper import WhisperModel

        model = WhisperModel(
            args.model,
            device=args.device,
            compute_type="float16" if args.device == "cuda" else "int8",
            download_root=str(models_dir() / "faster-whisper"),
        )

        def transcribe(audio: object) -> str:
            segments, _ = model.transcribe(
                audio, language="en", beam_size=1, vad_filter=False, without_timestamps=True
            )
            return " ".join(s.text.strip() for s in segments)

        return transcribe

    run(args, "faster-whisper", load)


if __name__ == "__main__":
    main()
