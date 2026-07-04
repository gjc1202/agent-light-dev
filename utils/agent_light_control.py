import sys
import time

import serial
from serial.tools import list_ports


BAUD_RATE = 115200

MENU_ITEMS = {
    "1": ("IDLE", "idle"),
    "2": ("THINKING", "thinking"),
    "3": ("BUSY", "busy"),
    "4": ("SUCCESS", "success"),
    "5": ("WAIT_CONFIRM", "wait_confirm"),
    "6": ("CONFIRM", "confirm"),
    "7": ("WAITING", "waiting"),
    "8": ("WAIT", "wait"),
    "9": ("ERROR", "error"),
}


def score_port(port):
    text = " ".join(
        [
            port.device or "",
            port.description or "",
            port.manufacturer or "",
            port.hwid or "",
        ]
    ).lower()

    score = 0
    if "1a86" in text or "ch340" in text or "ch341" in text:
        return -1000
    if "espressif" in text or "303a" in text:
        score += 200
    if "esp32" in text:
        score += 100
    if "jtag" in text:
        score += 80
    if "usbmodem" in text:
        score += 60
    if "usb" in text:
        score += 20
    if "bluetooth" in text or "bth" in text:
        score -= 100
    return score


def detect_port():
    ports = list(list_ports.comports())
    if not ports:
        return None

    ranked = sorted(ports, key=score_port, reverse=True)
    if score_port(ranked[0]) > 0:
        return ranked[0].device

    return ranked[0].device


def open_serial(port):
    ser = serial.Serial(port=port, baudrate=BAUD_RATE, timeout=0.3, write_timeout=1)
    time.sleep(0.5)

    # 清掉上电或复位时可能残留的启动信息，避免影响交互显示。
    try:
        waiting = ser.in_waiting
        if waiting:
            ser.read(waiting)
    except serial.SerialException:
        pass

    return ser


def print_menu():
    print()
    print("ESP32-C3 AI Agent 状态灯控制")
    for key, (label, _) in MENU_ITEMS.items():
        print(f"{key} {label}")
    print("q 退出")


def main():
    port = detect_port()
    if not port:
        print("未检测到可用串口。")
        sys.exit(1)

    try:
        ser = open_serial(port)
    except Exception as exc:
        print(f"打开串口失败：{port} - {exc}")
        sys.exit(1)

    print(f"已连接 {port}，波特率 {BAUD_RATE}")

    try:
        while True:
            print_menu()
            choice = input("请输入选项：").strip().lower()

            if choice == "q":
                print("已退出。")
                break

            if choice not in MENU_ITEMS:
                print("无效选项，请重新输入。")
                continue

            label, command = MENU_ITEMS[choice]
            ser.write((command + "\n").encode("utf-8"))
            ser.flush()
            print(f"已发送：{label}")
    finally:
        ser.close()


if __name__ == "__main__":
    main()
