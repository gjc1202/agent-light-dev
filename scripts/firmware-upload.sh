#!/usr/bin/env bash
# firmware upload wrapper：upload 前停 timeline logger（释放串口），
# upload 后重启 timeline logger。
#
# 【Safety gate】upload 前必须通过 USB 直插检测：
#   1. 调用 check-usb-direct.py 验证 ESP32 直插 Mac，不经过任何 hub
#   2. 即使 --force 强制烧录，也会再检测一次（用户口头确认不够，必须客观证据）
#   3. 原因：显示器 USB hub 供电/通信不稳，烧录失败可能让 ESP32 卡死，
#      需要 BOOT+RST 物理救板（实测发生过）
#
# 用法：
#   ./scripts/firmware-upload.sh                 # 默认 /dev/cu.usbmodem*
#   ./scripts/firmware-upload.sh /dev/cu.X       # 指定端口
#   ./scripts/firmware-upload.sh --no-build      # 跳过编译直接 upload
#   ./scripts/firmware-upload.sh --force         # 用户已确认要烧，跳过 USB 检测 prompt
#                                                # （仍然会跑检测，只是不再 prompt 确认）

set -euo pipefail

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
ROOT="$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)"
FIRMWARE_DIR="$ROOT/firmware"
PYTHON="$ROOT/.venv/bin/python"
TIMELINE_LABEL="com.user.agentcore-light.timeline"
USB_CHECK="$SCRIPT_DIR/check-usb-direct.py"

# 选端口
PORT=""
NO_BUILD=0
FORCE=0
for arg in "$@"; do
  case "$arg" in
    --no-build) NO_BUILD=1 ;;
    --force) FORCE=1 ;;
    --help|-h)
      echo "Usage: $0 [--no-build] [--force] [/dev/cu.usbmodem*]"
      echo ""
      echo "Safety: upload 前必须 ESP32 直插 Mac（不经过显示器 hub）。"
      echo "        --force 跳过 prompt 但仍然会做检测并打印警告。"
      exit 0
      ;;
    *) PORT="$arg" ;;
  esac
done

if [[ -z "$PORT" ]]; then
  PORT=$(ls /dev/cu.usbmodem* 2>/dev/null | head -1 || true)
  if [[ -z "$PORT" ]]; then
    echo "[ERR] 没找到 /dev/cu.usbmodem* — ESP32 没插好或不在 download mode" >&2
    exit 1
  fi
fi

echo "============================================================"
echo "firmware-upload: port=$PORT no_build=$NO_BUILD force=$FORCE"
echo "============================================================"

# ============================================================
# Safety gate: USB 直插检测（不可绕过，即使 --force）
# ============================================================
echo ""
echo "[safety] 检查 ESP32 是否直插 Mac（不经过 hub）..."
# 注意：
# 1. 不能在 $() 里用 || echo，否则 $? 拿到的是 echo 的退出码（永远是 0），
#    检测失败会被悄悄忽略。
# 2. 由于本脚本 set -e，需要在赋值时临时关掉 errexit，否则 USB_CHECK
#    exit 1 时整个脚本立刻挂掉，没机会打印警告。
# 分两步走：先抓输出 + exit code，再看 exit code 决定行为。
set +e
USB_STATUS=$("$PYTHON" "$USB_CHECK" --quiet 2>&1)
USB_EXIT=$?
set -e
USB_STATUS=$(echo "$USB_STATUS" | tail -1)

if [[ "$USB_EXIT" != "0" ]]; then
  echo ""
  "$PYTHON" "$USB_CHECK" 2>&1 | tail -15
  echo ""
  if [[ "$FORCE" == "1" ]]; then
    echo "[safety] ⚠️  --force 模式，但 USB 检测未通过：$USB_STATUS"
    echo "[safety] ⚠️  继续烧录可能导致 ESP32 卡死，需要 BOOT+RST 物理救板。"
    echo "[safety] ⚠️  将在 5 秒后开始烧录（Ctrl-C 取消）..."
    sleep 5
  else
    echo "[safety] ❌ USB 检测未通过：$USB_STATUS"
    echo "[safety] ❌ 拒绝烧录。请把 ESP32 拔下直插 Mac USB 口，然后重跑本脚本。"
    echo "[safety] ❌ 如果绝对确定要烧（例如正在调试 hub 兼容性问题），用 --force。"
    exit 1
  fi
else
  echo "[safety] ✓ ESP32 直插 Mac，可以安全烧录"
fi

# ============================================================
# Step 1: 暂停 timeline logger
# ============================================================
echo ""
echo "[1/4] 暂停 timeline logger（释放串口）"
TIMELINE_WAS_RUNNING=0
if launchctl print "gui/$UID/$TIMELINE_LABEL" >/dev/null 2>&1; then
  TIMELINE_WAS_RUNNING=1
  launchctl bootout "gui/$UID/$TIMELINE_LABEL" 2>&1 | head -3 || true
  sleep 1
  echo "      已暂停"
else
  echo "      timeline logger 没在跑，跳过"
fi

# Step 2: 编译（可选）
if [[ "$NO_BUILD" == "0" ]]; then
  echo ""
  echo "[2/4] 编译固件"
  (cd "$FIRMWARE_DIR" && "$PYTHON" -m platformio run 2>&1) | tail -8
else
  echo ""
  echo "[2/4] 跳过编译"
fi

# Step 3: upload
echo ""
echo "[3/4] 烧录到 $PORT"
(cd "$FIRMWARE_DIR" && "$PYTHON" -m platformio run -t upload --upload-port "$PORT" 2>&1) | tail -8

# Step 4: 恢复 timeline logger
if [[ "$TIMELINE_WAS_RUNNING" == "1" ]]; then
  echo ""
  echo "[4/4] 重启 timeline logger"
  PLIST="$HOME/Library/LaunchAgents/$TIMELINE_LABEL.plist"
  if [[ -f "$PLIST" ]]; then
    launchctl bootstrap "gui/$UID" "$PLIST" 2>&1 | head -3 || true
    echo "      已恢复"
  else
    echo "      [WARN] plist 不存在，timeline 没起来；请手动跑 autostart-install.sh"
  fi
fi

echo ""
echo "============================================================"
echo "✓ 完成。等 ESP32 boot（~10s）+ BLE 重连后恢复正常。"
echo "============================================================"
