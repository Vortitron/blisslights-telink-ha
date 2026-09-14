"""BlissLights Sky Lite live state probe — READ-ONLY (queries only, no state changes).

Run INSIDE the Home Assistant container (has bleak + cryptography + dbus Bluetooth):
    python3 read_state.py            # tries candidate mesh addresses for the 0x48 query

Flow: connect -> notify on 1911 -> login handshake (1914 write+read) ->
send vendor query {0x48} (LED state: R,G,B,laser,motor,bright,breathe) and
{0x44,0xFD} (device type) -> print raw + decrypted notifications.
The only writes are the login session-establishment and read-only queries.
"""
import asyncio, sys, time
from bleak import BleakClient
from telink import (MAC, MESH_NAME, PASSWORD, VENDOR_ID, MAC_LE,
                    key0, login_write, verify_check, session_key,
                    cmd_iv, notify_iv, encrypt_packet, decrypt_packet, build_packet)

SVC  = "00010203-0405-0607-0809-0a0b0c0d1910"
PAIR = f"{SVC[:-4]}1914"
CMD  = f"{SVC[:-4]}1912"
NTF  = f"{SVC[:-4]}1911"

k0 = key0(MESH_NAME, PASSWORD)
seq = int(time.time() * 1000) & 0xFFFFFF
notifications = []

def on_notify(_ch, data):
    notifications.append(bytes(data))
    print(f"[notify] {bytes(data).hex()}")

async def main():
    async with BleakClient(MAC, timeout=20) as cl:
        print(f"connected: {cl.is_connected}")
        await cl.start_notify(NTF, on_notify)

        # --- login handshake ---
        import os
        rand8 = os.urandom(8)
        await cl.write_gatt_char(PAIR, login_write(k0, rand8), response=True)
        resp = await cl.read_gatt_char(PAIR)
        print(f"login read: {bytes(resp).hex()} ({len(resp)} bytes)")
        resp = bytes(resp)
        op = resp[0]
        print(f"op = 0x{op:02x}", "(FAIL — wrong credentials)" if op not in (0x0C, 0x0D) else "")
        r2 = resp[1:9]
        check = resp[9:17]
        if check != verify_check(k0, r2):
            print("!! check mismatch — aborting (device rejected handshake)")
            return
        sk = session_key(k0, rand8, r2)
        print(f"sessionKey: {sk.hex()}  — LOGIN OK")

        # --- read-only queries; discriminate real device address ---
        candidates = [0xFFFF, 0x1234, 0x0000]
        queries = [[0x48], [0x44, 0xFF], [0x44, 0xFB]]
        for addr in candidates:
            for params in queries:
                global seq
                seq += 1
                pkt = build_packet(seq, addr, 0xF0, VENDOR_ID, params)
                enc = encrypt_packet(sk, cmd_iv(MAC_LE, seq), pkt)
                print(f"[write] addr=0x{addr:04x} params={[hex(p) for p in params]}")
                await cl.write_gatt_char(CMD, enc, response=True)
                got = list(notifications); notifications.clear()
                await asyncio.sleep(2.0)
                got += notifications; notifications.clear()
                for n in got:
                    dec = decrypt_packet(sk, notify_iv(MAC_LE, n), n)
                    if dec:
                        p = dec[7:]
                        if p[:3] == bytes([0xEA, 0x11, 0x02]):
                            p = p[3:]
                        print(f"  <- resp: {p.hex()}")
                    else:
                        print(f"  <- raw (enc fail): {n.hex()}")
            print(f"  [addr 0x{addr:04x}: {len(notifications)} pending]")

asyncio.run(main())