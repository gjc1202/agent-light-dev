#!/usr/bin/env bash
# 一键安装 macOS 环境：建 venv、装依赖、装 Cursor hooks。
# 用法：./scripts/setup-mac.sh
set -euo pipefail

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
ROOT="$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)"
VENV="$ROOT/.venv"
NO_PROXY_ENV=(env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u ALL_PROXY -u all_proxy)

echo "============================================================"
echo "AgentCore-Light 开发环境安装（macOS）"
echo "============================================================"
echo "工程根目录：$ROOT"
echo ""

if [[ ! -x "$VENV/bin/python" ]]; then
  echo "[1/3] 创建 Python venv..."
  python3 -m venv "$VENV"
else
  echo "[1/3] Python venv 已存在"
fi

echo "[2/3] 安装 Python 依赖（pyserial bleak esptool）..."
"${NO_PROXY_ENV[@]}" "$VENV/bin/pip" install --quiet --no-cache-dir --upgrade pip
"${NO_PROXY_ENV[@]}" "$VENV/bin/pip" install --quiet --no-cache-dir pyserial bleak esptool

echo "[3/3] 安装 Cursor hooks（本地 + SSH 模板）..."
"$VENV/bin/python" "$ROOT/scripts/install-hooks.js" 2>/dev/null || \
  node "$ROOT/scripts/install-hooks.js"

echo ""
echo "[OK] 安装完成。下一步："
echo "  ./scripts/start-mac.sh      # 启动完整系统"
echo "  ./scripts/doctor.sh         # 自检"
echo "  ./scripts/run-tests.sh      # 跑测试"
