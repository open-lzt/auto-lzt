#!/usr/bin/env bash
# Idempotent one-shot installer for the lzt-flow Docker demo stack on a clean Ubuntu host.
# Safe to re-run: it never destroys data, skips steps already satisfied, and fails loud on the
# first real error. For the no-Docker dev loop use `uv run python dev.py` instead (see README).
set -euo pipefail

RED=$'\033[0;31m'; GREEN=$'\033[0;32m'; YELLOW=$'\033[0;33m'; BLUE=$'\033[0;34m'; NC=$'\033[0m'
info()  { printf '%s==>%s %s\n' "$BLUE" "$NC" "$*"; }
ok()    { printf '%s[ok]%s %s\n' "$GREEN" "$NC" "$*"; }
warn()  { printf '%s[!]%s  %s\n' "$YELLOW" "$NC" "$*" >&2; }
die()   { printf '%s[x]%s  %s\n' "$RED" "$NC" "$*" >&2; exit 1; }

usage() {
  cat <<'EOF'
install.sh — bootstrap the lzt-flow Docker demo stack (idempotent).

Usage: scripts/install.sh [--help]

Steps:
  1. verify docker + docker compose are present
  2. copy .env.example -> .env if .env is absent (then STOP so you can fill secrets)
  3. docker compose up -d postgres redis, wait for health
  4. uv run alembic upgrade head            (lzt-flow's own schema)
  5. docker compose up -d                    (api + worker + frontend)

The lzt-eventus schema is NOT migrated here: the embedded engine calls
ensure_eventus_schema() (create_all, checkfirst) at worker startup — see
app/worker/eventus_bootstrap.py. Re-running this script is always safe.
EOF
}

[[ "${1:-}" == "--help" || "${1:-}" == "-h" ]] && { usage; exit 0; }

cd "$(dirname "$0")/.."

command -v docker >/dev/null 2>&1 || die "docker not found — install Docker Engine first."
docker compose version >/dev/null 2>&1 || die "docker compose v2 not found — install the compose plugin."
ok "docker + compose present"

if [[ ! -f .env ]]; then
  cp .env.example .env
  warn ".env created from .env.example — fill in the secrets before continuing:"
  warn "  LZT_FLOW_MASTER_KEY  (envelope key for account tokens)"
  warn "  LZT_TOKEN_ENC_KEY    (separate eventus poll-token key)"
  warn "  LZT_TOKENS           (eventus polling tokens, only for the on-event path)"
  warn "Then re-run scripts/install.sh — this run stops here on purpose."
  exit 1
fi
ok ".env present"

info "starting postgres + redis"
docker compose up -d postgres redis

info "waiting for postgres health"
for _ in $(seq 1 60); do
  status="$(docker inspect -f '{{.State.Health.Status}}' "$(docker compose ps -q postgres)" 2>/dev/null || echo starting)"
  [[ "$status" == "healthy" ]] && break
  sleep 2
done
[[ "${status:-}" == "healthy" ]] || die "postgres did not become healthy in time"
ok "postgres healthy"

info "applying lzt-flow migrations (alembic upgrade head)"
uv run alembic upgrade head
ok "lzt-flow schema migrated"
info "lzt-eventus schema auto-creates at worker startup (ensure_eventus_schema) — no manual step"

info "starting the full stack (api + worker + frontend)"
docker compose up -d
ok "stack up — API on http://localhost:8000, canvas on http://localhost:5173"
info "verify: curl -fsS http://localhost:8000/health"
