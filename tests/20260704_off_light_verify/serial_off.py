"""
真灯验证 v2：高频串口发 off，对抗生产 BLE bridge 的 busy 覆盖。
策略：每 50ms 发一次 off，持续 3 秒，看 timeline 是否出现 state: X -> off。
"""
import serial
import time
import sys

PORT = '/dev/cu.usbmodem13201'
BAUD = 115200
DURATION_S = 3.0
INTERVAL_MS = 50

ser = serial.Serial(PORT, BAUD, timeout=0.05)
time.sleep(0.3)
if ser.in_waiting:
    ser.read(ser.in_waiting)

print(f"高频发 off {DURATION_S}s（每 {INTERVAL_MS}ms 一次）...")
end = time.time() + DURATION_S
writes = 0
buf = ""
while time.time() < end:
    ser.write(b'{"status":"off"}\n')
    ser.flush()
    writes += 1
    time.sleep(INTERVAL_MS / 1000.0)
    # 读固件回显
    data = ser.read(512)
    if data:
        buf += data.decode('utf-8', errors='replace')

ser.close()

print(f"共发 {writes} 次 off")
print()
print("=== 固件回显（最后 1500 字符）===")
print(buf[-1500:] if buf else "(no output)")
print()

# 检查是否出现过 state: X -> off
if "-> off" in buf or "state: off" in buf:
    print("✅ 固件层确实能进 STATE_OFF（'-> off' 出现在回显里）")
    sys.exit(0)
else:
    print("❌ 固件从未进入 off（生产 bridge 的 busy 覆盖太快）")
    print("   → 需要临时停 bridge 才能纯验证")
    sys.exit(1)
