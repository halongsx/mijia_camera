"""设备来源：复用官方 Xiaomi Home 集成（xiaomi_home）的设备信息。

本集成不自行登录小米账号，而是复用官方 Xiaomi Home 集成已经拉取到的
设备信息，从而彻底绕开小米账号的登录风控，也无需任何小米云签名 / 令牌 /
密码。

设备信息有两个来源，按优先级合并：

1. **运行时内存**（首选）：官方集成把设备缓存在
   ``hass.data["xiaomi_home"]["devices"][config_entry_id]``（``MIoTDevice``
   对象列表）。这种方式最可靠、实时，无需解析私有文件。
2. **落盘文件**（兜底）：官方集成会把设备清单写入
   ``.storage/<集成目录>/miot_devices/<uid>_<server>.dict``。
   注意该文件是官方自研存储格式——后缀是 ``.dict`` 而非 ``.json``，且文件
   末尾追加了 32 字节 SHA256 校验值，读取时必须先剥离这几字节再 ``json.loads``。
   其中包含 ``local_ip`` / ``token`` 等本地 RTSP 所需的字段。
"""
from __future__ import annotations

import glob
import json
import logging
import os
from typing import Any

from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)

# 官方集成自研存储文件末尾追加的 SHA256 校验值长度（字节）
_MIOT_STORAGE_HASH_LEN = 32


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


def _extract_device(dev: Any) -> dict[str, Any] | None:
    """从 ``MIoTDevice`` 对象或原始 dict 中抽取统一字段。"""
    if dev is None:
        return None
    # 官方集成运行时的 MIoTDevice 对象
    if hasattr(dev, "did") and not isinstance(dev, dict):
        did = getattr(dev, "did", None)
        if did is None:
            return None
        return {
            "did": str(did),
            "name": getattr(dev, "name", None) or str(did),
            "model": getattr(dev, "model", None) or "",
            "online": bool(getattr(dev, "online", False)),
            "local_ip": "",
            "token": "",
        }
    # 落盘文件中的原始 dict
    if isinstance(dev, dict):
        did = dev.get("did") or dev.get("did_str")
        if did is None:
            return None
        return {
            "did": str(did),
            "name": dev.get("name") or str(did),
            "model": dev.get("model") or "",
            "online": bool(dev.get("online", False)),
            "local_ip": dev.get("local_ip") or "",
            "token": dev.get("token") or "",
        }
    return None


def _read_storage_file(path: str) -> dict[str, Any] | None:
    """读取官方集成自研存储文件（.dict 或 .json）。

    ``.dict`` 文件末尾追加了 32 字节 SHA256 校验值，必须先剥离再解析。
    """
    try:
        with open(path, "rb") as fh:
            raw = fh.read()
    except Exception as err:  # noqa: BLE001
        _LOGGER.debug("读取设备文件失败 %s: %s", path, err)
        return None
    # 先尝试直接解析；失败则剥离末尾 32 字节哈希后再试
    for candidate in (raw, raw[: -_MIOT_STORAGE_HASH_LEN]):
        try:
            data = json.loads(candidate.decode("utf-8"))
        except Exception:  # noqa: BLE001
            continue
        if isinstance(data, dict):
            return data
    _LOGGER.debug("设备文件解析失败（含/不含哈希均失败）：%s", path)
    return None


def _iter_device_files(hass: HomeAssistant):
    """遍历官方集成 storage 下的 miot_devices 设备文件（.dict / .json）。"""
    storage_root = hass.config.path(".storage")
    dirs = glob.glob(os.path.join(storage_root, "**", "miot_devices"), recursive=True)
    if not dirs:
        _LOGGER.warning(
            "mijia_camera: 在 %s 下未找到任何 miot_devices 目录，"
            "请确认已安装并登录官方 Xiaomi Home 集成且设备已同步",
            storage_root,
        )
        return
    for d in dirs:
        for path in glob.glob(os.path.join(d, "*.dict")) + glob.glob(
            os.path.join(d, "*.json")
        ):
            data = _read_storage_file(path)
            if isinstance(data, dict):
                yield data


def _collect_from_runtime(hass: HomeAssistant) -> list[dict[str, Any]]:
    """从官方集成内存数据 hass.data['xiaomi_home']['devices'] 读取设备。"""
    result: list[dict[str, Any]] = []
    xiaomi_data = hass.data.get("xiaomi_home")
    if not isinstance(xiaomi_data, dict):
        return result
    devices_map = xiaomi_data.get("devices")
    if devices_map is None:
        return result
    # 某些版本 devices 是 {entry_id: [MIoTDevice,...]}，也可能直接是列表
    if isinstance(devices_map, dict):
        candidates = list(devices_map.values())
    else:
        candidates = [devices_map]
    for item in candidates:
        if isinstance(item, dict):
            item = list(item.values())
        if not isinstance(item, (list, tuple)):
            continue
        for dev in item:
            info = _extract_device(dev)
            if info:
                result.append(info)
    return result


def _collect_from_files(hass: HomeAssistant) -> list[dict[str, Any]]:
    """从官方集成落盘文件读取设备（含 local_ip / token）。"""
    result: list[dict[str, Any]] = []
    for data in _iter_device_files(hass):
        for dev in data.values():
            info = _extract_device(dev)
            if info:
                result.append(info)
    return result


def load_all_devices(hass: HomeAssistant) -> dict[str, dict[str, Any]]:
    """合并运行时与文件来源的全部设备（以 did 为键，运行时优先）。"""
    devices: dict[str, dict[str, Any]] = {}
    # 先放文件来源（含 local_ip / token）
    for info in _collect_from_files(hass):
        devices[info["did"]] = info
    # 运行时来源覆盖（更实时），但保留文件里的 local_ip / token
    for info in _collect_from_runtime(hass):
        did = info["did"]
        existing = devices.get(did, {})
        merged = dict(existing)
        merged.update({k: v for k, v in info.items() if v not in (None, "")})
        merged["did"] = did
        devices[did] = merged
    return devices


def discover_cameras(hass: HomeAssistant) -> list[CameraInfo]:
    """筛选官方集成设备中的摄像头（以 did 去重）。"""
    try:
        runtime = _collect_from_runtime(hass)
        files = _collect_from_files(hass)
        devices = load_all_devices(hass)

        _LOGGER.warning(
            "mijia_camera: 设备发现 -> 运行时来源 %d 台，文件来源 %d 台，"
            "合并后 %d 台；models=%s",
            len(runtime),
            len(files),
            len(devices),
            sorted((str(d.get("model", "")) for d in devices.values())),
        )

        cameras: list[CameraInfo] = []
        seen: set[str] = set()
        for did, dev in devices.items():
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

        _LOGGER.warning(
            "mijia_camera: 识别到摄像头 %d 台：%s",
            len(cameras),
            [f"{c.name}({c.model})" for c in cameras],
        )
        return cameras
    except Exception as err:  # noqa: BLE001
        _LOGGER.warning(
            "mijia_camera: discover_cameras 执行异常（已兜底返回空列表）：%s", err
        )
        return []


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
