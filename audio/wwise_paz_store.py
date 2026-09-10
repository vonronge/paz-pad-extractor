from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

from archive.paz_index import PazIndex

from .voice_dialog import VoiceDialogState, resolve_voice_dialog
from .wwise_music_graph import WwiseMusicGraph, resolve_music_media
from .wem_decode import (
    needs_vgmstream_decode,
    output_suffix_for_payload,
    path_is_godot_playable_wav,
    try_decode_wem,
)
from .wwise_bank import (
    ACTION_SOUND_BANK,
    LOBBY_BOOT_EVENT_ID,
    ParsedBank,
    WwiseBankMap,
    parse_bnk,
    parse_wwise_ids_header,
)

DEFAULT_VOICE_LOCALE = "english(us)"
CREATURE_BANK_PREFIX = "sound/windows/creature_"
SYSTEM_SOUND_BANK = "sound/windows/systemsound.bnk"
WWISE_IDS_LOGICAL = "sound/wwise_ids.h"

BGM_ZONE_BANKS: dict[str, str] = {
    "lobby": "BGM_0_0",
    "login": "BGM_0_0",
    "loading": "BGM_0_1",
    "field": "BGM_1_1001",
    "world": "BGM_1_1001",
    "default": "BGM_1_1001",
}
DEFAULT_AMBIENT_BANK = "Environment_Region_0_0"

_CLASS_GENDER: dict[int, str] = {
    0: "man", 4: "woman", 8: "woman", 12: "man", 16: "woman", 20: "man",
    22: "man", 24: "woman", 25: "woman", 26: "man", 27: "woman", 28: "man", 31: "woman",
}

def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]

def default_meta_path() -> Path:
    return _repo_root() / "BDO Client/Paz/pad00000.meta"

def default_output_dir() -> Path:
    return _repo_root() / "extracted"

def default_sound_root(client_root: Path) -> Path:
    return client_root / "Sound"

def normalize_locale(locale: str | None) -> str:
    if not locale or not locale.strip():
        return DEFAULT_VOICE_LOCALE
    return locale.strip()

def voice_bank_logical_path(
    class_type: int,
    voice_type: int,
    locale: str | None = None,
    *,
    lobby: bool = True,
) -> str:
    from lobby.customization_voice import resolve_voice_bank_logical_path

    loc = normalize_locale(locale)
    if lobby:
        return resolve_voice_bank_logical_path(class_type, voice_type, loc)
    gender = _CLASS_GENDER.get(class_type, "man")
    prefix = "mn" if gender == "man" else "wm"
    pack = max(1, min(int(voice_type), 10))
    return f"sound/windows/{loc}/pc_vce_{prefix}_common{pack}_1_0.bnk"

def bgm_bank_logical_path(zone_or_event: str) -> str:
    key = zone_or_event.strip()
    if not key:
        return f"sound/windows/{BGM_ZONE_BANKS['default']}.bnk"
    lowered = key.lower()
    if lowered in BGM_ZONE_BANKS:
        return f"sound/windows/{BGM_ZONE_BANKS[lowered]}.bnk"
    upper = key.upper().replace(".BNK", "")
    if upper.startswith("BGM_"):
        return f"sound/windows/{upper}.bnk"
    return f"sound/windows/{BGM_ZONE_BANKS['default']}.bnk"

def environment_bank_logical_path(region_key: str = "default") -> str:
    key = region_key.strip().lower().replace(" ", "_")
    if not key or key == "default":
        return f"sound/windows/{DEFAULT_AMBIENT_BANK}.bnk"
    if key.startswith("environment_region_"):
        return f"sound/windows/{key}.bnk"
    if key.startswith("environment_"):
        return f"sound/windows/{key}.bnk"
    return f"sound/windows/Environment_Region_{key}.bnk"

def creature_bank_logical_path(creature_key: str) -> str:
    key = creature_key.strip().lower().replace(" ", "_")
    if key.startswith("creature_"):
        key = key[len("creature_") :]
    if not key.endswith("_0"):
        if key.endswith("_common"):
            key = f"{key}_0"
        elif "_common_" not in key:
            key = f"{key}_common_0"
    return f"{CREATURE_BANK_PREFIX}{key}.bnk"

@dataclass
class MediaCacheResult:
    path: Path | None
    decode_error: str = ""
    needs_vgmstream: bool = False

@dataclass
class AudioExtractResult:
    path: Path | None
    logical_path: str = ""
    event_id: int = 0
    media_id: int = 0
    bank_logical: str = ""
    decode_error: str = ""
    needs_vgmstream: bool = False

class WwisePazStore:
    def __init__(
        self,
        output_dir: Path | None = None,
        meta_path: Path | None = None,
        client_root: Path | None = None,
        index: PazIndex | None = None,
    ) -> None:
        self.output_dir = output_dir or default_output_dir()
        self.meta_path = meta_path or default_meta_path()
        self.client_root = client_root or _repo_root() / "BDO Client"
        self.sound_root = default_sound_root(self.client_root)
        self._index = index
        self._event_names: dict[int, str] | None = None
        self._bank_cache: dict[str, ParsedBank] = {}

    @property
    def index(self) -> PazIndex:
        if self._index is None:
            if not self.meta_path.is_file():
                raise FileNotFoundError(self.meta_path)
            self._index = PazIndex.load(self.meta_path, paz_dir=self.meta_path.parent)
        return self._index

    def event_names(self) -> dict[int, str]:
        if self._event_names is not None:
            return self._event_names
        loose = self.sound_root / "Wwise_IDs.h"
        if loose.is_file():
            self._event_names = parse_wwise_ids_header(loose)
            return self._event_names
        payload = self.index.extract(WWISE_IDS_LOGICAL)
        if payload is None:
            self._event_names = {}
            return self._event_names
        dest = self.output_dir / WWISE_IDS_LOGICAL
        dest.parent.mkdir(parents=True, exist_ok=True)
        if not dest.is_file():
            dest.write_bytes(payload)
        self._event_names = parse_wwise_ids_header(dest)
        return self._event_names

    def _prepare_bank_payload(self, payload: bytes) -> bytes | None:
        from lobby.customization_voice import maybe_decrypt_voice_payload

        blob = maybe_decrypt_voice_payload(payload)
        if blob[:4] != b"BKHD":
            return None
        return blob

    def load_bank(self, logical_path: str) -> ParsedBank | None:
        if logical_path in self._bank_cache:
            return self._bank_cache[logical_path]
        loose_name = logical_path.split("sound/", 1)[-1] if logical_path.startswith("sound/") else logical_path
        loose = self.sound_root / loose_name
        if loose.is_file():
            bank = parse_bnk(loose)
            self._bank_cache[logical_path] = bank
            return bank
        dest = self.output_dir / logical_path
        if dest.is_file():
            raw = dest.read_bytes()
            prepared = self._prepare_bank_payload(raw)
            if prepared is None:
                dest.unlink(missing_ok=True)
            elif prepared != raw:
                dest.write_bytes(prepared)
        if not dest.is_file():
            payload = self.index.extract(logical_path)
            if payload is None:
                return None
            prepared = self._prepare_bank_payload(payload)
            if prepared is None:
                return None
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(prepared)
        bank = parse_bnk(dest)
        self._bank_cache[logical_path] = bank
        return bank

    def _cache_media_path(self, bank_logical: str, bank: ParsedBank, media_id: int) -> Path | None:
        return self._cache_media(bank_logical, bank, media_id).path

    def _cache_media(self, bank_logical: str, bank: ParsedBank, media_id: int) -> MediaCacheResult:
        if media_id <= 0:
            return MediaCacheResult(path=None)
        payload = bank.embedded_bytes(media_id)
        stream_dest = self.output_dir / f"sound/windows/stream/{media_id}.wem"
        if payload is None:
            if not stream_dest.is_file():
                stream_payload = self.index.extract(f"sound/windows/stream/{media_id}.wem")
                if stream_payload is None:
                    loose = self.sound_root / f"{media_id}.wem"
                    if not loose.is_file():
                        return MediaCacheResult(path=None)
                    stream_dest.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(loose, stream_dest)
                else:
                    stream_dest.parent.mkdir(parents=True, exist_ok=True)
                    stream_dest.write_bytes(stream_payload)
            payload = stream_dest.read_bytes()

        cache_dir = self.output_dir / "sound/cache" / bank_logical.replace("/", "__")
        if needs_vgmstream_decode(payload):
            wav_dest = cache_dir / f"{media_id}.wav"
            if path_is_godot_playable_wav(wav_dest):
                return MediaCacheResult(path=wav_dest, needs_vgmstream=True)
            decoded = try_decode_wem(payload, wav_dest)
            if decoded.path is not None:
                return MediaCacheResult(path=decoded.path, needs_vgmstream=True)
            wem_dest = cache_dir / f"{media_id}.wem"
            if not wem_dest.is_file():
                wem_dest.parent.mkdir(parents=True, exist_ok=True)
                wem_dest.write_bytes(payload)
            return MediaCacheResult(
                path=wem_dest,
                needs_vgmstream=True,
                decode_error=decoded.error or "",
            )

        suffix = output_suffix_for_payload(payload)
        dest = cache_dir / f"{media_id}{suffix}"
        if dest.is_file() and dest.stat().st_size > 0:
            return MediaCacheResult(path=dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(payload)
        return MediaCacheResult(path=dest)

    def _result_from_cache(
        self,
        cache: MediaCacheResult,
        *,
        event_id: int = 0,
        media_id: int = 0,
        bank_logical: str = "",
    ) -> AudioExtractResult:
        return AudioExtractResult(
            path=cache.path,
            event_id=event_id,
            media_id=media_id,
            bank_logical=bank_logical,
            decode_error=cache.decode_error,
            needs_vgmstream=cache.needs_vgmstream,
        )

    def _first_playable_media(
        self,
        bank: ParsedBank,
        event_id: int = 0,
        active_states: dict[int, int] | None = None,
    ) -> int:
        if active_states:
            media_id = resolve_music_media(bank, event_id, active_states)
            if media_id > 0:
                return media_id
        if event_id:
            media_ids = bank.media_ids_for_event(event_id)
            if media_ids:
                return media_ids[0]
            if event_id in bank.events:
                return 0
        return bank.first_embedded_media_id() or 0

    def _extract_bank_media(
        self,
        logical: str,
        event_id: int = 0,
        graph: WwiseMusicGraph | None = None,
    ) -> AudioExtractResult:
        bank = self.load_bank(logical)
        if bank is None:
            return AudioExtractResult(path=None, bank_logical=logical, event_id=event_id)
        resolved_event = event_id or next(iter(bank.events), 0)
        active_states = graph.active_states if graph is not None else None
        media_id = self._first_playable_media(bank, resolved_event, active_states)
        if media_id <= 0:
            return AudioExtractResult(path=None, event_id=resolved_event, bank_logical=logical)
        cache = self._cache_media(logical, bank, media_id)
        return self._result_from_cache(
            cache,
            event_id=resolved_event,
            media_id=media_id,
            bank_logical=logical,
        )

    def _event_bank_candidates(self, event_id: int) -> list[str]:
        candidates: list[str] = []
        event_name = self.event_names().get(event_id, "")
        if event_name:
            candidates.append(f"sound/windows/{event_name}.bnk")
            if event_name.startswith("PLAY_ACTION_"):
                candidates.append(ACTION_SOUND_BANK)
        if event_id == LOBBY_BOOT_EVENT_ID and ACTION_SOUND_BANK not in candidates:
            candidates.append(ACTION_SOUND_BANK)
        candidates.append(SYSTEM_SOUND_BANK)
        seen: set[str] = set()
        ordered: list[str] = []
        for logical in candidates:
            if logical not in seen:
                seen.add(logical)
                ordered.append(logical)
        return ordered

    def extract_event(self, event_id: int, bank_logical: str | None = None) -> AudioExtractResult:
        banks: list[tuple[str, ParsedBank]] = []
        if bank_logical:
            bank = self.load_bank(bank_logical)
            if bank is not None:
                banks.append((bank_logical, bank))
        else:
            for logical in self._event_bank_candidates(event_id):
                bank = self.load_bank(logical)
                if bank is not None:
                    banks.append((logical, bank))
        for logical, bank in banks:
            media_id = self._first_playable_media(bank, event_id)
            if media_id > 0:
                cache = self._cache_media(logical, bank, media_id)
                return self._result_from_cache(
                    cache,
                    event_id=event_id,
                    media_id=media_id,
                    bank_logical=logical,
                )
        return AudioExtractResult(path=None, event_id=event_id)

    def extract_bgm(
        self,
        zone_or_event: str = "lobby",
        graph: WwiseMusicGraph | None = None,
    ) -> AudioExtractResult:
        music_graph = graph or WwiseMusicGraph()
        if graph is None:
            music_graph.set_menu_state(zone_or_event if zone_or_event in {"login", "lobby", "loading", "customize", "character_create", "home", "default"} else "field")
        logical = music_graph.resolve_bgm_bank(zone_or_event)
        return self._extract_bank_media(logical, graph=music_graph)

    def extract_ambient(
        self,
        region_key: str = "default",
        graph: WwiseMusicGraph | None = None,
    ) -> AudioExtractResult:
        music_graph = graph or WwiseMusicGraph()
        logical = music_graph.resolve_ambient_bank(region_key)
        return self._extract_bank_media(logical, graph=music_graph)

    def extract_event_by_name(self, event_name: str) -> AudioExtractResult:
        needle = event_name.strip()
        if not needle:
            return AudioExtractResult(path=None)
        names = self.event_names()
        for event_id, name in names.items():
            if name == needle:
                return self.extract_event(event_id)
        upper = needle.upper()
        for event_id, name in names.items():
            if upper in name.upper():
                result = self.extract_event(event_id)
                if result.path is not None:
                    return result
        from .wwise_bridge import WwiseEvent

        return self.extract_event(WwiseEvent(needle).resolve_id())

    def extract_creature(self, creature_key: str) -> AudioExtractResult:
        logical = creature_bank_logical_path(creature_key)
        bank = self.load_bank(logical)
        if bank is None:
            return AudioExtractResult(path=None, bank_logical=logical)
        event_id = next(iter(bank.events), 0)
        media_ids = bank.media_ids_for_event(event_id) if event_id else []
        media_id = media_ids[0] if media_ids else 0
        if media_id <= 0:
            media_id = bank.first_embedded_media_id() or 0
        if media_id <= 0:
            return AudioExtractResult(path=None, event_id=event_id, bank_logical=logical)
        cache = self._cache_media(logical, bank, media_id)
        return self._result_from_cache(
            cache,
            event_id=event_id,
            media_id=media_id,
            bank_logical=logical,
        )

    def extract_voice_preview(
        self,
        class_type: int,
        voice_type: int,
        audio_index: int = 0,
        locale: str | None = None,
    ) -> AudioExtractResult:
        logical = voice_bank_logical_path(class_type, voice_type, locale)
        bank = self.load_bank(logical)
        if bank is None:
            return AudioExtractResult(path=None, bank_logical=logical)
        state = VoiceDialogState(voice_type=int(voice_type), audio_index=int(audio_index))
        dialog = resolve_voice_dialog(bank, state)
        event_id = dialog.event_id if dialog is not None else next(iter(bank.events), 0)
        media_id = dialog.media_id if dialog is not None else 0
        if media_id <= 0:
            media_id = self._first_playable_media(bank, event_id)
        if media_id <= 0:
            return AudioExtractResult(path=None, event_id=event_id, bank_logical=logical)
        cache = self._cache_media(logical, bank, media_id)
        return self._result_from_cache(
            cache,
            event_id=event_id,
            media_id=media_id,
            bank_logical=logical,
        )

    def build_bank_map(self) -> WwiseBankMap:
        root = self.sound_root if self.sound_root.is_dir() else self.output_dir / "sound"
        bank_map = WwiseBankMap(root)
        if self.sound_root.is_dir():
            bank_map.load(self.sound_root)
        for logical in (SYSTEM_SOUND_BANK,):
            bank = self.load_bank(logical)
            if bank is not None:
                bank_map.load_parsed(bank, logical)
        action_bank = self.load_bank(ACTION_SOUND_BANK)
        if action_bank is not None:
            bank_map.banks.append(action_bank)
            boot_media = action_bank.media_ids_for_event(LOBBY_BOOT_EVENT_ID)
            if boot_media:
                bank_map._store_ref(LOBBY_BOOT_EVENT_ID, ACTION_SOUND_BANK, boot_media[0])
        bank_map.event_names.update(self.event_names())
        return bank_map

    def resolve_map(self, event_ids: list[int] | None = None) -> dict[str, object]:
        bank_map = self.build_bank_map()
        ids = event_ids or [LOBBY_BOOT_EVENT_ID]
        events: list[dict[str, object]] = []
        for event_id in ids:
            path = bank_map.resolve_event(event_id)
            if path is None:
                extracted = self.extract_event(event_id)
                path = extracted.path
            events.append({
                "event_id": event_id,
                "event_hex": f"0x{event_id:08X}",
                "label": bank_map.event_names.get(event_id, ""),
                "path": str(path) if path is not None else "",
            })
        boot_path = bank_map.resolve_lobby_boot() or self.extract_event(LOBBY_BOOT_EVENT_ID).path
        return {
            "ok": True,
            "sound_root": str(self.sound_root if self.sound_root.is_dir() else self.output_dir / "sound"),
            "bank_count": len(bank_map.banks),
            "mapped_events": len(bank_map.event_refs),
            "lobby_boot_event_id": LOBBY_BOOT_EVENT_ID,
            "lobby_boot_path": str(boot_path) if boot_path is not None else "",
            "events": events,
            "rows": bank_map.to_json_rows(),
        }
