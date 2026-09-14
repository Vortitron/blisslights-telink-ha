"""Scene select entity for the BlissLights Sky Lite projector."""

from __future__ import annotations

import logging

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import BlissLightsEntity
from .const import DIY_SCENE_ID_END, DIY_SCENE_ID_START, DOMAIN, SCENES

_LOGGER = logging.getLogger(__name__)

POWER = 0x41

# id -> display name, including the 10 DIY slots
ALL_SCENES: dict[int, str] = dict(SCENES)
for _i in range(DIY_SCENE_ID_START, DIY_SCENE_ID_END + 1):
    ALL_SCENES[_i] = f"DIY {_i - DIY_SCENE_ID_START + 1}"

OPTIONS = list(ALL_SCENES.values())
NAME_TO_ID = {name: sid for sid, name in ALL_SCENES.items()}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    data = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([BlissSceneSelect(data["coordinator"], entry, data["client"])])


class BlissSceneSelect(BlissLightsEntity, SelectEntity):
    """Select the active scene; 'Off' is handled by the light entity."""

    def __init__(self, coordinator, entry, client):
        super().__init__(coordinator, entry, kind="scene", name_suffix="Scene")
        self._client = client
        self._attr_options = OPTIONS

    @property
    def current_option(self) -> str | None:
        scene = self.coordinator.data.get("last_scene")
        if scene is None:
            return None
        if not isinstance(scene, int):
            return None
        return ALL_SCENES.get(scene, f"Scene {scene}")

    async def async_select_option(self, option: str) -> None:
        scene_id = NAME_TO_ID.get(option)
        if scene_id is None:
            # tolerate a "Scene N" fallback value read back earlier
            if option.startswith("Scene "):
                scene_id = int(option.split()[1])
            else:
                return
        # {0x41, sceneId, 0x00} switches the scene (and powers on).
        await self._client.exchange(bytes([POWER, scene_id & 0xFF, 0x00]), None)
        await self.coordinator.async_request_refresh()