"""The installed app's entry point: the same as `uv run tycho`."""

import multiprocessing
import sys

# What Tycho imports only when it needs it (the speech model, joysticks, the key
# store). The build's smoke test imports each, so anything missing from the build
# fails CI instead of the player's first start.
LAZY_IMPORTS = (
    "onnx_asr",
    "onnxruntime",
    "sdl2",
    "keyring.backends.Windows",
    "tycho.stt.parakeet",
    "tycho.ptt.joystick",
    # sounddevice loads PortAudio when imported, and the spec picks its DLLs by hand.
    "sounddevice",
    "tycho.audio.microphone",
    "tycho.feedback",
    "tycho.voice.playback",
    "anthropic",
    "tycho.brain.claude",
    # The Tile (ADR 16): drawn with Pillow, whose text needs its FreeType module.
    "PIL._imagingft",
    "tycho.tile.display",
    # The tray icon and the voice loop's own thread (ADR 17).
    "tycho.desktop.host",
    "tycho.desktop.tray",
)


def smoke_imports() -> int:
    import importlib

    for name in LAZY_IMPORTS:
        importlib.import_module(name)
    print(f"imported {len(LAZY_IMPORTS)} modules")
    return 0


if __name__ == "__main__":
    multiprocessing.freeze_support()  # harmless if unused; required if anything spawns
    if sys.argv[1:] == ["--smoke-imports"]:
        sys.exit(smoke_imports())
    from tycho.app import main

    sys.exit(main())
