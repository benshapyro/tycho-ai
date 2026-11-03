# PyInstaller build for Tycho (#9): one folder, a console app, nothing GPL or LGPL.
#
#   uv sync --group build --no-group gpu
#   uv run pyinstaller packaging/tycho.spec --noconfirm
#
# The result is dist/tycho/tycho.exe with its libraries beside it. What's left out:
# - NVIDIA's CUDA libraries (1.6 GB): `tycho gpu install` downloads them (#81).
# - pysdl2-dll's extra DLLs (SDL_mixer, SDL_image, SDL_ttf and their codecs, some
#   LGPL): joysticks need only SDL2.dll, which is zlib (#79).
# - Development and benchmark packages.

from pathlib import PurePath

from PyInstaller.utils.hooks import collect_data_files

ROOT = PurePath(SPECPATH).parent  # noqa: F821 - PyInstaller defines SPECPATH

datas = [
    *collect_data_files("tycho", includes=["**/*.toml", "**/*.txt", "**/*.md"]),
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
    ],
    excludes=[
        "nvidia",
        "pygame",
        "tkinter",
        "pytest",
        "openai",
        "IPython",
        "matplotlib",
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
    console=True,
    upx=False,  # UPX-packed executables are flagged by antivirus more often
)
coll = COLLECT(exe, a.binaries, a.datas, name="tycho", upx=False)  # noqa: F821
