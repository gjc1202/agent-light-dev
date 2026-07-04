#!/usr/bin/env python3
"""
检查 ESP32-C3 是否直插 Mac，还是经过外接 USB hub（如显示器 USB 口）。

【背景】
日常使用时 ESP32 通常插在显示器 USB 口上（方便），但烧录固件需要直插 Mac：
- 显示器 USB hub 通常供电不足，烧录中途可能掉电 → ESP32 进 download mode
- Hub 可能引入通信延迟，esptool 超时失败
- 已实测过：烧录失败导致 ESP32 卡死，需要按 BOOT+RST 救回

【判断方法】
用 ioreg 拿 USB 拓扑，找 ESP32（Vendor ID 0x303a）的 parent 链：
- 如果 parent 是 "USB2.0 Hub" / "USB3.0 Hub" / "Element Hub" 等外接 hub → 不安全
- 如果 parent 直接是 Apple XHCI controller（Mac 主控）→ 安全

输出：
- exit 0 + stdout "DIRECT"  : 直插 Mac，可以烧录
- exit 1 + stdout "HUB"     : 经过外接 hub，不能烧录，给详细路径
- exit 2 + stdout "MISSING" : 没找到 ESP32

用法：
  ./scripts/check-usb-direct.py            # 人读输出
  ./scripts/check-usb-direct.py --quiet    # 只输出 DIRECT/HUB/MISSING，给脚本用
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys


# ESP32-C3 USB Vendor ID (Espressif)
ESP_VID = 0x303a


def get_usb_tree() -> str:
    """Run ioreg and return full USB tree output."""
    try:
        out = subprocess.check_output(
            ["ioreg", "-p", "IOUSB", "-l", "-w", "0"],
            stderr=subprocess.DEVNULL,
        )
        return out.decode("utf-8", errors="replace")
    except Exception as e:
        print(f"[ERR] ioreg failed: {e}", file=sys.stderr)
        return ""


def parse_tree(text: str) -> tuple[list[dict], list[dict]]:
    """Parse ioreg tree into list of device dicts with parent relationships.

    Returns (all_devices, esp32_devices).
    Each device has: name, indent_level, location_id, vid, pid, product, raw_lines.
    """
    # Build a stack-based tree parser
    devices = []
    stack = []  # list of (indent_level, device_dict)

    for line in text.split("\n"):
        # ioreg indents with "  " and "|" characters to show ancestor lines.
        # e.g. "  | | | +-o USB JTAG/serial debug unit@01110000  <class IOUSBHostDevice..."
        # The "+" before "o" is the actual device; "|" are just ancestor markers.
        m = re.match(r"^([\s|]*)\+-o\s+([^@\s][^@]*?)@([0-9a-fA-F]+)\s+<class\s+IOUSBHostDevice", line)
        if not m:
            if stack:
                top = stack[-1][1]
                vm = re.search(r'"idVendor"\s*=\s*(\d+)', line)
                if vm:
                    top["vid"] = int(vm.group(1))
                pm = re.search(r'"idProduct"\s*=\s*(\d+)', line)
                if pm:
                    top["pid"] = int(pm.group(1))
                nm = re.search(r'"USB Product Name"\s*=\s*"([^"]+)"', line)
                if nm:
                    top["product"] = nm.group(1)
            continue

        # Indent depth = count of leading characters (space or "|") divided by 2
        # (each level is "  " or "| ")
        prefix = m.group(1)
        # Each ancestor level contributes 2 chars ("  " or "| ")
        indent = len(prefix) // 2
        name = m.group(2).strip()
        loc_str = m.group(3)
        try:
            loc = int(loc_str, 16)
        except ValueError:
            loc = 0

        dev = {
            "name": name,
            "indent": indent,
            "location_id": loc,
            "location_str": f"0x{loc:08x}",
            "vid": None,
            "pid": None,
            "product": name,
            "parent_chain": [],
        }

        # Pop stack until we find a parent (smaller indent)
        while stack and stack[-1][0] >= indent:
            stack.pop()

        # Parent chain = everything remaining on stack (root → direct parent)
        for _, parent_dev in stack:
            dev["parent_chain"].append(parent_dev)

        stack.append((indent, dev))
        devices.append(dev)

    esp_devices = [d for d in devices if d["vid"] == ESP_VID]
    return devices, esp_devices


def classify(esp_dev: dict) -> tuple[str, str]:
    """Classify connection as DIRECT or HUB, return (status, reason)."""
    if not esp_dev["parent_chain"]:
        return "UNKNOWN", "no parent in tree"

    # Walk parent chain from ESP32 outward
    # We expect: Apple XHCI controller at the root, possibly with hubs in between
    chain_names = [p["product"] or p["name"] for p in esp_dev["parent_chain"]]

    # Find immediate parent (closest in chain)
    immediate_parent = esp_dev["parent_chain"][-1]
    parent_name = immediate_parent["product"] or immediate_parent["name"]

    # Hubs we consider "external" (i.e., not safe for firmware flashing)
    HUB_KEYWORDS = (
        "USB2.0 Hub", "USB3.0 Hub", "USB3.1 Hub",
        "Element Hub", "Element USB",
        "BillBoard",
        "Multiport Adapter",  # USB-C Digital AV Multiport Adapter
        "Hub",
    )

    # Apple's own vendor ID — internal Mac root hubs use this.
    # Only consider non-Apple hubs as "external".
    APPLE_VID = 0x05ac  # 1452

    # Check if any parent (except the Mac XHCI root) is an external hub
    # The Mac XHCI controller name contains "AppleT8112" / "AppleXHCI" etc.
    for parent in esp_dev["parent_chain"]:
        pname = parent["product"] or parent["name"]
        pvid = parent.get("vid")

        # Apple XHCI controller (USB host controller driver) — fine
        if "Apple" in pname and ("XHCI" in pname or "USBXHCI" in pname):
            continue

        # Apple-VID hub = Mac internal root hub (built into the laptop) — fine
        if pvid == APPLE_VID:
            continue

        # Anything else that looks like a hub — NOT fine
        if any(kw in pname for kw in HUB_KEYWORDS):
            return "HUB", f"经过外接 hub: '{pname}' (VID=0x{pvid:04x})"
        # Other non-Apple device without known keyword — also flag
        if not pname.startswith("Root"):
            return "HUB", f"经过非 Mac 直连设备: '{pname}' (VID=0x{pvid:04x})"

    return "DIRECT", "直插 Mac USB 口（中间的 hub 都是 Mac 内置的）"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--quiet", action="store_true", help="只输出 DIRECT/HUB/MISSING")
    args = ap.parse_args()

    text = get_usb_tree()
    if not text:
        print("MISSING")
        return 2

    _, esp_devices = parse_tree(text)

    if not esp_devices:
        if args.quiet:
            print("MISSING")
        else:
            print("[FAIL] 没找到 ESP32（Vendor ID 0x303a）— 没插好 / 没上电 / 卡在 download mode")
        return 2

    esp = esp_devices[0]
    status, reason = classify(esp)

    if args.quiet:
        print(status)
        return 0 if status == "DIRECT" else 1

    # Human-readable output
    print(f"ESP32: {esp['product']}")
    print(f"  location: {esp['location_str']}")
    print(f"  VID/PID:  0x{esp['vid']:04x} / 0x{esp['pid']:04x}")
    print()
    print("USB 拓扑（从 Mac 根 hub 到 ESP32）:")
    for i, parent in enumerate(esp["parent_chain"]):
        marker = "→" if i == len(esp["parent_chain"]) - 1 else " "
        pname = parent["product"] or parent["name"]
        pvid = parent.get("vid")
        vid_str = f" (VID=0x{pvid:04x})" if pvid else ""
        print(f"  {i+1}. {pname}{vid_str}")
    print(f"  {marker} {esp['product']}  ← ESP32")
    print()

    if status == "DIRECT":
        print("✅ DIRECT — 直插 Mac，可以烧录固件")
        return 0
    elif status == "HUB":
        print(f"❌ HUB — {reason}")
        print()
        print("烧录固件前必须把 ESP32 拔下来直插 Mac USB 口，原因：")
        print("  1. 显示器/外接 hub 供电可能不稳，烧录中途掉电会让 ESP32 卡在 download mode")
        print("  2. hub 引入通信延迟，esptool 可能超时失败")
        print("  3. 已实测过烧录失败需要按 BOOT+RST 物理救板")
        return 1
    else:
        print(f"? UNKNOWN — {reason}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
