"""数据协调器：定期重新读取官方集成的设备清单，刷新在线状态。"""
from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import (
    DataUpdateCoordinator,
    UpdateFailed,
)

from .miot_client import discover_cameras

_LOGGER = logging.getLogger(__name__)

SCAN_INTERVAL = timedelta(seconds=60)


class MijiaCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """缓存摄像头列表与在线状态（以 did 为键）。

    数据来源是官方 Xiaomi Home 集成缓存到本地的设备清单文件，因此本集成
    不轮询小米云，只在本地刷新；在线状态由官方集成负责更新到文件中。
    """

    def __init__(self, hass: HomeAssistant) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name="mijia_camera",
            update_interval=SCAN_INTERVAL,
        )

    async def _async_update_data(self) -> dict[str, Any]:
        try:
            cameras = await self.hass.async_add_executor_job(
                discover_cameras, self.hass
            )
        except Exception as err:  # noqa: BLE001
            raise UpdateFailed(f"刷新米家摄像头失败：{err}") from err
        return {cam.did: cam.to_dict() for cam in cameras}
