"""
api_client.py — talk to the scanner hub (site/api.php) from the T480s.

Replaces the git data-bridge + static publish for *data*: after a scan, push the
results as state (so the dashboard renders them and the Claude routine can read
each name's trajectory) and drop a one-line event in the feed. Also lets you
post manual notes to the feed.

Config (env, exported from .env by run_scan.sh):
  SCANNER_API_URL    e.g. https://stocks.example.ee/api.php
  SCANNER_API_TOKEN  shared secret (matches api_config.php on the server)

Usage:
  python3 api_client.py push --result result.json --watchlist watchlist.json
  python3 api_client.py note --title "Eyeing LI" --body "watching June deliveries"
"""

import argparse
import json
import os
import sys

import requests


def _cfg() -> tuple[str, str]:
    url = os.environ.get("SCANNER_API_URL", "")
    token = os.environ.get("SCANNER_API_TOKEN", "")
    if not url:
        print("SCANNER_API_URL not set — skipping API push", file=sys.stderr)
        sys.exit(0)        # soft no-op so run_scan.sh doesn't fail when unconfigured
    return url, token


def post_message(*, source, kind, title, body="", body_html="", meta=None) -> bool:
    url, token = _cfg()
    try:
        r = requests.post(f"{url}?action=message",
                          headers={"X-Auth": token},
                          json={"source": source, "kind": kind, "title": title,
                                "body": body, "body_html": body_html, "meta": meta},
                          timeout=20)
        r.raise_for_status()
        return True
    except Exception as e:
        print(f"  api message failed: {e}", file=sys.stderr)
        return False


def put_state(name: str, data) -> bool:
    url, token = _cfg()
    try:
        r = requests.post(f"{url}?action=state&name={name}",
                          headers={"X-Auth": token}, json={"data": data}, timeout=20)
        r.raise_for_status()
        return True
    except Exception as e:
        print(f"  api state '{name}' failed: {e}", file=sys.stderr)
        return False


def _trend(e: dict) -> str:
    """One-word trajectory of % off high (kept in sync with scan._trend)."""
    h = [o for o in e.get("history", []) if o.get("pct_off_high") is not None]
    if len(h) < 2:
        return "new"
    first, last = h[0]["pct_off_high"], h[-1]["pct_off_high"]
    if last - first >= 5:
        return "cheaper"
    if first - last >= 5:
        return "recovering"
    return "flat"


def push_scan(result_path: str, watchlist_path: str) -> None:
    with open(result_path) as f:
        r = json.load(f)

    # State: scan (summary + candidates) and watchlist (with a trend label).
    scan_state = {k: r.get(k) for k in (
        "sector", "date", "scanned", "n_candidates", "n_trough", "n_momentum",
        "trough_symbols", "momentum_symbols", "by_strategy", "candidates")}
    put_state("scan", scan_state)

    if os.path.exists(watchlist_path):
        with open(watchlist_path) as f:
            wl = json.load(f)
        wl_state = {sym: {
            "name": e.get("name", sym), "sector": e.get("sector", ""),
            "first_seen": e.get("first_seen"), "last_seen": e.get("last_seen"),
            "ever_trough": e.get("ever_trough", False), "tags": e.get("tags", []),
            "trend": _trend(e), "history": e.get("history", []),
        } for sym, e in wl.items()}
        put_state("watchlist", wl_state)

    # Feed: one event line summarising the scan.
    sector, date = r.get("sector", "?"), r.get("date", "")
    parts = [f"{r.get('n_candidates', 0)} hits"]
    if r.get("n_trough"):
        parts.append(f"{r['n_trough']} 🔻 {', '.join(r.get('trough_symbols', []))}")
    if r.get("n_momentum"):
        parts.append(f"{r['n_momentum']} 🚀 {', '.join(r.get('momentum_symbols', []))}")
    post_message(source="t480s", kind="scan",
                 title=f"Scan: {sector} — {parts[0]}", body=" · ".join(parts),
                 meta={"date": date, "sector": sector})


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)
    ps = sub.add_parser("push"); ps.add_argument("--result", default="result.json")
    ps.add_argument("--watchlist", default="watchlist.json")
    pn = sub.add_parser("note"); pn.add_argument("--title", required=True)
    pn.add_argument("--body", default=""); pn.add_argument("--source", default="you")
    a = p.parse_args()

    if a.cmd == "push":
        push_scan(a.result, a.watchlist)
        print("  pushed scan to API", file=sys.stderr)
    elif a.cmd == "note":
        ok = post_message(source=a.source, kind="note", title=a.title, body=a.body)
        print("  note posted" if ok else "  note failed", file=sys.stderr)
