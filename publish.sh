#!/usr/bin/env bash
#
# publish.sh — rebuild the dashboard data feed and push the static site to
# zone.ee (or any static host). Safe to run standalone any time:
#
#   ./publish.sh
#
# It (1) regenerates site/data.json from the latest result.json + watchlist.json
# + the newest Claude verdict branch, then (2) rsyncs site/ to the host.
#
# Config (in .env next to this script; publish is skipped if creds are unset):
#   ZONE_SSH_HOST   e.g. yourname.zone.ee   (or an ssh-config Host alias)
#   ZONE_SSH_USER   your zone.ee username
#   ZONE_SSH_PATH   absolute docroot, e.g. /data01/virt12345/domeenid/www.example.ee/htdocs
#   ZONE_SSH_PORT   optional, default 22
set -euo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"; cd "$DIR"
[ -f .env ] && set -a && . ./.env && set +a

# ── 1. Rebuild the data feed (pulls the latest verdict from origin/claude/*) ──
python3 build_site.py --out site/data.json

# ── 2. Push the static site ──
if [ -z "${ZONE_SSH_HOST:-}" ] || [ -z "${ZONE_SSH_PATH:-}" ]; then
  echo "  publish skipped (ZONE_SSH_HOST / ZONE_SSH_PATH unset)"; exit 0
fi
PORT="${ZONE_SSH_PORT:-22}"
TARGET="${ZONE_SSH_USER:+${ZONE_SSH_USER}@}${ZONE_SSH_HOST}:${ZONE_SSH_PATH%/}/"

if command -v rsync >/dev/null 2>&1; then
  rsync -az --delete -e "ssh -p ${PORT}" site/ "$TARGET" \
    && echo "  published → ${TARGET}"
else
  # Fallback for hosts without rsync: scp the files (no --delete semantics).
  scp -P "${PORT}" -r site/* "$TARGET" \
    && echo "  published (scp) → ${TARGET}"
fi
