# Mijia Camera · 米家摄像头 Home Assistant 集成

[![hacs_badge](https://img.shields.io/badge/HACS-自定义集成-41BDF5.svg)](https://github.com/hacs/integration)

非官方的 Home Assistant 米家摄像头集成。登录米家账号后自动发现账号下的摄像头，并接入 Home Assistant，获得实时画面、录制与快照能力。

> ⚠️ **免责声明**：本项目为非官方实现，使用小米公开云服务接口。可能因官方接口调整而失效，作者不对可用性作保证。请勿用于任何商业或违规用途。

---

## 功能特性

- 🔐 米家账号登录（用户名/密码），自动发现账号下所有摄像头
- 📷 为每个摄像头创建 `camera` 实体，支持主码流实时画面
- 🎬 对接 Home Assistant 原生 `stream` 组件，支持 WebRTC 实时预览、录制、回看
- 🌐 两种取流方式：
  - **本地 RTSP（推荐）**：低延迟、不依赖云；需在米家 APP 开启“局域网查看”
  - **云端中继**：对接 go2rtc / mediaMTX，仅需登录即可取流，适合无法开启局域网查看的机型
- 🔄 周期性刷新摄像头在线状态
- 🗂 支持多账号（可多次添加不同米家账号）

---

## 安装（通过 HACS）

1. 在 HACS → 右上角菜单 → **自定义仓库（Custom repositories）**
2. 仓库地址填：`https://github.com/halongsx/mijia_camera`
3. 类别选择：**Integration（集成）**
4. 找到 **Mijia Camera** → 下载 → 重启 Home Assistant
5. 配置 → 设备与服务 → 添加集成 → 搜索 **Mijia Camera**

> HACS 仅支持 GitHub 公开仓库。本项目的 GitHub 仓库作为 HACS 安装源，同时可镜像到 Gitee 作为国内备份。

---

## 配置

### 方式一：本地 RTSP（推荐，低延迟）

1. 在**米家 APP** 中进入摄像头 → 设置 → 打开 **“局域网查看”**
2. 开启后页面会显示该摄像头的 **RTSP 地址、用户名（admin）与密码（验证码）**
3. 在 HA 集成配置中：
   - 取流方式选择 **本地 RTSP**
   - 登录米家账号后，勾选要添加的摄像头
   - 在凭据步骤中，按每台摄像头填写 APP 显示的 RTSP 密码（验证码）
4. 完成后即可在 HA 中看到摄像头实时画面

不同机型 RTSP 路径参考：

| 机型 | 主码流路径 | 子码流路径 |
|---|---|---|
| Aqara G2H / G3 | `live/ch00_0` | `live/ch00_1` |
| 小米 / 创米摄像机 | `ch0_0.h264` | `ch0_1.h264` |

> 若画面无法显示，请在摄像头实体属性中查看 `local_ip` 与生成的 RTSP 地址，用 VLC 直接打开 `rtsp://admin:验证码@IP:554/路径` 验证网络与凭据。

### 方式二：云端中继（go2rtc / mediaMTX）

适用于无法开启局域网查看、或希望走云端取流的场景。

1. 在 HA 主机或局域网内部署 **go2rtc**（或 mediaMTX），并为其配置米家账号与摄像头（go2rtc 原生支持 Xiaomi 账号源）
2. 在 HA 集成配置中：
   - 取流方式选择 **云端中继**
   - 填写中继地址，例如 `192.168.1.10:8554`（go2rtc 默认 RTSP 端口）
3. 集成会以摄像头 `DID` 作为流名，生成形如 `rtsp://192.168.1.10:8554/<DID>` 的地址交给 HA

> 云端中继依赖外部中继服务与小米云，延迟高于本地 RTSP，且受官方接口稳定性影响。

---

## 机型兼容性

| 类型 | 支持情况 |
|---|---|
| 小米 / Redmi 智能摄像机（支持局域网查看） | ✅ 本地 RTSP |
| Aqara 摄像头（G2H / G3 等，原生 RTSP） | ✅ 本地 RTSP |
| 小米 / Redmi 摄像机（无法开启局域网查看） | ✅ 云端中继 |
| 早期 Xiaofang / Dafang（默认无 RTSP） | 需刷第三方固件后使用本地 RTSP |

---

## 故障排查

- **登录失败（invalid_auth）**：检查账号/密码；小米可能触发风控，尝试在米家 APP 解除风控或更换网络后重试。
- **未发现摄像头（no_cameras）**：确认账号下确实有摄像机类设备；部分蓝牙/虚拟设备不被识别。
- **画面黑屏/无法加载**：先确认摄像头与 HA 在同一局域网且“局域网查看”已开启；用 VLC 直接测试 RTSP 地址；检查防火墙是否放行 554 端口。
- **凭据提示**：本地 RTSP 模式的密码为米家 APP “局域网查看”页面显示的**验证码**，不是米家账号密码。

---

## 开发 / 贡献

仓库结构遵循 HACS 自定义集成规范：

```
.
├─ hacs.json
├─ README.md
├─ custom_components/mijia_camera/
│  ├─ __init__.py
│  ├─ manifest.json
│  ├─ const.py
│  ├─ config_flow.py
│  ├─ coordinator.py
│  ├─ miot_client.py
│  ├─ camera.py
│  └─ translations/{en,zh-Hans}.json
└─ .github/workflows/{hassfest,hacs}.yaml
```

校验：提交会触发 `hassfest` 与 `hacs` Action 自动检查 manifest / 结构 / 翻译。

依赖：`micloud`（PyPI，用于米家云登录与设备发现）。

---

## 许可证

[MIT](LICENSE)
