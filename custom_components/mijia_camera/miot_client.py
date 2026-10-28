"""设备来源：读取官方 Xiaomi Home 集成已下载的设备清单。

本集成不自行登录小米账号，而是复用官方 Xiaomi Home 集成（xiaomi_home）
已经登录并缓存到 Home Assistant `.storage/xiaomi_home/` 目录下的设备列表。
这样彻底绕开小米账号的登录风控，也无需任何小米云签名 / 令牌 / 密码。
"""
from __future__ import annotations

import glob
import json
import logging
import os
from typing import Any

from homeassistant.core import HomeAssistant

from .const import XIAOMI_HOME_STORAGE_DIR

_LOGGER = logging.getLogger(__name__)


class CameraInfo:
    """单个摄像头设备的简档。"""

    def __init__(
        self,
        did: str,
        name: str,
        model: str,
        local_ip: str,
        token: str,
        online: bool,
    ) -> None:
        self.did = did
        self.name = name
        self.model = model
        self.local_ip = local_ip
        self.token = token
        self.online = online

    def to_dict(self) -> dict[str, Any]:
        return {
            "did": self.did,
            "name": self.name,
            "model": self.model,
            "local_ip": self.local_ip,
            "token": self.token,
            "online": self.online,
        }


def storage_dir(hass: HomeAssistant) -> str:
    """返回官方 Xiaomi Home 集成的 storage 目录。"""
    return hass.config.path(XIAOMI_HOME_STORAGE_DIR)


def _iter_device_files(hass: HomeAssistant):
    """遍历官方集成 storage 目录下所有设备 JSON 文件。"""
    base = storage_dir(hass)
    if not os.path.isdir(base):
        _LOGGER.debug("xiaomi_home storage 目录不存在：%s", base)
        return
    for path in glob.glob(os.path.join(base, "**", "*.json"), recursive=True):
        if "miot_devices" not in os.path.basename(path):
            continue
        try:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("读取设备文件失败 %s: %s", path, err)
            continue
        if isinstance(data, dict):
            yield data


def load_all_devices(hass: HomeAssistant) -> dict[str, dict[str, Any]]:
    """读取官方集成的全部设备（以 did 为键去重）。"""
    devices: dict[str, dict[str, Any]] = {}
    for data in _iter_device_files(hass):
        for did, dev in data.items():
            if isinstance(dev, dict) and "model" in dev:
                devices[str(did)] = dev
    return devices


def discover_cameras(hass: HomeAssistant) -> list[CameraInfo]:
    """筛选官方集成设备中的摄像头（以 did 去重）。"""
    cameras: list[CameraInfo] = []
    seen: set[str] = set()
    for did, dev in load_all_devices(hass).items():
        if did in seen:
            continue
        model = (dev.get("model") or "").lower()
        if "camera" not in model:
            continue
        seen.add(did)
        cameras.append(
            CameraInfo(
                did=str(did),
                name=dev.get("name") or str(did),
                model=dev.get("model") or "",
                local_ip=dev.get("local_ip") or "",
                token=dev.get("token") or "",
                online=bool(dev.get("online", False)),
            )
        )
    return cameras
