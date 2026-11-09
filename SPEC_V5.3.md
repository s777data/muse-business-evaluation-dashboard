# SPEC — Engine V5.3: Forward Buyback Yield (announced / remaining authorization) in Value

**Status: APPROVED by BIG SANDY 2026-11-09 — repo engine BUILT 2026-11-09
(74/74 tests, MU + CRDO live runs verified); Space port in progress.**
Date: 2026-11-09 · Supersedes: nothing (V5.2 remains live until this ships)
Scope: repo engine (`src/evaluator/`) + Space TS port + reruns.
Out of scope: Business and Moat scoring rules, DCF construction, any per-name
overrides, any paid or third-party buyback database.

---

## 1. Problem (verified 2026-11-09)

The engine has **no buyback field at all**. A full-text search of the repo
engine and the dashboard Space engine on 2026-11-09 found zero occurrences of
"buyback" / "repurchase" as a data input — the words appear only in comments
about the dilution guard.

What exists today is only the *net result* of buybacks, inside Business:

- `dilution_share_count_growth_pct` (Business, weight 0.05): CAGR of the filed
  diluted share count. A shrinking count scores up to 10 (−3%/yr), a growing
  count down to 0 (+20%/yr). It cannot distinguish buybacks from SBC issuance,
  option exercises, or offerings — they are netted into one number.

The blind spot, concretely (MU, §3): Micron spent **$650M** on buybacks in
FY26, yet its diluted share count still **rose +0.464%/yr** (dilution score
7.92) because employee stock issuance outran the repurchases. The model
recorded slight dilution and no buyback. It also has no way to see the
**$35.16B of authorization now standing** — the single largest capital-return
fact about the company — because an authorization is a forward commitment,
not a filed share count.

BIG SANDY's framing (2026-11-09, confirmed in his own words): based on how
much authorization is left, future buybacks add value to the shareholder, so
this metric belongs in **Value**. This spec implements exactly that: Business
keeps the *realized, historical* net share count; Value gains the *forward*
remaining authorization.

## 2. The metric — `forward_buyback_yield_pct`

**Definition (one uniform rule, all inputs filed, no per-name constants):**

```
forward_buyback_yield_pct = 100 × repurchase_authorization_remaining_usd
                                  ÷ market_cap_usd
market_cap_usd            = price (engine's existing price feed, dated)
                            × diluted shares (EDGAR companyfacts,
                              same series the DCF already uses)
```

### 2.1 Where `repurchase_authorization_remaining_usd` comes from

System of record, in priority order — all EDGAR, all code, extending the
full-text filing pull `filings.py` already performs for the latest 10-K:

1. **Reg S-K Item 703 table** — 10-Q Part II Item 2 / 10-K Part II Item 5.
   Final column: *"Approximate dollar value of shares that may yet be
   purchased under publicly announced plans or programs."* Last (total) row
   of the latest periodic filing. This column is HTML, not XBRL, so it needs
   a table parser; companyfacts cannot supply it.
2. **Narrative in the same Item + the equity note / MD&A** — the sentences
   around the table ("…$X billion of the authorization remained available…"),
   used when the table's final column is blank or share-denominated, and
   **mandatorily scanned for post-period-end board actions** disclosed in
   the same filing (an authorization increased after period end but before
   filing — the MU case, §3 — supersedes the table's period-end figure).
3. **Early-flag layer (never scored):** EDGAR full-text watch over 8-K /
   Exhibit 99.1 earnings releases for "repurchase + authorized / increase".
   Some issuers announce there (e.g. APP); MU does not (§3.3). A flag from
   this layer is displayed as `announced_not_yet_in_periodic_filing` and
   enters the score only when a periodic filing confirms it.

**Cross-check (mandatory, fail-soft warning, not a score input):** XBRL
`PaymentsForRepurchaseOfCommonStock` (companyfacts) — cash actually spent.
Between filings, `remaining` should fall by roughly the amount spent; a rise
means a new/increased authorization and must reconcile to a narrative
disclosure found in step 2, otherwise the run flags
`buyback_remaining_unreconciled`.

### 2.2 Fields stored per run

`buyback_authorization_total_usd`, `buyback_cumulative_used_usd` (when
stated), `buyback_remaining_usd`, `buyback_authorization_date`,
`buyback_effective_from` (when stated), `buyback_expiry` (null = none
stated), `buyback_conditions` (verbatim short quote, e.g. CHIPS Act limits),
`buyback_source_accession`, `buyback_source_quote` (verbatim sentence the
remaining figure was read from), `forward_buyback_yield_pct`.

### 2.3 Fail-closed behaviour

- No Item 703 table and no narrative remaining figure → sub-score missing,
  Value weights renormalise over the remaining legs (existing convention),
  flag `buyback_remaining_not_disclosed`. **Never** infer remaining from a
  press headline.
- Remaining stated in **shares**, not dollars (some issuers authorize a
  share count) → store shares, mark `buyback_remaining_share_denominated`,
  sub-score missing. Do not convert at market price in v1 (a conversion is
  a modelling choice; revisit only if it proves common in the config set).
- Ambiguity between "total authorization" and "remaining" (see trap, §3.2)
  → the verbatim-quote check governs: if the filing's own construction
  sentence cannot be located, fail closed as above.
- Employee tax-withholding repurchases are **not** program repurchases and
  must never be counted (the Item 703 table separates them; MU's Q3 FY26
  table shows 912 withheld shares vs 0 program shares).

## 3. Worked example — MU (Micron Technology), all values as filed

### 3.1 What buybacks Micron has mentioned, and what is left

| When | What was said | Amount | Source |
|---|---|---|---|
| 2018 | Board authorized a discretionary repurchase program, no expiration, no obligation to buy | **$10.00B** authorized | Q3 FY26 10-Q, Part II Item 2 (period ended May 28, 2026; filed Jun 25, 2026; accession 0000723125-26-000015) |
| Through May 28, 2026 | Cumulatively repurchased under that program | **$7.84B** used | Same 10-Q, Note "Common Stock Repurchases" |
| 9M FY26 (to May 28, 2026) | Executed: 2.5M shares; **none in Q3** | **$650M** spent | Same 10-Q, Note + cash-flow statement |
| As of May 28, 2026 | **Remaining** under the 2018 program — Item 703 table, final column ("may yet be purchased") | **$2.156B** | Same 10-Q, Item 2 Item 703 table |
| Full FY26 | Executed for the year (all of it in H1; none in Q4) | **$650M** | FY26 10-K cash-flow statement; Q4 FY26 earnings release (8-K Ex-99.1, Sep 30, 2026), financing activities |
| **Oct 8, 2026** | **Board increased the authorization** — the new available total is built as: $2.16B left over **+ 100% of Q4 FY26 adjusted FCF, less CHIPS Act grant proceeds for capex, less dividends paid**. Purchases under it **from Dec 9, 2026** (CHIPS Act restriction expiry), caps tied to FCF apply for the following 3 years; **no expiration date, no obligation** | **$35.16B available** | FY26 10-K, Part II Item 5 (period ended Sep 3, 2026; filed Oct 9, 2026; accession 0000723125-26-000023; primary document mu-20260903.htm) |
| Q4 FY26 | Adjusted FCF, the sizing input above | **$33.199B** | Q4 FY26 earnings release, GAAP→non-GAAP reconciliation (also filed as 8-K Ex-99.1, Sep 30, 2026) |

**Current remaining authorization for scoring: $35.16B.**

### 3.2 The trap this example sets for a naive parser

$35.16B is the **new available amount**, *not* a cumulative program cap.
Subtracting the $7.84B already spent from it ($27.32B) double-counts the
spend, because the $2.16B leftover it is built from already nets the spend
out. Filers use "total authorization" wording both ways; §2.1 step 2 +
§2.3's verbatim-quote rule exist for exactly this reason.

### 3.3 Why the IR-release layer cannot be the record

MU's last four quarterly earnings releases (Q1 FY26 Dec 17, 2025; Q2 Mar 18,
2026; Q3 Jun 24, 2026; Q4 Sep 30, 2026 — full text scraped from
investors.micron.com, identical to the 8-K Exhibit 99.1s) were searched for
"buyback", "authoriz*", "repurchase", "dividend" on 2026-11-09:

- "buyback": **0 mentions in all four**; authorization: **0 in all four**.
- The only "repurchase" hits are the cash-flow lines in §3.1 (executed
  amounts: $300M in Q1; $650M cumulative from Q2 onward).
- The October increase post-dates the Q4 release by 8 days and was disclosed
  **only in the 10-K** — no 8-K, no press release.

Hence the layering in §2.1: periodic filings are the system of record;
8-K / release scanning is an unscored early flag only. (All four releases
*do* carry the dividend declaration — $0.115 → $0.15, including the +30%
raise in Q2 — so that channel is the right source if dividends are ever
added. Out of scope here.)

## 4. Scoring change (Value only)

New sub-score in `config/scoring.yaml`, Value section:

```yaml
forward_buyback_yield_pct:
  weight: 0.05
  anchors: [[0, 2], [2, 4], [5, 6.5], [10, 8.5], [20, 10]]   # % of market cap
```

- **Weight is funded from `discount_from_52w_high_pct`: 0.10 → 0.05.**
  Rationale: the discount leg is the noisiest Value input (it rewards a
  fallen price regardless of cause). All other Value weights unchanged;
  weights still sum to 1.000, so no renormalisation side-effects.
- **Linear interpolation between anchors**, the engine's existing
  convention for every other sub-score.
- **Overvaluation cap (Decision Point 2, §8):** if `dcf_upside_pct < −50%`,
  the buyback sub-score is capped at **4.0**. Buying back deeply overvalued
  stock destroys per-share value; size alone must not be rewarded. (MU is
  nowhere near this cap — its V5.2 DCF upside is +43.1%.)
- **Business and Moat: byte-identical.** No rule, weight, or input changes
  outside the new Value leg — the same invariant V5.2 held.

### Inputs for the MU calculation (engine's own V5.2 run basis)

| Input | Value | Source |
|---|---|---|
| Price | **$690.00** (close, Nov 6, 2026) | Yahoo chart feed, engine run `inputs.price` |
| Diluted shares, latest period | **1.143B** | EDGAR companyfacts, engine KPI `diluted_shares_latest_period` |
| Market cap (derived) | **$788.67B** | 690.00 × 1.143B. Cross-check: EV/S 5.4089 × revenue $133.188B = EV $720.4B + net cash $68.27B = $788.7B ✓ |
| Remaining authorization | **$35.16B** | §3.1, FY26 10-K Item 5 |
| **Forward buyback yield** | **4.458%** | 35.16 ÷ 788.67 |

## 5. Expected output — MU, V5.2 (current code) → V5.3 (this spec)

V5.2 baseline is the actual engine run (snapshot 2026-11-09,
`engine_runs_v52/MU.json`): Business **6.83**, Value **6.95**, Moat **6.29**,
DCF **$987.28** (upside **+43.08%** at $690.00).

Buyback sub-score at 4.458%: interpolating the §4 anchors between
2% → 4.0 and 5% → 6.5 gives **6.05**.

| Value leg | V5.2 score | V5.2 wt | V5.2 contrib. | V5.3 score | V5.3 wt | V5.3 contrib. |
|---|---|---|---|---|---|---|
| Trailing P/E (9.28×) | 8.287 | 0.10 | 0.829 | 8.287 | 0.10 | 0.829 |
| Price / Sales (5.92×) | 4.219 | 0.15 | 0.633 | 4.219 | 0.15 | 0.633 |
| EV / Sales (5.41×) | 4.561 | 0.10 | 0.456 | 4.561 | 0.10 | 0.456 |
| FCF yield (7.31%) | 8.480 | 0.20 | 1.696 | 8.480 | 0.20 | 1.696 |
| DCF upside (+43.08%) | 7.585 | 0.20 | 1.517 | 7.585 | 0.20 | 1.517 |
| Discount from 52-wk high (−43.14%) | 7.314 | 0.10 | 0.731 | 7.314 | **0.05** | 0.366 |
| Relative EV/S (72.2 pctile) | 7.220 | 0.15 | 1.083 | 7.220 | 0.15 | 1.083 |
| **Forward buyback yield (4.458%)** | — | — | — | **6.048** | **0.05** | **0.302** |
| **Value total** | | | **6.95** | | | **6.88** |

**Headline result, stated plainly: MU Value 6.95 → 6.88 (−0.06).**
Business 6.83 and Moat 6.29 unchanged. On the 100-point page scale:
Value 69.5 → 68.8.

### 5.1 Why MU goes *down* slightly — and why that is correct

The new leg is not a bonus added on top; it is funded by halving the
discount-from-high leg, where MU currently scores 7.31. MU's buyback yield
scores 6.05 — good, not top-decile — because $35.16B is only ~4.5% of an
$789B market cap (roughly 3–4.5% of the shares, depending on price). A
5%-weight leg can only move a total by ±0.13 at the extremes; this is a
signal, not a verdict-changer, by design.

### 5.2 What the feature *does* capture: the October event

Run the same V5.3 formula against the pre-increase state ($2.156B left,
yield 0.273%, sub-score 2.27): Value would be **6.69**. The October 8
authorization therefore moves MU **+0.19** within V5.3 (6.69 → 6.88) — the
score now *responds* to a buyback announcement, which V5.2 cannot do at
all. The −0.06 headline is a one-time level shift from re-weighting that
applies to every name; the +0.19 is the new information the feature adds.

### 5.3 Sensitivity (any name; delta vs its V5.2 Value, discount-funded)

| Remaining ÷ market cap | Sub-score | Δ Value |
|---|---|---|
| 0% | 2.00 | −0.266 |
| 1% | 3.00 | −0.216 |
| 2% | 4.00 | −0.166 |
| **4.458% (MU today)** | **6.05** | **−0.063** |
| 5% | 6.50 | −0.041 |
| 7% | 7.30 | −0.001 |
| 10% | 8.50 | +0.059 |
| 15% | 9.25 | +0.097 |
| 20% | 10.00 | +0.134 |

(The Δ column is computed against a discount-leg score of 7.314, MU's;
each name's break-even differs with its own discount score. For MU the
break-even is a 7.04% yield ≈ **$55.5B** remaining at the Nov 6 price —
i.e. the authorization would need to be ~$20B larger for the new leg to
pay for itself in MU's total.)

### 5.4 Display (dashboard + run JSON)

New KPI card, shown whether or not the sub-score is present:
**"Buyback authorization remaining: $35.16B (4.46% of market cap) —
authorized Oct 8, 2026, purchases from Dec 9, 2026, no expiry · FY26 10-K
Item 5"**, with the verbatim source quote one click away, mirroring the
existing concentration-evidence pattern. Value explanation gains one
template sentence naming the leg, its yield, and its score.

## 6. Tests and rollout

Existing suite: 65/65 (V5.2). New regression tests:

1. Item 703 parse: MU Q3 FY26 10-Q fixture → remaining $2,156M, program
   shares 0, withheld shares excluded.
2. Post-period increase: MU FY26 10-K fixture → remaining $35.16B (not the
   period-end $2.156B), effective-from Dec 9, 2026 captured.
3. Total-vs-remaining trap: a filing whose construction sentence is absent
   → fail closed, `buyback_remaining_not_disclosed`, no score.
4. Share-denominated authorization fixture → stored as shares, sub-score
   missing, weights renormalise.
5. Anchors/interpolation: 4.458% → 6.048; cap: `dcf_upside_pct` −60% with
   a 15% yield → sub-score 4.0, not 9.25.
6. Invariants: every existing fixture's Business and Moat totals
   byte-identical; Value weights sum to exactly 1.000.
7. MU end-to-end on the Nov 6 price basis reproduces §5 exactly
   (Value 6.88, Business 6.83, Moat 6.29).

Rollout mirrors V5.2: repo engine → tests → rerun the comparison set →
before/after diff table (Business, Value, DCF per ticker) → Space TS port
(port the parser identically; verify live via the Space's own search on
MU, META, and one fail-closed name) → heatmap page gains the V5.3 Code
column alongside V5.2, not replacing it. No GitHub push (still blocked;
working tree only).

## 7. Decision points for BIG SANDY

Defaults below are what §4–§5 already assume; approving the spec as
written approves them.

1. **Weight funding:** discount-from-52-wk-high 0.10 → 0.05 (assumed).
   Alternatives costed on MU: funding from the DCF leg (0.20 → 0.15)
   gives MU 6.87; pro-rata funding from all legs gives 6.90; a pure
   additive bonus (+0.30 → 7.25) is **not recommended** — it breaks the
   weights-sum-to-1.000 convention every other score obeys.
2. **Overvaluation cap:** cap sub-score at 4.0 when DCF upside < −50%
   (assumed). Alternative: no cap (size scored as-is).
3. **Scored vs display-only for one version:** scoring immediately
   (assumed), vs shipping §5.4's display card in V5.3 and the sub-score
   in V5.4 after a comparison-set sanity pass.

## 8. Known limitations (accepted, stated)

- **Cadence is quarterly.** A mid-quarter standalone 8-K authorization
  enters via the early-flag layer as display-only until the next periodic
  filing confirms it. This is the price of filed-data-only discipline —
  and MU shows the reverse case (filing-only disclosure) is just as real.
- **Authorizations are permissions, not commitments.** MU's own history:
  $7.84B used of the 2018 $10B program over 8 years, $0 in FY25. The
  5% weight and the anchors (a 4.46% yield scores only 6.05) are set so
  an unexecuted promise cannot dominate a score.
- **Foreign private issuers (20-F)** disclose repurchases differently;
  they follow the engine's existing fail-closed treatment for missing
  periodic data.
- Remaining figures are as of the *filing's* period end (plus disclosed
  post-period actions) — between filings the figure ages, like every
  other filed input in the engine.
