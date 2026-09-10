
from __future__ import annotations

import struct
from pathlib import Path

from archive.paz_index import PazIndex

UI_TEXTURE_PREFIX = "ui_texture/"
DDS_MAGIC = b"DDS "
_DXT_BLOCK_BYTES = {
    "DXT1": 8,
    "ATI1": 8,
    "BC4U": 8,
    "DXT2": 16,
    "DXT3": 16,
    "DXT4": 16,
    "DXT5": 16,
    "ATI2": 16,
    "BC5U": 16,
}

def normalize_ui_texture_ref(texture_ref: str) -> str:
    ref = texture_ref.replace("\\", "/").strip().lstrip("/").lower()
    if not ref:
        return ""
    if not ref.endswith(".dds"):
        ref += ".dds"
    if not ref.startswith(UI_TEXTURE_PREFIX):
        ref = UI_TEXTURE_PREFIX + ref
    return ref

def resolve_ui_texture_path(texture_ref: str, index: PazIndex | None = None) -> str | None:
    logical = normalize_ui_texture_ref(texture_ref)
    if not logical:
        return None
    if index is None:
        return logical
    if index.lookup(logical) is not None:
        return logical
    bare = logical.split("/")[-1]
    for entry in index.entries:
        path = index.path_of(entry).lower()
        if path.endswith("/" + bare) and path.startswith(UI_TEXTURE_PREFIX):
            if index.lookup(path) is not None:
                return path
    return None

def dds_header_info(data: bytes) -> dict[str, int | str]:
    if len(data) < 128 or data[:4] != DDS_MAGIC:
        raise ValueError("not a DDS file")
    height, width = struct.unpack_from("<II", data, 12)
    fourcc = data[84:88].decode("ascii", errors="replace")
    mipmaps = struct.unpack_from("<I", data, 28)[0]
    return {"width": width, "height": height, "fourcc": fourcc, "mipmaps": mipmaps}

def _dxt_full_chain_bytes(width: int, height: int, block: int) -> int:
    total = 0
    w = max(1, int(width))
    h = max(1, int(height))
    while True:
        bw = max(1, (w + 3) // 4)
        bh = max(1, (h + 3) // 4)
        total += bw * bh * block
        if w <= 1 and h <= 1:
            break
        w = max(1, w // 2)
        h = max(1, h // 2)
    return total

def pad_dds_mip_chain(data: bytes) -> bytes:
    if len(data) < 128 or data[:4] != DDS_MAGIC:
        return data
    mipmaps = struct.unpack_from("<I", data, 28)[0]
    if mipmaps < 2:
        return data
    height, width = struct.unpack_from("<II", data, 12)
    fourcc = data[84:88].decode("ascii", errors="replace").rstrip("\x00")
    block = _DXT_BLOCK_BYTES.get(fourcc, 0)
    if block <= 0:
        return data
    header = 148 if fourcc == "DX10" else 128
    if len(data) < header:
        return data
    expected = _dxt_full_chain_bytes(width, height, block)
    levels = 0
    w = max(1, int(width))
    h = max(1, int(height))
    while True:
        levels += 1
        if w <= 1 and h <= 1:
            break
        w = max(1, w // 2)
        h = max(1, h // 2)
    have = len(data) - header
    if have >= expected and mipmaps >= levels:
        return data
    out = bytearray(data)
    if mipmaps < levels:
        struct.pack_into("<I", out, 28, levels)
    if have < expected:
        missing = expected - have
        if missing > block * 2:
            return data
        out.extend(bytes(missing))
    return bytes(out)

def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]

def default_meta_path() -> Path:
    return _repo_root() / "BDO Client/Paz/pad00000.meta"

def default_output_dir() -> Path:
    return _repo_root() / "extracted"

def ensure_ui_texture(
    texture_ref: str,
    output_dir: Path | None = None,
    meta_path: Path | None = None,
    index: PazIndex | None = None,
) -> Path | None:
    if output_dir is None:
        output_dir = default_output_dir()
    if meta_path is None:
        meta_path = default_meta_path()
    if index is None:
        if not meta_path.is_file():
            return None
        index = PazIndex.load(meta_path, paz_dir=meta_path.parent)

    logical = resolve_ui_texture_path(texture_ref, index=index)
    if logical is None:
        return None
    dest = output_dir / logical
    if dest.is_file() and dest.stat().st_size > 0:
        return dest

    payload = index.extract(logical)
    if payload is None or len(payload) < 128:
        return None
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(payload)
    return dest

def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Extract one UI DDS from BDO PAZ.")
    parser.add_argument("texture", help="logical path or Scaleform relative .dds")
    parser.add_argument("-o", "--output", type=Path, default=default_output_dir())
    parser.add_argument("--meta", type=Path, default=default_meta_path())
    parser.add_argument("--print-path", action="store_true")
    args = parser.parse_args()

    path = ensure_ui_texture(args.texture, output_dir=args.output, meta_path=args.meta)
    if path is None:
        raise SystemExit(1)
    if args.print_path:
        print(path)

if __name__ == "__main__":
    main()
