# SPEC — Engine V5.4: Missing-Leg Policy (Business missing = 0; Value unprofitable legs = 0, optional)

**Status: APPROVED by BIG SANDY 2026-11-11 — repo engine + wheel vendor
BUILT and verified 2026-11-11 (133/133 repo tests; ANGX / MU / CRDO /
OSCR live-verified). Space TS port NOT yet done — the deployed Space
still runs the pre-V5.4 renormalise behaviour and is out of date on
this policy until ported.**
Date: 2026-11-11 · Supersedes: the pre-V5.4 blanket renormalise rule
for Business, and for two Value legs under the condition below.
Scope: repo engine (`src/evaluator/`, `config/scoring.yaml`) + wheel
vendor copy (`vendor/muse-business-evaluation-dashboard`).
Out of scope: Moat missing behaviour (still renormalises, coverage
disclosed), DCF construction, anchor points, weights.

---

## 1. Problem (verified 2026-11-11, ANGX)

Under the old rule every missing sub-score was excluded and the
remaining weights renormalised. For ANGX that meant:

- **Value 8.08/10** was computed from only 0.45 of the Value weight
  (P/S, EV/S, discount-from-high, relative EV/S). Trailing P/E, FCF
  yield, DCF upside and forward buyback yield were all missing and
  renormalised away — an unprofitable name escaped the very legs that
  measure profitability-linked cheapness.
- **Business 5.58/10** was renormalised over 0.85 of weight; the one
  missing leg (FCF margin median 5y, weight 0.15) cost it nothing.

BIG SANDY's ruling, in his words: for Value, trailing P/E and FCF
yield missing because the name is not profitable should score 0 —
"just keep this as optional"; DCF upside and forward buyback yield
stay excluded/renormalised. "For Business score none is optional.
If missing, value is 0" — built WITH the two exceptions in §3 that
were flagged to him before the build and approved by him.

## 2. The policy (config: `missing_policy` in `config/scoring.yaml`)

### Business — `missing_as_zero: true`
Every missing Business sub-score scores **0 at its full weight**.
`weights_renormalised_over` is therefore 1.0 unless an exception leg
is missing. A name with all Business legs missing scores 0.0, not
None. Zero-scored legs remain in `missing_metrics` and are also
listed in `data_quality.zero_scored_missing_business_metrics`; their
sub-score entries carry `value: None, score: 0.0,
zero_scored_missing: True`.

### Value — `unprofitable_missing_as_zero` (optional, `enabled: true`)
Only `trailing_pe` and `fcf_yield_latest_pct`, and only when the
filer is unprofitable — tested from filed data alone
(`scoring.is_unprofitable`): the negative-P/E guard fired
(`negative_trailing_pe_not_scored`), or the EPS used for the
trailing P/E is ≤ 0, or filed TTM / latest net income is ≤ 0.
Those legs score 0 at full weight when missing. `dcf_upside_pct`
and `forward_buyback_yield_pct` are NOT in the list and still
renormalise. Setting `enabled: false` restores the pre-V5.4
behaviour for these legs. A profitable filer with a missing leg
keeps the renormalise treatment.

### Moat — unchanged
Missing legs renormalise; coverage disclosed as before.

## 3. The two Business exceptions (approved by BIG SANDY)

Decided from the recorded missing reasons
(`scoring.business_zero_exclusions`), never guessed:

1. **Interest coverage, no interest expense.**
   `interest_coverage_ttm` missing with
   `interest_coverage_missing_reason =
   interest_expense_ttm_missing_or_nonpositive` renormalises — a
   filer with no interest expense has nothing to cover. Coverage
   missing for any other reason (e.g. operating income missing)
   scores 0.
2. **Debt legs, debt not determinable.**
   `net_cash_to_revenue` and `leverage_debt_to_revenue_ttm` missing
   with `debt_not_determinable_fail_closed` renormalise —
   fail-closed stays fail-closed: the legs are excluded and the
   failure stays flagged, instead of converting an extraction
   failure into a 0. No debt figure is ever invented (the V2 rule is
   untouched — zero here is a policy *score* for a missing leg in the
   non-exception cases, never a debt *value*).

Applied exceptions are disclosed per run in
`data_quality.missing_policy_business_exceptions_applied`.

## 4. Verification (2026-11-11, live engine runs)

| Ticker | Check | Result |
|---|---|---|
| ANGX | both policies | Business 55.8 → **47.4** (FCF-margin leg zeroed), Value 80.8 → **48.5** (P/E + FCF yield zeroed; DCF + buyback still excluded), GAV 87.9 → **71.8**, Final 70.1 → **53.8** |
| MU | control, no missing legs | Business **6.83** unchanged; zero-scored lists empty |
| CRDO | exception 1 | Coverage missing (no interest expense) renormalised; Business **68.7** unchanged |
| OSCR | exception 2 | Raw output: `weights_renormalised_over` 0.825 (= 1 − 0.10 − 0.075), other 4 missing legs zero-scored, exception recorded |

Tests: repo suite 123 → **133** (10 new in `TestV54MissingPolicy`,
covering the zero rule, the default renormalise path, both
exceptions, the OI-missing non-exception, the Value unprofitable /
profitable split, and the config shape). Wheel D-17 Phase 5 script:
0 failures after the vendor sync; methodology version bumped to
`dashboard-v5.4+gav-code`.

## 5. Known follow-on

- The dashboard **Space TS port** does not implement V5.4 yet; its
  scores for names with missing legs will read higher than the repo
  / wheel engine until ported (same disclosure pattern as earlier
  port lags).
- Existing heatmap / Phase 5 run files are historical outputs and
  are NOT restated; V5.4 applies to new runs.
