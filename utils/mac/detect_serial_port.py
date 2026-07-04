#!/usr/bin/env python3
import subprocess
import sys
from pathlib import Path

from serial.tools import list_ports

ROOT = Path(__file__).resolve().parent
PYTHON = ROOT / ".venv" / "bin" / "python"

PROBE_SCRIPT = """
import sys
import time
import serial

device = sys.argv[1]
ser = serial.Serial(device, 115200, timeout=0.5, write_timeout=2)
time.sleep(1.0)
boot = ser.read(512).decode("utf-8", errors="replace")
if "waiting for download" in boot.lower():
    raise SystemExit(1)
ser.write(b"thinking\\r\\n")
ser.flush()
time.sleep(0.8)
resp = ser.read(512).decode("utf-8", errors="replace")
ser.close()
if "State changed" in resp or "traffic light ready" in boot:
    raise SystemExit(0)
raise SystemExit(1)
"""


def is_esp32_port(port):
    text = " ".join(
        [
            port.device or "",
            port.description or "",
            port.manufacturer or "",
            port.hwid or "",
        ]
    ).lower()
    if "1a86" in text or "ch340" in text or "ch341" in text:
        return False
    return (
        "303a" in text
        or "espressif" in text
        or "esp32" in text
        or "jtag" in text
        or "usb jtag" in text
    )


def probe_port(device):
    try:
        result = subprocess.run(
            [str(PYTHON), "-c", PROBE_SCRIPT, device],
            timeout=6,
            capture_output=True,
            text=True,
        )
        return result.returncode == 0
    except Exception:
        return False


def main():
    ports = [port for port in list_ports.comports() if is_esp32_port(port)]
    for port in ports:
        if probe_port(port.device):
            print(port.device)
            return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
