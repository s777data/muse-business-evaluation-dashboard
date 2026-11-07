# Muse Business Evaluation Dashboard — v1 engine

A deterministic, code-only stock evaluation engine. **No LLM / Muse model is
called anywhere.** Run the code twice on the same filed data and you get the
same scores twice. Every weight, threshold, anchor point, peer, ticker and
DCF assumption lives in `config/*.yaml` — scoring code contains only
arithmetic (piecewise-linear interpolation, weighted means, CAPM, Gordon
terminal value), so changing the method means editing config, and the change
is visible and reviewable.

## Run it

```bash
pip install -r requirements.txt   # pyyaml only; everything else is stdlib
python -m src.evaluator.run MU    # writes output/scores.json, prints a summary
python -m unittest discover -s tests -v
```

Open `dashboard/index.html` (served over http, e.g. `python -m http.server`
from the project root) — it renders `output/scores.json`: score cards, KPI
table, valuation table, peer section, moat replicability checks, filing-based
customer concentration with verbatim quotes, a DCF-vs-price bar, and the
bottom "why these scores" explanations. This static dashboard is the code
that mirrors the Muse Space dashboard.

## Data sources (free only)

| Input | Source |
|---|---|
| Revenue, margins, OCF, capex, cash, investments, debt, shares, EPS, R&D | SEC EDGAR XBRL companyfacts (`data.sec.gov`), 10-K/FY rows, latest filing wins per fiscal year |
| Customer concentration | Latest 10-K full text from SEC Archives, rule-based sentence extraction; the exact sentence is stored as evidence in `scores.json` |
| Price, 52-week high/low | Free feeds in config order (`config/sources.yaml`). In v1 testing, Stooq returned an empty body from this network, so the Yahoo chart API is tried first. If **no** feed returns data, the run **fails closed** with a clear error — no price is ever invented |
| Beta, risk-free rate, ERP | **Config inputs** (`config/tickers.yaml`, `config/dcf.yaml`), disclosed in `data_quality`. No free keyless source covers them in v1 |

SEC fair access: the User-Agent in `config/sources.yaml` carries an
email-style contact, which `www.sec.gov` requires (a bare descriptive UA is
served an "undeclared automated tool" page). Responses are disk-cached for
12h under `output/cache/`.

## Formulas

- **Sub-scores (business / value / moat):** each metric is mapped to 0–10 by
  piecewise-linear interpolation between the anchor points in
  `config/scoring.yaml`, then combined by the config weights (weights
  renormalise over present metrics; exclusions are listed in `data_quality`).
- **Through-cycle rule:** margin *levels* are scored on the **5-year median**,
  not the latest year, so a cyclical peak year cannot max a score. Margin
  *stability* (5-year gross-margin stdev) is its own sub-score.
- **DCF:** FCF/share base = latest filed FY free cash flow (OCF − capex) /
  diluted shares. Growth = filed 5-year revenue CAGR clamped to the config
  floor/cap and faded linearly to terminal growth over the projection.
  WACC is **built**: cost of equity = risk-free + beta × ERP (CAPM); WACC
  blends that with after-tax cost of debt at market weights. Fair value =
  PV of projected FCF + Gordon terminal value. Output includes fair value,
  dollar distance and % upside vs the current price.
- **Peers:** percentile rank of the subject vs the config peer set on
  revenue, gross margin, FCF margin and price/sales, all computed from the
  same EDGAR + price pipeline.
- **Concentration score:** rule table in `config/scoring.yaml` applied to
  filing facts (base score if no single customer ≥ the threshold, penalties
  for top-10 share above the config allowance, etc.).
- **Explanations:** template sentences filled with the actual sub-scores —
  the top drivers and main drags the formula produced. No generated prose.

## How this differs from the earlier analyst-judgment scores

The MU scores previously shown in chat (Business 7 / Value 7 / Moat 6) were
analyst judgment: the same evidence weighed in a person's head, including
things no formula sees (management commentary tone, deal quality, whether a
margin *looks* durable). This engine sees only what its config says it sees,
weights it exactly as config says, and cannot make exceptions. Where the two
disagree, the engine's number is the reproducible one and the sub-scores show
exactly why; the judgment number may still be the better-informed one. Both
are shown their workings; neither is hidden inside a model call.

## Known v1 limits

- **No paid consensus.** Forward EPS estimates are never used. Where a
  judgment process would use consensus (forward P/E, DCF growth), the engine
  uses filed actuals + config scenarios, and `data_quality` flags consensus
  as *missing by design*.
- **US peers only.** SK hynix and Samsung — MU's closest memory rivals — are
  excluded because they are not SEC EDGAR XBRL filers and no free keyless
  source covers them. Peer percentiles are therefore against US-listed semi
  and storage names listed in `config/peers.yaml`, each with its rationale.
- **Beta / risk-free / ERP are config inputs**, updated by hand in config.
- **Switching costs are not evaluated** (no structured free source); the moat
  section flags this instead of proxying it silently.
- One ticker (MU) is wired in `config/tickers.yaml`; adding a ticker is a
  config entry (name, CIK, beta), not a code change.
