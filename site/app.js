// Trough Scanner dashboard — fetches data.json (built by build_site.py) and
// renders it. Vanilla JS, no dependencies, so it runs on any static host.

const $ = (id) => document.getElementById(id);

const fmtMoney = (v) => v == null ? "N/A" : "$" + Number(v).toLocaleString(undefined, { maximumFractionDigits: 2 });
const fmtNum = (v, d = 2) => v == null ? "N/A" : Number(v).toFixed(d);
const fmtPct = (v, d = 1) => v == null ? "N/A" : Number(v).toFixed(d) + "%";
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

function stars(score) {
  score = Math.max(0, Math.min(5, score | 0));
  return "★".repeat(score) + "☆".repeat(5 - score);
}

// Inline-SVG sparkline of a name's "% off high" history (more off = lower price).
function sparkline(history, w = 90, h = 24) {
  const pts = history.map((o) => o.pct_off_high).filter((v) => v != null);
  if (pts.length < 2) return `<svg class="spark" width="${w}" height="${h}"></svg>`;
  const min = Math.min(...pts), max = Math.max(...pts), span = max - min || 1;
  const step = w / (pts.length - 1);
  const coords = pts.map((v, i) => `${(i * step).toFixed(1)},${(h - 2 - ((v - min) / span) * (h - 4)).toFixed(1)}`);
  const rising = pts[pts.length - 1] >= pts[0]; // more off high over time => cheaper
  const color = rising ? "var(--trough)" : "var(--good)";
  return `<svg class="spark" width="${w}" height="${h}" viewBox="0 0 ${w} ${h}">
    <polyline fill="none" stroke="${color}" stroke-width="1.5" points="${coords.join(" ")}"/>
  </svg>`;
}

function renderSummary(d) {
  const s = d.scan || {};
  const troughTxt = s.n_trough > 0 ? ` · <span class="trough">${s.n_trough} 🔻 trough</span>` : "";
  $("summary").innerHTML =
    `<b>${esc(s.sector || "—")}</b> · ${esc(s.date || "")} · ` +
    `scanned <b>${s.scanned ?? "?"}</b> · <b>${s.n_candidates ?? 0}</b> passed${troughTxt}` +
    ` · <b>${s.watchlist_size ?? 0}</b> tracked`;
}

function renderVerdict(v) {
  if (!v) return;
  $("verdict-section").hidden = false;
  $("verdict-meta").textContent =
    `from ${v.branch || "routine"}${v.committed_at ? " · " + v.committed_at.slice(0, 16).replace("T", " ") : ""}`;
  $("verdict").innerHTML = v.html || "";
}

function card(c) {
  const reasons = c.trough_reasons?.length
    ? `<div class="sig reasons">🔻 ${c.trough_reasons.map(esc).join("; ")}</div>` : "";
  const signals = c.signals?.length
    ? `<div class="sig">${c.signals.map(esc).join("; ")}</div>` : "";
  const row = (k, val) => `<div><span class="k">${k}</span><span>${val}</span></div>`;
  return `<div class="card ${c.trough ? "is-trough" : ""}">
    <div class="top">
      <div><span class="sym">${esc(c.symbol)}</span> <span class="nm">${esc(c.name)}</span></div>
      <span class="stars">${stars(c.score)}</span>
    </div>
    <div class="nm">${esc(c.industry || "")}</div>
    ${c.trough ? '<span class="badge">TROUGH SETUP</span>' : ""}
    <div class="grid">
      ${row("Price", fmtMoney(c.price))}
      ${row("Mkt cap", c.market_cap_bn != null ? "$" + c.market_cap_bn + "bn" : "N/A")}
      ${row("P/B", fmtNum(c.price_to_book))}
      ${row("Fwd P/E", c.forward_pe ? fmtNum(c.forward_pe, 1) : "N/A")}
      ${row("EV/EBITDA", c.ev_ebitda ? fmtNum(c.ev_ebitda, 1) : "N/A (neg)")}
      ${row("Off high", fmtPct(c.pct_off_high))}
      ${row("D/E", c.debt_to_equity != null ? fmtNum(c.debt_to_equity, 0) + "%" : "N/A")}
      ${row("Current", c.current_ratio != null ? fmtNum(c.current_ratio) : "N/A")}
      ${row("Cash/Debt", `$${c.total_cash_bn}bn / $${c.total_debt_bn}bn`)}
      ${row("52w range", `$${c.week52_low}–$${c.week52_high}`)}
    </div>
    ${reasons}${signals}
  </div>`;
}

function renderCandidates(cands) {
  $("cand-count").textContent = `(${cands.length})`;
  if (!cands.length) { $("candidates").innerHTML = `<p class="empty">No candidates in the latest scan.</p>`; return; }
  cands.sort((a, b) => (b.trough - a.trough) || (b.score - a.score) || ((b.pct_off_high || 0) - (a.pct_off_high || 0)));
  $("candidates").innerHTML = cands.map(card).join("");
}

function renderWatchlist(wl) {
  const syms = Object.keys(wl);
  $("wl-count").textContent = `(${syms.length})`;
  if (!syms.length) { $("watchlist").innerHTML = `<p class="empty">Nothing tracked yet.</p>`; return; }
  syms.sort((a, b) => (wl[b].ever_trough - wl[a].ever_trough) || a.localeCompare(b));
  $("watchlist").innerHTML = syms.map((sym) => {
    const e = wl[sym];
    return `<div class="wl-row">
      <div><b>${esc(sym)}</b> <span class="nm">${esc(e.name)}</span><br>
        <span class="nm">${esc(e.sector || "")} · seen ${esc(e.first_seen || "")}→${esc(e.last_seen || "")}</span></div>
      ${sparkline(e.history || [])}
      <span class="trend ${e.trend}">${e.trend}${e.ever_trough ? " 🔻" : ""}</span>
    </div>`;
  }).join("");
}

async function main() {
  try {
    const res = await fetch("data.json", { cache: "no-store" });
    if (!res.ok) throw new Error("HTTP " + res.status);
    const d = await res.json();
    renderSummary(d);
    renderVerdict(d.verdict);
    renderCandidates(d.candidates || []);
    renderWatchlist(d.watchlist || {});
    $("generated").textContent = "Generated " + (d.generated_at || "").replace("T", " ");
  } catch (e) {
    $("summary").innerHTML = `<span class="empty">Could not load data.json (${esc(e.message)}).</span>`;
  }
}
main();
