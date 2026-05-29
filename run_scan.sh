#!/usr/bin/env bash
#
# run_scan.sh — daily orchestrator on the T480s.
#
#   1. Scan today's sector (deterministic, no LLM, free).
#   2. Append observations to watchlist.json, commit + push to git
#      (so the Claude routine can read the longitudinal history).
#   3. Push a short ntfy summary to your phone.
#   4. GATE: if today's scan found any 🔻 trough setup → fire the Claude
#      routine to analyze those names. Otherwise do nothing.
#
# cron (Mon-Fri 07:00):
#   0 7 * * 1-5  /home/you/stock-scanner/run_scan.sh >> /home/you/stock-scanner/cron.log 2>&1
#
# Stage 1 (Nasdaq screener) needs no API key. Results are pushed to the hub
# (site/api.php) — git is now code-only. Secrets live in .env next to this
# script (NEVER commit .env):
#   SCANNER_API_URL SCANNER_API_TOKEN          (the hub)
#   NTFY_SERVER NTFY_TOKEN NTFY_REPORT_TOPIC
#   CLAUDE_ROUTINE_ID CLAUDE_ROUTINE_TOKEN CLAUDE_BETA_HEADER
set -euo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"; cd "$DIR"
[ -f .env ] && set -a && . ./.env && set +a

REPORT_MD="$DIR/report.md"; RESULT_JSON="$DIR/result.json"; WATCHLIST="$DIR/watchlist.json"
NTFY_SERVER="${NTFY_SERVER:-https://ntfy.sh}"

# ─── 1. Scan today's sector ───
echo "[$(date '+%F %T')] scanning…"
python3 scan.py --screen --watchlist "$WATCHLIST" --output "$REPORT_MD" --result-json "$RESULT_JSON" >/dev/null

read_json(){ if command -v jq >/dev/null 2>&1; then jq -r ".$1" "$RESULT_JSON";
  else python3 -c "import json;print(json.load(open('$RESULT_JSON'))['$1'])"; fi; }

SECTOR="$(read_json sector)"; SCAN_DATE="$(read_json date)"
N_CAND="$(read_json n_candidates)"; N_TROUGH="$(read_json n_trough)"
SHOULD="$(read_json should_analyze)"; WL_SIZE="$(read_json watchlist_size)"
echo "[$(date '+%F %T')] $SECTOR: $N_CAND passed, $N_TROUGH trough, analyze=$SHOULD"

# ─── 2. Push results to the hub (state + feed event) ───
#   Replaces the old git data-bridge + static publish: the dashboard and the
#   Claude routine both read the hub (site/api.php). Static assets are deployed
#   separately with ./publish.sh when the site code changes.
python3 api_client.py push --result "$RESULT_JSON" --watchlist "$WATCHLIST" 2>&1 || echo "  api push failed"

# ─── 3. ntfy: one complete message per candidate (or a single "no hits" note) ───
#   Deep-links each to the dashboard via DASHBOARD_URL. See notify.py.
if [ -n "${NTFY_REPORT_TOPIC:-}" ]; then
  python3 notify.py "$RESULT_JSON" || echo "  WARN: some ntfy messages failed"
fi

# ─── 4. GATE: fire Claude only on a trough setup ───
if [ "$SHOULD" = "True" ] || [ "$SHOULD" = "true" ]; then
  if [ -n "${CLAUDE_ROUTINE_ID:-}" ] && [ -n "${CLAUDE_ROUTINE_TOKEN:-}" ]; then
    echo "  trough found → firing Claude"
    TROUGH_CSV="$(read_json trough_symbols | tr -d '[]"\n ')"
    PROSE="Trough setups detected in ${SECTOR} on ${SCAN_DATE}: ${TROUGH_CSV}. Read today's scan and each name's trajectory from the hub, decide cyclical recovery vs value trap, and post your verdict to the feed (see your instructions)."
    PAYLOAD="$(python3 -c 'import json,sys;print(json.dumps({"text":sys.argv[1]}))' "$PROSE")"
    curl -s -X POST \
      -H "Authorization: Bearer ${CLAUDE_ROUTINE_TOKEN}" \
      -H "anthropic-beta: ${CLAUDE_BETA_HEADER:-experimental-cc-routine-2026-04-01}" \
      -H "anthropic-version: 2023-06-01" -H "Content-Type: application/json" \
      -d "${PAYLOAD}" \
      "https://api.anthropic.com/v1/claude_code/routines/${CLAUDE_ROUTINE_ID}/fire" \
      && echo "  Claude fired" || echo "  WARN: Claude trigger failed"
  else
    echo "  trough found but CLAUDE_ROUTINE_ID/TOKEN unset"
  fi
else
  echo "  no trough → no Claude run today"
fi
echo "[$(date '+%F %T')] done."
