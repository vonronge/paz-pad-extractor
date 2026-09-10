from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

from archive.bulk_extract import bulk_extract
from archive.paz_index import PazIndex
from mesh.extract_meshes import DEFAULT_MESH_EXTENSIONS, mesh_paths
from mesh.odm_loader import default_warrior_preview_parts
from mesh.par_header import PAR_MAGIC

MESH_MANIFEST_NAME = "_extract_mesh_manifest.json"
MESH_VERIFY_REPORT_NAME = "_extract_mesh_verify.json"

@dataclass
class MeshVerifyReport:
    meta_path: str
    output_dir: str
    extensions: list[str]
    expected: int = 0
    present: int = 0
    missing: int = 0
    wrong_size: int = 0
    missing_sample: list[str] = field(default_factory=list)
    wrong_size_sample: list[dict[str, object]] = field(default_factory=list)
    warrior_spot_checks: list[dict[str, object]] = field(default_factory=list)
    extension_counts: dict[str, int] = field(default_factory=dict)
    ok: bool = False

    def to_dict(self) -> dict[str, object]:
        return asdict(self)

def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]

def _default_meta_path() -> Path:
    return _repo_root() / "BDO Client/Paz/pad00000.meta"

def _default_output_dir() -> Path:
    return _repo_root() / "extracted"

def _spot_check_warrior_pacs(output_dir: Path) -> list[dict[str, object]]:
    checks: list[dict[str, object]] = []
    for path in default_warrior_preview_parts(output_dir):
        entry: dict[str, object] = {"path": str(path.relative_to(output_dir))}
        if not path.is_file():
            entry["ok"] = False
            entry["error"] = "missing"
            checks.append(entry)
            continue
        data = path.read_bytes()
        entry["bytes"] = len(data)
        entry["ok"] = len(data) >= 4 and data[:4] == PAR_MAGIC
        if not entry["ok"]:
            entry["error"] = f"expected PAR magic, got {data[:4]!r}"
        checks.append(entry)
    return checks

def verify_mesh_extract(
    meta_path: Path,
    output_dir: Path,
    extensions: frozenset[str] = DEFAULT_MESH_EXTENSIONS,
    missing_sample_limit: int = 20,
) -> MeshVerifyReport:
    index = PazIndex.load(meta_path)
    candidates = mesh_paths(index, extensions)
    report = MeshVerifyReport(
        meta_path=str(meta_path),
        output_dir=str(output_dir),
        extensions=sorted(extensions),
        expected=len(candidates),
    )

    for logical_path, ref in candidates:
        ext = Path(logical_path).suffix.lower()
        report.extension_counts[ext] = report.extension_counts.get(ext, 0) + 1
        dest = output_dir / logical_path
        if not dest.is_file():
            report.missing += 1
            if len(report.missing_sample) < missing_sample_limit:
                report.missing_sample.append(logical_path)
            continue
        size = dest.stat().st_size
        if size != ref.original_size:
            report.wrong_size += 1
            if len(report.wrong_size_sample) < missing_sample_limit:
                report.wrong_size_sample.append(
                    {
                        "path": logical_path,
                        "on_disk": size,
                        "expected": ref.original_size,
                    }
                )
            continue
        report.present += 1

    report.warrior_spot_checks = _spot_check_warrior_pacs(output_dir)
    report.ok = (
        report.missing == 0
        and report.wrong_size == 0
        and report.present == report.expected
        and all(bool(c.get("ok")) for c in report.warrior_spot_checks)
    )
    return report

def write_mesh_manifest(report: MeshVerifyReport, output_dir: Path) -> Path:
    manifest = {
        "meta_path": report.meta_path,
        "output_dir": report.output_dir,
        "extensions": report.extensions,
        "total_candidates": report.expected,
        "present": report.present,
        "missing": report.missing,
        "wrong_size": report.wrong_size,
        "extension_counts": report.extension_counts,
        "verified_ok": report.ok,
        "warrior_spot_checks": report.warrior_spot_checks,
    }
    path = output_dir / MESH_MANIFEST_NAME
    path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return path

def write_verify_report(report: MeshVerifyReport, output_dir: Path) -> Path:
    path = output_dir / MESH_VERIFY_REPORT_NAME
    path.write_text(json.dumps(report.to_dict(), indent=2), encoding="utf-8")
    return path

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Verify extracted mesh PAR files against the PAZ index.",
    )
    parser.add_argument("--meta", type=Path, default=_default_meta_path())
    parser.add_argument("--output", type=Path, default=_default_output_dir())
    parser.add_argument(
        "--extensions",
        default=",".join(sorted(DEFAULT_MESH_EXTENSIONS)),
    )
    parser.add_argument(
        "--fix",
        action="store_true",
        help="Re-extract missing or wrong-size mesh files from PAZ.",
    )
    parser.add_argument("--no-write", action="store_true", help="Skip manifest/report files.")
    args = parser.parse_args(argv)

    if not args.meta.is_file():
        print(f"meta not found: {args.meta}", file=sys.stderr)
        return 1

    extensions = frozenset(
        ext if ext.startswith(".") else f".{ext}"
        for ext in args.extensions.lower().split(",")
        if ext.strip()
    )

    report = verify_mesh_extract(args.meta, args.output, extensions=extensions)
    if args.fix and not report.ok:
        stats = bulk_extract(
            meta_path=args.meta,
            output_dir=args.output,
            extensions=extensions,
            progress_every=500,
            dry_run=False,
        )
        if stats.failed:
            print(f"fix pass failed: {stats.failed} errors", file=sys.stderr)
            return 1
        report = verify_mesh_extract(args.meta, args.output, extensions=extensions)

    if not args.no_write:
        args.output.mkdir(parents=True, exist_ok=True)
        write_mesh_manifest(report, args.output)
        write_verify_report(report, args.output)

    print(
        f"mesh verify: expected={report.expected} present={report.present} "
        f"missing={report.missing} wrong_size={report.wrong_size} ok={report.ok}"
    )
    return 0 if report.ok else 1

if __name__ == "__main__":
    raise SystemExit(main())
