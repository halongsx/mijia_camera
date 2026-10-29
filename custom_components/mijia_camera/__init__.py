"""米家摄像头集成入口。"""
from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import DOMAIN, CONF_MODE, CONF_RELAY_HOST, MODE_RELAY
from .coordinator import MijiaCoordinator
from .webrtc_provider import register_provider

_LOGGER = logging.getLogger(__name__)

PLATFORMS = ["camera"]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """从配置项建立集成。"""
    coordinator = MijiaCoordinator(hass)
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = {"coordinator": coordinator}

    # 注意：不要在此阻塞等待首次刷新（async_config_entry_first_refresh）。
    # 当官方 Xiaomi Home 设备数据尚未就绪、刷新抛 UpdateFailed 时，会触发
    # ConfigEntryNotReady，使整个集成 setup 中止，已配置的 camera 实体会全部
    # 变成「不再提供此实体」。改为直接转发平台 setup，由 camera 平台内部触发
    # 刷新并在失败时兜底创建实体；这里再补一次后台刷新供后续自动更新。
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    hass.async_create_task(coordinator.async_request_refresh())

    # 云端中继模式：注册 WebRTC 提供器，把实况从 HLS 改为 WebRTC，
    # 解决 go2rtc 中继源在 HLS 下只出首帧就卡住的问题。
    if entry.data.get(CONF_MODE, MODE_RELAY) == MODE_RELAY:
        remove = await register_provider(
            hass, entry.data.get(CONF_RELAY_HOST, "")
        )
        if remove is not None:
            hass.data[DOMAIN][entry.entry_id]["webrtc_remove"] = remove

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """卸载集成。"""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        entry_data = hass.data[DOMAIN].pop(entry.entry_id, None) or {}
        remove = entry_data.get("webrtc_remove")
        if remove is not None:
            try:
                remove()
            except Exception:  # noqa: BLE001
                _LOGGER.debug("mijia_camera: WebRTC 提供器注销异常（忽略）")
    return unloaded
