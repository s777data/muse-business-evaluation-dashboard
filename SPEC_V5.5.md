# SPEC — Engine V5.5: SMCI Extraction & Business-Quality Fixes

**Status: BUILT 2026-11-11 at BIG SANDY's direction (task: "SMCI — Fix
Financial Fundamental Extraction & Business Quality Scoring Bugs").
Repo engine only; 143/143 tests (133 pre-existing + 10 new). Space TS
port NOT done — the deployed Space still runs pre-V5.5 behaviour for
these four legs until ported (it has not had the V5.4 port either).
Wheel vendor sync NOT done in this build.**
Date: 2026-11-11 · Supersedes: nothing in V5.4 — the missing-leg
policy is unchanged and governs the two points where this task's
generic wording differed (see §6).

---

## 1. Problem (reproduced 2026-11-11 on SMCI, CIK 0001375365)

Baseline from the pre-fix engine, matching the figures BIG SANDY
quoted exactly:

| Leg | Before | Filed reality |
|---|---|---|
| Total debt | $120.179M, as of **2023-06-30** (`LongTermDebt`) | $8,646.017M at 2026-09-30 |
| Leverage debt / revenue TTM | 0.0022 (displayed 0) | 0.1601 |
| Net cash / revenue | +0.1256 (quoted +0.13) | −0.0324 |
| Interest coverage TTM | 235.59x | 18.45x on gross expense |
| Dilution | missing (zero-scored under V5.4; renormalised in older paths) | +5.44%/yr |
| Gross-margin stdev | 2.708 pts, scored 8.92/10 | CV 0.197, scored 5.06/10 |

Business total: **5.34 → 4.94**, weights 1.00 in both, but after the
fix no leg is missing or zero-scored — every leg is filed data.

## 2. Fix 1 — Total Debt component-sum fallback

SMCI files NO aggregate debt tag at any recent date: its loans live
only under `DebtLongtermAndShorttermCombinedAmount` and its
convertible senior notes only under `ConvertibleLongTermNotesPayable`
— neither concept was configured, so the resolver walked back to a
stale FY2023 `LongTermDebt` row. `metrics.resolve_total_debt` gains
step **(d) component sum**, tried at each date only after grand
total, family pairs and single totals all fail there (an aggregate
already contains its components — never added to):

`Total Debt = combined borrowings + convertible notes (current +
noncurrent) + lines of credit + short-term borrowings + finance
leases`, from the component classes in `config/sources.yaml`
(`debt_component_*`). At least one borrowing class must be present;
finance leases alone do not resolve. Explicit filed zeros count as
present (0 is data); all-null stays fail-closed (value None, V5.4
Exception 2 renormalises the two debt legs). Component concepts were
also added to the verified-zero evidence scan, so a filer carrying
component-tagged debt can never be "verified zero".
`Net Cash = (Cash + Investments) − Total Debt`; both ratios over
Revenue TTM (formulas unchanged, inputs fixed).

## 3. Fix 2 — Interest coverage on GROSS interest expense

`InterestExpenseNonoperating` (SMCI's gross tag: $194.574M FY26) and
`InterestExpenseOperating` added to `interest_expense` in
`config/sources.yaml`; the freshest-series pick now selects SMCI's
gross series instead of its stale loans-only `InterestExpense` series
(TTM $19.749M → the 235.59x print). Net interest concepts are banned
from the list by comment and by test. Coverage =
`OperatingIncome_TTM / abs(GrossInterestExpense_TTM)`, raw value
**capped at 100.0** with the uncapped figure kept in
`interest_coverage_ttm_uncapped`. SMCI after: TTM gross expense
$252.182M (FY26 $194.574M + Q1 FY27 $82.539M − Q1 FY26 $24.931M) →
**18.45x** — still a 10.0 sub-score, now genuinely earned at the 15x
fortress anchor instead of manufactured by a collapsed denominator.

## 4. Fix 3 — Dilution: split adjustment + fallback chain

The annual diluted series steps 53.507M (FY21, pre-split filing) →
536.155M (FY22, filed post-split in the Feb-2025 10-K): ratio 10.02,
SMCI's 10-for-1 split. The old continuity guard failed the leg
closed on any >5x step. Now `_split_adjusted_series` restates
pre-split points by any clean integer factor 2..25 (within 3%)
before the CAGR; a non-clean break (units error, ~1000x — the ONDS
shape) still fails closed. Fallback chain after the diluted tags
(`WeightedAverageNumberOfDilutedSharesOutstanding`,
`WeightedAverageNumberOfSharesOutstandingDiluted`): class-share
filing text → basic shares (loss-makers) → `CommonStockSharesOutstanding`
point-in-time (new last-resort rung in edgar, flagged) → quarterly
YoY (Q_t vs Q_{t-4} from standalone quarters) when the annual path
has <2 usable points. Method + split events are recorded
(`dilution_method`, `dilution_split_adjustments`); when the leg
still ends missing, `dilution_data_quality_warning` records why.
SMCI after: **+5.44%/yr** (FY21 restated ×10: 535.070M → 697.348M
over 5.0 years, 6 annual points), score 3.28 — previously 0 /
escaped. Quarterly YoY cross-check: +12.98% (749.351M vs 663.235M).

## 5. Fix 4 — Gross-margin volatility normalised by level

Business now scores `gross_margin_relative_volatility` =
`stdev(GM_5yr) / max(median(GM_5yr), 5.0)` (weight 0.10, anchors in
`config/scoring.yaml`: CV 0→10, 0.05→8.5, 0.10→7, 0.20→5, 0.35→3,
0.50→1.5, 0.75→0). SMCI: 2.708 / 13.75 = **0.197 → 5.06** (was 8.92
on raw pts). The raw stdev metric is still computed and displayed,
and still feeds the Moat leg and the fungibility flag, unchanged.

## 6. Deviations from the task text (flagged, not silent)

- The task said renormalise dilution if all fallbacks fail, and
  assign coverage 10/10 when gross expense ≤ 0. Both conflict with
  the V5.4 policy BIG SANDY set on 2026-11-11 (Business missing = 0
  at full weight with only two exceptions; coverage missing for no
  interest expense renormalises — it is not awarded 10). V5.4
  governs; the renormalise-if-all-fail wording is superseded.
- Net cash uses Cash + current + noncurrent investments (the
  engine's standing definition), a superset of the task's
  Cash + ShortTermInvestments; SMCI difference is $1.59M.

## 7. Controls

- MU: Business 6.83 → 6.68, driven solely by Fix 4 (GM CV 0.739 —
  Micron's margins genuinely swing ~74% of level); debt method,
  dilution (+0.46%/yr, `annual_cagr`, no split flag) unchanged;
  coverage capped at 100 (uncapped ~179x), sub-score unchanged.
- CRDO: verified-zero debt and the interest-coverage Exception 1
  renormalise both intact; Business 6.87 → 6.84 (Fix 4 leg only).

## 8. Files changed

`config/sources.yaml`, `config/scoring.yaml`, `src/evaluator/edgar.py`,
`src/evaluator/metrics.py`, `tests/test_evaluator.py` (+1 V5.4 fixture
metric rename), this spec. No push, no Space port, no wheel vendor
sync — each on BIG SANDY's word, as before.
