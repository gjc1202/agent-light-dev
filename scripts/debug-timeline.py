#!/usr/bin/env python3
"""
Agent light timeline logger.

聚合三个数据源到 /tmp/agent-light-timeline.log，每行带秒级时间戳。
- [HOOK]  web hook 队列收到的原始事件（来自 hook-queue-worker log）
- [API]   web server 聚合后的 device_status（每 2s 拉一次）
- [BLE]   bridge 发给 ESP32 的命令（来自 bridge log）
- [ESP32] ESP32 串口回显（来自 /dev/cu.usbmodem*）

用法：
  直接跑：./scripts/debug-timeline.py
  也可被 launchd / nohup 拉起。
"""
from __future__ import annotations

import glob
import os
import subprocess
import sys
import threading
import time
import urllib.request
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOG = Path("/tmp/agent-light-timeline.log")
SOURCES = {
    "HOOK": "/tmp/agentcore-light-hook-queue.log",
    "BLE": "/tmp/agentcore-light-bridge.log",
}
API_URL = "http://127.0.0.1:8787/api/status"
ESP32_PORT_GLOB = "/dev/cu.usbmodem*"

_lock = threading.Lock()
_stop = threading.Event()


def ts() -> str:
    return datetime.now().strftime("%H:%M:%S")


def write(line: str) -> None:
    with _lock:
        with LOG.open("a") as f:
            f.write(line + "\n")


def tail_file(source: str, path: str) -> None:
    """Tail a file forever, prefixing each line."""
    try:
        with open(path) as f:
            f.seek(0, 2)  # end of file
            while not _stop.is_set():
                line = f.readline()
                if not line:
                    time.sleep(0.2)
                    continue
                line = line.rstrip()
                if line:
                    write(f"[{ts()}] [{source}] {line}")
    except Exception as e:
        write(f"[{ts()}] [{source}] (tail failed: {e})")


def poll_api() -> None:
    """Poll API status every 2s."""
    while not _stop.is_set():
        try:
            req = urllib.request.Request(API_URL, headers={"Cache-Control": "no-cache"})
            with urllib.request.urlopen(req, timeout=2) as resp:
                import json
                d = json.loads(resp.read().decode("utf-8"))
            sessions = d.get("sessions", [])
            sess_info = "; ".join(
                f"{s.get('event')}/age={s.get('age_s', 0)}s/deg={s.get('degraded', False)}"
                for s in sessions
            )
            write(
                f"[{ts()}] [API] {d.get('device_status', '?')} | "
                f"event={d.get('winner_event', '?')} | effect={d.get('effect_id', '?')} | "
                f"{sess_info}"
            )
        except Exception as e:
            write(f"[{ts()}] [API] (poll failed: {e})")
        for _ in range(20):  # 2s, but interruptible
            if _stop.is_set():
                return
            time.sleep(0.1)


def poll_esp32() -> None:
    """Open ESP32 serial and log every line."""
    try:
        import serial
    except ImportError:
        write(f"[{ts()}] [ESP32] pyserial not installed")
        return

    while not _stop.is_set():
        ports = glob.glob(ESP32_PORT_GLOB)
        if not ports:
            time.sleep(1)
            continue
        port = ports[0]
        try:
            ser = serial.Serial(port, 115200, timeout=0.3)
            write(f"[{ts()}] [ESP32] connected to {port}")
            while not _stop.is_set():
                data = ser.read(256)
                if data:
                    for line in data.decode("utf-8", errors="replace").split("\n"):
                        if line.strip():
                            write(f"[{ts()}] [ESP32] {line.strip()}")
            ser.close()
        except Exception as e:
            write(f"[{ts()}] [ESP32] (serial error: {e}, retrying in 2s)")
            time.sleep(2)


def main() -> int:
    LOG.parent.mkdir(parents=True, exist_ok=True)
    # Truncate on start
    with LOG.open("w") as f:
        f.write(f"=== timeline started {datetime.now().isoformat()} ===\n")

    write(f"[{ts()}] [SYS] timeline logger started, pid={os.getpid()}")

    threads = []
    for src, path in SOURCES.items():
        if os.path.exists(path):
            t = threading.Thread(target=tail_file, args=(src, path), daemon=True)
            t.start()
            threads.append(t)
        else:
            write(f"[{ts()}] [SYS] {src} source not found: {path}")

    for fn, name in [(poll_api, "API"), (poll_esp32, "ESP32")]:
        t = threading.Thread(target=fn, daemon=True)
        t.start()
        threads.append(t)

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        write(f"[{ts()}] [SYS] timeline logger stopping")
        _stop.set()
    return 0


if __name__ == "__main__":
    sys.exit(main())
