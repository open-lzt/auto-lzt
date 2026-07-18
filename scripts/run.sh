#!/usr/bin/env bash
set -euo pipefail
# Bring up the local stack (Postgres + Redis + API) and apply migrations.
cd "$(dirname "$0")/.."
docker compose up -d postgres redis
echo "waiting for postgres/redis health..."
until [ "$(docker inspect -f '{{.State.Health.Status}}' "$(docker compose ps -q postgres)")" = "healthy" ]; do sleep 1; done
uv run alembic upgrade head
echo "API on http://localhost:8000 — for the canvas, in another terminal run:"
echo "  pnpm --dir frontend install && pnpm --dir frontend dev"
echo "(or 'docker compose up frontend' for the built SPA on http://localhost:5173)"
# Loopback by default — the API is unauthenticated; override only behind an auth proxy.
uv run uvicorn app.main:app --host "${LZT_FLOW_BIND_HOST:-127.0.0.1}" --port 8000
