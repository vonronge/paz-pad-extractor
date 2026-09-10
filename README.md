# PAZ / PAD Archive Extractor

Reads `pad00000.meta` + `PAD*.PAZ`, ICE/LZ-decodes payloads, and writes them under `../extracted/` at the original logical paths.

Needs a `Paz/` folder next to this repo (`BDO Client/Paz/`).

## Scripts

| Script | What it pulls |
|--------|----------------|
| `extract-all.sh` | Every logical path, in phases (see below). Resume-safe. |
| `extract-meshes.sh` | `.pam` `.pac` `.combine` `.probe` `.lod` |
| `extract-mapdata.sh` | `.mapdata` |
| `extract-spawninfo.sh` | `.spawninfo` |
| `extract-texture.sh <ref>` | One `.dds` (logical path or bare filename) |
| `extract-audio.sh` | On-demand Wwise `.bnk` / `.wem` |
| `extract-ui-font.sh <name>` | `ui_data/font/` TTF via `fontmap.xml` |
| `verify-mesh-extract.sh` | Audit extracted mesh PAR vs the index (`--fix` re-extracts misses) |

```bash
./extract-all.sh --help
./extract-texture.sh character/texture/phm_00_lb_0007.dds --print-path
```

## `extract-all.sh` phases (filetypes)

| Phase | Extensions |
|-------|------------|
| probe | `.probe` `.vnl` `.vnm` `.vnt` |
| spawn | `.spawninfo` `.pab` `.pat` `.pad` |
| scripts | `.luac` `.lua` `.ai` `.txt` `.srt` `.xml` |
| collision | `.collisiondata2` `.dbss` `.bss` |
| motion | `.paa` `.pae` `.paac` `.pah` `.paem` |
| meshes | `.pam` `.pac` `.combine` `.lod` `.mapdata` |
| ui | `.png` `.jpg` `.jpeg` `.gif` `.woff` `.ttf` `.woff2` |
| video | `.webm` `.bik` |
| audio | `.wem` `.pcm` `.bnk` |
| dds | `.dds` |
| remainder | anything else in the index |

`--phase NAME` runs one row. `--extensions` / `--prefix` further filter. `.dbss` is copied without ICE/LZ (Rel `strstr(path, ".dbss")`).

## License

MIT
