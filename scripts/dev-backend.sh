#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

if [[ -x .venv/bin/uvicorn ]]; then
  UVICORN=".venv/bin/uvicorn"
else
  UVICORN="uvicorn"
fi

exec "$UVICORN" app.main:app \
  --app-dir backend \
  --host 127.0.0.1 \
  --port 8000 \
  --reload \
  --reload-dir backend/app \
  --reload-exclude '__pycache__' \
  --reload-exclude '*.pyc'
