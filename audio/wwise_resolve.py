from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .creature_audio_map import CreatureAudioMap
from .wwise_bank import LOBBY_BOOT_EVENT_ID, WwiseBankMap
from .wwise_bridge import KNOWN_EVENT_IDS
from .wwise_paz_store import WwisePazStore, default_sound_root

def resolve_map(client_root: Path, event_ids: list[int] | None = None) -> dict[str, object]:
    sound_root = default_sound_root(client_root)
    ids = event_ids or sorted({LOBBY_BOOT_EVENT_ID, *KNOWN_EVENT_IDS.keys()})
    if sound_root.is_dir():
        bank_map = WwiseBankMap(sound_root)
        bank_map.load()
        events: list[dict[str, object]] = []
        for event_id in ids:
            path = bank_map.resolve_event(event_id)
            events.append({
                "event_id": event_id,
                "event_hex": f"0x{event_id:08X}",
                "label": KNOWN_EVENT_IDS.get(event_id, bank_map.event_names.get(event_id, "")),
                "path": str(path) if path is not None else "",
            })
        boot_path = bank_map.resolve_lobby_boot()
        return {
            "ok": True,
            "sound_root": str(sound_root),
            "bank_count": len(bank_map.banks),
            "mapped_events": len(bank_map.event_refs),
            "lobby_boot_event_id": LOBBY_BOOT_EVENT_ID,
            "lobby_boot_path": str(boot_path) if boot_path is not None else "",
            "events": events,
            "rows": bank_map.to_json_rows(),
        }

    store = WwisePazStore(client_root=client_root)
    payload = store.resolve_map(ids)
    for row in payload["events"]:
        event_id = int(row["event_id"])
        row["label"] = KNOWN_EVENT_IDS.get(event_id, row.get("label", ""))
    return payload

def resolve_creature_map() -> dict[str, object]:
    creature_map = CreatureAudioMap.load()
    payload = creature_map.to_json()
    payload["ok"] = True
    return payload

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Resolve BDO Wwise event to OGG/WEM paths")
    parser.add_argument("--client-root", type=Path, required=True)
    parser.add_argument("--creature-map", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    if args.creature_map:
        payload = resolve_creature_map()
    else:
        payload = resolve_map(args.client_root)
    if args.json:
        json.dump(payload, sys.stdout, indent=2)
        sys.stdout.write("\n")
    else:
        print(payload)
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
