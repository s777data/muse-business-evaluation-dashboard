import { integer, real, sqliteTable, text } from "drizzle-orm/sqlite-core";

// One row per daily price snapshot (close). Fundamentals, scores and the DCF
// live in the analyst note (actions.ts constants) and only change with a new
// filing; the snapshot is what the daily refresh updates.
export const snapshots = sqliteTable("snapshots", {
  id: integer("id").primaryKey({ autoIncrement: true }),
  asOf: text("as_of").notNull(), // trading date, YYYY-MM-DD
  price: real("price").notNull(),
  changePct: real("change_pct"),
  marketCapB: real("market_cap_b"),
  source: text("source").notNull().default("Finnhub / StockAnalysis"),
  createdAt: integer("created_at", { mode: "timestamp_ms" })
    .notNull()
    .$defaultFn(() => new Date()),
});
