from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .creature_audio_map import CreatureAudioMap
from .wwise_bridge import WwiseEvent
from .wwise_music_graph import WwiseMusicGraph
from .wwise_paz_store import (
    WwisePazStore,
    bgm_bank_logical_path,
    creature_bank_logical_path,
    environment_bank_logical_path,
    voice_bank_logical_path,
)

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Extract BDO Wwise audio on demand")
    parser.add_argument("--client-root", type=Path, default=None)
    parser.add_argument("--meta", type=Path, default=None)
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--event-id", type=lambda x: int(x, 0), default=None)
    parser.add_argument("--event-name", default="")
    parser.add_argument("--creature", default="")
    parser.add_argument("--creature-id", type=int, default=None)
    parser.add_argument("--bgm", default="")
    parser.add_argument("--ambient", default="")
    parser.add_argument("--menu-state", default="")
    parser.add_argument("--music-region-x", type=int, default=None)
    parser.add_argument("--music-region-y", type=int, default=None)
    parser.add_argument("--music-index", type=int, default=None)
    parser.add_argument("--ambient-region-x", type=int, default=None)
    parser.add_argument("--ambient-region-y", type=int, default=None)
    parser.add_argument("--env-region-index", type=int, default=None)
    parser.add_argument("--weather", type=int, default=None)
    parser.add_argument("--rain", type=int, default=None)
    parser.add_argument("--wind", type=int, default=None)
    parser.add_argument("--voice-class", type=int, default=None)
    parser.add_argument("--voice-type", type=int, default=1)
    parser.add_argument("--voice-audio-index", type=int, default=0)
    parser.add_argument("--voice-locale", default="english(us)")
    parser.add_argument("--bank", default="")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--print-path", action="store_true")
    args = parser.parse_args(argv)

    store = WwisePazStore(output_dir=args.output, meta_path=args.meta, client_root=args.client_root)
    graph = WwiseMusicGraph()
    if args.menu_state:
        graph.set_menu_state(args.menu_state)
    if args.music_region_x is not None and args.music_region_y is not None:
        graph.set_music_region(args.music_region_x, args.music_region_y, args.music_index)
    elif args.music_index is not None:
        graph.set_music_index(args.music_index)
    if args.ambient_region_x is not None and args.ambient_region_y is not None:
        graph.set_ambient_region(args.ambient_region_x, args.ambient_region_y, args.env_region_index)
    if args.weather is not None:
        graph.set_weather(args.weather, rain=args.rain, wind=args.wind)

    if args.bgm:
        result = store.extract_bgm(args.bgm, graph)
    elif args.ambient:
        result = store.extract_ambient(args.ambient, graph)
    elif args.creature_id is not None:
        creature_map = CreatureAudioMap.load()
        entry = creature_map.resolve_creature_id(args.creature_id)
        if entry is None:
            parser.error(f"unknown creature_id: {args.creature_id}")
        result = store.extract_creature(entry.spawn_key)
        if entry.event_id and not result.event_id:
            result.event_id = entry.event_id
        if entry.bank_logical:
            result.bank_logical = entry.bank_logical
    elif args.creature:
        result = store.extract_creature(args.creature)
    elif args.voice_class is not None:
        result = store.extract_voice_preview(
            args.voice_class,
            args.voice_type,
            args.voice_audio_index,
            args.voice_locale,
        )
    elif args.event_id is not None or args.event_name:
        if args.event_id is not None:
            result = store.extract_event(args.event_id, args.bank or None)
        else:
            result = store.extract_event_by_name(args.event_name)
    else:
        parser.error("one of --event-id, --bgm, --ambient, --creature, --creature-id, or --voice-class is required")

    playable = (
        result.path is not None
        and result.path.suffix.lower() in {".wav", ".ogg"}
        and (result.decode_error == "" or not result.needs_vgmstream)
    )
    payload = {
        "ok": playable,
        "path": str(result.path) if result.path is not None else "",
        "event_id": result.event_id,
        "event_hex": f"0x{result.event_id:08X}" if result.event_id else "",
        "media_id": result.media_id,
        "bank_logical": result.bank_logical,
        "needs_vgmstream": result.needs_vgmstream,
        "decode_error": result.decode_error,
    }
    if args.bgm:
        payload["bgm_bank"] = graph.resolve_bgm_bank(args.bgm)
        payload["music_graph"] = graph.to_json()
    elif args.ambient:
        payload["ambient_bank"] = graph.resolve_ambient_bank(args.ambient)
        payload["music_graph"] = graph.to_json()
    elif args.voice_class is not None:
        payload["voice_bank"] = voice_bank_logical_path(
            args.voice_class, args.voice_type, args.voice_locale
        )
    elif args.creature_id is not None:
        creature_map = CreatureAudioMap.load()
        entry = creature_map.resolve_creature_id(args.creature_id)
        if entry is not None:
            payload["creature_id"] = args.creature_id
            payload["spawn_key"] = entry.spawn_key
            payload["creature_bank"] = entry.bank_logical
            if entry.event_name:
                payload["event_name"] = entry.event_name
    elif args.creature:
        payload["creature_bank"] = creature_bank_logical_path(args.creature)

    if args.json:
        json.dump(payload, sys.stdout, indent=2)
        sys.stdout.write("\n")
    elif args.print_path:
        if result.path is None:
            return 1
        print(result.path)
    else:
        print(payload)
    return 0 if playable else 1

if __name__ == "__main__":
    raise SystemExit(main())
