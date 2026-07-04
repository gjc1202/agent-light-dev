# 架构

## 组件总览

```
agent-light-dev/
├── firmware/                    # ESP32-C3 固件（PlatformIO / Arduino）
│   └── esp32_c3_traffic_light.ino
├── web-dashboard/               # Mac 上的状态聚合服务（Node.js）
│   ├── server.js                # HTTP 服务（接收 hook、聚合状态、SSE 广播）
│   ├── hook-queue-worker.js     # 本地 hook 队列消费者（Node 版，macOS 用）
│   ├── hook-queue-worker.py     # 同上（Python 版，Linux 用）
│   ├── static/                  # 前端 dashboard
│   └── data/config.json         # 灯效配置
├── bridges/                     # 状态 → 硬件的桥接
│   ├── ble_status_bridge.py     # BLE 桥接（推荐，走显示器 USB hub 也稳）
│   └── codex_status_bridge.py   # USB 串口桥接（备用）
├── hooks/                       # 各 AI Agent 的 hook 脚本
│   ├── cursor/
│   │   ├── cursor-hook-fast.sh  # Mac 本地 Cursor hook（写队列 + 标 source=local）
│   │   ├── cursor-hook-remote.sh# Linux 远程 Cursor hook（标 source=ssh）
│   │   ├── cursor-hook-adapter.js  # 旧版 Node 适配器（保留兼容）
│   │   └── cursor-hooks-remote.json # Linux 上 hooks.json 模板
│   ├── codex/                   # Codex hooks
│   └── claude/                  # Claude Code hooks
├── scripts/                     # 一键脚本
│   ├── setup-mac.sh             # 装环境
│   ├── start-mac.sh             # 启动系统
│   ├── start-bridge-mac.sh      # 只启 BLE 桥
│   ├── doctor.sh                # 自检
│   ├── autostart-install.sh     # 配置开机自启
│   ├── install-hooks.js         # 装 Cursor/Codex/Claude hooks
│   └── install-remote.sh        # 在 Linux 上一键装 hooks + worker
├── utils/
│   ├── mac/detect_serial_port.py
│   └── agent_light_control.py   # 命令行手动控灯
├── tests/
│   ├── test-multi-agent-switch.py    # 多窗口切换压力测试
│   └── test-green-light-stall.py     # 绿灯常亮边界 case 复现
├── docs/                        # 文档
└── .cursor/rules/               # Agent 治理规则
```

## 数据流

### 本地 Cursor 触发灯变化

```
1. Cursor 发 hook event（preToolUse 等）
2. ~/.cursor/hooks/agentcore-light.sh 把 payload 写到 ~/.cursor/hooks/queue/<timestamp>.json
3. hook-queue-worker.js 读队列，POST 到 http://127.0.0.1:8787/hook?agent=cursor
4. server.js 的 handleHook：
   - 解析 event / sid / source
   - SessionStore.set(sid, event, cwd, agent, source)
     · 清掉所有旧 success（Stop）
     · 清掉超期的僵尸 PreToolUse/PostToolUse/UserPromptSubmit
   - broadcast() 把新状态推给所有 SSE 客户端
5. statusPayload()：
   - 过滤 stale 会话
   - 按 DEVICE_STATUS_PRIORITY 选 winner
6. BLE 桥接每 0.5s 拉 /api/status，map_status_to_command() 算出灯命令
7. 通过 BLE 写到 ESP32，固件切换 state
```

### SSH 远程 Cursor

```
1. Linux 上 Cursor 发 hook event
2. Linux ~/.cursor/hooks/agentcore-light.sh 写队列（标 source=ssh）
3. Linux 上 hook-queue-worker.py（systemd user 服务）读队列
4. POST 到 http://127.0.0.1:18787/hook?agent=cursor
5. SSH 反向隧道（RemoteForward）把 18787 转到 Mac 的 8787
6-7. 同上
```

## 关键设计决策

### 1. Hook 用「写队列 + 异步消费」，不是同步 POST

Cursor 对 hook 执行时间敏感（超过 5s 就 `canceled by signal abort`）。直接 curl 偶尔会被取消，造成事件丢失。

**解法**：hook 脚本只做一件事——`cat > queue/<ts>.json`，几毫秒就退出。后台 worker 异步消费队列，重试失败的事件。

### 2. 多窗口合并：按优先级 + 僵尸清理

一盏灯不能表示多个并行 Agent。设计取舍：

- **优先级合并**：`error(70) > wait_confirm(60) > busy(55) > thinking(40) > success(20) > idle(10)`
- **新事件清旧 success**：任何新 hook 来时，主动清掉所有 `Stop`（success）会话——避免绿灯常亮阻塞新状态
- **僵尸扫描**：
  - `set()` 时清掉超期僵尸（PreToolUse 3s、PostToolUse 30s、UserPromptSubmit 30s）
  - 每秒周期性扫描，即便没新事件也清——避免「真实会话偶尔漏发 PostToolUse」导致 winner 长期锁住

### 3. BLE 优于 USB 串口

显示器 USB hub（特别是雷电 docks）经常让 USB-CDC 不稳。BLE 走无线，与 USB hub 解耦，**只要灯有 5V 供电**（USB hub 即可）就能稳定工作。

### 4. SSH 反向隧道而非公网暴露

内网 Linux 通常不能被 Mac 直接访问，但 Mac 能 SSH 进去。`RemoteForward` 顺着已有 SSH 连接开反向通道，不用暴露 Mac 到内网/公网。

## 状态机（固件）

```
idle      → 绿色呼吸（PWM 渐变）
thinking  → 红黄绿跑马灯（150ms 切换）
busy      → 黄灯慢闪（500ms 切换）
success   → 绿灯常亮 5s → 自动回 idle
error     → 红灯快闪（250ms 切换）
wait_confirm → 黄灯常亮
off       → 全灭
```

状态由 BLE 收到的 JSON `{"status": "thinking"}` 决定，固件无状态保持，断电重启默认 idle。

## 配置文件

`web-dashboard/data/config.json`：

```json
{
  "effects": [
    { "id": "idle_green", "frames": [{"leds": ["on","off","off"]}] },
    { "id": "working_yellow", "frames": [{"leds": ["off","breathe","off"]}] },
    ...
  ],
  "event_bindings": {
    "SessionStart": "idle_green",
    "PreToolUse": "working_yellow",
    ...
  },
  "event_priority": ["StopFailure", "PermissionRequest", ...]
}
```

可在 dashboard 网页上改灯效，热生效。
