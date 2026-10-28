# Mijia Camera · 米家摄像头 Home Assistant 集成

[![hacs_badge](https://img.shields.io/badge/HACS-自定义集成-41BDF5.svg)](https://github.com/hacs/integration)

非官方的 Home Assistant 米家摄像头集成。**复用官方「Xiaomi Home 米家集成」的登录态与设备列表**，自动发现账号下的摄像头并接入 Home Assistant，获得实时画面、录制与快照能力——本集成**不需要你输入米家账号密码**，也绕开了小米账号的登录风控。

> ⚠️ **免责声明**：本项目为非官方实现，读取的是官方集成已缓存到本地的设备信息。可能因官方接口调整而失效，作者不对可用性作保证。请勿用于任何商业或违规用途。

---

## 工作原理（重要）

本集成**不自己登录小米云**。它直接读取官方 Xiaomi Home 集成登录后缓存在 Home Assistant 本地的设备清单（`.storage/xiaomi_home/` 下的 `miot_devices_*.json`），从中筛选出摄像头，拿到所需的 `local_ip`、`token`、`online` 等字段来构造 RTSP 地址。

这样做的好处：
- ✅ 无需输入米家账号密码，彻底绕开小米登录风控 / 验证码 / 二次验证
- ✅ 无需 `micloud` 等任何额外依赖，也不处理小米云签名
- ✅ 设备在线状态由官方集成负责刷新，本集成只做本地读取

**前提**：你必须先在 HA 中安装并登录官方 **Xiaomi Home 米家集成**，且摄像头已同步到该集成。

---

## 功能特性

- 🔗 复用官方 Xiaomi Home 集成的登录态与设备列表（无需账号密码）
- 📷 为每个摄像头创建 `camera` 实体，支持主码流实时画面
- 🎬 对接 Home Assistant 原生 `stream` 组件，支持 WebRTC 实时预览、录制、回看
- 🌐 两种取流方式：
  - **本地 RTSP（推荐）**：低延迟、不依赖云；需在米家 APP 开启“局域网查看”
  - **云端中继**：对接 go2rtc / mediaMTX，适合无法开启局域网查看的机型
- 🔄 周期性刷新摄像头在线状态（读取官方集成缓存）
- 🗂 自动聚合官方集成下所有账号 / 地区的摄像头

---

## 安装（通过 HACS）

0. **（必须先做）** 在 HACS 安装并登录官方 **Xiaomi Home 米家集成**（搜索 `Xiaomi Home`），确保其下能看到你的摄像头
1. HACS → 右上角菜单 → **自定义仓库（Custom repositories）**
2. 仓库地址填：`https://github.com/halongsx/mijia_camera`
3. 类别选择：**Integration（集成）**
4. 找到 **Mijia Camera** → 下载 → 重启 Home Assistant
5. 配置 → 设备与服务 → 添加集成 → 搜索 **Mijia Camera**

> HACS 仅支持 GitHub 公开仓库。本项目的 GitHub 仓库作为 HACS 安装源，同时可镜像到 Gitee 作为国内备份。

---

## 配置

添加本集成后，按提示操作即可，**全程无需米家账号密码**：

1. 选择取流方式（见下）
2. 勾选要接入的摄像头（来自官方 Xiaomi Home 集成）
3. 若选本地 RTSP：在凭据步骤按每台摄像头填写米家 APP “局域网查看”页面显示的 RTSP 密码（验证码）

### 方式一：本地 RTSP（推荐，低延迟）

1. 在**米家 APP** 中进入摄像头 → 设置 → 打开 **“局域网查看”**
2. 开启后页面会显示该摄像头的 **RTSP 地址、用户名（admin）与密码（验证码）**
3. 在 HA 集成配置中：取流方式选择 **本地 RTSP**，勾选摄像头，按台填写 RTSP 密码
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
2. 在 HA 集成配置中：取流方式选择 **云端中继**，填写中继地址，例如 `192.168.1.10:8554`（go2rtc 默认 RTSP 端口）
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

- **未检测到官方集成（no_xiaomi_home）**：本集成依赖官方 Xiaomi Home 集成。请先在 HACS 安装并登录官方 Xiaomi Home 集成，确认其下能看到摄像头后，再添加本集成。
- **未发现摄像头（no_cameras）**：官方 Xiaomi Home 集成下没有模型包含 `camera` 的设备；确认摄像头已同步到官方集成，或尝试在官方集成中重新加载。
- **画面黑屏 / 无法加载**：确认摄像头与 HA 在同一局域网且“局域网查看”已开启；用 VLC 直接测试 RTSP 地址；检查防火墙是否放行 554 端口。
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
│  ├─ miot_client.py        # 读取官方 Xiaomi Home 集成的设备清单
│  ├─ camera.py
│  └─ translations/{en,zh-Hans}.json
└─ .github/workflows/{hassfest,hacs}.yaml
```

校验：提交会触发 `hassfest` 与 `hacs` Action 自动检查 manifest / 结构 / 翻译。

依赖：无第三方 Python 依赖（不再依赖 `micloud`）；运行时依赖已安装的官方 **Xiaomi Home 集成**提供设备列表。

---

## 许可证

[MIT](LICENSE)
