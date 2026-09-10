from __future__ import annotations

import struct
from dataclasses import dataclass
from pathlib import Path

@dataclass(frozen=True)
class PakResource:
    resource_id: int
    offset: int
    size: int

    @property
    def is_png(self) -> bool:
        return self.size >= 8 and self.data[:8].startswith(b"\x89PNG\r\n\x1a\n")

    @property
    def is_html(self) -> bool:
        return is_html_payload(self.data)

    data: bytes = b""

def is_html_payload(data: bytes) -> bool:
    if not data:
        return False
    head = data.lstrip(b"\xef\xbb\xbf").lstrip()[:256].lower()
    return head.startswith(b"<!doctype html") or head.startswith(b"<html")

class ChromiumPak:

    def __init__(self, path: Path) -> None:
        self.path = path
        self.version, self.encoding, self._entries = self._parse_index(path.read_bytes())

    @staticmethod
    def _parse_index(data: bytes) -> tuple[int, int, list[tuple[int, int]]]:
        if len(data) < 9:
            raise ValueError("pak too small")
        version, count = struct.unpack_from("<II", data, 0)
        if version not in (4, 5):
            raise ValueError(f"unsupported pak version {version}")
        encoding = data[8]
        base = 9
        need = base + count * 6
        if len(data) < need:
            raise ValueError("pak index truncated")
        entries: list[tuple[int, int]] = []
        for i in range(count):
            off = base + i * 6
            rid, file_off = struct.unpack_from("<HI", data, off)
            entries.append((rid, file_off))
        return version, encoding, entries

    @property
    def resource_count(self) -> int:
        return len(self._entries)

    def resource_at(self, index: int) -> PakResource:
        rid, start = self._entries[index]
        data = self.path.read_bytes()
        if index + 1 < len(self._entries):
            end = self._entries[index + 1][1]
        else:
            end = len(data)
        chunk = data[start:end]
        return PakResource(resource_id=rid, offset=start, size=len(chunk), data=chunk)

    def find_png_resources(self) -> list[PakResource]:
        out: list[PakResource] = []
        for i in range(len(self._entries)):
            res = self.resource_at(i)
            if res.is_png:
                out.append(res)
        return out

    def find_html_resources(self) -> list[PakResource]:
        out: list[PakResource] = []
        for i in range(len(self._entries)):
            res = self.resource_at(i)
            if res.is_html:
                out.append(res)
        return out

    def resource_by_id(self, resource_id: int) -> PakResource | None:
        for i, (rid, _) in enumerate(self._entries):
            if rid == resource_id:
                return self.resource_at(i)
        return None

    def write_resource(self, index: int, dest: Path) -> Path:
        dest.parent.mkdir(parents=True, exist_ok=True)
        res = self.resource_at(index)
        dest.write_bytes(res.data)
        return dest
