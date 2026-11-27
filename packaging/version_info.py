"""The Windows version details on tycho.exe and tycho-app.exe (#9).

SignPath signs only files whose product name and version are set, and the same in every
file of one build (its signing policy checks them), so the spec gives both programs
these, from Tycho's own version. The installer sets the same in packaging/tycho.iss.

Loaded by path from packaging/tycho.spec (a folder named ``packaging`` can't be
imported: the PyPI package of that name wins), and by tests/test_installer.py.
"""

import re

PRODUCT = "Tycho"
COPYRIGHT = "Copyright 2026 Ben Shapiro and Tycho contributors"


def numbers(version: str) -> tuple[int, int, int, int]:
    """Windows' four numbers for a version: ``0.11.0`` is (0, 11, 0, 0), and a
    pre-release's suffix is dropped (``0.12.0rc1`` is (0, 12, 0, 0))."""
    head = re.match(r"\d+(\.\d+)*", version)
    if head is None:
        raise ValueError(f"not a version: {version!r}")
    parts = [int(n) for n in head.group(0).split(".")[:4]]
    parts += [0] * (4 - len(parts))
    return (parts[0], parts[1], parts[2], parts[3])


def strings(version: str, filename: str, description: str) -> dict[str, str]:
    """The text details Windows shows in a file's Properties, and SignPath checks."""
    return {
        "CompanyName": "Tycho contributors",
        "FileDescription": description,
        "FileVersion": version,
        "InternalName": filename.removesuffix(".exe"),
        "LegalCopyright": COPYRIGHT,
        "OriginalFilename": filename,
        "ProductName": PRODUCT,
        "ProductVersion": version,
    }


def version_info(version: str, filename: str, description: str) -> object:
    """PyInstaller's version resource for one program (PyInstaller is only in the
    build group, so it's imported here, not at the top)."""
    from PyInstaller.utils.win32.versioninfo import (
        FixedFileInfo,
        StringFileInfo,
        StringStruct,
        StringTable,
        VarFileInfo,
        VarStruct,
        VSVersionInfo,
    )

    four = numbers(version)
    return VSVersionInfo(
        ffi=FixedFileInfo(filevers=four, prodvers=four),
        kids=[
            StringFileInfo(
                [
                    StringTable(
                        "040904B0",  # US English, Unicode
                        [
                            StringStruct(k, v)
                            for k, v in strings(version, filename, description).items()
                        ],
                    )
                ]
            ),
            VarFileInfo([VarStruct("Translation", [0x0409, 1200])]),
        ],
    )
