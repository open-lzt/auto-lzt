#!/usr/bin/env bash
# Restore a ./backups/*.sql dump into the running compose Postgres. With no argument it picks the
# newest dump. The dump was taken with --clean --if-exists, so this drops-then-recreates cleanly.
set -euo pipefail

RED=$'\033[0;31m'; GREEN=$'\033[0;32m'; YELLOW=$'\033[0;33m'; BLUE=$'\033[0;34m'; NC=$'\033[0m'
info() { printf '%s==>%s %s\n' "$BLUE" "$NC" "$*"; }
ok()   { printf '%s[ok]%s %s\n' "$GREEN" "$NC" "$*"; }
warn() { printf '%s[!]%s  %s\n' "$YELLOW" "$NC" "$*" >&2; }
die()  { printf '%s[x]%s  %s\n' "$RED" "$NC" "$*" >&2; exit 1; }

usage() { echo "restore.sh [backups/<file>.sql]  (defaults to the newest dump in ./backups)"; }
[[ "${1:-}" == "--help" || "${1:-}" == "-h" ]] && { usage; exit 0; }

cd "$(dirname "$0")/.."
command -v docker >/dev/null 2>&1 || die "docker not found"
docker compose ps -q postgres >/dev/null 2>&1 || die "postgres container not running — start the stack first"

file="${1:-}"
if [[ -z "$file" ]]; then
  file="$(ls -1t backups/*.sql 2>/dev/null | head -n1 || true)"
  [[ -n "$file" ]] || die "no backups/*.sql found — pass a file explicitly"
fi
[[ -f "$file" ]] || die "no such file: ${file}"

warn "restoring ${file} into database 'lztflow' — existing objects will be dropped/recreated"
info "restoring"
docker compose exec -T postgres psql -U lzt -d lztflow -v ON_ERROR_STOP=1 < "$file"
ok "restore complete from ${file}"
