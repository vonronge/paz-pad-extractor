from __future__ import annotations

BDO_ICE_KEY = bytes([0x51, 0xF3, 0x0F, 0x11, 0x04, 0x24, 0x6A, 0x00])

_ICE_SMOD = (
    (333, 313, 505, 369),
    (379, 375, 319, 391),
    (361, 445, 451, 397),
    (397, 425, 395, 505),
)
_ICE_SXOR = (
    (0x83, 0x85, 0x9B, 0xCD),
    (0xCC, 0xA7, 0xAD, 0x41),
    (0x4B, 0x2E, 0xD4, 0x33),
    (0xEA, 0xCB, 0x2E, 0x04),
)
_ICE_PBOX = (
    0x00000001, 0x00000080, 0x00000400, 0x00002000, 0x00080000, 0x00200000, 0x01000000, 0x40000000,
    0x00000008, 0x00000020, 0x00000100, 0x00004000, 0x00010000, 0x00800000, 0x04000000, 0x20000000,
    0x00000004, 0x00000010, 0x00000200, 0x00008000, 0x00020000, 0x00400000, 0x08000000, 0x10000000,
    0x00000002, 0x00000040, 0x00000800, 0x00001000, 0x00040000, 0x00100000, 0x02000000, 0x80000000,
)
_KEY_ROT = (0, 1, 2, 3, 2, 1, 3, 0, 1, 3, 2, 0, 3, 1, 0, 2)

def _gf_mult(a: int, b: int, m: int) -> int:
    res = 0
    while b:
        if b & 1:
            res ^= a
        a <<= 1
        b >>= 1
        if a >= 256:
            a ^= m
    return res

def _gf_exp7(b: int, m: int) -> int:
    if b == 0:
        return 0
    x = _gf_mult(b, b, m)
    x = _gf_mult(b, x, m)
    x = _gf_mult(x, x, m)
    return _gf_mult(b, x, m)

def _ice_perm32(x: int) -> int:
    res = 0
    for pb in _ICE_PBOX:
        if x & 1:
            res |= pb
        x >>= 1
    return res

def _build_sbox() -> list[int]:
    sbox = [0] * 4096
    for i in range(1024):
        col = (i >> 1) & 0xFF
        row = (i & 1) | ((i & 0x200) >> 8)
        sbox[i] = _ice_perm32(_gf_exp7(col ^ _ICE_SXOR[0][row], _ICE_SMOD[0][row]) << 24)
        sbox[1024 + i] = _ice_perm32(_gf_exp7(col ^ _ICE_SXOR[1][row], _ICE_SMOD[1][row]) << 16)
        sbox[2048 + i] = _ice_perm32(_gf_exp7(col ^ _ICE_SXOR[2][row], _ICE_SMOD[2][row]) << 8)
        sbox[3072 + i] = _ice_perm32(_gf_exp7(col ^ _ICE_SXOR[3][row], _ICE_SMOD[3][row]))
    return sbox

_SBOX = _build_sbox()

def _rotl(value: int, bits: int) -> int:
    return ((value << bits) | (value >> (32 - bits))) & 0xFFFFFFFF

class IceCipher:

    def __init__(self, key: bytes = BDO_ICE_KEY) -> None:
        if len(key) != 8:
            raise ValueError("ICE key must be 8 bytes")
        self._keysched: list[tuple[int, int, int]] = []
        kb = [0] * 4
        for i in range(4):
            kb[3 - i] = (key[i * 2] << 8) | key[i * 2 + 1]
        self._key_sched_build(kb)

    def _key_sched_build(self, kb: list[int]) -> None:
        for i in range(8):
            kr = _KEY_ROT[i]
            isk = [0, 0, 0]
            for _ in range(5):
                for j in range(3):
                    cur = isk[j]
                    for k in range(4):
                        idx = (kr + k) & 3
                        bit = kb[idx] & 1
                        cur = (cur << 1) | bit
                        kb[idx] = ((kb[idx] >> 1) | ((bit ^ 1) << 15)) & 0xFFFF
                    isk[j] = cur
            self._keysched.append((isk[0], isk[1], isk[2]))

    def _ice_f(self, p: int, sk: tuple[int, int, int]) -> int:
        tr = (p & 0x3FF) | ((p << 2) & 0xFFC00)
        tl = ((p >> 16) & 0x3FF) | (_rotl(p, 18) & 0xFFC00)
        salt = sk[2] & (tl ^ tr)
        al = salt ^ tl ^ sk[0]
        ar = salt ^ tr ^ sk[1]
        return (
            _SBOX[(al >> 10) & 0x3FF]
            ^ _SBOX[1024 + (al & 0x3FF)]
            ^ _SBOX[2048 + ((ar >> 10) & 0x3FF)]
            ^ _SBOX[3072 + (ar & 0x3FF)]
        )

    def decrypt(self, data: bytes) -> bytes:
        buf = bytearray(data)
        end = len(buf) - (len(buf) % 8)
        for off in range(0, end, 8):
            left = int.from_bytes(buf[off : off + 4], "big")
            right = int.from_bytes(buf[off + 4 : off + 8], "big")
            for i in range(6, -1, -2):
                left ^= self._ice_f(right, self._keysched[i + 1])
                right ^= self._ice_f(left, self._keysched[i])
            buf[off : off + 4] = right.to_bytes(4, "big")
            buf[off + 4 : off + 8] = left.to_bytes(4, "big")
        return bytes(buf)

    def decrypt_inplace(self, data: bytearray) -> bytearray:
        return bytearray(self.decrypt(bytes(data)))

def ice_decrypt(data: bytes, key: bytes = BDO_ICE_KEY) -> bytes:
    return IceCipher(key).decrypt(data)
