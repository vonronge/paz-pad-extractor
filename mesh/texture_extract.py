from __future__ import annotations

import argparse
from pathlib import Path

from archive.paz_archive import payload_is_plaintext
from archive.paz_index import PazIndex

from .texture_paths import resolve_texture_logical_path

def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]

def default_meta_path() -> Path:
    return _repo_root() / "BDO Client/Paz/pad00000.meta"

def default_output_dir() -> Path:
    return _repo_root() / "extracted"

def ensure_texture(
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

    logical = resolve_texture_logical_path(texture_ref, index=index)
    if logical is None:
        return None
    dest = output_dir / logical
    if dest.is_file() and dest.stat().st_size > 0:
        if payload_is_plaintext(logical, dest.read_bytes()[:4]):
            return dest

    payload = index.extract(logical)
    if payload is None or len(payload) < 4:
        return None
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(payload)
    return dest

def main() -> None:
    parser = argparse.ArgumentParser(description="Extract one DDS from BDO PAZ on demand.")
    parser.add_argument("texture", help="logical path or bare .dds filename")
    parser.add_argument("-o", "--output", type=Path, default=default_output_dir())
    parser.add_argument("--meta", type=Path, default=default_meta_path())
    parser.add_argument("--print-path", action="store_true", help="print extracted file path")
    args = parser.parse_args()

    path = ensure_texture(args.texture, output_dir=args.output, meta_path=args.meta)
    if path is None:
        raise SystemExit(1)
    if args.print_path:
        print(path)

if __name__ == "__main__":
    main()
