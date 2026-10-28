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


def build_go2rtc_relay_config(
    cameras: list[CameraInfo],
    user_id: str = "你的小米UID",
    region: str = "cn",
    ip_hint: str = "摄像机IP",
) -> str:
    """生成 go2rtc 云端中继流的 YAML 片段（供用户在 go2rtc 配置中粘贴）。

    注意：小米云中继需要的是老式 xiaomiio ``service_token``，与官方 Xiaomi Home
    集成的 OAuth2 token 不同，无法自动复用。请在 go2rtc 的 Web UI 用小米账号
    登录一次（支持账号密码 + 验证码），go2rtc 会自动拿到令牌；随后按下方片段
    逐台建立流，流名必须恰好等于摄像机 DID（本集成读取 rtsp://<relay>/<DID>）。
    """
    lines = ["streams:"]
    for cam in cameras:
        lines.append(f'  "{cam.did}":')
        lines.append(
            f"    - xiaomi://{user_id}:{region}@{ip_hint}"
            f"?did={cam.did}&model={cam.model}"
        )
    return "\n".join(lines)
