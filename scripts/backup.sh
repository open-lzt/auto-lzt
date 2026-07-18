#!/usr/bin/env bash
# Dump the Postgres database to ./backups/{timestamp}.sql via the running compose stack.
# Plain-format dump with --clean --if-exists so restore.sh (psql) is idempotent. For a
# custom-format archive restored with pg_restore, add -Fc and name the file .dump instead.
set -euo pipefail

RED=$'\033[0;31m'; GREEN=$'\033[0;32m'; BLUE=$'\033[0;34m'; NC=$'\033[0m'
info() { printf '%s==>%s %s\n' "$BLUE" "$NC" "$*"; }
ok()   { printf '%s[ok]%s %s\n' "$GREEN" "$NC" "$*"; }
die()  { printf '%s[x]%s  %s\n' "$RED" "$NC" "$*" >&2; exit 1; }

[[ "${1:-}" == "--help" || "${1:-}" == "-h" ]] && {
  echo "backup.sh — pg_dump the compose Postgres to ./backups/{timestamp}.sql"; exit 0;
}

cd "$(dirname "$0")/.."
command -v docker >/dev/null 2>&1 || die "docker not found"
docker compose ps -q postgres >/dev/null 2>&1 || die "postgres container not running — start the stack first"

mkdir -p backups
ts="$(date -u +%Y%m%dT%H%M%SZ)"
out="backups/${ts}.sql"

info "dumping database -> ${out}"
docker compose exec -T postgres pg_dump -U lzt --clean --if-exists lztflow > "$out"
[[ -s "$out" ]] || die "dump is empty — aborting"
ok "backup written: ${out} ($(wc -c < "$out") bytes)"
