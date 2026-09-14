"""Config flow for the BlissLights (Telink mesh) integration."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import voluptuous as vol

from homeassistant.components import bluetooth
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_ADDRESS, CONF_NAME

from .const import (
    DEFAULT_NAME,
    DEFAULT_PASSWORD,
    DOMAIN,
    MANUFACTURER_KEY,
    VENDOR_ID,
    CONF_MESH_NAME,
    CONF_PASSWORD,
)

_LOGGER = logging.getLogger(__name__)


def _is_blisslights_advertisement(service_info) -> bool:
    return MANUFACTURER_KEY in (service_info.manufacturer_data or {})


class BlissLightsConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the BlissLights config flow."""

    VERSION = 1

    def __init__(self):
        self._discovered: dict[str, Any] = {}

    async def async_step_user(self, user_input=None):
        errors: dict[str, str] = {}

        # Discovery: currently-advertised devices with vendor-id 0x0211
        # manufacturer data (key 529) and a local name (the mesh name).
        discovered = {}
        for service_info in bluetooth.async_discovered_service_info(
            self.hass, connectable=True
        ):
            if not _is_blisslights_advertisement(service_info):
                continue
            address = service_info.address
            if address in discovered:
                continue
            name = service_info.name or service_info.local_name or ""
            if not name:
                continue  # mesh name comes from the advert local name
            discovered[address] = name

        if user_input is not None:
            address = user_input[CONF_ADDRESS]
            if address not in self._discovered and address not in discovered:
                errors[CONF_ADDRESS] = "unknown"
            else:
                self._discovered.update(discovered)
                return await self.async_step_device(
                    {
                        CONF_ADDRESS: address,
                        CONF_NAME: user_input.get(CONF_NAME) or DEFAULT_NAME,
                        CONF_MESH_NAME: user_input.get(CONF_MESH_NAME)
                        or discovered.get(address, ""),
                        CONF_PASSWORD: user_input.get(CONF_PASSWORD) or DEFAULT_PASSWORD,
                    }
                )

        schema = vol.Schema(
            {
                vol.Required(CONF_ADDRESS): vol.In(
                    {a: f"{n} ({a})" for a, n in sorted(discovered.items())}
                ),
                vol.Optional(CONF_NAME, default=DEFAULT_NAME): str,
                vol.Optional(CONF_MESH_NAME, description="Mesh name from advert"): str,
                vol.Optional(CONF_PASSWORD, default=DEFAULT_PASSWORD): str,
            }
        )
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)

    async def async_step_device(self, user_input=None):
        errors: dict[str, str] = {}
        if user_input is not None:
            address = user_input[CONF_ADDRESS]
            await self.async_set_unique_id(address)
            self._abort_if_unique_id_configured()

            mesh_name = user_input.get(CONF_MESH_NAME) or self._discovered.get(address, "")
            password = user_input.get(CONF_PASSWORD, DEFAULT_PASSWORD)
            name = user_input.get(CONF_NAME) or DEFAULT_NAME

            if not mesh_name:
                errors[CONF_MESH_NAME] = "missing_mesh_name"
            else:
                ok, error = await _async_validate(self.hass, address, mesh_name, password)
                if ok:
                    return self.async_create_entry(
                        title=name,
                        data={
                            CONF_ADDRESS: address,
                            CONF_NAME: name,
                            CONF_MESH_NAME: mesh_name,
                            CONF_PASSWORD: password,
                        },
                    )
                errors["base"] = error

        schema = vol.Schema(
            {
                vol.Required(CONF_ADDRESS): str,
                vol.Required(CONF_MESH_NAME, default=user_input.get(CONF_MESH_NAME, "") if user_input else ""): str,
                vol.Required(CONF_PASSWORD, default=user_input.get(CONF_PASSWORD, DEFAULT_PASSWORD) if user_input else DEFAULT_PASSWORD): str,
                vol.Optional(CONF_NAME, default=user_input.get(CONF_NAME, DEFAULT_NAME) if user_input else DEFAULT_NAME): str,
            }
        )
        return self.async_show_form(step_id="device", data_schema=schema, errors=errors)


async def _async_validate(hass, address, mesh_name, password) -> tuple[bool, str | None]:
    """Try a live connect + login so bad credentials never get saved."""
    from .telink_protocol import key0, login_write, verify_check, PAIR_OP_SUCCESS
    from .const import CHAR_PAIR, CONNECT_TIMEOUT

    from bleak import BleakClient, BleakError
    from bleak_retry_connector import establish_connection

    device = bluetooth.async_ble_device_from_address(hass, address)
    if device is None:
        return False, "not_found"
    try:
        client = await establish_connection(
            BleakClient, device, address, timeout=CONNECT_TIMEOUT
        )
        try:
            k0 = key0(mesh_name, password)
            import os

            rand8 = os.urandom(8)
            await client.write_gatt_char(CHAR_PAIR, login_write(k0, rand8), response=True)
            resp = bytes(await client.read_gatt_char(CHAR_PAIR))
            if len(resp) < 17:
                _LOGGER.error(
                    "BlissLights %s: short login response (%d bytes)", address, len(resp)
                )
                return False, "cannot_connect"
            if resp[0] != PAIR_OP_SUCCESS:
                return False, "invalid_auth"
            if resp[9:17] != verify_check(k0, resp[1:9]):
                return False, "invalid_auth"
            return True, None
        finally:
            await client.disconnect()
    except (BleakError, TimeoutError, asyncio.TimeoutError, OSError) as err:
        _LOGGER.error("BlissLights %s: validation connect failed: %s", address, err)
        return False, "cannot_connect"
    except Exception as err:  # noqa: BLE001 - surface everything to the log
        _LOGGER.exception("BlissLights %s: unexpected validation error", address)
        return False, "cannot_connect"