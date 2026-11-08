// Repo-engine port — muse-business-evaluation-dashboard (github.com/s777data).
//
// A faithful TypeScript port of the Python engine in src/evaluator/:
//   EDGAR companyfacts -> KPI metrics -> config-anchor sub-scores
//   EDGAR 10-K text    -> customer concentration evidence (verbatim quotes)
//   Yahoo chart API    -> latest close / 52-week range (Stooq fallback,
//                         fail closed if neither returns data)
//   Config DCF         -> CAPM-built WACC, filed-CAGR growth clamped + faded
//   Trailing multiples -> TTM bridge: latest annual + current-FY YTD −
//                         prior-year YTD (same period), from 10-Q XBRL rows.
//                         Annual revenue alone overstates P/S when the fiscal
//                         year ended months ago (NVDA FY ended Jan 2026).
//                         5-yr medians and the DCF keep annual history.
//
// v2 (repo parity): total debt is RESOLVED at the latest balance date
// across every debt concept (pair first, then single totals — see
// DEBT_RESOLUTION below), never read as zero when it cannot be
// determined (fail closed: net-cash and leverage sub-scores go missing).
// Business adds leverage (debt / TTM revenue) and interest-coverage
// sub-scores with the repo's rebalanced weights.
//
// v3 (repo parity): FCF is SBC-adjusted owner FCF (operating cash flow
// − capex − stock-based compensation) everywhere it is used — the FCF
// margin median, TTM FCF / FCF yield, and the DCF base. Adjusted FCF
// exists only for years where SBC is reported; if no year reports SBC
// the FCF margin sub-score goes missing (fail closed) rather than using
// the unadjusted figure. Business adds a dilution sub-score: CAGR of
// diluted weighted-average shares over the available span (up to 5
// years), with a continuity guard — a >5x or <0.2x single-year jump in
// the filed series is a split/units break, so dilution goes missing and
// is flagged instead of being scored off the break.
//
// Every weight and anchor below is copied verbatim from the repo's
// config/scoring.yaml and config/dcf.yaml. No model is called anywhere.
// Missing inputs are reported and weights renormalise over what is present
// — exactly the repo's rule. Beta is the one per-ticker input with no free
// keyless source in the repo (config/tickers.yaml): for a searched ticker
// we try Yahoo's key statistics, and if that fails the DCF and the
// value score's DCF sub-score are marked missing rather than invented.

const UA = "Muse Business Evaluation Dashboard admin@example.com";

/* ------------------------- config (repo verbatim) ------------------------- */

type Anchors = [number, number][];
interface SubCfg { weight: number; anchors: Anchors }

const BUSINESS_CFG: Record<string, SubCfg> = {
  revenue_growth_latest_pct: { weight: 0.075, anchors: [[-20, 0], [0, 3], [25, 6], [75, 8.5], [150, 10]] },
  revenue_cagr_5y_pct: { weight: 0.075, anchors: [[-5, 1], [0, 3], [10, 5.5], [20, 7.5], [30, 9], [40, 10]] },
  gross_margin_median_5y_pct: { weight: 0.15, anchors: [[0, 0], [15, 2.5], [30, 4.5], [45, 6.5], [60, 8.5], [75, 10]] },
  operating_margin_median_5y_pct: { weight: 0.15, anchors: [[-10, 0], [0, 3], [10, 5], [20, 7], [35, 9], [50, 10]] },
  fcf_margin_median_5y_pct: { weight: 0.15, anchors: [[-10, 0], [0, 2.5], [10, 5.5], [20, 8], [30, 10]] },
  net_cash_to_revenue: { weight: 0.10, anchors: [[-1.0, 0], [-0.25, 2], [0, 4.5], [0.25, 6.5], [0.5, 8], [1.0, 10]] },
  gross_margin_stdev_5y_pts: { weight: 0.10, anchors: [[0, 10], [5, 8], [10, 6], [20, 3.5], [30, 1.5], [40, 0]] },
  leverage_debt_to_revenue_ttm: { weight: 0.075, anchors: [[0, 10], [0.5, 8], [1.0, 6], [2.0, 4], [3.0, 2], [5.0, 0]] },
  interest_coverage_ttm: { weight: 0.075, anchors: [[0, 0], [1, 2], [2, 4], [4, 6], [8, 8], [15, 10]] },
  dilution_share_count_growth_pct: { weight: 0.05, anchors: [[-3, 10], [0, 8.5], [2, 6], [5, 3.5], [10, 1], [20, 0]] },
};

const VALUE_CFG: Record<string, SubCfg> = {
  trailing_pe: { weight: 0.20, anchors: [[3, 10], [5, 10], [10, 8], [15, 6.5], [25, 4.5], [40, 2.5], [60, 1]] },
  price_to_sales: { weight: 0.15, anchors: [[0.5, 10], [1, 9], [2, 7.5], [4, 5.5], [7, 3.5], [10, 2], [15, 0.5]] },
  ev_to_sales: { weight: 0.10, anchors: [[0.5, 10], [1, 9], [2, 7.5], [4, 5.5], [7, 3.5], [10, 2], [15, 0.5]] },
  fcf_yield_latest_pct: { weight: 0.20, anchors: [[-2, 0], [0, 1], [2, 3.5], [4, 5.5], [6, 7.5], [8, 9], [12, 10]] },
  dcf_upside_pct: { weight: 0.25, anchors: [[-50, 0], [-25, 2], [0, 4.5], [25, 6.5], [50, 8], [100, 10]] },
  discount_from_52w_high_pct: { weight: 0.10, anchors: [[0, 2], [-15, 4], [-30, 6], [-45, 7.5], [-60, 9]] },
};

const MOAT_CFG: Record<string, SubCfg> = {
  roic_median_5y_pct: { weight: 0.20, anchors: [[-5, 0], [0, 2.5], [8, 5], [15, 7.5], [25, 10]] },
  gross_margin_median_5y_pct: { weight: 0.20, anchors: [[0, 0], [15, 2.5], [30, 4.5], [45, 6.5], [60, 8.5], [75, 10]] },
  gross_margin_stdev_5y_pts: { weight: 0.15, anchors: [[0, 10], [5, 8], [10, 6], [20, 3.5], [30, 1.5], [40, 0]] },
  revenue_percentile_vs_peers: { weight: 0.15, anchors: [[0, 1], [25, 3.5], [50, 5.5], [75, 7.5], [100, 10]] },
  customer_concentration_score: { weight: 0.15, anchors: [[0, 0], [10, 10]] },
  capex_intensity_latest_pct: { weight: 0.15, anchors: [[0, 1], [5, 3], [15, 6], [25, 8], [35, 10]] },
};

// config/scoring.yaml concentration_rules
const CONC_RULES = {
  base_if_no_single_customer_at_threshold: 7.0,
  base_if_single_customer_present: 4.0,
  single_customer_extra_penalty_per_pct_over_20: 0.1,
  top10_free_allowance_pct: 40,
  top10_penalty_per_pct_over_allowance: 0.04,
  top10_approx_value_when_over_half_pct: 55,
  top10_approx_value_when_one_half_pct: 50,
  score_floor: 0,
  score_ceiling: 10,
};

// config/scoring.yaml moat_checks
const MOAT_CHECKS_CFG = {
  entrant_capex_barrier_min_capex_usd: 10_000_000_000,
  margin_fungibility_stdev_pts_over: 15,
  oligopoly_peer_count_max_for_concentrated: 10,
};

// config/dcf.yaml
const DCF_CFG = {
  risk_free_rate_pct: 4.3,
  equity_risk_premium_pct: 5.0,
  pre_tax_cost_of_debt_pct: 4.5,
  tax_rate_pct: 15.0,
  projection_years: 5,
  terminal_growth_pct: 0.0,
  growth_floor_pct: 0.0,
  growth_cap_pct: 25.0,
};

// config/tickers.yaml — the only tickers with a config beta in the repo.
const CONFIG_BETAS: Record<string, { beta: number; source: string }> = {
  MU: { beta: 2.3, source: "config input (long-run market beta used in prior DD; no free keyless beta source in v1)" },
};

// config/sources.yaml concepts
const CONCEPTS: Record<string, string[]> = {
  revenue: ["RevenueFromContractWithCustomerExcludingAssessedTax", "SalesRevenueNet", "Revenues"],
  gross_profit: ["GrossProfit"],
  cost_of_revenue: ["CostOfGoodsAndServicesSold", "CostOfRevenue", "CostOfGoodsSold"],
  operating_income: ["OperatingIncomeLoss"],
  net_income: ["NetIncomeLoss"],
  operating_cash_flow: ["NetCashProvidedByUsedInOperatingActivities"],
  capex: ["PaymentsToAcquirePropertyPlantAndEquipment", "PaymentsToAcquireProductiveAssets"],
  cash: ["CashAndCashEquivalentsAtCarryingValue"],
  investments_current: ["AvailableForSaleSecuritiesDebtSecuritiesCurrent"],
  investments_noncurrent: ["AvailableForSaleSecuritiesDebtSecuritiesNoncurrent"],
  total_debt: ["DebtAndCapitalLeaseObligations", "LongTermDebtAndCapitalLeaseObligations", "DebtInstrumentCarryingAmount", "Debt", "Borrowings", "LongTermBorrowings", "LongTermDebt"],
  interest_expense: ["InterestExpense", "InterestExpenseDebt", "InterestAndDebtExpense"],
  share_based_compensation: ["ShareBasedCompensation", "AllocatedShareBasedCompensationExpense"],
  diluted_shares: ["WeightedAverageNumberOfDilutedSharesOutstanding"],
  eps_diluted: ["EarningsPerShareDiluted"],
  rd_expense: ["ResearchAndDevelopmentExpense"],
  equity: ["StockholdersEquity"],
};
const BALANCE_METRICS = new Set(["cash", "investments_current", "investments_noncurrent", "total_debt", "equity"]);
const UNIT_PREF: Record<string, string[]> = { diluted_shares: ["shares"], eps_diluted: ["USD/shares"] };

// Debt component concept lists (repo config/sources.yaml). Total debt is
// resolved from instant rows of ALL of these — see resolveTotalDebt.
const DEBT_CURRENT_CONCEPTS = ["LongTermDebtCurrent", "DebtCurrent", "LongTermBorrowingsCurrent"];
const DEBT_NONCURRENT_CONCEPTS = ["LongTermDebtNoncurrent", "DebtNoncurrent", "LongTermBorrowingsNoncurrent"];

// Repo peer universe for MU (config/peers.yaml) — used ONLY as the labeled
// percentile universe; the Space search marks percentiles unavailable
// rather than running 8 extra EDGAR pulls per search.
const PEER_UNIVERSE_NOTE =
  "Repo peer set is MU-specific (NVDA, AMD, AVGO, INTC, QCOM, MRVL, WDC, STX from SEC EDGAR). Peer percentile is not computed in the Space search — run the repo engine for the full peer run. SK hynix and Samsung are excluded in v1: not SEC EDGAR XBRL filers.";

/* ------------------------------- fetch utils ------------------------------ */

async function fetchWithTimeout(url: string, ms: number, asJson: boolean): Promise<unknown> {
  const ctrl = new AbortController();
  const t = setTimeout(() => ctrl.abort(), ms);
  try {
    const res = await fetch(url, { headers: { "User-Agent": UA, Accept: "*/*" }, signal: ctrl.signal });
    if (!res.ok) throw new Error(`HTTP ${res.status} for ${url}`);
    return asJson ? await res.json() : await res.text();
  } finally {
    clearTimeout(t);
  }
}
const fetchJson = (url: string, ms = 25000) => fetchWithTimeout(url, ms, true) as Promise<Record<string, unknown>>;
const fetchText = (url: string, ms = 45000) => fetchWithTimeout(url, ms, false) as Promise<string>;

/* --------------------------- EDGAR series (port) -------------------------- */

interface SeriesRow { end: string; value: number; filed?: string; start?: string }

function parseDay(s: string): number {
  const [y = 1970, m = 1, d = 1] = s.split("-").map(Number);
  return Date.UTC(y, m - 1, d);
}

function annualSeries(facts: Record<string, unknown>, candidates: string[], metric: string): SeriesRow[] {
  const gaap = ((facts.facts as Record<string, unknown> | undefined)?.["us-gaap"] ?? {}) as Record<string, unknown>;
  let best: SeriesRow[] = [];
  for (const concept of candidates) {
    const node = gaap[concept] as { units?: Record<string, unknown[]> } | undefined;
    if (!node?.units) continue;
    const pref = UNIT_PREF[metric] ?? ["USD"];
    const unitKey = pref.find((u) => u in node.units!) ?? Object.keys(node.units)[0];
    if (!unitKey) continue;
    const rows = (node.units[unitKey] ?? []) as Record<string, unknown>[];
    const byEnd = new Map<string, Record<string, unknown>>();
    for (const row of rows) {
      if (row.form !== "10-K" || row.fp !== "FY") continue;
      if (BALANCE_METRICS.has(metric)) {
        if (row.start) continue;
      } else {
        if (!row.start) continue;
        const dur = (parseDay(String(row.end)) - parseDay(String(row.start))) / 86400000;
        if (!(dur >= 300 && dur <= 400)) continue;
      }
      const end = String(row.end);
      const prev = byEnd.get(end);
      if (!prev || String(row.filed ?? "") > String(prev.filed ?? "")) byEnd.set(end, row);
    }
    if (byEnd.size) {
      const series = [...byEnd.keys()].sort().map((e) => ({
        end: e,
        value: Number(byEnd.get(e)!.val),
        filed: byEnd.get(e)!.filed as string | undefined,
        start: byEnd.get(e)!.start as string | undefined,
      }));
      const lastEnd = series[series.length - 1]?.end ?? "";
      const bestEnd = best[best.length - 1]?.end ?? "";
      if (!best.length || lastEnd > bestEnd) best = series;
    }
  }
  return best;
}

/* ----------------- balance instants + debt resolution ------------------- */
// Repo parity (edgar.py / metrics.py): balance-sheet "latest" is the
// latest filed instant across 10-K and 10-Q (not just the annual 10-K
// row), and total debt is resolved across every debt concept at the
// latest determinable date — (a) current + noncurrent pair, then
// (b) a single total concept in config priority order. A carrying-amount
// total replaces its components, never adds to them. Fail closed: when
// nothing resolves, debt is null and debt-derived sub-scores go missing.

function unitRowsRaw(facts: Record<string, unknown>, concept: string, metric: string): Record<string, unknown>[] {
  const gaap = ((facts.facts as Record<string, unknown> | undefined)?.["us-gaap"] ?? {}) as Record<string, unknown>;
  const node = gaap[concept] as { units?: Record<string, unknown[]> } | undefined;
  if (!node?.units) return [];
  const pref = UNIT_PREF[metric] ?? ["USD"];
  const unitKey = pref.find((u) => u in node.units!) ?? Object.keys(node.units)[0];
  return unitKey ? ((node.units[unitKey] ?? []) as Record<string, unknown>[]) : [];
}

function instantsByEnd(facts: Record<string, unknown>, concept: string, metric: string): Map<string, number> {
  const byEnd = new Map<string, { value: number; filed: string }>();
  for (const row of unitRowsRaw(facts, concept, metric)) {
    const form = String(row.form ?? "");
    if (!form.startsWith("10-K") && !form.startsWith("10-Q")) continue;
    if (row.start || !row.end) continue;
    const value = Number(row.val);
    if (!Number.isFinite(value)) continue;
    const end = String(row.end);
    const prev = byEnd.get(end);
    if (!prev || String(row.filed ?? "") > prev.filed) byEnd.set(end, { value, filed: String(row.filed ?? "") });
  }
  return new Map([...byEnd.entries()].sort((a, b) => (a[0] < b[0] ? -1 : 1)).map(([e, v]) => [e, v.value]));
}

function latestInstantForMetric(facts: Record<string, unknown>, candidates: string[], metric: string): SeriesRow | null {
  // Chosen concept = freshest annual series (repo rule); fall back to the
  // first concept with any instant rows.
  let chosen: string | null = null;
  let bestEnd = "";
  for (const concept of candidates) {
    const s = annualSeries(facts, [concept], metric);
    const lastEnd = s[s.length - 1]?.end ?? "";
    if (lastEnd > bestEnd) { bestEnd = lastEnd; chosen = concept; }
  }
  if (!chosen) {
    for (const concept of candidates) {
      if (instantsByEnd(facts, concept, metric).size) { chosen = concept; break; }
    }
  }
  if (!chosen) return null;
  const inst = instantsByEnd(facts, chosen, metric);
  const ends = [...inst.keys()].sort();
  const last = ends[ends.length - 1];
  return last ? { end: last, value: inst.get(last)! } : null;
}

export interface DebtInstants {
  totals: Map<string, Map<string, number>>;
  current: Map<string, Map<string, number>>;
  noncurrent: Map<string, Map<string, number>>;
}

function buildDebtInstants(facts: Record<string, unknown>): DebtInstants {
  const build = (concepts: string[]) => {
    const out = new Map<string, Map<string, number>>();
    for (const concept of concepts) {
      const inst = instantsByEnd(facts, concept, "total_debt");
      if (inst.size) out.set(concept, inst);
    }
    return out;
  };
  return {
    totals: build(CONCEPTS.total_debt ?? []),
    current: build(DEBT_CURRENT_CONCEPTS),
    noncurrent: build(DEBT_NONCURRENT_CONCEPTS),
  };
}

export interface DebtResolution {
  value: number | null;
  as_of: string | null;
  method: string;
  components: Record<string, unknown>;
}

function resolveTotalDebt(inst: DebtInstants, fallback: SeriesRow | null): DebtResolution {
  const firstAt = (group: Map<string, Map<string, number>>, end: string): [string, number] | null => {
    for (const [concept, byEnd] of group) {
      const v = byEnd.get(end);
      if (v != null) return [concept, v];
    }
    return null;
  };
  const dates = new Set<string>();
  for (const group of [inst.totals, inst.current, inst.noncurrent]) {
    for (const byEnd of group.values()) for (const e of byEnd.keys()) dates.add(e);
  }
  for (const end of [...dates].sort().reverse()) {
    const cur = firstAt(inst.current, end);
    const non = firstAt(inst.noncurrent, end);
    if (cur && non) {
      return {
        value: cur[1] + non[1], as_of: end, method: "current_plus_noncurrent_pair",
        components: { current_concept: cur[0], current_value: cur[1], noncurrent_concept: non[0], noncurrent_value: non[1] },
      };
    }
    const tot = firstAt(inst.totals, end);
    if (tot) return { value: tot[1], as_of: end, method: `single_total:${tot[0]}`, components: { concept: tot[0] } };
  }
  if (fallback) return { value: fallback.value, as_of: fallback.end, method: "balance_latest_single_concept", components: {} };
  return { value: null, as_of: null, method: "not_determinable", components: {} };
}

/* --------------------- TTM bridge (quarterly XBRL rows) ------------------ */
// Trailing-twelve-months for flow metrics, built the way filers report it:
//   TTM = latest annual FY + year-to-date of the current fiscal year
//         − year-to-date of the same period one year earlier.
// The fiscal Q4 is NOT filed as a standalone quarter in XBRL, so summing the
// last four standalone quarters is wrong; the YTD bridge avoids that trap.
// When no quarter has been filed since year-end, TTM = the annual figure.

interface RawRow { start: string; end: string; value: number; filed?: string }
const durDays = (r: { start: string; end: string }) => (parseDay(r.end) - parseDay(r.start)) / 86400000 + 1;

function flowRows(facts: Record<string, unknown>, candidates: string[], metric: string): RawRow[] {
  const gaap = ((facts.facts as Record<string, unknown> | undefined)?.["us-gaap"] ?? {}) as Record<string, unknown>;
  // Pick the candidate whose annual series reaches furthest — same rule as annualSeries.
  let bestConcept: string | null = null;
  let bestEnd = "";
  for (const concept of candidates) {
    const s = annualSeries(facts, [concept], metric);
    const lastEnd = s[s.length - 1]?.end ?? "";
    if (lastEnd > bestEnd) { bestEnd = lastEnd; bestConcept = concept; }
  }
  if (!bestConcept) return [];
  const node = gaap[bestConcept] as { units?: Record<string, unknown[]> } | undefined;
  if (!node?.units) return [];
  const pref = UNIT_PREF[metric] ?? ["USD"];
  const unitKey = pref.find((u) => u in node.units!) ?? Object.keys(node.units)[0];
  if (!unitKey) return [];
  const byKey = new Map<string, RawRow>();
  for (const row of (node.units[unitKey] ?? []) as Record<string, unknown>[]) {
    const form = String(row.form ?? "");
    if (!form.startsWith("10-K") && !form.startsWith("10-Q")) continue;
    if (!row.start || !row.end) continue;
    const value = Number(row.val);
    if (!Number.isFinite(value)) continue;
    const r: RawRow = { start: String(row.start), end: String(row.end), value, filed: row.filed as string | undefined };
    const key = `${r.start}|${r.end}`;
    const prev = byKey.get(key);
    if (!prev || String(r.filed ?? "") > String(prev.filed ?? "")) byKey.set(key, r);
  }
  return [...byKey.values()].sort((a, b) => (a.end < b.end ? -1 : a.end > b.end ? 1 : a.start < b.start ? -1 : 1));
}

export interface TtmInfo {
  value: number;
  end: string;
  method: string;
  annualValue: number;
  annualEnd: string;
  ytdCurrent: number | null;
  ytdPrior: number | null;
}

function computeTtm(rows: RawRow[], annual: SeriesRow): TtmInfo {
  const base: TtmInfo = {
    value: annual.value, end: annual.end, method: "annual",
    annualValue: annual.value, annualEnd: annual.end, ytdCurrent: null, ytdPrior: null,
  };
  const annualEndMs = parseDay(annual.end);
  const post = rows.filter((r) => {
    const gap = (parseDay(r.end) - annualEndMs) / 86400000;
    const dur = durDays(r);
    return gap > 0 && gap <= 370 && dur >= 20 && dur <= 330;
  });
  if (!post.length) {
    return { ...base, method: "Latest annual = TTM — no quarter filed after the fiscal year-end yet" };
  }
  const latestEnd = post.map((r) => r.end).sort().pop()!;
  // Primary bridge: the filed YTD cumulative row for the current fiscal year
  // (earliest start among rows ending at the latest quarter-end).
  const atLatest = post.filter((r) => r.end === latestEnd).sort((a, b) => parseDay(a.start) - parseDay(b.start));
  const ytdCur = atLatest[0];
  if (ytdCur && Math.abs(parseDay(ytdCur.start) - annualEndMs) <= 20 * 86400000) {
    const dur = durDays(ytdCur);
    const priors = rows
      .filter((r) => {
        const gap = (parseDay(latestEnd) - parseDay(r.end)) / 86400000;
        return gap >= 340 && gap <= 385 && Math.abs(durDays(r) - dur) <= 16;
      })
      .sort((a, b) =>
        Math.abs((parseDay(latestEnd) - parseDay(a.end)) / 86400000 - 364) - Math.abs((parseDay(latestEnd) - parseDay(b.end)) / 86400000 - 364)
        || parseDay(a.start) - parseDay(b.start));
    const ytdPrior = priors[0];
    if (ytdPrior) {
      return {
        value: annual.value + ytdCur.value - ytdPrior.value, end: latestEnd,
        method: "TTM = latest annual + current-FY YTD − prior-year YTD (same period)",
        annualValue: annual.value, annualEnd: annual.end, ytdCurrent: ytdCur.value, ytdPrior: ytdPrior.value,
      };
    }
  }
  // Fallback bridge: sum the standalone quarters filed since year-end
  // (Q1–Q3 of the new fiscal year) and subtract the same quarters a year earlier.
  const isQuarter = (r: RawRow) => durDays(r) >= 55 && durDays(r) <= 120;
  const curQ = post.filter(isQuarter).sort((a, b) => parseDay(a.end) - parseDay(b.end));
  if (curQ.length) {
    let priorSum = 0;
    let ok = true;
    for (const q of curQ) {
      const match = rows
        .filter((r) => {
          const gap = (parseDay(q.end) - parseDay(r.end)) / 86400000;
          return isQuarter(r) && gap >= 340 && gap <= 385;
        })
        .sort((a, b) => Math.abs((parseDay(q.end) - parseDay(a.end)) / 86400000 - 364) - Math.abs((parseDay(q.end) - parseDay(b.end)) / 86400000 - 364))[0];
      if (!match) { ok = false; break; }
      priorSum += match.value;
    }
    if (ok) {
      const curSum = curQ.reduce((a, r) => a + r.value, 0);
      return {
        value: annual.value + curSum - priorSum, end: latestEnd,
        method: "TTM = latest annual + standalone quarters since year-end − same quarters a year earlier",
        annualValue: annual.value, annualEnd: annual.end, ytdCurrent: curSum, ytdPrior: priorSum,
      };
    }
  }
  return { ...base, method: "TTM bridge unavailable (no matching prior-year YTD in filings) — annual figure used" };
}

/* --------------------------- metrics (port) ------------------------------- */

function median(vals: number[]): number | null {
  if (!vals.length) return null;
  const s = [...vals].sort((a, b) => a - b);
  const mid = Math.floor(s.length / 2);
  return s.length % 2 ? (s[mid] ?? null) : ((s[mid - 1] ?? 0) + (s[mid] ?? 0)) / 2;
}
function pstdev(vals: number[]): number | null {
  if (!vals.length) return null;
  const mean = vals.reduce((a, b) => a + b, 0) / vals.length;
  return Math.sqrt(vals.reduce((a, b) => a + (b - mean) ** 2, 0) / vals.length);
}
const byEnd = (s: SeriesRow[]) => new Map(s.map((r) => [r.end, r.value]));
const latestOf = (s: SeriesRow[] | undefined): number | null => (s && s.length ? (s[s.length - 1]?.value ?? null) : null);

export interface Metrics {
  history_years: string[];
  fiscal_year_end: string | null;
  revenue_latest: number | null;
  revenue_growth_latest_pct: number | null;
  revenue_cagr_5y_pct: number | null;
  fcf_latest: number | null;
  gross_margin_latest_pct: number | null;
  gross_margin_median_5y_pct: number | null;
  gross_margin_stdev_5y_pts: number | null;
  operating_margin_median_5y_pct: number | null;
  fcf_margin_median_5y_pct: number | null;
  fcf_margin_latest_pct: number | null;
  cash_latest: number | null;
  investments_latest: number | null;
  total_debt_latest: number | null;
  net_cash_latest: number | null;
  net_cash_to_revenue: number | null;
  capex_latest: number | null;
  capex_intensity_latest_pct: number | null;
  operating_cash_flow_latest: number | null;
  net_income_latest: number | null;
  rd_expense_latest: number | null;
  eps_diluted_latest: number | null;
  diluted_shares_latest: number | null;
  sbc_latest: number | null;
  sbc_ttm: number | null;
  sbc_pct_of_revenue_latest: number | null;
  sbc_not_reported_fcf_unadjusted_not_used: boolean;
  sbc_ttm_unavailable: boolean;
  fcf_raw_latest: number | null;
  fcf_raw_ttm: number | null;
  dilution_share_count_growth_pct: number | null;
  dilution_span_years: number | null;
  dilution_annual_points: number | null;
  dilution_share_series_discontinuity: boolean;
  revenue_ttm: number | null;
  eps_ttm: number | null;
  fcf_ttm: number | null;
  operating_income_ttm: number | null;
  interest_expense_ttm: number | null;
  interest_coverage_ttm: number | null;
  interest_coverage_missing_reason: string | null;
  leverage_debt_to_revenue_ttm: number | null;
  total_debt_resolution: DebtResolution | null;
  debt_not_determinable_fail_closed: boolean;
  ttm_end: string | null;
  ttm_method: string | null;
  diluted_shares_current: number | null;
  roic_median_5y_pct: number | null;
  market_cap: number | null;
  price_to_sales: number | null;
  ev_to_sales: number | null;
  trailing_pe: number | null;
  fcf_yield_latest_pct: number | null;
  discount_from_52w_high_pct: number | null;
}

function computeMetrics(
  series: Record<string, SeriesRow[]>,
  taxRatePct: number,
  ttmMap: Record<string, TtmInfo> = {},
  balance?: { latest: Record<string, SeriesRow | null>; debtInstants: DebtInstants },
): Metrics {
  const rev = series.revenue ?? [];
  const m: Metrics = {
    history_years: rev.map((r) => r.end),
    fiscal_year_end: rev.length ? (rev[rev.length - 1]?.end ?? null) : null,
    revenue_latest: latestOf(rev),
    revenue_growth_latest_pct: null, revenue_cagr_5y_pct: null, fcf_latest: null,
    gross_margin_latest_pct: null, gross_margin_median_5y_pct: null, gross_margin_stdev_5y_pts: null,
    operating_margin_median_5y_pct: null, fcf_margin_median_5y_pct: null, fcf_margin_latest_pct: null,
    cash_latest: null, investments_latest: null, total_debt_latest: null, net_cash_latest: null,
    net_cash_to_revenue: null, capex_latest: null, capex_intensity_latest_pct: null,
    operating_cash_flow_latest: null, net_income_latest: null, rd_expense_latest: null,
    eps_diluted_latest: null, diluted_shares_latest: null, roic_median_5y_pct: null,
    sbc_latest: null, sbc_ttm: null, sbc_pct_of_revenue_latest: null,
    sbc_not_reported_fcf_unadjusted_not_used: false, sbc_ttm_unavailable: false,
    fcf_raw_latest: null, fcf_raw_ttm: null,
    dilution_share_count_growth_pct: null, dilution_span_years: null,
    dilution_annual_points: null, dilution_share_series_discontinuity: false,
    revenue_ttm: null, eps_ttm: null, fcf_ttm: null, ttm_end: null, ttm_method: null,
    operating_income_ttm: null, interest_expense_ttm: null, interest_coverage_ttm: null,
    interest_coverage_missing_reason: null, leverage_debt_to_revenue_ttm: null,
    total_debt_resolution: null, debt_not_determinable_fail_closed: false,
    diluted_shares_current: null,
    market_cap: null, price_to_sales: null, ev_to_sales: null, trailing_pe: null,
    fcf_yield_latest_pct: null, discount_from_52w_high_pct: null,
  };
  if (!rev.length || m.revenue_latest == null) return m;
  const latestRev = m.revenue_latest;
  if (rev.length >= 2) {
    const prev = rev[rev.length - 2]?.value ?? 0;
    m.revenue_growth_latest_pct = prev ? (latestRev / prev - 1) * 100 : null;
  }
  const n = Math.min(5, rev.length - 1);
  if (n >= 2) {
    const base = rev[rev.length - 1 - n]?.value ?? 0;
    m.revenue_cagr_5y_pct = base ? ((latestRev / base) ** (1 / n) - 1) * 100 : null;
  }
  // Gross profit: direct, else revenue - cost of revenue (repo fallback).
  let gpSeries = series.gross_profit ?? [];
  if (!gpSeries.length && series.cost_of_revenue?.length) {
    const cost = byEnd(series.cost_of_revenue);
    gpSeries = rev.filter((r) => cost.has(r.end)).map((r) => ({ end: r.end, value: r.value - (cost.get(r.end) ?? 0) }));
  }
  const ratio = (num: SeriesRow[], den: SeriesRow[]) => {
    const d = byEnd(den);
    return num.filter((r) => d.has(r.end) && d.get(r.end)).map((r) => ({ end: r.end, value: (r.value / (d.get(r.end) ?? 1)) * 100 }));
  };
  const gm = ratio(gpSeries, rev);
  const om = ratio(series.operating_income ?? [], rev);
  const ocf = byEnd(series.operating_cash_flow ?? []);
  const capexMap = byEnd(series.capex ?? []);
  const sbcMap = byEnd(series.share_based_compensation ?? []);
  const revMap = byEnd(rev);
  const fcfRawByEnd = new Map<string, number>();
  for (const [e, v] of ocf) if (capexMap.has(e)) fcfRawByEnd.set(e, v - (capexMap.get(e) ?? 0));
  // Owner FCF (v3): subtract stock-based compensation year by year.
  // Adjusted FCF exists ONLY for years where SBC is reported; if no
  // year reports SBC the FCF margin sub-score goes missing (fail
  // closed) instead of falling back to the unadjusted figure.
  const fcfByEnd = new Map<string, number>();
  for (const e of [...fcfRawByEnd.keys()].sort()) {
    const sbc = sbcMap.get(e);
    if (sbc != null) fcfByEnd.set(e, (fcfRawByEnd.get(e) ?? 0) - sbc);
  }
  m.sbc_latest = sbcMap.size ? (sbcMap.get([...sbcMap.keys()].sort().pop()!) ?? null) : null;
  m.sbc_not_reported_fcf_unadjusted_not_used = sbcMap.size === 0;
  if (fcfRawByEnd.size) {
    const lastRaw = [...fcfRawByEnd.keys()].sort().pop()!;
    m.fcf_raw_latest = fcfRawByEnd.get(lastRaw) ?? null;
  }
  const fcfm = [...fcfByEnd.keys()].sort().filter((e) => revMap.get(e)).map((e) => ({ end: e, value: ((fcfByEnd.get(e) ?? 0) / (revMap.get(e) ?? 1)) * 100 }));
  if (fcfByEnd.size) {
    const lastKey = [...fcfByEnd.keys()].sort().pop()!;
    m.fcf_latest = fcfByEnd.get(lastKey) ?? null;
    const sbcAtEnd = sbcMap.get(lastKey);
    const revAtEnd = revMap.get(lastKey);
    m.sbc_pct_of_revenue_latest = sbcAtEnd != null && revAtEnd ? (sbcAtEnd / revAtEnd) * 100 : null;
  }
  const gmVals = gm.map((r) => r.value);
  m.gross_margin_latest_pct = gmVals.length ? (gmVals[gmVals.length - 1] ?? null) : null;
  m.gross_margin_median_5y_pct = median(gmVals.slice(-5));
  m.gross_margin_stdev_5y_pts = gmVals.length >= 3 ? pstdev(gmVals.slice(-5)) : null;
  m.operating_margin_median_5y_pct = median(om.map((r) => r.value).slice(-5));
  m.fcf_margin_median_5y_pct = median(fcfm.map((r) => r.value).slice(-5));
  m.fcf_margin_latest_pct = fcfm.length ? (fcfm[fcfm.length - 1]?.value ?? null) : null;

  // Balance sheet at the latest filed instant (10-K or 10-Q), repo rule.
  const bal = balance?.latest ?? {};
  const cashRow = bal.cash ?? null;
  const cash = cashRow?.value ?? latestOf(series.cash) ?? 0;
  const invCur = bal.investments_current?.value ?? latestOf(series.investments_current);
  const invNon = bal.investments_noncurrent?.value ?? latestOf(series.investments_noncurrent);
  const inv = (invCur ?? 0) + (invNon ?? 0);
  // Total debt resolved across all debt concepts; FAIL CLOSED when it
  // cannot be determined — net cash and leverage stay null (sub-scores
  // go missing) instead of treating missing debt as zero debt.
  const annualDebt = series.total_debt?.length ? series.total_debt[series.total_debt.length - 1]! : null;
  const debtRes = balance
    ? resolveTotalDebt(balance.debtInstants, bal.total_debt ?? annualDebt)
    : { value: latestOf(series.total_debt), as_of: annualDebt?.end ?? null, method: "annual_series_fallback", components: {} } as DebtResolution;
  const debt = debtRes.value;
  m.cash_latest = cashRow?.value ?? latestOf(series.cash);
  m.investments_latest = invCur != null || invNon != null ? inv : null;
  m.total_debt_latest = debt;
  m.total_debt_resolution = debtRes;
  m.debt_not_determinable_fail_closed = debt == null;
  // Snapshot ratios use TTM revenue when the bridge produced it, else
  // the latest annual (repo rule).
  const revSnapshot = ttmMap.revenue?.value ?? latestRev;
  if (debt == null) {
    m.net_cash_latest = null;
    m.net_cash_to_revenue = null;
    m.leverage_debt_to_revenue_ttm = null;
  } else {
    m.net_cash_latest = cash + inv - debt;
    m.net_cash_to_revenue = revSnapshot ? m.net_cash_latest / revSnapshot : null;
    m.leverage_debt_to_revenue_ttm = revSnapshot ? debt / revSnapshot : null;
  }
  // Interest coverage on the same TTM bridge; missing when interest
  // expense is absent or non-positive (no free perfect score).
  m.operating_income_ttm = ttmMap.operating_income?.value ?? null;
  m.interest_expense_ttm = ttmMap.interest_expense?.value ?? null;
  if (m.operating_income_ttm != null && m.interest_expense_ttm != null && m.interest_expense_ttm > 0) {
    m.interest_coverage_ttm = m.operating_income_ttm / m.interest_expense_ttm;
    m.interest_coverage_missing_reason = null;
  } else {
    m.interest_coverage_ttm = null;
    m.interest_coverage_missing_reason = m.operating_income_ttm == null
      ? "operating_income_ttm_missing"
      : "interest_expense_ttm_missing_or_nonpositive";
  }
  m.capex_latest = latestOf(series.capex);
  m.capex_intensity_latest_pct = m.capex_latest != null && latestRev ? (m.capex_latest / latestRev) * 100 : null;
  m.operating_cash_flow_latest = latestOf(series.operating_cash_flow);
  m.net_income_latest = latestOf(series.net_income);
  m.rd_expense_latest = latestOf(series.rd_expense);
  m.eps_diluted_latest = latestOf(series.eps_diluted);
  m.diluted_shares_latest = latestOf(series.diluted_shares);

  // Dilution (v3): CAGR of the annual diluted share count over the
  // available span, up to 5 years back from the latest annual point.
  // >= 2 points required; a >5x or <0.2x single-year jump in the window
  // is a split/units break — fail closed (missing + flagged).
  const shareRowsDil = (series.diluted_shares ?? []).filter((r) => r.value);
  if (shareRowsDil.length >= 2) {
    const latestRow = shareRowsDil[shareRowsDil.length - 1]!;
    const cutoff = parseDay(latestRow.end) - Math.floor(5 * 365.25) * 86400000;
    const windowRows = shareRowsDil.filter((r) => parseDay(r.end) >= cutoff);
    const disc = windowRows.some((r, i) => {
      if (i === 0) return false;
      const prev = windowRows[i - 1]!;
      if (!(prev.value > 0)) return false;
      const ratio = r.value / prev.value;
      return ratio > 5 || ratio < 0.2;
    });
    m.dilution_share_series_discontinuity = disc;
    const earliestRow = windowRows[0]!;
    const spanDays = (parseDay(latestRow.end) - parseDay(earliestRow.end)) / 86400000;
    if (!disc && spanDays > 0 && earliestRow.value > 0) {
      const spanYears = spanDays / 365.25;
      m.dilution_span_years = Math.round(spanYears * 100) / 100;
      m.dilution_share_count_growth_pct = ((latestRow.value / earliestRow.value) ** (1 / spanYears) - 1) * 100;
      m.dilution_annual_points = windowRows.length;
    }
  }

  // Through-cycle ROIC (repo): after-tax operating income / invested capital.
  const eq = byEnd(series.equity ?? []), db = byEnd(series.total_debt ?? []), ca = byEnd(series.cash ?? []);
  const ic = byEnd(series.investments_current ?? []), inn = byEnd(series.investments_noncurrent ?? []);
  const oi = byEnd(series.operating_income ?? []);
  const roics: number[] = [];
  for (const e of [...oi.keys()].sort()) {
    if (eq.has(e) && db.has(e) && ca.has(e)) {
      const capital = (eq.get(e) ?? 0) + (db.get(e) ?? 0) - (ca.get(e) ?? 0) - (ic.get(e) ?? 0) - (inn.get(e) ?? 0);
      if (capital > 0) roics.push(((oi.get(e) ?? 0) * (1 - taxRatePct / 100)) / capital * 100);
    }
  }
  m.roic_median_5y_pct = median(roics.slice(-5));

  // TTM figures (bridge over the latest annual) — used only by the trailing
  // multiples in evaluateTicker. Medians and the DCF keep annual history.
  const revTtm = ttmMap.revenue;
  m.revenue_ttm = revTtm ? revTtm.value : m.revenue_latest;
  m.ttm_end = revTtm ? revTtm.end : m.fiscal_year_end;
  m.ttm_method = revTtm ? revTtm.method : null;
  m.eps_ttm = ttmMap.eps_diluted ? ttmMap.eps_diluted.value : m.eps_diluted_latest;
  const ocfTtm = ttmMap.operating_cash_flow;
  const capexTtm = ttmMap.capex;
  const sbcTtm = ttmMap.share_based_compensation;
  m.sbc_ttm = sbcTtm ? sbcTtm.value : null;
  m.sbc_ttm_unavailable = (series.share_based_compensation?.length ?? 0) > 0 && !sbcTtm;
  m.fcf_raw_ttm = ocfTtm && capexTtm ? ocfTtm.value - capexTtm.value : null;
  // TTM FCF is SBC-adjusted owner FCF (v3); null when any bridge leg is
  // missing — the FCF-yield consumer falls back to the latest annual
  // adjusted FCF, exactly as the repo's add_price_metrics does.
  m.fcf_ttm = ocfTtm && capexTtm && sbcTtm ? ocfTtm.value - capexTtm.value - sbcTtm.value : null;
  return m;
}

/* ------------------------------ prices (port) ----------------------------- */

export interface PriceData { source: string; last_close: number; last_date: string; high_52w: number; low_52w: number }

async function getPrice(symbol: string): Promise<PriceData> {
  const errors: string[] = [];
  // Yahoo chart API (repo's first feed)
  try {
    const url = `https://query1.finance.yahoo.com/v8/finance/chart/${encodeURIComponent(symbol)}?range=1y&interval=1d`;
    const data = (await fetchJson(url, 20000)) as unknown as { chart?: { result?: unknown[] } };
    const result = (data.chart?.result?.[0] ?? null) as {
      timestamp?: number[];
      indicators?: { quote?: { close?: (number | null)[] }[] };
    } | null;
    const stamps = result?.timestamp ?? [];
    const closes = result?.indicators?.quote?.[0]?.close ?? [];
    const rows: { date: string; close: number }[] = [];
    stamps.forEach((ts, i) => {
      const c = closes[i];
      if (c != null) rows.push({ date: new Date(ts * 1000).toISOString().slice(0, 10), close: c });
    });
    if (rows.length) {
      const cs = rows.map((r) => r.close);
      const last = rows[rows.length - 1]!;
      return { source: "yahoo_chart", last_close: last.close, last_date: last.date, high_52w: Math.max(...cs), low_52w: Math.min(...cs) };
    }
    errors.push("yahoo_chart: empty/unusable response");
  } catch (e) {
    errors.push(`yahoo_chart: ${e instanceof Error ? e.message : String(e)}`);
  }
  // Stooq daily CSV (repo's fallback)
  try {
    const url = `https://stooq.com/q/d/l/?s=${symbol.toLowerCase()}.us&i=d`;
    const text = await fetchText(url, 20000);
    const lines = text.trim().split("\n");
    if (lines.length > 1 && lines[0]?.includes("Date")) {
      const rows: { date: string; close: number }[] = [];
      for (const line of lines.slice(1)) {
        const parts = line.split(",");
        const close = Number(parts[4]);
        if (parts[0] && Number.isFinite(close)) rows.push({ date: parts[0], close });
      }
      // Stooq daily file is long-history; the repo takes the last row as the
      // close and the file max/min as the range (v1 behaviour on a 1y file).
      const tail = rows.slice(-252);
      if (tail.length) {
        const cs = tail.map((r) => r.close);
        const last = tail[tail.length - 1]!;
        return { source: "stooq_daily_csv", last_close: last.close, last_date: last.date, high_52w: Math.max(...cs), low_52w: Math.min(...cs) };
      }
    }
    errors.push("stooq_daily_csv: empty/unusable response");
  } catch (e) {
    errors.push(`stooq_daily_csv: ${e instanceof Error ? e.message : String(e)}`);
  }
  throw new Error(`No free price feed returned data for ${symbol}. Tried: ${errors.join("; ")}`);
}

/* --------------------------- beta (free attempt) -------------------------- */

async function getBeta(symbol: string): Promise<{ beta: number; source: string } | null> {
  const cfgBeta = CONFIG_BETAS[symbol];
  if (cfgBeta) return cfgBeta;
  try {
    const url = `https://query1.finance.yahoo.com/v10/finance/quoteSummary/${encodeURIComponent(symbol)}?modules=defaultKeyStatistics`;
    const data = (await fetchJson(url, 15000)) as unknown as {
      quoteSummary?: { result?: { defaultKeyStatistics?: { beta?: { raw?: number } } }[] };
    };
    const raw = data.quoteSummary?.result?.[0]?.defaultKeyStatistics?.beta?.raw;
    if (typeof raw === "number" && Number.isFinite(raw)) {
      return { beta: raw, source: "Yahoo Finance key statistics (free feed) at search time" };
    }
  } catch { /* fall through — beta stays missing, never invented */ }
  return null;
}

/* --------------------- concentration (10-K text, port) -------------------- */

export interface Concentration {
  single_customer: { quote: string; threshold_pct: number; no_customer_at_or_above_threshold: boolean; max_single_customer_pct: number | null } | null;
  top_ten: { quote: string; top_ten_share_pct: number | null; basis: string } | null;
  data_center: { quote: string; data_center_share_pct: number } | null;
  filing: { filed: string; accession: string; primary_document: string; url: string } | null;
}

function stripHtml(raw: string): string {
  return raw.replace(/<[^>]+>/g, " ").replace(/&amp;/g, "&").replace(/&lt;/g, "<").replace(/&gt;/g, ">").replace(/&nbsp;/g, " ").replace(/\s+/g, " ").trim();
}

function extractConcentration(text: string, latestFyYear: number | null): Omit<Concentration, "filing"> {
  const sentences = text.split(/(?<=[.!?])\s+/).map((s) => s.trim()).filter(Boolean);
  const out: Omit<Concentration, "filing"> = { single_customer: null, top_ten: null, data_center: null };
  for (const s of sentences) {
    if (!s.toLowerCase().includes("customer accounted for")) continue;
    const mm = s.match(/(\d+(?:\.\d+)?)\s*%/);
    if (!mm) continue;
    const noCust = s.toLowerCase().startsWith("no customer");
    out.single_customer = {
      quote: s,
      threshold_pct: Number(mm[1]),
      no_customer_at_or_above_threshold: noCust,
      max_single_customer_pct: noCust ? null : Number(mm[1]),
    };
    break;
  }
  const topHits = sentences.filter((s) => s.toLowerCase().includes("top ten customers"));
  let chosen: string | undefined;
  if (latestFyYear != null) chosen = topHits.find((s) => s.includes(String(latestFyYear)));
  chosen = chosen ?? topHits[0];
  if (chosen) {
    const mm = chosen.match(/(\d+(?:\.\d+)?)\s*%/);
    if (mm) {
      out.top_ten = { quote: chosen, top_ten_share_pct: Number(mm[1]), basis: "explicit_percent_in_filing" };
    } else if (/over half/i.test(chosen)) {
      out.top_ten = { quote: chosen, top_ten_share_pct: CONC_RULES.top10_approx_value_when_over_half_pct, basis: "config_approx_for_wording_over_half_lower_bound_50" };
    } else if (/(approximately|about) (one-half|half)/i.test(chosen)) {
      out.top_ten = { quote: chosen, top_ten_share_pct: CONC_RULES.top10_approx_value_when_one_half_pct, basis: "config_approx_for_wording_one_half" };
    } else {
      out.top_ten = { quote: chosen, top_ten_share_pct: null, basis: "unparsed_wording" };
    }
  }
  for (const s of sentences) {
    const low = s.toLowerCase();
    if (low.includes("data center") && low.includes("revenue") && low.includes("%") && low.includes("end market")) {
      const mm = s.match(/(\d+(?:\.\d+)?)\s*%/);
      if (mm) { out.data_center = { quote: s, data_center_share_pct: Number(mm[1]) }; break; }
    }
  }
  return out;
}

async function getConcentration(cikPadded: string, latestFyYear: number | null): Promise<Concentration> {
  const empty: Concentration = { single_customer: null, top_ten: null, data_center: null, filing: null };
  try {
    const sub = await fetchJson(`https://data.sec.gov/submissions/CIK${cikPadded}.json`, 20000);
    const recent = ((sub.filings as Record<string, unknown>)?.recent ?? {}) as Record<string, unknown[]>;
    const forms = (recent.form ?? []) as string[];
    let idx = -1;
    for (let i = 0; i < forms.length; i++) if (forms[i] === "10-K") { idx = i; break; }
    if (idx < 0) return empty;
    const filed = String((recent.filingDate as string[])[idx] ?? "");
    const accession = String((recent.accessionNumber as string[])[idx] ?? "");
    const doc = String((recent.primaryDocument as string[])[idx] ?? "");
    const url = `https://www.sec.gov/Archives/edgar/data/${Number(cikPadded)}/${accession.replace(/-/g, "")}/${doc}`;
    const raw = await fetchText(url, 60000);
    if (raw.slice(0, 2000).includes("Undeclared Automated Tool")) return empty;
    const text = stripHtml(raw);
    return { ...extractConcentration(text, latestFyYear), filing: { filed, accession, primary_document: doc, url } };
  } catch {
    return empty; // concentration is evidence, not a hard dependency — score marks it missing
  }
}

function concentrationScore(conc: Concentration): number | null {
  if (!conc.single_customer) return null;
  let score: number;
  if (conc.single_customer.no_customer_at_or_above_threshold) {
    score = CONC_RULES.base_if_no_single_customer_at_threshold;
  } else {
    score = CONC_RULES.base_if_single_customer_present;
    const pct = conc.single_customer.max_single_customer_pct ?? 0;
    if (pct > 20) score -= (pct - 20) * CONC_RULES.single_customer_extra_penalty_per_pct_over_20;
  }
  const topPct = conc.top_ten?.top_ten_share_pct;
  if (topPct != null && topPct > CONC_RULES.top10_free_allowance_pct) {
    score -= (topPct - CONC_RULES.top10_free_allowance_pct) * CONC_RULES.top10_penalty_per_pct_over_allowance;
  }
  return Math.max(CONC_RULES.score_floor, Math.min(CONC_RULES.score_ceiling, Math.round(score * 1000) / 1000));
}

/* ------------------------------ scoring (port) ---------------------------- */

function interpolate(value: number, anchors: Anchors): number {
  const pts = [...anchors].sort((a, b) => a[0] - b[0]);
  const first = pts[0]!, last = pts[pts.length - 1]!;
  if (value <= first[0]) return first[1];
  if (value >= last[0]) return last[1];
  for (let i = 0; i < pts.length - 1; i++) {
    const [x0, y0] = pts[i]!, [x1, y1] = pts[i + 1]!;
    if (x0 <= value && value <= x1) return x1 === x0 ? y1 : y0 + ((y1 - y0) * (value - x0)) / (x1 - x0);
  }
  return last[1];
}

export interface SubScore { value: number; score: number; weight: number }
export interface ScoredSection { subscores: Record<string, SubScore>; total: number | null; missing: string[]; renormalisedOver: number }

function scoreSection(cfg: Record<string, SubCfg>, values: Record<string, number | null>): ScoredSection {
  const subs: Record<string, SubScore> = {};
  const missing: string[] = [];
  let weightSum = 0;
  for (const [metric, spec] of Object.entries(cfg)) {
    const v = values[metric];
    if (v == null || !Number.isFinite(v)) { missing.push(metric); continue; }
    subs[metric] = { value: Math.round(v * 10000) / 10000, score: Math.round(interpolate(v, spec.anchors) * 1000) / 1000, weight: spec.weight };
    weightSum += spec.weight;
  }
  const total = weightSum ? Math.round((Object.values(subs).reduce((a, s) => a + s.score * s.weight, 0) / weightSum) * 100) / 100 : null;
  return { subscores: subs, total, missing, renormalisedOver: Math.round(weightSum * 10000) / 10000 };
}

/* -------------------------------- DCF (port) ------------------------------ */

function buildWacc(beta: number, marketCap: number | null, totalDebt: number | null) {
  const costEquity = DCF_CFG.risk_free_rate_pct + beta * DCF_CFG.equity_risk_premium_pct;
  const costDebtAfterTax = DCF_CFG.pre_tax_cost_of_debt_pct * (1 - DCF_CFG.tax_rate_pct / 100);
  const ev = (marketCap ?? 0) + (totalDebt ?? 0);
  const equityW = ev ? (marketCap ?? 0) / ev : 1;
  const wacc = costEquity * equityW + costDebtAfterTax * (1 - equityW);
  return {
    cost_of_equity_pct: Math.round(costEquity * 1000) / 1000,
    cost_of_debt_after_tax_pct: Math.round(costDebtAfterTax * 1000) / 1000,
    equity_weight: Math.round(equityW * 10000) / 10000,
    debt_weight: Math.round((1 - equityW) * 10000) / 10000,
    wacc_pct: Math.round(wacc * 1000) / 1000,
  };
}

function growthPath(filedCagrPct: number | null): number[] {
  const initial = Math.max(DCF_CFG.growth_floor_pct, Math.min(DCF_CFG.growth_cap_pct, filedCagrPct ?? 0));
  const years = DCF_CFG.projection_years;
  const term = DCF_CFG.terminal_growth_pct;
  if (years === 1) return [initial];
  const step = (initial - term) / years;
  return Array.from({ length: years }, (_, i) => Math.round((initial - step * i) * 10000) / 10000);
}

function fairValue(fcfBase: number, path: number[], waccPct: number, termPct: number) {
  const wacc = waccPct / 100, term = termPct / 100;
  let fcf = fcfBase, pv = 0;
  const projected: number[] = [];
  path.forEach((g, i) => {
    fcf = fcf * (1 + g / 100);
    projected.push(Math.round(fcf * 10000) / 10000);
    pv += fcf / (1 + wacc) ** (i + 1);
  });
  const tv = wacc > term ? (fcf * (1 + term)) / (wacc - term) : 0;
  const pvTv = tv / (1 + wacc) ** path.length;
  return {
    fair_value_per_share: Math.round((pv + pvTv) * 100) / 100,
    pv_of_projected_fcf: Math.round(pv * 100) / 100,
    pv_of_terminal_value: Math.round(pvTv * 100) / 100,
    projected_fcf_per_share: projected,
  };
}

/* ---------------------------- explanations (port) ------------------------- */

function explain(section: string, scored: ScoredSection, extra = ""): string {
  const entries = Object.entries(scored.subscores);
  if (scored.total == null || !entries.length) return `${section} score unavailable: required filed metrics were missing.`;
  const sorted = [...entries].sort((a, b) => b[1].score - a[1].score);
  const fmtE = ([mName, e]: [string, SubScore]) => `${mName.replace(/_/g, " ")} scored ${e.score}/10 (value ${e.value})`;
  const top = sorted.slice(0, 2);
  const bottom = sorted.length > 2 ? sorted.slice(-2).reverse() : [];
  let s = `${section} score is ${scored.total}/10, driven mainly by ${top.map(fmtE).join(" and ")}.`;
  if (bottom.length) s += ` The main drags were ${bottom.map(fmtE).join(" and ")}.`;
  return (s + extra).trim();
}

/* --------------------------------- evaluate ------------------------------- */

export interface EngineResult {
  symbol: string;
  entityName: string | null;
  cik: string;
  snapshotDate: string;
  price: PriceData & { url: string };
  beta: { value: number; source: string } | null;
  kpis: Metrics;
  scores: { business: ScoredSection; value: ScoredSection; moat: ScoredSection };
  dcf: {
    fcf_per_share_base: number | null;
    growth_path_pct: number[];
    wacc: ReturnType<typeof buildWacc> | null;
    wacc_inputs: { risk_free_rate_pct: number; equity_risk_premium_pct: number; beta: number | null; pre_tax_cost_of_debt_pct: number; tax_rate_pct: number };
    fair_value_per_share: number | null;
    pv_of_projected_fcf: number | null;
    pv_of_terminal_value: number | null;
    projected_fcf_per_share: number[];
    current_price: number;
    upside_pct: number | null;
    distance_dollars: number | null;
  };
  concentration: Concentration;
  concentrationScore: number | null;
  moatChecks: { check: string; status: string; evidence: string }[];
  explanations: { business: string; value: string; moat: string };
  dataQuality: string[];
  sources: { filingUrl: string | null; priceFeed: string; scoringConfig: string; dcfConfig: string; peerNote: string };
}

let tickerMapCache: { map: Map<string, { cik: string; name: string }>; at: number } | null = null;

async function resolveTicker(symbol: string): Promise<{ cik: string; name: string } | null> {
  if (!tickerMapCache || Date.now() - tickerMapCache.at > 6 * 3600 * 1000) {
    const data = (await fetchJson("https://www.sec.gov/files/company_tickers.json", 20000)) as Record<string, { cik_str: number; ticker: string; title: string }>;
    const map = new Map<string, { cik: string; name: string }>();
    for (const row of Object.values(data)) {
      map.set(row.ticker.toUpperCase(), { cik: String(row.cik_str).padStart(10, "0"), name: row.title });
    }
    tickerMapCache = { map, at: Date.now() };
  }
  return tickerMapCache.map.get(symbol) ?? null;
}

export async function evaluateTicker(rawSymbol: string): Promise<EngineResult> {
  const symbol = rawSymbol.trim().toUpperCase();
  if (!/^[A-Z][A-Z0-9.\-]{0,9}$/.test(symbol)) throw new Error(`"${rawSymbol}" is not a valid ticker symbol.`);
  const resolved = await resolveTicker(symbol);
  if (!resolved) throw new Error(`Ticker ${symbol} was not found in SEC EDGAR's company tickers list — the engine covers US-listed SEC filers only.`);

  const facts = await fetchJson(`https://data.sec.gov/api/xbrl/companyfacts/CIK${resolved.cik}.json`, 40000);
  const series: Record<string, SeriesRow[]> = {};
  for (const [metric, candidates] of Object.entries(CONCEPTS)) series[metric] = annualSeries(facts, candidates, metric);
  const entityName = (facts.entityName as string | undefined) ?? resolved.name;
  // TTM bridge for the flow metrics that feed trailing multiples and the
  // interest-coverage sub-score.
  const ttmMap: Record<string, TtmInfo> = {};
  for (const metric of ["revenue", "eps_diluted", "operating_cash_flow", "capex", "operating_income", "interest_expense", "share_based_compensation"]) {
    const candidates = CONCEPTS[metric];
    const annual = series[metric] ?? [];
    const lastAnnual = annual[annual.length - 1];
    if (candidates && lastAnnual) {
      const rows = flowRows(facts, candidates, metric);
      if (rows.length) ttmMap[metric] = computeTtm(rows, lastAnnual);
    }
  }
  // Balance-sheet latest instants (10-K/10-Q) + debt instants for the
  // total-debt resolution — repo rules, see resolveTotalDebt.
  const balanceLatest: Record<string, SeriesRow | null> = {};
  for (const metric of BALANCE_METRICS) {
    balanceLatest[metric] = latestInstantForMetric(facts, CONCEPTS[metric] ?? [], metric);
  }
  const debtInstants = buildDebtInstants(facts);
  const m = computeMetrics(series, DCF_CFG.tax_rate_pct, ttmMap, { latest: balanceLatest, debtInstants });
  // Current share count for market cap — the repo's rule
  // (metrics._latest_period_value): at the most recent period end, the
  // SHORTEST-duration row (the standalone quarter, not the YTD
  // cumulative), tie-broken by latest filed. Falls back to the annual
  // count when no period rows exist.
  const shareRows = flowRows(facts, CONCEPTS.diluted_shares ?? [], "diluted_shares");
  if (shareRows.length) {
    const maxEnd = shareRows.map((r) => r.end).sort().pop()!;
    const atMax = shareRows
      .filter((r) => r.end === maxEnd)
      .sort((a, b) => durDays(a) - durDays(b) || String(b.filed ?? "").localeCompare(String(a.filed ?? "")));
    m.diluted_shares_current = atMax[0]?.value ?? null;
  }
  if (m.revenue_latest == null) throw new Error(`EDGAR returned no annual revenue series for ${symbol} — the engine cannot score it from filings.`);

  const price = await getPrice(symbol); // fail closed — throws with both feed errors

  // Price-derived metrics — trailing multiples use TTM denominators:
  // P/S and EV/S divide by TTM revenue (not the stale annual figure),
  // trailing P/E divides by TTM EPS, and FCF yield uses TTM FCF.
  const sharesForCap = m.diluted_shares_current ?? m.diluted_shares_latest;
  const revTtm = m.revenue_ttm ?? m.revenue_latest;
  if (sharesForCap && revTtm) {
    m.market_cap = price.last_close * sharesForCap;
    m.price_to_sales = m.market_cap / revTtm;
    m.ev_to_sales = (m.market_cap - (m.net_cash_latest ?? 0)) / revTtm;
    // Guard the repo's blind spot: a non-positive filed EPS makes P/E
    // meaningless, so it is marked missing (renormalised), not scored.
    m.trailing_pe = m.eps_ttm != null && m.eps_ttm > 0 ? price.last_close / m.eps_ttm : null;
    const fcfForYield = m.fcf_ttm ?? m.fcf_latest;
    m.fcf_yield_latest_pct = fcfForYield != null ? (fcfForYield / m.market_cap) * 100 : null;
    m.discount_from_52w_high_pct = price.high_52w ? (price.last_close / price.high_52w - 1) * 100 : null;
  }

  const fyYear = m.fiscal_year_end ? Number(m.fiscal_year_end.slice(0, 4)) : null;
  const [conc, betaInfo] = await Promise.all([getConcentration(resolved.cik, fyYear), getBeta(symbol)]);
  const concScore = concentrationScore(conc);

  // DCF — only when beta and a positive FCF/share base both exist.
  const dataQuality: string[] = [];
  const fmtUsd = (v: number) => {
    const a = Math.abs(v);
    if (a >= 1e12) return `$${(v / 1e12).toFixed(2)}T`;
    if (a >= 1e9) return `$${(v / 1e9).toFixed(2)}B`;
    if (a >= 1e6) return `$${(v / 1e6).toFixed(0)}M`;
    return `$${Math.round(v).toLocaleString("en-US")}`;
  };
  const revTtmInfo = ttmMap.revenue;
  if (revTtmInfo && revTtmInfo.ytdCurrent != null && revTtmInfo.ytdPrior != null) {
    dataQuality.push(
      `TTM revenue ${fmtUsd(revTtmInfo.value)} through ${revTtmInfo.end} — ${revTtmInfo.method}: annual ${fmtUsd(revTtmInfo.annualValue)} (FY ended ${revTtmInfo.annualEnd}) + current-FY YTD ${fmtUsd(revTtmInfo.ytdCurrent)} − prior-year YTD ${fmtUsd(revTtmInfo.ytdPrior)}. Trailing P/S and EV/S use this TTM revenue; trailing P/E uses TTM EPS and FCF yield uses TTM FCF, bridged the same way. Dividing by annual revenue alone overstates these multiples when the fiscal year ended months before the latest quarter. The 5-year medians and the DCF still use annual filings by design.`,
    );
  } else if (revTtmInfo) {
    dataQuality.push(
      `TTM revenue = latest annual revenue ${fmtUsd(revTtmInfo.value)} (FY ended ${revTtmInfo.annualEnd}) — ${revTtmInfo.method}. Trailing P/S, EV/S, P/E and FCF yield use TTM figures.`,
    );
  }
  let wacc: ReturnType<typeof buildWacc> | null = null;
  let fv: ReturnType<typeof fairValue> | null = null;
  const fcfBase = m.fcf_latest != null && m.diluted_shares_latest ? m.fcf_latest / m.diluted_shares_latest : null;
  const path = growthPath(m.revenue_cagr_5y_pct);
  if (!betaInfo) dataQuality.push("Beta unavailable from config or the free Yahoo statistics feed — DCF and the value score's DCF sub-score are marked missing and the remaining weights renormalised. No beta was invented.");
  if (fcfBase == null || fcfBase <= 0) dataQuality.push("Latest filed free cash flow per share is not positive — a Gordon-growth DCF on a negative base is not meaningful, so the DCF is marked missing.");
  if (betaInfo && fcfBase != null && fcfBase > 0) {
    wacc = buildWacc(betaInfo.beta, m.market_cap, m.total_debt_latest);
    fv = fairValue(fcfBase, path, wacc.wacc_pct, DCF_CFG.terminal_growth_pct);
  }
  const upside = fv ? (fv.fair_value_per_share / price.last_close - 1) * 100 : null;
  if (m.debt_not_determinable_fail_closed) {
    dataQuality.push("Total debt could not be determined from any filed debt concept at the latest balance date — fail closed: the net-cash and leverage sub-scores are marked missing and weights renormalised. Missing debt is never treated as zero debt.");
  } else if (m.total_debt_resolution) {
    dataQuality.push(`Total debt ${fmtUsd(m.total_debt_latest ?? 0)} resolved as of ${m.total_debt_resolution.as_of} via ${m.total_debt_resolution.method} (repo priority: current+noncurrent pair, then single total concept). Finance-lease and operating-lease liabilities are excluded from total debt — several total concepts already embed capital/finance leases.`);
  }
  if (m.interest_coverage_ttm == null) {
    dataQuality.push(`Interest coverage sub-score missing (${m.interest_coverage_missing_reason ?? "unknown"}) — weight renormalised rather than awarded a free score.`);
  }
  if (m.sbc_not_reported_fcf_unadjusted_not_used) {
    dataQuality.push("No stock-based compensation was reported in any filed year — fail closed (v3): the FCF margin sub-score is marked missing rather than computed on unadjusted FCF. Raw FCF is disclosed in the KPIs as fcf_raw_*.");
  } else if (m.sbc_pct_of_revenue_latest != null) {
    dataQuality.push(`FCF is SBC-adjusted owner FCF (v3) — operating cash flow − capex − stock comp. Stock comp was ${m.sbc_pct_of_revenue_latest.toFixed(1)}% of revenue in the latest year${m.sbc_ttm != null ? ` (TTM SBC ${fmtUsd(m.sbc_ttm)})` : ""}; raw FCF is kept in the KPIs as fcf_raw_*.`);
  }
  if (m.dilution_share_series_discontinuity) {
    dataQuality.push("Dilution sub-score missing: the filed diluted share-count series has a >5x or <0.2x single-year jump (an unrestated split or a shares-vs-thousands units break) — fail closed, flagged rather than scored off the break.");
  } else if (m.dilution_share_count_growth_pct != null) {
    dataQuality.push(`Dilution: diluted share count changed ${m.dilution_share_count_growth_pct >= 0 ? "+" : ""}${m.dilution_share_count_growth_pct.toFixed(2)}%/yr over ${m.dilution_span_years ?? "?"} years (${m.dilution_annual_points ?? "?"} annual points) — Business sub-score (v3).`);
  }
  if (!conc.single_customer) dataQuality.push("No single-customer concentration sentence was extracted from the latest 10-K — the concentration sub-score is missing and moat weights renormalise.");
  dataQuality.push(PEER_UNIVERSE_NOTE);
  dataQuality.push("Consensus EPS: missing by design (v1, free sources only) — forward multiples use filed actuals and the config DCF only.");
  dataQuality.push("Switching costs / contract lock-in: not evaluated in v1 — no structured free source; flagged, not proxied.");
  if (m.gross_margin_latest_pct != null && m.gross_margin_median_5y_pct != null && m.gross_margin_latest_pct - m.gross_margin_median_5y_pct > 20) {
    dataQuality.push("Peak-cycle warning: latest gross margin is more than 20 pts above its 5-year median — through-cycle medians are what the scores use.");
  }

  const scoreInputs: Record<string, number | null> = {
    revenue_growth_latest_pct: m.revenue_growth_latest_pct,
    revenue_cagr_5y_pct: m.revenue_cagr_5y_pct,
    gross_margin_median_5y_pct: m.gross_margin_median_5y_pct,
    operating_margin_median_5y_pct: m.operating_margin_median_5y_pct,
    fcf_margin_median_5y_pct: m.fcf_margin_median_5y_pct,
    net_cash_to_revenue: m.net_cash_to_revenue,
    leverage_debt_to_revenue_ttm: m.leverage_debt_to_revenue_ttm,
    interest_coverage_ttm: m.interest_coverage_ttm,
    dilution_share_count_growth_pct: m.dilution_share_count_growth_pct,
    gross_margin_stdev_5y_pts: m.gross_margin_stdev_5y_pts,
    trailing_pe: m.trailing_pe,
    price_to_sales: m.price_to_sales,
    ev_to_sales: m.ev_to_sales,
    fcf_yield_latest_pct: m.fcf_yield_latest_pct,
    discount_from_52w_high_pct: m.discount_from_52w_high_pct,
    roic_median_5y_pct: m.roic_median_5y_pct,
    capex_intensity_latest_pct: m.capex_intensity_latest_pct,
    dcf_upside_pct: upside,
    customer_concentration_score: concScore,
    revenue_percentile_vs_peers: null, // labeled unavailable — see peer note
  };
  const business = scoreSection(BUSINESS_CFG, scoreInputs);
  const value = scoreSection(VALUE_CFG, scoreInputs);
  const moat = scoreSection(MOAT_CFG, scoreInputs);

  const moatChecks = [
    {
      check: "New-entrant capex barrier",
      status: m.capex_latest != null && m.capex_latest >= MOAT_CHECKS_CFG.entrant_capex_barrier_min_capex_usd ? "pass" : "fail",
      evidence: m.capex_latest != null ? `Latest FY capex $${m.capex_latest.toLocaleString("en-US")} vs config barrier $${MOAT_CHECKS_CFG.entrant_capex_barrier_min_capex_usd.toLocaleString("en-US")}` : "Capex missing from filings",
    },
    {
      check: "Product fungibility risk (margin volatility)",
      status: m.gross_margin_stdev_5y_pts != null && m.gross_margin_stdev_5y_pts > MOAT_CHECKS_CFG.margin_fungibility_stdev_pts_over ? "risk_flagged" : "not_flagged",
      evidence: `Gross-margin stdev over last 5 FYs = ${m.gross_margin_stdev_5y_pts != null ? m.gross_margin_stdev_5y_pts.toFixed(2) : "n/a"} pts (config flag threshold ${MOAT_CHECKS_CFG.margin_fungibility_stdev_pts_over} pts)`,
    },
    {
      check: "No single-customer dependency",
      status: conc.single_customer?.no_customer_at_or_above_threshold ? "pass" : "fail_or_unknown",
      evidence: conc.single_customer?.quote ?? "No single-customer sentence found in latest 10-K",
    },
    {
      check: "Switching costs / contract lock-in",
      status: "not_evaluated_v1",
      evidence: "No structured free source in v1 quantifies switching costs; flagged, not scored by proxy.",
    },
  ];

  // Generic leverage sentence (repo explanations.leverage_note).
  const levParts: string[] = [];
  for (const [key, label] of [["leverage_debt_to_revenue_ttm", "Debt-to-TTM-revenue leverage"], ["interest_coverage_ttm", "Interest coverage"]] as const) {
    const e = business.subscores[key];
    if (e) levParts.push(`${label} scored ${e.score}/10 (value ${e.value})`);
  }
  const leverageNote = levParts.length
    ? ` ${levParts.join(" and ")}.`
    : " Leverage checks (debt-to-TTM-revenue and interest coverage) were excluded: filed debt or interest data could not be determined.";

  // Generic dilution / SBC sentence (repo explanations.dilution_note).
  const dilParts: string[] = [];
  const dilEntry = business.subscores.dilution_share_count_growth_pct;
  if (dilEntry) {
    dilParts.push(`Diluted share count changed ${dilEntry.value >= 0 ? "+" : ""}${dilEntry.value}%/yr${m.dilution_span_years != null ? ` over ${m.dilution_span_years} years` : ""}, scored ${dilEntry.score}/10`);
  }
  if (m.sbc_not_reported_fcf_unadjusted_not_used) {
    dilParts.push("no stock-based compensation was reported, so the FCF margin sub-score was excluded rather than computed on unadjusted FCF");
  } else if (m.sbc_pct_of_revenue_latest != null) {
    dilParts.push(`FCF is SBC-adjusted owner FCF (stock comp was ${m.sbc_pct_of_revenue_latest.toFixed(1)}% of revenue in the latest year)`);
  }
  const dilutionNote = dilParts.length ? ` ${dilParts.join("; ")}.` : "";

  const explanations = {
    business: explain("Business", business, leverageNote + dilutionNote),
    value: explain("Value", value, fv && upside != null ? ` The DCF fair value is $${fv.fair_value_per_share.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })} vs the current $${price.last_close.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })} (${upside >= 0 ? "+" : ""}${upside.toFixed(1)}%).` : ""),
    moat: explain("Moat", moat),
  };

  return {
    symbol,
    entityName,
    cik: resolved.cik,
    snapshotDate: new Date().toISOString().slice(0, 10),
    price: { ...price, url: `https://query1.finance.yahoo.com/v8/finance/chart/${symbol}?range=1y&interval=1d` },
    beta: betaInfo ? { value: betaInfo.beta, source: betaInfo.source } : null,
    kpis: m,
    scores: { business, value, moat },
    dcf: {
      fcf_per_share_base: fcfBase != null ? Math.round(fcfBase * 10000) / 10000 : null,
      growth_path_pct: path,
      wacc,
      wacc_inputs: { risk_free_rate_pct: DCF_CFG.risk_free_rate_pct, equity_risk_premium_pct: DCF_CFG.equity_risk_premium_pct, beta: betaInfo?.beta ?? null, pre_tax_cost_of_debt_pct: DCF_CFG.pre_tax_cost_of_debt_pct, tax_rate_pct: DCF_CFG.tax_rate_pct },
      fair_value_per_share: fv?.fair_value_per_share ?? null,
      pv_of_projected_fcf: fv?.pv_of_projected_fcf ?? null,
      pv_of_terminal_value: fv?.pv_of_terminal_value ?? null,
      projected_fcf_per_share: fv?.projected_fcf_per_share ?? [],
      current_price: price.last_close,
      upside_pct: upside != null ? Math.round(upside * 100) / 100 : null,
      distance_dollars: fv ? Math.round((fv.fair_value_per_share - price.last_close) * 100) / 100 : null,
    },
    concentration: conc,
    concentrationScore: concScore,
    moatChecks,
    explanations,
    dataQuality,
    sources: {
      filingUrl: conc.filing?.url ?? null,
      priceFeed: price.source,
      scoringConfig: "config/scoring.yaml (repo) — anchors & weights copied verbatim",
      dcfConfig: "config/dcf.yaml (repo) — CAPM WACC, filed-CAGR growth clamped 0–25% and faded to 0% terminal",
      peerNote: PEER_UNIVERSE_NOTE,
    },
  };
}
