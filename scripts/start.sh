#!/usr/bin/env bash
# Start NavRUNA without Docker: creates a virtual environment, builds the interface when its sources
# changed, loads .env, and serves on port 8000 (one worker: jobs run inside the API process).
set -euo pipefail
cd "$(dirname "$0")/.."
ROOT=$(pwd)
if [ -f .env ]; then set -a; . ./.env; set +a; fi
PY=${PYTHON:-python3}
if [ ! -d backend/.venv ]; then "$PY" -m venv backend/.venv; fi
backend/.venv/bin/pip install -q --upgrade pip
backend/.venv/bin/pip install -q -r backend/requirements.txt
if [ ! -f frontend/dist/index.html ] || [ -n "$(find frontend/src frontend/package.json -newer frontend/dist/index.html 2>/dev/null | head -1)" ]; then
  (cd frontend && npm install --no-audit --no-fund && npm run build)
fi
cd backend
exec .venv/bin/uvicorn app.main:app --host "${HOST:-0.0.0.0}" --port "${PORT:-8000}" --workers 1
