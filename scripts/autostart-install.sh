#!/usr/bin/env bash
# 配置/移除 Mac 开机自启（launchd 直接管理三个独立服务）。
# 用法：
#   ./scripts/autostart-install.sh     # 配置开机自启
#   ./scripts/autostart-install.sh --uninstall
set -euo pipefail

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
ROOT="$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)"
PLIST_DIR="$HOME/Library/LaunchAgents"
NODE="$(command -v node)"
PYTHON="$ROOT/.venv/bin/python"
WEB_DIR="$ROOT/web-dashboard"
BRIDGES_DIR="$ROOT/bridges"

LABELS=(
  "com.user.agentcore-light.web"
  "com.user.agentcore-light.bridge"
  "com.user.agentcore-light.queue"
  "com.user.agentcore-light.timeline"
  "com.user.agentcore-light.logrotate"
)

ACTION="${1:-install}"

render_template() {
  local tpl="$1" out="$2"
  sed \
    -e "s|__NODE__|$NODE|g" \
    -e "s|__PYTHON__|$PYTHON|g" \
    -e "s|__WEB_DIR__|$WEB_DIR|g" \
    -e "s|__BRIDGES_DIR__|$BRIDGES_DIR|g" \
    -e "s|__ROOT__|$ROOT|g" \
    "$tpl" > "$out"
}

case "$ACTION" in
  install)
    mkdir -p "$PLIST_DIR"
    # 先杀掉手动启动的进程，避免端口冲突
    pkill -f "node server.js" 2>/dev/null || true
    pkill -f "node hook-queue-worker.js" 2>/dev/null || true
    pkill -f "ble_status_bridge.py" 2>/dev/null || true
    sleep 1

    render_template "$SCRIPT_DIR/com.user.agentcore-light.web.plist.template"   "$PLIST_DIR/com.user.agentcore-light.web.plist"
    render_template "$SCRIPT_DIR/com.user.agentcore-light.bridge.plist.template" "$PLIST_DIR/com.user.agentcore-light.bridge.plist"
    render_template "$SCRIPT_DIR/com.user.agentcore-light.queue.plist.template" "$PLIST_DIR/com.user.agentcore-light.queue.plist"
    render_template "$SCRIPT_DIR/com.user.agentcore-light.timeline.plist.template" "$PLIST_DIR/com.user.agentcore-light.timeline.plist"
    render_template "$SCRIPT_DIR/com.user.agentcore-light.logrotate.plist.template" "$PLIST_DIR/com.user.agentcore-light.logrotate.plist"

    # 删旧的合并 plist（如有）
    launchctl unload "$PLIST_DIR/com.user.agentcore-light.plist" 2>/dev/null || true
    rm -f "$PLIST_DIR/com.user.agentcore-light.plist"

    for LABEL in "${LABELS[@]}"; do
      PLIST="$PLIST_DIR/$LABEL.plist"
      launchctl unload "$PLIST" 2>/dev/null || true
      launchctl load "$PLIST"
      echo "[OK] 已加载 $LABEL"
    done

    sleep 2
    echo ""
    echo "五个 launchd 服务已配置："
    echo "  • web 服务（:8787）"
    echo "  • BLE 桥接"
    echo "  • hook 队列 worker"
    echo "  • timeline 日志聚合（/tmp/agent-light-timeline.log）"
    echo "  • 日志轮转（每小时跑一次，避免 /tmp 被撑爆）"
    echo ""
    echo "开机自动启动；进程意外退出会自动重启（KeepAlive=true）。"
    echo ""
    echo "卸载：$SCRIPT_DIR/autostart-install.sh --uninstall"
    ;;
  --uninstall|uninstall)
    for LABEL in "${LABELS[@]}"; do
      PLIST="$PLIST_DIR/$LABEL.plist"
      if [[ -f "$PLIST" ]]; then
        launchctl unload "$PLIST" 2>/dev/null || true
        rm -f "$PLIST"
        echo "[OK] 已移除 $LABEL"
      fi
    done
    # 顺便清旧的合并 plist
    if [[ -f "$PLIST_DIR/com.user.agentcore-light.plist" ]]; then
      launchctl unload "$PLIST_DIR/com.user.agentcore-light.plist" 2>/dev/null || true
      rm -f "$PLIST_DIR/com.user.agentcore-light.plist"
    fi
    ;;
  *)
    echo "用法：$0 [install|--uninstall]"
    exit 1
    ;;
esac
