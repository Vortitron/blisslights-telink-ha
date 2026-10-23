"""Rotation and fading switches for the BlissLights Sky Lite projector."""

from __future__ import annotations

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import BlissLightsEntity
from .const import DEFAULT_BREATHE, DOMAIN


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    data = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        [
            BlissRotationSwitch(data["coordinator"], entry, data["client"]),
            BlissFadingSwitch(data["coordinator"], entry, data["client"]),
        ]
    )


class BlissChannelSwitch(BlissLightsEntity, SwitchEntity):
    """One 0x47 channel as a switch: nonzero = on (the app's semantics).

    Changes go out as a full 0x47 with the other channels kept as they are,
    and only while the projector is lit.
    """

    _channel: str

    @property
    def is_on(self) -> bool:
        return bool(self.coordinator.data.get(self._channel, 0))

    def _on_value(self) -> int:
        return 255

    async def async_turn_on(self, **kwargs) -> None:
        await self._set_channels(**{self._channel: self._on_value()})

    async def async_turn_off(self, **kwargs) -> None:
        await self._set_channels(**{self._channel: 0})


class BlissRotationSwitch(BlissChannelSwitch):
    """Laser rotation: the motor byte (0-255 speed slider in the app)."""

    _channel = "motor"

    def __init__(self, coordinator, entry, client):
        super().__init__(coordinator, entry, client, kind="rotation", name_suffix="Rotation")


class BlissFadingSwitch(BlissChannelSwitch):
    """The app's "Fading" (breathing) toggle: the breathe byte.

    Not the "Fading" scene, which is a separate built-in effect.
    """

    _channel = "breathe"

    def __init__(self, coordinator, entry, client):
        super().__init__(coordinator, entry, client, kind="fading", name_suffix="Fading")

    def _on_value(self) -> int:
        return self.coordinator.data.get("last_breathe") or DEFAULT_BREATHE
