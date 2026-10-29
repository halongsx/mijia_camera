"""go2rtc WebRTC 信令代理（与实体解耦，便于复用与测试）。

为什么需要它：HA 摄像头实况默认走 HLS（stream 组件把 RTSP 转码），而 go2rtc
中继出来的 RTSP 在 HLS 下经常只出首帧就冻住。WebRTC 是浏览器与 go2rtc 的
直连，流畅不卡。本模块把前端的 offer / ICE candidate 透明转发到 go2rtc 的
WebSocket 信令（ws://<host>:1984/api/ws?src=<DID>），并把 go2rtc 的 answer /
candidate 回传前端，从而让米家摄像头实况走 WebRTC。

go2rtc 信令协议（已逐行对照 go2rtc 源码 internal/webrtc、internal/api/ws 核对）：
- 客户端发 offer：  {"type":"webrtc","value":{"type":"offer","sdp":"..."}}
- go2rtc 回 answer：{"type":"webrtc","value":{"type":"answer","sdp":"..."}}
- 候选（双向）：    {"type":"webrtc/candidate","value":"candidate:..."} （value 为字符串）

关键修复（v1.3.8）：信令链路上任何失败都必须通过 send_message(WebRTCError(...))
回传给前端，否则浏览器永远收不到 answer 也收不到 error，表现为「永久转圈」。
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any
from urllib.parse import quote, urlparse

import aiohttp
from homeassistant.helpers.aiohttp_client import async_get_clientsession

try:
    from homeassistant.components.camera.webrtc import (
        WebRTCAnswer,
        WebRTCCandidate,
        WebRTCError,
        WebRTCSendMessage,
    )
    from webrtc_models import RTCIceCandidateInit

    WEBRTC_SUPPORTED = True
except Exception:  # noqa: BLE001  # 旧版本 HA 无此 API
    WebRTCAnswer = None  # type: ignore[assignment]
    WebRTCCandidate = None  # type: ignore[assignment]
    WebRTCError = None  # type: ignore[assignment]
    WebRTCSendMessage = None  # type: ignore[assignment]
    RTCIceCandidateInit = None  # type: ignore[assignment]
    WEBRTC_SUPPORTED = False

_LOGGER = logging.getLogger(__name__)

# go2rtc WebUI / WebSocket 默认端口（与 RTSP 的 8554 区分）
WS_PORT = 1984

# 超过此秒数仍未收到 go2rtc 的 answer，即判定信令失败（流名不符 / 米家未登录 /
# go2rtc 未启动等），向前端报错并关闭会话，避免无限转圈。
ANSWER_TIMEOUT = 20


def parse_host(relay_host: str) -> str:
    """从 relay_host（rtsp://host:8554 或 host:8554）提取 host。"""
    rh = (relay_host or "").strip()
    if not rh:
        return "127.0.0.1"
    if "://" not in rh:
        rh = "rtsp://" + rh
    return urlparse(rh).hostname or "127.0.0.1"


def ws_url(relay_host: str, did: str) -> str:
    """构造 go2rtc WebSocket 信令地址。"""
    return f"ws://{parse_host(relay_host)}:{WS_PORT}/api/ws?src={quote(did, safe='')}"


class Go2rtcSignaling:
    """单个摄像头的 WebRTC 信令会话管理（go2rtc 一侧）。"""

    def __init__(self, hass: Any, relay_host: str) -> None:
        self._hass = hass
        self._relay_host = relay_host
        self._sessions: dict[str, aiohttp.ClientWebSocketResponse | None] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    @staticmethod
    def _normalize_candidate(value: Any) -> str:
        """把 go2rtc 回传的 candidate 归一为字符串。

        go2rtc 通常回传字符串 "candidate:..."；个别版本/分支可能回传
        {"candidate":"...","sdpMid":..,"sdpMLineIndex":..} 字典，这里都兼容。
        """
        if isinstance(value, str):
            return value
        if isinstance(value, dict):
            return value.get("candidate") or ""
        return ""

    def _safe_send(self, send_message: WebRTCSendMessage, message: Any) -> None:
        """安全回传消息，避免连接已断开时抛异常把信令循环打断。"""
        try:
            send_message(message)
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("mijia_camera: 向前端回传 WebRTC 消息失败: %s", err)

    async def handle_offer(
        self,
        did: str,
        offer_sdp: str,
        session_id: str,
        send_message: WebRTCSendMessage,
    ) -> None:
        """与 go2rtc 建立 WS、发 offer、把 answer/candidate 回传前端。

        任何失败都通过 WebRTCError 回传前端，杜绝「永久转圈」式的静默失败。
        """
        url = ws_url(self._relay_host, did)
        _LOGGER.info("mijia_camera: WebRTC 信令连接 go2rtc: %s", url)

        got_answer = asyncio.Event()
        lock = asyncio.Lock()
        self._sessions[session_id] = None  # 占位，连接成功后替换为真实 ws
        self._locks[session_id] = lock

        watchdog: asyncio.Task | None = None
        try:
            session = async_get_clientsession(self._hass)
            async with session.ws_connect(url, timeout=10) as ws:
                self._sessions[session_id] = ws
                await ws.send_json(
                    {"type": "webrtc", "value": {"type": "offer", "sdp": offer_sdp}}
                )

                # 看门狗：超时未收到 answer 即判定失败并关闭会话。
                watchdog = self._hass.loop.create_task(
                    self._watchdog(session_id, send_message, got_answer, url, did)
                )

                async for msg in ws:
                    if msg.type != aiohttp.WSMsgType.TEXT:
                        if msg.type in (
                            aiohttp.WSMsgType.CLOSED,
                            aiohttp.WSMsgType.ERROR,
                        ):
                            break
                        continue
                    try:
                        payload = json.loads(msg.data)
                    except Exception:  # noqa: BLE001
                        continue
                    mtype = payload.get("type")
                    value = payload.get("value")
                    if mtype == "webrtc":
                        sdp = (
                            value.get("sdp")
                            if isinstance(value, dict)
                            else value
                        )
                        if sdp:
                            got_answer.set()
                            _LOGGER.debug(
                                "mijia_camera: 收到 go2rtc answer，回传前端"
                            )
                            async with lock:
                                self._safe_send(
                                    send_message, WebRTCAnswer(answer=sdp)
                                )
                    elif mtype == "webrtc/candidate":
                        cand = self._normalize_candidate(value)
                        if cand and RTCIceCandidateInit is not None:
                            async with lock:
                                self._safe_send(
                                    send_message,
                                    WebRTCCandidate(
                                        RTCIceCandidateInit(candidate=cand)
                                    ),
                                )
                    elif mtype == "error":
                        _LOGGER.warning(
                            "mijia_camera: go2rtc 信令返回错误: %s", value
                        )
                        async with lock:
                            self._safe_send(
                                send_message,
                                WebRTCError(
                                    code="go2rtc_error",
                                    message=(
                                        f"go2rtc 返回错误：{value}。请检查 go2rtc "
                                        f"是否已用小米账号登录、以及 streams 中流名"
                                        f"是否恰好等于摄像头 DID（{did}）。"
                                    ),
                                ),
                            )
        except aiohttp.ClientError as err:
            _LOGGER.warning(
                "mijia_camera: 无法连接 go2rtc 信令 (%s): %s", url, err
            )
            self._safe_send(
                send_message,
                WebRTCError(
                    code="go2rtc_unreachable",
                    message=(
                        f"无法连接 go2rtc 信令服务 {url}：{err}。请确认："
                        f"① go2rtc 已启动且 1984 端口可达；"
                        f"② relay_host 填写的是「运行 go2rtc 的机器」地址"
                        f"（mi-relay 在另一台 Windows 上时填那台机器的局域网 IP，"
                        f"不要填 127.0.0.1，除非 go2rtc 与 HA 同机）。"
                    ),
                ),
            )
        except asyncio.TimeoutError:
            _LOGGER.warning("mijia_camera: 连接 go2rtc 信令超时: %s", url)
            self._safe_send(
                send_message,
                WebRTCError(
                    code="go2rtc_timeout",
                    message=f"连接 go2rtc 信令超时：{url}。",
                ),
            )
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning("mijia_camera: WebRTC 代理失败: %s", err)
            self._safe_send(
                send_message,
                WebRTCError(
                    code="webrtc_proxy_failed",
                    message=f"WebRTC 代理失败：{err}。",
                ),
            )
        finally:
            if watchdog is not None:
                watchdog.cancel()
            self._sessions.pop(session_id, None)
            self._locks.pop(session_id, None)

    async def _watchdog(
        self,
        session_id: str,
        send_message: WebRTCSendMessage,
        got_answer: asyncio.Event,
        url: str,
        did: str,
    ) -> None:
        """answer 看门狗：超时未收到 answer 则向前端报错并关闭 WS。"""
        try:
            await asyncio.wait_for(got_answer.wait(), timeout=ANSWER_TIMEOUT)
        except asyncio.TimeoutError:
            _LOGGER.warning(
                "mijia_camera: %s 秒内未收到 go2rtc answer（流名 %s 可能未配置/"
                "米家未登录 / go2rtc 未启动）: %s",
                ANSWER_TIMEOUT,
                did,
                url,
            )
            self._safe_send(
                send_message,
                WebRTCError(
                    code="no_answer",
                    message=(
                        f"{ANSWER_TIMEOUT} 秒内未收到 go2rtc 的 answer。请检查："
                        f"① go2rtc 中是否已用小米账号登录（service_token 有效）；"
                        f"② go2rtc 的 streams 里流名是否恰好等于摄像头 DID（{did}）；"
                        f"③ relay_host 是否指向运行 go2rtc 的机器；"
                        f"④ 浏览器能否访问 HA 同一局域网内 go2rtc 的媒体端口"
                        f"（远程访问 HA 时需给 go2rtc 配置公网候选/TURN）。"
                    ),
                ),
            )
            ws = self._sessions.get(session_id)
            if ws is not None and not ws.closed:
                ws.close()

    async def on_candidate(self, session_id: str, candidate_str: str) -> None:
        """把前端 ICE candidate 转发给 go2rtc。"""
        ws = self._sessions.get(session_id)
        lock = self._locks.get(session_id)
        if ws is None or ws.closed or not candidate_str:
            return
        try:
            async with lock:
                await ws.send_json(
                    {"type": "webrtc/candidate", "value": candidate_str}
                )
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("mijia_camera: 转发 ICE candidate 失败: %s", err)

    def close(self, session_id: str) -> None:
        """关闭某个 WebRTC 会话的 WS。"""
        ws = self._sessions.pop(session_id, None)
        if ws is not None and not ws.closed:
            ws.close()
