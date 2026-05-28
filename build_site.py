"""
build_site.py — assemble the static dashboard's data feed (runs on the T480s).

Merges three sources into site/data.json, which the static frontend
(site/index.html + app.js) fetches and renders:

  1. result.json     — today's scan summary + structured candidates (from scan.py)
  2. watchlist.json  — each tracked name's observation history (for sparklines)
  3. analysis.md     — the Claude routine's verdict, auto-pulled from the latest
                       origin/claude/* branch (the routine pushes there, not main)

The verdict markdown is pre-rendered to HTML here so the frontend needs no JS
markdown library. If the `markdown` package isn't installed, the raw text is
shown in a <pre> block instead (graceful degradation).

Usage:  python3 build_site.py            # uses ./result.json, ./watchlist.json
        python3 build_site.py --result result.json --watchlist watchlist.json \
                              --out site/data.json
"""

import argparse
import html
import json
import os
import subprocess
import sys
from datetime import datetime, timezone

from scan import _trend   # reuse the watchlist trajectory classifier


def _md_to_html(md: str) -> str:
    """Render markdown to HTML; fall back to escaped <pre> if no lib available."""
    if not md:
        return ""
    try:
        import markdown
        return markdown.markdown(md, extensions=["tables", "fenced_code", "sane_lists"])
    except Exception:
        return f"<pre class='raw-md'>{html.escape(md)}</pre>"


def _git(*args) -> str:
    """Run a git command, return stdout (stripped); '' on any failure."""
    try:
        return subprocess.run(["git", *args], capture_output=True, text=True,
                              timeout=60).stdout.strip()
    except Exception:
        return ""


def latest_verdict() -> dict | None:
    """Fetch and return the newest analysis.md from an origin/claude/* branch."""
    _git("fetch", "--quiet", "--prune", "origin")
    refs = _git("for-each-ref", "--sort=-committerdate",
                "--format=%(refname:short)", "refs/remotes/origin/claude")
    for ref in [r for r in refs.splitlines() if r.strip()]:
        md = _git("show", f"{ref}:analysis.md")
        if md:
            commit_date = _git("show", "-s", "--format=%cI", ref)
            return {"branch": ref, "committed_at": commit_date,
                    "raw": md, "html": _md_to_html(md)}
    return None


def build(result_path: str, watchlist_path: str) -> dict:
    with open(result_path) as f:
        result = json.load(f)
    watchlist = {}
    if os.path.exists(watchlist_path):
        with open(watchlist_path) as f:
            watchlist = json.load(f)

    # Decorate each watchlist entry with a trend label for the frontend.
    wl_out = {}
    for sym, e in watchlist.items():
        wl_out[sym] = {
            "name": e.get("name", sym), "sector": e.get("sector", ""),
            "first_seen": e.get("first_seen"), "last_seen": e.get("last_seen"),
            "ever_trough": e.get("ever_trough", False),
            "trend": _trend(e), "history": e.get("history", []),
        }

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "scan": {k: result.get(k) for k in
                 ("sector", "date", "scanned", "n_candidates", "n_trough",
                  "should_analyze", "watchlist_size", "trough_symbols")},
        "candidates": result.get("candidates", []),
        "watchlist": wl_out,
        "verdict": latest_verdict(),
    }


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--result", default="result.json")
    p.add_argument("--watchlist", default="watchlist.json")
    p.add_argument("--out", default="site/data.json")
    a = p.parse_args()

    data = build(a.result, a.watchlist)
    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    with open(a.out, "w") as f:
        json.dump(data, f, indent=2, default=str)
    v = data["verdict"]
    print(f"site data → {a.out} "
          f"({len(data['candidates'])} candidates, "
          f"{len(data['watchlist'])} watched, "
          f"verdict={'yes' if v else 'none'})", file=sys.stderr)
