#!/usr/bin/env bash
# Documents the manual capture of docs/demo.gif. There is no fully-headless capture in the demo
# pipeline (the gif must visibly show the canvas LiveBadge ticking while the browser tab is
# closed), so this script guides the operator and validates the result rather than auto-recording.
set -euo pipefail

YELLOW=$'\033[0;33m'; BLUE=$'\033[0;34m'; GREEN=$'\033[0;32m'; NC=$'\033[0m'
cd "$(dirname "$0")/.."

cat <<EOF
${BLUE}Recording docs/demo.gif — manual capture procedure${NC}

Goal: a <=15s loop that visibly shows the killer flow running unattended —
the LiveBadge reads "24/7 · N accounts" and a lot bump lands while the tab is closed.

1. Bring the stack up:
     scripts/install.sh              # or: uv run python dev.py   (no-Docker dev canvas)
     pnpm --dir frontend dev         # canvas on http://localhost:5173
2. Open the canvas, deploy the killer flow (on-schedule -> get-my-lots -> for-each -> bump).
3. Start a screen recorder (peek / asciinema+agg / ScreenToGif / OBS) at ~800x500.
4. Deploy, then CLOSE the browser tab; reopen after the next scheduled tick to show the
   badge still live and the run count incremented — the "works without your PC" proof.
5. Export to docs/demo.gif (<=15s, <=8 MB). Optimise with gifsicle -O3 if large.

${YELLOW}This gif is captured by hand and committed to docs/demo.gif.${NC}
EOF

if [[ -f docs/demo.gif ]]; then
  printf '%s[ok]%s docs/demo.gif present (%s bytes)\n' "$GREEN" "$NC" "$(wc -c < docs/demo.gif)"
else
  printf '%s[!]%s  docs/demo.gif not captured yet — see docs/DEMO.md\n' "$YELLOW" "$NC"
fi
