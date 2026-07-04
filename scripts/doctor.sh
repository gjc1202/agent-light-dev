#!/usr/bin/env bash
# AgentCore-Light 自检：检查整条链路是否健康。
# 用法：./scripts/doctor.sh
set -uo pipefail

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
ROOT="$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)"
WEB_URL="${AGENT_LIGHT_URL:-http://127.0.0.1:8787}"
# 绕过任何代理环境变量（curl 走代理会连不上本地服务）
CURL="env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u ALL_PROXY -u all_proxy curl -s --max-time 5"

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'
ok()   { echo -e "${GREEN}[OK]${NC}  $1"; }
warn() { echo -e "${YELLOW}[WARN]${NC}$1"; }
fail() { echo -e "${RED}[FAIL]${NC}$1"; }

echo "============================================================"
echo "AgentCore-Light 自检"
echo "============================================================"
echo ""

# 1. Web 服务
if $CURL "$WEB_URL/api/status" >/dev/null 2>&1; then
  ok "Mac web 服务          $WEB_URL 在跑"
else
  fail "Mac web 服务          无法访问 $WEB_URL/api/status"
  echo "       修复：./scripts/start-mac.sh 或 ./scripts/autostart-install.sh"
fi

# 2. Web 服务的 sessions
if STATUS=$($CURL "$WEB_URL/api/status" 2>/dev/null); then
  COUNT=$(echo "$STATUS" | python3 -c "import sys,json; print(len(json.load(sys.stdin).get('sessions',[])))" 2>/dev/null || echo "?")
  WINNER=$(echo "$STATUS" | python3 -c "import sys,json; d=json.load(sys.stdin); print(f\"{d['device_status']}/{d['winner_event']}\")" 2>/dev/null || echo "?")
  ok "Mac web 服务状态      sessions=$COUNT winner=$WINNER"
fi

# 3. Hook 队列 worker
if pgrep -f "hook-queue-worker.js" >/dev/null 2>&1; then
  ok "hook 队列 worker      在跑"
else
  warn "hook 队列 worker      未在跑（事件可能堆在队列里）"
  echo "       修复：./scripts/start-mac.sh"
fi

# 4. 队列堆积
QUEUE_DIR="$HOME/.cursor/hooks/queue"
if [[ -d "$QUEUE_DIR" ]]; then
  QUEUED=$(find "$QUEUE_DIR" -name "*.json" 2>/dev/null | wc -l | tr -d ' ')
  if [[ "$QUEUED" -gt 20 ]]; then
    warn "hook 队列堆积         $QUEUED 个事件待处理（worker 可能卡住）"
  else
    ok "hook 队列             $QUEUED 个待处理"
  fi
else
  warn "hook 队列目录         不存在 $QUEUE_DIR"
fi

# 5. BLE 桥接
if pgrep -f "ble_status_bridge.py" >/dev/null 2>&1; then
  ok "BLE 桥接              在跑"
  # 看最近的日志
  if [[ -f /tmp/agentcore-light-bridge.log ]]; then
    LAST=$(tail -1 /tmp/agentcore-light-bridge.log 2>/dev/null)
    echo "       最近：$LAST"
  fi
else
  fail "BLE 桥接              未在跑"
  echo "       修复：./scripts/start-bridge-mac.sh"
fi

# 6. Cursor hooks 已装
if [[ -f "$HOME/.cursor/hooks.json" ]]; then
  HOOK_COUNT=$(grep -c "agentcore-light.sh" "$HOME/.cursor/hooks.json" 2>/dev/null || echo 0)
  if [[ "$HOOK_COUNT" -ge 10 ]]; then
    ok "Cursor hooks          已装（$HOOK_COUNT 条）"
  else
    warn "Cursor hooks          不完整（仅 $HOOK_COUNT 条，期望 ≥10）"
  fi
else
  fail "Cursor hooks          ~/.cursor/hooks.json 不存在"
  echo "       修复：node $ROOT/scripts/install-hooks.js"
fi

# 7. SSH 隧道（如果当前在 SSH 模式，或本机能 SSH 到 gjc_2031）
# 7-8. 扫所有 SSH 主机（任意有 RemoteForward 的）
SSH_CONFIG="${HOME}/.ssh/config"
if [[ -f "$SSH_CONFIG" ]]; then
  # 提取所有 Host 条目（取第一个，跳过 * 通配）
  SSH_HOSTS=$(awk '/^Host [^*]/ {print $2}' "$SSH_CONFIG" | sort -u)
  for HOST in $SSH_HOSTS; do
    # 跳过明显非红绿灯相关的（可在 ~/.agent-light-ssh-hosts 显式指定）
    if [[ -f "${HOME}/.agent-light-ssh-hosts" ]]; then
      if ! grep -q "^${HOST}$" "${HOME}/.agent-light-ssh-hosts" 2>/dev/null; then
        continue
      fi
    fi
    # 测连通性
    if ! ssh -O check "$HOST" >/dev/null 2>&1; then
      continue
    fi
    # 测隧道
    TUNNEL_PORT=$(ssh "$HOST" 'echo $SSH_CONNECTION' 2>/dev/null | awk '{print $0}' || echo "")
    # 简单方法：尝试连 18787
    TUNNEL_TEST=$(ssh "$HOST" 'curl -s --max-time 2 http://127.0.0.1:18787/api/status 2>/dev/null | head -c 20' 2>/dev/null || echo "")
    if [[ -n "$TUNNEL_TEST" ]]; then
      ok "SSH 隧道 ${HOST}    通（18787 → Mac 8787）"
    else
      warn "SSH 隧道 ${HOST}    隧道未通（重连 SSH 窗口试试）"
      continue
    fi
    # 测远程 hooks + worker + 心跳
    REMOTE_HOOKS=$(ssh "$HOST" 'grep -c agentcore-light.sh ~/.cursor/hooks.json 2>/dev/null || echo 0' 2>/dev/null || echo "?")
    REMOTE_WORKER=$(ssh "$HOST" 'pgrep -f hook-queue-worker.py >/dev/null && echo yes || echo no' 2>/dev/null || echo "?")
    REMOTE_HEARTBEAT=$(ssh "$HOST" 'cat ~/.cursor/hooks/heartbeat.txt 2>/dev/null || echo missing' 2>/dev/null || echo "?")
    REMOTE_HB_AGE_S=$(ssh "$HOST" 'python3 -c "
import json, time
from pathlib import Path
from datetime import datetime, timezone
try:
    data = json.loads(Path(\"~/.cursor/hooks/heartbeat.txt\").expanduser().read_text())
    at = datetime.fromisoformat(data[\"at\"])
    age = (datetime.now(timezone.utc).astimezone() - at).total_seconds()
    print(int(age))
except Exception as e:
    print(-1)
" 2>/dev/null || echo -1' 2>/dev/null || echo "?")

    if [[ "$REMOTE_HOOKS" -ge 10 && "$REMOTE_WORKER" == "yes" ]]; then
      if [[ "$REMOTE_HB_AGE_S" -ge 0 && "$REMOTE_HB_AGE_S" -le 300 ]]; then
        ok "${HOST} hooks+worker+心跳  全 OK（心跳 ${REMOTE_HB_AGE_S}s 前）"
      elif [[ "$REMOTE_HB_AGE_S" -ge 0 ]]; then
        warn "${HOST} 心跳过期        ${REMOTE_HB_AGE_S}s 前（worker 卡住？）"
        echo "       修复：ssh $HOST 'systemctl --user restart agentcore-light-hook'"
      else
        warn "${HOST} 心跳文件异常    （$REMOTE_HEARTBEAT）"
      fi
    else
      warn "${HOST} hooks/worker    hooks=$REMOTE_HOOKS worker=$REMOTE_WORKER"
      echo "       修复：scp $ROOT/scripts/install-remote.sh ${HOST}:/tmp/ && ssh ${HOST} bash /tmp/install-remote.sh"
    fi
  done
fi

# 9. 开机自启（三个独立 launchd 服务）
WEB_LOADED=$(launchctl list 2>/dev/null | grep -c "agentcore-light.web" || echo 0)
BRIDGE_LOADED=$(launchctl list 2>/dev/null | grep -c "agentcore-light.bridge" || echo 0)
QUEUE_LOADED=$(launchctl list 2>/dev/null | grep -c "agentcore-light.queue" || echo 0)
TOTAL_LOADED=$((WEB_LOADED + BRIDGE_LOADED + QUEUE_LOADED))
if [[ "$TOTAL_LOADED" -eq 3 ]]; then
  ok "开机自启 launchd      三个服务都已加载"
elif [[ "$TOTAL_LOADED" -ge 1 ]]; then
  warn "开机自启 launchd      部分（web=$WEB_LOADED bridge=$BRIDGE_LOADED queue=$QUEUE_LOADED）"
  echo "       修复：./scripts/autostart-install.sh --uninstall && ./scripts/autostart-install.sh"
elif [[ -f "$HOME/Library/LaunchAgents/com.user.agentcore-light.plist" ]]; then
  warn "开机自启 launchd      旧版合并 plist 残留"
  echo "       修复：./scripts/autostart-install.sh --uninstall && ./scripts/autostart-install.sh"
else
  warn "开机自启 launchd      未配置（可选）"
  echo "       配置：./scripts/autostart-install.sh"
fi

echo ""
echo "============================================================"
echo "自检完成。FAIL 项必须修；WARN 项按需修。"
echo "============================================================"
