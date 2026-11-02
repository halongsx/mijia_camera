"""自动把小米账号下的摄像头注册成 go2rtc 流（按 DID 命名）。

前提：用户已在 go2rtc WebUI 用小米账号登录一次（全局 `xiaomi:` 登录态）。
本模块查询 go2rtc 的 API 拿到该账号的摄像头列表（每条已自带现成的
`xiaomi://...` 源 URL），再调用 `/api/streams` 把每台摄像头注册为名为 DID 的流。
这样用户完全不需要向 `go2rtc.yaml` / 加载项配置里手写 `streams:` 段。

工作原理（基于 go2rtc 源码核对，master 分支）：
  - GET /api/xiaomi          -> JSON 数组：已登录的小米 userID 列表（来自全局 xiaomi: 配置）
  - GET /api/xiaomi?id=<uid> -> {"sources":[{"name","info","url":"xiaomi://uid:region@ip?did=..&model=.."}]}
  - POST /api/streams?src=<url>&name=<did> -> 走 GetOrPatch 按 DID 创建流（主方案，契约明确）
  - PUT  /api/streams/<did>  body=url            -> 后备方案（与 WebUI「添加流」一致）
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from urllib.parse import parse_qs, urlparse

import aiohttp
from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)

# 注册结果缓存（reload / 重启集成时模块被重新加载，自动清空）
_REGISTERED: set[str] = set()


@dataclass
class StreamRegResult:
    """自动建流结果，供上层向前端给出可读诊断。"""

    ok: bool = False  # go2rtc 可达且至少登录了一个小米账号
    registered: bool = False  # 目标 did 已注册（本次新建或此前已存在）
    reason: str = ""  # 未注册时的可读原因（中文）


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
) -> StreamRegResult:
    """确保 go2rtc 中存在名为 DID 的摄像头流，不存在则自动注册。

    返回 StreamRegResult：ok 表示流程正常（可达且已登录），registered 表示
    目标 did 的流已存在/已建好，reason 在未注册时给出可读原因（中文）。
    """
    result = StreamRegResult()
    try:
        return await _ensure_inner(hass, relay_host, did, result)
    except Exception:  # noqa: BLE001
        # 兜底：任何意外异常都记录下来并返回失败原因，避免任务异常未被回收
        # 导致 HA 日志出现「Task exception was never retrieved」。
        _LOGGER.exception("mijia_camera: 自动建流发生未预期异常（已捕获）")
        result.ok = False
        result.reason = "自动建流任务发生未预期异常，详见 HA 日志（过滤 mijia_camera）。"
        return result


async def _ensure_inner(
    hass: HomeAssistant, relay_host: str, did: str | None, result: StreamRegResult
) -> StreamRegResult:
    if did and did in _REGISTERED:
        result.ok = True
        result.registered = True
        return result

    if not relay_host:
        result.reason = (
            "relay_host 为空，无法自动建流。HAOS 加载项请填 172.30.32.1:8554。"
        )
        return result

    base = _api_base(relay_host)
    # ⚠️ 关键：async_get_clientsession 必须传入 hass，否则会抛 TypeError。
    session = hass.helpers.aiohttp_client.async_get_clientsession(hass)

    # 1) 拿到已登录的小米账号 userID 列表
    try:
        async with session.get(
            f"{base}/api/xiaomi", timeout=aiohttp.ClientTimeout(total=10)
        ) as resp:
            if resp.status != 200:
                result.reason = (
                    f"go2rtc /api/xiaomi 返回 HTTP {resp.status}，无法自动建流"
                    f"（go2rtc 未启动 / 地址不可达 / 需 API 认证）。relay_host={relay_host}"
                )
                return result
            user_ids = await resp.json(content_type=None)
    except Exception as err:  # noqa: BLE001
        result.reason = (
            f"连接 go2rtc API {base} 失败：{err}。"
            f"请确认 go2rtc 已启动且 1984 端口可达（与 WebRTC 媒体同一地址）。"
        )
        return result

    if not isinstance(user_ids, list) or not user_ids:
        result.reason = (
            "go2rtc 未登录小米账号（/api/xiaomi 返回空列表）。请先到 go2rtc WebUI"
            "（http://<你的HAOS局域网IP>:1984）用小米账号登录一次，然后重启 Home Assistant。"
        )
        return result

    result.ok = True

    # 2) 逐个账号拉取摄像头列表并注册
    seen_dids: list[str] = []  # 收集 go2rtc 实际返回的设备 DID，便于排查不匹配
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
            seen_dids.append(dev_did)
            if did and dev_did != did:
                continue
            if dev_did in _REGISTERED:
                if dev_did == did:
                    result.registered = True
                continue
            if await _register_one(session, base, dev_did, source_url):
                _REGISTERED.add(dev_did)
                _LOGGER.info(
                    "mijia_camera: 已自动在 go2rtc 注册摄像头流 %s", dev_did
                )
                if dev_did == did:
                    result.registered = True
            elif dev_did == did:
                result.reason = (
                    f"已在 go2rtc 找到摄像头 DID {did} 的源，但注册流失败"
                    f"（见上方 mijia_camera 警告，通常为 go2rtc API 认证或网络问题）。"
                )

    # 指定了具体 did 但仍未注册成功：给出明确告警，避免静默失败。
    # 常见原因：集成侧 did 与 go2rtc 设备列表里的 did 不一致，或该摄像头
    # 未出现在 go2rtc 当前登录账号下的设备列表里。
    if did and not result.registered and not result.reason:
        result.reason = (
            f"go2rtc 设备列表中未匹配到目标 DID {did}"
            f"（go2rtc 返回 DID 共 {len(seen_dids)} 个：{seen_dids[:20]}）。"
            f"请对比相机实体 did 属性与 go2rtc WebUI 设备 DID 是否一致，"
            f"或确认 go2rtc 登录的账号包含该摄像头。"
        )

    if did and not result.registered:
        _LOGGER.warning("mijia_camera: %s", result.reason)

    return result


async def _register_one(
    session: aiohttp.ClientSession, base: str, name: str, source_url: str
) -> bool:
    """把一个 xiaomi:// 源注册为名为 name 的 go2rtc 流。

    主方案：POST /api/streams?src=<url>&name=<name>（走 GetOrPatch，契约明确）。
    后备方案：PUT /api/streams/<name>  body=源URL（与 WebUI「添加流」一致）。
    """
    # 主方案：POST 查询参数方式，避免请求体格式歧义
    try:
        async with session.post(
            f"{base}/api/streams",
            params={"src": source_url, "name": name},
            timeout=aiohttp.ClientTimeout(total=15),
        ) as resp:
            if resp.status in (200, 201):
                return True
            body = await resp.text()
            _LOGGER.warning(
                "mijia_camera: 注册流 %s 失败（POST HTTP %s）：%s",
                name,
                resp.status,
                body[:200],
            )
    except Exception as err:  # noqa: BLE001
        _LOGGER.debug("mijia_camera: POST /api/streams 失败：%s", err)

    # 后备：PUT /api/streams/<name> body=源URL
    try:
        async with session.put(
            f"{base}/api/streams/{name}",
            data=source_url,
            timeout=aiohttp.ClientTimeout(total=15),
        ) as resp:
            if resp.status in (200, 201):
                return True
            body = await resp.text()
            _LOGGER.warning(
                "mijia_camera: 注册流 %s 失败（PUT HTTP %s）：%s",
                name,
                resp.status,
                body[:200],
            )
    except Exception as err:  # noqa: BLE001
        _LOGGER.warning("mijia_camera: 注册流 %s 异常：%s", name, err)
    return False
