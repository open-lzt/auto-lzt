#!/usr/bin/env bash
# Single-host update: pull, rebuild, migrate, restart. No blue-green — this deploy shape is one
# host, deliberately; zero-downtime belongs with managed multi-host hosting (Phase 2).
set -euo pipefail

RED=$'\033[0;31m'; GREEN=$'\033[0;32m'; BLUE=$'\033[0;34m'; NC=$'\033[0m'
info() { printf '%s==>%s %s\n' "$BLUE" "$NC" "$*"; }
ok()   { printf '%s[ok]%s %s\n' "$GREEN" "$NC" "$*"; }
die()  { printf '%s[x]%s  %s\n' "$RED" "$NC" "$*" >&2; exit 1; }

usage() {
  cat <<'EOF'
update.sh — pull latest code and roll the stack forward (single host, brief downtime).

Usage: scripts/update.sh [--help]

Steps: git pull -> docker compose up -d --build -> uv run alembic upgrade head.
The lzt-eventus schema self-heals at worker startup (ensure_eventus_schema), so no
separate migration step is needed for it.
EOF
}

[[ "${1:-}" == "--help" || "${1:-}" == "-h" ]] && { usage; exit 0; }

cd "$(dirname "$0")/.."
[[ -f .env ]] || die ".env missing — run scripts/install.sh first"

info "git pull"
git pull --ff-only
ok "code updated"

info "rebuild + restart"
docker compose up -d --build
ok "containers rebuilt"

info "applying migrations"
uv run alembic upgrade head
ok "update complete"
