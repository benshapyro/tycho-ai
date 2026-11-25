"""The installed app's entry point: the same as `uv run tycho`.

The build makes two programs from it (#146): tycho.exe, for the terminal (`tycho
check`, `tycho bind`, and agents), and tycho-app.exe, the Start menu's Tycho, which has
no terminal. With no terminal, Windows gives Python no stdout or stderr, so this one
runs `tycho run` with what it prints kept in memory (only the end of it), and shows
that end in a message box if Tycho stops with a problem.
"""

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
    # The registry of built-in parts, and Star Citizen's code its builders import when
    # a pack is loaded or a brain is built (ADR 19).
    "tycho.games",
    "tycho.games.star_citizen.checks",
    "tycho.games.star_citizen.fleet",
    "tycho.games.star_citizen.gamelog",
    "tycho.games.star_citizen.item_lookup",
    "tycho.games.star_citizen.mining",
    "tycho.games.star_citizen.ship_lookup",
    "tycho.games.star_citizen.store",
    "tycho.games.star_citizen.trade_lookup",
    # The Tile (ADR 16): drawn with Pillow, whose text needs its FreeType module.
    "PIL._imagingft",
    "tycho.tile.display",
    # The tray icon and the voice loop's own thread (ADR 17).
    "tycho.desktop.host",
    "tycho.desktop.menu",
    "tycho.desktop.tray",
    # The app window (ADR 17): pywebview, and pythonnet under it, which starts .NET.
    "tycho.desktop.window",
    "tycho.desktop.bridge",
    "webview",
    # Looking at the game window (#31), in a child process (#558).
    "tycho.eyes.child",
    "tycho.eyes.windows",
    # Reading the star map's words to plot a route (#588), in the same child process.
    "tycho.eyes.ocr",
    "winrt.windows.globalization",
    "winrt.windows.media.ocr",
    "winrt.runtime",
    "winrt.windows.foundation",
    "winrt.windows.graphics",
    "winrt.windows.graphics.capture",
    "winrt.windows.graphics.capture.interop",
    "winrt.windows.graphics.directx",
    "winrt.windows.graphics.directx.direct3d11",
    "winrt.windows.graphics.directx.direct3d11.interop",
    "winrt.windows.graphics.imaging",
    "winrt.windows.storage.streams",
    "clr",
)


def smoke_imports() -> int:
    import importlib

    for name in LAZY_IMPORTS:
        importlib.import_module(name)
    print(f"imported {len(LAZY_IMPORTS)} modules")
    packs = smoke_packs()
    if not packs:
        print("no game packs in the build")
        return 1
    print(f"loaded {len(packs)} game packs: {', '.join(packs)}")
    return 0


def smoke_packs() -> list[str]:
    """Every game pack that ships with Tycho, loaded as the frozen app reads them (with
    ``importlib.resources``), so a pack file left out of the build fails CI (#320)."""
    from tycho.packs import built_in, installed

    return [f"{pack.id} {pack.version}" for pack in map(built_in, installed())]


class _Tail:
    """A stand-in for stdout and stderr that keeps only what was written last."""

    KEEP = 4000

    def __init__(self) -> None:
        self.text = ""

    def write(self, text: str) -> int:
        self.text = (self.text + text)[-self.KEEP :]
        return len(text)

    def flush(self) -> None:
        pass

    def isatty(self) -> bool:
        return False


def _last_lines(text: str) -> list[str]:
    return [line for line in text.splitlines() if line.strip()][-6:]


def windowed(argv: list[str]) -> int:
    """tycho-app.exe: Tycho with no terminal. A problem that stops it is shown in a
    message box, since nothing else would show it."""
    import contextlib
    import ctypes

    from tycho import cli, logs, paths
    from tycho.start import run as started

    started.WINDOWED = True
    tail = _Tail()
    sys.stdout = sys.stderr = tail  # type: ignore[assignment]
    try:
        code = cli.main(argv or ["run"])
    except Exception as e:  # the player must see something, and the log the traceback
        import logging

        tail.write(f"\nTycho stopped: {e}")
        lines = _last_lines(tail.text)  # before logging, which writes the traceback here too
        if not logging.getLogger("tycho").handlers:  # it stopped before logging began
            with contextlib.suppress(OSError):  # if logging was what failed, the box still shows
                logs.setup()
        logging.getLogger("tycho").exception("Tycho stopped")
        from tycho import crash_reports

        crash_reports.keep_crash(e)  # only if the player said yes (ADR 4, #429)
        code = 1
    else:
        lines = _last_lines(tail.text)
    if code != 0:
        message = "\n".join(lines) or "Tycho stopped."
        log = paths.logs_dir()
        message += f"\n\nThe log is in {log}."
        ctypes.windll.user32.MessageBoxW(None, message, "Tycho", 0x30)  # MB_ICONWARNING
    return code


if __name__ == "__main__":
    multiprocessing.freeze_support()  # harmless if unused; required if anything spawns
    if sys.argv[1:] == ["--smoke-imports"]:
        sys.exit(smoke_imports())
    from tycho.eyes import child

    if sys.argv[1:2] == [child.FLAG]:  # one picture of the game window, for Tycho (#558)
        sys.exit(child.main(sys.argv[2:]))
    if sys.argv[1:2] == [child.READ_FLAG]:  # the game window's words, for a route (#588)
        sys.exit(child.main_words(sys.argv[2:]))
    if sys.argv[1:2] == [child.LOOP_FLAG]:  # the words again and again, for one route (#661)
        sys.exit(child.main_words_loop(sys.argv[2:]))
    if sys.stdout is None or sys.stderr is None:  # tycho-app.exe: no terminal
        sys.exit(windowed(sys.argv[1:]))
    from tycho.cli import main

    sys.exit(main())
