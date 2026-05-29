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

function renderSummary(s, wlCount) {
  let alerts = "";
  if (s.n_trough > 0) alerts += ` · <span class="trough">${s.n_trough} 🔻 trough</span>`;
  if (s.n_momentum > 0) alerts += ` · <span class="momentum">${s.n_momentum} 🚀 momentum</span>`;
  $("summary").innerHTML =
    `<b>${esc(s.sector || "—")}</b> · ${esc(s.date || "")} · ` +
    `scanned <b>${s.scanned ?? "?"}</b> · <b>${s.n_candidates ?? 0}</b> passed${alerts}` +
    ` · <b>${wlCount}</b> tracked`;
}

// Compact markdown renderer for feed bodies (headings, bold/italic/code, lists,
// tables, hr, blockquote, links). Posters may also send pre-rendered body_html.
function md(src) {
  if (!src) return "";
  const inline = (s) => esc(s)
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
    .replace(/(^|[^*])\*([^*]+)\*/g, "$1<em>$2</em>")
    .replace(/\[([^\]]+)\]\((https?:\/\/[^)]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>');
  const lines = src.replace(/\r/g, "").split("\n");
  const out = [];
  let para = [], list = null;
  const flushPara = () => { if (para.length) { out.push(`<p>${inline(para.join(" "))}</p>`); para = []; } };
  const flushList = () => { if (list) { out.push(`<${list.t}>${list.items.map((x) => `<li>${inline(x)}</li>`).join("")}</${list.t}>`); list = null; } };
  const flush = () => { flushPara(); flushList(); };
  for (let i = 0; i < lines.length; i++) {
    const ln = lines[i];
    let m;
    if (/^\s*$/.test(ln)) { flush(); continue; }
    if ((m = ln.match(/^(#{1,6})\s+(.*)$/))) { flush(); out.push(`<h${m[1].length}>${inline(m[2])}</h${m[1].length}>`); continue; }
    if (/^\s*([-*_])\1{2,}\s*$/.test(ln)) { flush(); out.push("<hr>"); continue; }
    // Table: header row + separator row of ---/:--
    if (ln.includes("|") && i + 1 < lines.length && /^\s*\|?[\s:|-]*-[\s:|-]*\|?\s*$/.test(lines[i + 1])) {
      flush();
      const cells = (r) => r.replace(/^\s*\|/, "").replace(/\|\s*$/, "").split("|").map((c) => c.trim());
      const head = cells(ln); i++;
      const rows = [];
      while (i + 1 < lines.length && lines[i + 1].includes("|")) { rows.push(cells(lines[++i])); }
      out.push(`<table><thead><tr>${head.map((h) => `<th>${inline(h)}</th>`).join("")}</tr></thead><tbody>${
        rows.map((r) => `<tr>${r.map((c) => `<td>${inline(c)}</td>`).join("")}</tr>`).join("")}</tbody></table>`);
      continue;
    }
    if ((m = ln.match(/^\s*[-*]\s+(.*)$/))) { flushPara(); if (!list || list.t !== "ul") { flushList(); list = { t: "ul", items: [] }; } list.items.push(m[1]); continue; }
    if ((m = ln.match(/^\s*\d+\.\s+(.*)$/))) { flushPara(); if (!list || list.t !== "ol") { flushList(); list = { t: "ol", items: [] }; } list.items.push(m[1]); continue; }
    if ((m = ln.match(/^\s*>\s?(.*)$/))) { flush(); out.push(`<blockquote>${inline(m[1])}</blockquote>`); continue; }
    para.push(ln.trim());
  }
  flush();
  return out.join("\n");
}

const FEED_KIND = { scan: "📊", verdict: "🧠", note: "📝", alert: "🚨" };

function renderFeed(feed) {
  $("feed-count").textContent = `(${feed.length})`;
  if (!feed.length) { $("feed").innerHTML = `<p class="empty">No feed entries yet.</p>`; return; }
  $("feed").innerHTML = feed.map((m) => {
    const emoji = FEED_KIND[m.kind] || "•";
    const when = (m.ts || "").replace("T", " ").slice(0, 16);
    const body = m.body_html ? m.body_html : md(m.body || "");
    return `<article class="feed-item kind-${esc(m.kind)}">
      <div class="feed-head"><span class="fk">${emoji}</span>
        <b>${esc(m.title || "")}</b>
        <span class="feed-meta">${esc(m.source || "")} · ${esc(when)}</span></div>
      ${body ? `<div class="feed-body">${body}</div>` : ""}
    </article>`;
  }).join("");
}

// Strategy display metadata (emoji + CSS class). Falls back gracefully for any
// strategy added later without a mapping here.
const STRAT = {
  Trough: { emoji: "🔻", cls: "trough" },
  Momentum: { emoji: "🚀", cls: "momentum" },
};
const sMeta = (name) => STRAT[name] || { emoji: "•", cls: "other" };

function card(c) {
  const tags = c.tags || [];
  const alerts = c.alerts || [];
  // High-conviction badges, then plain tag chips for non-alert matches.
  const badges = alerts.map((n) => `<span class="badge ${sMeta(n).cls}">${sMeta(n).emoji} ${esc(n.toUpperCase())}</span>`).join(" ");
  const chips = tags.filter((n) => !alerts.includes(n))
    .map((n) => `<span class="chip ${sMeta(n).cls}">${sMeta(n).emoji} ${esc(n)}</span>`).join(" ");
  // One reasons line per strategy (high-conviction reasons, else signals).
  const reasons = Object.entries(c.strategies || {}).map(([n, r]) => {
    const detail = (r.conviction_reasons && r.conviction_reasons.length ? r.conviction_reasons : r.signals) || [];
    if (!detail.length) return "";
    const hc = r.high_conviction ? " is-hc" : "";
    return `<div class="sig ${sMeta(n).cls}${hc}">${sMeta(n).emoji} <b>${esc(n)}:</b> ${detail.map(esc).join("; ")}</div>`;
  }).join("");
  const row = (k, val) => `<div><span class="k">${k}</span><span>${val}</span></div>`;
  const cls = alerts.length ? "is-" + sMeta(alerts[0]).cls : "";
  return `<div class="card ${cls}">
    <div class="top">
      <div><span class="sym">${esc(c.symbol)}</span> <span class="nm">${esc(c.name)}</span></div>
      <span class="stars">${stars(c.score)}</span>
    </div>
    <div class="nm">${esc(c.industry || "")}</div>
    <div class="badges">${badges} ${chips}</div>
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
    ${reasons}
  </div>`;
}

function renderCandidates(cands) {
  $("cand-count").textContent = `(${cands.length})`;
  if (!cands.length) { $("candidates").innerHTML = `<p class="empty">No candidates in the latest scan.</p>`; return; }
  cands.sort((a, b) => ((b.alerts?.length || 0) - (a.alerts?.length || 0)) || (b.score - a.score) || ((b.pct_off_high || 0) - (a.pct_off_high || 0)));
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

// API endpoint: same-origin api.php by default; override with ?api=<url>.
const API = new URLSearchParams(location.search).get("api") || "api.php";

async function main() {
  try {
    const res = await fetch(`${API}?action=all`, { cache: "no-store" });
    if (!res.ok) throw new Error("HTTP " + res.status);
    const d = await res.json();
    const scan = d.scan || {};
    const watchlist = d.watchlist || {};
    renderSummary(scan, Object.keys(watchlist).length);
    renderFeed(d.feed || []);
    renderCandidates(scan.candidates || []);
    renderWatchlist(watchlist);
    $("generated").textContent = "Generated " + (d.generated_at || "").replace("T", " ");
  } catch (e) {
    $("summary").innerHTML = `<span class="empty">Could not load ${esc(API)} (${esc(e.message)}).</span>`;
  }
}
main();
