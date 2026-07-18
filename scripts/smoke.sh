#!/usr/bin/env bash
# CI-safe core-loop gate — NO Docker, NO external services, NO live token.
# Runs the no-Docker dev demo (SQLite + fakeredis + mock market) end to end and asserts the run
# reaches 'completed'. Proves compile -> enqueue -> execute -> completed against the mock market
# on every commit. Exit 0 = the core loop works; exit 1 = it regressed.
set -euo pipefail

GREEN=$'\033[0;32m'; RED=$'\033[0;31m'; NC=$'\033[0m'
cd "$(dirname "$0")/.."

echo "==> running no-Docker core-loop demo (uv run python dev.py --demo)"
out="$(uv run python dev.py --demo)"
echo "$out"

if grep -q "'status': 'completed'" <<<"$out"; then
  printf '%sSMOKE OK%s — core loop reached completed\n' "$GREEN" "$NC"
  exit 0
fi
printf '%sSMOKE FAILED%s — demo did not reach completed\n' "$RED" "$NC" >&2
exit 1
