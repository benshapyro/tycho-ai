"""Reading single files from the game's Data.p4k archive. Read-only.

Data.p4k is a ZIP64 archive of about 1.4 million entries, most compressed with
Zstandard under method number 100 (the standard number is 93). Finding a file
means scanning the central directory, which takes several seconds, so callers
should cache what they read.

The archive may be mid-patch or damaged, so every malformed read raises
ValueError rather than hanging or failing deep inside struct.
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
_EOCD_SIZE = 22
_LOCATOR_SIZE = 20
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
        try:
            return self._find({_key(n): n for n in names})
        except struct.error as e:
            raise ValueError(f"{self.path}: corrupt archive ({e})") from e

    def _find(self, wanted: dict[str, str]) -> dict[str, Entry]:
        found: dict[str, Entry] = {}
        with self.path.open("rb") as f:
            cd_offset, cd_size = _central_directory(f)
            f.seek(cd_offset)
            buffer, remaining = b"", cd_size
            while remaining or buffer:
                if remaining and len(buffer) < _CHUNK:
                    chunk = f.read(min(_CHUNK, remaining))
                    if not chunk:
                        raise ValueError(f"{self.path}: central directory is truncated")
                    remaining -= len(chunk)
                    buffer += chunk
                pos = 0
                while pos + _CENTRAL.size <= len(buffer):
                    sig, _, method, csize, usize, nlen, elen, clen, offset = _CENTRAL.unpack_from(
                        buffer, pos
                    )
                    if sig != b"PK\x01\x02":
                        raise ValueError(f"{self.path}: corrupt central directory")
                    end = pos + _CENTRAL.size + nlen + elen + clen
                    if end > len(buffer):
                        break
                    name = buffer[pos + _CENTRAL.size : pos + _CENTRAL.size + nlen]
                    key = name.decode("utf-8", "replace").lower().replace("/", "\\")
                    if key in wanted:
                        start = pos + _CENTRAL.size + nlen
                        usize, csize, offset = _zip64(
                            buffer[start : start + elen], usize, csize, offset
                        )
                        found[wanted[key]] = Entry(name.decode(), method, csize, usize, offset)
                        if len(found) == len(wanted):
                            return found
                    pos = end
                buffer = buffer[pos:]
                if not remaining and buffer:
                    if pos == 0:  # the directory ends partway through a record
                        raise ValueError(f"{self.path}: central directory is truncated")
                elif pos == 0 and len(buffer) >= _CHUNK:  # a record can't be this large
                    raise ValueError(f"{self.path}: corrupt central directory")
        return found

    def read(self, entry: Entry) -> bytes:
        with self.path.open("rb") as f:
            f.seek(entry.header_offset)
            header = f.read(_LOCAL.size)
            if len(header) < _LOCAL.size:
                raise ValueError(f"{self.path}: truncated local header for {entry.name}")
            sig, nlen, elen = _LOCAL.unpack(header)
            if sig != b"PK\x03\x04":
                raise ValueError(f"{self.path}: bad local header for {entry.name}")
            f.seek(entry.header_offset + _LOCAL.size + nlen + elen)
            data = f.read(entry.compressed_size)
        if len(data) != entry.compressed_size:
            raise ValueError(f"{self.path}: {entry.name} is truncated")
        try:
            match entry.method:
                case 0:  # stored
                    return data
                case 8:  # deflate
                    return zlib.decompress(data, -15)
                case 93 | 100:  # Zstandard: standard number, and the game's own
                    return zstd.decompress(data)
                case other:
                    raise ValueError(f"{entry.name}: unsupported compression method {other}")
        except (zlib.error, zstd.ZstdError) as e:
            raise ValueError(f"{self.path}: {entry.name} is corrupt ({e})") from e

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
    start = max(0, size - 65_557)
    f.seek(start)
    tail = f.read()
    eocd = tail.rfind(b"PK\x05\x06")
    if eocd < 0 or eocd + _EOCD_SIZE > len(tail):
        raise ValueError("not a zip archive")
    cd_size, cd_offset = struct.unpack_from("<II", tail, eocd + 12)
    # The ZIP64 locator, when present, sits directly before the end record.
    locator = eocd - _LOCATOR_SIZE
    if locator >= 0 and tail[locator : locator + 4] == b"PK\x06\x07":
        (eocd64,) = struct.unpack_from("<Q", tail, locator + 8)
        f.seek(eocd64)
        record = f.read(56)
        if len(record) < 56 or record[:4] != b"PK\x06\x06":
            raise ValueError("bad ZIP64 end record")
        cd_size, cd_offset = struct.unpack_from("<QQ", record, 40)
    if cd_offset + cd_size > size:
        raise ValueError("central directory runs past the end of the file")
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
