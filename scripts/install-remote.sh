#!/usr/bin/env bash
# 远程 Linux 上一次性部署：hooks + queue worker + 心跳 + systemd 服务
# 在 Mac 上执行：scp 到 Linux 后用 ssh 跑
#
# 此脚本由 docs/SSH-SETUP.md 和 .cursor/skills/remote-deploy/SKILL.md 描述。
set -euo pipefail

PORT="${AGENT_LIGHT_TUNNEL_PORT:-18787}"

mkdir -p "$HOME/.cursor/hooks/queue"

# hook 脚本：写队列 + 标 source=ssh + 记日志
cat > "$HOME/.cursor/hooks/agentcore-light.sh" <<'HOOK_EOF'
#!/usr/bin/env sh
# 远程 Cursor hook（标 source=ssh）
QUEUE_DIR="${HOME}/.cursor/hooks/queue"
HOOK_LOG="${HOME}/.cursor/hooks/hook.log"
mkdir -p "$QUEUE_DIR"
payload=$(cat)
if [ -n "$payload" ]; then
  case "$payload" in
    *'"source"'*) : ;;
    *) payload=$(printf '%s' "$payload" | sed 's/^{/{\"source\":\"ssh\",/' 2>/dev/null || printf '%s' "$payload") ;;
  esac
  file="$QUEUE_DIR/$(date +%s%N)-$$.json"
  printf '%s' "$payload" > "$file"
  # 记日志：时间 + event name + queue 文件名（便于排查 hook 是否触发）
  event=$(printf '%s' "$payload" | sed -n 's/.*"hook_event_name":"\([^"]*\)".*/\1/p' 2>/dev/null)
  ts=$(date '+%Y-%m-%dT%H:%M:%S%z')
  echo "${ts} event=${event:-?} -> $(basename "$file")" >> "$HOOK_LOG" 2>/dev/null || true
fi
exit 0
HOOK_EOF
chmod +x "$HOME/.cursor/hooks/agentcore-light.sh"

# queue worker：转发 + 心跳 + 健康日志
cat > "$HOME/.cursor/hooks/hook-queue-worker.py" <<WORKER_EOF
#!/usr/bin/env python3
"""Remote hook queue worker (Linux).

- Drains ~/.cursor/hooks/queue/*.json → POST to Mac status light via SSH tunnel
- Writes heartbeat every 60s
- Logs health (success/failure) to ~/.cursor/hooks/health.log
"""
from __future__ import annotations
import json, os, socket, sys, time, urllib.error, urllib.request
from datetime import datetime, timezone
from pathlib import Path

HOOK_URL = os.environ.get("AGENT_LIGHT_HOOK_URL", "http://127.0.0.1:${PORT}/hook?agent=cursor")
QUEUE_DIR = Path(os.environ.get("AGENT_LIGHT_QUEUE_DIR", os.path.expanduser("~/.cursor/hooks/queue")))
HEARTBEAT_PATH = Path(os.path.expanduser("~/.cursor/hooks/heartbeat.txt"))
HEALTH_LOG = Path(os.path.expanduser("~/.cursor/hooks/health.log"))
POLL_SECONDS = 0.05
STALE_SECONDS = 60.0
HEARTBEAT_INTERVAL = 60.0


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _log_health(msg: str) -> None:
    try:
        with open(HEALTH_LOG, "a", encoding="utf-8") as f:
            f.write(f"{_now_iso()} {msg}\n")
        # Rotate: keep last 200 lines
        lines = HEALTH_LOG.read_text(encoding="utf-8").splitlines()
        if len(lines) > 200:
            HEALTH_LOG.write_text("\n".join(lines[-200:]) + "\n", encoding="utf-8")
    except OSError:
        pass


def _write_heartbeat(status: str = "ok", detail: str = "") -> None:
    try:
        HEARTBEAT_PATH.write_text(
            json.dumps({
                "at": _now_iso(),
                "host": socket.gethostname(),
                "pid": os.getpid(),
                "tunnel_url": HOOK_URL,
                "status": status,
                "detail": detail,
                "queue_pending": len(list(QUEUE_DIR.glob("*.json"))) if QUEUE_DIR.exists() else 0,
            }, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    except OSError:
        pass


def post_payload(raw: str) -> None:
    req = urllib.request.Request(
        HOOK_URL,
        data=raw.encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    resp = opener.open(req, timeout=2)
    resp.read()
    if resp.status >= 400:
        raise RuntimeError(f"hook post failed: {resp.status}")


def drain_once() -> tuple[int, int]:
    """Return (success_count, failure_count)."""
    QUEUE_DIR.mkdir(parents=True, exist_ok=True)
    success = failure = 0
    for path in sorted(QUEUE_DIR.iterdir()):
        if path.suffix != ".json":
            continue
        try:
            raw = path.read_text(encoding="utf-8")
            if raw.strip():
                post_payload(raw)
                success += 1
            path.unlink()
        except Exception as exc:
            failure += 1
            try:
                age = time.time() - path.stat().st_mtime
                if age > STALE_SECONDS:
                    path.unlink()
            except OSError:
                pass
            _log_health(f"FAIL {path.name}: {exc}")
    return success, failure


def main() -> None:
    QUEUE_DIR.mkdir(parents=True, exist_ok=True)
    print(f"[hook-queue] draining {QUEUE_DIR} -> {HOOK_URL}", file=sys.stderr)
    _write_heartbeat("starting")
    last_heartbeat = 0.0
    last_status = "starting"
    while True:
        try:
            success, failure = drain_once()
            if success > 0 or failure > 0:
                _log_health(f"drain ok={success} fail={failure}")
                last_status = "ok" if failure == 0 else f"partial({failure} fail)"
        except Exception as exc:
            _log_health(f"loop error: {exc}")
            last_status = f"error: {exc}"
            print(f"[hook-queue] {exc}", file=sys.stderr)
        # 心跳
        now = time.time()
        if now - last_heartbeat >= HEARTBEAT_INTERVAL:
            _write_heartbeat(last_status[:50])
            last_heartbeat = now
        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    main()
WORKER_EOF
chmod +x "$HOME/.cursor/hooks/hook-queue-worker.py"

# Cursor hooks.json
cat > "$HOME/.cursor/hooks.json" <<'HOOKS_EOF'
{
  "version": 1,
  "hooks": {
    "sessionStart":          [{ "command": "sh \"./hooks/agentcore-light.sh\"", "timeout": 5 }],
    "sessionEnd":            [{ "command": "sh \"./hooks/agentcore-light.sh\"", "timeout": 5 }],
    "beforeSubmitPrompt":    [{ "command": "sh \"./hooks/agentcore-light.sh\"", "timeout": 5 }],
    "preToolUse":            [{ "command": "sh \"./hooks/agentcore-light.sh\"", "timeout": 5 }],
    "postToolUse":           [{ "command": "sh \"./hooks/agentcore-light.sh\"", "timeout": 5 }],
    "postToolUseFailure":    [{ "command": "sh \"./hooks/agentcore-light.sh\"", "timeout": 5 }],
    "subagentStart":         [{ "command": "sh \"./hooks/agentcore-light.sh\"", "timeout": 5 }],
    "subagentStop":          [{ "command": "sh \"./hooks/agentcore-light.sh\"", "timeout": 5 }],
    "preCompact":            [{ "command": "sh \"./hooks/agentcore-light.sh\"", "timeout": 5 }],
    "stop":                  [{ "command": "sh \"./hooks/agentcore-light.sh\"", "timeout": 5 }],
    "afterAgentResponse":    [{ "command": "sh \"./hooks/agentcore-light.sh\"", "timeout": 5 }]
  }
}
HOOKS_EOF

echo "[install-remote] hooks 已安装到 ~/.cursor/hooks.json"
echo "[install-remote] queue worker 已放到 ~/.cursor/hooks/hook-queue-worker.py"
echo "[install-remote] 端口：$PORT（如需改，重装前 export AGENT_LIGHT_TUNNEL_PORT=<port>）"

# 启动 / 重启 queue worker
pkill -f "hook-queue-worker.py" 2>/dev/null || true
sleep 0.3

# systemd user 服务
mkdir -p "$HOME/.config/systemd/user"
cat > "$HOME/.config/systemd/user/agentcore-light-hook.service" <<'SVC_EOF'
[Unit]
Description=AgentCore-Light Cursor hook queue worker
After=default.target

[Service]
ExecStart=/usr/bin/python3 %h/.cursor/hooks/hook-queue-worker.py
Restart=on-failure
RestartSec=2
StandardOutput=append:%h/.cursor/hooks/queue-worker.log
StandardError=append:%h/.cursor/hooks/queue-worker.log

[Install]
WantedBy=default.target
SVC_EOF

# 【设计】长跑服务必须由 systemd user 托管（重启/开机自启/日志归集）。
# 不再回退到 nohup——在 Linux 上 nohup & disown 仍然会被父 shell 退出时
# 触发的信号带走（macOS 上更严重）。systemd 不可用时直接报错，让人工介入。
if ! command -v systemctl >/dev/null 2>&1; then
  echo "[install-remote] [ERR] systemctl not found — this host must have systemd for the worker to run reliably."
  echo "[install-remote] [ERR] 不再回退到 nohup（不可靠，会被信号带走）。"
  exit 1
fi

systemctl --user daemon-reload 2>/dev/null && \
  systemctl --user enable agentcore-light-hook.service 2>/dev/null && \
  systemctl --user restart agentcore-light-hook.service 2>/dev/null && \
  echo "[install-remote] 已用 systemd user 服务启动" || {
    echo "[install-remote] [ERR] systemctl --user 启动失败（lingering 没开？loginctl enable-linger $USER）"
    exit 1
  }

# ============================================================
# 日志轮转：worker.log / health.log 不能无限增长
# systemd 用 StandardOutput=append: 写日志，没有自动轮转。
# 用 logrotate（user-level）每天轮转，保留 7 天。
# ============================================================
if command -v logrotate >/dev/null 2>&1; then
  mkdir -p "$HOME/.config/logrotate"

  cat > "$HOME/.config/logrotate/agentcore-light" <<'LOGROTATE_EOF'
$HOME/.cursor/hooks/queue-worker.log $HOME/.cursor/hooks/health.log $HOME/.cursor/hooks/hook.log {
    daily
    rotate 7
    compress
    delaycompress
    missingok
    notifempty
    copytruncate
    su $USER $USER
}
LOGROTATE_EOF

  # 替换 $HOME / $USER 占位符（logrotate 配置里要绝对路径）
  sed -i "s|\$HOME|$HOME|g; s|\$USER|$USER|g" "$HOME/.config/logrotate/agentcore-light"

  # state 文件（权限 600，避免 logrotate warning）
  LOGROT_STATE="$HOME/.config/logrotate/agentcore-light.state"
  touch "$LOGROT_STATE"
  chmod 600 "$LOGROT_STATE"

  # 写一个 user-level cron（如果没装 systemd timer 的话）—— 用 ~/.local/bin/agent-light-logrotate.sh
  mkdir -p "$HOME/.local/bin"
  cat > "$HOME/.local/bin/agent-light-logrotate.sh" <<CRON_EOF
#!/bin/sh
# 每天由 cron 调用，轮转 agentcore-light 远程 worker 日志
logrotate -s "$LOGROT_STATE" "$HOME/.config/logrotate/agentcore-light" 2>&1 >>"$HOME/.cursor/hooks/logrotate.log"
CRON_EOF
  chmod +x "$HOME/.local/bin/agent-light-logrotate.sh"

  # 加 cron entry（如果不存在）
  (crontab -l 2>/dev/null | grep -v "agent-light-logrotate.sh"; echo "0 3 * * * $HOME/.local/bin/agent-light-logrotate.sh") | crontab -

  # 立刻跑一次确认配置没错
  logrotate -s "$LOGROT_STATE" "$HOME/.config/logrotate/agentcore-light" 2>&1 | head -5 || \
    echo "[install-remote] logrotate 测试失败（不致命，cron 会按天跑）"

  echo "[install-remote] 日志轮转已配置：每天 3:00 跑，保留 7 天压缩备份"
else
  echo "[install-remote] [WARN] 没装 logrotate，worker.log 会无限增长。建议 apt install logrotate"
fi

sleep 2
echo "---"
echo "[verify] 隧道连通性测试："
if curl -s --max-time 3 "http://127.0.0.1:${PORT}/api/status" | head -c 80; then
  echo
else
  echo "（隧道不通）"
fi
echo "[verify] queue worker 进程："
pgrep -af "hook-queue-worker.py" || echo "（未找到）"
echo "[verify] 心跳文件："
cat "$HOME/.cursor/hooks/heartbeat.txt" 2>/dev/null | head -10 || echo "（无）"
