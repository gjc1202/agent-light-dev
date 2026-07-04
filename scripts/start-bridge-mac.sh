#!/usr/bin/env bash
# 启动 BLE / 串口桥接，把状态推到 ESP32 红绿灯。
# 用法：./scripts/start-bridge-mac.sh [ble|serial]
#
# 【设计】本脚本是 launchd 管理的 bridge 服务的「触发器」。
# 长跑进程由 launchd KeepAlive=true 自动重启；本脚本只做 kickstart。
# 不再直接 fork 长跑进程（nohup & disown 在 macOS 上不可靠）。
set -uo pipefail

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
ROOT="$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)"
LOG="${AGENT_LIGHT_LOG:-/tmp/agentcore-light-bridge.log}"
TRANSPORT="${1:-${AGENT_LIGHT_TRANSPORT:-ble}}"
LABEL="com.user.agentcore-light.bridge"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"

if [[ ! -f "$PLIST" ]]; then
  # 还没装 launchd plist — 回退到 autostart-install
  echo "[bridge] launchd plist 不存在，先跑 autostart-install.sh" >> "$LOG"
  "$SCRIPT_DIR/autostart-install.sh" >>"$LOG" 2>&1
fi

# 已在 launchd 管理就 kickstart（重启）
if launchctl print "gui/$UID/$LABEL" >/dev/null 2>&1; then
  echo "[bridge] kickstart (restart) via launchd" >> "$LOG"
  launchctl kickstart -k "gui/$UID/$LABEL" 2>>"$LOG"
else
  echo "[bridge] bootstrap into launchd" >> "$LOG"
  launchctl bootstrap "gui/$UID" "$PLIST" 2>>"$LOG"
fi

echo "[bridge] mode=$TRANSPORT managed by launchd ($LABEL)" >> "$LOG"
