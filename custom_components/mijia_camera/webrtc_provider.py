"""WebRTC 提供器：把 HA 前端的 WebRTC 信令透明代理到 go2rtc。

为什么需要它：HA 摄像头实况默认走 HLS（stream 组件把 RTSP 转码），而 go2rtc
中继出来的 RTSP 在 HLS 下经常只出首帧就冻住。WebRTC 是浏览器与 go2rtc 的
直连，流畅不卡。本提供器把前端的 offer / candidate 转发给 go2rtc 的 WebSocket
信令接口，并把 go2rtc 的 answer / candidate 回传前端，从而让米家摄像头实况
走 WebRTC。

go2rtc 信令协议（已在 go2rtc 源码 internal/webrtc 中核对）：
- 连接：ws://<go2rtc主机>:1984/api/ws?src=<流名(DID)>
- 客户端发 offer：  {"type":"webrtc","value":{"type":"offer","sdp":"..."}}
- go2rtc 回 answer：{"type":"webrtc","value":{"type":"answer","sdp":"..."}}
- 候选（双向）：   {"type":"webrtc/candidate","value":"candidate:..."}
"""
from __future__ import annotations

import json
import logging
from typing import Any
from urllib.parse import quote, urlparse

import aiohttp

try:
    from homeassistant.components.camera.webrtc import (
        CameraWebRTCProvider,
        WebRTCAnswer,
        WebRTCCandidate,
        WebRTCSendMessage,
        async_register_webrtc_provider,
    )
    from webrtc_models import RTCIceCandidateInit

    WEBRTC_SUPPORTED = True
except Exception:  # noqa: BLE001  # 旧版本 HA 无此 API
    CameraWebRTCProvider = None  # type: ignore[assignment]
    WebRTCAnswer = None  # type: ignore[assignment]
    WebRTCCandidate = None  # type: ignore[assignment]
    WebRTCSendMessage = None  # type: ignore[assignment]
    async_register_webrtc_provider = None  # type: ignore[assignment]
    RTCIceCandidateInit = None  # type: ignore[assignment]
    WEBRTC_SUPPORTED = False

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

# go2rtc WebUI / WebSocket 默认端口（与 RTSP 的 8554 区分）
GO2RTC_WS_PORT = 1984


def _relay_rtsp_host(relay_host: str) -> str:
    """从 relay_host（可能含 rtsp:// 与端口）解析出 go2rtc 的 host:port(RTSP)。"""
    rh = (relay_host or "").strip()
    if not rh:
        return ""
    if "://" not in rh:
        rh = "rtsp://" + rh
    parsed = urlparse(rh)
    host = parsed.hostname
    if not host:
        return ""
    port = parsed.port or 8554
    return f"{host}:{port}"


def _ws_url(relay_host: str, did: str) -> str:
    """构造 go2rtc WebSocket 信令地址。"""
    rh = (relay_host or "").strip()
    if "://" not in rh:
        rh = "rtsp://" + rh
    parsed = urlparse(rh)
    host = parsed.hostname or "127.0.0.1"
    return f"ws://{host}:{GO2RTC_WS_PORT}/api/ws?src={quote(did, safe='')}"


class Go2rtcWebRTCProvider(CameraWebRTCProvider):
    """把 WebRTC 信令代理给 go2rtc 的中继提供器（仅对云端中继源生效）。"""

    def __init__(self, hass: HomeAssistant, relay_host: str) -> None:
        self._hass = hass
        self._relay_rtsp = _relay_rtsp_host(relay_host)
        self._sessions: dict[str, aiohttp.ClientWebSocketResponse] = {}

    @property
    def domain(self) -> str:
        return DOMAIN

    @callback
    def async_is_supported(self, stream_source: str) -> bool:
        # 只对指向 go2rtc 中继（RTSP 8554）的源启用 WebRTC；本地 RTSP 源交回 HLS。
        if not stream_source or not stream_source.startswith("rtsp://"):
            return False
        parsed = urlparse(stream_source)
        host = parsed.hostname
        port = parsed.port or 0
        if self._relay_rtsp:
            return f"{host}:{port}" == self._relay_rtsp
        # 未配置中继时按默认端口兜底
        return port == 8554

    async def async_handle_async_webrtc_offer(
        self,
        camera: Any,
        offer_sdp: str,
        session_id: str,
        send_message: WebRTCSendMessage,
    ) -> None:
        # 直接从实体取中继地址与 DID，避免依赖 stream_source 的 await 兼容性。
        relay_host = getattr(camera, "_relay_host", "") or ""
        did = getattr(camera, "_did", "") or ""
        if not did:
            _LOGGER.warning("mijia_camera: WebRTC offer 缺少 did，放弃代理")
            return
        ws_url = _ws_url(relay_host, did)
        # 异步执行代理；结果通过 send_message 回传，不阻塞本次 await。
        self._hass.async_create_task(
            self._proxy(ws_url, offer_sdp, session_id, send_message)
        )

    async def _proxy(
        self,
        ws_url: str,
        offer_sdp: str,
        session_id: str,
        send_message: WebRTCSendMessage,
    ) -> None:
        try:
            session = async_get_clientsession(self._hass)
            async with session.ws_connect(ws_url, timeout=10) as ws:
                self._sessions[session_id] = ws
                await ws.send_json(
                    {"type": "webrtc", "value": {"type": "offer", "sdp": offer_sdp}}
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
                        sdp = value.get("sdp") if isinstance(value, dict) else value
                        if sdp:
                            send_message(WebRTCAnswer(sdp))
                    elif mtype == "webrtc/candidate":
                        cand = value if isinstance(value, str) else ""
                        if cand and RTCIceCandidateInit is not None:
                            send_message(
                                WebRTCCandidate(RTCIceCandidateInit(candidate=cand))
                            )
                    elif mtype == "error":
                        _LOGGER.warning(
                            "mijia_camera: go2rtc 信令返回错误: %s", value
                        )
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning("mijia_camera: WebRTC 代理失败（go2rtc 不可达？）: %s", err)
        finally:
            self._sessions.pop(session_id, None)

    async def async_on_webrtc_candidate(
        self, session_id: str, candidate: Any
    ) -> None:
        ws = self._sessions.get(session_id)
        if ws is None or ws.closed:
            return
        cand_str = getattr(candidate, "candidate", None) or ""
        if not cand_str:
            return
        try:
            await ws.send_json(
                {"type": "webrtc/candidate", "value": cand_str}
            )
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("mijia_camera: 转发 ICE candidate 失败: %s", err)

    @callback
    def async_close_session(self, session_id: str) -> None:
        ws = self._sessions.pop(session_id, None)
        if ws is not None and not ws.closed:
            ws.close()


async def register_provider(hass: HomeAssistant, relay_host: str):
    """注册 go2rtc WebRTC 提供器，返回注销回调（注册失败返回 None）。"""
    if not WEBRTC_SUPPORTED or async_register_webrtc_provider is None:
        return None
    provider = Go2rtcWebRTCProvider(hass, relay_host)
    try:
        return async_register_webrtc_provider(hass, provider)
    except Exception as err:  # noqa: BLE001
        _LOGGER.warning("mijia_camera: 注册 WebRTC 提供器失败: %s", err)
        return None
