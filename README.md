# BlissLights Sky Lite — Home Assistant integration

> Fork of [vik-pfqld/blisslights-telink-ha](https://github.com/vik-pfqld/blisslights-telink-ha)
> that installs through HACS and is faster and steadier in use. See
> [Changes in this fork](#changes-in-this-fork).

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
| `light.blisslights` | `light` | on/off, brightness (Low / Medium / High), RGB color, scene as effect |
| `select.blisslights_scene` | `select` | the 9 built-in scenes + 10 DIY slots (+ off) |
| `switch.blisslights_rotation` | `switch` | laser motor (rotation) on/off |
| `switch.blisslights_fading` | `switch` | the app's Fading (breathing) toggle on/off |

State is polled every 2 minutes. A logged-in BLE connection is reused for
bursts of commands and dropped after 15 s idle, so it doesn't hold one of your
adapter's connection slots between uses.

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

1. HACS → ⋮ → *Custom repositories* → add
   `https://github.com/Vortitron/blisslights-telink-ha`, category
   **Integration**.
2. Install **BlissLights Sky Lite**, restart Home Assistant.
3. Settings → Devices & Services → *Add Integration* → **BlissLights**.
4. Pick your device, enter the mesh name and password shown in the vendor app
   (factory default password is `123`).

Or manually: copy `custom_components/blisslights/` into your
`config/custom_components/` directory and restart.

## Changes in this fork

- **Installs through HACS**: adds `hacs.json`, plus the `documentation` /
  `issue_tracker` manifest keys and `translations/en.json`.
- **One connection per burst**: the upstream client did connect → login →
  one command → disconnect for every command, and a state refresh took two
  more connections (one per query), so one button press cost three full
  BLE connects. This fork keeps the logged-in session open while commands keep
  coming (15 s idle timeout), runs both state queries on one connection, and
  transparently reconnects once if a reused session turns out to be dead.
- **Optimistic state**: entities update the moment a command is sent. One
  refresh 3 s after the last command in a burst confirms it, so the UI no
  longer jumps back to the old state while a refresh is still running.
- **Brightness levels**: the Sky Lite reports brightness as a level 1–3
  (the app's Low / Medium / High), not 0–255. HA's slider maps onto the
  three levels (85 / 170 / 255). A firmware that reports values above 3 is
  still treated as raw 0–255.
- **Colour from off**: turning the projector on with a colour or brightness
  used to reuse the zeros it reads back while off, so the laser and rotation
  came back off. It now reuses the settings from the last time it was lit.
- **Scene as a light effect**: `light.turn_on` takes `effect: <scene name>`,
  so one call can power on, switch scene, and set the brightness on top of
  the scene's own settings, in that order. Example:
  `light.turn_on` with `effect: Stars against nebula` and `brightness_pct: 50`.
- **Fading switch**: the app's Fading (breathing) toggle, the `breathe`
  channel. Turning it back on restores the last speed seen. Rotation and
  Fading only act while the projector is lit.

`tests/fake_device_harness.py` runs the client and entities against a fake
Telink device (real crypto on both ends, HA and bleak stubbed):
`python3 tests/fake_device_harness.py`.

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