"""数据协调器：定期刷新摄像头在线状态。"""
from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .miot_client import MijiaClient

_LOGGER = logging.getLogger(__name__)

SCAN_INTERVAL = timedelta(seconds=60)


class MijiaCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """缓存摄像头列表与在线状态（以 did 为键）。"""

    def __init__(self, hass: HomeAssistant, client: MijiaClient) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name="mijia_camera",
            update_interval=SCAN_INTERVAL,
        )
        self.client = client

    async def _async_update_data(self) -> dict[str, Any]:
        try:
            cameras = await self.hass.async_add_executor_job(self.client.get_cameras)
        except Exception as err:  # noqa: BLE001
            raise UpdateFailed(f"刷新米家摄像头失败：{err}") from err
        return {cam.did: cam.to_dict() for cam in cameras}
