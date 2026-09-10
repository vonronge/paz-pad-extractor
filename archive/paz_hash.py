from __future__ import annotations

PATH_HASH_VA = 0x140D4D1E0
PATH_NORMALIZE_VA = 0x140D16350
_HASH_SEED = 558228019

def _rol32(value: int, bits: int) -> int:
    value &= 0xFFFFFFFF
    bits &= 31
    return ((value << bits) | (value >> (32 - bits))) & 0xFFFFFFFF

def _ror32(value: int, bits: int) -> int:
    value &= 0xFFFFFFFF
    bits &= 31
    return ((value >> bits) | (value << (32 - bits))) & 0xFFFFFFFF

def _read_c(data: bytes, offset: int) -> int:
    return data[offset] & 0xFF

def _read_h(data: bytes, offset: int) -> int:
    return data[offset] | (data[offset + 1] << 8)

def _read_d3(data: bytes, offset: int) -> int:
    return data[offset] | (data[offset + 1] << 8) | (data[offset + 2] << 16)

def _finalize_hash(v3: int, v4: int, v5: int) -> int:
    v77 = _rol32(v5, 14)
    v78 = ((v5 ^ v3) - v77) & 0xFFFFFFFF
    v79 = _rol32(v78, 11)
    v80 = ((v4 ^ v78) - v79) & 0xFFFFFFFF
    v81 = _ror32(v80, 7)
    v82 = ((v80 ^ v5) - v81) & 0xFFFFFFFF
    v83 = _rol32(v82, 16)
    v84 = ((v82 ^ v78) - v83) & 0xFFFFFFFF
    v85 = _rol32(v84, 4)
    v86 = ((v80 ^ v84) - v85) & 0xFFFFFFFF
    v87 = _rol32(v86, 14)
    v88 = ((v86 ^ v82) - v87) & 0xFFFFFFFF
    v89 = _ror32(v88, 8)
    return ((v88 ^ v84) - v89) & 0xFFFFFFFF

def _mix_block_type1(buf: bytes, offset: int, v3: int, v4: int, v5: int) -> tuple[int, int, int]:
    v6 = int.from_bytes(buf[offset + 8 : offset + 12], "little") + v3
    v7 = int.from_bytes(buf[offset + 4 : offset + 8], "little") + v5
    v8 = _rol32(v6, 4)
    v9 = (v4 + int.from_bytes(buf[offset : offset + 4], "little") - v6) & 0xFFFFFFFF
    v9 ^= v8
    v10 = (v7 + v6) & 0xFFFFFFFF
    v11 = (v7 - v9) & 0xFFFFFFFF
    v12 = _rol32(v9, 6)
    v13 = (v10 + v9) & 0xFFFFFFFF
    v14 = (v11 ^ v12) & 0xFFFFFFFF
    v15 = (v10 - v14) & 0xFFFFFFFF
    v16 = _rol32(v14, 8)
    v17 = (v13 + v14) & 0xFFFFFFFF
    v18 = (v15 ^ v16) & 0xFFFFFFFF
    v19 = (v13 - v18) & 0xFFFFFFFF
    v20 = _rol32(v18, 16)
    v21 = (v17 + v18) & 0xFFFFFFFF
    v22 = (v19 ^ v20) & 0xFFFFFFFF
    v23 = (v17 - v22) & 0xFFFFFFFF
    v24 = _ror32(v22, 13)
    v4 = (v21 + v22) & 0xFFFFFFFF
    v25 = (v23 ^ v24) & 0xFFFFFFFF
    v26 = _rol32(v25, 4)
    v3 = ((v21 - v25) & 0xFFFFFFFF) ^ v26
    v5 = (v4 + v25) & 0xFFFFFFFF
    return v3, v4, v5

def _tail_type1(buf: bytes, offset: int, remaining: int, v3: int, v4: int, v5: int) -> tuple[int, int, int, bool]:
    if remaining >= 12:
        v3 = (v3 + int.from_bytes(buf[offset + 8 : offset + 12], "little")) & 0xFFFFFFFF
    if remaining >= 8:
        v5 = (v5 + int.from_bytes(buf[offset + 4 : offset + 8], "little")) & 0xFFFFFFFF
    if remaining >= 4:
        v4 = (v4 + int.from_bytes(buf[offset : offset + 4], "little")) & 0xFFFFFFFF
    elif remaining == 11:
        v5 = (v5 + int.from_bytes(buf[offset + 4 : offset + 8], "little")) & 0xFFFFFFFF
        v3 = (v3 + _read_d3(buf, offset + 8)) & 0xFFFFFFFF
        v4 = (v4 + int.from_bytes(buf[offset : offset + 4], "little")) & 0xFFFFFFFF
    elif remaining == 10:
        v5 = (v5 + int.from_bytes(buf[offset + 4 : offset + 8], "little")) & 0xFFFFFFFF
        v3 = (v3 + _read_h(buf, offset + 8)) & 0xFFFFFFFF
        v4 = (v4 + int.from_bytes(buf[offset : offset + 4], "little")) & 0xFFFFFFFF
    elif remaining == 9:
        v5 = (v5 + int.from_bytes(buf[offset + 4 : offset + 8], "little")) & 0xFFFFFFFF
        v3 = (v3 + _read_c(buf, offset + 8)) & 0xFFFFFFFF
        v4 = (v4 + int.from_bytes(buf[offset : offset + 4], "little")) & 0xFFFFFFFF
    elif remaining == 7:
        v5 = (v5 + _read_d3(buf, offset + 4)) & 0xFFFFFFFF
        v4 = (v4 + int.from_bytes(buf[offset : offset + 4], "little")) & 0xFFFFFFFF
    elif remaining == 6:
        v5 = (v5 + _read_h(buf, offset + 4)) & 0xFFFFFFFF
        v4 = (v4 + int.from_bytes(buf[offset : offset + 4], "little")) & 0xFFFFFFFF
    elif remaining == 5:
        v5 = (v5 + _read_c(buf, offset + 4)) & 0xFFFFFFFF
        v4 = (v4 + int.from_bytes(buf[offset : offset + 4], "little")) & 0xFFFFFFFF
    elif remaining == 3:
        v4 = (v4 + _read_d3(buf, offset)) & 0xFFFFFFFF
    elif remaining == 2:
        v4 = (v4 + _read_h(buf, offset)) & 0xFFFFFFFF
    elif remaining == 1:
        v4 = (v4 + _read_c(buf, offset)) & 0xFFFFFFFF
    elif remaining == 0:
        return v3, v4, v5, True
    return v3, v4, v5, False

def hash_path_bytes(data: bytes, alignment: int = 1) -> int:
    if alignment not in (1, 2, 3):
        raise ValueError("alignment must be 1, 2, or 3")
    if alignment != 1:
        raise NotImplementedError("only dword-aligned hash (type 1) is implemented")
    length = len(data)
    v3 = v4 = v5 = (length - _HASH_SEED) & 0xFFFFFFFF
    offset = 0
    if length > 12:
        block_count = (length - 13) // 12 + 1
        for _ in range(block_count):
            v3, v4, v5 = _mix_block_type1(data, offset, v3, v4, v5)
            offset += 12
    remaining = len(data) - offset
    v3, v4, v5, done = _tail_type1(data, offset, remaining, v3, v4, v5)
    if done:
        return v3
    return _finalize_hash(v3, v4, v5)

def normalize_logical_path(path: str, install_root: str | None = None) -> str:
    working = path
    if install_root:
        root = install_root.replace("\\", "/")
        if not root.endswith("/"):
            root += "/"
        candidate = working.replace("\\", "/")
        if candidate.lower().startswith(root.lower()):
            working = candidate[len(root) :]

    out: list[str] = []
    last_slash_slot = -2
    last_dot_slot = -2
    last_non_slash_slot = -1

    for ch in working:
        if not ch:
            break
        if len(out) >= 259:
            break
        if "A" <= ch <= "Z":
            ch = chr(ord(ch) + 32)
        elif ch == "\\":
            ch = "/"
        if ch != "/":
            if ch == ".":
                last_dot_slot = len(out)
            out.append(ch)
            last_non_slash_slot = len(out) - 1
            continue
        if last_slash_slot != last_non_slash_slot:
            if last_dot_slot != last_non_slash_slot:
                last_slash_slot = len(out)
                out.append("/")
            else:
                if out:
                    out.pop()
                last_non_slash_slot -= 1
                last_slash_slot = -2
                last_dot_slot = -2
        continue

    normalized = "".join(out)
    return normalized.rstrip(" \t")

def hash_path_key(path: str | bytes) -> int:
    if isinstance(path, bytes):
        payload = path
    else:

        payload = normalize_logical_path(path).encode("utf-8")
    return hash_path_bytes(payload, alignment=1)
