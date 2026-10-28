"""米家云客户端：登录并获取摄像头设备列表。

基于社区库 `micloud`（PyPI）实现登录与设备发现。
所有阻塞调用通过 executor 执行，避免阻塞 Home Assistant 事件循环。
"""
from __future__ import annotations

import logging
from typing import Any

_LOGGER = logging.getLogger(__name__)


class CameraInfo:
    """单个摄像头设备的简档。"""

    def __init__(
        self,
        did: str,
        name: str,
        model: str,
        localip: str,
        token: str,
        online: bool,
    ) -> None:
        self.did = did
        self.name = name
        self.model = model
        self.localip = localip
        self.token = token
        self.online = online

    def to_dict(self) -> dict[str, Any]:
        return {
            "did": self.did,
            "name": self.name,
            "model": self.model,
            "localip": self.localip,
            "token": self.token,
            "online": self.online,
        }


class MijiaClient:
    """米家账号客户端（封装 micloud）。"""

    def __init__(self, username: str, password: str, country: str = "cn") -> None:
        self._username = username
        self._password = password
        self._country = country
        self._cloud = None

    def login(self) -> bool:
        """登录米家云（同步，需在 executor 中调用）。

        返回 True 表示登录成功；False 表示账号/密码错误或被风控。
        """
        try:
            from micloud import MiCloud  # 延迟导入，避免未安装时影响集成加载
        except ImportError as err:  # pragma: no cover
            _LOGGER.error("未安装 micloud 库：%s", err)
            return False
        try:
            self._cloud = MiCloud(self._username, self._password)
            if not self._cloud.login():
                _LOGGER.warning("米家账号登录失败（账号/密码错误或触发风控）")
                return False
            return True
        except Exception as err:  # noqa: BLE001
            _LOGGER.exception("米家登录异常：%s", err)
            return False

    def get_cameras(self) -> list[CameraInfo]:
        """获取账号下所有摄像头设备（同步，需在 executor 中调用）。"""
        if self._cloud is None:
            return []
        try:
            devices = self._cloud.get_devices(country=self._country) or []
        except Exception as err:  # noqa: BLE001
            _LOGGER.exception("获取设备列表异常：%s", err)
            return []
        cameras: list[CameraInfo] = []
        for dev in devices:
            model = (dev.get("model") or "").lower()
            if "camera" not in model:
                continue
            did = str(dev.get("did"))
            name = dev.get("name") or did or "未知设备"
            cameras.append(
                CameraInfo(
                    did=did,
                    name=name,
                    model=dev.get("model") or "",
                    localip=dev.get("localip") or "",
                    token=dev.get("token") or "",
                    online=bool(dev.get("online", False)),
                )
            )
        return cameras
