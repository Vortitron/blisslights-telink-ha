"""Light entity for the BlissLights Sky Lite projector."""

from __future__ import annotations

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

from . import BlissLightsEntity
from .const import DOMAIN

# 0x47 full-control payload: R, G, B, laser, motor, bright, breathe — all raw
# 0-255 bytes (app sliders are android:max="255", sent unscaled).
FULL_CONTROL = 0x47
# {0x41, onOff, 0x01} = power; {0x41, sceneId, 0x00} = scene switch (0x41 with
# 0x00 third byte is power, which we use for on/off).
POWER = 0x41


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    data = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([BlissLight(data["coordinator"], entry, data["client"])])


class BlissLight(BlissLightsEntity, LightEntity):
    """Sky Lite projector: on/off, brightness, RGB (raw byte values)."""

    _attr_color_mode = ColorMode.RGB
    _attr_supported_color_modes = {ColorMode.RGB}

    def __init__(self, coordinator, entry, client):
        super().__init__(coordinator, entry, kind="light")
        self._client = client

    # ------------------------------------------------------------ state

    def _byte(self, key: str, default: int = 0) -> int:
        value = self.coordinator.data.get(key, default)
        return value if isinstance(value, int) else default

    @property
    def is_on(self) -> bool:
        # Off-state readback not live-verified; nonzero light channels = on.
        return bool(
            self._byte("r") or self._byte("g") or self._byte("b") or self._byte("laser")
        )

    @property
    def brightness(self) -> int | None:
        if "bright" not in self.coordinator.data:
            return None
        return self._byte("bright")

    @property
    def rgb_color(self) -> tuple[int, int, int] | None:
        if "r" not in self.coordinator.data:
            return None
        return (self._byte("r"), self._byte("g"), self._byte("b"))

    # ------------------------------------------------------------ commands

    async def _apply_full_control(
        self, r: int | None = None, g: int | None = None, b: int | None = None,
        bright: int | None = None, motor: int | None = None,
    ) -> None:
        params = bytes(
            [
                FULL_CONTROL,
                r if r is not None else self._byte("r"),
                g if g is not None else self._byte("g"),
                b if b is not None else self._byte("b"),
                self._byte("laser"),
                motor if motor is not None else self._byte("motor"),
                bright if bright is not None else self._byte("bright"),
                self._byte("breathe"),
            ]
        )
        await self._client.exchange(params, None)
        await self.coordinator.async_request_refresh()

    async def async_turn_on(self, **kwargs: Any) -> None:
        brightness = kwargs.get(ATTR_BRIGHTNESS)
        rgb = kwargs.get(ATTR_RGB_COLOR)
        if brightness is None and rgb is None:
            await self._client.exchange(bytes([POWER, 0x01, 0x01]), None)
            await self.coordinator.async_request_refresh()
            return
        # Applying 0x47 with cached values both sets the value and turns the
        # projector on.
        await self._apply_full_control(
            r=rgb[0] if rgb else None,
            g=rgb[1] if rgb else None,
            b=rgb[2] if rgb else None,
            bright=brightness,
        )

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._client.exchange(bytes([POWER, 0x00, 0x01]), None)
        await self.coordinator.async_request_refresh()