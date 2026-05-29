# Trough Scanner — Nasdaq screen → yfinance filter → Claude analysis

Finds Micron-type setups: cyclical-sector stocks trading below what their
fundamentals justify, before the market re-rates them. Screening runs daily on
your T480s; Claude is invoked only when a genuine trough setup appears.

## The two-stage funnel
```
Stage 1  Nasdaq sector screener → ~70-250 tickers   (1 request, no key, instant)
Stage 2  yfinance + trough test → those down to 0-5  (~3 min, free/unlimited)
Gate     any 🔻 trough today?   → fire Claude on just those names
```
The Nasdaq screener can screen a whole sector but doesn't return P/B, D/E, or
52w-high. yfinance can't screen but fetches those per-ticker for free. So Nasdaq
finds the universe, yfinance does the precise valuation work, and only the
handful of real troughs reach Claude (the one rate-limited resource).

> **Note on the Semiconductors / Tech Hardware split:** Nasdaq's free screener
> filters by *sector* but not *industry*, so both of those weekday slots pull the
> whole "Technology" sector. `scan.py` then narrows each by yfinance's per-ticker
> `industry` field (`SECTOR_INDUSTRY_KEYWORDS`) — at no extra API cost, since that
> field is already fetched. (This replaced FMP, whose free screener was retired:
> its `/api/v3/` screener is now a dead "legacy" endpoint and the new
> `/stable/company-screener` is paid-only.)

## The loop
- **Mon-Fri, one sector/day:** Semiconductors, Energy, Materials, Industrials,
  Tech Hardware. The week-long spread IS the weekday batching.
- **Every passing name is tracked over time** in watchlist.json (price, % off
  high, P/B per observation). Names drop after 30 days unseen.
- **A name becomes a 🔻 trough** (40%+ off high, near/below book, survivable
  debt, depressed earnings) → cron fires Claude to judge cyclical-recovery vs
  value-trap, using the tracked trajectory as context.
- **No trough = no Claude run.**

## Files
- `nasdaq_screen.py` — Stage 1: Nasdaq sector screener (the universe source)
- `scan.py`        — Stage 2: yfinance fetch + strategy registry + watchlist
- `notify.py`      — one ntfy message per candidate (tagged by strategy)
- `api_client.py`  — push scan results/state + feed events to the hub
- `site/api.php`   — the hub: flat-file feed + state store (POST token-gated)
- `site/`          — dashboard (index.html, app.js, style.css) — reads the hub
- `run_scan.sh`    — cron orchestrator: scan → push hub → ntfy → conditional Claude
- `publish.sh`     — deploy site/ assets to zone.ee (code deploy, not per-scan)
- `routine_prompt.md` — Claude routine prompt
- `.env.example`   — secrets template (copy to .env, never commit)
- `requirements.txt`

## Strategies (one fetch, many lenses)
The yfinance fetch is the only expensive step, so every strategy is evaluated on
the *same* fetched data — adding one is near-free. Each is an entry in `scan.py`'s
`STRATEGIES` registry returning a uniform result (match / score / signals /
high-conviction / reasons). A name is a tracked candidate if **any** strategy
matches, and is tagged with all that did (a name can be both).

- **🔻 Trough** — the original value lens: ≥25% off high, near/below book,
  survivable debt, depressed earnings (the Micron pattern). High-conviction hits
  **fire the Claude routine**.
- **🚀 Momentum** — the opposite lens: within 15% of the 52w high, above the
  200-day, and actually growing; ignores valuation/balance sheet. High-conviction
  = a breakout (at the highs, stacked above 50/200-day, on a volume/growth surge).
  This catches DELL-type earnings breakouts that Trough rejects by design.
  Momentum is **screen + notify + dashboard only** — it does *not* fire Claude
  (the routine prompt is trough-specific).

Both run on the same daily weekday-rotation universe, so Momentum is
sector-delayed (a mover is only caught on its sector's day). Giving Momentum its
own daily all-sector "movers" universe is a future option.

## The hub (zone.ee) — one place everyone reads/writes
`site/api.php` is a tiny PHP endpoint that replaces git as the data bridge.
Every party POSTs to it (token-gated) and the dashboard + Claude routine read
from it:
```
T480s   --POST state(scan,watchlist) + feed event-->  api.php
Claude  --GET  state(scan,watchlist)-->  api.php  --POST verdict to feed-->
Browser --GET  ?action=all-->  api.php   (dashboard renders it live)
Phone   <--ntfy push--  (the buzz; tap → opens the dashboard)
```
Two data shapes:
- **feed** — append-only message log (`messages.ndjson`): scan events, Claude
  verdicts, manual notes. Newest-first on the dashboard.
- **state** — last-write-wins JSON blobs (`state_scan.json`, `state_watchlist.json`)
  for the cards/sparklines and the routine's trajectory reads.

**Storage is plain flat files** (no DB/extension), `flock`-guarded, kept in a
directory **outside the web root** (`api_config.php` sets `$DATA_DIR`). Writes
need the shared token in an `X-Auth` header; reads are open.

Endpoints: `POST ?action=message` · `POST ?action=state&name=X` ·
`GET ?action=feed|state&name=X|all|ping`. The vanilla-JS frontend fetches
`?action=all` and renders feed (with a built-in markdown renderer), candidate
cards, and watchlist sparklines — no build step.

ntfy stays the **push** layer (phone buzz, deep-links via `DASHBOARD_URL`); the
hub/dashboard is the **pull** layer. Git is now **code-only**.

> **Public, no auth on reads** — by choice. Anyone with the URL can read the
> feed/verdicts. Writes are token-gated. Don't post anything you wouldn't publish.

Setup:
1. `cp site/api_config.php.example site/api_config.php`, set a long random
   `$API_TOKEN`, and ensure `$DATA_DIR` is outside the web root.
2. Put the same token in `.env` as `SCANNER_API_TOKEN`, set `SCANNER_API_URL`,
   and set both (plus `NTFY_*`, `DASHBOARD_URL`) on the Claude routine.
3. `./publish.sh` to deploy `site/` (it never uploads `api_config.php`). Re-run
   only when the site code changes — data flows through the hub, not publish.

## No fallback by design
If the Nasdaq screener returns nothing (network, taxonomy drift), the run EXITS
with an error rather than silently scanning a stale hardcoded list. Fix the
cause; don't mask it. The hardcoded lists in scan.py are kept ONLY for offline
testing (`--sector X` without `--screen`).

## Setup on the T480s
```bash
git clone git@github.com:mollymoltbo/stockmarket.git ~/stock-scanner
cd ~/stock-scanner
pip3 install -r requirements.txt --break-system-packages
echo ".env" >> .gitignore
cp .env.example .env        # fill in real secrets (ntfy + Claude routine)
chmod +x run_scan.sh
```

### Get the keys
- **Nasdaq screener:** none — Stage 1 is keyless.
- **ntfy:** token + a report topic; subscribe to that topic in the ntfy app.
- **Claude routine:** see below.

### Test the funnel before scheduling
```bash
python3 nasdaq_screen.py --sector Energy        # check the screener returns tickers
python3 scan.py --screen --sector Energy --watchlist /tmp/t.json   # full stage 1+2
# Hub smoke test (locally): serve site/, then push and read back
cp site/api_config.php.example site/api_config.php   # set a test token + /tmp DATA_DIR
php -S 127.0.0.1:8097 -t site &
SCANNER_API_URL=http://127.0.0.1:8097/api.php SCANNER_API_TOKEN=<token> \
  python3 api_client.py push --result result.json --watchlist watchlist.json
curl -s 'http://127.0.0.1:8097/api.php?action=all' | python3 -m json.tool
./run_scan.sh                                   # full orchestration
```
The Nasdaq screener is US-listed only (NYSE/Nasdaq/AMEX); the EU-exchange scope
from the old FMP setup is gone. Foreign names still appear via their US listings
(ADRs/cross-listings — e.g. SHEL, BHP, TTE show up under Energy).

### Schedule
```bash
crontab -e
# 0 7 * * 1-5  /home/you/stock-scanner/run_scan.sh >> /home/you/stock-scanner/cron.log 2>&1
```

## The Claude routine
Prereq: "Claude Code on the web" enabled (Pro/Max/Team/Enterprise). If
claude.ai/code/routines redirects to a download page, enable it first.

Create at claude.ai/code/routines:
- Prompt: paste `routine_prompt.md`
- Repository: your repo (optional now — the routine reads/writes the hub, not git)
- Trigger: **API only** — Add trigger → API → Generate token. Copy routine_id
  and token into .env (shown once).
- Env vars on the routine: SCANNER_API_URL, SCANNER_API_TOKEN (to read state +
  post its verdict), NTFY_SERVER, NTFY_TOKEN, NTFY_REPORT_TOPIC, DASHBOARD_URL.

Endpoint (wired in run_scan.sh):
```
POST https://api.anthropic.com/v1/claude_code/routines/{routine_id}/fire
Headers: Authorization: Bearer {token}
         anthropic-beta: experimental-cc-routine-2026-04-01   (may rotate)
         anthropic-version: 2023-06-01
Body:    {"text": "human-readable prose"}   ← NOT structured JSON
```

## Why a hub (not git)
The T480s WRITES results; the Claude routine (Anthropic cloud) READS each name's
trajectory and WRITES its verdict; the dashboard READS everything. Earlier this
rode on git (the cron committed `watchlist.json`, the routine pushed `analysis.md`
to a `claude/*` branch — awkward to retrieve). The hub (`site/api.php`) replaces
that with one HTTP endpoint everyone reads/writes. **Git is now code-only.** The
T480s still keeps `watchlist.json` locally as the source of truth between runs.

## The Micron fix
1. EV/EBITDA & fwd P/E only disqualify POSITIVE high values — negative
   (cyclical trough) passes instead of being rejected.
2. 🔻 trough flag: 40%+ off high + near/below book + survivable D/E + depressed
   earnings. Verified on MU Dec 2022 ($50, P/B 1.25, neg EBITDA) — old logic
   rejected it, new logic flags it. Ran to ~$157 by mid-2024.

## Tuning
- `nasdaq_screen.py`: NASDAQ_SECTORS, MIN_MARKET_CAP, MIN_PRICE, SECTOR_LIMIT
- `scan.py`: THRESHOLDS + TROUGH_SIGNALS (Trough), MOMENTUM_GATES +
  MOMENTUM_SIGNALS + MOMENTUM_BREAKOUT (Momentum), STRATEGIES registry
  (enable/disable, `fires_claude`), SECTOR_INDUSTRY_KEYWORDS,
  WATCHLIST_MAX_AGE_DAYS, WEEKDAY_SECTOR

## Adding a strategy
1. Write `evaluate_x(d) -> _result(match, score, signals, high_conviction, reasons)`
   in `scan.py` (read only from the already-fetched `d`; if you need a new field,
   add it to `fetch_ticker_data`).
2. Add `{"name","emoji","evaluate","fires_claude"}` to `STRATEGIES`.
3. Add display metadata: `STRATEGY_NTFY` in `notify.py` and `STRAT` in
   `site/app.js`. That's it — report, dashboard, ntfy, and watchlist tagging
   pick it up automatically.
