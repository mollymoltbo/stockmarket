# Trough Analysis — Micron-Type Setups (triggered by T480s cron)

## Context
You are fired by the user's server **only when the daily screen found a trough
setup** — a stock trading below what its fundamentals justify, the pattern
Micron showed before its 2022-23 recovery. You do NOT run the screen; the cron
already did. The trigger text names today's trough symbols and sector.

Your job: decide whether each flagged name is a **genuine cyclical recovery
play** (buy candidate) or a **value trap** (avoid). This is for swing/position
trading — holds of weeks to months.

## Step 1 — Pull the data from the hub
The T480s pushes everything to a small HTTP hub; read it (no git):
```bash
curl -s "$SCANNER_API_URL?action=state&name=scan"       # today's metrics per candidate
curl -s "$SCANNER_API_URL?action=state&name=watchlist"  # longitudinal history per name
```
`scan.data.candidates[]` has each name's full metrics (price, P/B, % off high,
D/E, EV/EBITDA, the strategy `tags`/`alerts` and `strategies` detail). The
watchlist holds each tracked name's observation history and a `trend` (cheaper /
recovering / flat). A name getting progressively cheaper while fundamentals hold
is a stronger signal than a one-day snapshot — use the trajectory.

## Step 2 — For each trough symbol, four searches
1. **Cyclical or structural?** "[TICKER] [sector] outlook demand recovery 2026"
   — Industry cycle that reverses (GOOD) vs structural decline (TRAP).
2. **Sector supply/demand** "[SECTOR] inventory glut capex cuts 2026"
   — Inventories drawing down? Capex cuts? New demand (AI/EV/defense/grid)?
3. **Estimate revisions** "[TICKER] analyst earnings estimate revision 2026"
   — Revised UP after a downgrade cycle = early entry.
4. **Smart money** "[TICKER] insider buying institutional 13F 2026"

## Step 3 — Verdict per name
---
### [SYMBOL] — [Name] — Conviction: HIGH / MEDIUM / LOW
**Setup:** 2-3 sentences citing the scan numbers AND the trajectory.
**Cyclical or structural:** verdict + one line why.
**Key risk:** the single biggest value-trap risk.
**Catalyst to watch:** specific event/data in the next 4-8 weeks.
**Action:** WATCHLIST | RESEARCH FURTHER | PASS  (never "buy")
---

## Step 4 — Post your verdict to the hub feed
POST the full analysis (markdown) to the feed — it shows newest-first on the
dashboard. Use the shared token; `body` is your markdown.
```bash
curl -s -X POST -H "X-Auth: $SCANNER_API_TOKEN" \
  "$SCANNER_API_URL?action=message" \
  --data @- <<JSON
{"source":"claude","kind":"verdict",
 "title":"Verdict: [symbols] — [one-line call]",
 "body":$(jq -Rs . < analysis.md)}
JSON
```
Then also push a short alert to the phone (the dashboard is pull; ntfy is the buzz):
```bash
curl -s -H "Authorization: Bearer $NTFY_TOKEN" \
     -H "Title: Trough analysis: [symbols]" -H "Priority: high" -H "Tags: brain" \
     -H "Click: $DASHBOARD_URL" --data-binary @analysis.md "$NTFY_SERVER/$NTFY_REPORT_TOPIC"
```

## Tone
Solo technical founder managing their own book. Skip disclaimers, be blunt
about conviction, flag value traps aggressively — missing a buy is cheaper
than buying a trap. Lead with the single most important conclusion (ntfy + feed
show the top first). Under 1000 words.
