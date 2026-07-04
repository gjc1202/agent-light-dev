# AgentCore-Light（v1 红绿灯）

**中文** | [English](#english)

面向 **AgentCore-Light v1 实体红绿灯** 的主机端与固件增强：用 **ESP32-C3 + 红/黄/绿三色灯** 把 Cursor、Codex、Claude Code 的工作状态映射到桌面。

> A BLE-powered RGB traffic light for AI coding agents — visualize Cursor / Codex / Claude Code states on your desk.

![status](https://img.shields.io/badge/status-active-green) ![license](https://img.shields.io/badge/license-MIT-blue)

本仓库在 [AgentCore-Light](https://github.com/FPGAmaster-wyc/AgentCore-Light) 生态基础上，针对 **v1 红绿灯成品** 做了主机端与联调增强。硬件为成品红绿灯，无需自行焊接。

---

## 本仓库新增能力

在 v1 红绿灯成品基础上，本仓库补充了以下能力：

- **Cursor 原生 Hooks 全链路** — 覆盖思考、调工具、完成、出错等生命周期；hook 写本地队列、毫秒级返回，后台 worker 异步消费
- **BLE 无线控灯** — 灯只需 5V 供电，不必占用稳定的数据 USB 口
- **Web Dashboard** — `http://127.0.0.1:8787` 查看各会话明细（含 `local` / `ssh` 来源标签）
- **SSH 远程 Cursor** — Linux 服务器上的 Cursor 经 SSH 反向隧道驱动同一盏灯
- **多窗口全局合并** — 本地 + 远程、多个 Cursor 窗口并行时，按优先级合并到一盏物理灯
- **多 Agent 统一状态模型** — Cursor、Codex、Claude Code 共用灯效与合并规则
- **开箱运维** — `setup-mac.sh` / `start-mac.sh` / `doctor.sh` 自检、远程一键装 hooks、macOS 开机自启

---

## 1、项目简介

AgentCore-Light v1 把 ESP32-C3 与实体红/黄/绿三色灯结合，由 Mac 上的状态服务接收 Agent Hooks，经 BLE 下发到固件切换灯效。

核心思路：

- 使用 **ESP32-C3** 作为主控，驱动 **红 / 黄 / 绿** 三色 LED
- 通过 **BLE 蓝牙** 接收 Mac 端桥接脚本发送的状态指令
- 结合 **Cursor / Codex / Claude Code Hooks**，让 Agent 工作状态自动映射到灯效
- Mac 上 **Node.js 状态服务** 聚合多窗口、多来源会话，Dashboard 展示明细

---

## 2、功能特性

- BLE / JSON 命令控制状态：`idle` · `thinking` · `busy` · `wait_confirm` · `success` · `error` · `off`
- Cursor Hooks：SessionStart / PreToolUse / PostToolUse / Stop 等事件自动映射
- Codex、Claude Code hooks 与 Cursor 共用合并规则
- 本地 hook 队列 + 异步 worker，避免 Cursor 5s 超时丢事件
- SSH 远程 Cursor：`RemoteForward` 反向隧道，远程与本地窗口合并到同一盏灯
- Web Dashboard：SSE 实时刷新，按会话查看状态与来源
- 固件动画基于 `millis()` 非阻塞，新状态可随时打断旧动画

---

## 3、硬件说明

本仓库面向 **AgentCore-Light v1 成品红绿灯**（ESP32-C3 + 实体 RGB 三色灯 + BLE）。

| 项目 | 说明 |
| ---- | ---- |
| 主控 | ESP32-C3 |
| 灯体 | 红 / 黄 / 绿 三色 LED（实体红绿灯造型） |
| 通信 | BLE（设备名 `AgentCore-Light`） |
| 供电 | USB 5V（接电脑或显示器 hub 均可，仅需供电） |

DIY 套件（灯环 + OLED 等）见上游 [AgentCore-Light](https://github.com/FPGAmaster-wyc/AgentCore-Light)。

---

## 4、灯效映射

| Agent 状态 | 灯效 | 何时出现 |
|------------|------|----------|
| 空闲 (`idle`) | 绿色呼吸 | 会话空闲 / 刚启动 |
| 思考 (`thinking`) | 红黄绿跑马灯 | 发消息、思考、子任务 |
| 调工具 (`busy`) | 黄灯慢闪 | Agent 正在调 Shell / Read / Write 等 |
| 等你确认 (`wait_confirm`) | 黄灯常亮 | 权限请求、AskUserQuestion（*） |
| 完成 (`success`) | 绿灯常亮 ~5s | 一轮对话完成（瞬时，自动让位） |
| 出错 (`error`) | 红灯快闪 | 工具失败 / Stop with error |
| 不确定 (`unknown`) | 三色慢闪 | 长时间无新事件，server 诚实降级 |
| 无会话 (`off`) | 全灭 | 所有会话结束且 TTL 到期 |

**多窗口合并优先级**（高 → 低）：

```
error > wait_confirm > busy > thinking > success > idle
```

（*）Cursor 3.9.x 下 AskUserQuestion 相关 hook 尚未稳定发出，详见 [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md)。

---

## 5、项目结构

```text
agent-light-dev/
├─ firmware/
│  ├─ esp32_c3_traffic_light.ino
│  └─ platformio.ini
├─ web-dashboard/              # Mac 状态聚合服务（Node.js）
│  ├─ server.js
│  ├─ hook-queue-worker.js     # macOS 队列消费者
│  ├─ hook-queue-worker.py     # Linux 队列消费者
│  └─ static/                  # Dashboard 前端
├─ bridges/
│  ├─ ble_status_bridge.py     # BLE 桥接（推荐）
│  └─ codex_status_bridge.py   # USB 串口桥接（备用）
├─ hooks/
│  ├─ cursor/                  # Cursor hook 脚本
│  └─ codex/                   # Codex hook 脚本
├─ scripts/
│  ├─ setup-mac.sh             # 装环境 + hooks
│  ├─ start-mac.sh             # 启动完整系统
│  ├─ doctor.sh                # 自检
│  ├─ install-remote.sh        # Linux 远程一键装 hooks
│  └─ autostart-install.sh     # macOS 开机自启
├─ utils/
│  └─ agent_light_control.py   # 命令行手动控灯
├─ tests/                      # 多窗口 / 灯效回归测试
├─ docs/
├─ LICENSE
└─ README.md
```

---

## 6、技术栈

- 固件：Arduino Framework（ESP32-C3）/ PlatformIO
- 主机服务：Node.js（状态聚合、SSE、Hook 接收）
- 桥接：Python 3 + bleak（BLE）
- 自动化：Cursor / Codex Hooks + 本地文件队列 + 异步 worker
- 远程：SSH `RemoteForward` + systemd user 服务（Linux worker）

---

## 7、快速开始

详细步骤见 [docs/SETUP-MAC.md](docs/SETUP-MAC.md)。

最短流程（macOS）：

```bash
git clone https://github.com/gjc1202/agent-light-dev.git
cd agent-light-dev

# 1. 装环境（venv + 依赖 + Cursor hooks）
./scripts/setup-mac.sh

# 2. 启动完整系统
./scripts/start-mac.sh

# 3. 自检
./scripts/doctor.sh
```

在 Cursor 里正常使用 Agent，灯就会跟着变。Dashboard：`http://127.0.0.1:8787`

### SSH 远程 Cursor

详见 [docs/SSH-SETUP.md](docs/SSH-SETUP.md)。在 Mac `~/.ssh/config` 配置 `RemoteForward`，Linux 上执行 `./scripts/install-remote.sh` 即可。

---

## 8、架构概览

详见 [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)。

```
┌───────────────────┐   hook event    ┌─────────────────┐
│ Cursor (本地)     │ ───────────────► │                 │
│ ~/.cursor/hooks/  │   queue + POST   │   Mac web 服务  │
└───────────────────┘                  │   :8787         │
┌───────────────────┐   SSH 反向隧道   │   (会话合并)    │
│ Cursor (SSH)      │ ───────────────► │                 │
│ Linux 上 ~/.cursor│   18787 → 8787   └────────┬────────┘
└───────────────────┘                          │ /api/status
                                                 ▼
                                        ┌────────────────┐
                                        │ BLE 桥接       │
                                        └───────┬────────┘
                                                │ BLE
                                                ▼
                                        ┌────────────────┐
                                        │ ESP32-C3 红绿灯│
                                        └────────────────┘
```

---

## 9、文档

| 文档 | 说明 |
| ---- | ---- |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | 架构与数据流 |
| [docs/SETUP-MAC.md](docs/SETUP-MAC.md) | macOS 安装 |
| [docs/SSH-SETUP.md](docs/SSH-SETUP.md) | SSH 远程接入 |
| [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md) | 常见问题 |
| [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md) | 开发与测试 |
| [docs/SAFETY.md](docs/SAFETY.md) | 固件烧录安全 |

---

## 10、已知限制

- **一盏灯只表示一种全局状态** — 多窗口并行时显示最高优先级；各会话明细见 Dashboard
- **SSH 隧道依赖 SSH 连接存活** — 断线期间远程事件暂时进不来，重连后自动恢复
- **Cursor hook 偶发超时** — 通过本地队列缓解，正常情况事件不丢

---

## 11、上游与致谢

- **v1 交付源码**：[light.buildfpga.com](https://light.buildfpga.com/agentcore-light-v1.txt) 提供的客户交付包（`agentcore-light-v1-delivery-20260611.zip`）与固件包；本仓库在其基础上开发
- **上游开源项目**：[FPGAmaster-wyc/AgentCore-Light](https://github.com/FPGAmaster-wyc/AgentCore-Light)
- **v1 红绿灯成品**：BuildFPGA / [FPGAmaster-wyc](https://github.com/FPGAmaster-wyc)
- **本仓库主机端增强**：Alexander Gu

欢迎 Star、Issue；与上游相关的通用改进也会择机向上游提交 PR。

---

# 开源协议（License）

本项目采用 MIT License 开源协议。使用、修改、分发时请保留版权声明与 LICENSE 文件。

## 免责声明

本项目按「现状（AS IS）」提供，不提供任何形式的明示或暗示担保。作者不对因使用本项目而产生的任何直接或间接损失承担责任。

MIT，见 [LICENSE](LICENSE)。

---

# English

[Back to 中文](#agentcore-lightv1-红绿灯)

Host-side and firmware enhancements for the **AgentCore-Light v1 physical traffic light**: an **ESP32-C3 + RGB tri-color lamp** that visualizes Cursor, Codex, and Claude Code on your desk.

> A BLE-powered RGB traffic light for AI coding agents.

This repo builds on the [AgentCore-Light](https://github.com/FPGAmaster-wyc/AgentCore-Light) ecosystem, targeting the **v1 pre-built traffic-light hardware**. No soldering required.

---

## What's New in This Repo

Enhancements for the v1 traffic-light product:

- **Full Cursor native Hooks pipeline** — lifecycle from thinking through tool calls to completion and errors; hooks write to a local queue and return in milliseconds; a background worker consumes asynchronously
- **BLE wireless control** — the lamp only needs 5V power; no stable data USB port required
- **Web Dashboard** — `http://127.0.0.1:8787` with per-session detail and `local` / `ssh` source tags
- **SSH remote Cursor** — Cursor on a Linux server drives the same lamp via SSH reverse tunnel
- **Multi-window global merge** — local + remote windows combined with priority rules on one physical lamp
- **Unified status model** — Cursor, Codex, and Claude Code share effects and merge rules
- **Ops tooling** — `setup-mac.sh` / `start-mac.sh` / `doctor.sh`, one-click remote hook install, macOS autostart

---

## 1. Overview

AgentCore-Light v1 pairs ESP32-C3 with a physical red / yellow / green traffic light. A Mac status service receives Agent Hooks and sends state over BLE to the firmware.

- **ESP32-C3** drives **red / yellow / green** LEDs
- **BLE** carries JSON status commands from the Mac bridge
- **Cursor / Codex / Claude Code Hooks** map agent runtime to lamp effects
- **Node.js status service** merges multi-window, multi-source sessions; Dashboard shows details

---

## 2. Features

- BLE / JSON states: `idle` · `thinking` · `busy` · `wait_confirm` · `success` · `error` · `off`
- Cursor Hooks: SessionStart, PreToolUse, PostToolUse, Stop, and more
- Codex and Claude Code hooks share the same merge rules as Cursor
- Local hook queue + async worker to avoid Cursor's 5s hook timeout
- SSH remote Cursor via `RemoteForward`; remote and local windows merge on one lamp
- Web Dashboard with SSE live updates
- Non-blocking firmware animations based on `millis()`

---

## 3. Hardware

This repo targets the **AgentCore-Light v1 pre-built traffic light** (ESP32-C3 + RGB tri-color lamp + BLE).

| Item | Notes |
| ---- | ----- |
| MCU | ESP32-C3 |
| Lamp | Red / yellow / green LEDs (traffic-light form factor) |
| Link | BLE (device name `AgentCore-Light`) |
| Power | USB 5V (power only; hub-friendly) |

For the DIY kit (LED ring + OLED, etc.), see upstream [AgentCore-Light](https://github.com/FPGAmaster-wyc/AgentCore-Light).

---

## 4. Status → Lamp Effects

| Status | Effect | When |
|--------|--------|------|
| `idle` | Green breathing | Session idle / just started |
| `thinking` | R-Y-G chase | Prompt, thinking, subagents |
| `busy` | Yellow slow blink | Shell / Read / Write / tools |
| `wait_confirm` | Yellow solid | Permission / AskUserQuestion (*) |
| `success` | Green solid ~5s | Turn completed (transient) |
| `error` | Red fast blink | Tool failure / Stop with error |
| `unknown` | Tri-color slow blink | Degraded when events stall |
| `off` | All off | No sessions after TTL |

**Multi-window merge priority** (high → low):

```
error > wait_confirm > busy > thinking > success > idle
```

(*) AskUserQuestion hooks are not reliably emitted on Cursor 3.9.x yet — see [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md).

---

## 5. Project Layout

See the Chinese section above for the full tree (`firmware/`, `web-dashboard/`, `bridges/`, `hooks/`, `scripts/`, `tests/`, `docs/`).

---

## 6. Tech Stack

- Firmware: Arduino (ESP32-C3) / PlatformIO
- Host: Node.js (aggregation, SSE, hook ingestion)
- Bridge: Python 3 + bleak (BLE)
- Automation: Cursor / Codex Hooks + file queue + async worker
- Remote: SSH `RemoteForward` + systemd user service on Linux

---

## 7. Quick Start

Details: [docs/SETUP-MAC.md](docs/SETUP-MAC.md).

```bash
git clone https://github.com/gjc1202/agent-light-dev.git
cd agent-light-dev
./scripts/setup-mac.sh
./scripts/start-mac.sh
./scripts/doctor.sh
```

Use Cursor Agent normally — the lamp follows. Dashboard: `http://127.0.0.1:8787`

**SSH remote Cursor:** [docs/SSH-SETUP.md](docs/SSH-SETUP.md)

---

## 8. Architecture

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) and the ASCII diagram in the Chinese section.

---

## 9. Docs

| Doc | Topic |
| --- | ----- |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | Architecture & data flow |
| [docs/SETUP-MAC.md](docs/SETUP-MAC.md) | macOS setup |
| [docs/SSH-SETUP.md](docs/SSH-SETUP.md) | SSH remote |
| [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md) | FAQ |
| [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md) | Dev & tests |
| [docs/SAFETY.md](docs/SAFETY.md) | Safe firmware upload |

---

## 10. Known Limitations

- **One lamp, one global state** — highest priority wins; per-session detail on the Dashboard
- **SSH tunnel must stay up** — remote events pause while disconnected
- **Occasional Cursor hook timeout** — mitigated by the local queue

---

## 11. Upstream & Credits

- **v1 delivery source**: Customer delivery package (`agentcore-light-v1-delivery-20260611.zip`) and firmware package from [light.buildfpga.com](https://light.buildfpga.com/agentcore-light-v1.txt); this repo builds on that baseline
- **Upstream open source**: [FPGAmaster-wyc/AgentCore-Light](https://github.com/FPGAmaster-wyc/AgentCore-Light)
- **v1 hardware**: BuildFPGA / [FPGAmaster-wyc](https://github.com/FPGAmaster-wyc)
- **Host enhancements in this repo**: Alexander Gu

Stars and Issues welcome. Generic improvements may be contributed upstream via PR.

---

# License

MIT License — see [LICENSE](LICENSE). Keep copyright notices when redistributing.

Provided **AS IS** without warranty.
