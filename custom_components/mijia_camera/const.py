"""常量定义。"""
from __future__ import annotations

DOMAIN = "mijia_camera"

# 官方 Xiaomi Home 集成（提供登录态与设备列表）
XIAOMI_HOME_DOMAIN = "xiaomi_home"
XIAOMI_HOME_STORAGE_DIR = ".storage/xiaomi_home"

# 配置键
CONF_MODE = "mode"
CONF_RELAY_HOST = "relay_host"
CONF_DEVICES = "devices"
CONF_CAMERA_CREDS = "camera_creds"
CONF_RTSG_USER = "user"
CONF_RTSG_PW = "password"

# 接入模式
MODE_LOCAL = "local"
MODE_RELAY = "relay"

# 默认 RTSP 参数
DEFAULT_RTSP_PORT = 554
DEFAULT_RTSP_USER = "admin"

# 子码流路径（主码流 -> 子码流）
SUB_STREAM_SUFFIX_MAP = {
    "live/ch00_0": "live/ch00_1",
    "ch0_0.h264": "ch0_1.h264",
}
DEFAULT_RTSP_PATH = "ch0_0.h264"

# 日志
LOGGER_NAME = "custom_components.mijia_camera"
