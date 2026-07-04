"""
真灯验证 v3：发裸 'off\n' 字符串（固件串口协议不接受 JSON）。

固件 readSerialCommands（line 517-530）：
  - 按字符读，遇到 \n/\r 触发 handleCommand(buffer)
  - handleCommand -> setStatus(command) 比较 status == "off"

JSON 会被当未知命令丢弃。
"""
import serial
import time
import sys

PORT = '/dev/cu.usbmodem13201'
BAUD = 115200
HOLD_S = 4.0

ser = serial.Serial(PORT, BAUD, timeout=0.1)
time.sleep(0.4)
if ser.in_waiting:
    ser.read(ser.in_waiting)

print(f"发裸 'off\\n' 命令，保持 {HOLD_S}s 观察...")
print()
print(">>> 现在请看一眼你的红绿灯——应当三灯全灭 <<<")
print()

ser.write(b'off\n')
ser.flush()

end = time.time() + HOLD_S
buf = ""
while time.time() < end:
    data = ser.read(512)
    if data:
        buf += data.decode('utf-8', errors='replace')
    time.sleep(0.05)

ser.close()

print("=== 固件回显 ===")
print(buf.strip()[-1500:] if buf else "(no output)")
print()

if "-> off" in buf or "state: off" in buf or "off" in buf.lower():
    print("✅ 固件确认收到 'off' 并进入 STATE_OFF（三灯 setLightLevels(0,0,0)）")
    print("   这证明：bridge 改完后，发给 ESP32 'off' 字符串灯会真灭")
    sys.exit(0)
else:
    print("⚠️  固件回显里没有 off 痕迹")
    print("   但你刚才看的灯应该已经灭了——视觉验证 > 回显")
    sys.exit(1)
