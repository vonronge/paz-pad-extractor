from __future__ import annotations

import re
import struct
from dataclasses import dataclass, field
from pathlib import Path

from .wem_decode import needs_vgmstream_decode

CHUNK_BKHD = b"BKHD"
CHUNK_DIDX = b"DIDX"
CHUNK_DATA = b"DATA"
CHUNK_HIRC = b"HIRC"

HIRC_SOUND_SFX = 2
HIRC_EVENT_ACTION = 3
HIRC_EVENT = 4
HIRC_RANDOM_CONTAINER = 5
HIRC_SWITCH_CONTAINER = 6
HIRC_ACTOR_MIXER = 7
HIRC_ACTION_PLAY = 0x0001

CONTAINER_TYPES = frozenset({HIRC_RANDOM_CONTAINER, HIRC_SWITCH_CONTAINER, HIRC_ACTOR_MIXER})

LOBBY_BOOT_EVENT_ID = 0x002E66A0
ACTION_SOUND_BANK = "sound/windows/action.bnk"
WWISE_IDS_HEADER = "Wwise_IDs.h"
SYSTEM_UI_BANK_PATTERN = re.compile(r"^SYSTEM_UI_(\d{2})_(\d{2})\.bnk$", re.IGNORECASE)

@dataclass(frozen=True)
class BnkMedia:
    media_id: int
    offset: int
    size: int

@dataclass
class BnkSound:
    sound_id: int
    embedded_media_id: int
    stream_media_id: int

@dataclass
class BnkEvent:
    event_id: int
    action_ids: list[int] = field(default_factory=list)

@dataclass
class ParsedBank:
    path: Path
    bank_id: int
    version: int
    media: list[BnkMedia]
    data: bytes
    sounds: dict[int, BnkSound]
    events: dict[int, BnkEvent]
    actions: dict[int, int]
    children: dict[int, list[int]]
    object_ids: frozenset[int] = frozenset()
    switch_payloads: dict[int, bytes] = field(default_factory=dict)

    def _child_targets(self, object_id: int) -> list[int]:
        child_ids = self.children.get(object_id, [])
        if child_ids:
            return child_ids
        payload = self.switch_payloads.get(object_id)
        if payload is None:
            return []
        return _child_ids(payload, set(self.object_ids), object_id, byte_scan=True)

    def media_ids_for_event(self, event_id: int) -> list[int]:
        ev = self.events.get(event_id)
        if ev is None:
            return []
        out: list[int] = []
        for action_id in ev.action_ids:
            if action_id in self.sounds:
                for media_id in self._media_from_target(action_id):
                    if media_id not in out:
                        out.append(media_id)
                continue
            target = self.actions.get(action_id)
            if target is None:
                continue
            for media_id in self._media_from_target(target):
                if media_id not in out:
                    out.append(media_id)
        return out

    def _media_from_target(self, object_id: int, depth: int = 0) -> list[int]:
        if depth > 8:
            return []
        sound = self.sounds.get(object_id)
        if sound is not None:
            didx_ids = {entry.media_id for entry in self.media}
            media_id = sound.embedded_media_id
            if media_id not in didx_ids and sound.stream_media_id in didx_ids:
                media_id = sound.stream_media_id
            elif media_id not in didx_ids and sound.stream_media_id:
                media_id = sound.stream_media_id
            return [media_id] if media_id else []
        out: list[int] = []
        for child_id in self._child_targets(object_id):
            for media_id in self._media_from_target(child_id, depth + 1):
                if media_id not in out:
                    out.append(media_id)
        return out

    def embedded_bytes(self, media_id: int) -> bytes | None:
        for entry in self.media:
            if entry.media_id != media_id:
                continue
            end = entry.offset + entry.size
            if end > len(self.data):
                return None
            return self.data[entry.offset : end]
        return None

    def first_embedded_media_id(self) -> int | None:
        return self.media[0].media_id if self.media else None

@dataclass(frozen=True)
class EventAudioRef:
    event_id: int
    name: str | None
    bank: str
    media_id: int
    wem_path: Path | None
    ogg_path: Path | None

    def preferred_path(self) -> Path | None:
        if self.ogg_path is not None and self.ogg_path.is_file():
            return self.ogg_path
        if self.wem_path is not None and self.wem_path.is_file():
            return self.wem_path
        return None

def _read_chunks(data: bytes) -> dict[bytes, bytes]:
    chunks: dict[bytes, bytes] = {}
    pos = 0
    while pos + 8 <= len(data):
        tag = data[pos : pos + 4]
        size = struct.unpack_from("<I", data, pos + 4)[0]
        end = pos + 8 + size
        if end > len(data):
            break
        chunks[tag] = data[pos + 8 : end]
        pos = end
    return chunks

def _child_ids(payload: bytes, object_ids: set[int], self_id: int, *, byte_scan: bool = False) -> list[int]:
    seen: list[int] = []
    step = 1 if byte_scan else 4
    for off in range(0, len(payload) - 3, step):
        child_id = struct.unpack_from("<I", payload, off)[0]
        if child_id in object_ids and child_id != self_id and child_id not in seen:
            seen.append(child_id)
    return seen

def _parse_hirc(
    body: bytes,
) -> tuple[
    dict[int, BnkSound],
    dict[int, BnkEvent],
    dict[int, int],
    dict[int, list[int]],
    dict[int, bytes],
    frozenset[int],
]:
    sounds: dict[int, BnkSound] = {}
    events: dict[int, BnkEvent] = {}
    actions: dict[int, int] = {}
    children: dict[int, list[int]] = {}
    switch_payloads: dict[int, bytes] = {}
    if len(body) < 4:
        return sounds, events, actions, children, switch_payloads, frozenset()

    count = struct.unpack_from("<I", body, 0)[0]
    pos = 4
    raw_objects: dict[int, tuple[int, bytes]] = {}
    for _ in range(count):
        if pos + 5 > len(body):
            break
        obj_type = body[pos]
        obj_size = struct.unpack_from("<I", body, pos + 1)[0]
        obj_end = pos + 5 + obj_size
        if obj_end > len(body) or obj_size < 4:
            break
        obj_id = struct.unpack_from("<I", body, pos + 5)[0]
        payload = body[pos + 9 : obj_end]
        raw_objects[obj_id] = (obj_type, payload)
        pos = obj_end

    object_ids = set(raw_objects)
    for obj_id, (obj_type, payload) in raw_objects.items():
        if obj_type == HIRC_SWITCH_CONTAINER:
            switch_payloads[obj_id] = payload
        if obj_type in CONTAINER_TYPES or obj_type == HIRC_ACTOR_MIXER:
            children[obj_id] = _child_ids(payload, object_ids, obj_id)
        if obj_type == HIRC_SOUND_SFX and len(payload) >= 12:
            plugin_id = struct.unpack_from("<I", payload, 0)[0]
            if plugin_id == 0x00040001:
                embedded_media_id = struct.unpack_from("<I", payload, 5)[0] if len(payload) >= 9 else 0
                stream_media_id = struct.unpack_from("<I", payload, 8)[0]
                sounds[obj_id] = BnkSound(
                    sound_id=obj_id,
                    embedded_media_id=embedded_media_id,
                    stream_media_id=stream_media_id,
                )
        elif obj_type == HIRC_EVENT and payload:
            action_count = payload[0]
            action_ids: list[int] = []
            cursor = 1
            if action_count > 0 and len(payload) >= 8 and payload[1:4] == b"\x00\x00\x00":
                cursor = 4
            for _ in range(action_count):
                if cursor + 4 > len(payload):
                    break
                action_ids.append(struct.unpack_from("<I", payload, cursor)[0])
                cursor += 4
            events[obj_id] = BnkEvent(event_id=obj_id, action_ids=action_ids)
        elif obj_type == HIRC_EVENT_ACTION and payload:
            action_count = payload[0]
            if action_count and len(payload) >= 9:
                action_type, _scope, target_id = struct.unpack_from("<HHI", payload, 1)
                if action_type == HIRC_ACTION_PLAY and target_id in object_ids:
                    events[obj_id] = BnkEvent(event_id=obj_id, action_ids=[target_id])
                    continue
            if len(payload) >= 7:
                actions[obj_id] = struct.unpack_from("<I", payload, 2)[0]

    return sounds, events, actions, children, switch_payloads, frozenset(object_ids)

def parse_bnk(path: Path | bytes) -> ParsedBank:
    data = path.read_bytes() if isinstance(path, Path) else path
    chunks = _read_chunks(data)
    bkhd = chunks.get(CHUNK_BKHD, b"")
    bank_id = struct.unpack_from("<I", bkhd, 0)[0] if len(bkhd) >= 4 else 0
    version = struct.unpack_from("<I", bkhd, 8)[0] if len(bkhd) >= 12 else 0
    media: list[BnkMedia] = []
    didx = chunks.get(CHUNK_DIDX, b"")
    pos = 0
    while pos + 12 <= len(didx):
        media_id, offset, size = struct.unpack_from("<III", didx, pos)
        media.append(BnkMedia(media_id=media_id, offset=offset, size=size))
        pos += 12
    sounds, events, actions, children, switch_payloads, object_ids = _parse_hirc(chunks.get(CHUNK_HIRC, b""))
    return ParsedBank(
        path=path if isinstance(path, Path) else Path("memory.bnk"),
        bank_id=bank_id,
        version=version,
        media=media,
        data=chunks.get(CHUNK_DATA, b""),
        sounds=sounds,
        events=events,
        actions=actions,
        children=children,
        object_ids=object_ids,
        switch_payloads=switch_payloads,
    )

def build_test_bnk(event_id: int, media_id: int, wem_payload: bytes | None = None) -> bytes:
    sound_id = 0x000C0001
    wem = wem_payload if wem_payload is not None else bytes(16)
    sound_payload = struct.pack("<IBxxxI", 0x00040001, 0, media_id)
    event_payload = struct.pack("<B", 1) + struct.pack("<HHI", HIRC_ACTION_PLAY, 0x0001, sound_id)

    def hirc_obj(obj_type: int, obj_id: int, payload: bytes) -> bytes:
        body = struct.pack("<I", obj_id) + payload
        return struct.pack("<B", obj_type) + struct.pack("<I", len(body)) + body

    hirc_body = b"".join([
        hirc_obj(HIRC_SOUND_SFX, sound_id, sound_payload),
        hirc_obj(HIRC_EVENT_ACTION, event_id, event_payload),
    ])
    hirc = struct.pack("<I", 2) + hirc_body
    didx = struct.pack("<III", media_id, 0, len(wem))
    bkhd = struct.pack("<IIIIII", 1, 0, 88, 0, 0, 0)

    def chunk(tag: bytes, body: bytes) -> bytes:
        return tag + struct.pack("<I", len(body)) + body

    return b"".join([chunk(CHUNK_BKHD, bkhd), chunk(CHUNK_DIDX, didx), chunk(CHUNK_DATA, wem), chunk(CHUNK_HIRC, hirc)])

def parse_wwise_ids_header(path: Path) -> dict[int, str]:
    if not path.is_file():
        return {}
    text = path.read_text(encoding="utf-8", errors="ignore")
    out: dict[int, str] = {}
    for match in re.finditer(
        r"(?:static\s+const\s+AkUniqueID|#define)\s+(\w+)\s*[=,]\s*(0x[0-9A-Fa-f]+|\d+)",
        text,
    ):
        name, raw = match.group(1), match.group(2)
        out[int(raw, 0)] = name
    return out

def media_cache_suffix(media_id: int, payload: bytes) -> str:
    if needs_vgmstream_decode(payload):
        return ".wem"
    return ".wav"

class WwiseBankMap:
    def __init__(self, sound_root: Path | None = None) -> None:
        self.sound_root = sound_root
        self.banks: list[ParsedBank] = []
        self.event_names: dict[int, str] = {}
        self.event_refs: dict[int, EventAudioRef] = {}

    def load(self, sound_root: Path | None = None) -> None:
        root = sound_root or self.sound_root
        if root is None:
            raise ValueError("sound_root required")
        self.sound_root = root
        self.banks.clear()
        self.event_names.clear()
        self.event_refs.clear()
        if not root.is_dir():
            return
        self.event_names.update(parse_wwise_ids_header(root / WWISE_IDS_HEADER))
        for bank_path in sorted(root.glob("*.bnk")):
            try:
                bank = parse_bnk(bank_path)
            except (OSError, struct.error, ValueError):
                continue
            self.banks.append(bank)
            for event_id in bank.events:
                media_ids = bank.media_ids_for_event(event_id)
                if media_ids:
                    self._store_ref(event_id, bank_path.name, media_ids[0])

    def load_parsed(self, bank: ParsedBank, bank_label: str) -> None:
        self.banks.append(bank)
        for event_id in bank.events:
            media_ids = bank.media_ids_for_event(event_id)
            if media_ids:
                self._store_ref(event_id, bank_label, media_ids[0])

    def _store_ref(self, event_id: int, bank_name: str, media_id: int) -> None:
        root = self.sound_root or Path(".")
        wem = root / f"{media_id}.wem"
        ogg = root / f"{media_id}.ogg"
        self.event_refs[event_id] = EventAudioRef(
            event_id=event_id,
            name=self.event_names.get(event_id),
            bank=bank_name,
            media_id=media_id,
            wem_path=wem if wem.is_file() else None,
            ogg_path=ogg if ogg.is_file() else None,
        )

    def resolve_event(self, event_id: int) -> Path | None:
        ref = self.event_refs.get(event_id)
        if ref is None:
            return None
        path = ref.preferred_path()
        if path is not None:
            return path
        root = self.sound_root or Path(".")
        for candidate in (root / f"{ref.media_id}.ogg", root / f"{ref.media_id}.wem"):
            if candidate.is_file():
                return candidate
        return None

    def resolve_lobby_boot(self) -> Path | None:
        return self.resolve_event(LOBBY_BOOT_EVENT_ID)

    def to_json_rows(self) -> list[dict[str, object]]:
        rows: list[dict[str, object]] = []
        for event_id in sorted(self.event_refs):
            ref = self.event_refs[event_id]
            path = self.resolve_event(event_id)
            rows.append({
                "event_id": event_id,
                "event_hex": f"0x{event_id:08X}",
                "name": ref.name,
                "bank": ref.bank,
                "media_id": ref.media_id,
                "path": str(path) if path is not None else "",
            })
        return rows
