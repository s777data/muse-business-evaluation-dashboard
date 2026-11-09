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
