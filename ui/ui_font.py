
from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

from archive.paz_index import PazIndex

FONT_PREFIX = "ui_data/font/"
FONTMAP_DEFAULT = "ui_data/font/fontmap.xml"
FONTMAP_BY_LOCALE = {
    "sc": "ui_data/font/fontmap_sc.xml",
    "zh-cn": "ui_data/font/fontmap_sc.xml",
    "zh_cn": "ui_data/font/fontmap_sc.xml",
    "tc": "ui_data/font/fontmap_tc.xml",
    "zh-tw": "ui_data/font/fontmap_tc.xml",
    "zh_tw": "ui_data/font/fontmap_tc.xml",
}
TTF_MAGIC = bytes([0, 1, 0, 0])
OTF_MAGIC = b"OTTO"
TTC_MAGIC = b"ttcf"

@dataclass(frozen=True)
class UiFontStyle:
    name: str
    file: str
    size: int
    render_type: str = ""
    outline_color: str = ""

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "file": self.file,
            "size": self.size,
            "render_type": self.render_type,
            "outline_color": self.outline_color,
        }

@dataclass
class UiFontMap:
    source: str
    styles: dict[str, UiFontStyle]

    def get(self, name: str) -> UiFontStyle | None:
        if name in self.styles:
            return self.styles[name]
        return self.styles.get("BaseFont")

    def to_dict(self) -> dict:
        return {name: style.to_dict() for name, style in self.styles.items()}

def fontmap_path_for_locale(locale: str) -> str:
    key = (locale or "en").lower().replace("/", "")
    return FONTMAP_BY_LOCALE.get(key, FONTMAP_DEFAULT)

def normalize_font_file(filename: str) -> str:
    ref = filename.replace("\\", "/").strip().lstrip("/")
    while ref.startswith("../"):
        ref = ref[3:]
    ref = ref.lstrip("/")
    return ref.lower()

def parse_fontmap(data: bytes | str) -> UiFontMap:
    if isinstance(data, bytes):
        text = data.decode("utf-8-sig")
    else:
        text = data.lstrip("\ufeff")
    wrapped = "<FontMap>%s</FontMap>" % text
    root = ET.fromstring(wrapped)
    styles: dict[str, UiFontStyle] = {}
    for el in root.findall("Font"):
        name = (el.get("Name") or "").strip()
        if not name or name.startswith("-"):
            continue
        try:
            size = int(float(el.get("Size") or "14"))
        except ValueError:
            size = 14
        attrs = {a.get("Type"): a.get("Value") or "" for a in el.findall("Attribute")}
        styles[name] = UiFontStyle(
            name=name,
            file=normalize_font_file(el.get("FileName") or ""),
            size=size,
            render_type=attrs.get("RenderType") or "",
            outline_color=(attrs.get("OutlineColor") or "").lower(),
        )
    return UiFontMap(source="", styles=styles)

def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]

def default_meta_path() -> Path:
    return _repo_root() / "BDO Client/Paz/pad00000.meta"

def default_output_dir() -> Path:
    return _repo_root() / "extracted"

def load_font_map(
    client_root: Path | None = None,
    locale: str = "en",
    index: PazIndex | None = None,
    meta_path: Path | None = None,
) -> UiFontMap:
    if meta_path is None:
        meta_path = default_meta_path() if client_root is None else client_root / "Paz" / "pad00000.meta"
    if index is None:
        if not meta_path.is_file():
            return UiFontMap(source="", styles={})
        index = PazIndex.load(meta_path, paz_dir=meta_path.parent)
    logical = fontmap_path_for_locale(locale)
    data = index.extract(logical)
    if data is None and logical != FONTMAP_DEFAULT:
        logical = FONTMAP_DEFAULT
        data = index.extract(logical)
    if data is None:
        return UiFontMap(source=logical, styles={})
    parsed = parse_fontmap(data)
    parsed.source = logical
    return parsed

def ensure_ui_font(
    font_ref: str,
    output_dir: Path | None = None,
    meta_path: Path | None = None,
    index: PazIndex | None = None,
) -> Path | None:
    if output_dir is None:
        output_dir = default_output_dir()
    if meta_path is None:
        meta_path = default_meta_path()
    if index is None:
        if not meta_path.is_file():
            return None
        index = PazIndex.load(meta_path, paz_dir=meta_path.parent)

    logical = normalize_font_file(font_ref)
    if not logical:
        return None
    if not logical.startswith(FONT_PREFIX):
        logical = FONT_PREFIX + logical.split("/")[-1]
    dest = output_dir / logical
    if dest.is_file() and dest.stat().st_size > 0:
        return dest

    payload = index.extract(logical)
    if payload is None or len(payload) < 4:
        return None
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(payload)
    return dest

def is_sfnt(data: bytes) -> bool:
    if len(data) < 4:
        return False
    return data[:4] in (TTF_MAGIC, OTF_MAGIC, TTC_MAGIC)

def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Extract one UI font from BDO PAZ.")
    parser.add_argument("font", help="logical path (ui_data/font/pearl.ttf) or Scaleform name")
    parser.add_argument("-o", "--output", type=Path, default=default_output_dir())
    parser.add_argument("--meta", type=Path, default=default_meta_path())
    parser.add_argument("--locale", default="en")
    parser.add_argument("--print-path", action="store_true")
    args = parser.parse_args()

    ref = args.font
    slash_free = ref.replace("\\", "/")
    if "/" not in slash_free and not ref.lower().endswith((".ttf", ".otf", ".xml")):
        fmap = load_font_map(meta_path=args.meta, locale=args.locale)
        style = fmap.get(ref)
        if style is None:
            raise SystemExit(1)
        ref = style.file
    path = ensure_ui_font(ref, output_dir=args.output, meta_path=args.meta)
    if path is None:
        raise SystemExit(1)
    if args.print_path:
        print(path)

if __name__ == "__main__":
    main()
