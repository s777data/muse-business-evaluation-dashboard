# SPEC — Engine V5.2: Normalized-CapEx DCF Base, Fade Floor, Terminal Growth

**Status: FOR REVIEW — not approved, not built.**
Date: 2026-11-08 · Supersedes: nothing (V5.1 remains live until this ships)
Scope: repo engine (`src/evaluator/`) + reruns + comparison heatmap page.
Out of scope: dashboard Space TS port (separate follow-on), WACC construction,
scoring weights, any per-name overrides, any imported third-party figures.

---

## 1. Problem (verified 2026-11-08)

The V5 DCF capitalizes **actual TTM owner FCF** (OCF − actual capex − SBC) with a
growth fade to a **0% terminal growth**. At peak AI capex this produces stress
artifacts, not valuations:

| Ticker | TTM OCF | Actual capex | SBC | Actual base | V5 DCF | Price (Nov 6) |
|---|---|---|---|---|---|---|
| META | $133.09B | $104.44B (43.2% rev) | $26.94B | **$1.71B** | **$9.43** | $610.15 |
| GOOGL | $179.55B | $159.80B (33.9% rev) | $30.41B | **−$10.66B** | none (refused) | $284.78 |
| MSFT | $195.88B | $135.92B (39.2% rev) | $12.56B | $47.40B | $96.03 | $530.61 |

Three distinct defects:
1. **Trough base** — the base year is the most capex-distorted year on record;
   growing it mechanically assumes peak capex intensity forever.
2. **Fade to zero** — FCF growth fades from the filed revenue CAGR to 0% in
   five years, inconsistent with any mid-cycle base.
3. **Terminal growth 0%** — a perpetual slow-liquidation assumption.

Gemini's alternative (asserted "normalized" FCF of $65B/$88B/$95B) was audited
and rejected as an input source: its stated construction rule (D&A + ~6.5% of
revenue) does not reproduce its own numbers (MSFT off by ~$23B), its MSFT D&A
is $4.2B above the filed figure, and its META cross-check misses by ~$2B.
The *concept* (maintenance-capex normalization) is sound; the execution is
hand-fitted. V5.2 implements the concept as one uniform, filing-derived rule.

## 2. Change 1 — Normalized capex and DCF base

**Rule (all inputs from EDGAR filings, computed per ticker, no constants
fitted per name):**

- `capex_intensity_y` = annual capex ÷ annual revenue, for each of the last
  5 fiscal years with both values filed.
- `median_intensity` = median of those ratios.
- `normalized_capex` = `min(actual TTM capex, median_intensity × TTM revenue)`.
  The cap makes the rule one-directional: it only trims expansion spikes; it
  never raises capex above actual spend for asset-light names.
- `fcf_base_normalized` = TTM OCF − normalized capex − TTM SBC.
- `fcf_base_actual` = TTM OCF − actual TTM capex − TTM SBC (unchanged from V5).

**Primary DCF base = `fcf_base_normalized`.** The actual-base DCF is still
computed and reported alongside as the labeled **stress floor**.

**Fail-closed behaviour:**
- Fewer than **3** annual intensity observations → normalization unavailable:
  primary base = actual base, flag `capex_normalization_unavailable`.
- Normalized base ≤ 0 → DCF missing, reason `non_positive_fcf_base` (same
  guard as today). Actual base ≤ 0 with normalized base > 0 → DCF proceeds on
  the normalized base; stress floor reported as missing.
- Normalization binds (normalized < actual) → flag `capex_normalized` with the
  median intensity and the $ amount trimmed, in `data_quality` and run output.

**Worked values (verified from filings):**

| Ticker | Filed intensities (5 FYs) | Median | Normalized capex | Normalized base | Base/share |
|---|---|---|---|---|---|
| META | 15.8 / 26.7 / 20.0 / 22.6 / 34.7% | 22.6% | $54.72B | $51.43B | $19.99 |
| GOOGL | 12.2 / 9.6 / 11.1 / 10.5 / 15.0% | 11.1% | $52.44B | $96.70B | $7.82 |
| MSFT | 12.0 / 13.3 / 18.1 / 22.9 / 34.9% | 18.1% | $62.86B | $120.46B | $16.18 |

Note the rule is not a "raise the base" machine: it lands below Gemini for
META (Meta's own median intensity exceeds Gemini's fitted 17%) and above
Gemini for GOOGL and MSFT.

## 3. Change 2 — Growth fade floor

- `g0` = filed 5-yr revenue CAGR, clamped to [0%, 25%] (unchanged).
- Fade target = **15%** if `g0 > 15%`, else the terminal growth rate (Change 3).
  (Decision Point 1 — see §8.)
- Path: linear from `g0` to the fade target over the 5 projection years, same
  step construction as V5 (`step = (g0 − target) / 5`).

## 4. Change 3 — Terminal growth

- `terminal_growth_pct`: **0% → 3%** in `config/dcf.yaml`, used in the Gordon
  terminal value. 3% sits below every WACC the engine produces (observed
  range ~7–14%), so the terminal formula stays well-defined for all names.

## 5. Expected outputs under the full spec (engine formula, pre-build)

| Ticker | Base used | Growth path | WACC | **V5.2 DCF** | V5.1 DCF | Gemini DCF | Price |
|---|---|---|---|---|---|---|---|
| META | $19.99/sh (norm) | 18.51 → 15.70 | 11.15% | **$446.36** | $9.43 | $931.05 | $610.15 |
| GOOGL | $7.82/sh (norm) | 20.01 → 16.00 | 9.93% | **$214.95** | none | $269.06 | $284.78 |
| MSFT | $16.18/sh (norm) | 14.57 → 5.31 | 9.63% | **$339.64** | $96.03 | $470.76 | $530.61 |
| NVDA | $4.93/sh (actual) | 25 → 17 | 11.42% | **$123.76** | $79.53 | $235.00 | $186.64 |
| MU | $50.42/sh (actual) | 25 → 17 | 13.59% | **$987.28** | $674.87 | $835.08 | $690.00 |

META stress floor under the new fade/terminal (actual base): $14.81.
The engine remains the strictest of the four scorers on DCF — by design.
This spec does not tune toward any verdict.

## 6. Scoring impact

- The Value score's `dcf_upside_pct` leg uses the **normalized-base** DCF
  upside vs price. All other Value legs unchanged.
- GOOGL (and any name whose actual base is ≤ 0 but normalized base > 0) gains
  a DCF leg it previously lacked; its Value score renormalisation changes
  accordingly. This is intended and will be visible in the before/after diff.
- Business and Moat scores: untouched.

## 7. Tests and rollout

**New regression tests** (in addition to the existing 58):
1. Normalization binds: META-shaped inputs → normalized base used, flag set,
   trim amount correct.
2. Cap holds: asset-light inputs (actual capex < median-intensity capex) →
   actual base used, no flag.
3. History < 3 years → actual base + `capex_normalization_unavailable`.
4. Normalized base ≤ 0 → DCF missing with `non_positive_fcf_base`.
5. Fade path: g0 = 18.51 → target 15 path values; g0 = 10 → fades to 3.
6. Terminal value with g∞ = 3% against a hand-computed Gordon value.
Existing tests that assert V5 DCF behaviour (fade-to-0, terminal 0, actual
base) are updated deliberately and **listed by name in the build report** —
no silent test edits.

**Rollout:**
1. Implement in repo; full suite green.
2. Sync to comparison working copy; rerun the full comparison set (30 names)
   plus all configured names.
3. Publish a **before/after diff table** (Business / Value / DCF, old vs new,
   per ticker) for review before the page changes.
4. Update the Score Comparison Heatmap page from the diffed data. This rerun
   also resolves the known TSLA inconsistency: the page currently shows
   −$4.49 from the Nov 7 batch, which capitalized a negative base; under the
   current guard TSLA's DCF is missing (`non_positive_fcf_base`) and its
   Value score renormalises.
5. No GitHub push (push path remains blocked; working tree only).

## 8. Decision points for BIG SANDY

1. **Fade target when g0 ≤ 15%** — specced as "fade to terminal growth (3%)".
   Alternative: hold flat at g0. (MSFT is the live example: 14.57 → 5.31
   specced.)
2. **Minimum history** — specced at ≥3 of the last 5 fiscal years for the
   median; stricter (require all 5) is available.
3. **Value leg** — specced as normalized-base DCF only (actual base shown as
   stress floor, not scored). Alternative: score the average of the two.
4. **Terminal growth** — specced at 3.0%; 2.5% is the more conservative
   variant.

## 9. Known limitations (accepted, stated)

- The 5-yr median window already contains surge years for early movers
  (META's median is 22.6%, not a pre-surge ~17%) — the rule measures each
  company's own mid-cycle, surge included. Accepted: it is uniform and
  reproducible.
- Cyclicals (e.g., MU): the cap binds at actual capex, so a peak-cycle
  earnings base remains peak — normalization targets capex *spikes*, not
  cycle position. Out of scope for V5.2.
- D&A plays no role in this design; the median-intensity rule replaces the
  D&A-based formulation entirely (one mechanism, no blended constants).
