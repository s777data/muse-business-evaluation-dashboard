// MU Mini Dashboard actions.
//
// The price snapshot is stored state: seeded once with the Nov 6, 2026 close
// ($690, from the MU deep-dive update that day; Finnhub checked against
// StockAnalysis) and updated by the daily refresh. The analyst note itself
// (scores, KPIs, valuation, peers, DCF) is fixed editorial content from the
// FY26 10-K deep-dive and lives in the client; it only changes with a new
// filing, via an edit to this artifact.

import { defineAction, z, type ActionsModule } from "@hatch/space-sdk";
import { desc, eq } from "drizzle-orm";
import * as schema from "./schema";
import { evaluateTicker } from "./engine";

const dashboardResponse = z.object({
  snapshot: z.object({
    asOf: z.string(),
    price: z.number(),
    changePct: z.number().nullable(),
    marketCapB: z.number().nullable(),
    source: z.string(),
  }),
  history: z.array(z.object({ asOf: z.string(), price: z.number() })),
  seeded: z.boolean(),
});

type DashboardResponse = z.infer<typeof dashboardResponse>;

const seedResponse = z.object({ ok: z.literal(true), inserted: z.boolean() });
type SeedResponse = z.infer<typeof seedResponse>;

const okResponse = z.object({ ok: z.literal(true) });
type OkResponse = z.infer<typeof okResponse>;

/* ------------------------- ticker search (repo engine) ------------------------- */

const subScore = z.object({ value: z.number(), score: z.number(), weight: z.number() });
const scoredSection = z.object({
  subscores: z.record(z.string(), subScore),
  total: z.number().nullable(),
  missing: z.array(z.string()),
  renormalisedOver: z.number(),
});
const nullableNum = z.number().nullable();

const searchResponse = z.object({
  status: z.enum(["ok", "error"]),
  error: z.string().nullable(),
  result: z
    .object({
      symbol: z.string(),
      entityName: z.string().nullable(),
      cik: z.string(),
      snapshotDate: z.string(),
      price: z.object({
        source: z.string(),
        last_close: z.number(),
        last_date: z.string(),
        high_52w: z.number(),
        low_52w: z.number(),
        url: z.string(),
      }),
      beta: z.object({ value: z.number(), source: z.string() }).nullable(),
      kpis: z.object({
        history_years: z.array(z.string()),
        fiscal_year_end: z.string().nullable(),
        revenue_latest: nullableNum,
        revenue_growth_latest_pct: nullableNum,
        revenue_cagr_5y_pct: nullableNum,
        fcf_latest: nullableNum,
        gross_margin_latest_pct: nullableNum,
        gross_margin_median_5y_pct: nullableNum,
        gross_margin_stdev_5y_pts: nullableNum,
        operating_margin_median_5y_pct: nullableNum,
        fcf_margin_median_5y_pct: nullableNum,
        fcf_margin_latest_pct: nullableNum,
        cash_latest: nullableNum,
        investments_latest: nullableNum,
        total_debt_latest: nullableNum,
        net_cash_latest: nullableNum,
        net_cash_to_revenue: nullableNum,
        capex_latest: nullableNum,
        capex_intensity_latest_pct: nullableNum,
        operating_cash_flow_latest: nullableNum,
        net_income_latest: nullableNum,
        rd_expense_latest: nullableNum,
        eps_diluted_latest: nullableNum,
        diluted_shares_latest: nullableNum,
        sbc_latest: nullableNum,
        sbc_ttm: nullableNum,
        sbc_pct_of_revenue_latest: nullableNum,
        sbc_not_reported_fcf_unadjusted_not_used: z.boolean(),
        sbc_ttm_unavailable: z.boolean(),
        fcf_raw_latest: nullableNum,
        fcf_raw_ttm: nullableNum,
        dilution_share_count_growth_pct: nullableNum,
        dilution_span_years: nullableNum,
        dilution_annual_points: nullableNum,
        dilution_share_series_discontinuity: z.boolean(),
        revenue_ttm: nullableNum,
        eps_ttm: nullableNum,
        fcf_ttm: nullableNum,
        operating_income_ttm: nullableNum,
        interest_expense_ttm: nullableNum,
        interest_coverage_ttm: nullableNum,
        interest_coverage_missing_reason: z.string().nullable(),
        leverage_debt_to_revenue_ttm: nullableNum,
        total_debt_resolution: z
          .object({
            value: nullableNum,
            as_of: z.string().nullable(),
            method: z.string(),
            components: z.record(z.string(), z.unknown()),
          })
          .nullable(),
        debt_not_determinable_fail_closed: z.boolean(),
        ttm_end: z.string().nullable(),
        ttm_method: z.string().nullable(),
        diluted_shares_current: nullableNum,
        roic_median_5y_pct: nullableNum,
        market_cap: nullableNum,
        price_to_sales: nullableNum,
        ev_to_sales: nullableNum,
        trailing_pe: nullableNum,
        fcf_yield_latest_pct: nullableNum,
        discount_from_52w_high_pct: nullableNum,
      }),
      scores: z.object({ business: scoredSection, value: scoredSection, moat: scoredSection }),
      dcf: z.object({
        fcf_per_share_base: nullableNum,
        growth_path_pct: z.array(z.number()),
        wacc: z
          .object({
            cost_of_equity_pct: z.number(),
            cost_of_debt_after_tax_pct: z.number(),
            equity_weight: z.number(),
            debt_weight: z.number(),
            wacc_pct: z.number(),
          })
          .nullable(),
        wacc_inputs: z.object({
          risk_free_rate_pct: z.number(),
          equity_risk_premium_pct: z.number(),
          beta: nullableNum,
          pre_tax_cost_of_debt_pct: z.number(),
          tax_rate_pct: z.number(),
        }),
        fair_value_per_share: nullableNum,
        pv_of_projected_fcf: nullableNum,
        pv_of_terminal_value: nullableNum,
        projected_fcf_per_share: z.array(z.number()),
        current_price: z.number(),
        upside_pct: nullableNum,
        distance_dollars: nullableNum,
      }),
      concentration: z.object({
        single_customer: z
          .object({
            quote: z.string(),
            threshold_pct: z.number(),
            no_customer_at_or_above_threshold: z.boolean(),
            max_single_customer_pct: nullableNum,
          })
          .nullable(),
        top_ten: z.object({ quote: z.string(), top_ten_share_pct: nullableNum, basis: z.string() }).nullable(),
        data_center: z.object({ quote: z.string(), data_center_share_pct: z.number() }).nullable(),
        filing: z
          .object({ filed: z.string(), accession: z.string(), primary_document: z.string(), url: z.string() })
          .nullable(),
      }),
      concentrationScore: nullableNum,
      moatChecks: z.array(z.object({ check: z.string(), status: z.string(), evidence: z.string() })),
      explanations: z.object({ business: z.string(), value: z.string(), moat: z.string() }),
      dataQuality: z.array(z.string()),
      sources: z.object({
        filingUrl: z.string().nullable(),
        priceFeed: z.string(),
        scoringConfig: z.string(),
        dcfConfig: z.string(),
        peerNote: z.string(),
      }),
    })
    .nullable(),
});
type SearchResponse = z.infer<typeof searchResponse>;

export const Actions = {
  getDashboard: defineAction({
    description:
      "Returns the latest stored MU price snapshot plus recent price history for the dashboard header and DCF gap.",
    request: z.object({}),
    response: dashboardResponse,
    async handler(ctx): Promise<DashboardResponse> {
      const db = ctx.db<typeof schema>();
      const rows = await db
        .select()
        .from(schema.snapshots)
        .orderBy(desc(schema.snapshots.asOf))
        .limit(60);
      const latest = rows[0];
      if (!latest) {
        return {
          snapshot: {
            asOf: "2026-11-06",
            price: 690,
            changePct: 5.12,
            marketCapB: 780.68,
            source: "Finnhub / StockAnalysis — Nov 6, 2026 close",
          },
          history: [{ asOf: "2026-11-06", price: 690 }],
          seeded: false,
        };
      }
      return {
        snapshot: {
          asOf: latest.asOf,
          price: latest.price,
          changePct: latest.changePct ?? null,
          marketCapB: latest.marketCapB ?? null,
          source: latest.source,
        },
        history: rows
          .slice()
          .reverse()
          .map((r) => ({ asOf: r.asOf, price: r.price })),
        seeded: true,
      };
    },
  }),

  seedInitial: defineAction({
    description:
      "Seeds the initial MU price snapshot (Nov 6, 2026 close, $690) if no snapshot exists yet.",
    request: z.object({}),
    response: seedResponse,
    async handler(ctx): Promise<SeedResponse> {
      const db = ctx.db<typeof schema>();
      const existing = await db.select().from(schema.snapshots).limit(1);
      if (existing.length > 0) return { ok: true, inserted: false };
      await db.insert(schema.snapshots).values({
        asOf: "2026-11-06",
        price: 690,
        changePct: 5.12,
        marketCapB: 780.68,
        source: "Finnhub / StockAnalysis — Nov 6, 2026 close",
      });
      ctx.invalidateQueries();
      return { ok: true, inserted: true };
    },
  }),

  saveSnapshot: defineAction({
    description:
      "Saves one daily MU closing-price snapshot from the refresh task, replacing any row for the same trading date.",
    request: z.object({
      asOf: z.string().regex(/^\d{4}-\d{2}-\d{2}$/),
      price: z.number().positive(),
      changePct: z.number().nullable().optional(),
      marketCapB: z.number().positive().nullable().optional(),
      source: z.string().min(1).max(200),
    }),
    response: okResponse,
    async handler(ctx, args): Promise<OkResponse> {
      const db = ctx.db<typeof schema>();
      await db
        .delete(schema.snapshots)
        .where(eq(schema.snapshots.asOf, args.asOf));
      await db.insert(schema.snapshots).values({
        asOf: args.asOf,
        price: args.price,
        changePct: args.changePct ?? null,
        marketCapB: args.marketCapB ?? null,
        source: args.source,
      });
      ctx.invalidateQueries();
      return { ok: true };
    },
  }),

  searchTicker: defineAction({
    description:
      "Runs the repo formula engine (EDGAR filings + free price feed, config anchors, CAPM DCF, no model) for any US-listed ticker and returns its Business / Value / Moat scores, KPIs, DCF and filing-based customer concentration.",
    request: z.object({ ticker: z.string().min(1).max(12) }),
    response: searchResponse,
    async handler(_ctx, args): Promise<SearchResponse> {
      try {
        const result = await evaluateTicker(args.ticker);
        return { status: "ok", error: null, result };
      } catch (e) {
        return {
          status: "error",
          error: e instanceof Error ? e.message : "The engine run failed for that ticker.",
          result: null,
        };
      }
    },
  }),

  refreshDaily: defineAction({
    description:
      "Starts a background task that fetches the latest MU closing price and saves it as the newest snapshot.",
    request: z.object({}),
    response: z.object({ started: z.boolean() }),
    async handler(ctx) {
      try {
        await ctx.agent.spawnTask(
          "Fetch the latest closing price for Micron Technology (NASDAQ: MU). Use web search with the finance vertical (query: 'Micron MU stock price') and prefer the Finnhub result's Current Price and Price As Of date, cross-checked against StockAnalysis. Then call this artifact's saveSnapshot action exactly once with: asOf = the trading date of that close (YYYY-MM-DD), price = the closing price in USD, changePct = the day's percent change if reported (else null), marketCapB = market cap in billions of USD if reported (else null), source = 'Finnhub / StockAnalysis — <date> close'. Do not change any scores, fundamentals or DCF inputs — price only.",
          { expectsAction: "saveSnapshot" },
        );
        return { started: true };
      } catch {
        return { started: false };
      }
    },
  }),
} satisfies ActionsModule;
