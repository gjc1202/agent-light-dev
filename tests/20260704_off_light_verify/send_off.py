"""
真灯验证：直接通过 BLE 给 ESP32 发 off 命令，验证灯是否真灭。

这个脚本绕过 web server，直接连 BLE 设备写 "off"。
不与生产 bridge 冲突——因为生产 bridge 用 instance lock 锁住端口 37638，
但 BLE 本身允许多客户端连接同一设备（ESP32-C3 NimBLE 支持多连接）。

如果不行（BLE 单连接限制），就先停生产 bridge 再发。
"""
import asyncio
import json
import sys
import time

from bleak import BleakClient, BleakScanner

DEVICE_NAMES = ("AgentCore-Light", "SignalLight-C3")
BLE_CHAR_UUID = "12345678-1234-5678-1234-56789abcdef1"


async def send_command(command: str, hold_seconds: float = 5.0):
    print(f"[1/4] 扫描 BLE 设备...")
    devices = await BleakScanner.discover(timeout=8.0)
    target = None
    for d in devices:
        name = d.name or ""
        if any(token in name for token in DEVICE_NAMES):
            target = d
            print(f"      找到 {name} ({d.address})")
            break
    if target is None:
        print("      ❌ 未找到 AgentCore-Light BLE 设备")
        return 1

    print(f"[2/4] 连接 BLE...")
    try:
        async with BleakClient(target.address, timeout=20.0) as client:
            print(f"      已连接")
            for cmd in ["off"]:  # 先发 idle 再发 off，让灯有可见切换
                print(f"[3/4] 发送命令: {cmd!r}")
                payload = json.dumps({"status": cmd}).encode("utf-8")
                await client.write_gatt_char(BLE_CHAR_UUID, payload, response=False)
                print(f"      已写入")
                print(f"[4/4] 保持 {hold_seconds}s 让你看灯效...")
                await asyncio.sleep(hold_seconds)
                # 读一下固件回显（如果写了日志）
            print("\n✅ 验证完成：发送 'off' 命令后，三灯应当全灭。")
            print("   若灯仍亮 → 固件层 bug；若灯灭 → 修复链路完整。")
            return 0
    except Exception as exc:
        print(f"      ❌ BLE 连接/写入失败：{exc}")
        print("      → 可能生产 bridge 占着 BLE，停掉它再试：")
        print("        launchctl stop com.user.agentcore-light.bridge")
        return 2


def main():
    return asyncio.run(send_command("off", hold_seconds=4.0))


if __name__ == "__main__":
    sys.exit(main())
