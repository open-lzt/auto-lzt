#!/usr/bin/env bash
# Manual / nightly LIVE smoke — hits the real api.lzt.market with a demo token. NOT part of the
# required CI gate (it needs a secret and touches a live account). Run before publishing, or wire
# as an optional nightly GitHub Actions job. Requires LZT_LIVE_TOKEN in the environment.
set -euo pipefail

GREEN=$'\033[0;32m'; RED=$'\033[0;31m'; NC=$'\033[0m'
cd "$(dirname "$0")/.."

: "${LZT_LIVE_TOKEN:?set LZT_LIVE_TOKEN to a real lzt.market token before running the live smoke}"

echo "==> running LIVE core-loop demo against api.lzt.market"
out="$(uv run python dev.py --demo --no-mock --token "$LZT_LIVE_TOKEN")"
echo "$out"

if grep -q "'status': 'completed'" <<<"$out"; then
  printf '%sLIVE SMOKE OK%s\n' "$GREEN" "$NC"
  exit 0
fi
printf '%sLIVE SMOKE FAILED%s\n' "$RED" "$NC" >&2
exit 1
