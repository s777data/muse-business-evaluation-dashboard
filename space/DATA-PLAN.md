# Data Plan

## Context provenance
- `Business score 7/10, value score 7/10 for MU` (prior MU DD 2026-11-03 delivered to BIG SANDY; preserved as original scores)
- `keep original value score unchanged and add a separate today's value score column` (prior user preference 2026-11-07; applied here as Original Value vs Today's Value)
- `DCF fair value today ~$1,213, end-2027 ~$1,370, WACC 11%, terminal 0%, FY27E EPS $176.50, FY28E $207.76, FY29E $203.33, FCF/sh base $52.17` (prior DCF delivered 2026-11-06; reused verbatim)
- `FY26 rev $133.2B +256%, GAAP net $85.0B / EPS $74.33, adj EPS $75.52, OCF $89.7B, capex $30.7B, FCF $59.0B, cash+investments $73.45B vs debt $5.18B` (prior DD from 10-K filed Oct 9, 2026)
- `Sources are filings/EDGAR for hard numbers, consensus TIKR/Zacks/MarketBeat for forecasts, Finnhub checked vs StockAnalysis for price` (assistant explanation accepted in parent conversation)

## Tested sources
### Finnhub via browser.search finance vertical + StockAnalysis
**Used by**: getDashboard (seed), refreshDaily
**Test command**: browser.search finance 'Micron MU stock price market cap November 2026'
**Sample output**: Current Price $690.00 close 2026-11-06 +5.12%, prev $656.39, 52w high $1,255 low $192.59, beta 2.43, EPS TTM $74.34, market cap $780.68B (StockAnalysis), consensus target $1,561.91 +126%
**Processing**: price snapshot stored once; daily refresh updates price, change, market cap, multiples derived from price (P/E, P/S, EV/S) and recomputes DCF upside %; fundamentals stay from filings until new filing.

### Micron FY26 10-K summaries (EDGAR filing Oct 9, 2026)
**Used by**: getDashboard seed
**Test command**: browser.search 'Micron MU FY26 revenue EPS gross margin 10-K 2026'
**Sample output**: Revenue $133.19B +256.3%, gross margin 80.7% FY (Q4 87%), net $84.97B, diluted EPS $74.33, Q4 rev $54.23B, Q1 FY27 guide rev $61.5B ±$1.5B adj EPS $38.15 ±$1
**Processing**: shown as KPI table with FY vs Q4 labeled; no invented figures.

### Peer / HBM share — Counterpoint & TrendForce via search
**Used by**: peer section
**Test command**: browser.search 'SK hynix Samsung Micron HBM market share 2026'
**Sample output**: HBM Q2 2026 revenue share SK hynix 50%, Samsung 33%, Micron 18% (Counterpoint Sep 1, 2026); DRAM share Samsung 39.4%, SK hynix 24.9%, Micron 23.3% (TrendForce)
**Processing**: peer table uses only these shares + valuation context already in DD (SK hynix forward P/E ~4.2x vs MU ~4-5.9x depending provider/date, labeled with date).

### Customer concentration & moat replicability — added 2026-11-06 edit
**Used by**: Moat section (valid vs easily replicated) + customer concentration panel + bottom "Why these scores?" briefs
**Test command**: browser.search 'Micron 10-K customer concentration largest customer percent revenue 2025 2026'; 'Micron moat replicable HBM barrier entry cost fab'
**Sample output**: FY26 10-K (filed Oct 9, 2026, accession 0000723125-26-000023, year ended Sep 3, 2026): Note 21 "No customer accounted for 10% or more of total revenue in 2026" — vs one customer at 17% in FY25 (primarily CMBU) and 10% in FY24; Item 1A "over half" of revenue from top ten customers, ~60% from data-center end market; Note 22 US-headquartered $91,323M of $133,188M (68.6%), Taiwan $16,950M. Filing names no customer. Moat barriers: ~$15–20B per leading fab, 3–5 years to build, EUV/TSV, patent thicket; product underneath HBM is fungible commodity with low switching costs.
**Source URLs (verbatim)**:
- https://evidinvest.com/blog/mu-fy2026-10k-134-billion-rpo-what-the-annual-report-adds-2026-10-13
- https://www.fool.com/investing/2026/10/14/no-single-customer-accounts-for-10-of-micron-s-sales-anymore-and-that-lowers-one-risk-in-the-stock/
**Processing**: Shown only as disclosed in the filing (single <10%, top-10 >50%, data-center ~60%, US HQ 68.6%); no customer names invented. Moat verdict split into newcomer barrier (not replicable) vs existing-rival replication (replicable/contested). Bottom score briefs kept to 2 sentences each per user request (MEMORY.md preference recorded 2026-11-06).

## Web-search sources
### MU price refresh (daily)
**Delivered by**: refreshDaily action via ctx.agent.spawnTask re-running Finnhub/StockAnalysis search
**Checked with**: finance vertical search above returned structured Finnhub entity with daily candles and current price.

## Long-term data behavior
- **Refresh policy**: Daily cron (per refresh_triggers) calls refreshDaily — updates price snapshot, derived multiples, DCF upside; scores/fundamentals unchanged unless a new filing/earnings changes them (shown with as-of dates).
- **Growth**: Single-ticker dashboard; snapshot history table appends one row per refresh for price trend sparkline.
- **Ordering**: KPIs by importance (price, market cap, revenue, EPS, margins, FCF, net cash); peers by HBM share desc.
- **Time semantics**: All prices America/New_York close; fundamentals fiscal year ended Sep 3, 2026; as-of labels in America/Los_Angeles display.

## Imagery
Imagery not needed: data dashboard — charts, gauges and tables carry the content; no photographic subject requested.

## Rejected approaches
- **Tried**: Live browser price scraping at page open
  **Why rejected**: Artifact must use stored snapshot + scheduled server refresh, not client-side scraping; keeps provenance and as-of honest.
- **Tried**: Single value score only
  **Why rejected**: User explicitly wants original score preserved alongside today's score.
