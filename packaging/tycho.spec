# PyInstaller build for Tycho (#9): one folder, two programs sharing it (tycho.exe for
# the terminal, tycho-app.exe for the Start menu, #146), nothing GPL or LGPL.
#
#   uv sync --group build --no-group gpu
#   uv run pyinstaller packaging/tycho.spec --noconfirm
#
# The result is dist/tycho/tycho.exe with its libraries beside it. What's left out:
# - NVIDIA's CUDA libraries (1.6 GB): `tycho gpu install` downloads them (#81).
# - pysdl2-dll's extra DLLs (SDL_mixer, SDL_image, SDL_ttf and their codecs, some
#   LGPL): joysticks need only SDL2.dll, which is zlib (#79).
# - Development and benchmark packages.

import importlib.util
from importlib.metadata import version as package_version
from pathlib import PurePath

from PyInstaller.utils.hooks import collect_data_files, collect_submodules, copy_metadata

ROOT = PurePath(SPECPATH).parent  # noqa: F821 - PyInstaller defines SPECPATH

# The product name and version on both programs, which SignPath checks before it signs
# (#9). Loaded by path: `import packaging` would find the PyPI package.
_spec = importlib.util.spec_from_file_location(
    "tycho_version_info", str(ROOT / "packaging" / "version_info.py")
)
assert _spec is not None and _spec.loader is not None
version_info = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(version_info)
VERSION = package_version("tycho-ai")

datas = [
    # Package metadata isn't bundled unless asked for. onnx-asr reads its own version
    # when imported, so without this the speech model can't load; Tycho's gives
    # `tycho --version` the real version.
    *copy_metadata("onnx-asr"),
    *copy_metadata("tycho-ai"),
    # The app window's page (ADR 17) is HTML, CSS and JavaScript; the window and the Tile
    # draw with the bundled fonts (tycho/fonts, SIL OFL).
    *collect_data_files(
        "tycho",
        includes=["**/*.toml", "**/*.txt", "**/*.md", "**/*.html", "**/*.css", "**/*.js", "**/*.ttf"],
        # Guides for coding agents, and the packs' eval cases: not for players' installs.
        excludes=["**/AGENTS.md", "packs/*/eval/*"],
    ),
    *collect_data_files("onnx_asr", includes=["preprocessors/data/*"]),
    *collect_data_files("sdl2dll", includes=["dll/SDL2.dll"]),
]

a = Analysis(  # noqa: F821
    [str(ROOT / "packaging" / "tycho_main.py")],
    pathex=[str(ROOT / "src")],
    datas=datas,
    hiddenimports=[
        "keyring.backends.Windows",  # chosen explicitly when entry points are lost (#78)
        "tycho.ptt.joystick",  # imported on first use
        "tycho.stt.parakeet",
        "tycho.tile.display",  # the Tile, opened when Tycho runs (ADR 16)
        "tycho.desktop.window",  # the app window and its page (ADR 17)
        "tycho.desktop.bridge",
        "webview",
        # Looking at the game window (#31): the WinRT modules import each other from C,
        # which the analysis can't see (#555). Only the capture's child process loads them.
        "tycho.eyes.child",
        "tycho.eyes.windows",
        "tycho.eyes.ocr",
        *collect_submodules("winrt"),
    ],
    excludes=[
        "nvidia",
        "pygame",
        "tkinter",
        "pytest",
        "_pytest",  # pytest's code, pulled in through hooks when the dev group is installed
        "pygments",
        "openai",
        "IPython",
        "matplotlib",
        "yaml",  # only numpy.__config__ uses it, to print numpy's build settings
        "setuptools",
        "pkg_resources",
    ],
    noarchive=False,
)

def kept(entry: tuple[str, str, str]) -> bool:
    """Leave out every CUDA library, every pysdl2-dll DLL but SDL2.dll, and TensorRT,
    which Tycho never uses (the CUDA provider stays: it's how `tycho gpu install` works)."""
    parts = entry[0].replace("\\", "/").lower().split("/")
    if "nvidia" in parts:
        return False
    if "sdl2dll" in parts and parts[-1].endswith(".dll"):
        return parts[-1] == "sdl2.dll"
    if "portaudio-binaries" in parts and parts[-1].startswith("libportaudio"):
        # Only the 64-bit build sounddevice loads; not the ASIO ones (Steinberg's ASIO is
        # GPLv3 or proprietary), 32-bit, ARM or macOS.
        return parts[-1] == "libportaudio64bit.dll"
    return parts[-1] != "onnxruntime_providers_tensorrt.dll"


a.binaries = [b for b in a.binaries if kept(b)]
a.datas = [d for d in a.datas if kept(d)]

pyz = PYZ(a.pure)  # noqa: F821
exe = EXE(  # noqa: F821
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="tycho",
    version=version_info.version_info(VERSION, "tycho.exe", "Tycho (terminal)"),
    console=True,  # for the terminal: `tycho check`, `tycho bind`, and agents
    upx=False,  # UPX-packed executables are flagged by antivirus more often
)
app = EXE(  # noqa: F821
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="tycho-app",
    version=version_info.version_info(VERSION, "tycho-app.exe", "Tycho"),
    console=False,  # the Start menu's Tycho: the tray icon and the window, no terminal (#146)
    upx=False,
)
coll = COLLECT(exe, app, a.binaries, a.datas, name="tycho", upx=False)  # noqa: F821
