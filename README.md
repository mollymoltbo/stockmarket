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
- `build_site.py`  — assemble `site/data.json` (scan + watchlist + verdict)
- `site/`          — static dashboard (index.html, app.js, style.css)
- `run_scan.sh`    — cron orchestrator: scan → git → publish → ntfy → conditional Claude
- `publish.sh`     — rebuild `site/data.json` + rsync `site/` to the static host
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

## The dashboard (zone.ee)
A static site browses everything ntfy can't: candidate metric cards, each
watchlist name's "% off high" trajectory as a sparkline, and the rendered Claude
verdict. `build_site.py` merges `result.json` + `watchlist.json` + the latest
verdict (auto-pulled from the `origin/claude/*` branch the routine pushes to)
into `site/data.json`; the vanilla-JS frontend fetches and renders it — no build
step, no server code, so it runs on plain static hosting.

ntfy stays as the **push** layer; the dashboard is the **pull** layer. The scan
notification deep-links to the dashboard via `DASHBOARD_URL` (ntfy `Click`).

> **Public, no auth** — by choice. Anyone with the URL can read the verdicts.
> Don't host anything you wouldn't publish.

Deploy: set `ZONE_SSH_HOST` / `ZONE_SSH_USER` / `ZONE_SSH_PATH` (and optional
`ZONE_SSH_PORT`, `DASHBOARD_URL`) in `.env`. `publish.sh` rsyncs over SSH (falls
back to scp). zone.ee is PHP/Node + static — we use it purely as a static host;
all compute stays on the T480s and the Anthropic cloud.

**Verdict timing:** a trough fires Claude at the end of `run_scan.sh`; the cloud
verdict lands minutes later. The same run's publish won't have it yet, so it
shows on the next publish. To pull it sooner, add a refresh cron (publish only,
no scan):
```bash
30 7 * * 1-5  /home/you/stock-scanner/publish.sh >> /home/you/stock-scanner/cron.log 2>&1
```

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
- Repository: your repo (read; the cron writes)
- Trigger: **API only** — Add trigger → API → Generate token. Copy routine_id
  and token into .env (shown once).
- Env vars on the routine: NTFY_SERVER, NTFY_TOKEN, NTFY_REPORT_TOPIC.

Endpoint (wired in run_scan.sh):
```
POST https://api.anthropic.com/v1/claude_code/routines/{routine_id}/fire
Headers: Authorization: Bearer {token}
         anthropic-beta: experimental-cc-routine-2026-04-01   (may rotate)
         anthropic-version: 2023-06-01
Body:    {"text": "human-readable prose"}   ← NOT structured JSON
```

## Why git
The cron (T480s) WRITES watchlist.json; the Claude routine (Anthropic cloud)
READS it for each name's trajectory. Two machines, one shared file → git bridges.

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
