from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .wwise_bank import parse_wwise_ids_header
from .wwise_paz_store import WWISE_IDS_LOGICAL, creature_bank_logical_path, default_meta_path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_WWISE_IDS = _REPO_ROOT / "extracted" / "sound" / "wwise_ids.h"
_OVERRIDES_PATH = Path(__file__).resolve().parent / "data" / "creature_id_overrides.json"
_BANK_PREFIX = "sound/windows/"

@dataclass(frozen=True)
class CreatureAudioEntry:
    spawn_key: str
    bank_logical: str
    event_id: int = 0
    event_name: str = ""
    creature_id: int | None = None
    source: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "spawn_key": self.spawn_key,
            "bank_logical": self.bank_logical,
            "event_id": self.event_id,
            "event_hex": f"0x{self.event_id:08X}" if self.event_id else "",
            "event_name": self.event_name,
            "creature_id": self.creature_id,
            "source": self.source,
        }

def _load_paz_creature_bank_names() -> list[str]:
    meta = default_meta_path()
    if not meta.is_file():
        return []
    from archive.paz_index import PazIndex

    idx = PazIndex.load(meta, paz_dir=meta.parent)
    names: list[str] = []
    for filename in idx.file_names:
        low = filename.lower()
        if not low.endswith(".bnk"):
            continue
        if low.startswith("creature_") or "social_creature_" in low:
            names.append(filename)
    return sorted(set(names))

def bank_filename_to_spawn_key(filename: str) -> str:
    base = filename.lower().removesuffix(".bnk")
    if base.startswith("creature_"):
        body = base[len("creature_") :]
        if body.endswith("_common_0"):
            return body[: -len("_common_0")]
        if body.endswith("_0"):
            return body[: -len("_0")]
        return body
    marker = "social_creature_"
    if marker in base:
        return base.split(marker, 1)[1]
    return base

def bank_filename_to_logical(filename: str) -> str:
    return f"{_BANK_PREFIX}{filename.lower()}"

def _creature_event_names(wwise_ids_path: Path | None = None) -> dict[int, str]:
    path = wwise_ids_path or _DEFAULT_WWISE_IDS
    if not path.is_file():
        return {}
    names = parse_wwise_ids_header(path)
    return {event_id: name for event_id, name in names.items() if name.startswith("CREATURE_")}

def _event_name_for_spawn_key(spawn_key: str, event_names: dict[int, str]) -> tuple[int, str]:
    key = spawn_key.strip().lower()
    candidates = [
        f"CREATURE_{key.upper()}_COMMON_0",
        f"CREATURE_{key.upper()}_0",
        f"CREATURE_{key.upper()}",
        f"CREATURE_{key.upper().replace('_', '')}_COMMON_0",
    ]
    if key.startswith("stand_") or key.startswith("moving_"):
        candidates.insert(0, f"CREATURE_{key.upper()}")
    name_to_id = {name: event_id for event_id, name in event_names.items()}
    for candidate in candidates:
        if candidate in name_to_id:
            return name_to_id[candidate], candidate
    for event_id, name in event_names.items():
        tail = name[len("CREATURE_") :].lower()
        if tail.startswith(f"{key}_") or tail == key:
            return event_id, name
    return 0, ""

def build_bank_catalog(
    wwise_ids_path: Path | None = None,
    paz_bank_names: list[str] | None = None,
) -> dict[str, dict[str, Any]]:
    event_names = _creature_event_names(wwise_ids_path)
    bank_names = paz_bank_names if paz_bank_names is not None else _load_paz_creature_bank_names()
    catalog: dict[str, dict[str, Any]] = {}
    for filename in bank_names:
        spawn_key = bank_filename_to_spawn_key(filename)
        event_id, event_name = _event_name_for_spawn_key(spawn_key, event_names)
        catalog[spawn_key] = {
            "spawn_key": spawn_key,
            "bank_logical": bank_filename_to_logical(filename),
            "bank_filename": filename.lower(),
            "event_id": event_id,
            "event_hex": f"0x{event_id:08X}" if event_id else "",
            "event_name": event_name,
        }
    return catalog

def load_creature_id_overrides(path: Path | None = None) -> dict[int, dict[str, Any]]:
    overrides_path = path or _OVERRIDES_PATH
    if not overrides_path.is_file():
        return {}
    payload = json.loads(overrides_path.read_text(encoding="utf-8"))
    rows = payload.get("by_creature_id", payload)
    out: dict[int, dict[str, Any]] = {}
    for raw_id, row in rows.items():
        if not isinstance(row, dict):
            continue
        out[int(raw_id)] = row
    return out

class CreatureAudioMap:
    def __init__(
        self,
        catalog: dict[str, dict[str, Any]] | None = None,
        overrides: dict[int, dict[str, Any]] | None = None,
    ) -> None:
        self.catalog = catalog or build_bank_catalog()
        self.overrides = overrides if overrides is not None else load_creature_id_overrides()

    @classmethod
    def load(cls) -> CreatureAudioMap:
        return cls()

    def resolve_spawn_key(self, spawn_key: str) -> CreatureAudioEntry | None:
        key = spawn_key.strip().lower().replace(" ", "_")
        if key.startswith("creature_"):
            key = key[len("creature_") :]
        row = self.catalog.get(key)
        if row is None:
            logical = creature_bank_logical_path(key)
            matched = next((item for item in self.catalog.values() if item["bank_logical"] == logical), None)
            if matched is not None:
                row = matched
            else:
                return CreatureAudioEntry(
                    spawn_key=key,
                    bank_logical=logical,
                    source="derived_path",
                )
        return CreatureAudioEntry(
            spawn_key=row["spawn_key"],
            bank_logical=row["bank_logical"],
            event_id=int(row.get("event_id", 0)),
            event_name=str(row.get("event_name", "")),
            source="catalog",
        )

    def resolve_creature_id(self, creature_id: int) -> CreatureAudioEntry | None:
        row = self.overrides.get(int(creature_id))
        if row is None:
            return None
        spawn_key = str(row.get("spawn_key", "")).strip().lower()
        if not spawn_key:
            return None
        entry = self.resolve_spawn_key(spawn_key)
        if entry is None:
            return None
        return CreatureAudioEntry(
            spawn_key=entry.spawn_key,
            bank_logical=entry.bank_logical,
            event_id=entry.event_id,
            event_name=entry.event_name,
            creature_id=int(creature_id),
            source=str(row.get("source", "override")),
        )

    def to_json(self) -> dict[str, Any]:
        return {
            "bank_count": len(self.catalog),
            "creature_id_count": len(self.overrides),
            "banks": self.catalog,
            "by_creature_id": {
                str(creature_id): {
                    **row,
                    "resolved": (
                        self.resolve_creature_id(creature_id).to_dict()
                        if self.resolve_creature_id(creature_id)
                        else {}
                    ),
                }
                for creature_id, row in sorted(self.overrides.items())
            },
            "wwise_ids_logical": WWISE_IDS_LOGICAL,
        }
