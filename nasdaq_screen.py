"""
nasdaq_screen.py — Stage 1 universe discovery via the free Nasdaq screener.

Replaces fmp_screen.py. FMP retired its free screener: the /api/v3/ screener is
a dead "legacy" endpoint for any key issued after 2025-08-31, and the new
/stable/company-screener requires a paid plan. So Stage 1 now uses the public
endpoint behind nasdaq.com's own stock screener — free, no API key, no quota.

The contract is unchanged: produce a per-sector ticker list. scan.py (Stage 2)
still pulls precise fundamentals (P/B, D/E, 52w-high) per ticker from yfinance
and runs the trough test. The screener's only job is the broad universe cut.

Endpoint:
  GET https://api.nasdaq.com/api/screener/stocks
      ?tableonly=true&limit=<n>&sector=<Nasdaq sector>
  Requires a browser-like User-Agent header or it returns 403.
  Each row has: symbol, name, lastsale ("$12.34"), marketCap ("1,234,567").
  It does NOT return P/B, D/E, or 52w-high (yfinance does), nor an industry
  filter — see the Semiconductors/Tech Hardware note below.

Sector taxonomy: Nasdaq exposes only broad sectors (no industry narrowing on the
free tier). Semiconductors and Tech Hardware therefore both screen the whole
"Technology" sector here; scan.py narrows them by yfinance's per-ticker industry
field in Stage 2 (SECTOR_INDUSTRY_KEYWORDS), which costs no extra API calls
because that field is already fetched.
"""

import sys
import requests

NASDAQ_URL = "https://api.nasdaq.com/api/screener/stocks"

# Browser-like headers — the endpoint 403s a bare request.
HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
}

# Logical sector (scan.py's weekday slots) → Nasdaq screener sector string.
# Semiconductors + Tech Hardware both map to Technology (no industry filter on
# the free tier); scan.py splits them by industry in Stage 2.
NASDAQ_SECTORS = {
    "Semiconductors": "Technology",
    "Tech Hardware":  "Technology",
    "Energy":         "Energy",
    "Materials":      "Basic Materials",
    "Industrials":    "Industrials",
}

# Broad Stage-1 cut (yfinance does the precise trough test in Stage 2).
MIN_MARKET_CAP = 1_000_000_000   # >$1bn, skip micro-caps
MIN_PRICE = 5                    # skip penny stocks
# Cap tickers handed to Stage 2 (per sector) to bound yfinance runtime. The two
# Technology slots are capped higher because Stage 2 then trims them by industry.
SECTOR_LIMIT = {"Semiconductors": 250, "Tech Hardware": 250}
DEFAULT_LIMIT = 120


def _num(s: str) -> float:
    """Parse Nasdaq's '$12.34' / '1,234,567' strings to float; 0 on failure."""
    if not s:
        return 0.0
    try:
        return float(s.replace("$", "").replace(",", "").strip())
    except ValueError:
        return 0.0


def screen_sector(sector: str) -> list[str]:
    """Return a list of tickers for the sector via Nasdaq's screener (1 call)."""
    nasdaq_sector = NASDAQ_SECTORS.get(sector)
    if not nasdaq_sector:
        print(f"Unknown sector '{sector}'. Options: {list(NASDAQ_SECTORS)}",
              file=sys.stderr)
        return []

    params = {"tableonly": "true", "limit": 10000, "sector": nasdaq_sector}
    try:
        r = requests.get(NASDAQ_URL, params=params, headers=HEADERS, timeout=30)
        r.raise_for_status()
        data = r.json().get("data") or {}
        rows = (data.get("table") or {}).get("rows") or []
    except Exception as e:
        print(f"Nasdaq screen failed for {sector}: {e}", file=sys.stderr)
        return []

    # Apply size/liquidity floors, dedupe, sort by market cap (desc), then cap.
    cand = []
    seen = set()
    for row in rows:
        sym = (row.get("symbol") or "").strip().upper()
        if not sym or sym in seen or "^" in sym or "/" in sym:
            continue
        mc, px = _num(row.get("marketCap")), _num(row.get("lastsale"))
        if mc < MIN_MARKET_CAP or px < MIN_PRICE:
            continue
        seen.add(sym)
        cand.append((sym, mc))

    cand.sort(key=lambda t: t[1], reverse=True)
    limit = SECTOR_LIMIT.get(sector, DEFAULT_LIMIT)
    tickers = [sym for sym, _ in cand[:limit]]
    print(f"Nasdaq {sector} ({nasdaq_sector}): {len(rows)} raw → "
          f"{len(cand)} after floors → {len(tickers)} returned", file=sys.stderr)
    return tickers


if __name__ == "__main__":
    import argparse
    import json
    p = argparse.ArgumentParser()
    p.add_argument("--sector", required=True)
    a = p.parse_args()
    print(json.dumps(screen_sector(a.sector)))
