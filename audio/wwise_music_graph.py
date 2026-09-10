from __future__ import annotations

WWISE_BGM_LOAD_VA = 0x140CABD80
WWISE_AMBIENT_LOAD_VA = 0x140CABED0
WWISE_SETSTATE_VA = 0x140CADB10

import re
import struct
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from .voice_dialog import SwitchGroup, hirc_object_payloads, parse_switch_container
from .wwise_bank import HIRC_SOUND_SFX, HIRC_SWITCH_CONTAINER, ParsedBank

MUTE_WORLD_GROUP = 0xF8E8B8FB
MUTE_ENVIRONMENT_GROUP = 0xE8E8E8BC
MUTE_MUSIC_GROUP = 0xA3A3A3A2
MUSIC_MENUSTATE_GROUP = 1645622703
MUSIC_REGION_GROUP = 3741135221
MUSIC_INDEX_GROUP = 1301167647
ENV_REGION_GROUP = 2618217973
ENV_WEATHER_GROUP = 1071024731
ENV_INTENSITY_RAIN_GROUP = 3594636077
ENV_INTENSITY_WIND_GROUP = 625738409
ENV_PC_INDOOR_GROUP = 315420348

HIRC_MUSIC_SEGMENT = 10
HIRC_MUSIC_TRACK = 11
HIRC_MUSIC_PLAYLIST = 12
HIRC_MUSIC_SWITCH = 13
MUSIC_INDEX_MAX = 0x10E

DEFAULT_MUTE_WORLD_STATE = 3877674602
DEFAULT_MUTE_ENVIRONMENT_STATE = 707773025
DEFAULT_MUTE_MUSIC_STATE = 2296366555

MENU_STATE_IDS = {
    "login": 1191504337,
    "lobby": 1509837722,
    "loading": 3877126638,
    "customize": 628593015,
    "character_create": 628593015,
    "home": 625435475,
    "default": 1865557961,
}

MENU_BGM_BANKS = {
    "login": "BGM_0_0",
    "lobby": "BGM_0_0",
    "loading": "BGM_0_1",
    "customize": "BGM_0_0",
    "character_create": "BGM_0_0",
    "home": "BGM_0_0",
    "default": "BGM_0_0",
    "field": "BGM_1_1001",
    "world": "BGM_1_1001",
}

DEFAULT_AMBIENT_BANK = "Environment_Region_0_0"
WWISE_IDS_LOGICAL = "sound/wwise_ids.h"

@dataclass
class MusicGraphState:
    active_states: dict[int, int] = field(default_factory=dict)
    menu_state: str = "lobby"
    music_region_x: int = 1
    music_region_y: int = 1001
    ambient_region_x: int = 0
    ambient_region_y: int = 0
    music_index: int = 0
    weather_level: int = 0
    rain_intensity: int = 0
    wind_intensity: int = 0
    indoor_level: int = 0
    env_region_index: int = 0

@dataclass(frozen=True)
class SetStateCall:
    group_id: int
    state_id: int
    group_name: str = ""
    state_name: str = ""

def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]

@lru_cache(maxsize=1)
def load_wwise_ids() -> dict[str, int]:
    header = _repo_root() / "extracted" / WWISE_IDS_LOGICAL
    if not header.is_file():
        return {}
    text = header.read_text(encoding="utf-8", errors="ignore")
    return {m.group(1): int(m.group(2)) for m in re.finditer(r"(\w+)\s*=\s*(\d+)U", text)}

def state_id(name: str) -> int | None:
    value = load_wwise_ids().get(name)
    return value if value is not None else None

def music_index_state_id(index: int) -> int | None:
    return state_id(f"MUSIC_INDEX_{int(index)}")

def env_region_state_id(index: int) -> int | None:
    return state_id(f"ENV_REGION_{int(index)}")

def env_weather_state_id(level: int) -> int | None:
    level = max(0, min(int(level), 2))
    return state_id(f"ENV_WEATHER_{level}")

def rain_intensity_state_id(level: int) -> int | None:
    level = max(0, min(int(level), 2))
    return state_id(f"ENV_INTENSITY_RAIN_{level}")

def wind_intensity_state_id(level: int) -> int | None:
    level = max(0, min(int(level), 2))
    return state_id(f"ENV_INTENSITY_WIND_{level}")

def indoor_state_id(level: int) -> int | None:
    mapping = {
        0: "ENV_PC_INDOOR_0_OUT",
        1: "ENV_PC_INDOOR_1_CAVEDEEP",
        2: "ENV_PC_INDOOR_2_CAVEENTRANCE",
        3: "ENV_PC_INDOOR_3_INDOORCLOSED",
        4: "ENV_PC_INDOOR_4_INDOOROPENED",
    }
    name = mapping.get(max(0, min(int(level), 4)))
    return state_id(name) if name else None

def bgm_bank_logical_path(region_x: int, region_y: int) -> str:
    return f"sound/windows/BGM_{int(region_x)}_{int(region_y)}.bnk"

def environment_bank_logical_path(region_x: int, region_y: int) -> str:
    return f"sound/windows/Environment_Region_{int(region_x)}_{int(region_y)}.bnk"

class WwiseMusicGraph:
    def __init__(self, state: MusicGraphState | None = None) -> None:
        self.state = state or MusicGraphState()
        self.history: list[SetStateCall] = []
        self.apply_mute_defaults()

    @property
    def active_states(self) -> dict[int, int]:
        return self.state.active_states

    def set_state(self, group_id: int, state_id_val: int) -> SetStateCall:
        group_id = int(group_id) & 0xFFFFFFFF
        state_id_val = int(state_id_val) & 0xFFFFFFFF
        self.state.active_states[group_id] = state_id_val
        ids = load_wwise_ids()
        id_to_name = {value: key for key, value in ids.items()}
        call = SetStateCall(
            group_id=group_id,
            state_id=state_id_val,
            group_name=id_to_name.get(group_id, ""),
            state_name=id_to_name.get(state_id_val, ""),
        )
        self.history.append(call)
        return call

    def apply_mute_defaults(self) -> list[SetStateCall]:
        return [
            self.set_state(MUTE_WORLD_GROUP, DEFAULT_MUTE_WORLD_STATE),
            self.set_state(MUTE_ENVIRONMENT_GROUP, DEFAULT_MUTE_ENVIRONMENT_STATE),
            self.set_state(MUTE_MUSIC_GROUP, DEFAULT_MUTE_MUSIC_STATE),
        ]

    def set_menu_state(self, name: str) -> SetStateCall:
        key = name.strip().lower().replace("-", "_")
        if key not in MENU_STATE_IDS:
            key = "default"
        self.state.menu_state = key
        return self.set_state(MUSIC_MENUSTATE_GROUP, MENU_STATE_IDS[key])

    def set_music_region(self, region_x: int, region_y: int, music_index: int | None = None) -> list[SetStateCall]:
        self.state.music_region_x = int(region_x)
        self.state.music_region_y = int(region_y)
        calls = [self.set_state(MUSIC_REGION_GROUP, state_id(f"MUSIC_REGION_{int(region_x)}") or int(region_x))]
        if music_index is not None:
            calls.extend(self.set_music_index(music_index))
        return calls

    def set_music_index(self, index: int) -> list[SetStateCall]:
        idx = max(0, min(int(index), MUSIC_INDEX_MAX))
        self.state.music_index = idx
        music_state = music_index_state_id(idx)
        if music_state is None:
            return []
        return [self.set_state(MUSIC_INDEX_GROUP, music_state)]

    def set_ambient_region(self, region_x: int, region_y: int, env_index: int | None = None) -> list[SetStateCall]:
        self.state.ambient_region_x = int(region_x)
        self.state.ambient_region_y = int(region_y)
        calls: list[SetStateCall] = []
        if env_index is not None:
            self.state.env_region_index = int(env_index)
            env_state = env_region_state_id(int(env_index))
            if env_state is not None:
                calls.append(self.set_state(ENV_REGION_GROUP, env_state))
        return calls

    def set_weather(self, level: int, *, rain: int | None = None, wind: int | None = None) -> list[SetStateCall]:
        self.state.weather_level = max(0, min(int(level), 2))
        calls: list[SetStateCall] = []
        weather_state = env_weather_state_id(self.state.weather_level)
        if weather_state is not None:
            calls.append(self.set_state(ENV_WEATHER_GROUP, weather_state))
        if rain is not None:
            self.state.rain_intensity = max(0, min(int(rain), 2))
            rain_state = rain_intensity_state_id(self.state.rain_intensity)
            if rain_state is not None:
                calls.append(self.set_state(ENV_INTENSITY_RAIN_GROUP, rain_state))
        if wind is not None:
            self.state.wind_intensity = max(0, min(int(wind), 2))
            wind_state = wind_intensity_state_id(self.state.wind_intensity)
            if wind_state is not None:
                calls.append(self.set_state(ENV_INTENSITY_WIND_GROUP, wind_state))
        return calls

    def set_indoor(self, level: int) -> SetStateCall | None:
        self.state.indoor_level = max(0, min(int(level), 4))
        indoor = indoor_state_id(self.state.indoor_level)
        if indoor is None:
            return None
        return self.set_state(ENV_PC_INDOOR_GROUP, indoor)

    def resolve_bgm_bank(self, zone_or_event: str | None = None) -> str:
        if zone_or_event:
            key = zone_or_event.strip().lower()
            if key in MENU_BGM_BANKS:
                return f"sound/windows/{MENU_BGM_BANKS[key]}.bnk"
            upper = key.upper().replace(".BNK", "")
            if upper.startswith("BGM_"):
                return f"sound/windows/{upper}.bnk"
        if self.state.menu_state in MENU_BGM_BANKS and self.state.menu_state not in {"field", "world"}:
            return f"sound/windows/{MENU_BGM_BANKS[self.state.menu_state]}.bnk"
        return bgm_bank_logical_path(self.state.music_region_x, self.state.music_region_y)

    def resolve_ambient_bank(self, region_key: str | None = None) -> str:
        if region_key:
            key = region_key.strip().lower().replace(" ", "_")
            if not key or key == "default":
                return f"sound/windows/{DEFAULT_AMBIENT_BANK}.bnk"
            if key.startswith("environment_region_"):
                return f"sound/windows/{key}.bnk"
            if key.startswith("environment_"):
                return f"sound/windows/{key}.bnk"
            return f"sound/windows/Environment_Region_{key}.bnk"
        return environment_bank_logical_path(self.state.ambient_region_x, self.state.ambient_region_y)

    def to_json(self) -> dict[str, object]:
        return {
            "menu_state": self.state.menu_state,
            "music_region": [self.state.music_region_x, self.state.music_region_y],
            "ambient_region": [self.state.ambient_region_x, self.state.ambient_region_y],
            "music_index": self.state.music_index,
            "weather_level": self.state.weather_level,
            "bgm_bank": self.resolve_bgm_bank(),
            "ambient_bank": self.resolve_ambient_bank(),
            "states": [
                {
                    "group_id": call.group_id,
                    "group_hex": f"0x{call.group_id:08X}",
                    "state_id": call.state_id,
                    "state_hex": f"0x{call.state_id:08X}",
                    "group_name": call.group_name,
                    "state_name": call.state_name,
                }
                for call in self.history
            ],
        }

def _pick_state_node(groups: tuple[SwitchGroup, ...], active_states: dict[int, int]) -> int | None:
    for group_id, _state_id_val in active_states.items():
        for group in groups:
            if group.switch_id == group_id and group.nodes:
                return group.nodes[0]
    for group in groups:
        if group.nodes:
            return group.nodes[0]
    return None

def _media_from_music_object(
    bank: ParsedBank,
    object_id: int,
    objects: dict[int, tuple[int, bytes]],
    known_ids: set[int],
    active_states: dict[int, int],
    depth: int = 0,
) -> list[int]:
    if depth > 16:
        return []
    obj_type, payload = objects.get(object_id, (None, b""))
    if obj_type is None:
        return []

    if obj_type in {HIRC_SWITCH_CONTAINER, HIRC_MUSIC_SWITCH}:
        container = parse_switch_container(object_id, payload, known_ids)
        if container is None:
            return []
        child_id = _pick_state_node(container.groups, active_states)
        if child_id is None and container.children:
            child_id = container.children[0]
        if child_id is None:
            return []
        return _media_from_music_object(bank, child_id, objects, known_ids, active_states, depth + 1)

    if obj_type == HIRC_SOUND_SFX and len(payload) >= 12:
        plugin_id = struct.unpack_from("<I", payload, 0)[0]
        if plugin_id == 0x00040001:
            embedded = struct.unpack_from("<I", payload, 5)[0] if len(payload) >= 9 else 0
            stream = struct.unpack_from("<I", payload, 8)[0]
            didx_ids = {entry.media_id for entry in bank.media}
            media_id = embedded if embedded in didx_ids else stream
            return [media_id] if media_id else []

    if obj_type in {HIRC_MUSIC_SEGMENT, HIRC_MUSIC_TRACK, HIRC_MUSIC_PLAYLIST}:
        for off in (5, 8, 12, 16):
            if len(payload) >= off + 4:
                media_id = struct.unpack_from("<I", payload, off)[0]
                if media_id in {entry.media_id for entry in bank.media}:
                    return [media_id]

    out: list[int] = []
    for child_id in bank.children.get(object_id, []):
        for media_id in _media_from_music_object(bank, child_id, objects, known_ids, active_states, depth + 1):
            if media_id not in out:
                out.append(media_id)
    return out

def resolve_music_media(bank: ParsedBank, event_id: int = 0, active_states: dict[int, int] | None = None) -> int:
    states = active_states or {}
    resolved_event = event_id or next(iter(bank.events), 0)
    ev = bank.events.get(resolved_event)
    if ev is None or not ev.action_ids:
        return bank.first_embedded_media_id() or 0

    objects = hirc_object_payloads(bank)
    known_ids = set(objects)
    root = bank.actions.get(ev.action_ids[0])
    if root is None:
        media_ids = bank.media_ids_for_event(resolved_event)
        return media_ids[0] if media_ids else (bank.first_embedded_media_id() or 0)

    media_ids = _media_from_music_object(bank, root, objects, known_ids, states)
    if media_ids:
        return media_ids[0]
    fallback = bank.media_ids_for_event(resolved_event)
    if fallback:
        return fallback[0]
    return bank.first_embedded_media_id() or 0
