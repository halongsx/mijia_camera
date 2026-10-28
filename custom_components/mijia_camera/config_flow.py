"""配置流：登录米家账号并选择要接入的摄像头。"""
from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.helpers import selector

from .const import (
    CONF_PASSWORD,
    CONF_USERNAME,
    CONF_COUNTRY,
    CONF_MODE,
    CONF_RELAY_HOST,
    CONF_DEVICES,
    CONF_CAMERA_CREDS,
    CONF_RTSG_USER,
    CONF_RTSG_PW,
    DEFAULT_COUNTRY,
    DEFAULT_RTSP_USER,
    MODE_LOCAL,
    MODE_RELAY,
    DOMAIN,
)
from .miot_client import MijiaClient

_LOGGER = logging.getLogger(__name__)


class MijiaCameraConfigFlow(ConfigFlow, domain=DOMAIN):
    """米家摄像头配置流。"""

    VERSION = 1

    def __init__(self) -> None:
        self._username: str = ""
        self._password: str = ""
        self._country: str = DEFAULT_COUNTRY
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
            self._username = user_input[CONF_USERNAME]
            self._password = user_input[CONF_PASSWORD]
            self._country = user_input.get(CONF_COUNTRY, DEFAULT_COUNTRY)
            self._mode = user_input[CONF_MODE]
            self._relay_host = user_input.get(CONF_RELAY_HOST, "")

            # 同一账号只允许配置一次
            await self.async_set_unique_id(self._username)
            self._abort_if_unique_id_configured()

            client = MijiaClient(self._username, self._password, self._country)
            ok = await self.hass.async_add_executor_job(client.login)
            if not ok:
                errors["base"] = "invalid_auth"
            else:
                cameras = await self.hass.async_add_executor_job(client.get_cameras)
                self._cameras = cameras
                if not cameras:
                    errors["base"] = "no_cameras"
                else:
                    return await self.async_step_select()

        schema = vol.Schema(
            {
                vol.Required(CONF_USERNAME): str,
                vol.Required(CONF_PASSWORD): str,
                vol.Optional(
                    CONF_COUNTRY, default=DEFAULT_COUNTRY
                ): vol.In({"cn": "中国", "sg": "新加坡/全球", "de": "欧洲"}),
                vol.Required(CONF_MODE, default=MODE_LOCAL): vol.In(
                    {
                        MODE_LOCAL: "本地RTSP（需开启局域网查看）",
                        MODE_RELAY: "云端中继（go2rtc/mediaMTX）",
                    }
                ),
                vol.Optional(CONF_RELAY_HOST, default=""): str,
            }
        )
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)

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
        return self.async_show_form(step_id="select", data_schema=schema)

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
            CONF_USERNAME: self._username,
            CONF_PASSWORD: self._password,
            CONF_COUNTRY: self._country,
            CONF_MODE: self._mode,
            CONF_RELAY_HOST: self._relay_host,
            CONF_DEVICES: self._selected,
        }
        if self._mode == MODE_LOCAL:
            data[CONF_CAMERA_CREDS] = self._creds
        return self.async_create_entry(title=self._username, data=data)
