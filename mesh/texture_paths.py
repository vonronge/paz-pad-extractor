from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING

from archive.paz_index import PazIndex

if TYPE_CHECKING:
    from .odm_loader import OdmMesh

MESH_NAME_RE = re.compile(rb"[A-Z]{2,3}_[A-Za-z0-9_]{4,}")
ALBEDO_SUFFIXES = ("_m", "_n", "_sp", "_ao", "_deco")
TEXTURE_PREFIXES = ("character/texture/", "object/texture/")
LOBBY_POSE_UI_SUBPATH = "ui_texture/new_ui_common_forlua/window/lobby"

def resolve_motion_pose_texture_path(texture_ref: str, index: PazIndex | None = None) -> str | None:
    if not texture_ref:
        return None
    bare = Path(texture_ref.replace("\\", "/")).name.lower()
    if not bare.endswith(".dds"):
        bare += ".dds"
    lobby = f"{LOBBY_POSE_UI_SUBPATH}/{bare}"
    if index is not None:
        from ui.ui_texture import resolve_ui_texture_path

        resolved = resolve_ui_texture_path(bare, index=index)
        if resolved:
            return resolved
        if index.lookup(lobby) is not None:
            return lobby
    return lobby

def pac_mesh_tokens(data: bytes, scan_start: int = 0x10, scan_end: int = 0x180) -> list[str]:
    region = data[scan_start:scan_end]
    tokens: list[str] = []
    seen: set[str] = set()
    for start in range(0, max(0, len(region) - 8)):
        name_len = region[start]
        if 8 <= name_len <= 64 and start + 1 + name_len <= len(region):
            raw = region[start + 1 : start + 1 + name_len]
            if raw.isascii() and all(32 <= c < 127 for c in raw) and b"_" in raw:
                name = raw.decode("ascii")
                if name not in seen:
                    seen.add(name)
                    tokens.append(name)
    for match in MESH_NAME_RE.finditer(region):
        name = match.group(0).decode("ascii")
        if name not in seen:
            seen.add(name)
            tokens.append(name)
    return tokens

def resolve_pac_albedo_path(
    tokens: list[str],
    source_path: str | Path | None = None,
) -> str:
    if not tokens:
        return ""
    mesh_token = max(tokens, key=len)
    if source_path is not None:
        stem = Path(source_path).stem.lower()
        mesh_lower = mesh_token.lower()
        if stem.endswith("_dm") and mesh_lower in stem:
            return f"character/texture/{stem}.dds"
    dm = [t for t in tokens if t.lower().endswith("_dm")]
    token = dm[-1] if dm else mesh_token
    return f"character/texture/{token.lower()}.dds"

def resolve_submesh_albedo_path(
    mesh_name: str,
    source_path: str | Path | None = None,
    index: PazIndex | None = None,
) -> str:
    if not mesh_name:
        return ""
    token = mesh_name.lower()
    if source_path is not None:
        stem = Path(source_path).stem.lower()
        if stem.endswith("_dm") and token.replace("_dm", "") in stem:
            path = f"character/texture/{stem}.dds"
            if index is not None:
                resolved = resolve_texture_logical_path(f"{stem}.dds", index=index)
                if resolved:
                    return resolved
            return path
    path = f"character/texture/{token}.dds"
    if index is not None:
        resolved = resolve_texture_logical_path(f"{token}.dds", index=index)
        if resolved:
            return resolved
    return path

def albedo_to_normal_path(albedo_path: str) -> str:
    if not albedo_path:
        return ""
    path = Path(albedo_path.lower())
    return str(path.with_name(f"{path.stem}_n{path.suffix}"))

def is_decal_texture_path(texture_path: str) -> bool:
    lower = texture_path.lower()
    if "_dec" in lower:
        return True
    stem = Path(lower).stem
    return "deco" in stem

def should_apply_palette_tint(part_label: str, texture_path: str) -> bool:
    if part_label not in {"head", "hair", "eyebrows", "beard", "mustache", "whiskers", "body"}:
        return False
    return not is_decal_texture_path(texture_path)

def resolve_normal_path(albedo_path: str, index: PazIndex | None = None) -> str:
    normal = albedo_to_normal_path(albedo_path)
    if not normal:
        return ""
    if index is not None and index.lookup(normal) is None:
        return normal
    return normal

def normalize_texture_ref(texture_ref: str) -> str:
    ref = texture_ref.replace("\\", "/").strip().lstrip("/")
    if not ref:
        return ""
    if "/" not in ref:
        stem = ref.lower()
        if not stem.endswith(".dds"):
            stem += ".dds"
        return f"character/texture/{stem}"
    return ref.lower()

def _is_albedo_path(path: str) -> bool:
    lower = path.lower()
    if not lower.endswith(".dds"):
        return False
    stem = Path(lower).stem
    return not any(stem.endswith(suffix) for suffix in ALBEDO_SUFFIXES)

def resolve_texture_logical_path(texture_ref: str, index: PazIndex | None = None) -> str | None:
    if not texture_ref:
        return None
    candidates: list[str] = []
    ref = texture_ref.replace("\\", "/").strip().lstrip("/")
    bare = Path(ref).name.lower()
    if not bare.endswith(".dds"):
        bare += ".dds"
    if "/" in ref:
        candidates.append(ref.lower())
    candidates.extend(f"{prefix}{bare}" for prefix in TEXTURE_PREFIXES)

    if index is None:
        for candidate in candidates:
            if _is_albedo_path(candidate):
                return candidate
        return candidates[0] if candidates else None

    for candidate in candidates:
        if not _is_albedo_path(candidate):
            continue
        if index.lookup(candidate) is not None:
            return candidate

    for entry in index.entries:
        path = index.path_of(entry).lower()
        if not path.endswith("/" + bare) and path != bare:
            continue
        if "/texture/" not in path or not _is_albedo_path(path):
            continue
        if index.lookup(path) is not None:
            return path
    return None

def resolve_mesh_texture_paths(mesh: OdmMesh, raw: bytes, index: PazIndex | None = None) -> list[str]:
    pac_tokens = pac_mesh_tokens(raw) if mesh.version in {0x01000103, 0x01000203, 0x01000303, 0x00000303} else []
    paths: list[str] = []
    for sub in mesh.submeshes:
        if sub.texture_name:
            resolved = resolve_texture_logical_path(sub.texture_name, index=index)
            paths.append(resolved or normalize_texture_ref(sub.texture_name))
        elif sub.name:
            paths.append(resolve_submesh_albedo_path(sub.name, source_path=mesh.path, index=index))
        elif pac_tokens:
            paths.append(resolve_pac_albedo_path(pac_tokens, source_path=mesh.path))
        else:
            paths.append("")
    return paths

def resolve_mesh_normal_paths(mesh: OdmMesh, raw: bytes, index: PazIndex | None = None) -> list[str]:
    return [resolve_normal_path(albedo, index=index) for albedo in resolve_mesh_texture_paths(mesh, raw, index=index)]
