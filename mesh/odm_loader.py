from __future__ import annotations

import json
import math
import re
import struct
from dataclasses import dataclass
from pathlib import Path

from .par_header import PAR_MAGIC, parse_par_header
from .texture_paths import resolve_submesh_albedo_path
from .pac_vertex_layout import (
    PAC_BONE_INDEX_OFFSET,
    PAC_BONE_WEIGHT_OFFSET,
    PAC_COLOR_OFFSET,
    PAC_NORMAL_OFFSET,
    PAC_POSITION_OFFSET,
    PAC_TANGENT_W_OFFSET,
    PAC_UV_OFFSET,
    PAC_VERTEX_STRIDE,
    PAM_NORMAL_OFFSET,
    PAM_POSITION_OFFSET,
    PAM_TANGENT_W_OFFSET,
    PAM_UV1_OFFSET,
    PAM_UV_OFFSET,
    PAM_VERTEX_STRIDE,
)

PAM_SUBMESH_BASE = 0x410
PAM_TEXTURE_NAME_BYTES = 256
PAM_SUBMESH_DESC_SIZE = 8 + 12 + PAM_TEXTURE_NAME_BYTES
PAM_INDEX_RESTART = 0xFFFF

PAC_VERSIONS = frozenset({0x01000103, 0x01000203, 0x01000303, 0x00000303})
MESH_NAME_RE = re.compile(rb"[A-Z]{2,3}_[A-Za-z0-9_]{4,}")
PAC_NAME_SCAN_START = 0x10
PAC_NAME_HEADER_END = 0x180

@dataclass(frozen=True)
class OdmSubmesh:
    name: str
    texture_name: str
    vertex_count: int
    index_count: int
    positions: tuple[tuple[float, float, float], ...]
    uvs: tuple[tuple[float, float], ...]
    normals: tuple[tuple[float, float, float], ...]
    indices: tuple[int, ...]
    lod_index: int = 0
    bone_indices: tuple[tuple[int, int, int, int], ...] = ()
    bone_weights: tuple[tuple[float, float, float, float], ...] = ()
    uv1s: tuple[tuple[float, float], ...] = ()
    vertex_colors: tuple[tuple[float, float, float, float], ...] = ()
    tangents: tuple[tuple[float, float, float, float], ...] = ()

@dataclass(frozen=True)
class OdmMesh:
    path: str
    version: int
    version_kind: str
    submeshes: tuple[OdmSubmesh, ...]

    @property
    def vertex_count(self) -> int:
        return sum(s.vertex_count for s in self.submeshes)

    @property
    def index_count(self) -> int:
        return sum(s.index_count for s in self.submeshes)

def _align4(offset: int) -> int:
    return (offset + 3) & ~3

def _read_vec3(data: bytes, offset: int) -> tuple[float, float, float]:
    return struct.unpack_from("<3f", data, offset)

def _snorm8(value: int) -> float:
    signed = value - 256 if value >= 128 else value
    return max(-1.0, min(1.0, signed / 127.0))

def _read_snorm8_vec3(data: bytes, offset: int) -> tuple[float, float, float]:
    return (
        _snorm8(data[offset]),
        _snorm8(data[offset + 1]),
        _snorm8(data[offset + 2]),
    )

def _read_half2_uv(data: bytes, offset: int) -> tuple[float, float]:
    u, v = struct.unpack_from("<2e", data, offset)
    return float(u), float(v)

def _read_u8_rgba(data: bytes, offset: int) -> tuple[float, float, float, float]:
    r, g, b, a = struct.unpack_from("<4B", data, offset)
    return r / 255.0, g / 255.0, b / 255.0, a / 255.0

def _read_uv1_uint16(data: bytes, offset: int) -> tuple[float, float]:
    u, v = struct.unpack_from("<2H", data, offset)
    return u / 65535.0, v / 65535.0

def _vdot3(
    a: tuple[float, float, float], b: tuple[float, float, float]
) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]

def _vcross3(
    a: tuple[float, float, float], b: tuple[float, float, float]
) -> tuple[float, float, float]:
    return (
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    )

def _vsub3(
    a: tuple[float, float, float], b: tuple[float, float, float]
) -> tuple[float, float, float]:
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])

def _vscale3(v: tuple[float, float, float], s: float) -> tuple[float, float, float]:
    return (v[0] * s, v[1] * s, v[2] * s)

def _vnormalize3(v: tuple[float, float, float]) -> tuple[float, float, float]:
    length = math.sqrt(_vdot3(v, v))
    if length < 1e-12:
        return (0.0, 0.0, 0.0)
    return _vscale3(v, 1.0 / length)

def _compute_tangent_frames(
    positions: tuple[tuple[float, float, float], ...],
    uvs: tuple[tuple[float, float], ...],
    normals: tuple[tuple[float, float, float], ...],
    tangent_ws: tuple[float, ...],
    indices: tuple[int, ...],
    *,
    index_restart: int = PAM_INDEX_RESTART,
) -> tuple[tuple[float, float, float, float], ...]:
    vertex_count = len(positions)
    accum: list[list[float]] = [[0.0, 0.0, 0.0] for _ in range(vertex_count)]
    for tri in range(0, len(indices), 3):
        i0, i1, i2 = indices[tri], indices[tri + 1], indices[tri + 2]
        if i0 == index_restart or i1 == index_restart or i2 == index_restart:
            continue
        if max(i0, i1, i2) >= vertex_count:
            continue
        p0, p1, p2 = positions[i0], positions[i1], positions[i2]
        uv0, uv1, uv2 = uvs[i0], uvs[i1], uvs[i2]
        edge1 = _vsub3(p1, p0)
        edge2 = _vsub3(p2, p0)
        duv1 = (uv1[0] - uv0[0], uv1[1] - uv0[1])
        duv2 = (uv2[0] - uv0[0], uv2[1] - uv0[1])
        det = duv1[0] * duv2[1] - duv2[0] * duv1[1]
        if abs(det) < 1e-12:
            continue
        inv = 1.0 / det
        tangent = (
            (edge1[0] * duv2[1] - edge2[0] * duv1[1]) * inv,
            (edge1[1] * duv2[1] - edge2[1] * duv1[1]) * inv,
            (edge1[2] * duv2[1] - edge2[2] * duv1[1]) * inv,
        )
        for index in (i0, i1, i2):
            for axis in range(3):
                accum[index][axis] += tangent[axis]
    frames: list[tuple[float, float, float, float]] = []
    for index in range(vertex_count):
        normal = normals[index] if index < len(normals) else (0.0, 0.0, 1.0)
        tangent = tuple(accum[index])
        tangent = _vsub3(tangent, _vscale3(normal, _vdot3(normal, tangent)))
        tangent = _vnormalize3(tangent)
        w = tangent_ws[index] if index < len(tangent_ws) else 1.0
        if w == 0.0:
            w = 1.0
        frames.append((tangent[0], tangent[1], tangent[2], w))
    return tuple(frames)

def _read_pac_skin(
    data: bytes, offset: int
) -> tuple[tuple[int, int, int, int], tuple[float, float, float, float]]:
    idx = struct.unpack_from("<4B", data, offset + PAC_BONE_INDEX_OFFSET)
    weights_raw = struct.unpack_from("<4B", data, offset + PAC_BONE_WEIGHT_OFFSET)
    total = sum(weights_raw) or 1
    weights = tuple(w / total for w in weights_raw)
    return idx, weights

def _is_pac_mesh_name(raw: bytes) -> bool:
    if not (8 <= len(raw) <= 64 and raw.isascii() and all(32 <= c < 127 for c in raw)):
        return False

    return (
        re.match(
            rb"^(?:[A-Za-z]{2,}(?:\d+)?|[A-Za-z]\d+)(?:_[A-Za-z0-9#]+)+$",
            raw,
        )
        is not None
    )

def _pac_mesh_name_at(data: bytes, start: int) -> tuple[int, str] | None:
    name_len = data[start]
    if 8 <= name_len <= 64 and start + 1 + name_len <= len(data):
        raw = data[start + 1 : start + 1 + name_len]
        if _is_pac_mesh_name(raw):
            return start, raw.decode("ascii")
    return None

def _find_pac_mesh_name(
    data: bytes,
    search_start: int = PAC_NAME_SCAN_START,
    search_end: int | None = None,
) -> tuple[int, str]:
    end = min(search_end if search_end is not None else PAC_NAME_HEADER_END, len(data))
    for start in range(search_start, end):
        found = _pac_mesh_name_at(data, start)
        if found is not None:
            return found
    match = MESH_NAME_RE.search(data[search_start:end])
    if match is None:
        raise ValueError("PAC mesh name not found")
    name = match.group(0).decode("ascii")
    return search_start + match.start(), name

def _find_pac_mesh_name_after(data: bytes, after: int) -> tuple[int, str] | None:
    for start in range(after, len(data)):
        found = _pac_mesh_name_at(data, start)
        if found is not None:
            return found
    return None

def _pac_submesh_payload(data: bytes, name_offset: int, name: str) -> int:
    if data[name_offset] == len(name):
        return name_offset + 1 + len(name)
    return name_offset + len(name)

def _parse_pac_submesh_at(
    data: bytes,
    name_offset: int,
    texture_name: str = "",
    source_path: str | Path | None = None,
) -> tuple[OdmSubmesh, int]:
    found = _pac_mesh_name_at(data, name_offset)
    if found is not None:
        name_offset, name = found
    else:
        raw = data[name_offset : name_offset + 64]
        match = MESH_NAME_RE.match(raw)
        if match is None:
            raise ValueError("PAC mesh name not found")
        name = match.group(0).decode("ascii")
    if not texture_name:
        texture_name = resolve_submesh_albedo_path(name, source_path=source_path)
    payload = _pac_submesh_payload(data, name_offset, name)
    if payload + 4 > len(data):
        raise ValueError("PAC submesh header truncated")
    _flags, vertex_count = struct.unpack_from("<HH", data, payload)
    vertex_start = payload + 4
    vertex_end = vertex_start + vertex_count * PAC_VERTEX_STRIDE
    if vertex_end + 4 > len(data):
        raise ValueError("PAC vertex buffer truncated")
    index_count = struct.unpack_from("<I", data, vertex_end)[0]
    index_start = vertex_end + 4
    index_end = index_start + index_count * 2
    if index_end > len(data):
        raise ValueError("PAC index buffer truncated")
    positions: list[tuple[float, float, float]] = []
    uvs: list[tuple[float, float]] = []
    normals: list[tuple[float, float, float]] = []
    tangent_ws: list[float] = []
    vertex_colors: list[tuple[float, float, float, float]] = []
    bone_indices: list[tuple[int, int, int, int]] = []
    bone_weights: list[tuple[float, float, float, float]] = []
    for i in range(vertex_count):
        base = vertex_start + i * PAC_VERTEX_STRIDE
        positions.append(_read_vec3(data, base + PAC_POSITION_OFFSET))
        normals.append(_read_snorm8_vec3(data, base + PAC_NORMAL_OFFSET))
        tangent_ws.append(_snorm8(data[base + PAC_TANGENT_W_OFFSET]))
        uvs.append(_read_half2_uv(data, base + PAC_UV_OFFSET))
        vertex_colors.append(_read_u8_rgba(data, base + PAC_COLOR_OFFSET))
        idx, weights = _read_pac_skin(data, base)
        bone_indices.append(idx)
        bone_weights.append(weights)
    indices = struct.unpack_from(f"<{index_count}H", data, index_start)
    if indices and max(indices) >= vertex_count:
        raise ValueError(
            f"PAC index out of range: max={max(indices)} vertex_count={vertex_count}"
        )
    uv1s = ((0.0, 0.0),) * vertex_count
    tangents = _compute_tangent_frames(
        tuple(positions),
        tuple(uvs),
        tuple(normals),
        tuple(tangent_ws),
        indices,
    )
    return (
        OdmSubmesh(
            name=name,
            texture_name=texture_name,
            vertex_count=vertex_count,
            index_count=index_count,
            positions=tuple(positions),
            uvs=tuple(uvs),
            normals=tuple(normals),
            indices=indices,
            bone_indices=tuple(bone_indices),
            bone_weights=tuple(bone_weights),
            uv1s=uv1s,
            vertex_colors=tuple(vertex_colors),
            tangents=tangents,
        ),
        index_end,
    )

def _parse_pac_submesh(
    data: bytes,
    offset: int,
    texture_name: str = "",
    source_path: str | Path | None = None,
) -> tuple[OdmSubmesh, int]:
    rel_offset, _ = _find_pac_mesh_name(data[offset:])
    return _parse_pac_submesh_at(
        data,
        offset + rel_offset,
        texture_name=texture_name,
        source_path=source_path,
    )

@dataclass(frozen=True)
class _PamSubmeshDesc:
    vertex_count: int
    index_count: int
    texture_name: str
    mesh_name: str

def _read_pam_submesh_desc(data: bytes, desc_offset: int) -> _PamSubmeshDesc:
    if desc_offset + PAM_SUBMESH_DESC_SIZE > len(data):
        raise ValueError("PAM submesh descriptor truncated")
    vertex_count, index_count = struct.unpack_from("<II", data, desc_offset)
    name_start = desc_offset + 20
    name_end = name_start + PAM_TEXTURE_NAME_BYTES
    texture_name = data[name_start:name_end].split(b"\0", 1)[0].decode("ascii", "replace")
    mesh_name = Path(texture_name).stem if texture_name else "pam"
    return _PamSubmeshDesc(
        vertex_count=vertex_count,
        index_count=index_count,
        texture_name=texture_name,
        mesh_name=mesh_name,
    )

def _pam_submesh_table_end(lod_count: int) -> int:
    return _align4(PAM_SUBMESH_BASE + lod_count * PAM_SUBMESH_DESC_SIZE)

def _validate_pam_indices(indices: tuple[int, ...], vertex_count: int) -> None:
    if not indices or vertex_count <= 0:
        return
    in_range = [idx for idx in indices if idx != PAM_INDEX_RESTART and idx < vertex_count]
    if in_range:
        return
    peak = max(indices)
    raise ValueError(
        f"PAM index out of range: max={peak} vertex_count={vertex_count}"
    )

def _parse_pam_geometry(
    data: bytes,
    desc: _PamSubmeshDesc,
    geometry_offset: int,
    *,
    lod_index: int = 0,
) -> tuple[OdmSubmesh, int]:
    vertex_count = desc.vertex_count
    index_count = desc.index_count
    vertex_start = geometry_offset
    vertex_end = vertex_start + vertex_count * PAM_VERTEX_STRIDE
    index_start = vertex_end
    index_end = index_start + index_count * 2
    if index_end > len(data):
        raise ValueError("PAM geometry truncated")
    positions: list[tuple[float, float, float]] = []
    uvs: list[tuple[float, float]] = []
    uv1s: list[tuple[float, float]] = []
    normals: list[tuple[float, float, float]] = []
    tangent_ws: list[float] = []
    vertex_colors: list[tuple[float, float, float, float]] = []
    for i in range(vertex_count):
        base = vertex_start + i * PAM_VERTEX_STRIDE
        positions.append(_read_vec3(data, base + PAM_POSITION_OFFSET))
        uvs.append(_read_half2_uv(data, base + PAM_UV_OFFSET))
        normals.append(_read_snorm8_vec3(data, base + PAM_NORMAL_OFFSET))
        tangent_ws.append(_snorm8(data[base + PAM_TANGENT_W_OFFSET]))
        uv1s.append(_read_uv1_uint16(data, base + PAM_UV1_OFFSET))
        vertex_colors.append((1.0, 1.0, 1.0, 1.0))
    indices = struct.unpack_from(f"<{index_count}H", data, index_start)
    _validate_pam_indices(indices, vertex_count)
    tangents = _compute_tangent_frames(
        tuple(positions),
        tuple(uvs),
        tuple(normals),
        tuple(tangent_ws),
        indices,
    )
    return (
        OdmSubmesh(
            name=desc.mesh_name,
            texture_name=desc.texture_name,
            vertex_count=vertex_count,
            index_count=index_count,
            lod_index=lod_index,
            positions=tuple(positions),
            uvs=tuple(uvs),
            normals=tuple(normals),
            indices=indices,
            uv1s=tuple(uv1s),
            vertex_colors=tuple(vertex_colors),
            tangents=tangents,
        ),
        index_end,
    )

def _parse_pam_lods(data: bytes, lod_count: int) -> list[OdmSubmesh]:
    if lod_count <= 0:
        raise ValueError("PAM LOD count must be positive")
    descriptors = [
        _read_pam_submesh_desc(data, PAM_SUBMESH_BASE + index * PAM_SUBMESH_DESC_SIZE)
        for index in range(lod_count)
    ]
    geometry_offset = _pam_submesh_table_end(lod_count)
    submeshes: list[OdmSubmesh] = []
    for lod_index, desc in enumerate(descriptors):
        submesh, geometry_offset = _parse_pam_geometry(
            data,
            desc,
            geometry_offset,
            lod_index=lod_index,
        )
        submeshes.append(submesh)
    return submeshes

def parse_odm_bytes(data: bytes, path: str = "") -> OdmMesh:
    if len(data) < 8 or data[:4] != PAR_MAGIC:
        raise ValueError(f"expected PAR magic, got {data[:4]!r}")
    header = parse_par_header(data)
    version = header.version
    submeshes: list[OdmSubmesh] = []

    if version == 0x01000506:
        submeshes.extend(_parse_pam_lods(data, max(1, header.field_0x10)))
    elif version in PAC_VERSIONS:
        search_from = PAC_NAME_SCAN_START
        while search_from < len(data):
            name_at = _find_pac_mesh_name_after(data, search_from)
            if name_at is None:
                break
            name_offset, _ = name_at
            try:
                submesh, next_offset = _parse_pac_submesh_at(
                    data,
                    name_offset,
                    source_path=path,
                )
            except ValueError:

                break
            submeshes.append(submesh)
            if next_offset <= name_offset:
                break
            search_from = next_offset
        if not submeshes:
            submesh, _ = _parse_pac_submesh(data, 0, source_path=path)
            submeshes.append(submesh)
        elif len(submeshes) > 1:
            submeshes.sort(key=lambda sub: sub.vertex_count, reverse=True)
    else:
        raise ValueError(f"unsupported PAR version 0x{version:08X}")

    if not submeshes:
        raise ValueError("no submeshes decoded")
    return OdmMesh(
        path=path,
        version=version,
        version_kind=header.version_kind,
        submeshes=tuple(submeshes),
    )

def pam_descriptor_counts(data: bytes) -> tuple[int, int]:
    header = parse_par_header(data)
    if header.version != 0x01000506:
        raise ValueError(f"not a PAM: 0x{header.version:08X}")
    lod_count = max(1, header.field_0x10)
    verts = 0
    idxs = 0
    for index in range(lod_count):
        desc = _read_pam_submesh_desc(data, PAM_SUBMESH_BASE + index * PAM_SUBMESH_DESC_SIZE)
        verts += desc.vertex_count
        idxs += desc.index_count
    return verts, idxs

def load_odm(path: str | Path) -> OdmMesh:
    p = Path(path)
    return parse_odm_bytes(p.read_bytes(), str(p))

def mesh_to_dict(mesh: OdmMesh, scale: float = 0.01) -> dict:
    from .winding import retail_front_face

    parts: list[dict] = []
    for sub in mesh.submeshes:
        positions: list[float] = []
        for x, y, z in sub.positions:
            positions.extend((x * scale, y * scale, z * scale))
        uvs: list[float] = []
        for u, v in sub.uvs:
            uvs.extend((u, v))
        normals: list[float] = []
        for nx, ny, nz in sub.normals:
            normals.extend((nx, ny, nz))
        uv1s: list[float] = []
        for u, v in sub.uv1s:
            uv1s.extend((u, v))
        colors: list[float] = []
        for r, g, b, a in sub.vertex_colors:
            colors.extend((r, g, b, a))
        tangents: list[float] = []
        for tx, ty, tz, tw in sub.tangents:
            tangents.extend((tx, ty, tz, tw))
        parts.append(
            {
                "name": sub.name,
                "texture": sub.texture_name,
                "lod_index": sub.lod_index,
                "vertex_count": sub.vertex_count,
                "index_count": sub.index_count,
                "positions": positions,
                "uvs": uvs,
                "uv1s": uv1s,
                "normals": normals,
                "colors": colors,
                "tangents": tangents,
                "indices": list(sub.indices),
                "front_face": retail_front_face(sub),
                "bone_indices": [list(ix) for ix in sub.bone_indices],
                "bone_weights": [list(w) for w in sub.bone_weights],
            }
        )
    return {
        "path": mesh.path,
        "version": mesh.version,
        "version_kind": mesh.version_kind,
        "scale": scale,
        "front_face": "cw",
        "parts": parts,
    }

def export_mesh_json(path: str | Path, out_path: str | Path | None = None, scale: float = 0.01) -> dict:
    mesh = load_odm(path)
    payload = mesh_to_dict(mesh, scale=scale)
    if out_path is not None:
        Path(out_path).write_text(json.dumps(payload), encoding="utf-8")
    return payload

def default_warrior_preview_parts(extracted_root: str | Path) -> list[Path]:
    base = Path(extracted_root) / "character/model/1_pc/1_phm"
    return [
        base / "nude/phm_00_nude_0001.pac",
        base / "armor/9_upperbody/phm_00_ub_0041_dm.pac",
        base / "armor/10_lowerbody/phm_00_lb_0007.pac",
        base / "armor/11_hand/phm_00_hand_0010.pac",
        base / "armor/12_foot/phm_03_foot_0001.pac",
    ]

def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Export BDO PAR/ODM mesh to JSON for Godot.")
    parser.add_argument("mesh_path", type=Path)
    parser.add_argument("-o", "--output", type=Path)
    parser.add_argument("--scale", type=float, default=0.01)
    args = parser.parse_args()
    payload = export_mesh_json(args.mesh_path, args.output, scale=args.scale)
    if args.output is None:
        print(json.dumps(payload))

if __name__ == "__main__":
    main()
