"""Light entity for the BlissLights Sky Lite projector."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ATTR_RGB_COLOR,
    ColorMode,
    LightEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import BlissLightsEntity, is_lit
from .const import BRIGHT_LEVELS, CHANNELS, DOMAIN, LIGHT_CHANNELS

# 0x47 full-control payload: R, G, B, laser, motor, bright, breathe — raw
# bytes, except bright, which the Sky Lite treats as a level (see
# BRIGHT_LEVELS).
FULL_CONTROL = 0x47
# {0x41, onOff, 0x01} = power; {0x41, sceneId, 0x00} = scene switch.
POWER = 0x41

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    data = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([BlissLight(data["coordinator"], entry, data["client"])])


def bright_to_ha(bright: int) -> int:
    if bright <= BRIGHT_LEVELS:
        return round(bright * 255 / BRIGHT_LEVELS)
    return bright


def bright_from_ha(brightness: int, current: int) -> int:
    if current > BRIGHT_LEVELS:
        return brightness
    return max(1, min(BRIGHT_LEVELS, round(brightness * BRIGHT_LEVELS / 255)))


class BlissLight(BlissLightsEntity, LightEntity):
    """Sky Lite projector: on/off, brightness level, RGB."""

    _attr_color_mode = ColorMode.RGB
    _attr_supported_color_modes = {ColorMode.RGB}

    def __init__(self, coordinator, entry, client):
        super().__init__(coordinator, entry, client, kind="light")

    # ------------------------------------------------------------ state

    def _byte(self, key: str, default: int = 0) -> int:
        value = self.coordinator.data.get(key, default)
        return value if isinstance(value, int) else default

    def _lit_channels(self) -> dict[str, int]:
        """Current channels, or those from the last time it was lit if off."""
        data = self.coordinator.data
        if not is_lit(data) and data.get("last_on"):
            return dict(data["last_on"])
        return {key: self._byte(key) for key in CHANNELS}

    @property
    def is_on(self) -> bool:
        return is_lit(self.coordinator.data)

    @property
    def brightness(self) -> int | None:
        if "bright" not in self.coordinator.data:
            return None
        return bright_to_ha(self._byte("bright"))

    @property
    def rgb_color(self) -> tuple[int, int, int] | None:
        if "r" not in self.coordinator.data:
            return None
        return (self._byte("r"), self._byte("g"), self._byte("b"))

    # ------------------------------------------------------------ commands

    async def _power_on(self) -> dict[str, int]:
        """Power on from off; return the channels it is now lit with."""
        channels = self._lit_channels()
        await self._client.send(bytes([POWER, 0x01, 0x01]))
        if is_lit(channels):
            return channels
        # HA has never seen it lit (off ever since HA started), so all it has
        # is the zeros read back while off. Sending those as the colour would
        # blank the projector, so read what powering on restored instead.
        return await self._client.read_lit_channels() or channels

    async def async_turn_on(self, **kwargs: Any) -> None:
        brightness = kwargs.get(ATTR_BRIGHTNESS)
        rgb = kwargs.get(ATTR_RGB_COLOR)
        if self.is_on:
            channels = self._lit_channels()
            if brightness is None and rgb is None:
                await self._send(bytes([POWER, 0x01, 0x01]), **channels)
                return
        else:
            # 0x47 sets the values but does not power the projector on.
            channels = await self._power_on()
            if brightness is None and rgb is None:
                await self._assume(**channels)
                return
        if rgb is not None:
            channels["r"], channels["g"], channels["b"] = rgb
        if brightness is not None:
            channels["bright"] = bright_from_ha(brightness, channels.get("bright", 0))
        if not is_lit(channels):
            # Lit channels unknown (it didn't answer after power-on): a 0x47
            # with zeros would blank it, so leave it on its own settings.
            _LOGGER.warning("Sky Lite channels unknown after power-on; brightness not set")
            await self._assume()
            return
        params = bytes([FULL_CONTROL] + [channels.get(key, 0) for key in CHANNELS])
        await self._send(params, **channels)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._send(
            bytes([POWER, 0x00, 0x01]), motor=0, **{key: 0 for key in LIGHT_CHANNELS}
        )
