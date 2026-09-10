from __future__ import annotations

import struct
from dataclasses import dataclass
from pathlib import Path

from .paz_archive import PazVolume, decode_payload
from .paz_hash import hash_path_key, normalize_logical_path
from .paz_ice import BDO_ICE_KEY, IceCipher

PAZ_INDEX_LOOKUP_VA = 0x140D16490
ENTRY_SIZE = 28
SECONDARY_ENTRY_SIZE = 12

def format_pad_filename(pad_index: int) -> str:
    index = abs(int(pad_index))
    if index > 99999:
        raise ValueError("pad index out of range")
    return f"PAD{index:05d}.PAZ"

@dataclass(frozen=True)
class PazPadDescriptor:
    pad_id: int
    crc: int
    size: int

@dataclass(frozen=True)
class PazHashEntry:
    hash_key: int
    folder_id: int
    file_id: int
    pad_index: int
    offset: int
    compressed_size: int
    original_size: int

    @classmethod
    def unpack(cls, raw: bytes) -> PazHashEntry:
        fields = struct.unpack("<7I", raw)
        return cls(*fields)

    @property
    def logical_path(self) -> str | None:
        return None

@dataclass(frozen=True)
class PazArchiveRef:
    pad_index: int
    filename: str
    lookup_key: int
    offset: int = 0
    compressed_size: int = 0
    original_size: int = 0
    logical_path: str | None = None
    verified: bool = False

    @classmethod
    def from_entry(
        cls,
        entry: PazHashEntry,
        lookup_key: int,
        logical_path: str | None = None,
        verified: bool = False,
    ) -> PazArchiveRef:
        pad_index = abs(entry.pad_index)
        return cls(
            pad_index=pad_index,
            filename=format_pad_filename(pad_index),
            lookup_key=lookup_key,
            offset=entry.offset,
            compressed_size=entry.compressed_size,
            original_size=entry.original_size,
            logical_path=logical_path,
            verified=verified,
        )

    @classmethod
    def from_logical_path(cls, logical_path: str, pad_index: int) -> PazArchiveRef:
        return cls(
            pad_index=pad_index,
            filename=format_pad_filename(pad_index),
            lookup_key=hash_path_key(logical_path),
            logical_path=logical_path,
        )

def _parse_folder_table(raw: bytes) -> list[str]:
    names: list[str] = []
    cur = 0
    limit = len(raw) - 8
    while cur < limit:
        cur += 8
        nul = raw.find(b"\x00", cur)
        if nul == -1:
            break
        names.append(raw[cur:nul].decode("utf-8", "replace"))
        cur = nul + 1
    return names

def _parse_file_table(raw: bytes) -> list[str]:
    names: list[str] = []
    cur = 0
    while cur < len(raw):
        nul = raw.find(b"\x00", cur)
        if nul == -1:
            break
        names.append(raw[cur:nul].decode("utf-8", "replace"))
        cur = nul + 1
    return names

class PazIndex:

    def __init__(
        self,
        header_version: int,
        secondary: list[PazPadDescriptor],
        entries: list[PazHashEntry],
        folder_names: list[str],
        file_names: list[str],
        paz_dir: Path | None = None,
    ) -> None:
        self.header_version = header_version
        self.secondary = secondary
        self.entries = entries
        self.folder_names = folder_names
        self.file_names = file_names
        self.paz_dir = paz_dir
        self._ice = IceCipher(BDO_ICE_KEY)
        self._volumes: dict[int, PazVolume] = {}
        self._path_index: dict[str, PazHashEntry] | None = None

    @classmethod
    def from_bytes(cls, data: bytes, paz_dir: Path | None = None) -> PazIndex:
        if len(data) < 8:
            raise ValueError("meta file too small")
        header_version, secondary_count = struct.unpack_from("<II", data, 0)
        offset = 8
        secondary: list[PazPadDescriptor] = []
        for _ in range(secondary_count):
            pad_id, crc, size = struct.unpack_from("<3I", data, offset)
            secondary.append(PazPadDescriptor(pad_id, crc, size))
            offset += SECONDARY_ENTRY_SIZE
        hash_count = struct.unpack_from("<I", data, offset)[0]
        offset += 4
        entries: list[PazHashEntry] = []
        for _ in range(hash_count):
            entries.append(PazHashEntry.unpack(data[offset : offset + ENTRY_SIZE]))
            offset += ENTRY_SIZE
        folder_pool_size = struct.unpack_from("<I", data, offset)[0]
        offset += 4
        folder_raw = data[offset : offset + folder_pool_size]
        offset += folder_pool_size
        file_pool_size = struct.unpack_from("<I", data, offset)[0]
        offset += 4
        file_raw = data[offset : offset + file_pool_size]
        ice = IceCipher(BDO_ICE_KEY)
        folder_names = _parse_folder_table(ice.decrypt(folder_raw))
        file_names = _parse_file_table(ice.decrypt(file_raw))
        return cls(header_version, secondary, entries, folder_names, file_names, paz_dir)

    @classmethod
    def load(cls, meta_path: Path | str, paz_dir: Path | str | None = None) -> PazIndex:
        meta = Path(meta_path)
        if paz_dir is None:
            paz_dir = meta.parent
        return cls.from_bytes(meta.read_bytes(), Path(paz_dir))

    def path_of(self, entry: PazHashEntry) -> str:
        folder = self.folder_names[entry.folder_id] if entry.folder_id < len(self.folder_names) else ""
        name = self.file_names[entry.file_id] if entry.file_id < len(self.file_names) else ""
        path = f"{folder.rstrip('/')}/{name.lstrip('/')}"
        return path.replace("//", "/")

    def _volume(self, pad_index: int) -> PazVolume:
        pad_index = abs(pad_index)
        cached = self._volumes.get(pad_index)
        if cached is not None:
            return cached
        if self.paz_dir is None:
            raise ValueError("paz_dir is not set")
        for candidate in (
            self.paz_dir / format_pad_filename(pad_index),
            self.paz_dir / format_pad_filename(pad_index).lower(),
        ):
            if candidate.is_file():
                volume = PazVolume.open(candidate)
                self._volumes[pad_index] = volume
                return volume
        raise FileNotFoundError(format_pad_filename(pad_index))

    def _find_hash(self, key: int) -> PazHashEntry | None:
        lo = 0
        hi = len(self.entries)
        while lo < hi:
            mid = (lo + hi) // 2
            entry_key = self.entries[mid].hash_key
            if entry_key < key:
                lo = mid + 1
            else:
                hi = mid
        if lo >= len(self.entries):
            return None
        if self.entries[lo].hash_key != key:
            return None
        return self.entries[lo]

    def lookup(self, logical_path: str, install_root: str | None = None) -> PazArchiveRef | None:
        normalized = normalize_logical_path(logical_path, install_root=install_root)
        key = hash_path_key(normalized)
        entry = self._find_hash(key)
        if entry is None:
            return self.find_by_normalized(normalized)
        resolved = self.path_of(entry)
        verified = resolved == normalized or resolved.endswith("/" + normalized.split("/")[-1])
        return PazArchiveRef.from_entry(entry, key, logical_path=resolved, verified=verified)

    def find_by_normalized(self, normalized: str) -> PazArchiveRef | None:
        want = normalize_logical_path(normalized)
        if self._path_index is None:
            index: dict[str, PazHashEntry] = {}
            for entry in self.entries:
                index[normalize_logical_path(self.path_of(entry))] = entry
            self._path_index = index
        entry = self._path_index.get(want)
        if entry is None:
            return None
        return PazArchiveRef.from_entry(
            entry, entry.hash_key, logical_path=self.path_of(entry), verified=True
        )

    def lookup_hash(self, key: int) -> PazArchiveRef | None:
        entry = self._find_hash(key)
        if entry is None:
            return None
        return PazArchiveRef.from_entry(entry, key, logical_path=self.path_of(entry), verified=True)

    def extract(self, logical_path: str, install_root: str | None = None) -> bytes | None:
        ref = self.lookup(logical_path, install_root=install_root)
        if ref is None:
            return None
        return self.extract_ref(ref)

    def extract_ref(self, ref: PazArchiveRef) -> bytes:
        volume = self._volume(ref.pad_index)
        return volume.read_at(
            ref.offset,
            ref.compressed_size,
            ref.original_size,
            logical_path=ref.logical_path or "",
        )
