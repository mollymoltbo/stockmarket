#!/usr/bin/env bash
#
# publish.sh — deploy the dashboard's static assets (incl. api.php) to zone.ee.
# This is the CODE deploy; run it when site/ changes — NOT every scan. Per-scan
# DATA flows through the hub (api.php) via api_client.py, not through here.
#
#   ./publish.sh
#
# The server's api_config.php (secret token + data dir) is NEVER uploaded — it
# lives only on the server. Config (in .env; skipped if creds unset):
#   ZONE_SSH_HOST   e.g. yourname.zone.ee   (or an ssh-config Host alias)
#   ZONE_SSH_USER   your zone.ee username
#   ZONE_SSH_PATH   absolute docroot, e.g. /data01/virt12345/domeenid/www.example.ee/htdocs
#   ZONE_SSH_PORT   optional, default 22
set -euo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"; cd "$DIR"
[ -f .env ] && set -a && . ./.env && set +a

if [ -z "${ZONE_SSH_HOST:-}" ] || [ -z "${ZONE_SSH_PATH:-}" ]; then
  echo "  publish skipped (ZONE_SSH_HOST / ZONE_SSH_PATH unset)"; exit 0
fi
PORT="${ZONE_SSH_PORT:-22}"
TARGET="${ZONE_SSH_USER:+${ZONE_SSH_USER}@}${ZONE_SSH_HOST}:${ZONE_SSH_PATH%/}/"

# Never overwrite the server's secret config or push local data artifacts.
EXCLUDES=(--exclude "api_config.php" --exclude "data.json")
if command -v rsync >/dev/null 2>&1; then
  rsync -az "${EXCLUDES[@]}" -e "ssh -p ${PORT}" site/ "$TARGET" \
    && echo "  deployed assets → ${TARGET}"
else
  echo "  rsync not found — install it, or scp site/*.{html,js,css,php} manually" >&2
  exit 1
fi
