#!/usr/bin/env python3
"""
SSH 远程日志聚合 — Mac 端 debug 工具。

从所有配置过的 SSH 主机拉 AgentCore-Light worker 的日志和状态，
聚合打印到 stdout（或追加到 /tmp/agent-light-timeline.log）。

【用途】当用户在 Mac 说「灯不对，可能 SSH 端挂了」时，debug agent
跑这个脚本，立刻拿到所有 Linux 主机的 worker 状态、心跳、health.log、
queue-worker.log 末尾，不需要手动 ssh 一个个查。

【用法】
  python3 scripts/pull-ssh-logs.py                  # 列出所有 SSH host 状态
  python3 scripts/pull-ssh-logs.py --host gjc_2031  # 详细看某台
  python3 scripts/pull-ssh-logs.py --append-timeline # 拉到后追加到 timeline.log
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

DEFAULT_SSH_TIMEOUT = 8  # 秒
SSH_CONFIG = Path.home() / ".ssh" / "config"
TIMELINE_LOG = Path("/tmp/agent-light-timeline.log")

# 每个 SSH host 上要拉的信息
REMOTE_CHECKS = [
    ("heartbeat", "cat ~/.cursor/hooks/heartbeat.txt 2>/dev/null"),
    ("health",    "tail -20 ~/.cursor/hooks/health.log 2>/dev/null"),
    ("worker",    "tail -30 ~/.cursor/hooks/queue-worker.log 2>/dev/null"),
    ("queue",     "ls ~/.cursor/hooks/queue 2>/dev/null | wc -l"),
    ("systemd",   "systemctl --user is-active agentcore-light-hook.service 2>&1"),
    ("tunnel",    "curl -s --max-time 3 http://127.0.0.1:18787/api/status 2>&1 | head -c 100"),
]


def parse_ssh_hosts() -> list[str]:
    """Parse ~/.ssh/config to find all Host entries (skip wildcards)."""
    if not SSH_CONFIG.exists():
        return []
    hosts = []
    for line in SSH_CONFIG.read_text().splitlines():
        m = re.match(r"^\s*Host\s+(.+)$", line, re.IGNORECASE)
        if not m:
            continue
        for h in m.group(1).split():
            if not any(c in h for c in "*?!") and h not in hosts:
                hosts.append(h)
    return hosts


def ssh_run(host: str, cmd: str, timeout: int = DEFAULT_SSH_TIMEOUT) -> tuple[int, str]:
    """Run a command via SSH, return (exit_code, output)."""
    try:
        r = subprocess.run(
            ["ssh", "-o", f"ConnectTimeout={timeout}", "-o", "BatchMode=yes",
             "-o", "StrictHostKeyChecking=accept-new", host, cmd],
            capture_output=True, text=True, timeout=timeout + 2,
        )
        return r.returncode, (r.stdout + r.stderr).strip()
    except subprocess.TimeoutExpired:
        return 124, "(SSH timeout)"
    except Exception as e:
        return 1, f"(SSH error: {e})"


def pull_host(host: str, verbose: bool = False) -> dict:
    """Pull all checks from one host. Returns dict of results."""
    results = {"host": host, "checks": {}, "reachable": False}
    rc, _ = ssh_run(host, "echo OK", timeout=5)
    if rc != 0:
        results["error"] = "unreachable"
        return results
    results["reachable"] = True

    for name, cmd in REMOTE_CHECKS:
        rc, out = ssh_run(host, cmd)
        results["checks"][name] = out if rc == 0 else f"(failed: rc={rc})"
    return results


def format_compact(r: dict) -> str:
    """One-line compact summary per host."""
    if not r.get("reachable"):
        return f"  {r['host']:20} ❌ UNREACHABLE"
    h = r.get("checks", {})
    systemd = h.get("systemd", "?").strip()
    queue = h.get("queue", "?").strip()
    # parse heartbeat JSON for status + age
    hb = h.get("heartbeat", "")
    hb_status = "?"
    hb_age = "?"
    if hb.startswith("{"):
        try:
            import json
            d = json.loads(hb)
            hb_status = d.get("status", "?")
            hb_at = d.get("at", "")
            # parse age
            if hb_at:
                try:
                    hb_dt = datetime.fromisoformat(hb_at)
                    hb_age = f"{(datetime.now(hb_dt.tzinfo) - hb_dt).total_seconds():.0f}s"
                except Exception:
                    pass
        except Exception:
            pass
    tunnel = h.get("tunnel", "?")[:60]
    systemd_icon = "✓" if systemd == "active" else "✗"
    return (f"  {r['host']:20} {systemd_icon} systemd={systemd:8} | "
            f"hb={hb_status}/{hb_age} | queue={queue} | tunnel={tunnel[:40]}")


def format_verbose(r: dict) -> None:
    """Multi-line verbose output per host."""
    print(f"\n=== {r['host']} ===")
    if not r.get("reachable"):
        print("  ❌ UNREACHABLE")
        return
    for name, out in r.get("checks", {}).items():
        print(f"\n--- {name} ---")
        print(out if out else "(empty)")


def append_to_timeline(r: dict) -> None:
    """Append results to /tmp/agent-light-timeline.log."""
    if not TIMELINE_LOG.exists():
        return
    ts = datetime.now().strftime("%H:%M:%S")
    line = format_compact(r).strip()
    with TIMELINE_LOG.open("a") as f:
        f.write(f"[{ts}] [SSH] {line}\n")
    # 如果 verbose，把详细日志也写进去
    if r.get("reachable"):
        for name in ("health", "worker"):
            out = r.get("checks", {}).get(name, "")
            if out:
                for sub in out.split("\n")[-5:]:  # 取最后 5 行
                    if sub.strip():
                        f.write(f"[{ts}] [SSH-{name.upper()}] [{r['host']}] {sub.strip()}\n")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", help="只查某个 host（默认查 ~/.ssh/config 里所有）")
    ap.add_argument("-v", "--verbose", action="store_true", help="详细输出（含完整日志）")
    ap.add_argument("--append-timeline", action="store_true",
                    help="把结果追加到 /tmp/agent-light-timeline.log（给 debug agent 用）")
    args = ap.parse_args()

    hosts = [args.host] if args.host else parse_ssh_hosts()
    if not hosts:
        print("[ERR] 没找到 SSH host（~/.ssh/config 没配置或不存在）", file=sys.stderr)
        return 1

    print(f"=== pulling from {len(hosts)} SSH host(s): {', '.join(hosts)} ===")
    for host in hosts:
        r = pull_host(host)
        if args.verbose:
            format_verbose(r)
        else:
            print(format_compact(r))
        if args.append_timeline:
            append_to_timeline(r)

    return 0


if __name__ == "__main__":
    sys.exit(main())
