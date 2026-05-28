"""
Stock Scanner — Cyclical Undervaluation Screen (Micron-type setups)

Finds fundamentally solid companies in cyclical sectors whose price has been
beaten down below what the balance sheet justifies — the pattern Micron showed
at its 2022-23 trough before it 3x'd.

Design:
  - One sector per weekday (Mon=Semis ... Fri=Tech Hardware).
  - Deterministic: pure data + filters, NO LLM. Runs free on the T480s daily.
  - Maintains a longitudinal watchlist (per-symbol observation HISTORY) so a
    name can be tracked over time. Entries drop 30 days after last seen.
  - Writes result.json so the cron can decide whether to invoke Claude:
    the gate is simply "did today's scan find any trough setup?"

Usage:
  python scan.py --screen                  # today's sector, universe from Nasdaq
  python scan.py --screen --sector Energy  # force a sector (live universe)
  python scan.py --sector Energy           # offline: use the small sample list
  python scan.py --tickers MU AMD          # ad-hoc test, no state
  python scan.py --screen --watchlist wl.json --output report.md --result-json result.json

Dependencies: pip install yfinance pandas requests
"""

import yfinance as yf
import json
import os
import argparse
import sys
from datetime import datetime, date
from typing import Optional

# ─── Sectors scanned, one per weekday. ───
# In production the ticker LISTS are ignored — the Nasdaq screener supplies the
# universe per sector (run with --screen). These hardcoded lists are kept ONLY
# as a small sample set for offline testing without network access (run
# --sector X without --screen). The SECTOR NAMES here must match the keys in
# nasdaq_screen.NASDAQ_SECTORS.
UNIVERSE = {
    "Semiconductors": ["MU","INTC","AMD","QCOM","MCHP","ON","WOLF","SWKS","MPWR","LRCX","AMAT","KLAC","ASML","TXN","STM"],
    "Energy":         ["DVN","MRO","APA","HAL","SLB","OVV","FANG","CTRA","SM","CHX","NOV","BKR","COP","EOG","XOM"],
    "Materials":      ["FCX","AA","CLF","NUE","STLD","X","MT","ALB","MP","LAC","VALE","RIO","BHP","SCCO","NEM"],
    "Industrials":    ["DE","CAT","EMR","ETN","ITW","PH","GE","HON","MMM","ROK","IR","XYL","AME","FLS","TDY"],
    "Tech Hardware":  ["WDC","STX","NTAP","HPE","HPQ","PSTG","SMCI","ANET","JNPR","CSCO","GLW","FLEX","JBL"],
}
WEEKDAY_SECTOR = {0:"Semiconductors",1:"Energy",2:"Materials",3:"Industrials",4:"Tech Hardware",
                  5:"Semiconductors",6:"Energy"}

# Stage-2 industry narrowing. The Nasdaq screener (Stage 1) has no industry
# filter, so Semiconductors and Tech Hardware both arrive as the whole
# "Technology" sector. We trim them here using yfinance's per-ticker `industry`
# field — which fetch_ticker_data already pulls, so this costs no extra calls.
# Match is a case-insensitive substring test against any keyword. Sectors absent
# from this map (Energy, Materials, Industrials) are not narrowed.
SECTOR_INDUSTRY_KEYWORDS = {
    "Semiconductors": ["semiconductor"],
    "Tech Hardware":  ["computer hardware", "communication equipment",
                       "electronic components", "data storage",
                       "networking", "scientific"],
}

WATCHLIST_MAX_AGE_DAYS = 30   # drop a name 30 days after last seen

# ─── Hard screen filters ───
THRESHOLDS = {
    "max_price_to_book":    2.5,
    "max_debt_to_equity":   80.0,   # %
    "min_current_ratio":    1.2,
    "min_pct_off_52w_high": 25.0,
    "max_ev_to_ebitda":     15.0,   # only applied when EBITDA is POSITIVE
    "min_market_cap_bn":    1.0,
    "max_forward_pe":       25.0,
}
# Quality (currently-healthy) bonus signals
BONUS_SIGNALS = {"deep_value_pb":1.0,"high_roe":15.0,"strong_gross_margin":30.0,"low_forward_pe":12.0}
# Trough-setup signal: looks bad on earnings, good on balance sheet (the Micron pattern)
TROUGH_SIGNALS = {"min_pct_off_high":40.0,"max_price_to_book":1.6,"max_debt_to_equity":60.0}


def fetch_ticker_data(symbol: str) -> Optional[dict]:
    try:
        info = yf.Ticker(symbol).info
        price = info.get("currentPrice") or info.get("regularMarketPrice")
        if not price:
            return None
        hi = info.get("fiftyTwoWeekHigh"); lo = info.get("fiftyTwoWeekLow")
        mc = info.get("marketCap", 0) or 0
        return {
            "symbol": symbol, "name": info.get("shortName", symbol),
            "sector": info.get("sector",""), "industry": info.get("industry",""),
            "price": price, "52w_high": hi, "52w_low": lo,
            "pct_off_high": round((1-price/hi)*100,1) if hi else None,
            "pct_above_low": round(((price/lo)-1)*100,1) if lo else None,
            "market_cap_bn": round(mc/1e9,2),
            "price_to_book": info.get("priceToBook"),
            "forward_pe": info.get("forwardPE"), "trailing_pe": info.get("trailingPE"),
            "ev_ebitda": info.get("enterpriseToEbitda"),
            "debt_to_equity": info.get("debtToEquity"),
            "current_ratio": info.get("currentRatio"),
            "revenue_growth": info.get("revenueGrowth"),
            "earnings_growth": info.get("earningsGrowth"),
            "roe": info.get("returnOnEquity"), "gross_margin": info.get("grossMargins"),
            "total_cash_bn": round((info.get("totalCash") or 0)/1e9,2),
            "total_debt_bn": round((info.get("totalDebt") or 0)/1e9,2),
        }
    except Exception as e:
        print(f"  [skip] {symbol}: {e}", file=sys.stderr)
        return None


def industry_matches(d: dict, sector: str) -> bool:
    """Stage-2 industry narrowing for the Technology sub-sectors. True if the
    sector isn't narrowed, or the ticker's yfinance industry hits a keyword."""
    kws = SECTOR_INDUSTRY_KEYWORDS.get(sector)
    if not kws:
        return True
    ind = (d.get("industry") or "").lower()
    return any(k in ind for k in kws)


def passes_screen(d: dict) -> tuple[bool, list[str]]:
    """EV/EBITDA and fwd P/E only disqualify when POSITIVE and high — negative
    (cyclical trough) passes through. This is the fix that lets Micron in."""
    T = THRESHOLDS; fails = []
    if d["market_cap_bn"] < T["min_market_cap_bn"]:
        fails.append(f"mktcap ${d['market_cap_bn']}bn")
    if d["price_to_book"] is None or d["price_to_book"] > T["max_price_to_book"]:
        fails.append(f"P/B {d['price_to_book']}")
    if d["debt_to_equity"] is not None and d["debt_to_equity"] > T["max_debt_to_equity"]:
        fails.append(f"D/E {d['debt_to_equity']:.0f}%")
    if d["current_ratio"] is not None and d["current_ratio"] < T["min_current_ratio"]:
        fails.append(f"current {d['current_ratio']:.2f}")
    if d["pct_off_high"] is None or d["pct_off_high"] < T["min_pct_off_52w_high"]:
        fails.append(f"{d['pct_off_high']}% off high")
    ev = d["ev_ebitda"]
    if ev is not None and ev > 0 and ev > T["max_ev_to_ebitda"]:
        fails.append(f"EV/EBITDA {ev:.1f}")
    fpe = d["forward_pe"]
    if fpe is not None and fpe > 0 and fpe > T["max_forward_pe"]:
        fails.append(f"fwd P/E {fpe:.1f}")
    return (len(fails)==0, fails)


def detect_trough_setup(d: dict) -> tuple[bool, list[str]]:
    """The Micron pattern: deeply discounted + near/below book + survivable
    balance sheet + earnings currently depressed. Value is in the balance sheet."""
    TS = TROUGH_SIGNALS; reasons = []
    deeply_off = (d["pct_off_high"] or 0) >= TS["min_pct_off_high"]
    near_book = d["price_to_book"] is not None and d["price_to_book"] <= TS["max_price_to_book"]
    survivable = d["debt_to_equity"] is None or d["debt_to_equity"] <= TS["max_debt_to_equity"]
    ev, roe, eg = d["ev_ebitda"], d["roe"], d["earnings_growth"]
    depressed = (ev is not None and ev < 0) or (roe is not None and roe <= 0) or (eg is not None and eg < 0)
    is_trough = deeply_off and near_book and survivable and depressed
    if is_trough:
        reasons.append(f"deeply discounted ({d['pct_off_high']}% off high)")
        if near_book: reasons.append(f"near/below book (P/B {d['price_to_book']:.2f})")
        if d["debt_to_equity"] is not None: reasons.append(f"survivable (D/E {d['debt_to_equity']:.0f}%)")
        reasons.append("earnings at cyclical trough — value in the balance sheet")
    return is_trough, reasons


def score_candidate(d: dict) -> tuple[int, list[str]]:
    s, sig, B = 0, [], BONUS_SIGNALS
    if d["price_to_book"] and d["price_to_book"] < B["deep_value_pb"]:
        s+=1; sig.append(f"below book (P/B {d['price_to_book']:.2f})")
    if d["revenue_growth"] and d["revenue_growth"] > 0:
        s+=1; sig.append(f"revenue growing ({d['revenue_growth']*100:.1f}%)")
    if d["roe"] and d["roe"]*100 > B["high_roe"]:
        s+=1; sig.append(f"high ROE ({d['roe']*100:.1f}%)")
    if d["gross_margin"] and d["gross_margin"]*100 > B["strong_gross_margin"]:
        s+=1; sig.append(f"gross margin {d['gross_margin']*100:.1f}%")
    if d["forward_pe"] and 0 < d["forward_pe"] < B["low_forward_pe"]:
        s+=1; sig.append(f"cheap fwd P/E {d['forward_pe']:.1f}")
    return s, sig


# ─── Longitudinal watchlist (per-symbol observation history) ───
def load_watchlist(path: str) -> dict:
    if path and os.path.exists(path):
        try:
            with open(path) as f: return json.load(f)
        except Exception: return {}
    return {}


def prune_watchlist(wl: dict, max_age_days: int = WATCHLIST_MAX_AGE_DAYS) -> dict:
    today = date.today(); out = {}
    for sym, e in wl.items():
        try:
            last = datetime.strptime(e["last_seen"], "%Y-%m-%d").date()
            if (today - last).days <= max_age_days:
                out[sym] = e
        except Exception:
            out[sym] = e
    return out


def record_observation(wl: dict, d: dict, is_trough: bool, today: str, sector: str):
    """Append today's observation to the symbol's history (keeps trajectory)."""
    obs = {
        "date": today,
        "price": d["price"],
        "pct_off_high": d["pct_off_high"],
        "price_to_book": round(d["price_to_book"],2) if d["price_to_book"] else None,
        "trough": is_trough,
    }
    e = wl.get(d["symbol"])
    if e:
        e["last_seen"] = today
        e["sector"] = d["sector"] or sector
        e["history"].append(obs)
        e["history"] = e["history"][-40:]   # cap history length
        e["ever_trough"] = e.get("ever_trough", False) or is_trough
    else:
        wl[d["symbol"]] = {
            "name": d["name"], "sector": d["sector"] or sector,
            "first_seen": today, "last_seen": today,
            "ever_trough": is_trough, "history": [obs],
        }


def _trend(e: dict) -> str:
    """One-word trajectory of % off high across the symbol's history."""
    h = [o for o in e.get("history",[]) if o.get("pct_off_high") is not None]
    if len(h) < 2: return "new"
    first, last = h[0]["pct_off_high"], h[-1]["pct_off_high"]
    if last - first >= 5: return "cheaper"     # more off-high = falling price
    if first - last >= 5: return "recovering"
    return "flat"


def build_report(candidates, sector, total, today, watchlist) -> str:
    L = [f"# Stock Scan — {sector} — {today}",
         f"Scanned {total} tickers. **{len(candidates)} passed.** "
         f"Tracked watchlist: **{len(watchlist)} names** (30-day rolling).\n", "---\n"]
    if not candidates:
        L.append(f"No {sector} candidates today.\n")
    else:
        candidates.sort(key=lambda x:(-int(x.get("_trough",False)),-x["_score"],-(x["pct_off_high"] or 0)))
        for c in candidates:
            stars = "★"*c["_score"] + "☆"*(5-c["_score"])
            tag = "  🔻 TROUGH SETUP" if c.get("_trough") else ""
            e = watchlist.get(c["symbol"], {})
            trend = _trend(e) if e else "new"
            seen_since = e.get("first_seen", today)
            L += [f"## {c['symbol']} — {c['name']}  {stars}{tag}",
                  f"**Industry:** {c['industry']} | **Tracked since:** {seen_since} | **Trend:** {trend}",
                  f"**Price:** ${c['price']:.2f} | **Mkt cap:** ${c['market_cap_bn']}bn",
                  f"**Valuation:** P/B {c['price_to_book']:.2f} | "
                  + (f"Fwd P/E {c['forward_pe']:.1f} | " if c['forward_pe'] else "Fwd P/E N/A | ")
                  + (f"EV/EBITDA {c['ev_ebitda']:.1f}" if c['ev_ebitda'] else "EV/EBITDA N/A (neg)"),
                  f"**Price action:** {c['pct_off_high']}% off 52w high (${c['52w_low']}-${c['52w_high']})",
                  "**Balance sheet:** "
                  + (f"D/E {c['debt_to_equity']:.0f}% | " if c['debt_to_equity'] else "D/E N/A | ")
                  + (f"Current {c['current_ratio']:.2f} | " if c['current_ratio'] else "")
                  + f"Cash ${c['total_cash_bn']}bn / Debt ${c['total_debt_bn']}bn", ""]
            if c.get("_trough_reasons"):
                L.append("**🔻 Trough signals:** " + "; ".join(c["_trough_reasons"]))
            if c["_signals"]:
                L.append("**Quality signals:** " + "; ".join(c["_signals"]))
            # Show trajectory if we have history
            h = e.get("history", [])
            if len(h) > 1:
                pts = ", ".join(f"{o['date']}: {o['pct_off_high']}% off" for o in h[-4:])
                L.append(f"**Trajectory:** {pts}")
            L.append("\n---\n")
    return "\n".join(L)


def serialize_candidate(c: dict) -> dict:
    """Flatten a scanned candidate to the JSON fields the web dashboard needs."""
    return {
        "symbol": c["symbol"], "name": c["name"],
        "sector": c.get("sector", ""), "industry": c.get("industry", ""),
        "price": c["price"], "market_cap_bn": c["market_cap_bn"],
        "pct_off_high": c["pct_off_high"],
        "week52_low": c["52w_low"], "week52_high": c["52w_high"],
        "price_to_book": c["price_to_book"], "forward_pe": c["forward_pe"],
        "ev_ebitda": c["ev_ebitda"], "trailing_pe": c["trailing_pe"],
        "debt_to_equity": c["debt_to_equity"], "current_ratio": c["current_ratio"],
        "total_cash_bn": c["total_cash_bn"], "total_debt_bn": c["total_debt_bn"],
        "score": c["_score"], "signals": c["_signals"],
        "trough": c.get("_trough", False), "trough_reasons": c.get("_trough_reasons", []),
    }


def run_scan(sector=None, tickers_override=None, watchlist_path=None, use_screen=False) -> dict:
    today = date.today().strftime("%Y-%m-%d")
    if not sector and not tickers_override:
        sector = WEEKDAY_SECTOR[date.today().weekday()]

    if tickers_override:
        batches = {"Override": tickers_override}
    elif use_screen:
        # Stage 1: ticker universe from the Nasdaq screener. No fallback — if it
        # returns nothing (network, taxonomy drift), the run fails loudly so the
        # cause gets fixed rather than silently scanning a stale list.
        from nasdaq_screen import screen_sector
        tickers = screen_sector(sector)
        if not tickers:
            print(f"FATAL: Nasdaq screener returned no tickers for {sector}. "
                  f"Check network and the sector name.", file=sys.stderr)
            sys.exit(1)
        batches = {sector: tickers}
    else:
        if sector not in UNIVERSE:
            print(f"Unknown sector. Options: {list(UNIVERSE)}", file=sys.stderr); sys.exit(1)
        batches = {sector: UNIVERSE[sector]}

    watchlist = prune_watchlist(load_watchlist(watchlist_path)) if watchlist_path else {}
    candidates, total = [], 0
    scanned_sector = list(batches.keys())[0]

    for sec, tickers in batches.items():
        print(f"\nScanning {sec} ({len(tickers)})...", file=sys.stderr)
        for sym in tickers:
            total += 1
            print(f"  {sym}", end=" ", flush=True, file=sys.stderr)
            d = fetch_ticker_data(sym)
            if not d:
                print("✗", file=sys.stderr); continue
            if not industry_matches(d, sec):
                print(f"· (industry: {d.get('industry') or 'n/a'})", file=sys.stderr); continue
            ok, fails = passes_screen(d)
            if ok:
                d["_score"], d["_signals"] = score_candidate(d)
                d["_trough"], d["_trough_reasons"] = detect_trough_setup(d)
                candidates.append(d)
                if watchlist_path:
                    record_observation(watchlist, d, d["_trough"], today, sec)
                print(f"✓ ({d['_score']}/5{' 🔻TROUGH' if d['_trough'] else ''})", file=sys.stderr)
            else:
                print(f"✗ [{', '.join(fails[:2])}]", file=sys.stderr)

    if watchlist_path:
        with open(watchlist_path, "w") as f:
            json.dump(watchlist, f, indent=2, default=str)

    report = build_report(candidates, scanned_sector, total, today, watchlist)
    trough = [c["symbol"] for c in candidates if c.get("_trough")]
    return {
        "report": report, "sector": scanned_sector, "date": today,
        "scanned": total, "n_candidates": len(candidates),
        "n_trough": len(trough),
        "should_analyze": len(trough) > 0,           # gate: any trough today
        "candidate_symbols": [c["symbol"] for c in candidates],
        "trough_symbols": trough,
        "watchlist_size": len(watchlist),
        "candidates": [serialize_candidate(c) for c in candidates],
    }


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--sector"); p.add_argument("--tickers", nargs="+")
    p.add_argument("--watchlist"); p.add_argument("--output"); p.add_argument("--result-json")
    p.add_argument("--screen", "--fmp", dest="screen", action="store_true",
                   help="Source universe from the Nasdaq screener (Stage 1)")
    a = p.parse_args()
    r = run_scan(sector=a.sector, tickers_override=a.tickers, watchlist_path=a.watchlist, use_screen=a.screen)
    if a.output:
        open(a.output,"w").write(r["report"]); print(f"report → {a.output}", file=sys.stderr)
    if a.result_json:
        json.dump({k:v for k,v in r.items() if k!="report"}, open(a.result_json,"w"), indent=2)
        print(f"result → {a.result_json}", file=sys.stderr)
    print(r["report"])
