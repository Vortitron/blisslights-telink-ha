"""Telink mesh v1 crypto — ported from com/telink/crypto/AES.java + LightController.java.

Device (live, 13 Sep 2026):
  MAC        A4:C1:38:2B:EC:B5
  mesh name  eeba4f5d3e18   (advert local name = app-renamed mesh)
  password   123
  key0       = pad16("eeba4f5d3e18") XOR pad16("123")
"""
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

# ---------------------------------------------------------------- primitives

def _ecb(key16, block16):
    c = Cipher(algorithms.AES(key16), modes.ECB())
    return c.encryptor().update(block16)

def E(key, block):
    """aes_att_encryption: AES-ECB on both key and block byte-reversed; output reversed."""
    return _ecb(key[::-1], block[::-1])[::-1]

def pad16(s):
    b = s.encode() if isinstance(s, str) else s
    return (b + b"\x00" * 16)[:16]

def key0(mesh_name, password):
    a, b = pad16(mesh_name), pad16(password)
    return bytes(x ^ y for x, y in zip(a, b))

# ---------------------------------------------------------------- login (LightController.login / getSessionKey, jadx-verified)

def login_write(k0, rand8):
    """17-byte write to char 1914: [0x0C, rand8, reverse(AES.encrypt(randpad, k0)[8..15])].
    NOTE the swapped arg order: randpad is the AES KEY, k0 is the DATA; raw AES.encrypt
    (no att-reversal) on the output."""
    auth = _ecb(pad16(rand8)[::-1], k0[::-1])      # AES.encrypt(randpad, k0)
    return bytes([0x0C]) + rand8 + auth[8:16][::-1]

def verify_check(k0, r2):
    """Expected 8 check bytes in the 1914 read-back (bytes 9..16)."""
    chk = _ecb(pad16(r2)[::-1], k0[::-1])          # AES.encrypt(r2pad, k0)
    return chk[8:16][::-1]

def session_key(k0, rand8, r2):
    """sessionKey = aes_att_encryption(key0, rand8 ++ r2)."""
    return E(k0, rand8 + r2)

# ---------------------------------------------------------------- mesh packets (AES.smali 3-arg, byte-exact)

def cmd_iv(mac_le, seq):
    return bytes([mac_le[0], mac_le[1], mac_le[2], mac_le[3], 0x01,
                  seq & 0xFF, (seq >> 8) & 0xFF, (seq >> 16) & 0xFF])

def notify_iv(mac_le, pkt):
    # base [mac0,mac1,mac2,0,0,0,0,0], then packet bytes 0..4 copied to iv[3..7]
    return bytes([mac_le[0], mac_le[1], mac_le[2], pkt[0], pkt[1], pkt[2], pkt[3], pkt[4]])

def encrypt_packet(key, iv, data):
    """In-place-style: data = 20-byte packet; seq[0..2] plaintext, MAC -> [3..4], ct [5..19]."""
    data = bytearray(data)
    a = bytearray(E(key, bytes(iv) + bytes([15]) + b"\x00" * 7))   # CBC-MAC chain
    for i in range(15):
        a[i & 15] ^= data[i + 5]
        if i == 14:
            a = bytearray(E(key, bytes(a)))
    data[3], data[4] = a[0], a[1]
    ctr = bytearray(16)
    ctr[1:9] = iv
    ks = None
    for i in range(15):
        if i == 0:
            ks = bytearray(E(key, bytes(ctr)))
            ctr[0] = (ctr[0] + 1) & 0xFF
        data[i + 5] ^= ks[i & 15]
    return bytes(data)

def decrypt_packet(key, iv, data):
    """Notify packet: CTR over ct[7..19], then CBC-MAC verify over pt[7..19]. None on MAC fail."""
    data = bytearray(data)
    ctr = bytearray(16)
    ctr[1:9] = iv
    ks = bytearray(E(key, bytes(ctr)))
    for i in range(13):
        data[i + 7] ^= ks[i & 15]
    a = bytearray(E(key, bytes(iv) + bytes([13]) + b"\x00" * 7))
    for i in range(13):
        a[i & 15] ^= data[i + 7]
        if i == 12:
            a = bytearray(E(key, bytes(a)))
    if data[5] != a[0] or data[6] != a[1]:
        return None
    return bytes(data)

def build_packet(seq, mesh_addr, opcode, vendor_id, params):
    assert len(params) <= 10
    p = bytes(params) + b"\x00" * (10 - len(params))
    return bytes([seq & 0xFF, (seq >> 8) & 0xFF, (seq >> 16) & 0xFF, 0, 0,
                  mesh_addr & 0xFF, (mesh_addr >> 8) & 0xFF,
                  opcode | 0xC0, vendor_id & 0xFF, (vendor_id >> 8) & 0xFF]) + p

# ---------------------------------------------------------------- device constants

MAC = "A4:C1:38:2B:EC:B5"
MESH_NAME = "eeba4f5d3e18"
PASSWORD = "123"
VENDOR_ID = 0x0211
MAC_LE = bytes.fromhex(MAC.replace(":", ""))[::-1]   # b5 ec 2b 38 c1 a4

if __name__ == "__main__":
    import os
    k0 = key0(MESH_NAME, PASSWORD)
    print("key0      :", k0.hex())
    rand8 = os.urandom(8)
    w = login_write(k0, rand8)
    print("login rand:", rand8.hex())
    print("login wr  :", w.hex(), f"({len(w)} bytes)")
    # dry-run the server side with a fake r2 to check the round trip + session key
    r2 = os.urandom(8)
    chk = verify_check(k0, r2)
    sk = session_key(k0, rand8, r2)
    print("check     :", chk.hex())
    print("sessionKey:", sk.hex())