"""
notify.py — send ntfy notifications for a scan, ONE message per candidate.

Replaces the old single message that dumped a truncated `head -c 1200` of
report.md (which got cut off mid-report). Each candidate now gets its own short,
complete notification built from result.json's structured fields; troughs are
flagged high-priority. A zero-candidate scan sends a single "nothing today" note
so you still know the run happened.

Reads config from the environment (run_scan.sh exports these from .env):
  NTFY_SERVER NTFY_TOKEN NTFY_REPORT_TOPIC DASHBOARD_URL

Usage:  python3 notify.py result.json
"""

import json
import os
import sys

import requests


def _ascii(s: str) -> str:
    """ntfy header values (Title) must be latin-1 — strip non-ASCII safely."""
    return (str(s).encode("ascii", "ignore").decode().strip() or "?")


def _send(body: str, *, title: str, tags: str, prio: str) -> bool:
    server = os.environ.get("NTFY_SERVER", "https://ntfy.sh").rstrip("/")
    topic = os.environ.get("NTFY_REPORT_TOPIC", "")
    if not topic:
        print("  ntfy skipped (NTFY_REPORT_TOPIC unset)", file=sys.stderr)
        return False
    headers = {"Title": _ascii(title), "Tags": tags, "Priority": prio}
    token = os.environ.get("NTFY_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    click = os.environ.get("DASHBOARD_URL")
    if click:
        headers["Click"] = click
    try:
        r = requests.post(f"{server}/{topic}", data=body.encode("utf-8"),
                          headers=headers, timeout=20)
        r.raise_for_status()
        return True
    except Exception as e:
        print(f"  ntfy fail ({title}): {e}", file=sys.stderr)
        return False


# Per-strategy ntfy tag (emoji) + whether a high-conviction hit is high-priority.
STRATEGY_NTFY = {
    "Trough":   {"emoji": "🔻", "tag": "rotating_light"},
    "Momentum": {"emoji": "🚀", "tag": "rocket"},
}
DEFAULT_TAG = "chart_with_upwards_trend"


def _n(v, nd=2, suffix=""):
    return "N/A" if v is None else f"{float(v):.{nd}f}{suffix}"


def _candidate_body(c: dict, sector: str, date: str) -> str:
    ev = "N/A (neg)" if not c.get("ev_ebitda") else _n(c["ev_ebitda"], 1)
    fpe = "N/A" if not c.get("forward_pe") else _n(c["forward_pe"], 1)
    tags = c.get("tags", [])
    lines = [
        f"{sector} · {date} · {'+'.join(tags) if tags else 'candidate'}",
        f"${_n(c['price'])} · {_n(c.get('pct_off_high'), 1, '%')} off high · ${c.get('market_cap_bn')}bn cap",
        f"P/B {_n(c.get('price_to_book'))} · Fwd P/E {fpe} · EV/EBITDA {ev}",
        f"D/E {_n(c.get('debt_to_equity'), 0, '%')} · Current {_n(c.get('current_ratio'))}",
    ]
    # One line per strategy: high-conviction reasons take priority, else signals.
    for name, r in (c.get("strategies") or {}).items():
        em = STRATEGY_NTFY.get(name, {}).get("emoji", "•")
        detail = r.get("conviction_reasons") or r.get("signals")
        if detail:
            lines.append(f"{em} {name}: " + "; ".join(detail))
    return "\n".join(lines)


def main(result_path: str) -> int:
    with open(result_path) as f:
        r = json.load(f)
    sector, date = r.get("sector", "?"), r.get("date", "")
    cands = r.get("candidates", [])

    if not cands:
        ok = _send(f"Scanned {r.get('scanned', '?')} {sector} tickers — nothing passed.",
                   title=f"Scan: {sector} — 0 hits", tags="heavy_minus_sign", prio="default")
        return 0 if ok else 1

    # High-conviction alerts first, then by score — most important notifications
    # arrive last so they sit at the top of the phone's notification stack.
    cands.sort(key=lambda c: (bool(c.get("alerts")), c.get("score", 0)))
    sent = 0
    for c in cands:
        alerts = c.get("alerts", [])          # strategies flagging it high-conviction
        tags = c.get("tags", [])
        stars = "★" * int(c.get("score", 0))
        if alerts:
            title = f"{'/'.join(a.upper() for a in alerts)}: {c['symbol']} {c.get('name','')} {stars}"
            ntfy_tag = STRATEGY_NTFY.get(alerts[0], {}).get("tag", DEFAULT_TAG)
            prio = "high"
        else:
            label = f" [{'+'.join(tags)}]" if tags else ""
            title = f"{c['symbol']} {c.get('name','')}{label} {stars}"
            ntfy_tag = DEFAULT_TAG
            prio = "default"
        ok = _send(_candidate_body(c, sector, date),
                   title=title.strip(), tags=ntfy_tag, prio=prio)
        sent += int(ok)
    print(f"  ntfy: {sent}/{len(cands)} candidate messages sent", file=sys.stderr)
    return 0 if sent == len(cands) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "result.json"))
