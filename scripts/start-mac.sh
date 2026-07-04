#!/usr/bin/env bash
# 启动完整系统：web 服务 + hook 队列 worker + BLE 桥接。
# 用法：./scripts/start-mac.sh
#
# 【设计】所有长跑服务一律由 launchd 托管（KeepAlive=true，崩了自动重启）。
# 不再用 nohup & disown——这种方式在 macOS 上不可靠，父 shell 退出时
# 子进程会被 SIGTERM 带走（之前 timeline logger 死过两次就是这个原因）。
# 一次性启动用 ./scripts/autostart-install.sh 把 plist 装好；本脚本是
# 用户友好的「检查 + 起服务」入口，本身不直接 fork 任何长跑进程。
set -uo pipefail

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
ROOT="$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)"

if [[ ! -x "$ROOT/.venv/bin/python" ]]; then
  echo "Python venv not found. Run ./scripts/setup-mac.sh first." >&2
  exit 1
fi
if ! command -v node >/dev/null 2>&1; then
  echo "Node.js not found. 请先 brew install node" >&2
  exit 1
fi

PLIST_DIR="$HOME/Library/LaunchAgents"
LABELS=(
  "com.user.agentcore-light.web"
  "com.user.agentcore-light.bridge"
  "com.user.agentcore-light.queue"
)

echo "============================================================"
echo "AI Status Light - 启动完整系统（macOS）"
echo "============================================================"

# 检查 plist 是否已 install；没有就先装一遍
NEED_INSTALL=0
for LABEL in "${LABELS[@]}"; do
  if [[ ! -f "$PLIST_DIR/$LABEL.plist" ]]; then
    NEED_INSTALL=1
    break
  fi
done

if [[ "$NEED_INSTALL" == "1" ]]; then
  echo "[Setup] 首次运行：装 launchd plist..."
  "$SCRIPT_DIR/autostart-install.sh" || {
    echo "[ERR] autostart-install.sh 失败" >&2
    exit 1
  }
fi

# bootstrap 所有 agent（已 loaded 的会跳过）
for LABEL in "${LABELS[@]}"; do
  PLIST="$PLIST_DIR/$LABEL.plist"
  if launchctl print "gui/$UID/$LABEL" >/dev/null 2>&1; then
    echo "[OK] $LABEL 已在 launchd 管理"
  else
    launchctl bootstrap "gui/$UID" "$PLIST" 2>&1 | head -3
    echo "[Run] $LABEL 已 bootstrap"
  fi
done

# 等 web server 起来
sleep 2

# 后台打开浏览器（不阻塞脚本，open 自己 fork）
(open "http://127.0.0.1:8787" 2>/dev/null &) || true

echo ""
echo "[OK] 系统已就绪。所有服务由 launchd 管理（KeepAlive=true，自动重启）。"
echo "[Web] http://127.0.0.1:8787"
echo "[Mode] BLE bridge（经显示器 USB hub 供电也适用）"
echo ""
echo "服务管理："
echo "  装开机自启：  ./scripts/autostart-install.sh"
echo "  查服务状态：  launchctl list | grep agentcore"
echo "  看实时日志：  tail -f /tmp/agent-light-timeline.log"
echo "  重启某个服务：launchctl kickstart -k gui/\$UID/com.user.agentcore-light.web"
echo ""
echo "日常用法：开机后会自动启动（launchd RunAtLoad），无需手动跑本脚本。"
