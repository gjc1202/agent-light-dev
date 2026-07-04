# macOS 安装

## 前置条件

- macOS 12+
- Python 3.10+（`python3 --version`）
- Node.js 18+（`node --version`，没有就 `brew install node`）
- ESP32-C3 红绿灯（已烧录固件，BLE 设备名 `AgentCore-Light`）
- USB 数据线（接电脑或显示器 USB hub 都行，仅供电）

## 一键安装

```bash
cd ~/Documents/agent-light-dev
./scripts/setup-mac.sh
```

这个脚本会：
1. 建 `.venv` Python 虚拟环境
2. 装 `pyserial`、`bleak`、`esptool`
3. 装 Cursor / Codex / Claude Code 的 hooks（`~/.cursor/hooks.json` 等）

## 启动

```bash
./scripts/start-mac.sh
```

启动后：
- 浏览器自动打开 `http://127.0.0.1:8787`（dashboard）
- 后台跑：web 服务、hook 队列 worker、BLE 桥接
- 在 Cursor 里用 Agent，灯就会跟着变

## 开机自启（推荐）

```bash
./scripts/autostart-install.sh
```

会装一个 launchd 服务，Mac 开机后自动启动。卸载：

```bash
./scripts/autostart-install.sh --uninstall
```

## 自检

```bash
./scripts/doctor.sh
```

输出形如：

```
[OK]  Mac web 服务          http://127.0.0.1:8787 在跑
[OK]  Mac web 服务状态      sessions=2 winner=busy/PreToolUse
[OK]  hook 队列 worker      在跑
[OK]  hook 队列             0 个待处理
[OK]  BLE 桥接              在跑
[OK]  Cursor hooks          已装（11 条）
[WARN]SSH 隧道 gjc_2031      未连接（不用 SSH 可忽略）
[WARN]开机自启 launchd      未配置（可选，配置方法见 docs/SETUP-MAC.md）
```

`FAIL` 必须修，`WARN` 按需修。

## 烧录固件（仅首次或更新固件时）

```bash
# 把 ESP32 用 USB 接到 Mac
cd firmware
# 用 PlatformIO 或 Arduino IDE 烧 esp32_c3_traffic_light.ino
# 或命令行：
../.venv/bin/esptool.py --chip esp32c3 --port /dev/cu.usbmodem* write_flash 0x0 esp32_c3_traffic_light.ino.bin
```

烧录后，BLE 设备名应为 `AgentCore-Light`。

## 手动测试灯效

网页 dashboard 上点「模拟测试」按钮，或命令行：

```bash
./utils/agent_light_control.py /dev/cu.usbmodem* thinking
# 或经 BLE（推荐）
curl -X POST http://127.0.0.1:8787/event -d 'R'   # 红灯闪
curl -X POST http://127.0.0.1:8787/event -d 'Y'   # 黄灯闪
curl -X POST http://127.0.0.1:8787/event -d 'G'   # 绿灯呼吸
curl -X POST http://127.0.0.1:8787/event -d 'O'   # 关灯
```

## 卸载

```bash
./scripts/autostart-install.sh --uninstall   # 移除开机自启
pkill -f "node server.js|ble_status_bridge|hook-queue-worker"
rm -rf ~/Documents/agent-light-dev/.venv
rm ~/.cursor/hooks.json                       # 如不再用 Cursor hooks
```
