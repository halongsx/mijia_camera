"""配置流：从官方 Xiaomi Home 集成读取摄像头并选择要接入的设备。"""
from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.helpers import selector

from .const import (
    CONF_MODE,
    CONF_RELAY_HOST,
    CONF_DEVICES,
    CONF_CAMERA_CREDS,
    CONF_RTSG_USER,
    CONF_RTSG_PW,
    DEFAULT_RTSP_USER,
    MODE_LOCAL,
    MODE_RELAY,
    XIAOMI_HOME_DOMAIN,
    DOMAIN,
)
from .miot_client import discover_cameras

_LOGGER = logging.getLogger(__name__)


class MijiaCameraConfigFlow(ConfigFlow, domain=DOMAIN):
    """米家摄像头配置流。"""

    VERSION = 1

    def __init__(self) -> None:
        self._mode: str = MODE_LOCAL
        self._relay_host: str = ""
        self._cameras: list = []
        self._selected: list[str] = []
        self._creds: dict[str, Any] = {}

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            await self.async_set_unique_id(DOMAIN)
            self._abort_if_unique_id_configured()

            self._mode = user_input[CONF_MODE]
            self._relay_host = user_input.get(CONF_RELAY_HOST, "")

            cameras = await self.hass.async_add_executor_job(
                discover_cameras, self.hass
            )
            self._cameras = cameras
            if not cameras:
                # 区分“未安装 / 未登录官方集成”与“无摄像头”
                if not self.hass.config_entries.async_entries(XIAOMI_HOME_DOMAIN):
                    errors["base"] = "no_xiaomi_home"
                else:
                    errors["base"] = "no_cameras"
            else:
                return await self.async_step_select()

        schema = vol.Schema(
            {
                vol.Required(CONF_MODE, default=MODE_LOCAL): vol.In(
                    {
                        MODE_LOCAL: "本地RTSP（需开启局域网查看）",
                        MODE_RELAY: "云端中继（go2rtc/mediaMTX）",
                    }
                ),
                vol.Optional(
                    CONF_RELAY_HOST,
                    default="",
                    description=(
                        "HAOS 加载项版 go2rtc 请填 172.30.32.1:8554；"
                        "与 HA 同机 Docker（host 网络）填 127.0.0.1:8554。"
                        "仅「云端中继」模式需要，本地 RTSP 可留空。"
                    ),
                ): str,
            }
        )
        return self.async_show_form(
            step_id="user",
            data_schema=schema,
            errors=errors,
            description_placeholders={
                "note": (
                    "本集成复用官方「Xiaomi Home 米家集成」的登录态与设备列表，"
                    "无需输入米家账号密码。请先安装并登录官方 Xiaomi Home 集成，"
                    "并确保摄像头已同步到该集成。"
                )
            },
        )

    async def async_step_select(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            self._selected = user_input[CONF_DEVICES]
            if self._mode == MODE_LOCAL:
                return await self.async_step_credentials()
            return self._create_entry()

        options = [
            selector.SelectOptionDict(
                value=cam.did, label=f"{cam.name} ({cam.model or '未知型号'})"
            )
            for cam in self._cameras
        ]
        schema = vol.Schema(
            {
                vol.Required(CONF_DEVICES): selector.SelectSelector(
                    selector.SelectSelectorConfig(options=options, multiple=True)
                )
            }
        )

        if self._mode == MODE_RELAY:
            note = (
                "【云端中继模式】本集成通过 go2rtc 读取摄像机流，"
                "无需摄像机与 HA 在同一子网（适合摄像机跨网段 / 不在同一局域网的场景）。\n\n"
                "前置步骤：\n"
                "1) 安装并运行 go2rtc（HA 加载项 / Docker 均可）；\n"
                "2) 在 go2rtc 的 Web UI 用小米账号登录一次（支持账号密码 + 验证码），"
                "它会自动拿到小米云 service_token；\n"
                "   或在其配置中加入 `xiaomi:` 令牌块。\n"
                "3) 在 go2rtc 中为每台摄像机建立「流名 = 设备 DID」的流（见下方清单），"
                "本集成随后读取 rtsp://<go2rtc地址>/<DID>。\n\n"
                "各摄像机 DID / 型号（用于在 go2rtc 中建流，流名必须恰好等于 DID）：\n"
                + "\n".join(
                    f"• {c.name}  |  did={c.did}  |  model={c.model}"
                    for c in self._cameras
                )
            )
        else:
            note = "请勾选要接入的摄像头。"

        return self.async_show_form(
            step_id="select",
            data_schema=schema,
            description_placeholders={"note": note},
        )

    async def async_step_credentials(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            creds: dict[str, Any] = {}
            for did in self._selected:
                creds[did] = {
                    CONF_RTSG_USER: user_input.get(f"u_{did}", DEFAULT_RTSP_USER),
                    CONF_RTSG_PW: user_input.get(f"p_{did}", ""),
                }
            self._creds = creds
            return self._create_entry()

        schema_dict: dict[Any, Any] = {}
        for cam in self._cameras:
            if cam.did not in self._selected:
                continue
            schema_dict[vol.Required(f"u_{cam.did}", default=DEFAULT_RTSP_USER)] = str
            schema_dict[vol.Required(f"p_{cam.did}")] = str
        schema = vol.Schema(schema_dict)
        return self.async_show_form(
            step_id="credentials",
            data_schema=schema,
            description_placeholders={
                "note": (
                    "在米家 APP 中进入对应摄像头 → 设置 → 打开“局域网查看”，"
                    "页面会显示 RTSP 用户名与密码（验证码）。请按摄像头逐一填写。"
                )
            },
        )

    def _create_entry(self) -> ConfigFlowResult:
        data: dict[str, Any] = {
            CONF_MODE: self._mode,
            CONF_RELAY_HOST: self._relay_host,
            CONF_DEVICES: self._selected,
        }
        if self._mode == MODE_LOCAL:
            data[CONF_CAMERA_CREDS] = self._creds
        return self.async_create_entry(title="米家摄像头", data=data)
