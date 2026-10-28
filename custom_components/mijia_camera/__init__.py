"""米家摄像头集成入口。"""
from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import CONF_COUNTRY, CONF_PASSWORD, CONF_USERNAME, DOMAIN
from .coordinator import MijiaCoordinator
from .miot_client import MijiaClient

_LOGGER = logging.getLogger(__name__)

PLATFORMS = ["camera"]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """从配置项建立集成。"""
    username = entry.data[CONF_USERNAME]
    password = entry.data[CONF_PASSWORD]
    country = entry.data[CONF_COUNTRY]
    client = MijiaClient(username, password, country)
    coordinator = MijiaCoordinator(hass, client)

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = {"coordinator": coordinator}

    await coordinator.async_config_entry_first_refresh()
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """卸载集成。"""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        hass.data[DOMAIN].pop(entry.entry_id, None)
    return unloaded
