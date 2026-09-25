"""BlissLights Sky Lite (Telink mesh v1) Home Assistant integration."""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import Awaitable, Callable
from datetime import timedelta
from typing import Any, TypeVar

from bleak import BleakClient, BleakError
from bleak.backends.device import BLEDevice
from bleak_retry_connector import establish_connection

from homeassistant.components import bluetooth
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_ADDRESS, CONF_NAME, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers.debounce import Debouncer
from homeassistant.helpers.update_coordinator import (
    CoordinatorEntity,
    DataUpdateCoordinator,
    UpdateFailed,
)

from .const import (
    CHANNELS,
    CHAR_COMMAND,
    CHAR_NOTIFY,
    CHAR_PAIR,
    CONF_MESH_NAME,
    CONF_PASSWORD,
    CONNECT_TIMEOUT,
    DOMAIN,
    IDLE_DISCONNECT_SECONDS,
    LIGHT_CHANNELS,
    MESH_ADDRESS,
    REFRESH_DELAY_SECONDS,
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

_T = TypeVar("_T")

_BLE_ERRORS = (BleakError, TimeoutError, asyncio.TimeoutError)


class TelinkClient:
    """Logged-in GATT session that is reused while busy and dropped when idle.

    Every exchange runs under one lock. The first one connects and logs in;
    later ones reuse the session until IDLE_DISCONNECT_SECONDS pass with no
    traffic, so a burst of commands plus the confirming refresh costs a single
    connect + login, and no proxy connection slot is held between bursts.
    """

    def __init__(self, hass: HomeAssistant, address: str, mesh_name: str, password: str):
        self.hass = hass
        self.address = address
        self.mesh_name = mesh_name
        self.password = password
        self._lock = asyncio.Lock()
        self._mac_le = mac_le_from_address(address)
        self._client: BleakClient | None = None
        self._session_key: bytes | None = None
        self._notifications: list[bytes] = []
        self._idle_handle: asyncio.TimerHandle | None = None
        self._last_used = 0.0
        self._seq = new_seq()

    def _device(self) -> BLEDevice:
        device = bluetooth.async_ble_device_from_address(self.hass, self.address)
        if device is None:
            raise UpdateFailed(f"Device not discoverable: {self.address}")
        return device

    def _next_seq(self) -> int:
        # Commands within one session must not reuse a sequence number.
        self._seq = (self._seq + 1) & 0xFFFFFF
        return self._seq

    def _on_notify(self, _char: Any, data: bytearray) -> None:
        self._notifications.append(bytes(data))

    def _on_disconnect(self, client: BleakClient) -> None:
        if client is self._client:
            self._client = None
            self._session_key = None

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

    async def _ensure_session(self) -> tuple[BleakClient, bytes]:
        client = self._client
        if client is not None and client.is_connected and self._session_key is not None:
            return client, self._session_key
        await self._drop()
        client = await establish_connection(
            BleakClient,
            self._device(),
            self.address,
            disconnected_callback=self._on_disconnect,
            timeout=CONNECT_TIMEOUT,
        )
        try:
            await client.start_notify(CHAR_NOTIFY, self._on_notify)
            sk = await self._login(client)
        except BaseException:
            await self._disconnect(client)
            raise
        self._client, self._session_key = client, sk
        return client, sk

    @staticmethod
    async def _disconnect(client: BleakClient) -> None:
        try:
            await client.disconnect()
        except _BLE_ERRORS as err:
            _LOGGER.debug("Ignoring error while disconnecting: %s", err)

    async def _drop(self) -> None:
        self._cancel_idle()
        client, self._client, self._session_key = self._client, None, None
        self._notifications.clear()
        if client is not None:
            await self._disconnect(client)

    def _cancel_idle(self) -> None:
        if self._idle_handle is not None:
            self._idle_handle.cancel()
            self._idle_handle = None

    def _schedule_idle(self) -> None:
        self._cancel_idle()
        self._idle_handle = self.hass.loop.call_later(
            IDLE_DISCONNECT_SECONDS,
            lambda: self.hass.async_create_background_task(
                self._idle_disconnect(), f"{DOMAIN} idle disconnect {self.address}"
            ),
        )

    async def _idle_disconnect(self) -> None:
        async with self._lock:
            # A timer that fired while an exchange held the lock is stale.
            idle_for = self.hass.loop.time() - self._last_used
            if idle_for < IDLE_DISCONNECT_SECONDS - 1:
                return
            self._idle_handle = None
            await self._drop()

    async def async_close(self) -> None:
        async with self._lock:
            await self._drop()

    async def _run(self, op: Callable[[BleakClient, bytes], Awaitable[_T]]) -> _T:
        """Run op on a logged-in session; retry once if a reused session died."""
        async with self._lock:
            self._cancel_idle()
            try:
                reused = self._client is not None
                try:
                    return await op(*await self._ensure_session())
                except _BLE_ERRORS as err:
                    await self._drop()
                    if not reused:
                        raise UpdateFailed(f"Bluetooth error: {err}") from err
                    _LOGGER.debug("Reused session failed (%s); reconnecting", err)
                try:
                    return await op(*await self._ensure_session())
                except _BLE_ERRORS as err:
                    await self._drop()
                    raise UpdateFailed(f"Bluetooth error: {err}") from err
            finally:
                self._last_used = self.hass.loop.time()
                if self._client is not None:
                    self._schedule_idle()

    async def _write(self, client: BleakClient, sk: bytes, params: bytes) -> None:
        seq = self._next_seq()
        pkt = build_packet(seq, MESH_ADDRESS, 0xF0, VENDOR_ID, params)
        enc = encrypt_packet(sk, cmd_iv(self._mac_le, seq), pkt)
        await client.write_gatt_char(CHAR_COMMAND, enc, response=True)

    async def _request(
        self, client: BleakClient, sk: bytes, params: bytes, expect: int
    ) -> bytes | None:
        """Send one vendor command and return the notify payload starting with expect."""
        self._notifications.clear()
        await self._write(client, sk, params)
        deadline = asyncio.get_running_loop().time() + RESPONSE_TIMEOUT
        while asyncio.get_running_loop().time() < deadline:
            while self._notifications:
                raw = self._notifications.pop(0)
                dec = decrypt_packet(sk, notify_iv(self._mac_le, raw), raw)
                if dec is None:
                    continue
                payload = strip_notify_prefix(dec[7:])
                if payload and payload[0] == expect:
                    return payload
            await asyncio.sleep(0.1)
        return None

    async def send(self, params: bytes) -> None:
        """Send one fire-and-forget vendor command."""
        params = bytes(params)

        async def _op(client: BleakClient, sk: bytes) -> None:
            await self._write(client, sk, params)

        await self._run(_op)

    async def query_state(self) -> dict[str, Any]:
        """Read LED state (0x48) and global config (0x44 0xFF) in one session."""

        async def _op(client: BleakClient, sk: bytes) -> dict[str, Any]:
            data: dict[str, Any] = {}
            # {0x48} -> R, G, B, laser, motor, bright, breathe
            state = await self._request(client, sk, bytes([0x48]), 0x48)
            if state is not None and len(state) >= 8:
                data.update(zip(CHANNELS, state[1:8]))
            # {0x44, 0xFF} -> [0x44, 0xFF, motor, bright, onTime, offTime,
            #                    defaultScene, lastScene, loopTime, isLoop]
            cfg = await self._request(client, sk, bytes([0x44, 0xFF]), 0x44)
            if cfg is not None and len(cfg) >= 10:
                data["default_scene"] = cfg[6]
                data["last_scene"] = cfg[7]
            return data

        return await self._run(_op)


def is_lit(data: dict[str, Any]) -> bool:
    """The projector reads back all light channels as 0 while it is off."""
    return any(data.get(key) for key in LIGHT_CHANNELS)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    hass.data.setdefault(DOMAIN, {})
    client = TelinkClient(
        hass,
        entry.data[CONF_ADDRESS],
        entry.data[CONF_MESH_NAME],
        entry.data[CONF_PASSWORD],
    )

    async def _async_update() -> dict[str, Any]:
        data = await client.query_state()
        if not data:
            raise UpdateFailed("Device did not answer state queries")
        # Remember the channels from the last time it was lit, so turning it
        # back on with a colour/brightness change doesn't zero laser + motor.
        previous = coordinator.data or {}
        if is_lit(data):
            data["last_on"] = {key: data[key] for key in CHANNELS if key in data}
        elif "last_on" in previous:
            data["last_on"] = previous["last_on"]
        return data

    coordinator = DataUpdateCoordinator(
        hass,
        _LOGGER,
        config_entry=entry,
        name=entry.data.get(CONF_NAME, DOMAIN),
        update_method=_async_update,
        update_interval=timedelta(seconds=UPDATE_INTERVAL_SECONDS),
        # Commands update state optimistically; one refresh confirms a burst.
        request_refresh_debouncer=Debouncer(
            hass, _LOGGER, cooldown=REFRESH_DELAY_SECONDS, immediate=False
        ),
    )

    # Verify we can talk to the device before creating entities (retries via
    # ConfigEntryNotReady). Give the scanner a moment on first setup.
    try:
        await coordinator.async_config_entry_first_refresh()
    except Exception as err:
        await client.async_close()
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
        data = hass.data[DOMAIN].pop(entry.entry_id, None)
        if data is not None:
            await data["client"].async_close()
    return unload_ok


class BlissLightsEntity(CoordinatorEntity):
    """Shared coordinator-entity base for all BlissLights entities."""

    def __init__(
        self, coordinator, entry, client: TelinkClient, kind: str, name_suffix: str | None = None
    ):
        super().__init__(coordinator)
        self._entry = entry
        self._client = client
        self._attr_unique_id = f"{entry.data[CONF_ADDRESS]}_{kind}"
        base_name = entry.data.get(CONF_NAME, "BlissLights")
        self._attr_name = base_name if name_suffix is None else f"{base_name} {name_suffix}"

    async def _send(self, params: bytes, **updates: Any) -> None:
        """Send a command, show its expected result now, confirm with a refresh."""
        await self._client.send(params)
        self.coordinator.async_set_updated_data({**(self.coordinator.data or {}), **updates})
        await self.coordinator.async_request_refresh()

    @property
    def device_info(self):
        from homeassistant.helpers.device_registry import DeviceInfo

        return DeviceInfo(
            identifiers={(DOMAIN, self._entry.data[CONF_ADDRESS])},
            name=self._entry.data.get(CONF_NAME, "BlissLights"),
            manufacturer="BlissLights",
            model="Sky Lite (Telink TLSR8250)",
        )
