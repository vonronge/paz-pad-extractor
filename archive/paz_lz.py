from __future__ import annotations

_LIT_LEN_TABLE = (4, 0, 1, 0, 2, 0, 1, 0, 3, 0, 1, 0, 2, 0, 1, 0)

def parse_file_header(data: bytes) -> tuple[int, int, int]:
    if data[0] & 0x02:
        comp_len = int.from_bytes(data[1:5], "little")
        decomp_len = int.from_bytes(data[5:9], "little")
        return decomp_len, comp_len, 9
    return data[2], data[1], 3

def parse_block_header(header: int) -> tuple[int, int, int]:
    cmd = header & 0x03
    if cmd == 3:
        if (header & 0x7F) == 3:
            return header >> 15, ((header >> 7) & 0xFF) + 3, 4
        return (header >> 7) & 0x1FFFF, ((header >> 2) & 0x1F) + 2, 3
    if cmd == 2:
        return (header >> 6) & 0x3FF, ((header >> 2) & 0xF) + 3, 2
    if cmd == 1:
        return (header >> 2) & 0x3FFF, 3, 2
    return (header >> 2) & 0x3F, 3, 1

def decompress_bdo_lz(data: bytes, original_size: int = 0) -> bytes:
    if not data:
        return b""
    target, comp_len, header_size = parse_file_header(data)
    if original_size and target != original_size:
        target = original_size
    blob = data[:comp_len]
    if blob[0] & 0x01 == 0:
        end = header_size + target
        return bytes(blob[header_size:end])

    out = bytearray(target)
    in_idx = header_size
    out_idx = 0
    group_header = 1
    size = len(blob)

    while out_idx < target and in_idx < size:
        if group_header == 1:
            if in_idx + 4 > size:
                break
            group_header = int.from_bytes(blob[in_idx : in_idx + 4], "little")
            in_idx += 4
        if group_header & 1:
            if in_idx + 4 > size:
                break
            raw = int.from_bytes(blob[in_idx : in_idx + 4], "little")
            distance, length, step = parse_block_header(raw)
            in_idx += step
            if out_idx < distance or out_idx + length > target:
                break
            src = out_idx - distance
            for k in range(length):
                out[out_idx + k] = out[src + k]
            out_idx += length
            group_header >>= 1
        else:
            lit_len = _LIT_LEN_TABLE[group_header & 0x0F]
            if out_idx + 4 > target or in_idx + 4 > size:
                break
            out[out_idx : out_idx + 4] = blob[in_idx : in_idx + 4]
            out_idx += lit_len
            in_idx += lit_len
            group_header >>= lit_len

    while out_idx < target:
        if group_header == 1:
            if in_idx + 4 <= size:
                in_idx += 4
            group_header = 0x80000000
        if in_idx >= size:
            break
        out[out_idx] = blob[in_idx]
        out_idx += 1
        in_idx += 1
        group_header >>= 1

    return bytes(out[:out_idx])
