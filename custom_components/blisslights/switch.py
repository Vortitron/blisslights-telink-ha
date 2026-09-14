"""Rotation (motor) switch for the BlissLights Sky Lite projector."""

from __future__ import annotations

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import BlissLightsEntity
from .const import DOMAIN

FULL_CONTROL = 0x47


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    data = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([BlissRotationSwitch(data["coordinator"], entry, data["client"])])


class BlissRotationSwitch(BlissLightsEntity, SwitchEntity):
    """Laser rotation — implemented via the 0x47 full-control command.

    The motor byte is 0-255 (slider); app semantics: motor != 0 = rotation on.
    We toggle between the cached value and 0.
    """

    def __init__(self, coordinator, entry, client):
        super().__init__(coordinator, entry, kind="rotation", name_suffix="Rotation")
        self._client = client

    @property
    def is_on(self) -> bool:
        motor = self.coordinator.data.get("motor", 0)
        return bool(motor)

    async def async_turn_on(self, **kwargs) -> None:
        await self._set_motor(255)

    async def async_turn_off(self, **kwargs) -> None:
        await self._set_motor(0)

    async def _set_motor(self, value: int) -> None:
        current = self.coordinator.data
        params = bytes(
            [
                FULL_CONTROL,
                current.get("r", 0),
                current.get("g", 0),
                current.get("b", 0),
                current.get("laser", 0),
                value,
                current.get("bright", 0),
                current.get("breathe", 0),
            ]
        )
        await self._client.exchange(params, None)
        await self.coordinator.async_request_refresh()