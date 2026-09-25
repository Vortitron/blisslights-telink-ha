"""Exercise the blisslights client and entities against a fake Telink device.

Stubs just enough of homeassistant + bleak to import the integration, then
runs the real telink_protocol crypto on both ends.
"""

import asyncio
import enum
import os
import sys
import types

FORK = os.path.join(os.path.dirname(__file__), "..", "custom_components")
sys.path.insert(0, FORK)


def mod(name, **attrs):
    m = types.ModuleType(name)
    m.__dict__.update(attrs)
    sys.modules[name] = m
    return m


class BleakError(Exception):
    pass


class HomeAssistantError(Exception):
    pass


class UpdateFailed(HomeAssistantError):
    pass


class ConfigEntryNotReady(HomeAssistantError):
    pass


mod("bleak", BleakClient=object, BleakError=BleakError)
mod("bleak.backends")
mod("bleak.backends.device", BLEDevice=object)

CONNECTS = []


async def establish_connection(cls, device, name, disconnected_callback=None, timeout=None):
    client = FakeClient(DEVICE, disconnected_callback)
    CONNECTS.append(client)
    return client


mod("bleak_retry_connector", establish_connection=establish_connection)
mod("homeassistant")
mod("homeassistant.components")
mod("homeassistant.components.bluetooth", async_ble_device_from_address=lambda hass, a: object())
sys.modules["homeassistant.components"].bluetooth = sys.modules["homeassistant.components.bluetooth"]
mod("homeassistant.config_entries", ConfigEntry=object, ConfigFlow=object, ConfigFlowResult=object)
mod("homeassistant.const", CONF_ADDRESS="address", CONF_NAME="name",
    Platform=enum.Enum("Platform", "LIGHT SELECT SWITCH"))
mod("homeassistant.core", HomeAssistant=object)
mod("homeassistant.exceptions", ConfigEntryNotReady=ConfigEntryNotReady, HomeAssistantError=HomeAssistantError)
mod("homeassistant.helpers")
mod("homeassistant.helpers.debounce", Debouncer=lambda *a, **k: None)


class CoordinatorEntity:
    def __init__(self, coordinator):
        self.coordinator = coordinator


mod("homeassistant.helpers.update_coordinator", CoordinatorEntity=CoordinatorEntity,
    DataUpdateCoordinator=object, UpdateFailed=UpdateFailed)
mod("homeassistant.helpers.entity_platform", AddEntitiesCallback=object)
mod("homeassistant.components.light", ATTR_BRIGHTNESS="brightness", ATTR_RGB_COLOR="rgb_color",
    ColorMode=types.SimpleNamespace(RGB="rgb"), LightEntity=object)
mod("homeassistant.components.switch", SwitchEntity=object)
mod("homeassistant.components.select", SelectEntity=object)

import blisslights  # noqa: E402
from blisslights import TelinkClient  # noqa: E402
from blisslights import const, telink_protocol as tp  # noqa: E402
from blisslights.light import BlissLight, bright_from_ha, bright_to_ha  # noqa: E402
from blisslights.switch import BlissRotationSwitch  # noqa: E402
from blisslights.select import BlissSceneSelect  # noqa: E402

const.IDLE_DISCONNECT_SECONDS = 0.5
blisslights.IDLE_DISCONNECT_SECONDS = 0.5
blisslights.RESPONSE_TIMEOUT = 0.5

ADDRESS = "A4:C1:38:B5:84:05"
MESH, PWD = "eb5786bfb857", "123"
MAC_LE = tp.mac_le_from_address(ADDRESS)


def encrypt_notify(key, prefix5, payload13):
    data = bytearray(prefix5 + b"\x00\x00" + payload13.ljust(13, b"\x00"))
    iv = tp.notify_iv(MAC_LE, data)
    a = bytearray(tp._att(key, bytes(iv) + bytes([13]) + b"\x00" * 7))
    for i in range(13):
        a[i & 15] ^= data[i + 7]
    a = bytearray(tp._att(key, bytes(a)))
    data[5], data[6] = a[0], a[1]
    ctr = bytearray(16)
    ctr[1:9] = iv
    ks = tp._att(key, bytes(ctr))
    for i in range(13):
        data[i + 7] ^= ks[i & 15]
    return bytes(data)


def decrypt_command(key, pkt):
    seq = pkt[0] | pkt[1] << 8 | pkt[2] << 16
    iv = tp.cmd_iv(MAC_LE, seq)
    data = bytearray(pkt)
    ctr = bytearray(16)
    ctr[1:9] = iv
    ks = tp._att(key, bytes(ctr))
    for i in range(15):
        data[i + 5] ^= ks[i & 15]
    # verify MAC
    a = bytearray(tp._att(key, bytes(iv) + bytes([15]) + b"\x00" * 7))
    for i in range(15):
        a[i & 15] ^= data[i + 5]
    a = tp._att(key, bytes(a))
    assert (pkt[3], pkt[4]) == (a[0], a[1]), "bad command MAC"
    return seq, bytes(data[10:20])


class FakeDevice:
    def __init__(self):
        self.on = True
        self.ch = dict(r=255, g=255, b=255, laser=10, motor=255, bright=3, breathe=0)
        self.scene = 1
        self.commands = []
        self.seqs = []

    def readback(self):
        if not self.on:
            return [0] * 7
        return [self.ch[k] for k in const.CHANNELS]

    def handle(self, params):
        self.commands.append(params)
        op = params[0]
        if op == 0x41 and params[2] == 0x01:
            self.on = bool(params[1])
            return None
        if op == 0x41 and params[2] == 0x00:
            self.on, self.scene = True, params[1]
            return None
        if op == 0x47:
            self.on = True
            self.ch.update(zip(const.CHANNELS, params[1:8]))
            return None
        if op == 0x48:
            return bytes([0xEA, 0x11, 0x02, 0x48] + self.readback())
        if op == 0x44 and params[1] == 0xFF:
            return bytes([0xEA, 0x11, 0x02, 0x44, 0xFF, 255, 3, 24, 12, 11, self.scene, 0, 0])
        raise AssertionError(f"unexpected {params.hex()}")


class FakeClient:
    def __init__(self, device, on_disconnect):
        self.device = device
        self.on_disconnect = on_disconnect
        self.is_connected = True
        self.notify_cb = None
        self.sk = None
        self.k0 = tp.key0(MESH, PWD)
        self.pair_resp = None
        self.fail_next_write = False

    async def start_notify(self, char, cb):
        self.notify_cb = cb

    async def write_gatt_char(self, char, data, response=True):
        if not self.is_connected or self.fail_next_write:
            self.fail_next_write = False
            raise BleakError("not connected")
        if char == const.CHAR_PAIR:
            assert data[0] == 0x0C
            rand8 = bytes(data[1:9])
            assert data == tp.login_write(self.k0, rand8), "bad login"
            r2 = os.urandom(8)
            self.pair_resp = bytes([0x0D]) + r2 + tp.verify_check(self.k0, r2)
            self.sk = tp.session_key(self.k0, rand8, r2)
            return
        assert char == const.CHAR_COMMAND and self.sk
        seq, params = decrypt_command(self.sk, data)
        self.device.seqs.append(seq)
        resp = self.device.handle(params)
        if resp is not None:
            pkt = encrypt_notify(self.sk, os.urandom(5), resp[:13])
            asyncio.get_running_loop().call_soon(self.notify_cb, None, bytearray(pkt))

    async def read_gatt_char(self, char):
        return self.pair_resp

    async def disconnect(self):
        if self.is_connected:
            self.is_connected = False
            if self.on_disconnect:
                self.on_disconnect(self)

    def drop(self):
        """Simulate the link dying underneath us."""
        self.is_connected = False
        self.on_disconnect(self)


class FakeHass:
    def __init__(self):
        self.loop = asyncio.get_running_loop()
        self.tasks = []

    def async_create_background_task(self, coro, name):
        t = self.loop.create_task(coro)
        self.tasks.append(t)
        return t


class FakeCoordinator:
    def __init__(self, client):
        self.client = client
        self.data = {}
        self.refreshes = 0

    async def refresh(self):
        data = await self.client.query_state()
        if blisslights.is_lit(data):
            data["last_on"] = {k: data[k] for k in const.CHANNELS}
        elif "last_on" in self.data:
            data["last_on"] = self.data["last_on"]
        self.data = data

    def async_set_updated_data(self, data):
        self.data = data

    async def async_request_refresh(self):
        self.refreshes += 1


DEVICE = None


async def main():
    global DEVICE
    DEVICE = FakeDevice()
    hass = FakeHass()
    client = TelinkClient(hass, ADDRESS, MESH, PWD)
    entry = types.SimpleNamespace(data={"address": ADDRESS, "name": "Sky Lite"})
    coord = FakeCoordinator(client)

    # 1. One refresh = one connection, both queries answered.
    await coord.refresh()
    assert len(CONNECTS) == 1, CONNECTS
    assert coord.data["bright"] == 3 and coord.data["last_scene"] == 1, coord.data
    print("refresh ok:", {k: coord.data[k] for k in ("r", "laser", "motor", "bright", "last_scene")})

    light = BlissLight(coord, entry, client)
    rot = BlissRotationSwitch(coord, entry, client)
    scene = BlissSceneSelect(coord, entry, client)
    assert light.is_on and light.brightness == 255 and rot.is_on

    # 2. Burst of commands + refresh reuse the same session; seqs are unique.
    await light.async_turn_off()
    assert not light.is_on and not rot.is_on, "optimistic off"
    await light.async_turn_on()
    assert light.is_on and rot.is_on and light.brightness == 255, "optimistic on restores last_on"
    await rot.async_turn_off()
    assert not rot.is_on
    await scene.async_select_option("Ocean")
    assert scene.current_option == "Ocean"
    await light.async_turn_on(brightness=128)
    assert DEVICE.ch["bright"] == 2 and light.brightness == 170, (DEVICE.ch, light.brightness)
    await coord.refresh()
    assert len(CONNECTS) == 1, f"expected reuse, got {len(CONNECTS)} connects"
    assert len(set(DEVICE.seqs)) == len(DEVICE.seqs), "duplicate seq"
    assert DEVICE.ch["motor"] == 0 and DEVICE.scene == 5
    print("burst ok: 1 connection for", len(DEVICE.commands), "commands, seqs unique")

    # 3. Off, then turn on with a colour: laser/motor come from last_on, not 0.
    await rot.async_turn_on()
    await coord.refresh()
    await light.async_turn_off()
    await coord.refresh()
    assert not light.is_on and coord.data["r"] == 0
    await light.async_turn_on(rgb_color=(255, 0, 0))
    assert DEVICE.ch["laser"] == 10 and DEVICE.ch["motor"] == 255 and DEVICE.ch["g"] == 0, DEVICE.ch
    print("colour-from-off ok:", DEVICE.ch)

    # 4. Idle disconnect releases the connection.
    await asyncio.sleep(0.8)
    await asyncio.gather(*hass.tasks)
    assert not CONNECTS[-1].is_connected and client._client is None
    print("idle disconnect ok")

    # 5. Next command reconnects.
    await coord.refresh()
    assert len(CONNECTS) == 2
    # 6. A reused session whose link died silently: retry transparently.
    CONNECTS[-1].fail_next_write = True
    await light.async_turn_off()
    assert len(CONNECTS) == 3 and not DEVICE.on
    print("stale-session retry ok")
    # 7. Link dropped (callback fired): next call reconnects.
    CONNECTS[-1].drop()
    await coord.refresh()
    assert len(CONNECTS) == 4
    print("dropped-link reconnect ok")

    # 8. Brightness mapping edges.
    assert [bright_from_ha(b, 3) for b in (1, 42, 43, 85, 128, 170, 212, 213, 255)] == [1, 1, 1, 1, 2, 2, 2, 3, 3]
    assert bright_to_ha(1) == 85 and bright_to_ha(3) == 255 and bright_from_ha(200, 180) == 200
    print("brightness mapping ok")

    await client.async_close()
    assert client._client is None
    print("ALL OK")


asyncio.run(main())
