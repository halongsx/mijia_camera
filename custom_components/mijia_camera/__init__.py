"""米家摄像头集成入口。"""
from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import DOMAIN
from .coordinator import MijiaCoordinator

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

    # 云端中继模式的 WebRTC 由 camera 实体原生实现（async_handle_async_webrtc_offer），
    # 不再依赖独立的 CameraWebRTCProvider 注册（注册时序不可靠，常导致 WebRTC 不接管、
    # 前端退回 HLS 而卡在一帧）。详见 camera.py / webrtc.py。
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """卸载集成。"""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        hass.data[DOMAIN].pop(entry.entry_id, None)
    return unloaded
