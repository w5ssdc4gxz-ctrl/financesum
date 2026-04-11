#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ZSHRC_PATH="$HOME/.zshrc"
KEY_FILE_PATH="$HOME/.claude/vercel-gateway-key"
NEW_KEY="${1:-${VERCEL_AI_GATEWAY_API_KEY:-}}"

if [[ -z "$NEW_KEY" ]]; then
  echo "Usage: scripts/set-claude-vercel-key.sh <vck_key>"
  echo "   or: VERCEL_AI_GATEWAY_API_KEY=<vck_key> scripts/set-claude-vercel-key.sh"
  exit 1
fi

if [[ "$NEW_KEY" != vck_* ]]; then
  echo "Expected a Vercel AI Gateway key starting with vck_."
  exit 1
fi

ZSHRC_PATH="$ZSHRC_PATH" KEY_FILE_PATH="$KEY_FILE_PATH" NEW_KEY="$NEW_KEY" python3 - <<'PY'
import os
from pathlib import Path

zshrc_path = Path(os.environ["ZSHRC_PATH"])
key_file_path = Path(os.environ["KEY_FILE_PATH"])
new_key = os.environ["NEW_KEY"]

zshrc = zshrc_path.read_text()
old_line = next(
    (line for line in zshrc.splitlines() if line.startswith('export VERCEL_AI_GATEWAY_API_KEY=')),
    None,
)

if old_line is None:
    raise SystemExit("Could not find export VERCEL_AI_GATEWAY_API_KEY in ~/.zshrc")

new_line = f'export VERCEL_AI_GATEWAY_API_KEY="{new_key}"'
zshrc_path.write_text(zshrc.replace(old_line, new_line, 1))
key_file_path.write_text(new_key + "\n")
PY

echo "Updated $ZSHRC_PATH"
echo "Updated $KEY_FILE_PATH"
