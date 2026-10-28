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
    mode = data.get(CONF_MODE, MODE_RELAY)
    relay_host = data.get(CONF_RELAY_HOST, "")
    creds = data.get(CONF_CAMERA_CREDS, {})
    selected = data.get(CONF_DEVICES, [])

    # 首次刷新：即便失败也不要中断实体创建。实体会用兜底信息建立，
    # 等 coordinator 后续刷新拿到真实数据后自动补全（见下面的动态属性）。
    try:
        await coordinator.async_request_refresh()
    except Exception as err:  # noqa: BLE001
        _LOGGER.warning("mijia_camera: 首次刷新失败，将用兜底信息创建实体：%s", err)

    if not selected:
        _LOGGER.warning(
            "mijia_camera: 配置中未选择任何摄像头（devices 为空），未创建实体"
        )

    entities = []
    for did in selected:
        info = (coordinator.data or {}).get(did) or {
            "did": did,
            "name": did,
            "model": "",
            "local_ip": "",
            "token": "",
            "online": False,
        }
        entities.append(MijiaCamera(coordinator, did, info, mode, relay_host, creds))
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
        did: str,
        info: dict[str, Any],
        mode: str,
        relay_host: str,
        creds: dict[str, Any],
    ) -> None:
        super().__init__()
        self.coordinator = coordinator
        self._did = did
        self._info = info  # 首次刷新为空时的兜底信息
        self._mode = mode
        self._relay_host = relay_host
        self._creds = creds or {}
        self._attr_unique_id = f"mijia_camera_{did}"
        self._attr_name = info.get("name", did)
        self._attr_supported_features = CameraEntityFeature.STREAM

    @property
    def available(self) -> bool:
        # 实体一旦被创建即视为可用。能否真正出流交给 RTSP / go2rtc 连接本身判断，
        # 绝不再依赖官方的 online 标志、local_ip 或 coordinator 刷新结果——
        # 否则在跨子网 / 官方数据未就绪 / 刷新失败时，实体会被整块置灰为
        # 「此实体不可用」，用户连具体的流错误都看不到，无从排查。
        # 即使底层流暂时连不上，HA 也会在前端显示流错误信息，便于定位。
        return True

    @property
    def stream_source(self) -> str | None:
        # 动态计算：coordinator 刷新拿到真实 local_ip / token 后，流地址自动纠正，
        # 避免首次刷新为空时一次性写死的地址永远无效（尤其本地 RTSP 模式）。
        data = self.coordinator.data or {}
        info = data.get(self._did, self._info)
        if self._mode == MODE_RELAY:
            return _build_relay_source(self._relay_host, self._did)
        local_ip = info.get("local_ip") or ""
        if not local_ip:
            return None
        cam_creds = self._creds.get(self._did, {})
        user = cam_creds.get(CONF_RTSG_USER, DEFAULT_RTSP_USER)
        pw = cam_creds.get(CONF_RTSG_PW, "")
        return _build_local_source(info, user, pw)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        info = (self.coordinator.data or {}).get(self._did, self._info)
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
        source = self.stream_source
        if ffmpeg_bin is None or not source:
            return None
        try:
            proc = await asyncio.create_subprocess_exec(
                ffmpeg_bin,
                "-rtsp_transport",
                "tcp",
                "-i",
                source,
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
