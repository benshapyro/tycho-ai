"""Reading single files from the game's Data.p4k archive. Read-only.

Data.p4k is a ZIP64 archive of about 1.4 million entries, most compressed with
Zstandard under method number 100 (the standard number is 93). Finding a file
means scanning the central directory, which takes several seconds, so callers
should cache what they read.
"""

import struct
import zlib
from collections.abc import Iterable
from compression import zstd
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

_CHUNK = 16 * 2**20
# Central directory record: signature, versions, flags, method, time/date/CRC,
# sizes, name/extra/comment lengths, disk/attributes, local header offset.
_CENTRAL = struct.Struct("<4s4xHH8xIIHHH8xI")  # 46 bytes
_LOCAL = struct.Struct("<4s22xHH")  # 30 bytes
_UNSET32 = 0xFFFFFFFF


@dataclass(frozen=True, slots=True)
class Entry:
    name: str
    method: int
    compressed_size: int
    size: int
    header_offset: int


class P4kArchive:
    def __init__(self, path: Path) -> None:
        self.path = path

    def find(self, names: Iterable[str]) -> dict[str, Entry]:
        """Look up entries by path, ignoring case and slash direction, in one scan."""
        wanted = {_key(n): n for n in names}
        found: dict[str, Entry] = {}
        with self.path.open("rb") as f:
            cd_offset, cd_size = _central_directory(f)
            f.seek(cd_offset)
            buffer, remaining = b"", cd_size
            while remaining or buffer:
                if remaining and len(buffer) < _CHUNK:
                    chunk = f.read(min(_CHUNK, remaining))
                    remaining -= len(chunk)
                    buffer += chunk
                pos = 0
                while pos + _CENTRAL.size <= len(buffer):
                    sig, _, method, csize, usize, nlen, elen, clen, offset = _CENTRAL.unpack_from(
                        buffer, pos
                    )
                    end = pos + _CENTRAL.size + nlen + elen + clen
                    if sig != b"PK\x01\x02":
                        raise ValueError(f"{self.path}: corrupt central directory")
                    if end > len(buffer):
                        break
                    name = buffer[pos + _CENTRAL.size : pos + _CENTRAL.size + nlen]
                    key = name.decode("utf-8", "replace").lower().replace("/", "\\")
                    if key in wanted:
                        extra = buffer[
                            pos + _CENTRAL.size + nlen : pos + _CENTRAL.size + nlen + elen
                        ]
                        usize, csize, offset = _zip64(extra, usize, csize, offset)
                        found[wanted[key]] = Entry(name.decode(), method, csize, usize, offset)
                        if len(found) == len(wanted):
                            return found
                    pos = end
                buffer = buffer[pos:]
                if not remaining and pos == 0:
                    break
        return found

    def read(self, entry: Entry) -> bytes:
        with self.path.open("rb") as f:
            f.seek(entry.header_offset)
            sig, nlen, elen = _LOCAL.unpack(f.read(_LOCAL.size))
            if sig != b"PK\x03\x04":
                raise ValueError(f"{self.path}: bad local header for {entry.name}")
            f.seek(entry.header_offset + _LOCAL.size + nlen + elen)
            data = f.read(entry.compressed_size)
        match entry.method:
            case 0:  # stored
                return data
            case 8:  # deflate
                return zlib.decompress(data, -15)
            case 93 | 100:  # Zstandard: standard number, and the game's own
                return zstd.decompress(data)
            case other:
                raise ValueError(f"{entry.name}: unsupported compression method {other}")

    def read_file(self, name: str) -> bytes:
        entry = self.find([name]).get(name)
        if entry is None:
            raise FileNotFoundError(f"{name} not found in {self.path}")
        return self.read(entry)


def _key(name: str) -> str:
    return name.lower().replace("/", "\\")


def _central_directory(f: BinaryIO) -> tuple[int, int]:
    """Return the central directory's offset and size, from the (ZIP64) end records."""
    f.seek(0, 2)
    size = f.tell()
    f.seek(max(0, size - 65_557))
    tail = f.read()
    locator = tail.rfind(b"PK\x06\x07")
    if locator >= 0:
        (eocd64,) = struct.unpack_from("<Q", tail, locator + 8)
        f.seek(eocd64)
        record = f.read(56)
        if record[:4] != b"PK\x06\x06":
            raise ValueError("bad ZIP64 end record")
        cd_size, cd_offset = struct.unpack_from("<QQ", record, 40)
        return cd_offset, cd_size
    eocd = tail.rfind(b"PK\x05\x06")
    if eocd < 0:
        raise ValueError("not a zip archive")
    cd_size, cd_offset = struct.unpack_from("<II", tail, eocd + 12)
    return cd_offset, cd_size


def _zip64(extra: bytes, usize: int, csize: int, offset: int) -> tuple[int, int, int]:
    """Replace 0xFFFFFFFF placeholders with values from the ZIP64 extra field."""
    pos = 0
    while pos + 4 <= len(extra):
        tag, length = struct.unpack_from("<HH", extra, pos)
        if tag == 0x0001:
            field = pos + 4
            if usize == _UNSET32:
                (usize,) = struct.unpack_from("<Q", extra, field)
                field += 8
            if csize == _UNSET32:
                (csize,) = struct.unpack_from("<Q", extra, field)
                field += 8
            if offset == _UNSET32:
                (offset,) = struct.unpack_from("<Q", extra, field)
            break
        pos += 4 + length
    return usize, csize, offset
