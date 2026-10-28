"""摄像头实体：为每台米家摄像头创建 Home Assistant camera 实体。"""
from __future__ import annotations

import asyncio
import logging
import shutil
from typing import Any

from homeassistant.components.camera import Camera, CameraEntityFeature
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    CONF_MODE,
    CONF_RELAY_HOST,
    CONF_DEVICES,
    CONF_CAMERA_CREDS,
    CONF_RTSG_USER,
    CONF_RTSG_PW,
    DEFAULT_RTSP_USER,
    DEFAULT_RTSP_PORT,
    DEFAULT_RTSP_PATH,
    MODE_RELAY,
    DOMAIN,
)
from .coordinator import MijiaCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: MijiaCoordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
    data = entry.data
    mode = data[CONF_MODE]
    relay_host = data.get(CONF_RELAY_HOST, "")
    creds = data.get(CONF_CAMERA_CREDS, {})
    selected = data[CONF_DEVICES]

    await coordinator.async_request_refresh()

    entities = []
    for did in selected:
        info = coordinator.data.get(did)
        if info is None:
            _LOGGER.warning("配置中的摄像头 %s 当前未在线或不存在，仍创建实体", did)
            # 用配置中保存的最小信息兜底，避免实体丢失
            info = {"did": did, "name": did, "model": "", "local_ip": "", "online": False}
        if mode == MODE_RELAY:
            source = _build_relay_source(relay_host, did)
        else:
            cam_creds = creds.get(did, {})
            user = cam_creds.get(CONF_RTSG_USER, DEFAULT_RTSP_USER)
            pw = cam_creds.get(CONF_RTSG_PW, "")
            source = _build_local_source(info, user, pw)
        entities.append(MijiaCamera(coordinator, info, source, mode))
    async_add_entities(entities)


def _build_local_source(info: dict[str, Any], user: str, pw: str) -> str:
    """构造本地 RTSP 地址。

    不同型号路径不同，这里做基础匹配；未知型号回退到默认路径。
    """
    ip = info.get("local_ip") or ""
    model = (info.get("model") or "").lower()
    if "aqara" in model:
        path = "live/ch00_0"  # Aqara G2H/G3 主码流
    else:
        path = DEFAULT_RTSP_PATH  # 小米/创米等：ch0_0.h264
    auth = f"{user}:{pw}" if pw else user
    return f"rtsp://{auth}@{ip}:{DEFAULT_RTSP_PORT}/{path}"


def _build_relay_source(relay_host: str, did: str) -> str:
    """构造云端中继 RTSP 地址（指向 go2rtc / mediaMTX）。

    流名使用摄像头 DID，需提前在 go2rtc 中配置米家账号并添加该摄像头。
    """
    relay_host = (relay_host or "127.0.0.1:8554").strip()
    if "://" not in relay_host:
        relay_host = f"rtsp://{relay_host}"
    base = relay_host.rstrip("/")
    return f"{base}/{did}"


class MijiaCamera(Camera):
    """米家摄像头实体。"""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: MijiaCoordinator,
        info: dict[str, Any],
        source: str,
        mode: str,
    ) -> None:
        super().__init__()
        self.coordinator = coordinator
        self._did = info["did"]
        self._info = info
        self._source = source
        self._mode = mode
        self._attr_unique_id = f"mijia_camera_{self._did}"
        self._attr_name = info.get("name", self._did)
        self._attr_supported_features = CameraEntityFeature.STREAM

    @property
    def available(self) -> bool:
        info = self.coordinator.data.get(self._did)
        if info is None:
            return False
        return bool(info.get("online", False))

    @property
    def stream_source(self) -> str | None:
        return self._source

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        info = self.coordinator.data.get(self._did, {})
        return {
            "did": self._did,
            "model": info.get("model", ""),
            "local_ip": info.get("local_ip", ""),
            "mode": self._mode,
        }

    async def async_camera_image(
        self, width: int | None = None, height: int | None = None
    ) -> bytes | None:
        """通过系统 ffmpeg 抓取单帧快照（可选能力）。"""
        ffmpeg_bin = shutil.which("ffmpeg")
        if ffmpeg_bin is None or not self._source:
            return None
        try:
            proc = await asyncio.create_subprocess_exec(
                ffmpeg_bin,
                "-rtsp_transport",
                "tcp",
                "-i",
                self._source,
                "-frames:v",
                "1",
                "-f",
                "image2",
                "-",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            out, _ = await proc.communicate()
            return out or None
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("快照抓取失败（%s）：%s", self._did, err)
            return None
