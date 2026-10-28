#!/usr/bin/env bash
# Development: API with auto-reload on :8000 and Vite on :5173 (proxying /api).
set -euo pipefail
cd "$(dirname "$0")/.."
if [ -f .env ]; then set -a; . ./.env; set +a; fi
(cd backend && .venv/bin/uvicorn app.main:app --reload --port 8000) &
(cd frontend && npm run dev)
