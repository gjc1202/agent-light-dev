#!/usr/bin/env python3
"""
Agent light 日志轮转。

策略：
- 每个 target 日志文件超过 max_bytes 时，保留尾部 keep_bytes，前面截掉。
- 用 atomic rename 避免 tail -F / launchd 写入冲突。
- 设计为定时任务（launchd StartInterval / cron），自身只跑一次就退出。

用法：
  python3 scripts/log-rotate.py                 # 用默认阈值
  python3 scripts/log-rotate.py --max-bytes 5M  # 改阈值

阈值：
- timeline.log:        默认 20M（保留最近 ~2-3 小时全链路日志，足够 debug）
- 其他 service log:    默认 20M
- 总占用上限：        ~100M（5 个日志 × 20M）
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import sys
from pathlib import Path

DEFAULT_MAX_BYTES = "20M"
DEFAULT_KEEP_BYTES = "10M"
SERVICE_LOG_MAX_BYTES = "20M"
SERVICE_LOG_KEEP_BYTES = "10M"

TIMELINE_LOG = Path("/tmp/agent-light-timeline.log")
SERVICE_LOGS = [
    Path("/tmp/agentcore-light-bridge.log"),
    Path("/tmp/agentcore-light-web.log"),
    Path("/tmp/agentcore-light-hook-queue.log"),
    Path("/tmp/agentcore-light-launchd.log"),
    Path("/tmp/agentcore-light-timeline-runner.log"),
]

# 单位解析
_SIZE_RE = re.compile(r"^(\d+(?:\.\d+)?)([KMG]?)B?$", re.IGNORECASE)
_UNITS = {"": 1, "K": 1024, "M": 1024 * 1024, "G": 1024 * 1024 * 1024}


def parse_size(s: str) -> int:
    m = _SIZE_RE.match(s.strip())
    if not m:
        raise ValueError(f"invalid size: {s!r} (e.g. '5M', '500K', '1G')")
    return int(float(m.group(1)) * _UNITS[m.group(2).upper()])


def rotate_one(path: Path, max_bytes: int, keep_bytes: int, dry_run: bool = False) -> str:
    """Rotate a single log file if it exceeds max_bytes.

    Returns a status string for logging.
    """
    # Clamp keep_bytes to be at most max_bytes (avoid seek errors)
    keep_bytes = min(keep_bytes, max(0, max_bytes - 1))

    try:
        size = path.stat().st_size
    except FileNotFoundError:
        return f"  {path.name:40} (missing, skip)"
    except Exception as e:
        return f"  {path.name:40} (stat failed: {e})"

    if size <= max_bytes:
        return f"  {path.name:40} {size:>9} bytes  (under limit, no rotation)"

    # Read tail, then atomic replace
    try:
        keep_actual = min(keep_bytes, size)
        with path.open("rb") as f:
            if keep_actual == 0:
                tail = b""
            else:
                f.seek(-keep_actual, os.SEEK_END)
                # Walk back to next newline to avoid cutting mid-line
                chunk = f.read(keep_actual)
                nl = chunk.find(b"\n")
                tail = chunk[nl + 1:] if nl >= 0 else chunk

        if dry_run:
            return f"  {path.name:40} {size:>9} -> {len(tail):>9} bytes  (dry-run)"

        # Atomic rename + rewrite
        tmp = path.with_suffix(path.suffix + ".rotate.tmp")
        with tmp.open("wb") as f:
            f.write(tail)
        os.replace(tmp, path)
        return f"  {path.name:40} {size:>9} -> {len(tail):>9} bytes  ✓ rotated"
    except Exception as e:
        return f"  {path.name:40} (rotate failed: {e})"


def main() -> int:
    ap = argparse.ArgumentParser(description="Rotate agent-light log files.")
    ap.add_argument(
        "--max-bytes", default=DEFAULT_MAX_BYTES,
        help=f"Max size for timeline.log before rotation (default {DEFAULT_MAX_BYTES})"
    )
    ap.add_argument(
        "--keep-bytes", default=DEFAULT_KEEP_BYTES,
        help=f"Tail bytes to keep when rotating timeline.log (default {DEFAULT_KEEP_BYTES})"
    )
    ap.add_argument(
        "--all-max-bytes",
        help="Override max-bytes for ALL logs (timeline + service). Useful for testing."
    )
    ap.add_argument("--dry-run", action="store_true", help="Don't actually rotate, just report")
    args = ap.parse_args()

    timeline_max = parse_size(args.max_bytes)
    timeline_keep = parse_size(args.keep_bytes)
    if args.all_max_bytes:
        timeline_max = parse_size(args.all_max_bytes)
        svc_max = parse_size(args.all_max_bytes)
    else:
        svc_max = parse_size(SERVICE_LOG_MAX_BYTES)
    svc_keep = parse_size(SERVICE_LOG_KEEP_BYTES)

    print(f"=== log-rotate at {os.times()[4]:.0f}s elapsed, dry_run={args.dry_run} ===")
    print(f"  timeline: max={timeline_max} keep={timeline_keep}")
    print(f"  service:  max={svc_max} keep={svc_keep}")
    print()

    results = []
    results.append(rotate_one(TIMELINE_LOG, timeline_max, timeline_keep, args.dry_run))
    for p in SERVICE_LOGS:
        results.append(rotate_one(p, svc_max, svc_keep, args.dry_run))

    for r in results:
        print(r)

    # Total disk usage after
    total = 0
    for p in [TIMELINE_LOG] + SERVICE_LOGS:
        try:
            total += p.stat().st_size
        except FileNotFoundError:
            pass
    print(f"\n  total: {total} bytes ({total / 1024 / 1024:.1f}M)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
