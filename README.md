# BlissLights Sky Lite — Home Assistant integration

A custom [Home Assistant](https://www.home-assistant.io/) integration for the
**BlissLights Sky Lite** laser galaxy projector (and other devices built on the
**Telink TLSR8250 BLE mesh v1** platform, e.g. the vendor app
`com.quhwa.mesh` / "BlissLights" / BLE mesh LED controllers of the same SDK).

The Sky Lite has no official API and its cloud/wifi story is nonexistent — this
integration talks to the projector **directly over Bluetooth LE**, speaking the
Telink mesh vendor protocol that was reverse-engineered from the vendor Android
app. Full protocol writeup: **[PROTOCOL.md](PROTOCOL.md)**.

## Entities

| Entity | Domain | What it does |
|---|---|---|
| `light.blisslights` | `light` | on/off, brightness, RGB color |
| `select.blisslights_scene` | `select` | the 9 built-in scenes + 10 DIY slots (+ off) |
| `switch.blisslights_rotation` | `switch` | laser motor (rotation) on/off |

State is polled every 2 minutes over a short-lived BLE connection
(connect → login → query → disconnect), which avoids hogging one of your
adapter's connection slots.

## Features

- **Discovery**: config flow discovers nearby devices advertising the Telink
  mesh service (vendor id `0x0211`) and validates the mesh name/password with
  a live login handshake before saving.
- **Protocol**: full Telink mesh v1 crypto — login handshake, session-key
  derivation, AES-128 (CBC-MAC + CTR) packet encryption/decryption — in a
  single self-contained module (`telink_protocol.py`, stdlib `cryptography`).
- **Scenes**: names + numbering taken from the vendor app (see
  `select.py`), including DIY slots 10–19.
- **Rotation switch**: the motor byte of the full-control command (`0x47`),
  so the laser can be stopped without changing color/brightness.

## Install (HACS custom repository)

1. HACS → ⋮ → *Custom repositories* → add this repo, category
   **Integration**.
2. Install **BlissLights Sky Lite**, restart Home Assistant.
3. Settings → Devices & Services → *Add Integration* → **BlissLights**.
4. Pick your device, enter the mesh name and password shown in the vendor app
   (factory default password is `123`).

Or manually: copy `custom_components/blisslights/` into your
`config/custom_components/` directory and restart.

## Configuration keys

| Key | Default | Meaning |
|---|---|---|
| `address` | — | BLE MAC address of the projector |
| `mesh_name` | — | mesh name from the vendor app (device advertises it as its local name) |
| `password` | `123` | mesh password |

## Notes / gotchas

- The integration requires Home Assistant **2024.x+** (uses
  `async_forward_entry_setups`) and the `cryptography` package bundled with HA.
- BLE range applies: the projector must be powered on and within radio reach
  of your HA host (or a [Bluetooth proxy](https://www.home-assistant.io/integrations/bluetooth/#connectivity)).
- The vendor app's "scheduling" feature is a 1–24 h countdown delay, not a
  time-of-day schedule — use a Home Assistant time-trigger automation instead.
- Only the projector's own mesh is affected; the integration does not join or
  manage other nodes in the mesh.

## Probe scripts

`probe/` contains the standalone, **read-only** scripts used to
reverse-engineer and verify the protocol:

- `telink.py` — protocol/crypto library (login, session keys, packet
  encrypt/decrypt) with the device constants at the top.
- `read_state.py` — connects, logs in, and dumps LED state, config, firmware
  version and device type without changing anything.

They need `bleak` + `cryptography` and a Bluetooth radio, e.g. from any Linux
box: `pip install bleak cryptography && python3 probe/read_state.py`.

## Credits & disclaimer

Protocol reverse-engineered from the vendor Android app for personal,
interoperability purposes. Not affiliated with BlissLights. Device names and
trademarks belong to their respective owners.