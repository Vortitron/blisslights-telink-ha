# BlissLights (com.quhwa.mesh v3.3.67) — Telink BLE Mesh Protocol

Device family: BlissLights Sky Lite laser galaxy projector (and Smartscape LED strip,
`devType == 5`). Chip: Telink TLSR8250. This is the classic **Telink "mesh v1" encrypted
GATT protocol** (same family as Arilux AL-LC0x lights) — fully extractable from the app,
contrary to the community thread's "AES, never cracked" conclusion. Everything needed to
build a controller is in `com/telink/bluetooth/light/LightController.java`,
`com/telink/crypto/AES.java`, `com/quhwa/mesh/lightstrip/StripCmdManager.java`,
`com/quhwa/mesh/ui/fragment/Fra_MainHome.java`.

## GATT

Service `00010203-0405-0607-0809-0a0b0c0d1910`:
- `...1914` PAIR   (write+read; login handshake)
- `...1912` COMMAND (write / write-no-rsp; encrypted mesh packets)
- `...1911` NOTIFY  (notifications; encrypted mesh packets)
- `...1913` OTA

## Credentials

- Factory mesh: name `telink_mesh0`, password `123` (app Constant; SDK default `telink_mesh1`).
- After first pairing the app renames the mesh to a 12-hex-char name derived from the
  phone's UUID (`QuhwaMeshApplication.java:203-215`); password stays `123`.
  The name+pwd are also embedded in the app's device "share" QR (gzip+JSON).
- Mesh name/password are padded to 16 bytes with zeros; `key0 = name XOR pwd`.
- Vendor ID: **529 (0x0211)**. Factory LTK: `C0 C1 C2 C3 C4 C5 C6 C7 D8 D9 DA DB DC DD DE DF`.

## Observed advert (live capture, HA bluetooth diagnostics, 13 Sep 2026)

Device seen by HA's Realtek adapter as:

```
MAC:      A4:C1:38:2B:EC:B5          (A4:C1:38 = Telink OUI)
local name: "eeba4f5d3e18"           ← the app-renamed mesh name → login credential
mfg data (key 529): 11 02 b5 ec 2b 38
raw: 02 01 05 0d 09 "eeba4f5d3e18" 09 ff 11 02 11 02 b5 ec 2b 38
RSSI -74, connectable, ~28 s advert interval (idle)
```

Payload after company id `0x0211`: `11 02` (vendorId), `b5 ec` + `2b 38` (two 2-byte
UUID fields — exactly MAC[5],MAC[4] and MAC[3],MAC[2], i.e. MAC-derived). This is a
**short 6-byte advert**: it carries NO on/off status byte and NO mesh address, so both
must be obtained via GATT (login → query), not from scanning. The 12-hex local name
confirms the mesh-credential scheme above for this device.
**Mesh address discovered via GATT (live, 13 Sep 2026): 0x0000** — the device answers
queries addressed to 0x0000 and to broadcast 0xFFFF, but not to random addresses
(0x1234: no response). Targeted commands can use either.

## Live session (13 Sep 2026, read-only — full state read VERIFIED)

Run from HA's a0d7b954_ssh add-on container (python3 + bleak + cryptography installed
via `pip install --break-system-packages`; host dbus → BlueZ; add-on creds readable
via HA MCP `ha_get_app` add-on options). Probe: `probe/read_state.py` + `probe/telink.py`,
driven over SSH via `probe/hassh.py` (paramiko).

- Login: op byte in the 1914 read-back was **0x0D on success**; check bytes matched the
  derivation exactly → all handshake formulas above are live-verified.
- Vendor responses arrive as notifications with payload `EA 11 02 <cmd> <params...>`
  — a 3-byte prefix (op echo + vendorId LE) BEFORE the cmd echo. Strip `EA 11 02`.
- deviceType = 0x34; firmware = ASCII **"V001.14"** (via `{0x44,0xFB}`).
- State readback: `{0x48}` → R=8, G=13, B=10, laser=10, motor=255, bright=3, breathe=0;
  `{0x44,0xFF}` → motor=255, bright=3, onTime=24, offTime=12, defaultScene=11,
  lastScene=11, loopTime=0, isLoop=0. Current scene = 11 (user confirmed: device ON,
  running a custom/DIY program → values are LIVE, and nonzero fields = active).
- 0x48 handler semantics (Fra_MainHome.smali:2296-2303, live-consistent): the app maps
  `motor != 0` → rotation ON (checkbox), `breathe != 0` → fading/breathing ON;
  R/G/B/laser/bright pass through as raw slider bytes both directions (0x47 send does
  no scaling — `int-to-byte` straight from the slider value).

## AES primitives (com/telink/crypto/AES.java)

`AES.encrypt(key, data)` = AES-128-ECB-NoPadding on **both key and data byte-reversed**
(Telink little-endian convention); output is NOT re-reversed.
`aes_att_encryption(key, block)` = `reverse(AES.encrypt(key, block))` — i.e.
`reverse(AES_ECB(reverse(key), reverse(block)))`. All uses below are `aes_att_encryption`
(abbrev. `E(key, block)` below).

All 3-arg encrypt/decrypt functions are **verified byte-exact against the smali**
(baksmali classes2.dex).

### encrypt(key, iv, data) — command packets (in-place on `data`, 20 bytes)

```
# CBC-MAC pass over plaintext data[5..19] (15 bytes)
b = iv[0..7] ++ [15] ++ zero[7]            # 16 bytes
a = E(key, b)
for i in 0..14:
    a[i & 15] ^= data[i + 5]
    if i == 14: a = E(key, a)              # (i&15)==15 impossible for 15 iters
data[3], data[4] = a[0], a[1]              # auth MAC
zero(a); a[1..8] = iv[0..7]                # CTR counter block = [0, iv, zero*7]
for i in 0..14:
    if i == 0: ks = E(key, a); a[0] += 1
    data[i + 5] ^= ks[i & 15]              # CTR over data[5..19]
return data  # data[0..2] = seq stays PLAINTEXT, data[3..4] = MAC, data[5..19] = ciphertext
```

### decrypt(key, iv, data) — notify packets (in-place on `data`, 20 bytes)

```
# CTR pass over ciphertext data[7..19] (13 bytes)
b = [0] ++ iv[0..7] ++ zero[8]             # iv at block offset 1!
ks = None
for i in 0..12:
    if i == 0: ks = E(key, b); b[0] += 1   # only 1 block needed (13 bytes)
    data[i + 7] ^= ks[i & 15]
# MAC verify: CBC-MAC over PLAINTEXT data[7..19] (13 bytes)
b = iv[0..7] ++ [13] ++ zero[7]
a = E(key, b)
for i in 0..12:
    a[i & 15] ^= data[i + 7]
    if i == 12: a = E(key, a)
if data[5] != a[0] or data[6] != a[1]: return None
return data  # plaintext layout: data[0..4] = prefix(seq), data[5..6] = MAC, data[7..19] = payload
```

Note the asymmetry: command packets MAC 15 bytes at `data[5..]` (counter at block
offset 0), notify packets MAC 13 bytes at `data[7..]` (length byte 13). Both use
iv-at-offset-1 for the CTR counter block.

## MAC byte order

`LightPeripheral.getMacBytes()` parses the MAC hex string into 6 bytes then
**Arrays.reverse** — so `mac[]` is little-endian (last octet first). Use
`bytes.fromhex(mac.replace(':',''))[::-1]` in Python.

## IVs (LightController.getSecIVM / getSecIVS — smali-verified)

- Command IV (getSecIVM): `[mac[0], mac[1], mac[2], mac[3], 0x01, seq & 0xFF,
  (seq>>8) & 0xFF, (seq>>16) & 0xFF]`
- Notify IV base (getSecIVS): `[mac[0], mac[1], mac[2], 0,0,0,0,0]`; onNotify then
  copies packet bytes `data[0..4]` into `iv[3..7]` → final notify IV =
  `[mac0, mac1, mac2, d0, d1, d2, d3, d4]`.

## Login handshake (LightController.login:194-237 + LoginCommandCallback:829-865 + getSessionKey:650-670 — jadx-verified; `Arrays.reverse(b, from, to)` is INCLUSIVE of both endpoints)

1. Connect, enable notifications on 1911.
2. `key0 = pad16(meshName) XOR pad16(password)` (16 bytes).
3. `rand8` = 8 random bytes (`loginRandm` is `new byte[8]` — NOT 16).
4. `auth = AES.encrypt(pad16(rand8), key0)` — **note the swapped argument order**:
   the rand-padded block is passed as the *key*, key0 as the *data*. Raw `AES.encrypt`
   semantics (ECB on both reversed, output NOT att-reversed).
5. Write **17 bytes** to 1914: `[0x0C, rand8[0..7], reverse(auth[8..15])]`.
6. READ 1914 → ≥17 bytes `[op, r2(8), check(8)]`. `op == PAIR_ENC_FAIL` → wrong
   credentials, device side rejects; disconnect.
7. Verify: `chk = AES.encrypt(pad16(r2), key0)` (same swapped order); expect
   `check == reverse(chk[8..15])`.
8. `sessionKey = aes_att_encryption(key0, rand8 ++ r2)`
   (= `reverse(AES.encrypt(key0, rand8 ++ r2))` — normal argument order here).

## Mesh command packet (LightController.sendCommand — smali-verified)

Buffer is **exactly 20 bytes** (`new byte[0x14]`), params zero-padded:

```
[ seq LSB, seq, seq MSB, 0x00, 0x00,       # data[0..2] seq stays plaintext after encrypt
  meshAddr LSB, meshAddr MSB,              # data[5..6]
  opcode | 0xC0,                            # data[7] (0xF0 for vendor commands)
  vendorId LSB (0x11), vendorId MSB (0x02), # data[8..9]
  params... (≤ 10 bytes, zero-padded) ]     # data[10..19]
```

- `meshAddr`: target short address (device's own mesh address from its advert, or group).
- seq: 32-bit incrementing sequence (generateSequenceNumber).
- Encrypt with `encrypt(sessionKey, getSecIVM(mac, seq), packet)`; the MAC lands in
  bytes 3–4 and bytes 5–19 are encrypted. Write the full 20 bytes to 1912
  (write-with-response when response requested; write-no-rsp otherwise).
- Notifications on 1911: build notify IV from packet bytes 0–4, run
  `decrypt(sessionKey, iv, packet)`; the decrypted app payload starts at byte 7
  (13 bytes max) = `[cmd, params...]` (vendor command echo/status).

### Notify packet layout after decrypt

`data[7..19]` (13 bytes) = `[cmd, p0, p1, ...]` — same vendor-command format as sent
params. A raw unencrypted variant is used right after mesh reset: byte 7 = op
(0xE1 = device-address notify), bytes 8–9 = vendorId LE, bytes 10–11 = new mesh address LE
(parsed from the raw ciphertext-side buffer since the device sends it plaintext).

## Vendor command opcodes (app layer, opcode 0xF0 | vendorId 0x0211)

All sent via `sendCommandNoResponse((byte)0xF0, meshAddr, params)`.

### Projector (Sky Lite, non-strip devType) — Fra_MainHome.java

| Params | Meaning |
|---|---|
| `{0x41, onOff, 0x01}` | **Power on/off** (1=on, 0=off). On is idempotent, but **off sent while already off turns the projector on** (live, Sky Lite, 2026-10-05): check the state (`0x48`) before sending off |
| `{0x41, sceneId, 0x00}` | **Switch to scene/mode** (sceneId 1..N; 0 = off) |
| `{0x47, R, G, B, laser, motor, bright, breathe}` | **Full LED control**: RGB (0-255), laser intensity, motor (rotation) speed, brightness, breathing rate. Does **not** power the projector on from off; send `{0x41, 0x01, 0x01}` first |
| `{0x40, cfgId, p0..p6}` | scene/global config: `{0x40, id, motor, bright, onTime, offTime, defaultScene, lastScene, loopTime, isLoop}` |
| `{0x45, enable, ...}` | enter/exit DIY edit mode |
| `{0x44, sub}` | **State sync query**: sub ∈ {0xFF global config, 0xFD device type, DIY ids, 0xFB version} |
| `{0x48}` | **Query LED state** (direct cmd; response 0x48 = R,G,B,laser,motor,bright,breathe) |

`sendCmd2OneDevice` shows scene-DIY write format `{0x01,0x11,0x12,...}`-style 15-byte
packets (DIY scene content, addr-targeted).

### LED strip (Smartscape, devType 5) — StripCmdManager.java

| Params | Meaning |
|---|---|
| `{0x16, onOff, 0x01}` | Power |
| `{0x11, sceneId}` | effect/scene select (0 → also sends power off) |
| `{0x12, idx, mode, r, g, b, speed1, 0, speed2, 0}` | static/custom colour slot config |
| `{0x13, h, m, s}` | timer |
| `{0x1B}` | get timer/effect mode |
| `{0x1C}` | get device version |
| `{0x14, ...}` / `{0x15, ...}` | rhythm colours |
| `{0x17/0x18, sceneId, ...}` | DIY segment/overall effects |
| `{0x19, sceneId, save}` | save/cancel DIY |

## Scene/effect IDs (projector)

From app resources (int-array `0x7f030000` / string-array `0x7f030002`, loop drops
the last "0/ON-OFF" pseudo-entry) + `EFFECTDIYS` = ids 10..19 (`EFFECTDIY_MAX`=19):

| id | name | | id | name |
|---|---|---|---|---|
| 1 | Stars against nebula | | 6 | Space |
| 2 | Fading | | 8 | Sunrise |
| 3 | Stars | | 9 | RGB auto |
| 4 | Nebula | | 10–19 | DIY 1–10 |
| 5 | Ocean | | 0 | off |

All 0x47 bytes (R,G,B,laser,motor,bright,breathe) are raw 0–255 — the app's sliders
are `android:max="255"` and values pass through unscaled both directions.

`switchScenesMode(addr, 1)` = scene 1 = default effect.

## HA integration (built 13 Sep 2026)

Custom integration deployed at `/config/custom_components/blisslights/` (source:
`/workspace/blisslights_decompile/integration/custom_components/blisslights/`).
Light (on/off/brightness/RGB) + Scene select (built-ins + DIY 1–10) + Rotation
switch (via 0x47 motor byte); config flow discovers vendor-529 adverts and
live-validates the login before saving; 120 s GATT poll. Activates on the next
HA restart.

## Status readback / notify responses (Fra_MainHome.syncData + syncAllData — smali-verified)

The app's notify handler dispatches on `params[0]` (cmd). For the projector:

| Query sent | Response cmd | Response params (after cmd byte) |
|---|---|---|
| `{0x44, 0xFF}` | `0x44` (len ≥ 10) | `[0x44, 0xFF, motor, bright, onTime, offTime, defaultScene, lastScene, loopTime, isLoop]` |
| `{0x44, 0xFD}` | `0x44` | `[0x44, 0xFD, deviceType]` |
| `{0x44, 0xFB}` | `0x44` | `[0x44, 0xFB, ...version...]` |
| `{0x48}` (direct cmd, not via 0x44) | `0x48` (len ≥ 8) | `[0x48, R, G, B, laser, motor, bright, breathe]` — **full LED state** |

Device-initiated notifications (also arrive when another controller changed something):

- `0x49` (len ≥ 10): `[0x49, ?, meshAddr LSB, meshAddr MSB, sceneId, ?, ?, ?, ?, loopTime]`
  — current scene + loop pushed on scene change. App updates `lastScene`.
- `0x41` (len ≥ 2): `[0x41, sceneId]` — scene-change echo ("change to model id").
- `0x40` (len ≥ 10) with `p1 == 0xFF`: `[0x40, 0xFF, motor, bright, onTime, offTime,
  defaultScene, lastScene, loopTime, isLoop]` — config readback echo.
- `0x42`: motor switch echo. `0x45`: DIY edit enter/exit echo (`p1` = switch). `0x46`: reset device.

On/off state: the app reads it from the advert manufacturer-data status byte
(DefaultAdvertiseDataFilter, byte 14 of the 0xFF AD structure) — but the **observed
advert from the real device (see above) is a short 6-byte format with no status byte**,
so a controller must poll state over GATT (`0x48` query) or track it from command
echoes instead.

`synchFirstInit()` query order: 255 (0xFF), 253 (0xFD), DIY scene ids, 251 (0xFB version).

## HA integration notes

- No official/custom HA integration exists (as of Sep 2026). Community solutions were
  hardware PWM/Zigbee mods. This protocol doc makes a software controller viable:
  bleak/ESP32 BLE client + login handshake + 1912 writes.
- A device still on the factory mesh can be controlled with `telink_mesh0`/`123` without
  ever using the vendor app. If already re-meshed by the app, extract the new mesh name
  from the app's share-QR (gzip+JSON) or app logs.