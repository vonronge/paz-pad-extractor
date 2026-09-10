from __future__ import annotations

import re
import struct
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from .wwise_bank import HIRC_SWITCH_CONTAINER, ParsedBank, _read_chunks

DEFAULT_PC_PLAYER = 0
DEFAULT_VOICE_CALL_TYPE = 0
WWISE_IDS_LOGICAL = "sound/wwise_ids.h"

@dataclass(frozen=True)
class SwitchGroup:
    switch_id: int
    nodes: tuple[int, ...]

@dataclass(frozen=True)
class ParsedSwitchContainer:
    object_id: int
    children: tuple[int, ...]
    groups: tuple[SwitchGroup, ...]

@dataclass(frozen=True)
class VoiceDialogState:
    voice_type: int = 1
    audio_index: int = 0
    pc_player: int = DEFAULT_PC_PLAYER
    voice_call_type: int = DEFAULT_VOICE_CALL_TYPE
    pitch: int = 50

@dataclass(frozen=True)
class VoiceDialogResult:
    media_id: int
    event_id: int
    object_path: tuple[int, ...]
    switch_path: tuple[str, ...]

def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]

@lru_cache(maxsize=1)
def load_switch_ids() -> dict[str, int]:
    header = _repo_root() / "extracted" / WWISE_IDS_LOGICAL
    if not header.is_file():
        return {}
    text = header.read_text(encoding="utf-8", errors="ignore")
    return {m.group(1): int(m.group(2)) for m in re.finditer(r"(\w+)\s*=\s*(\d+)U", text)}

def switch_id(name: str) -> int | None:
    value = load_switch_ids().get(name)
    return value if value is not None else None

def audio_index_switch_id(audio_index: int) -> int | None:
    return switch_id(f"AUDIO_INDEX_{int(audio_index)}")

def pc_player_switch_id(pc_player: int) -> int | None:
    return switch_id(f"CHARACTER_PC_PLAYER_{int(pc_player)}")

def voice_call_type_switch_id(call_type: int) -> int | None:
    return switch_id(f"CHARACTER_VOICECALLTYPE_{int(call_type)}")

def voice_type_switch_id(voice_type: int) -> int | None:
    return switch_id(f"CHARACTER_PC_VOICETYPE_{int(voice_type)}")

def voice_type_switch_label(voice_type: int) -> str:
    return f"CHARACTER_PC_VOICETYPE_{int(voice_type)}"

def hirc_object_payloads(bank: ParsedBank) -> dict[int, tuple[int, bytes]]:
    if not bank.path.is_file():
        return {}
    body = _read_chunks(bank.path.read_bytes()).get(b"HIRC", b"")
    if len(body) < 4:
        return {}
    count = struct.unpack_from("<I", body, 0)[0]
    pos = 4
    out: dict[int, tuple[int, bytes]] = {}
    for _ in range(count):
        if pos + 9 > len(body):
            break
        obj_type = body[pos]
        obj_size = struct.unpack_from("<I", body, pos + 1)[0]
        obj_id = struct.unpack_from("<I", body, pos + 5)[0]
        payload = body[pos + 9 : pos + 5 + obj_size]
        out[obj_id] = (obj_type, payload)
        pos += 5 + obj_size
    return out

def _parse_switch_groups(
    payload: bytes,
    cur: int,
    group_count: int,
    known_ids: set[int],
) -> tuple[list[SwitchGroup], int, bool]:
    groups: list[SwitchGroup] = []
    for _ in range(group_count):
        if cur + 8 > len(payload):
            return groups, cur, False
        switch_id_val, node_count = struct.unpack_from("<II", payload, cur)
        cur += 8
        if node_count > 64:
            return groups, cur, False
        nodes: list[int] = []
        for _ in range(node_count):
            if cur + 4 > len(payload):
                return groups, cur, False
            node_id = struct.unpack_from("<I", payload, cur)[0]
            cur += 4
            nodes.append(node_id)
        groups.append(SwitchGroup(switch_id=switch_id_val, nodes=tuple(nodes)))
    return groups, cur, True

def parse_switch_container(object_id: int, payload: bytes, known_ids: set[int]) -> ParsedSwitchContainer | None:
    best: ParsedSwitchContainer | None = None
    for off in range(0, max(0, len(payload) - 20)):
        child_count = struct.unpack_from("<I", payload, off)[0]
        if child_count < 1 or child_count > 120:
            continue
        child_end = off + 4 + child_count * 4
        if child_end + 4 > len(payload):
            continue
        children: list[int] = []
        valid = True
        for i in range(child_count):
            child_id = struct.unpack_from("<I", payload, off + 4 + i * 4)[0]
            if child_id not in known_ids:
                valid = False
                break
            children.append(child_id)
        if not valid:
            continue
        group_count = struct.unpack_from("<I", payload, child_end)[0]
        if group_count < 1 or group_count > 600:
            continue
        groups, _, ok = _parse_switch_groups(payload, child_end + 4, group_count, known_ids)
        if not ok or not groups:
            continue
        parsed = ParsedSwitchContainer(object_id=object_id, children=tuple(children), groups=tuple(groups))
        if best is None or len(groups) > len(best.groups):
            best = parsed
    return best

def _pick_switch_node(container: ParsedSwitchContainer, state: VoiceDialogState) -> tuple[int | None, str]:
    group_count = len(container.groups)
    if group_count == 2:
        target = pc_player_switch_id(state.pc_player)
        label = f"CHARACTER_PC_PLAYER_{state.pc_player}"
    elif group_count <= 8:
        target = voice_call_type_switch_id(state.voice_call_type)
        label = f"CHARACTER_VOICECALLTYPE_{state.voice_call_type}"
    else:
        target = audio_index_switch_id(state.audio_index)
        label = f"AUDIO_INDEX_{state.audio_index}"

    if target is not None:
        for group in container.groups:
            if group.switch_id == target and group.nodes:
                return group.nodes[0], label

    audio_idx = int(state.audio_index)
    if group_count > audio_idx and container.groups[audio_idx].nodes:
        return container.groups[audio_idx].nodes[0], f"AUDIO_INDEX_{audio_idx}"

    for group in container.groups:
        if group.nodes:
            return group.nodes[0], f"0x{group.switch_id:08X}"

    if container.children:
        return container.children[0], "default_child"
    return None, "unresolved"

def _media_from_object(
    bank: ParsedBank,
    object_id: int,
    objects: dict[int, tuple[int, bytes]],
    known_ids: set[int],
    state: VoiceDialogState,
    depth: int = 0,
    switch_path: list[str] | None = None,
) -> list[int]:
    if depth > 16:
        return []
    obj_type, payload = objects.get(object_id, (None, b""))
    if obj_type is None:
        return []

    if obj_type == HIRC_SWITCH_CONTAINER:
        container = parse_switch_container(object_id, payload, known_ids)
        if container is None:
            return []
        child_id, label = _pick_switch_node(container, state)
        if child_id is None:
            return []
        if switch_path is not None:
            switch_path.append(label)
        return _media_from_object(
            bank, child_id, objects, known_ids, state, depth + 1, switch_path
        )

    sound = bank.sounds.get(object_id)
    if sound is not None:
        didx_ids = {entry.media_id for entry in bank.media}
        media_id = sound.embedded_media_id
        if media_id not in didx_ids and sound.stream_media_id in didx_ids:
            media_id = sound.stream_media_id
        elif media_id not in didx_ids and sound.stream_media_id:
            media_id = sound.stream_media_id
        return [media_id] if media_id else []

    out: list[int] = []
    for child_id in bank.children.get(object_id, []):
        for media_id in _media_from_object(
            bank, child_id, objects, known_ids, state, depth + 1, switch_path
        ):
            if media_id not in out:
                out.append(media_id)
    return out

def resolve_voice_dialog(bank: ParsedBank, state: VoiceDialogState) -> VoiceDialogResult | None:
    if not bank.events:
        return None
    event_id = next(iter(bank.events))
    ev = bank.events[event_id]
    if not ev.action_ids:
        return None
    root = bank.actions.get(ev.action_ids[0])
    if root is None:
        for action_id in ev.action_ids:
            target = bank.actions.get(action_id)
            if target is not None:
                root = target
                break
    if root is None:
        return None

    objects = hirc_object_payloads(bank)
    known_ids = set(objects)
    switch_path: list[str] = [voice_type_switch_label(state.voice_type)]
    media_ids = _media_from_object(bank, root, objects, known_ids, state, switch_path=switch_path)
    if not media_ids:
        return None
    return VoiceDialogResult(
        media_id=media_ids[0],
        event_id=event_id,
        object_path=(root,),
        switch_path=tuple(switch_path),
    )

def resolve_voice_preview_media(
    bank: ParsedBank,
    voice_type: int,
    audio_index: int,
    *,
    pc_player: int = DEFAULT_PC_PLAYER,
    voice_call_type: int = DEFAULT_VOICE_CALL_TYPE,
) -> int:
    state = VoiceDialogState(
        voice_type=int(voice_type),
        audio_index=int(audio_index),
        pc_player=int(pc_player),
        voice_call_type=int(voice_call_type),
    )
    result = resolve_voice_dialog(bank, state)
    return result.media_id if result is not None else 0
