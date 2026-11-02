"""自动把小米账号下的摄像头注册成 go2rtc 流（按 DID 命名）。

前提：用户已在 go2rtc WebUI 用小米账号登录一次（全局 `xiaomi:` 登录态）。
本模块查询 go2rtc 的 API 拿到该账号的摄像头列表（每条已自带现成的
`xiaomi://...` 源 URL），再调用 `/api/streams` 把每台摄像头注册为名为 DID 的流。
这样用户完全不需要向 `go2rtc.yaml` / 加载项配置里手写 `streams:` 段。

工作原理（基于 go2rtc 源码）：
  - GET  /api/xiaomi            -> 已登录的小米 userID 列表
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

    查询 go2rtc 已登录的小米账号，拉取摄像头列表并把匹配（或全部）摄像头
    注册为 go2rtc 流（流名 = DID）。

    返回 True  表示 go2rtc 可达且至少已登录一个小米账号；
    返回 False 表示不可达或未登录——此时无法自动建流，调用方应提示用户
    先去 go2rtc WebUI 用小米账号登录。
    """
    if did and did in _REGISTERED:
        return True

    base = _api_base(relay_host)
    session = hass.helpers.aiohttp_client.async_get_clientsession()

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
            user_ids = await resp.json()
    except Exception as err:  # noqa: BLE001
        _LOGGER.warning("mijia_camera: 连接 go2rtc API %s 失败：%s", base, err)
        return False

    if not user_ids:
        if not did or did not in _REGISTER_FAILED:
            _LOGGER.warning(
                "mijia_camera: go2rtc 未登录小米账号，无法自动建流。"
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
                devices = await resp.json()
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning(
                "mijia_camera: 获取账号 %s 的摄像头列表异常：%s", user_id, err
            )
            continue

        for dev in devices:
            source_url = dev.get("url")
            if not source_url:
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
            else:
                _REGISTER_FAILED.add(dev_did)

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
                "mijia_camera: 注册流 %s 失败（PUT 与 POST 均非 2xx，POST=HTTP %s）",
                name,
                resp.status,
            )
            return False
    except Exception as err:  # noqa: BLE001
        _LOGGER.warning("mijia_camera: 注册流 %s 异常：%s", name, err)
        return False
