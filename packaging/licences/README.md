# Licence texts for the parts of the build that aren't Python packages

`tools/notices.py` puts these into THIRD-PARTY-NOTICES.txt in full: MIT and BSD
licences need the notice itself in every copy, not a link.

- `python.txt`: CPython's `Doc/license.rst` (3.14 branch), from
  https://github.com/python/cpython/blob/3.14/Doc/license.rst. The PSF licence, and
  the licences of what's built into the Python runtime: OpenSSL, libffi, expat,
  libmpdec, zlib, bzip2, Zstandard and others.
- `portaudio.txt`: PortAudio's `LICENSE.txt`, from
  https://github.com/PortAudio/portaudio/blob/master/LICENSE.txt (sounddevice's
  `libportaudio64bit.dll`).

- `webview2.txt`: the Microsoft WebView2 SDK's `LICENSE.txt`, from the
  Microsoft.Web.WebView2 1.0.3856.49 NuGet package (the version pywebview 6.2.1 ships:
  `Microsoft.Web.WebView2.Core.dll` and the loader DLLs).

OpenSSL 3 is Apache-2.0, the same text as Tycho's LICENSE, which the notices include.
Refresh these when Python's minor version or a bundled native library changes.
