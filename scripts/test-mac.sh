#!/usr/bin/env bash
set -euo pipefail

ROOT="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
PYTHON="$ROOT/.venv/bin/python"

if [[ ! -x "$PYTHON" ]]; then
  echo "Python venv not found. Run setup-mac.sh first."
  exit 1
fi

exec "$PYTHON" "$ROOT/agent_light_control.py"
