from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass
from pathlib import Path

from .paz_archive import payload_is_plaintext
from .paz_index import PazArchiveRef, PazIndex

@dataclass
class ExtractStats:
    total_candidates: int = 0
    extracted: int = 0
    skipped_existing: int = 0
    failed: int = 0
    bytes_written: int = 0
    elapsed_seconds: float = 0.0
    errors: list[str] | None = None

def candidate_paths(
    index: PazIndex,
    extensions: frozenset[str],
) -> list[tuple[str, PazArchiveRef]]:
    out: list[tuple[str, PazArchiveRef]] = []
    for entry in index.entries:
        logical_path = index.path_of(entry)
        if Path(logical_path).suffix.lower() not in extensions:
            continue
        ref = PazArchiveRef.from_entry(
            entry,
            entry.hash_key,
            logical_path=logical_path,
            verified=True,
        )
        out.append((logical_path, ref))
    return out

def bulk_extract(
    meta_path: Path,
    output_dir: Path,
    extensions: frozenset[str],
    limit: int | None = None,
    progress_every: int = 500,
    dry_run: bool = False,
) -> ExtractStats:
    stats = ExtractStats(errors=[])
    t0 = time.monotonic()

    index = PazIndex.load(meta_path)
    candidates = candidate_paths(index, extensions)
    stats.total_candidates = len(candidates)
    if limit is not None:
        candidates = candidates[:limit]

    output_dir.mkdir(parents=True, exist_ok=True)

    for i, (logical_path, ref) in enumerate(candidates, start=1):
        dest = output_dir / logical_path
        if dest.is_file() and dest.stat().st_size == ref.original_size:
            head = dest.read_bytes()[:16]
            if payload_is_plaintext(logical_path, head):
                stats.skipped_existing += 1
                continue

        if dry_run:
            stats.extracted += 1
            stats.bytes_written += ref.original_size
            continue

        try:
            data = index.extract_ref(ref)
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(data)
            stats.extracted += 1
            stats.bytes_written += len(data)
        except Exception as exc:
            stats.failed += 1
            if stats.errors is not None and len(stats.errors) < 50:
                stats.errors.append(f"{logical_path}: {exc}")

        if progress_every and i % progress_every == 0:
            elapsed = time.monotonic() - t0
            rate = i / elapsed if elapsed > 0 else 0.0
            print(
                f"[{i}/{len(candidates)}] "
                f"new={stats.extracted} skip={stats.skipped_existing} "
                f"fail={stats.failed} "
                f"{stats.bytes_written / (1024**2):.1f} MiB "
                f"({rate:.1f} files/s)",
            )

    stats.elapsed_seconds = time.monotonic() - t0
    manifest = {
        "meta_path": str(meta_path),
        "output_dir": str(output_dir),
        "extensions": sorted(extensions),
        **{k: v for k, v in asdict(stats).items() if k != "errors"},
        "errors": stats.errors,
    }
    if not dry_run:
        (output_dir / "_extract_manifest.json").write_text(
            json.dumps(manifest, indent=2), encoding="utf-8"
        )

    return stats
