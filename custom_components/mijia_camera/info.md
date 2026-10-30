# Mijia Camera

非官方的 Home Assistant 米家摄像头集成。复用官方「Xiaomi Home 米家集成」的登录态与设备列表，自动发现账号下的摄像头并接入 Home Assistant，支持实时画面、录制与快照。无需输入米家账号密码，绕开小米登录风控。

- **本地 RTSP 模式**：低延迟、不依赖云，需在米家 APP 开启“局域网查看”，且要求摄像机与 HA 在同一可路由局域网。
- **云端中继模式**：对接 go2rtc（需在其 Web UI 用小米账号登录一次），走小米云中继，**不受摄像机与 HA 是否同子网限制**，适合跨网段 / 无法开启局域网查看的场景。摄像头实体**原生实现 WebRTC**（HA 2024.12+），实况为浏览器 ↔ go2rtc 直连，流畅不卡帧；旧版 HLS 在 go2rtc 中继源上常只出首帧就冻住。WebRTC 信令连 go2rtc 的 `:1984`（RTSP 为 `:8554`），如该端口未暴露请告知。

> ⚠️ 本项目为非官方实现，读取官方集成已缓存到本地的设备信息，可能因官方调整而失效。云端中继所需的小米云 `service_token` 与官方集成的 OAuth2 token 不同，无法自动复用，仍需在 go2rtc 登录一次。

## 实况「一直转圈圈 / 连不上」的排查（v1.3.8+）

v1.3.8 起，信令链路任何失败都会在前端**直接弹出错误原因**（不再静默转圈）。先看错误文案，再对号入座：

1. **`go2rtc_unreachable` / 连接被拒**：HA 连不到 go2rtc 的 `:1984` 信令端口。
   - 最常见原因：`relay_host` 填成了 `127.0.0.1`，但 go2rtc（mi-relay）跑在**另一台 Windows 机器**上。请把它改成那台机器的**局域网 IP:8554**，例如 `192.168.1.50:8554`。
   - 确认 go2rtc / mi-relay 已启动，`1984` 与 `8554` 端口可达。
2. **`no_answer`（20 秒内没收到 answer）**：go2rtc 连上了，但没给 answer。
   - 检查 go2rtc 是否已用**小米账号登录**（service_token 有效，未过期）。
   - 检查 go2rtc 的 `streams` 里，**流名必须恰好等于摄像头 DID**（集成日志里会打印 DID）。
3. **`go2rtc_error`**：go2rtc 主动返回了错误，文案里会带具体原因。
4. **信令成功、前端仍转圈（无报错）**：说明 offer/answer 已交换，但**浏览器 ↔ go2rtc 的媒体（UDP）通道没打通**——这是浏览器侧的 ICE 问题，HA 报错看不到。
   - 浏览器必须与 go2rtc 在**同一可访问的网络**。若你是通过外网/Home Assistant Cloud 访问 HA，浏览器连不到 go2rtc 的局域网 IP:媒体端口。
   - 解决办法：在 mi-relay 的 `go2rtc` 配置里设置 `candidates`（公网 IP）或配置 TURN 服务器；或改用与 go2rtc 同局域网的浏览器访问。

排查时打开 HA 的「设置 → 系统 → 日志」，过滤 `mijia_camera`，关注 `WebRTC 信令连接 go2rtc: ws://...` 这一行，确认实际连接的地址是否正确。

## 通过 HomeKit 观看（走 HLS，不是 WebRTC）

HomeKit 摄像头实时画面**不走 WebRTC**，而是靠 HA 的 `stream` 组件把 `stream_source()` 返回的 RTSP 转码成 **HLS** 播放。因此前面针对 HA 前端 WebRTC 的修复（v1.3.7/v1.3.8）**对 HomeKit 观看路径无效**——这是两条不同的链路。

本集成的摄像头实体已声明 `CameraEntityFeature.STREAM`，`stream_source()` 也已返回云端中继 RTSP，代码侧无需改动。HomeKit 出现「一帧一帧 / 一张一张地刷新 / 不能实时」几乎都是 **HLS 实时流没建立成功，HomeKit 退回了「周期性快照」模式**，根因在环境配置：

1. **`relay_host` 必须填 go2rtc(mi-relay) 所在机器的局域网 IP:8554，绝不能填 `127.0.0.1` 或 `172.30.32.1`**（除非 go2rtc 与 HA 确实同机）。因为 `stream` 组件是在 **HA 那一侧** 去连 `rtsp://<relay_host>/<DID>` 拉流的——填 `127.0.0.1` 会让 HA 去连自己本机而连不到 go2rtc。
   - 例：mi-relay 跑在某台 Windows 电脑（IP `192.168.1.50`），则填 `192.168.1.50:8554`。
2. **Windows 防火墙必须放行 mi-relay 的入站端口**：HA 在另一台机器时，防火墙会挡掉连接。需放行 **TCP 8554（RTSP 拉流，HomeKit 实时流靠它）**、**TCP 1984（WebUI/信令）**、**UDP 8555（WebRTC 媒体）**。可在「Windows 安全中心 → 防火墙 → 允许应用」把 `mi-relay.exe` 勾上，或新建入站规则放行端口。
3. **HA 必须启用 `stream` 集成**：`configuration.yaml` 有 `default_config:`（通常已含）或显式 `stream:`。没有它，HomeKit 永远只能快照。
4. **确认 go2rtc↔米家本身出流正常**：在跑 mi-relay 的 Windows 本机用 VLC 打开 `rtsp://127.0.0.1:8554/<摄像头DID>`，能流畅播放说明上游没问题，问题只在前述地址/防火墙/stream。

改完 `relay_host` 与防火墙后**重启 Home Assistant**；iPhone 家庭 App 无需重新配对，实时流会自动走通，若仍卡可对该摄像头实体「重新加载」一次。

> 注：若地址与防火墙都正确、HomeKit 仍卡顿（而非完全不动），多为米家原始流**关键帧(GOP)间隔过长**导致 HLS 切片不连续。此时需在 mi-relay 的 go2rtc 中把该路流转码、强制短 GOP（联系维护者加 ffmpeg 重编码配置）。

