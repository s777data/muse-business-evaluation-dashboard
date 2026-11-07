// Renders ../output/scores.json produced by: python -m src.evaluator.run MU
// Pure rendering — no scoring happens in the browser, no model is called.
const fmt$ = v => v == null ? "—" : "$" + Number(v).toLocaleString(undefined, {maximumFractionDigits: 0});
const fmtB = v => v == null ? "—" : "$" + (Number(v) / 1e9).toFixed(1) + "B";
const fmtX = v => v == null ? "—" : Number(v).toFixed(2) + "x";
const fmtP = v => v == null ? "—" : Number(v).toFixed(1) + "%";

function rows(el, pairs) {
  el.innerHTML = pairs.map(([k, v]) => `<tr><td>${k}</td><td>${v}</td></tr>`).join("");
}

async function load() {
  let data;
  for (const path of ["../output/scores.json", "./scores.json", "scores.json"]) {
    try { const r = await fetch(path); if (r.ok) { data = await r.json(); break; } } catch (e) {}
  }
  if (!data) { document.getElementById("meta").textContent = "scores.json not found — run: python -m src.evaluator.run MU"; return; }
  const k = data.kpis, s = data.scores, d = data.dcf, p = data.inputs_with_sources.price;
  document.getElementById("title").textContent = `${data.symbol} — ${data.entity_name}`;
  document.getElementById("meta").textContent =
    `Snapshot ${data.snapshot_date} · Price $${p.last_close} on ${p.last_date} (${p.source}) · Filing metrics: SEC EDGAR, FY ended ${k.fiscal_year_end}`;

  document.getElementById("score-cards").innerHTML = ["business", "value", "moat"].map(name => `
    <div class="card"><div class="label">${name} score</div>
    <div class="score">${s[name].total}<span class="muted">/10</span></div>
    <div class="muted">${Object.entries(s[name].subscores).map(([m, e]) => `${m.replaceAll("_", " ")}: ${e.score}`).join(" · ")}</div></div>`).join("");

  const max = Math.max(d.current_price, d.fair_value_per_share) * 1.1;
  document.getElementById("dcf").innerHTML = `
    <div class="bar-wrap"><div class="bar-price" style="width:${d.current_price / max * 100}%"></div>
    <div class="bar-dcf" style="left:${d.fair_value_per_share / max * 100}%"></div></div>
    <p>Current $${d.current_price} · DCF fair value $${d.fair_value_per_share} ·
    distance $${d.distance_dollars} (${d.upside_pct > 0 ? "+" : ""}${d.upside_pct}%) ·
    WACC ${d.wacc.wacc_pct}% · FCF/share base $${d.fcf_per_share_base} · growth path ${d.growth_path_pct.join("%, ")}%</p>`;

  rows(document.getElementById("kpis"), [
    ["Revenue (latest FY)", fmtB(k.revenue_latest)], ["Revenue growth (latest FY)", fmtP(k.revenue_growth_latest_pct)],
    ["Revenue CAGR (filed, up to 5y)", fmtP(k.revenue_cagr_5y_pct)],
    ["Gross margin — latest / 5y median", `${fmtP(k.gross_margin_latest_pct)} / ${fmtP(k.gross_margin_median_5y_pct)}`],
    ["Gross margin stdev (5y)", k.gross_margin_stdev_5y_pts == null ? "—" : k.gross_margin_stdev_5y_pts.toFixed(1) + " pts"],
    ["Operating margin (5y median)", fmtP(k.operating_margin_median_5y_pct)],
    ["Free cash flow (latest FY)", fmtB(k.fcf_latest)], ["FCF margin (5y median)", fmtP(k.fcf_margin_median_5y_pct)],
    ["Net cash", fmtB(k.net_cash_latest)], ["ROIC (5y median, after config tax)", fmtP(k.roic_median_5y_pct)],
    ["R&D (latest FY)", fmtB(k.rd_expense_latest)], ["Capex intensity", fmtP(k.capex_intensity_latest_pct)],
  ]);

  rows(document.getElementById("valuation"), [
    ["Trailing P/E (filed diluted EPS)", fmtX(k.trailing_pe)], ["Price / Sales", fmtX(k.price_to_sales)],
    ["EV / Sales", fmtX(k.ev_to_sales)], ["FCF yield (latest FY)", fmtP(k.fcf_yield_latest_pct)],
    ["Market cap", fmtB(k.market_cap)], ["Discount from 52-week high", fmtP(k.discount_from_52w_high_pct)],
  ]);

  document.getElementById("peer-note").textContent = data.peers.excluded_note || "";
  const pr = data.peers.rows;
  document.getElementById("peers").innerHTML =
    "<tr><th>Peer</th><th>Revenue</th><th>Gross margin</th><th>FCF margin</th><th>P/S</th></tr>" +
    pr.map(r => `<tr><td>${r.symbol} — ${r.name}</td><td>${fmtB(r.revenue)}</td><td>${fmtP(r.gross_margin_latest_pct)}</td><td>${fmtP(r.fcf_margin_latest_pct)}</td><td>${fmtX(r.price_to_sales)}</td></tr>`).join("");
  document.getElementById("percentiles").innerHTML =
    "<p>Percentile ranks for " + data.symbol + " vs this peer set: " +
    Object.entries(data.peers.percentile_ranks_for_subject).map(([m, v]) => `${m.replaceAll("_", " ")}: ${v == null ? "—" : v + "th"}`).join(" · ") + "</p>";

  document.getElementById("moat-checks").innerHTML = data.moat_replicability_checks.map(c =>
    `<p><span class="badge">${c.status}</span> <strong>${c.check}</strong><br><span class="muted">${c.evidence}</span></p>`).join("");

  const c = data.customer_concentration || {};
  document.getElementById("concentration").innerHTML =
    [["Single customer", c.single_customer], ["Top ten customers", c.top_ten], ["Data center end market", c.data_center]]
    .map(([label, e]) => e ? `<p><strong>${label}</strong> — concentration sub-score input</p><blockquote>“${e.quote}”</blockquote>` : `<p><strong>${label}</strong> — not found in filing text</p>`).join("") +
    `<p class="muted">Source: ${data.inputs_with_sources.concentration.url}</p>`;

  document.getElementById("explanations").innerHTML =
    Object.entries(data.explanations).map(([name, text]) => `<p><strong>${name[0].toUpperCase() + name.slice(1)}:</strong> ${text}</p>`).join("");
  document.getElementById("data-quality").innerHTML =
    Object.entries(data.data_quality).map(([k2, v]) => `<p>${k2}: ${typeof v === "object" ? JSON.stringify(v) : v}</p>`).join("");
}
load();
