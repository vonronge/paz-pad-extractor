from __future__ import annotations

import struct
from dataclasses import dataclass

PAR_MAGIC = b"PAR "
PAR_MIN_SIZE = 0x38

KNOWN_VERSIONS = {
    0x01000506: "pam_mesh",
    0x01000217: "sector_combine",
    0x0100040A: "sector_probe",
}

@dataclass(frozen=True)
class ParHeader:
    magic: bytes
    version: int
    version_kind: str
    stamp: bytes
    field_0x10: int
    bounds: tuple[float, float, float, float, float, float]
    dword_0x34: int
    payload_offset: int
    file_size: int

    @property
    def is_lod_mesh(self) -> bool:
        return self.version_kind == "pam_mesh" and self.field_0x10 > 1

def detect_par_family(data: bytes) -> str | None:
    if len(data) < 4:
        return None
    if data[:4] == PAR_MAGIC:
        if len(data) < 6:
            return "par_unknown"

        if data[4] == 24 and 11 <= data[5] <= 13:
            return "pae_effect"
        if len(data) < 8:
            return "par_unknown"
        version = struct.unpack_from("<I", data, 4)[0]
        return KNOWN_VERSIONS.get(version, "par_unknown")
    return None

def parse_par_header(data: bytes) -> ParHeader:
    if len(data) < PAR_MIN_SIZE:
        raise ValueError(f"PAR header too short: {len(data)}")
    magic = data[:4]
    if magic != PAR_MAGIC:
        raise ValueError(f"expected PAR magic, got {magic!r}")
    version = struct.unpack_from("<I", data, 4)[0]
    stamp = data[8:16]
    field_0x10 = struct.unpack_from("<I", data, 0x10)[0]
    bounds = struct.unpack_from("<6f", data, 0x14)
    dword_0x34 = struct.unpack_from("<I", data, 0x34)[0]
    return ParHeader(
        magic=magic,
        version=version,
        version_kind=KNOWN_VERSIONS.get(version, "par_unknown"),
        stamp=stamp,
        field_0x10=field_0x10,
        bounds=bounds,
        dword_0x34=dword_0x34,
        payload_offset=PAR_MIN_SIZE,
        file_size=len(data),
    )
