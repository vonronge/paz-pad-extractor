from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path

from archive.paz_index import PazArchiveRef, PazIndex

EXTRACT_PHASES: list[tuple[str, frozenset[str] | None]] = [
    ("probe", frozenset({".probe", ".vnl", ".vnm", ".vnt"})),
    ("spawn", frozenset({".spawninfo", ".pab", ".pat", ".pad"})),
    ("scripts", frozenset({".luac", ".lua", ".ai", ".txt", ".srt", ".xml"})),
    ("collision", frozenset({".collisiondata2", ".dbss", ".bss"})),
    ("motion", frozenset({".paa", ".pae", ".paac", ".pah", ".paem"})),
    ("meshes", frozenset({".pam", ".pac", ".combine", ".lod", ".mapdata"})),
    ("ui", frozenset({".png", ".jpg", ".jpeg", ".gif", ".woff", ".ttf", ".woff2"})),
    ("video", frozenset({".webm", ".bik"})),
    ("audio", frozenset({".wem", ".pcm", ".bnk"})),
    ("dds", frozenset({".dds"})),
    ("remainder", None),
]

_PHASE_EXTENSIONS: frozenset[str] = frozenset(
    ext for _, exts in EXTRACT_PHASES if exts for ext in exts
)

@dataclass
class ExtractAllStats:
    total_candidates: int = 0
    extracted: int = 0
    skipped_existing: int = 0
    failed: int = 0
    bytes_written: int = 0
    elapsed_seconds: float = 0.0
    errors: list[str] | None = None
    phase: str | None = None
    prefix: str | None = None

def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]

def default_meta_path() -> Path:
    return _repo_root() / "BDO Client/Paz/pad00000.meta"

def default_output_dir() -> Path:
    return _repo_root() / "extracted"

def _normalize_prefix(prefix: str | None) -> str | None:
    if not prefix:
        return None
    p = prefix.replace("\\", "/").strip("/")
    return p.lower() + "/"

def candidate_paths(
    index: PazIndex,
    extensions: frozenset[str] | None = None,
    prefix: str | None = None,
    exclude_extensions: frozenset[str] | None = None,
) -> list[tuple[str, PazArchiveRef]]:
    norm_prefix = _normalize_prefix(prefix)
    out: list[tuple[str, PazArchiveRef]] = []
    for entry in index.entries:
        logical_path = index.path_of(entry)
        low = logical_path.lower()
        if norm_prefix is not None and not low.startswith(norm_prefix):
            continue
        ext = Path(logical_path).suffix.lower()
        if extensions is not None and ext not in extensions:
            continue
        if exclude_extensions and ext in exclude_extensions:
            continue
        ref = PazArchiveRef.from_entry(
            entry,
            entry.hash_key,
            logical_path=logical_path,
            verified=True,
        )
        out.append((logical_path, ref))
    return out

def extensions_for_phase(phase: str) -> frozenset[str] | None:
    for name, exts in EXTRACT_PHASES:
        if name == phase:
            return exts
    raise ValueError(f"unknown phase: {phase!r}")

def phase_names() -> list[str]:
    return [name for name, _ in EXTRACT_PHASES]

def extract_paths(
    meta_path: Path,
    output_dir: Path,
    extensions: frozenset[str] | None = None,
    prefix: str | None = None,
    exclude_extensions: frozenset[str] | None = None,
    limit: int | None = None,
    progress_every: int = 500,
    dry_run: bool = False,
    phase: str | None = None,
    log_path: Path | None = None,
) -> ExtractAllStats:
    stats = ExtractAllStats(errors=[], phase=phase, prefix=prefix)
    t0 = time.monotonic()

    index = PazIndex.load(meta_path, paz_dir=meta_path.parent)
    candidates = candidate_paths(
        index,
        extensions=extensions,
        prefix=prefix,
        exclude_extensions=exclude_extensions,
    )
    stats.total_candidates = len(candidates)
    if limit is not None:
        candidates = candidates[:limit]

    output_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_path.open("a", encoding="utf-8") if log_path and not dry_run else None

    def log(msg: str) -> None:
        print(msg, file=sys.stderr)
        if log_file is not None:
            log_file.write(msg + "\n")
            log_file.flush()

    if phase:
        log(f"phase={phase} candidates={stats.total_candidates}")

    for i, (logical_path, ref) in enumerate(candidates, start=1):
        dest = output_dir / logical_path
        if dest.is_file() and dest.stat().st_size == ref.original_size:
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
            log(
                f"[{i}/{len(candidates)}] "
                f"new={stats.extracted} skip={stats.skipped_existing} "
                f"fail={stats.failed} "
                f"{stats.bytes_written / (1024**2):.1f} MiB "
                f"({rate:.1f} files/s)"
            )

    stats.elapsed_seconds = time.monotonic() - t0

    if not dry_run:
        manifest = {
            "meta_path": str(meta_path),
            "output_dir": str(output_dir),
            "phase": phase,
            "prefix": prefix,
            "extensions": sorted(extensions) if extensions else None,
            **{k: v for k, v in asdict(stats).items() if k != "errors"},
            "errors": stats.errors,
        }
        name = f"_extract_{phase}_manifest.json" if phase else "_extract_all_manifest.json"
        (output_dir / name).write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    if log_file is not None:
        log_file.close()

    return stats

def run_all_phases(
    meta_path: Path,
    output_dir: Path,
    prefix: str | None = None,
    progress_every: int = 500,
    dry_run: bool = False,
    phases: list[str] | None = None,
) -> list[ExtractAllStats]:
    names = phases or phase_names()
    log_path = output_dir / "_extract_all.log"
    results: list[ExtractAllStats] = []
    for name in names:
        exts = extensions_for_phase(name)
        exclude = _PHASE_EXTENSIONS if name == "remainder" else None
        results.append(
            extract_paths(
                meta_path=meta_path,
                output_dir=output_dir,
                extensions=exts,
                prefix=prefix,
                exclude_extensions=exclude,
                progress_every=progress_every,
                dry_run=dry_run,
                phase=name,
                log_path=log_path,
            )
        )
    return results

def _parse_extensions(raw: str) -> frozenset[str]:
    return frozenset(
        ext if ext.startswith(".") else f".{ext}"
        for ext in raw.lower().split(",")
        if ext.strip()
    )

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Bulk-extract BDO PAZ logical paths into extracted/ (idempotent)."
    )
    parser.add_argument("--meta", type=Path, default=default_meta_path())
    parser.add_argument("--output", type=Path, default=default_output_dir())
    parser.add_argument("--extensions", default=None)
    parser.add_argument("--prefix", default=None)
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--phase", choices=phase_names())
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--progress-every", type=int, default=500)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    if not args.meta.is_file():
        print(f"meta not found: {args.meta}", file=sys.stderr)
        return 1

    if args.all:
        results = run_all_phases(
            meta_path=args.meta,
            output_dir=args.output,
            prefix=args.prefix,
            progress_every=args.progress_every,
            dry_run=args.dry_run,
            phases=[args.phase] if args.phase else None,
        )
        print(
            f"done: phases={len(results)} "
            f"extracted={sum(s.extracted for s in results)} "
            f"skipped={sum(s.skipped_existing for s in results)} "
            f"failed={sum(s.failed for s in results)} "
            f"bytes={sum(s.bytes_written for s in results)} "
            f"elapsed={sum(s.elapsed_seconds for s in results):.1f}s"
        )
        return 1 if any(s.failed for s in results) else 0

    extensions = _parse_extensions(args.extensions) if args.extensions else None
    if args.phase and extensions is None:
        extensions = extensions_for_phase(args.phase)

    stats = extract_paths(
        meta_path=args.meta,
        output_dir=args.output,
        extensions=extensions,
        prefix=args.prefix,
        limit=args.limit,
        progress_every=args.progress_every,
        dry_run=args.dry_run,
        phase=args.phase,
        log_path=args.output / "_extract_all.log",
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
