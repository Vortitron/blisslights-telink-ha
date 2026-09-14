"""Telink mesh v1 crypto + framing — ported from com.quhwa.mesh (BlissLights app).

Source of truth: /workspace/blisslights_decompile/PROTOCOL.md (live-verified
13 Sep 2026 against a Sky Lite running firmware V001.14).

All primitives are byte-exact ports of com/telink/crypto/AES.java and
LightController.java (see PROTOCOL.md for the derivations).
"""

from __future__ import annotations

import os

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

# ---------------------------------------------------------------- primitives


def _ecb(key16: bytes, block16: bytes) -> bytes:
    cipher = Cipher(algorithms.AES(key16), modes.ECB())
    return cipher.encryptor().update(block16)


def _att(key: bytes, block: bytes) -> bytes:
    """aes_att_encryption: ECB on both key and block byte-reversed, output reversed."""
    return _ecb(key[::-1], block[::-1])[::-1]


def _pad16(value: str | bytes) -> bytes:
    raw = value.encode() if isinstance(value, str) else value
    return (raw + b"\x00" * 16)[:16]


def key0(mesh_name: str, password: str) -> bytes:
    """16-byte login key: pad16(meshName) XOR pad16(password)."""
    a, b = _pad16(mesh_name), _pad16(password)
    return bytes(x ^ y for x, y in zip(a, b))


# ---------------------------------------------------------------- login

PAIR_OP_LOGIN = 0x0C
PAIR_OP_FAIL = 0x0C
PAIR_OP_SUCCESS = 0x0D


def login_write(k0: bytes, rand8: bytes) -> bytes:
    """17-byte write to the PAIR characteristic.

    [0x0C, rand8, reverse(AES.encrypt(randpad, k0)[8..15])] — note the swapped
    AES argument order (randpad is the KEY, key0 is the DATA).
    """
    auth = _ecb(_pad16(rand8)[::-1], k0[::-1])
    return bytes([0x0C]) + rand8 + auth[8:16][::-1]


def verify_check(k0: bytes, r2: bytes) -> bytes:
    """Expected 8 check bytes in the 17-byte PAIR read-back (bytes 9..16)."""
    chk = _ecb(_pad16(r2)[::-1], k0[::-1])
    return chk[8:16][::-1]


def session_key(k0: bytes, rand8: bytes, r2: bytes) -> bytes:
    """sessionKey = aes_att_encryption(key0, rand8 ++ r2)."""
    return _att(k0, rand8 + r2)


# ---------------------------------------------------------------- mesh packets


def cmd_iv(mac_le: bytes, seq: int) -> bytes:
    """getSecIVM."""
    return bytes(
        [
            mac_le[0],
            mac_le[1],
            mac_le[2],
            mac_le[3],
            0x01,
            seq & 0xFF,
            (seq >> 8) & 0xFF,
            (seq >> 16) & 0xFF,
        ]
    )


def notify_iv(mac_le: bytes, pkt: bytes) -> bytes:
    """getSecIVS: [mac0, mac1, mac2, pkt[0..4]]."""
    return bytes([mac_le[0], mac_le[1], mac_le[2], pkt[0], pkt[1], pkt[2], pkt[3], pkt[4]])


def encrypt_packet(key: bytes, iv: bytes, data: bytes) -> bytes:
    """Command packet (20 bytes): seq[0..2] plaintext, MAC -> [3..4], ct [5..19]."""
    data = bytearray(data)
    # CBC-MAC over plaintext data[5..19] (15 bytes)
    a = bytearray(_att(key, bytes(iv) + bytes([15]) + b"\x00" * 7))
    for i in range(15):
        a[i & 15] ^= data[i + 5]
        if i == 14:
            a = bytearray(_att(key, bytes(a)))
    data[3], data[4] = a[0], a[1]
    # CTR over data[5..19]
    ctr = bytearray(16)
    ctr[1:9] = iv
    ks = bytearray(_att(key, bytes(ctr)))
    for i in range(15):
        data[i + 5] ^= ks[i & 15]
    return bytes(data)


def decrypt_packet(key: bytes, iv: bytes, data: bytes) -> bytes | None:
    """Notify packet (20 bytes): CTR over ct[7..19], then CBC-MAC verify."""
    data = bytearray(data)
    ctr = bytearray(16)
    ctr[1:9] = iv
    ks = bytearray(_att(key, bytes(ctr)))
    for i in range(13):
        data[i + 7] ^= ks[i & 15]
    a = bytearray(_att(key, bytes(iv) + bytes([13]) + b"\x00" * 7))
    for i in range(13):
        a[i & 15] ^= data[i + 7]
        if i == 12:
            a = bytearray(_att(key, bytes(a)))
    if data[5] != a[0] or data[6] != a[1]:
        return None
    return bytes(data)


def build_packet(seq: int, mesh_addr: int, opcode: int, vendor_id: int, params: bytes) -> bytes:
    """20-byte mesh command buffer, params zero-padded to 10."""
    assert len(params) <= 10
    padded = bytes(params) + b"\x00" * (10 - len(params))
    return (
        bytes(
            [
                seq & 0xFF,
                (seq >> 8) & 0xFF,
                (seq >> 16) & 0xFF,
                0,
                0,
                mesh_addr & 0xFF,
                (mesh_addr >> 8) & 0xFF,
                opcode | 0xC0,
                vendor_id & 0xFF,
                (vendor_id >> 8) & 0xFF,
            ]
        )
        + padded
    )


def mac_le_from_address(address: str) -> bytes:
    """'A4:C1:38:2B:EC:B5' -> b5 ec 2b 38 c1 a4 (Telink little-endian convention)."""
    return bytes.fromhex(address.replace(":", "").replace("-", ""))[::-1]


def new_seq() -> int:
    """Fresh-ish sequence number (32-bit, ms since epoch truncated)."""
    import time

    return int(time.time() * 1000) & 0xFFFFFF


# Notify payloads start with a 3-byte prefix (op echo + vendorId LE) before
# the cmd echo — strip it. (Observed live: EA 11 02.)
def strip_notify_prefix(payload: bytes) -> bytes:
    if len(payload) >= 3 and payload[0] == 0xEA:
        return payload[3:]
    return payload