from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .wwise_bank import LOBBY_BOOT_EVENT_ID, WwiseBankMap
from .wwise_music_graph import SetStateCall, WwiseMusicGraph

@dataclass(frozen=True)
class WwiseEvent:
    name: str
    event_id: int | None = None

    def resolve_id(self) -> int:
        if self.event_id is not None:
            return self.event_id

        h = 2166136261
        for ch in self.name.encode("utf-8"):
            h ^= ch
            h = (h * 16777619) & 0xFFFFFFFF
        return h

LOBBY_UI_EVENTS = {
    "system_ui": WwiseEvent("audioPostEvent_SystemUi"),
    "system_ui_3d": WwiseEvent("audioPostEvent_SystemUi3D"),
    "system_item": WwiseEvent("audioPostEvent_SystemItem"),
    "bgm_setting": WwiseEvent("audioPostEvent_BGMSetting"),
}

KNOWN_EVENT_IDS = {
    0x002E66A0: "PLAY_ACTION_COMMON_MOVEMENT",
    0x0944CE42: "SYSTEM_UI_05_84",
    0x513E6135: "BGM_0_0",
    0x9141046C: "BGM_1_0",
    0xB43A107E: "BGM_1_1001",
    0x59FE4B9A: "BGM_STATE_LOBBY",
    0xAE0738A7: "ENVIRONMENT_REGION_0_0",
    0x55177409: "PLAY_ACTION_COMMON_FOOTSTEP",
    0xB0CA589B: "PLAY_ACTION_FIGHT_ATTACK2_SWING",
    0x40FDDB2C: "system_notify_stub",
}

DEFAULT_UI_EVENT_NAME = "SYSTEM_UI_05_84"

class WwiseBridge:
    def __init__(
        self,
        sink: Callable[[int, str, Path | None], None] | None = None,
        bank_map: WwiseBankMap | None = None,
    ) -> None:
        self._sink = sink or (lambda _id, _name, _path: None)
        self.posted: list[tuple[int, str, Path | None]] = []
        self.bank_map = bank_map or WwiseBankMap()
        self.music_graph = WwiseMusicGraph()
        self.state_calls: list[SetStateCall] = []

    def load_sound_root(self, sound_root: Path) -> None:
        self.bank_map.load(sound_root)

    def resolve_audio_path(self, event_id: int) -> Path | None:
        return self.bank_map.resolve_event(event_id)

    def post_event(self, event: WwiseEvent | str, game_object_id: int = 0) -> int:
        ev = event if isinstance(event, WwiseEvent) else WwiseEvent(event)
        eid = ev.resolve_id()
        label = ev.name
        path = self.resolve_audio_path(eid)
        self.posted.append((eid, label, path))
        self._sink(eid, label, path)
        return eid

    def post_event_id(self, event_id: int, game_object_id: int = 0) -> int:
        label = KNOWN_EVENT_IDS.get(event_id, f"ak_{event_id:08X}")
        path = self.resolve_audio_path(event_id)
        self.posted.append((event_id, label, path))
        self._sink(event_id, label, path)
        return event_id

    def post_lobby_ui(self, key: str = "system_ui") -> int:
        ev = LOBBY_UI_EVENTS.get(key)
        if ev is None:
            raise KeyError(key)
        return self.post_event(ev)

    def post_lobby_boot_sfx(self) -> int:
        return self.post_event_id(LOBBY_BOOT_EVENT_ID)

    def set_state(self, group_id: int, state_id: int) -> SetStateCall:
        call = self.music_graph.set_state(group_id, state_id)
        self.state_calls.append(call)
        return call

    def set_menu_state(self, name: str = "lobby") -> SetStateCall:
        call = self.music_graph.set_menu_state(name)
        self.state_calls.append(call)
        return call

    def set_music_region(self, region_x: int, region_y: int, music_index: int | None = None) -> list[SetStateCall]:
        calls = self.music_graph.set_music_region(region_x, region_y, music_index)
        self.state_calls.extend(calls)
        return calls

    def set_ambient_region(self, region_x: int, region_y: int, env_index: int | None = None) -> list[SetStateCall]:
        calls = self.music_graph.set_ambient_region(region_x, region_y, env_index)
        self.state_calls.extend(calls)
        return calls

    def set_weather(self, level: int, *, rain: int | None = None, wind: int | None = None) -> list[SetStateCall]:
        calls = self.music_graph.set_weather(level, rain=rain, wind=wind)
        self.state_calls.extend(calls)
        return calls

    def resolve_bgm_bank(self, zone_or_event: str | None = None) -> str:
        return self.music_graph.resolve_bgm_bank(zone_or_event)

    def resolve_ambient_bank(self, region_key: str | None = None) -> str:
        return self.music_graph.resolve_ambient_bank(region_key)
