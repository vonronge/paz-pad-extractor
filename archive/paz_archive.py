from __future__ import annotations

import struct
from dataclasses import dataclass
from pathlib import Path

from .paz_ice import BDO_ICE_KEY, IceCipher
from .paz_lz import decompress_bdo_lz

PAZ_DECODE_PAYLOAD_VA = 0x140D18960
PAZ_ENTRY_SIZE = 24
PAZ_COPY_SIZE_LIMIT = 1024
DDS_MAGIC = b"DDS "
PAR_MAGIC = b"PAR "
UTF8_BOM = b"\xef\xbb\xbf"
_ICE_STUB_SUFFIXES = (".dds", ".pab", ".pac", ".pam", ".xml")

@dataclass(frozen=True)
class PazLocalEntry:
    file_hash: int
    folder_id: int
    file_id: int
    offset: int
    compressed_size: int
    original_size: int

    @classmethod
    def unpack(cls, raw: bytes) -> PazLocalEntry:
        fields = struct.unpack("<6I", raw)
        return cls(*fields)

def _is_dbss_path(logical_path: str) -> bool:
    return ".dbss" in (logical_path or "").lower()

def payload_is_plaintext(logical_path: str, data: bytes) -> bool:
    if _is_dbss_path(logical_path):
        return True
    suf = Path(logical_path).suffix.lower()
    if suf == ".dds":
        return data.startswith(DDS_MAGIC)
    if suf in {".pab", ".pac", ".pam"}:
        return data.startswith(PAR_MAGIC)
    if suf == ".xml":
        body = data[3:] if data.startswith(UTF8_BOM) else data
        return body.lstrip(b" \t\r\n").startswith(b"<")
    return True

def decrypt_extracted_stub(
    data: bytes,
    logical_path: str = "",
    ice: IceCipher | None = None,
) -> bytes | None:
    if not data or payload_is_plaintext(logical_path, data):
        return None
    if _is_dbss_path(logical_path) or len(data) % 8 != 0:
        return None
    cipher = ice or IceCipher(BDO_ICE_KEY)
    dec = cipher.decrypt(data)
    if payload_is_plaintext(logical_path, dec):
        return dec
    return None

def repair_extracted_ice_stubs(
    root: Path,
    suffixes: tuple[str, ...] = _ICE_STUB_SUFFIXES,
) -> int:
    if not root.is_dir():
        return 0
    ice = IceCipher(BDO_ICE_KEY)
    want = {s.lower() for s in suffixes}
    fixed = 0
    for path in root.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in want:
            continue
        rel = path.relative_to(root).as_posix()
        head = path.read_bytes()[:16]
        if payload_is_plaintext(rel, head):
            continue
        data = path.read_bytes()
        dec = decrypt_extracted_stub(data, rel, ice)
        if dec is None:
            continue
        path.write_bytes(dec)
        fixed += 1
    return fixed

def decode_payload(
    data: bytes,
    compressed_size: int,
    original_size: int,
    ice: IceCipher | None = None,
    logical_path: str = "",
) -> bytes:
    size = int(compressed_size) if compressed_size else len(data)
    blob = data[:size]
    skip_ice = _is_dbss_path(logical_path)
    if not skip_ice and len(blob) % 8 == 0:
        cipher = ice or IceCipher(BDO_ICE_KEY)
        blob = cipher.decrypt(blob)
    if skip_ice or int(original_size) < PAZ_COPY_SIZE_LIMIT:
        if original_size and original_size <= len(blob):
            return blob[:original_size]
        return blob
    if (
        len(blob) > 9
        and blob[0] in (0x6E, 0x6F)
        and int.from_bytes(blob[5:9], "little") == original_size
    ):
        return decompress_bdo_lz(blob, original_size)
    if original_size <= len(blob):
        return blob[:original_size]
    return blob

class PazVolume:

    def __init__(self, path: Path | str, data: bytes | None = None) -> None:
        self.path = Path(path)
        self._data = data if data is not None else self.path.read_bytes()
        if len(self._data) < 12:
            raise ValueError("PAZ file too small")
        self.archive_hash, self.file_count, self.names_length = struct.unpack_from("<3I", self._data, 0)
        self._table_offset = 12
        self._names_offset = self._table_offset + self.file_count * PAZ_ENTRY_SIZE
        self._data_offset = self._names_offset + self.names_length
        self._ice = IceCipher(BDO_ICE_KEY)

    @classmethod
    def open(cls, path: Path | str) -> PazVolume:
        return cls(path)

    def entry_at(self, index: int) -> PazLocalEntry:
        offset = self._table_offset + index * PAZ_ENTRY_SIZE
        return PazLocalEntry.unpack(self._data[offset : offset + PAZ_ENTRY_SIZE])

    def read_entry(self, entry: PazLocalEntry, logical_path: str = "") -> bytes:
        chunk = self._data[entry.offset : entry.offset + entry.compressed_size]
        return decode_payload(
            chunk,
            entry.compressed_size,
            entry.original_size,
            self._ice,
            logical_path=logical_path,
        )

    def read_at(
        self,
        offset: int,
        compressed_size: int,
        original_size: int,
        logical_path: str = "",
    ) -> bytes:
        chunk = self._data[offset : offset + compressed_size]
        return decode_payload(
            chunk,
            compressed_size,
            original_size,
            self._ice,
            logical_path=logical_path,
        )
