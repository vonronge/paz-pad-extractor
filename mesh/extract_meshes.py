from __future__ import annotations

import argparse
import sys
from pathlib import Path

from archive.bulk_extract import ExtractStats, bulk_extract, candidate_paths
from archive.paz_index import PazArchiveRef, PazIndex

DEFAULT_MESH_EXTENSIONS = frozenset({".pam", ".pac", ".combine", ".probe", ".lod"})
DEFAULT_MAPDATA_EXTENSIONS = frozenset({".mapdata"})

def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]

def _default_meta_path() -> Path:
    return _repo_root() / "BDO Client/Paz/pad00000.meta"

def _default_output_dir() -> Path:
    return _repo_root() / "extracted"

def mesh_paths(index: PazIndex, extensions: frozenset[str]) -> list[tuple[str, PazArchiveRef]]:
    return candidate_paths(index, extensions=extensions)

def extract_meshes(
    meta_path: Path,
    output_dir: Path,
    extensions: frozenset[str] = DEFAULT_MESH_EXTENSIONS,
    limit: int | None = None,
    progress_every: int = 500,
    dry_run: bool = False,
) -> ExtractStats:
    return bulk_extract(
        meta_path=meta_path,
        output_dir=output_dir,
        extensions=extensions,
        limit=limit,
        progress_every=progress_every,
        dry_run=dry_run,
    )

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Extract BDO mesh archives from PAZ.")
    parser.add_argument("--meta", type=Path, default=_default_meta_path())
    parser.add_argument("--output", type=Path, default=_default_output_dir())
    parser.add_argument(
        "--extensions",
        default=",".join(sorted(DEFAULT_MESH_EXTENSIONS)),
    )
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--progress-every", type=int, default=500)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    if not args.meta.is_file():
        print(f"meta not found: {args.meta}", file=sys.stderr)
        return 1

    extensions = frozenset(
        ext if ext.startswith(".") else f".{ext}"
        for ext in args.extensions.lower().split(",")
        if ext.strip()
    )

    stats = extract_meshes(
        meta_path=args.meta,
        output_dir=args.output,
        extensions=extensions,
        limit=args.limit,
        progress_every=args.progress_every,
        dry_run=args.dry_run,
    )

    print(
        f"done: candidates={stats.total_candidates} "
        f"extracted={stats.extracted} skipped={stats.skipped_existing} "
        f"failed={stats.failed} "
        f"bytes={stats.bytes_written} "
        f"elapsed={stats.elapsed_seconds:.1f}s"
    )
    return 1 if stats.failed else 0

if __name__ == "__main__":
    raise SystemExit(main())
