"""Minimal read-only big-endian NBT parser, just enough to read world save files."""

from __future__ import annotations

import gzip
import struct
from pathlib import Path
from typing import Any

_I16 = struct.Struct(">h")
_I32 = struct.Struct(">i")
_I64 = struct.Struct(">q")
_F32 = struct.Struct(">f")
_F64 = struct.Struct(">d")


class _Reader:
    __slots__ = ("data", "pos")

    def __init__(self, data: bytes):
        self.data = data
        self.pos = 0

    def read(self, size: int) -> bytes:
        chunk = self.data[self.pos : self.pos + size]
        if len(chunk) != size:
            raise ValueError("Unexpected end of NBT data")
        self.pos += size
        return chunk

    def u8(self) -> int:
        return self.read(1)[0]

    def string(self) -> str:
        (length,) = struct.unpack(">H", self.read(2))
        return self.read(length).decode("utf-8", errors="replace")


def _read_payload(reader: _Reader, tag_type: int) -> Any:
    if tag_type == 1:
        return reader.u8()
    if tag_type == 2:
        return _I16.unpack(reader.read(2))[0]
    if tag_type == 3:
        return _I32.unpack(reader.read(4))[0]
    if tag_type == 4:
        return _I64.unpack(reader.read(8))[0]
    if tag_type == 5:
        return _F32.unpack(reader.read(4))[0]
    if tag_type == 6:
        return _F64.unpack(reader.read(8))[0]
    if tag_type == 7:
        (length,) = _I32.unpack(reader.read(4))
        return list(reader.read(length))
    if tag_type == 8:
        return reader.string()
    if tag_type == 9:
        element_type = reader.u8()
        (length,) = _I32.unpack(reader.read(4))
        return [_read_payload(reader, element_type) for _ in range(length)]
    if tag_type == 10:
        result: dict[str, Any] = {}
        while True:
            child_type = reader.u8()
            if child_type == 0:
                return result
            name = reader.string()
            result[name] = _read_payload(reader, child_type)
    if tag_type == 11:
        (length,) = _I32.unpack(reader.read(4))
        return [_I32.unpack(reader.read(4))[0] for _ in range(length)]
    if tag_type == 12:
        (length,) = _I32.unpack(reader.read(4))
        return [_I64.unpack(reader.read(8))[0] for _ in range(length)]
    raise ValueError(f"Unsupported NBT tag type {tag_type}")


def load(path: Path) -> dict[str, Any]:
    """Parse a gzip-or-raw big-endian NBT file and return its root compound tag's contents."""
    raw = Path(path).read_bytes()
    try:
        raw = gzip.decompress(raw)
    except OSError:
        pass
    reader = _Reader(raw)
    tag_type = reader.u8()
    reader.string()  # root tag name, unused
    return _read_payload(reader, tag_type)
