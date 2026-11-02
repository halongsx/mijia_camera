"""自动把小米账号下的摄像头注册成 go2rtc 流（按 DID 命名）。

前提：用户已在 go2rtc WebUI 用小米账号登录一次（全局 `xiaomi:` 登录态）。
本模块查询 go2rtc 的 API 拿到该账号的摄像头列表（每条已自带现成的
`xiaomi://...` 源 URL），再调用 `/api/streams` 把每台摄像头注册为名为 DID 的流。
这样用户完全不需要向 `go2rtc.yaml` / 加载项配置里手写 `streams:` 段。

工作原理（基于 go2rtc 源码 internal/xiaomi/xiaomi.go）：
  - GET  /api/xiaomi            -> 已登录的小米 userID 列表（tokens 配置的键）
  - GET  /api/xiaomi?id=<uid>   -> 该账号摄像头列表，每项含 ready-to-use 的
                                   `xiaomi://uid:region@ip?did=..&model=..` URL
  - POST /api/streams?src=<url>&name=<did>  -> 走 GetOrPatch 按 DID 创建流
  - PUT  /api/streams/<did>  body=url       -> WebUI「添加流」所用（后备）
"""
from __future__ import annotations

import logging
from urllib.parse import parse_qs, urlparse

import aiohttp
from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)

# 注册结果缓存（reload / 重启集成时模块被重新加载，自动清空）
_REGISTERED: set[str] = set()
_REGISTER_FAILED: set[str] = set()


def _extract_did(source_url: str) -> str | None:
    """从 xiaomi://...?did=xxx 源 URL 中取出 DID。"""
    try:
        query = parse_qs(urlparse(source_url).query)
        return query.get("did", [None])[0]
    except Exception:  # noqa: BLE001
        return None


def _api_base(relay_host: str) -> str:
    """由 relay_host（rtsp://host:8554）推导 go2rtc API 地址（http://host:1984）。"""
    host = relay_host.strip()
    if "://" in host:
        host = host.split("://", 1)[1]
    host = host.split(":")[0]  # 去掉 RTSP 端口 8554，改用 API 端口 1984
    return f"http://{host}:1984"


async def ensure_camera_streams(
    hass: HomeAssistant, relay_host: str, did: str | None = None
) -> bool:
    """确保 go2rtc 中存在名为 DID 的摄像头流（不存在则自动注册）。

    返回 True  表示流程正常执行（go2rtc 可达且至少已登录一个小米账号）；
    返回 False 表示不可达 / 未登录 / 注册失败——调用方应提示用户
    先去 go2rtc WebUI 用小米账号登录。
    """
    try:
        return await _ensure_camera_streams_inner(hass, relay_host, did)
    except Exception:  # noqa: BLE001
        # 兜底：任何意外异常都记录下来并返回 False，避免任务异常未被回收
        # 导致 HA 日志出现「Task exception was never retrieved」。
        _LOGGER.exception(
            "mijia_camera: 自动建流任务发生未预期异常（已捕获，不影响 HA 运行）"
        )
        return False


async def _ensure_camera_streams_inner(
    hass: HomeAssistant, relay_host: str, did: str | None = None
) -> bool:
    if did and did in _REGISTERED:
        return True

    if not relay_host:
        _LOGGER.warning(
            "mijia_camera: relay_host 为空，无法自动建流（HAOS 加载项请填 172.30.32.1:8554）"
        )
        return False

    base = _api_base(relay_host)
    # ⚠️ 关键：async_get_clientsession 必须传入 hass，否则会抛 TypeError。
    session = hass.helpers.aiohttp_client.async_get_clientsession(hass)

    # 1) 拿到已登录的小米账号 userID 列表
    try:
        async with session.get(
            f"{base}/api/xiaomi", timeout=aiohttp.ClientTimeout(total=10)
        ) as resp:
            if resp.status != 200:
                _LOGGER.warning(
                    "mijia_camera: go2rtc /api/xiaomi 返回 HTTP %s，无法自动建流"
                    "（go2rtc 未启动 / 地址不可达 / 需认证）",
                    resp.status,
                )
                return False
            user_ids = await resp.json(content_type=None)
    except Exception as err:  # noqa: BLE001
        _LOGGER.warning("mijia_camera: 连接 go2rtc API %s 失败：%s", base, err)
        return False

    if not isinstance(user_ids, list) or not user_ids:
        if not did or did not in _REGISTER_FAILED:
            _LOGGER.warning(
                "mijia_camera: go2rtc 未登录小米账号或返回异常，无法自动建流。"
                "请先在 go2rtc WebUI（http://<你的HAOS局域网IP>:1984）用小米账号登录一次，"
                "然后重启 Home Assistant（或重新加载本集成）。"
            )
        if did:
            _REGISTER_FAILED.add(did)
        return False

    # 2) 逐个账号拉取摄像头列表并注册
    for user_id in user_ids:
        try:
            async with session.get(
                f"{base}/api/xiaomi",
                params={"id": user_id},
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                if resp.status != 200:
                    _LOGGER.warning(
                        "mijia_camera: 获取账号 %s 的摄像头列表失败：HTTP %s",
                        user_id,
                        resp.status,
                    )
                    continue
                devices = await resp.json(content_type=None)
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning(
                "mijia_camera: 获取账号 %s 的摄像头列表异常：%s", user_id, err
            )
            continue

        # go2rtc 把设备列表包在 {"sources": [...]} 中返回
        # （见 internal/api/api.go 的 ResponseSources），必须先解包，
        # 否则会被当成「格式异常」整体跳过，导致一台流都注册不出来。
        if isinstance(devices, dict):
            devices = devices.get("sources") or []
        if not isinstance(devices, list):
            _LOGGER.warning(
                "mijia_camera: 账号 %s 的设备列表格式异常（非列表/非 sources 包裹）：%s",
                user_id,
                type(devices).__name__,
            )
            continue

        for dev in devices:
            if not isinstance(dev, dict):
                continue
            source_url = dev.get("url")
            if not source_url or not isinstance(source_url, str):
                continue
            dev_did = _extract_did(source_url)
            if not dev_did:
                continue
            if did and dev_did != did:
                continue
            if dev_did in _REGISTERED or dev_did in _REGISTER_FAILED:
                continue
            if await _register_one(session, base, dev_did, source_url):
                _REGISTERED.add(dev_did)
                _LOGGER.info(
                    "mijia_camera: 已自动在 go2rtc 注册摄像头流 %s", dev_did
                )
            # 注册失败不写入 _REGISTER_FAILED：下次取流（WebRTC offer）时
            # 会再次尝试，保证瞬时失败（如 go2rtc 启动未就绪）后能自愈。

    return True


async def _register_one(
    session: aiohttp.ClientSession, base: str, name: str, source_url: str
) -> bool:
    """把一个 xiaomi:// 源注册为名为 name 的 go2rtc 流。

    主方案：PUT /api/streams/<name>  body=源URL（go2rtc WebUI「添加流」所用）。
    后备方案：POST /api/streams?src=<url>&name=<name>（走 GetOrPatch 创建流）。
    """
    try:
        async with session.put(
            f"{base}/api/streams/{name}",
            data=source_url,
            timeout=aiohttp.ClientTimeout(total=15),
        ) as resp:
            if resp.status in (200, 201):
                return True
    except Exception as err:  # noqa: BLE001
        _LOGGER.debug("mijia_camera: PUT /api/streams/%s 失败：%s", name, err)

    try:
        async with session.post(
            f"{base}/api/streams",
            params={"src": source_url, "name": name},
            timeout=aiohttp.ClientTimeout(total=15),
        ) as resp:
            if resp.status in (200, 201):
                return True
            _LOGGER.warning(
                "mijia_camera: 注册流 %s 失败（POST 返回 HTTP %s）；"
                "若 go2rtc 配置了 api 认证，请在其配置中加入对 1984 端口的访问授权",
                name,
                resp.status,
            )
            return False
    except Exception as err:  # noqa: BLE001
        _LOGGER.warning("mijia_camera: 注册流 %s 异常：%s", name, err)
        return False
