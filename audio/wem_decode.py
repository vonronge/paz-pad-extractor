from __future__ import annotations

import os
import shutil
import struct
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

_VGMSTREAM_NAMES = ("vgmstream-cli", "vgmstream")
_VGMSTREAM_TOOL_NAMES = (
    "vgmstream-cli",
    "vgmstream-cli.exe",
    "vgmstream",
    "vgmstream.exe",
)

_GODOT_WAVE_FORMATS = frozenset({1, 3})
_FMT_SCAN_BYTES = 1024

class VgmstreamNotFoundError(FileNotFoundError):

    def __init__(self, message: str | None = None) -> None:
        super().__init__(
            message
            or (
                "vgmstream-cli not found. Install with scripts/install-vgmstream.sh, "
                "put the binary on PATH, or set VGMSTREAM_CLI to its full path."
            )
        )

def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]

def wave_format_tag(payload: bytes) -> int | None:
    if len(payload) < 44 or not payload.startswith(b"RIFF") or payload[8:12] != b"WAVE":
        return None
    offset = 12
    limit = min(len(payload), _FMT_SCAN_BYTES)
    while offset + 8 <= len(payload) and offset < limit:
        chunk_id = payload[offset : offset + 4]
        chunk_size = struct.unpack_from("<I", payload, offset + 4)[0]
        if chunk_id == b"fmt " and chunk_size >= 2 and offset + 10 <= len(payload):
            return int(struct.unpack_from("<H", payload, offset + 8)[0])
        offset += 8 + chunk_size
        if chunk_size % 2:
            offset += 1
    return None

def is_godot_playable_wav(payload: bytes) -> bool:
    return wave_format_tag(payload) in _GODOT_WAVE_FORMATS

def path_is_godot_playable_wav(path: Path) -> bool:
    if not path.is_file() or path.stat().st_size < 44:
        return False
    with path.open("rb") as handle:
        header = handle.read(_FMT_SCAN_BYTES)
    return is_godot_playable_wav(header)

def needs_vgmstream_decode(payload: bytes) -> bool:
    return bool(payload) and not is_godot_playable_wav(payload)

def find_vgmstream_cli() -> Path | None:
    env = os.environ.get("VGMSTREAM_CLI", "").strip()
    if env:
        candidate = Path(env)
        if candidate.is_file():
            return candidate
    for name in _VGMSTREAM_NAMES:
        found = shutil.which(name)
        if found:
            return Path(found)
    tools_dir = _repo_root() / "tools" / "vgmstream"
    for name in _VGMSTREAM_TOOL_NAMES:
        candidate = tools_dir / name
        if candidate.is_file():
            return candidate
    return None

def output_suffix_for_payload(payload: bytes, preferred: str = ".wav") -> str:
    if needs_vgmstream_decode(payload):
        return ".wem"
    return preferred if preferred.startswith(".") else f".{preferred}"

@dataclass(frozen=True)
class WemDecodeResult:
    path: Path | None
    error: str | None = None
    used_vgmstream: bool = False
    output_format: str = ""

def decode_wem_bytes(
    payload: bytes,
    dest: Path,
    *,
    cli: Path | str | None = None,
) -> Path:
    if not needs_vgmstream_decode(payload):
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(payload)
        return dest

    if path_is_godot_playable_wav(dest):
        return dest

    binary = Path(cli) if cli is not None else find_vgmstream_cli()
    if binary is None or not binary.is_file():
        raise VgmstreamNotFoundError()

    dest.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(suffix=".wem", delete=False) as handle:
        tmp = Path(handle.name)
        handle.write(payload)
    try:
        proc = subprocess.run(
            [str(binary), "-o", str(dest), str(tmp)],
            capture_output=True,
            text=True,
            check=False,
        )
    finally:
        tmp.unlink(missing_ok=True)

    if proc.returncode != 0 or not dest.is_file() or dest.stat().st_size <= 0:
        detail = (proc.stderr or proc.stdout or "").strip()
        message = "vgmstream-cli failed"
        if detail:
            message = f"{message}: {detail}"
        raise RuntimeError(message)
    return dest

def try_decode_wem(
    payload: bytes,
    dest: Path,
    *,
    cli: Path | str | None = None,
) -> WemDecodeResult:
    if path_is_godot_playable_wav(dest):
        return WemDecodeResult(
            path=dest,
            used_vgmstream=needs_vgmstream_decode(payload),
            output_format=dest.suffix.lstrip("."),
        )

    if not needs_vgmstream_decode(payload):
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(payload)
        return WemDecodeResult(path=dest, output_format=dest.suffix.lstrip(".") or "wav")

    binary = Path(cli) if cli is not None else find_vgmstream_cli()
    if binary is None:
        return WemDecodeResult(
            path=None,
            error=(
                "streamed .wem requires vgmstream-cli "
                "(install: scripts/install-vgmstream.sh; or set VGMSTREAM_CLI)"
            ),
        )

    try:
        out = decode_wem_bytes(payload, dest, cli=binary)
    except (RuntimeError, OSError) as exc:
        return WemDecodeResult(path=None, error=str(exc))

    return WemDecodeResult(
        path=out,
        used_vgmstream=True,
        output_format=out.suffix.lstrip(".") or "wav",
    )
