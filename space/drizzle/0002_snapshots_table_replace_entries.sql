DROP TABLE entries;
--> statement-breakpoint
CREATE TABLE snapshots (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  as_of TEXT NOT NULL,
  price REAL NOT NULL,
  change_pct REAL,
  market_cap_b REAL,
  source TEXT NOT NULL DEFAULT 'Finnhub / StockAnalysis',
  created_at INTEGER NOT NULL
);
