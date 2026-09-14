"""BlissLights Sky Lite (Telink mesh v1) Home Assistant integration."""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import timedelta
from typing import Any

from bleak import BleakClient, BleakError
from bleak.backends.device import BLEDevice
from bleak_retry_connector import establish_connection

from homeassistant.components import bluetooth
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_ADDRESS, CONF_NAME, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers.update_coordinator import (
    CoordinatorEntity,
    DataUpdateCoordinator,
    UpdateFailed,
)

from .const import (
    CHAR_COMMAND,
    CHAR_NOTIFY,
    CHAR_PAIR,
    CONF_MESH_NAME,
    CONF_PASSWORD,
    CONNECT_TIMEOUT,
    DOMAIN,
    MESH_ADDRESS,
    RESPONSE_TIMEOUT,
    UPDATE_INTERVAL_SECONDS,
    VENDOR_ID,
)
from .telink_protocol import (
    build_packet,
    cmd_iv,
    decrypt_packet,
    encrypt_packet,
    key0,
    login_write,
    mac_le_from_address,
    new_seq,
    notify_iv,
    session_key,
    strip_notify_prefix,
    verify_check,
    PAIR_OP_SUCCESS,
)

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.LIGHT, Platform.SELECT, Platform.SWITCH]


class TelinkClient:
    """Connect-on-demand client: connect -> login -> one exchange -> disconnect."""

    def __init__(self, hass: HomeAssistant, address: str, mesh_name: str, password: str):
        self.hass = hass
        self.address = address
        self.mesh_name = mesh_name
        self.password = password
        self._lock = asyncio.Lock()
        self._mac_le = mac_le_from_address(address)

    def _device(self) -> BLEDevice:
        device = bluetooth.async_ble_device_from_address(self.hass, self.address)
        if device is None:
            raise UpdateFailed(f"Device not discoverable: {self.address}")
        return device

    async def _login(self, client: BleakClient) -> bytes:
        """Run the 1914 handshake, return the session key."""
        k0 = key0(self.mesh_name, self.password)
        rand8 = os.urandom(8)
        await client.write_gatt_char(CHAR_PAIR, login_write(k0, rand8), response=True)
        resp = bytes(await client.read_gatt_char(CHAR_PAIR))
        if len(resp) < 17:
            raise UpdateFailed(f"Short login response ({len(resp)} bytes)")
        op = resp[0]
        if op != PAIR_OP_SUCCESS:
            raise UpdateFailed(f"Login rejected (op=0x{op:02x}) — wrong mesh name/password?")
        r2 = resp[1:9]
        check = resp[9:17]
        if check != verify_check(k0, r2):
            raise UpdateFailed("Login check bytes mismatch")
        return session_key(k0, rand8, r2)

    async def exchange(self, params: bytes, expect: int | None) -> bytes | None:
        """Connect, login, send one vendor command, return matching notify payload.

        expect = first payload byte of the awaited response (e.g. 0x48).
        Returns None if no matching response arrives (many cmds are fire-and-forget).
        """
        params = bytes(params)
        async with self._lock:
            device = self._device()
            notifications: list[bytes] = []

            def _on_notify(_char: int, data: bytearray) -> None:
                notifications.append(bytes(data))

            try:
                client = await establish_connection(
                    BleakClient, device, self.address, timeout=CONNECT_TIMEOUT
                )
                try:
                    await client.start_notify(CHAR_NOTIFY, _on_notify)
                    sk = await self._login(client)
                    seq = new_seq()
                    pkt = build_packet(seq, MESH_ADDRESS, 0xF0, VENDOR_ID, params)
                    enc = encrypt_packet(sk, cmd_iv(self._mac_le, seq), pkt)
                    await client.write_gatt_char(CHAR_COMMAND, enc, response=True)
                    if expect is None:
                        return None
                    deadline = asyncio.get_running_loop().time() + RESPONSE_TIMEOUT
                    while asyncio.get_running_loop().time() < deadline:
                        while notifications:
                            raw = notifications.pop(0)
                            dec = decrypt_packet(sk, notify_iv(self._mac_le, raw), raw)
                            if dec is None:
                                continue
                            payload = strip_notify_prefix(dec[7:])
                            if payload and payload[0] == expect:
                                return payload
                        await asyncio.sleep(0.2)
                    return None
                finally:
                    await client.disconnect()
            except (BleakError, TimeoutError, asyncio.TimeoutError) as err:
                raise UpdateFailed(f"Bluetooth error: {err}") from err


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    hass.data.setdefault(DOMAIN, {})
    client = TelinkClient(
        hass,
        entry.data[CONF_ADDRESS],
        entry.data[CONF_MESH_NAME],
        entry.data[CONF_PASSWORD],
    )

    async def _async_update() -> dict[str, Any]:
        data: dict[str, Any] = {}
        # {0x48} -> R, G, B, laser, motor, bright, breathe
        state = await client.exchange(bytes([0x48]), 0x48)
        if state is not None and len(state) >= 8:
            (
                data["r"],
                data["g"],
                data["b"],
                data["laser"],
                data["motor"],
                data["bright"],
                data["breathe"],
            ) = state[1:8]
        # {0x44, 0xFF} -> [0x44, 0xFF, motor, bright, onTime, offTime,
        #                    defaultScene, lastScene, loopTime, isLoop]
        cfg = await client.exchange(bytes([0x44, 0xFF]), 0x44)
        if cfg is not None and len(cfg) >= 10:
            data["default_scene"] = cfg[6]
            data["last_scene"] = cfg[7]
        if not data:
            raise UpdateFailed("Device did not answer state queries")
        return data

    coordinator = DataUpdateCoordinator(
        hass,
        _LOGGER,
        name=entry.data.get(CONF_NAME, DOMAIN),
        update_method=_async_update,
        update_interval=timedelta(seconds=UPDATE_INTERVAL_SECONDS),
    )

    # Verify we can talk to the device before creating entities (retries via
    # ConfigEntryNotReady). Give the scanner a moment on first setup.
    try:
        await coordinator.async_config_entry_first_refresh()
    except Exception as err:
        raise ConfigEntryNotReady(f"Device unreachable at setup: {err}") from err

    hass.data[DOMAIN][entry.entry_id] = {
        "client": client,
        "coordinator": coordinator,
    }
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id, None)
    return unload_ok


class BlissLightsEntity(CoordinatorEntity):
    """Shared coordinator-entity base for all BlissLights entities."""

    def __init__(self, coordinator, entry, kind: str, name_suffix: str | None = None):
        super().__init__(coordinator)
        self._entry = entry
        self._attr_unique_id = f"{entry.data[CONF_ADDRESS]}_{kind}"
        base_name = entry.data.get(CONF_NAME, "BlissLights")
        self._attr_name = base_name if name_suffix is None else f"{base_name} {name_suffix}"

    @property
    def device_info(self):
        from homeassistant.helpers.device_registry import DeviceInfo

        return DeviceInfo(
            identifiers={(DOMAIN, self._entry.data[CONF_ADDRESS])},
            name=self._entry.data.get(CONF_NAME, "BlissLights"),
            manufacturer="BlissLights",
            model="Sky Lite (Telink TLSR8250)",
        )