# Mijia Camera

非官方的 Home Assistant 米家摄像头集成。复用官方「Xiaomi Home 米家集成」的登录态与设备列表，自动发现账号下的摄像头并接入 Home Assistant，支持实时画面、录制与快照。无需输入米家账号密码，绕开小米登录风控。

- **本地 RTSP 模式**：低延迟、不依赖云，需在米家 APP 开启“局域网查看”，且要求摄像机与 HA 在同一可路由局域网。
- **云端中继模式**：对接 go2rtc（需在其 Web UI 用小米账号登录一次），走小米云中继，**不受摄像机与 HA 是否同子网限制**，适合跨网段 / 无法开启局域网查看的场景。摄像头实体**原生实现 WebRTC**（HA 2024.12+），实况为浏览器 ↔ go2rtc 直连，流畅不卡帧；旧版 HLS 在 go2rtc 中继源上常只出首帧就冻住。WebRTC 信令连 go2rtc 的 `:1984`（RTSP 为 `:8554`），如该端口未暴露请告知。

> ⚠️ 本项目为非官方实现，读取官方集成已缓存到本地的设备信息，可能因官方调整而失效。云端中继所需的小米云 `service_token` 与官方集成的 OAuth2 token 不同，无法自动复用，仍需在 go2rtc 登录一次。

## 实况「一直转圈圈 / 连不上」的排查（v1.3.8+）

v1.3.8 起，信令链路任何失败都会在前端**直接弹出错误原因**（不再静默转圈）。先看错误文案，再对号入座：

1. **`go2rtc_unreachable` / 连接被拒**：HA 连不到 go2rtc 的 `:1984` 信令端口。
   - 最常见原因：`relay_host` 填成了 `127.0.0.1`，但 go2rtc 跑在**另一台机器**上。请把它改成那台机器的**局域网 IP:8554**，例如 `192.168.1.50:8554`。
   - 确认 go2rtc 已启动，`1984` 与 `8554` 端口可达。
2. **`no_answer`（20 秒内没收到 answer）**：go2rtc 连上了，但没给 answer。
   - 检查 go2rtc 是否已用**小米账号登录**（service_token 有效，未过期）。
   - 检查 go2rtc 的 `streams` 里，**流名必须恰好等于摄像头 DID**（集成日志里会打印 DID）。
3. **`go2rtc_error`**：go2rtc 主动返回了错误，文案里会带具体原因。
4. **信令成功、前端仍转圈（无报错）**：说明 offer/answer 已交换，但**浏览器 ↔ go2rtc 的媒体（UDP）通道没打通**——这是浏览器侧的 ICE 问题，HA 报错看不到。
   - 浏览器必须与 go2rtc 在**同一可访问的网络**。若你是通过外网/Home Assistant Cloud 访问 HA，浏览器连不到 go2rtc 的局域网 IP:媒体端口。
   - 解决办法：在 go2rtc 配置里设置 `candidates`（公网 IP）或配置 TURN 服务器；或改用与 go2rtc 同局域网的浏览器访问。

排查时打开 HA 的「设置 → 系统 → 日志」，过滤 `mijia_camera`，关注 `WebRTC 信令连接 go2rtc: ws://...` 这一行，确认实际连接的地址是否正确。

## 第一步（最关键）：在 go2rtc 里给摄像头「建流」

无论用 HA 前端还是 HomeKit 看，**所有黑屏 / 一帧一帧的根因都是同一个**：go2rtc 的 `streams` 里没有这个摄像头。本集成按 `rtsp://<host>/<DID>` 取流，**流名 key 必须恰好等于摄像头 DID**，否则 go2rtc 无流可给，谁都看不到画面。

### A. 获取摄像头的 `did` 和 `model`
- HA → 开发者工具 → 状态（States）→ 搜 `camera.`，找到对应摄像头 → 看属性里的 `did` 与 `model`（本集成已专门暴露）。
- 或浏览器打开 `http://<go2rtc地址>:1984/api/xiaomi`，返回 JSON 列表，含每台摄像头的 `did` / `model`。

### B. 在 go2rtc 配置里加入该流
- **HAOS go2rtc 加载项**：Supervisor（或「加载项」）→ go2rtc → **配置**标签页，在 YAML 里加：
  ```yaml
  xiaomi:
    # 用小米账号登录一次即可（见下），这里可留空让 go2rtc 自动写入；
    # 也可手动粘贴登录后得到的 userID + service_token
  streams:
    "<摄像头DID>": "xiaomi://<userID>:<region>@<网关IP>?did=<摄像头DID>&model=<摄像头型号>"
  ```
  - `<userID>` / `<service_token>`：在 go2rtc **Web UI**（`http://<HAOS_IP>:1984/`）用小米账号登录后会自动写入配置；`region` 国内填 `cn`。
  - `<网关IP>`：你家里小米网关 / 路由器的局域网 IP（米家设备通常经网关中继）。
  - 多台摄像头就每条加一行，key 各自是各自的 DID。
- **其他安装方式（Docker / 二进制）**：直接编辑 `go2rtc.yaml` 的 `streams:` 段，格式同上。

### C. 重启 go2rtc 并验证上游
- HAOS 加载项：保存配置 → **重启** go2rtc 加载项。
- 打开 `http://<go2rtc地址>:1984/`（go2rtc 自带 Web UI），左侧选该摄像头播放：
  - **能流畅播放** → go2rtc↔米家通了，HA / HomeKit 必然也通。
  - **还是黑屏** → 多半是 `model` 填错或小米 token 失效，看加载项日志。

## 通过 HomeKit 观看（走 HLS，不是 WebRTC）

HomeKit 摄像头实时画面**不走 WebRTC**，而是靠 HA 的 `stream` 组件把 `stream_source()` 返回的 RTSP 转码成 **HLS** 播放。因此前面针对 HA 前端 WebRTC 的修复（v1.3.7/v1.3.8）**对 HomeKit 观看路径无效**——这是两条不同的链路。

本集成的摄像头实体已声明 `CameraEntityFeature.STREAM`，`stream_source()` 也已返回云端中继 RTSP，代码侧无需改动。HomeKit 出现「一帧一帧 / 一张一张地刷新 / 不能实时」几乎都是 **HLS 实时流没建立成功，HomeKit 退回了「周期性快照」模式**，根因在环境配置：

1. **`relay_host` 填写规则（填错就黑屏，按 go2rtc 部署位置填）**：`stream` 组件是在 **HA 那一侧** 去连 `rtsp://<relay_host>/<DID>` 拉流的：
   - **go2rtc 是 HAOS 加载项（你目前的情况）** → 填 `172.30.32.1:8554`。加载项的 go2rtc 监听在 hassio 网络 `172.30.32.1`，这是正确值，不要填 `127.0.0.1`。
   - **go2rtc 与 HA 同机 Docker（host 网络）** → 填 `127.0.0.1:8554`。
   - **go2rtc 在另一台独立机器** → 填那台机器的**局域网 IP:8554**，例如 `192.168.1.50:8554`。
   - ⚠️ **不能留空**：relay_host 留空时本集成会回退到 `127.0.0.1:8554`，对 HAOS 加载项是**错误**的（HA 容器本机并没有 go2rtc）。HAOS 用户务必**显式**填 `172.30.32.1:8554`。
2. **go2rtc 所在机器的防火墙必须放行入站端口**：HA 在另一台机器时，防火墙会挡掉连接。需放行 **TCP 8554（RTSP 拉流，HomeKit 实时流靠它）**、**TCP 1984（WebUI/信令）**、**UDP 8555（WebRTC 媒体）**。可在防火墙把运行 go2rtc 的程序/容器放行，或新建入站规则放行端口。
3. **HA 必须启用 `stream` 集成**：`configuration.yaml` 有 `default_config:`（通常已含）或显式 `stream:`。没有它，HomeKit 永远只能快照。
4. **确认 go2rtc↔米家本身出流正常**：在跑 go2rtc 的机器本机用 VLC 打开 `rtsp://127.0.0.1:8554/<摄像头DID>`，能流畅播放说明上游没问题，问题只在前述地址/防火墙/stream。

改完 `relay_host` 与防火墙后**重启 Home Assistant**；iPhone 家庭 App 无需重新配对，实时流会自动走通，若仍卡可对该摄像头实体「重新加载」一次。

> 注：若地址与防火墙都正确、HomeKit 仍卡顿（而非完全不动），多为米家原始流**关键帧(GOP)间隔过长**导致 HLS 切片不连续。此时需在 go2rtc 中把该路流转码、强制短 GOP（联系维护者加 ffmpeg 重编码配置）。

