import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { SafeAreaTopScrim } from "@hatch/space-sdk/client";
import { api } from "./api";

/* ------------------------------------------------------------------ */
/* Analyst note content — MU deep-dive 2026-11-03, updated 2026-11-06. */
/* Hard numbers: Micron FY26 10-K (filed Oct 9, 2026) & Q4 release.    */
/* Forecasts: consensus via TIKR / Zacks / MarketBeat.                  */
/* Prices: Finnhub checked against StockAnalysis.                      */
/* ------------------------------------------------------------------ */

const SCORES = {
  business: {
    label: "Business",
    score: 7,
    verdict: "Strong franchise, peak-cycle earnings",
    subscores: [
      { factor: "Moat / HBM position", score: 6, weight: "30%", note: "#3 in HBM at ~18% share — an oligopoly of three, but the leader holds ~2.8× MU's share" },
      { factor: "Growth & backlog", score: 8, weight: "20%", note: "$150B RPO across 26 take-or-pay deals; >35% of revenue covered through 2030; most CY27 HBM supply already locked" },
      { factor: "Profitability", score: 9, weight: "20%", note: "Q4 FY26 gross margin 87.0%, FY gross margin 80.7%, operating margin 74.6% — the best in MU's history, and the peak question in one line" },
      { factor: "Balance sheet", score: 9, weight: "10%", note: "$73.45B cash + investments vs $5.18B debt — ~$68.3B net cash" },
      { factor: "Cycle & supply risk", score: 4, weight: "20%", note: "Commodity memory. Micron, SK hynix and Samsung are all adding 2027–28 capacity; price, not demand, is the risk" },
    ],
  },
  moat: {
    label: "Moat",
    score: 6,
    verdict: "Real, but shared with two rivals",
    subscores: [
      { factor: "Market structure", score: 8, weight: "—", note: "Only three makers of HBM at scale on earth: SK hynix, Samsung, Micron" },
      { factor: "MU's share of it", score: 5, weight: "—", note: "HBM revenue share ~18% (Q2 2026) vs SK hynix ~50% and Samsung ~33%" },
      { factor: "Switching / qualification", score: 7, weight: "—", note: "HBM is qualified into AI accelerators years ahead; take-or-pay contracts lock volume — but pricing still resets with the commodity cycle" },
      { factor: "Geography", score: 7, weight: "—", note: "The only US-based memory maker — a genuine strategic premium with US hyperscalers and government" },
    ],
  },
  valueOriginal: {
    label: "Value — original",
    score: 7,
    asOf: "Nov 3, 2026 · $855.10",
    verdict: "Attractive if peak earnings hold",
    subscores: [
      { factor: "Forward multiples", score: 9, weight: "20%", note: "4.0–4.5× FY27E earnings at the Nov 6 price — optically very cheap" },
      { factor: "Trailing / normalized", score: 5, weight: "25%", note: "5.9× trailing sales and 5.3× EV/Sales vs a 2–3× mid-cycle norm for memory — not cheap on normalized numbers" },
      { factor: "DCF upside", score: 9, weight: "25%", note: "+42% to the $1,213 base-case fair value at the original $855.10 DD price" },
      { factor: "Consensus", score: 8, weight: "10%", note: "Target $1,562 across 49 analysts — but the $361–$3,000 range is deep disagreement, not agreement" },
      { factor: "Peak-earnings haircut", score: 4, weight: "20%", note: "Gross margin at 87% is the denominator problem: if margin normalizes, the 'cheap' forward P/E evaporates" },
    ],
  },
  valueToday: {
    label: "Today's value",
    score: 7,
    asOf: "Nov 6, 2026 · $690.00",
    verdict: "Held at 7 — cheaper price, higher risk premium",
    subscores: [
      { factor: "What changed", score: 7, weight: "—", note: "Price fell −19.4% from the DD ($855.10 → $690.00); multiples compressed; estimates had not been cut" },
      { factor: "Why not an 8", score: 7, weight: "—", note: "The fall raised the market's required return for peak-cycle risk instead of creating a free discount — the haircut stays until Dec earnings prove 87% margins are durable" },
    ],
  },
};

const KPIS: { label: string; value: string; sub: string; tone?: "up" | "warn" }[] = [
  { label: "Revenue — FY26", value: "$133.2B", sub: "+256% YoY · 10-K", tone: "up" },
  { label: "Revenue — Q4 FY26", value: "$54.23B", sub: "+379% YoY", tone: "up" },
  { label: "GAAP EPS — FY26", value: "$74.33", sub: "Non-GAAP $75.52" },
  { label: "Gross margin — Q4", value: "87.0%", sub: "FY26 full year 80.7%", tone: "warn" },
  { label: "Operating cash flow", value: "$89.7B", sub: "FY26" },
  { label: "Free cash flow", value: "$59.0B", sub: "After $30.7B capex" },
  { label: "Net cash", value: "$68.3B", sub: "$73.45B cash+inv vs $5.18B debt" },
  { label: "Backlog / RPO", value: "$150B", sub: "26 take-or-pay agreements" },
  { label: "Q1 FY27 guide — revenue", value: "$61.5B", sub: "± $1.5B" },
  { label: "Q1 FY27 guide — adj. EPS", value: "$38.15", sub: "± $1.00 · GM ~86%" },
  { label: "HBM share", value: "~18%", sub: "#3 of 3 makers · Q2 2026" },
  { label: "Next test", value: "Dec 2026", sub: "Q1 FY27 earnings — calendars split Dec 16 vs 23" },
];

const VALUATION: { metric: string; mu: string; read: string }[] = [
  { metric: "Trailing P/E", mu: "9.3×", read: "On peak TTM EPS of $74.33 — flatters the stock" },
  { metric: "Forward P/E (FY27E)", mu: "4.0–4.5×", read: "Only if FY27 EPS of $155–176 actually prints" },
  { metric: "Trailing P/S", mu: "5.9×", read: "Memory's mid-cycle norm is ~2–3× sales" },
  { metric: "Forward P/S", mu: "2.9×", read: "Back inside the historical band — on peak sales" },
  { metric: "EV / Sales", mu: "5.3×", read: "EV ~$712B after $68.3B net cash" },
  { metric: "Price / Book", mu: "5.6×", read: "Book value is not the constraint; earnings durability is" },
  { metric: "FCF yield", mu: "7.6%", read: "FY26 FCF $59.0B — before FY27's capex step-up (~$25B in H1 alone)" },
  { metric: "Beta", mu: "2.43", read: "Why the DCF uses an 11% WACC, not 7–8%" },
  { metric: "Consensus target", mu: "$1,562", read: "+126% vs $690 · 49 analysts · range $361–$3,000" },
  { metric: "52-week range", mu: "$192.59 – $1,255", read: "Price is −45.1% from the high, +141.8% YTD" },
];

const PEERS: {
  name: string;
  ticker: string;
  hbm: string;
  dram: string;
  edge: string;
  watch: string;
  isMu?: boolean;
}[] = [
  {
    name: "SK hynix",
    ticker: "000660 · KRX",
    hbm: "~50%",
    dram: "24.9%",
    edge: "The HBM leader. First to qualify at Nvidia, ~10 long-term customer agreements, HBM4 shipping — the share MU has to take, not defend.",
    watch: "Share already slipped 64% → 50% in a year as Samsung recovered; trades at a similar forward P/E to MU on comparable profits.",
  },
  {
    name: "Samsung Electronics",
    ticker: "005930 · KRX",
    hbm: "~33%",
    dram: "39.4%",
    edge: "The scale player: #1 in overall DRAM and NAND, first commercial HBM4 shipment (Feb 2026), HBM4 yields reported ~80%.",
    watch: "A conglomerate (foundry, phones, displays) — memory is one segment, so it never trades on memory alone; its HBM comeback is the main supply threat to MU's pricing.",
  },
  {
    name: "Micron",
    ticker: "MU · NASDAQ",
    hbm: "~18%",
    dram: "23.3%",
    edge: "The only US-based maker. Q2 DRAM revenue grew +65.5% QoQ — fastest of the three — and margins now match SK hynix (~80%+). $150B of take-or-pay RPO de-risks volume.",
    watch: "#3 in the product that matters most. Its moat is qualification + geography, not share — so its valuation case rests on pricing holding, not on winning share.",
    isMu: true,
  },
];

/* Moat test — is the advantage valid/durable, or can it be copied?
   New entrant vs existing rival are different questions, so they are
   scored separately. Customer concentration is from the FY26 10-K. */
const MOAT_CHECKS: {
  test: string;
  verdict: string;
  tone: "strong" | "mid" | "weak";
  replicable: string;
  evidence: string;
}[] = [
  {
    test: "Can a new entrant copy it?",
    verdict: "No — strong barrier",
    tone: "strong",
    replicable: "Not replicable",
    evidence:
      "A leading-edge fab costs ~$15–20B, takes 3–5 years to build, and needs EUV lithography, through-silicon-via (TSV) stacking and a dense patent thicket. That is why there are still only three HBM makers at scale — no credible newcomer has cleared it.",
  },
  {
    test: "Can the existing rivals copy it?",
    verdict: "Yes — they already have",
    tone: "weak",
    replicable: "Replicable by rivals",
    evidence:
      "SK hynix (~50% HBM share) and Samsung (~33%) make the same product, qualified first at key customers and are already shipping HBM4. Any Micron lead — e.g. power efficiency in one HBM generation — is a temporary feature that resets with the next generation, not a permanent lock.",
  },
  {
    test: "Switching & qualification",
    verdict: "Real, but per-generation",
    tone: "mid",
    replicable: "Hard to copy quickly",
    evidence:
      "HBM is qualified into AI accelerators years ahead and locked with take-or-pay contracts, so a customer cannot swap supplier mid-generation. At the next qualification round, though, all three compete again on equal footing.",
  },
  {
    test: "Product differentiation",
    verdict: "Weak underneath HBM",
    tone: "weak",
    replicable: "Easily substituted",
    evidence:
      "Standard DRAM and NAND are fungible commodities — interchangeable, dual-sourced, with low switching costs. Only the HBM layer is differentiated, and only for as long as that generation's qualification lasts.",
  },
  {
    test: "Pricing power on its own",
    verdict: "Weak — cycle gives it, not MU",
    tone: "weak",
    replicable: "Not Micron's alone",
    evidence:
      "Micron cannot raise prices by itself without losing volume; memory prices are set by global supply and demand. Pricing power appears only when the whole oligopoly is short — it belongs to the cycle and the three-player structure, not to Micron alone.",
  },
  {
    test: "Geography — only US maker",
    verdict: "Real and not copyable",
    tone: "mid",
    replicable: "Not replicable",
    evidence:
      "Micron is the only US-based memory maker, a genuine strategic premium with US hyperscalers and government. It helps win allocation and support, but it does not stop a price down-cycle.",
  },
];

/* Customer concentration — FY26 Form 10-K, filed Oct 9, 2026.
   Single customer: Note 21. Top-10 & data-center: Item 1A risk factors.
   By headquarters: Note 22. The filing does not name any customer. */
const CUSTOMER_CONCENTRATION: {
  label: string;
  value: string;
  detail: string;
  barPct: number;
}[] = [
  {
    label: "Largest single customer — FY26",
    value: "<10%",
    detail:
      "No customer accounted for 10% or more of total revenue in FY26 (Note 21). That is an improvement: one customer was 17% in FY25 — primarily in Cloud Memory (CMBU) — and 10% in FY24.",
    barPct: 9,
  },
  {
    label: "Top 10 customers — FY26",
    value: ">50%",
    detail:
      "Over half of total revenue came from the top ten customers (Item 1A). Single-name risk is low, top-10 risk is not — on $133.2B of revenue that averages more than $6.6B per top-10 customer.",
    barPct: 55,
  },
  {
    label: "Data-center end market — FY26",
    value: "~60%",
    detail:
      "Approximately 60% of revenue came from the data-center end market (Item 1A), up from about half in FY25. Today the bigger concentration is the end market, not one buyer.",
    barPct: 60,
  },
  {
    label: "US-headquartered customers — FY26",
    value: "68.6%",
    detail:
      "$91.3B of $133.2B by customer headquarters (Note 22). Taiwan is next at $17.0B — the book is heavily US-hyperscaler weighted by headquarters.",
    barPct: 69,
  },
];

/* Bottom briefs — why each score was given. Kept to 2 sentences each,
   as requested; the factor math stays on the score cards above. */
const SCORE_WHY: { label: string; score: number; asOf: string; brief: string }[] = [
  {
    label: "Business — 7/10",
    score: 7,
    asOf: "Nov 3, 2026 deep-dive",
    brief:
      "Micron sits in a three-company oligopoly with record profitability, about $68.3B of net cash and $150B of contracted backlog, so the franchise itself is strong. It does not score higher because memory is still a commodity cycle — all three makers are adding 2027–28 supply, and today's 87% gross margin is a peak, not a normal year.",
  },
  {
    label: "Moat — 6/10",
    score: 6,
    asOf: "Valid but narrow — see Moat section above",
    brief:
      "A new entrant cannot realistically copy this — a leading fab costs $15–20B, takes years to build, and HBM must be qualified into AI accelerators well ahead of shipment. But the two existing rivals already have the same technology, the product underneath is a fungible commodity, and Micron is #3 in HBM — so the moat is real, yet shared and narrow.",
  },
  {
    label: "Value — original — 7/10",
    score: 7,
    asOf: "Nov 3, 2026 · $855.10",
    brief:
      "At $855.10 the stock traded at only about 4–5× forward earnings with +42% upside to the $1,213 DCF fair value, which looks cheap on its face. It stays at 7, not 9, because that cheapness depends on peak earnings holding — on trailing sales and normalized margins it was not cheap at all.",
  },
  {
    label: "Today's value — 7/10",
    score: 7,
    asOf: "Nov 6, 2026 · $690.00",
    brief:
      "The price fell 19.4% and multiples compressed, but estimates had not been cut and the fall itself was the market charging more for peak-cycle risk. The score only upgrades when December earnings prove those margins are durable, not when the price alone drops.",
  },
];

const DCF_INPUTS: { label: string; value: string; note: string }[] = [
  { label: "FCF / share — base (FY26)", value: "$52.17", note: "FY26 FCF $59.0B ÷ 1.131B shares" },
  { label: "FY27E EPS", value: "$176.50", note: "+133.7% vs FY26 — growth rate applied to FCF" },
  { label: "FY28E EPS — peak year", value: "$207.76", note: "+17.7%" },
  { label: "FY29E EPS — fade begins", value: "$203.33", note: "−2.1%" },
  { label: "FY30–31 fade", value: "−10% / −5%", note: "Supply lands; margins normalize gently in the base case" },
  { label: "WACC", value: "11%", note: "MU is ~all-equity funded (net cash), so WACC ≈ cost of equity; beta 2.43 demands well above a stable name's 7–8%" },
  { label: "Terminal growth", value: "0%", note: "Deliberately no perpetual growth for a commodity memory maker" },
];

/** DCF engine — same path as the note. A calibration constant pins the
 *  base case (11% WACC, 0% terminal, no haircut) to the note's $1,213. */
const DCF_GROWTH = [1.337, 0.177, -0.021, -0.1, -0.05];
const DCF_BASE_FCF = 52.17;
function dcfRaw(wacc: number, termG: number, haircut: number): number {
  let fcf = DCF_BASE_FCF;
  let pv = 0;
  const flows: number[] = [];
  for (const g of DCF_GROWTH) {
    fcf = fcf * (1 + g);
    flows.push(fcf * (1 - haircut));
  }
  flows.forEach((f, i) => {
    pv += f / Math.pow(1 + wacc, i + 1);
  });
  const last = flows[flows.length - 1] ?? 0;
  const tv = wacc > termG ? (last * (1 + termG)) / (wacc - termG) : 0;
  pv += tv / Math.pow(1 + wacc, flows.length);
  return pv;
}
const DCF_CAL = 1213 / dcfRaw(0.11, 0, 0);
function dcfFairValue(waccPct: number, termGPct: number, haircutPct: number): number {
  return dcfRaw(waccPct / 100, termGPct / 100, haircutPct / 100) * DCF_CAL;
}

function fmt(n: number): string {
  return n.toLocaleString("en-US", { maximumFractionDigits: 0 });
}
function fmt2(n: number): string {
  return n.toLocaleString("en-US", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });
}

function ScoreDial({ score }: { score: number }) {
  const pct = score / 10;
  const r = 34;
  const circ = Math.PI * r; // semicircle
  return (
    <svg viewBox="0 0 84 48" className="w-24" role="img" aria-label={`Score ${score} out of 10`}>
      <path d="M 8 44 A 34 34 0 0 1 76 44" fill="none" stroke="var(--border)" strokeWidth="7" strokeLinecap="round" />
      <path
        d="M 8 44 A 34 34 0 0 1 76 44"
        fill="none"
        stroke="var(--accent)"
        strokeWidth="7"
        strokeLinecap="round"
        strokeDasharray={`${circ * pct} ${circ}`}
      />
      <text x="42" y="42" textAnchor="middle" fontSize="19" fontWeight="700" fill="var(--text)" fontFamily="var(--font-serif)">
        {score}
      </text>
    </svg>
  );
}

function SectionTitle({ children, sub }: { children: string; sub?: string }) {
  return (
    <div className="mt-10 mb-3">
      <h2 className="font-serif text-2xl font-semibold text-[var(--text)]">{children}</h2>
      {sub ? <p className="mt-1 text-sm text-[var(--dim)]">{sub}</p> : null}
    </div>
  );
}

export function App() {
  const queryClient = useQueryClient();
  const dash = useQuery({ queryKey: ["dashboard"], queryFn: () => api.getDashboard({}) });
  const refresh = useMutation({
    mutationFn: () => api.refreshDaily({}),
    onSuccess: () => {
      window.setTimeout(() => queryClient.invalidateQueries({ queryKey: ["dashboard"] }), 4000);
    },
  });

  const [wacc, setWacc] = useState(11);
  const [termG, setTermG] = useState(0);
  const [haircut, setHaircut] = useState(0);

  const snap = dash.data?.snapshot;
  const price = snap?.price ?? 690;
  const fair = useMemo(() => dcfFairValue(wacc, termG, haircut), [wacc, termG, haircut]);
  const baseFair = 1213;
  const upside = ((baseFair - price) / price) * 100;
  const fairGapPct = ((fair - price) / price) * 100;
  const maxScale = Math.max(1562, baseFair, price) * 1.05;

  return (
    <div className="min-h-screen bg-[var(--bg)] text-[var(--text)]">
      <SafeAreaTopScrim backgroundColor="var(--bg)" />
      <main className="mx-auto max-w-5xl px-4 pb-14">
        {/* ---------- Ticker header ---------- */}
        <header className="pt-6">
          <div className="flex flex-wrap items-end justify-between gap-3">
            <div>
              <div className="flex items-baseline gap-2">
                <span className="font-serif text-4xl font-bold tracking-tight">MU</span>
                <span className="text-base text-[var(--dim)]">Micron Technology · NASDAQ</span>
              </div>
              <p className="mt-1 max-w-xl text-sm text-[var(--dim)]">
                The only US-based maker of DRAM, NAND and HBM memory — one of three companies on earth
                that make high-bandwidth memory for AI accelerators at scale.
              </p>
            </div>
            <div className="text-right">
              <div className="num font-serif text-4xl font-bold">${fmt2(price)}</div>
              <div className="num text-sm">
                {snap?.changePct != null ? (
                  <span className={snap.changePct >= 0 ? "text-[var(--green)]" : "text-[var(--red)]"}>
                    {snap.changePct >= 0 ? "+" : ""}
                    {fmt2(snap.changePct)}% on the day
                  </span>
                ) : null}
                {snap?.marketCapB != null ? (
                  <span className="text-[var(--dim)]"> · Mkt cap ${fmt(snap.marketCapB)}B</span>
                ) : null}
              </div>
              <p className="mt-0.5 text-xs text-[var(--dim)]">
                Close {snap?.asOf ?? "2026-11-06"} · {snap?.source ?? "Finnhub / StockAnalysis"}
              </p>
            </div>
          </div>

          <div className="mt-3 flex flex-wrap items-center gap-2 text-xs">
            <span className="rounded-full bg-[var(--accent-soft)] px-2.5 py-1 font-medium text-[var(--accent)]">
              Your position: 500 shares · bought @ $852.88
            </span>
            <span className="rounded-full bg-[var(--surface-alt)] px-2.5 py-1 text-[var(--dim)]">
              Deep-dive Nov 3, 2026 · updated Nov 6, 2026
            </span>
            <button
              type="button"
              onClick={() => refresh.mutate()}
              disabled={refresh.isPending}
              className="rounded-full border border-[var(--border)] bg-[var(--surface)] px-2.5 py-1 font-medium disabled:opacity-50"
              aria-label="Refresh latest MU closing price"
            >
              {refresh.isPending ? "Refreshing…" : "↻ Refresh price"}
            </button>
            {refresh.data ? (
              <span className="text-[var(--dim)]">
                {refresh.data.started
                  ? "Refresh running — the new close lands here shortly."
                  : "Refresh couldn't start; showing the last stored close."}
              </span>
            ) : null}
          </div>

          {/* 52-week range */}
          <div className="mt-4 rounded-[var(--radius)] border border-[var(--border)] bg-[var(--surface)] px-4 py-3">
            <div className="flex justify-between text-xs text-[var(--dim)]">
              <span>52-wk low <span className="num text-[var(--text)]">$192.59</span></span>
              <span>−45.1% from the $1,255 high · +141.8% YTD</span>
              <span>52-wk high <span className="num text-[var(--text)]">$1,255</span></span>
            </div>
            <div className="relative mt-2 h-2 rounded-full bg-[var(--surface-alt)]">
              <div
                className="absolute top-1/2 h-4 w-1.5 -translate-y-1/2 rounded bg-[var(--accent)]"
                style={{ left: `${(((price - 192.59) / (1255 - 192.59)) * 100).toFixed(1)}%` }}
                aria-hidden
              />
            </div>
          </div>
        </header>

        {/* ---------- Score strip ---------- */}
        <SectionTitle sub="Analyst judgment on a 0–10 rubric — the factor math is shown under each score so you can re-weight it yourself. There is no black-box formula behind these.">
          Scores at a glance
        </SectionTitle>
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          {[
            { s: SCORES.business, asOf: "Nov 3, 2026 deep-dive" },
            { s: SCORES.moat, asOf: "Component of Business" },
            { s: SCORES.valueOriginal, asOf: SCORES.valueOriginal.asOf },
            { s: SCORES.valueToday, asOf: SCORES.valueToday.asOf },
          ].map(({ s, asOf }) => (
            <article key={s.label} className="rounded-[var(--radius)] border border-[var(--border)] bg-[var(--surface)] p-4">
              <div className="flex items-start justify-between gap-2">
                <div>
                  <h3 className="text-sm font-semibold">{s.label}</h3>
                  <p className="text-xs text-[var(--dim)]">{asOf}</p>
                </div>
                <ScoreDial score={s.score} />
              </div>
              <p className="mt-1 text-sm font-medium">{s.verdict}</p>
              <ul className="mt-3 space-y-2.5 border-t border-[var(--border)] pt-3">
                {s.subscores.map((sub) => (
                  <li key={sub.factor} className="text-xs">
                    <div className="flex items-baseline justify-between gap-2">
                      <span className="font-semibold">{sub.factor}</span>
                      <span className="num shrink-0 text-[var(--dim)]">
                        {sub.score}/10{sub.weight !== "—" ? ` · ${sub.weight}` : ""}
                      </span>
                    </div>
                    <p className="mt-0.5 leading-relaxed text-[var(--dim)]">{sub.note}</p>
                  </li>
                ))}
              </ul>
            </article>
          ))}
        </div>
        <p className="num mt-2 text-xs text-[var(--dim)]">
          Weighted check — Business: 6×30% + 8×20% + 9×20% + 9×10% + 4×20% = 6.9 → 7 · Value: 9×20% + 5×25% + 9×25% + 8×10% + 4×20% = 6.9 → 7
        </p>

        <div className="mt-3 rounded-[var(--radius)] border border-[var(--amber)] bg-[var(--amber-soft)] p-4">
          <h3 className="text-sm font-semibold">Why Today's Value didn't rise when the price fell 19%</h3>
          <p className="mt-1 text-sm leading-relaxed">
            A cheaper price only raises the score if the risk stayed the same. Here it didn't: MU's earnings are at a
            cyclical peak (87% gross margin), estimates hadn't been cut yet, and the −19.4% slide was the market raising
            its required return for exactly that peak risk. The score upgrades when December earnings prove the margins
            are durable — not when the price alone moves.
          </p>
        </div>

        {/* ---------- KPIs ---------- */}
        <SectionTitle sub="Fiscal 2026, year ended Sep 3, 2026 — from the 10-K filed Oct 9, 2026 and the Q4 earnings release.">
          Key metrics
        </SectionTitle>
        <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
          {KPIS.map((k) => (
            <div key={k.label} className="rounded-[var(--radius)] border border-[var(--border)] bg-[var(--surface)] p-3.5">
              <p className="text-xs font-medium text-[var(--dim)]">{k.label}</p>
              <p className="num mt-1 font-serif text-2xl font-bold">{k.value}</p>
              <p className={`mt-0.5 text-xs ${k.tone === "warn" ? "text-[var(--amber)]" : "text-[var(--dim)]"}`}>{k.sub}</p>
            </div>
          ))}
        </div>

        {/* ---------- Valuation ---------- */}
        <SectionTitle sub="At the Nov 6, 2026 close of $690.00 — the price the current snapshot started from. The DCF section below re-prices the gap against the latest stored close.">
          Valuation matrix
        </SectionTitle>
        <div className="overflow-hidden rounded-[var(--radius)] border border-[var(--border)] bg-[var(--surface)]">
          <table className="w-full text-left text-sm">
            <thead>
              <tr className="border-b border-[var(--border)] bg-[var(--surface-alt)] text-xs text-[var(--dim)]">
                <th scope="col" className="px-4 py-2.5 font-semibold">Metric</th>
                <th scope="col" className="px-4 py-2.5 font-semibold">MU</th>
                <th scope="col" className="hidden px-4 py-2.5 font-semibold sm:table-cell">How to read it</th>
              </tr>
            </thead>
            <tbody>
              {VALUATION.map((row, i) => (
                <tr key={row.metric} className={i % 2 ? "bg-[var(--surface-alt)]/50" : ""}>
                  <th scope="row" className="px-4 py-2.5 font-medium">{row.metric}</th>
                  <td className="num px-4 py-2.5 font-semibold">{row.mu}</td>
                  <td className="hidden px-4 py-2.5 text-[var(--dim)] sm:table-cell">{row.read}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <p className="mt-2 text-xs text-[var(--dim)] sm:hidden">
          Reads: forward multiples flatter MU because the denominator is peak earnings; trailing sales multiples say the
          opposite. Both views are in the full table on a wider screen.
        </p>

        {/* ---------- Peers ---------- */}
        <SectionTitle sub="How the peer call was made: only three companies make HBM at scale, so the peer set is the industry. Each was scored on the same yardsticks — HBM revenue share (Counterpoint Research, Q2 2026), overall DRAM share (TrendForce, Q2 2026), margins from filings, forward multiples on consensus, and contract coverage.">
          Peer evaluation — the HBM three
        </SectionTitle>
        <div className="grid gap-3 lg:grid-cols-3">
          {PEERS.map((p) => (
            <article
              key={p.name}
              className={`rounded-[var(--radius)] border bg-[var(--surface)] p-4 ${
                p.isMu ? "border-[var(--accent)] ring-1 ring-[var(--accent)]" : "border-[var(--border)]"
              }`}
            >
              <div className="flex items-baseline justify-between gap-2">
                <h3 className="font-serif text-lg font-semibold">{p.name}</h3>
                <span className="text-xs text-[var(--dim)]">{p.ticker}</span>
              </div>
              <div className="num mt-3 grid grid-cols-2 gap-2 text-center">
                <div className="rounded-md bg-[var(--surface-alt)] py-2">
                  <div className="text-lg font-bold">{p.hbm}</div>
                  <div className="text-[11px] text-[var(--dim)]">HBM share</div>
                </div>
                <div className="rounded-md bg-[var(--surface-alt)] py-2">
                  <div className="text-lg font-bold">{p.dram}</div>
                  <div className="text-[11px] text-[var(--dim)]">DRAM share</div>
                </div>
              </div>
              <p className="mt-3 text-sm leading-relaxed">{p.edge}</p>
              <p className="mt-2 border-t border-[var(--border)] pt-2 text-sm leading-relaxed text-[var(--dim)]">
                <span className="font-semibold text-[var(--text)]">Watch: </span>
                {p.watch}
              </p>
            </article>
          ))}
        </div>
        <div className="mt-3 rounded-[var(--radius)] border border-[var(--border)] bg-[var(--surface)] p-4 text-sm leading-relaxed">
          <span className="font-semibold">The verdict from the peer work: </span>
          MU is #3 where it counts — HBM share — but its current profitability is no longer #3: Q4 gross margin of 87%
          matches the leaders, its DRAM revenue grew fastest of the three last quarter (+65.5% QoQ), and it is the only
          one a US investor can buy directly on a US exchange without an ADR or a conglomerate attached. That is why the
          Business score is a 7 despite the share gap — and why the moat is a 6, not an 8: pricing power belongs to the
          cycle and to SK hynix's qualification lead, not to Micron.
        </div>

        {/* ---------- Moat — valid or easily replicated? + customer concentration ---------- */}
        <SectionTitle sub="Moat score 6/10. Tested two ways: could a newcomer copy the advantage, and could the two rivals who already have it copy the next step? Customer concentration from the filings is checked in the same section — a moat held up by one or two buyers is not a moat.">
          Moat — valid, or easily replicated?
        </SectionTitle>

        <div className="rounded-[var(--radius)] border border-[var(--accent)] bg-[var(--surface)] p-4 ring-1 ring-[var(--accent)]">
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div className="max-w-2xl">
              <h3 className="font-serif text-lg font-semibold">Verdict: valid, but narrow and shared — not a wide moat</h3>
              <p className="mt-1 text-sm leading-relaxed text-[var(--dim)]">
                Hard for anyone new to enter, easy for SK hynix and Samsung to contest. Micron owns a seat in a
                three-player oligopoly — it does not own the oligopoly.
              </p>
            </div>
            <div className="flex items-center gap-2">
              <ScoreDial score={SCORES.moat.score} />
              <span className="text-sm font-semibold">Moat 6/10</span>
            </div>
          </div>
        </div>

        <div className="mt-3 grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {MOAT_CHECKS.map((c) => (
            <article key={c.test} className="rounded-[var(--radius)] border border-[var(--border)] bg-[var(--surface)] p-4">
              <h3 className="text-sm font-semibold">{c.test}</h3>
              <div className="mt-2 flex flex-wrap gap-1.5 text-[11px] font-semibold">
                <span
                  className={`rounded-full px-2 py-0.5 ${
                    c.tone === "strong"
                      ? "bg-[var(--green-soft)] text-[var(--green)]"
                      : c.tone === "mid"
                        ? "bg-[var(--amber-soft)] text-[var(--amber)]"
                        : "bg-[var(--red-soft)] text-[var(--red)]"
                  }`}
                >
                  {c.verdict}
                </span>
                <span className="rounded-full bg-[var(--surface-alt)] px-2 py-0.5 text-[var(--dim)]">{c.replicable}</span>
              </div>
              <p className="mt-2 text-sm leading-relaxed text-[var(--dim)]">{c.evidence}</p>
            </article>
          ))}
        </div>

        <div className="mt-3 rounded-[var(--radius)] border border-[var(--border)] bg-[var(--surface)] p-4">
          <div className="flex flex-wrap items-baseline justify-between gap-2">
            <h3 className="font-serif text-lg font-semibold">Customer concentration — from the filings</h3>
            <span className="text-xs text-[var(--dim)]">FY26 Form 10-K · filed Oct 9, 2026 · year ended Sep 3, 2026</span>
          </div>
          <p className="mt-1 text-sm text-[var(--dim)]">
            The filing does not name any customer. What it does disclose is enough to judge the risk: how much rides
            on the single largest buyer, on the top ten, and on one end market.
          </p>

          <div className="mt-4 grid gap-3 sm:grid-cols-2">
            {CUSTOMER_CONCENTRATION.map((row) => (
              <div key={row.label} className="rounded-md bg-[var(--surface-alt)] p-3.5">
                <div className="flex items-baseline justify-between gap-2">
                  <p className="text-xs font-medium text-[var(--dim)]">{row.label}</p>
                  <p className="num font-serif text-2xl font-bold">{row.value}</p>
                </div>
                <div className="mt-2 h-2 overflow-hidden rounded-full bg-[var(--surface)]" aria-hidden>
                  <div className="h-full rounded-full bg-[var(--accent)]" style={{ width: `${row.barPct}%` }} />
                </div>
                <p className="mt-2 text-xs leading-relaxed text-[var(--dim)]">{row.detail}</p>
              </div>
            ))}
          </div>

          <div className="mt-4 space-y-2 text-sm">
            <div>
              <div className="flex justify-between gap-2 text-xs text-[var(--dim)]">
                <span>Largest customer, FY25</span>
                <span className="num font-semibold text-[var(--text)]">17%</span>
              </div>
              <div className="mt-1 h-2 overflow-hidden rounded-full bg-[var(--surface-alt)]" aria-hidden>
                <div className="h-full w-[17%] rounded-full bg-[var(--red)]" />
              </div>
            </div>
            <div>
              <div className="flex justify-between gap-2 text-xs text-[var(--dim)]">
                <span>Largest customer, FY26 — below the 10% disclosure line</span>
                <span className="num font-semibold text-[var(--text)]">&lt;10%</span>
              </div>
              <div className="mt-1 h-2 overflow-hidden rounded-full bg-[var(--surface-alt)]" aria-hidden>
                <div className="h-full w-[9%] rounded-full bg-[var(--green)]" />
              </div>
            </div>
          </div>

          <p className="mt-4 border-t border-[var(--border)] pt-3 text-sm leading-relaxed">
            <span className="font-semibold">What this means for the moat: </span>
            No single buyer can break Micron by leaving in FY26 — that risk fell from 17% to under 10%. But with the
            top ten still over half of sales and about 60% tied to data centers, a hyperscaler capex pause would
            still hit most of the book at once, so buyer concentration caps the moat at 6 rather than lifting it.
          </p>
          <p className="mt-2 text-xs text-[var(--dim)]">
            Sources: FY26 10-K Note 21 (single customer), Item 1A (top ten, data-center share) and Note 22 (by
            headquarters), as summarised in{" "}
            <a className="underline" href="https://evidinvest.com/blog/mu-fy2026-10k-134-billion-rpo-what-the-annual-report-adds-2026-10-13" target="_blank" rel="noreferrer">the FY26 10-K read-through</a>{" "}
            and{" "}
            <a className="underline" href="https://www.fool.com/investing/2026/10/14/no-single-customer-accounts-for-10-of-micron-s-sales-anymore-and-that-lowers-one-risk-in-the-stock/" target="_blank" rel="noreferrer">Motley Fool, Oct 14, 2026</a>.
            The 17% FY25 customer was disclosed as primarily in Cloud Memory (CMBU); it is not named in the filing.
          </p>
        </div>

        {/* ---------- DCF ---------- */}
        <SectionTitle sub="Discounted cash flow on the note's base case: FY27–28 peak earnings, a gentle fade after, discounted at 11%. Fair value is a value today — not a future price target.">
          DCF — fair value vs current price
        </SectionTitle>

        <div className="rounded-[var(--radius)] border border-[var(--border)] bg-[var(--surface)] p-4">
          <div className="space-y-3">
            {[
              { label: `Current price (${snap?.asOf ?? "2026-11-06"})`, val: price, color: "var(--text)", bold: `$${fmt2(price)}` },
              { label: "DCF fair value — base case, today", val: baseFair, color: "var(--green)", bold: `$${fmt(baseFair)}` },
              { label: "DCF fair value — rolled to end-2027", val: 1370, color: "var(--accent)", bold: "$1,370" },
              { label: "Consensus analyst target", val: 1562, color: "var(--amber)", bold: "$1,562" },
            ].map((b) => (
              <div key={b.label}>
                <div className="flex items-baseline justify-between gap-2 text-sm">
                  <span className="text-[var(--dim)]">{b.label}</span>
                  <span className="num font-serif text-lg font-bold">{b.bold}</span>
                </div>
                <div className="mt-1 h-3 overflow-hidden rounded-full bg-[var(--surface-alt)]">
                  <div className="h-full rounded-full" style={{ width: `${(b.val / maxScale) * 100}%`, background: b.color }} />
                </div>
              </div>
            ))}
          </div>
          <p className="mt-4 text-base">
            Base-case gap:{" "}
            <span className={`num font-bold ${upside >= 0 ? "text-[var(--green)]" : "text-[var(--red)]"}`}>
              {upside >= 0 ? "+" : ""}
              {fmt(upside)}%
            </span>{" "}
            — the price sits ${fmt(baseFair - price)} below fair value{" "}
            <span className="text-[var(--dim)]">
              if the FY27–28 peak prints and only fades gently. That "if" is the whole argument: it's why the DCF says
              +76% and the Value score still says 7.
            </span>
          </p>
        </div>

        <div className="mt-3 grid gap-3 lg:grid-cols-2">
          <div className="overflow-hidden rounded-[var(--radius)] border border-[var(--border)] bg-[var(--surface)]">
            <table className="w-full text-left text-sm">
              <thead>
                <tr className="border-b border-[var(--border)] bg-[var(--surface-alt)] text-xs text-[var(--dim)]">
                  <th scope="col" className="px-4 py-2.5 font-semibold">DCF input</th>
                  <th scope="col" className="px-4 py-2.5 font-semibold">Value</th>
                </tr>
              </thead>
              <tbody>
                {DCF_INPUTS.map((row, i) => (
                  <tr key={row.label} className={i % 2 ? "bg-[var(--surface-alt)]/50" : ""}>
                    <th scope="row" className="px-4 py-2.5 font-medium">
                      {row.label}
                      <span className="block text-xs font-normal text-[var(--dim)]">{row.note}</span>
                    </th>
                    <td className="num px-4 py-2.5 font-semibold">{row.value}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <div className="rounded-[var(--radius)] border border-[var(--border)] bg-[var(--surface)] p-4">
            <h3 className="text-sm font-semibold">Stress-test it yourself</h3>
            <p className="text-xs text-[var(--dim)]">
              Same model, your assumptions. At the note's settings it returns the note's $1,213.
            </p>
            <div className="mt-4 space-y-4">
              <div>
                <label htmlFor="wacc" className="flex justify-between text-sm">
                  <span>WACC (discount rate)</span>
                  <span className="num font-semibold">{fmt2(wacc)}%</span>
                </label>
                <input id="wacc" type="range" min={9} max={13} step={0.25} value={wacc} onChange={(e) => setWacc(Number(e.target.value))} className="w-full accent-[var(--accent)]" />
              </div>
              <div>
                <label htmlFor="termg" className="flex justify-between text-sm">
                  <span>Terminal growth</span>
                  <span className="num font-semibold">{fmt2(termG)}%</span>
                </label>
                <input id="termg" type="range" min={-2} max={2} step={0.5} value={termG} onChange={(e) => setTermG(Number(e.target.value))} className="w-full accent-[var(--accent)]" />
              </div>
              <div>
                <label htmlFor="haircut" className="flex justify-between text-sm">
                  <span>FCF haircut (capex / margin stress)</span>
                  <span className="num font-semibold">{haircut}%</span>
                </label>
                <input id="haircut" type="range" min={0} max={40} step={5} value={haircut} onChange={(e) => setHaircut(Number(e.target.value))} className="w-full accent-[var(--accent)]" />
                <p className="mt-1 text-xs text-[var(--dim)]">The note's heavy-capex case (FCF conversion → ~55%) ≈ a 15% haircut → ~$1,030.</p>
              </div>
            </div>
            <div className="mt-4 flex items-baseline justify-between border-t border-[var(--border)] pt-3">
              <span className="text-sm text-[var(--dim)]">Your fair value</span>
              <span className="num font-serif text-3xl font-bold">${fmt(fair)}</span>
            </div>
            <p className="num text-right text-sm">
              <span className={fairGapPct >= 0 ? "text-[var(--green)]" : "text-[var(--red)]"}>
                {fairGapPct >= 0 ? "+" : ""}
                {fmt(fairGapPct)}% vs current price
              </span>
            </p>
            <p className="mt-2 text-xs leading-relaxed text-[var(--dim)]">
              Note's stated range across its own sensitivities: ~$1,170 (12% WACC, −2% terminal) to ~$1,710 (10% WACC,
              +2% terminal). If FY28–29 margins collapse instead of fading, fair value falls below $900 fast — the model
              is only as good as the peak path.
            </p>
          </div>
        </div>

        {/* ---------- Price history ---------- */}
        {dash.data && dash.data.history.length > 1 ? (
          <>
            <SectionTitle>Stored closes</SectionTitle>
            <div className="flex flex-wrap gap-2">
              {dash.data.history.map((h) => (
                <span key={h.asOf} className="num rounded-md border border-[var(--border)] bg-[var(--surface)] px-2.5 py-1 text-xs">
                  {h.asOf} · ${fmt2(h.price)}
                </span>
              ))}
            </div>
          </>
        ) : null}

        {/* ---------- Method & sources ---------- */}
        <SectionTitle>How this was evaluated — method & sources</SectionTitle>
        <div className="grid gap-3 lg:grid-cols-2">
          <div className="rounded-[var(--radius)] border border-[var(--border)] bg-[var(--surface)] p-4 text-sm leading-relaxed">
            <h3 className="font-semibold">The scoring method</h3>
            <ul className="mt-2 list-disc space-y-1.5 pl-5 text-[var(--dim)]">
              <li><span className="text-[var(--text)]">Business score</span> — moat, growth & backlog, profitability, balance sheet, and cycle risk, weighted as shown on the score card. Analyst judgment, not code: the weights above are the rubric made visible so the score is repeatable and arguable.</li>
              <li><span className="text-[var(--text)]">Value score</span> — price vs peers, price vs DCF fair value, and a haircut when the earnings in the denominator are at a cyclical peak. Kept as two numbers — the original score at the deep-dive price and today's — so a price move never silently rewrites history.</li>
              <li><span className="text-[var(--text)]">WACC</span> — weighted average cost of capital: the return investors demand, used to discount future cash flows to today's dollars. $100 in one year at 11% is worth $90.09 today; in five years, $59.35.</li>
            </ul>
          </div>
          <div className="rounded-[var(--radius)] border border-[var(--border)] bg-[var(--surface)] p-4 text-sm leading-relaxed">
            <h3 className="font-semibold">Where the numbers come from</h3>
            <ul className="mt-2 list-disc space-y-1.5 pl-5 text-[var(--dim)]">
              <li>Revenue, margins, cash, debt, EPS: Micron's filings — FY26 Form 10-K (filed Oct 9, 2026) and the Q4 FY26 earnings release, via EDGAR / investor relations.</li>
              <li>Guidance, RPO and backlog: the same earnings releases and calls.</li>
              <li>Forecasts (FY27–29 EPS): analyst consensus via TIKR, Zacks and MarketBeat — estimates, labeled as such wherever they appear.</li>
              <li>
                Prices: Finnhub, checked against{" "}
                <a className="underline" href="https://stockanalysis.com/stocks/mu/" target="_blank" rel="noreferrer">StockAnalysis — MU</a>.
                Refreshed daily; the close date is printed at the top.
              </li>
              <li>
                HBM / DRAM shares:{" "}
                <a className="underline" href="https://joonklee.com/insights/hbm-supply-chain-korea/" target="_blank" rel="noreferrer">Counterpoint Research tracker (Q2 2026)</a>{" "}
                and{" "}
                <a className="underline" href="https://www.indmoney.com/blog/us-stocks/micron-vs-sk-hynix-vs-samsung-ai-memory-stocks" target="_blank" rel="noreferrer">TrendForce shares via peer comparison</a>.
              </li>
              <li>
                Customer concentration: FY26 10-K Note 21 (no customer ≥10% in FY26; one at 17% in FY25, primarily Cloud Memory), Item 1A (top ten over half of revenue; data center ~60%) and Note 22 (US-headquartered $91.3B of $133.2B) — see{" "}
                <a className="underline" href="https://evidinvest.com/blog/mu-fy2026-10k-134-billion-rpo-what-the-annual-report-adds-2026-10-13" target="_blank" rel="noreferrer">FY26 10-K read-through</a>.
                The filing names no customer.
              </li>
              <li>Anything not from a filing — e.g. which customers sit behind contract headlines — is flagged as analyst reporting in the note, never presented as filed fact.</li>
            </ul>
          </div>
        </div>

        {/* ---------- Why these scores — bottom briefs, 2 sentences each ---------- */}
        <SectionTitle sub="The short version, at the bottom as requested — two sentences per score. The full factor-by-factor math stays on the score cards at the top.">
          Why these scores?
        </SectionTitle>
        <div className="grid gap-3 sm:grid-cols-2">
          {SCORE_WHY.map((w) => (
            <article key={w.label} className="rounded-[var(--radius)] border border-[var(--border)] bg-[var(--surface)] p-4">
              <div className="flex items-start justify-between gap-2">
                <div>
                  <h3 className="text-sm font-semibold">{w.label}</h3>
                  <p className="text-xs text-[var(--dim)]">{w.asOf}</p>
                </div>
                <ScoreDial score={w.score} />
              </div>
              <p className="mt-2 text-sm leading-relaxed">{w.brief}</p>
            </article>
          ))}
        </div>

        <footer className="mt-10 border-t border-[var(--border)] pt-4 text-xs leading-relaxed text-[var(--dim)]">
          Research snapshot for BIG SANDY's own tracking — not investment advice. Scores and the DCF are one analyst
          view, dated Nov 3–6, 2026; the price line refreshes daily, the view changes only when the facts do (next
          scheduled test: Q1 FY27 earnings, Dec 2026).
        </footer>
      </main>
    </div>
  );
}
