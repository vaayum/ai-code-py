#!/bin/bash
# Start the AICoder Web Shell
set -e
DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="${AICODER_DIR:-$(dirname "$DIR")}"
VENV="$PROJECT_ROOT/.venv/bin/python"
PORT="${PORT:-8765}"

echo ""
echo "  ⚡ AICoder Web Shell"
echo "  ───────────────────────"
echo "  Project: $PROJECT_ROOT"
echo "  URL:     http://localhost:$PORT"
echo ""

AICODER_DIR="$PROJECT_ROOT" "$VENV" -m uvicorn server:app \
  --host 0.0.0.0 --port "$PORT" --app-dir "$DIR"
