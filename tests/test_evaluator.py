import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.evaluator.scoring import (interpolate, concentration_score, score_section,
                                   cheapness_percentile, relative_ev_to_sales,
                                   apply_buyback_overvaluation_cap,
                                   growth_band_score, gav_label, gav_code)
from src.evaluator.buybacks import extract_buyback
from src.evaluator import prices as pricesmod
from src.evaluator.dcf import (fair_value, growth_path, build_wacc,
                               fcf_base_per_share, dcf_bases)
from src.evaluator.filings import extract_concentration, strip_html
from src.evaluator.metrics import (ttm_from_periods, _latest_period_value, add_price_metrics,
                                   compute_metrics, resolve_total_debt)
from src.evaluator import config as cfgmod
from src.evaluator import edgar as edgarmod
from src.evaluator import class_shares as classsharesmod
from src.evaluator import run as runmod

RULES = {"base_if_no_single_customer_at_threshold": 7.0,
         "base_if_single_customer_present": 4.0,
         "single_customer_extra_penalty_per_pct_over_20": 0.1,
         "top10_free_allowance_pct": 40,
         "top10_penalty_per_pct_over_allowance": 0.04,
         "top10_approx_value_when_over_half_pct": 55,
         "top10_approx_value_when_one_half_pct": 50,
         "score_floor": 0, "score_ceiling": 10}
SEARCH = {"single_customer_keywords": ["customer accounted for"],
          "top_ten_keywords": ["top ten customers"],
          "data_center_keywords": ["data center", "end market"]}


class TestInterpolation(unittest.TestCase):
    def test_midpoint(self):
        self.assertAlmostEqual(interpolate(15, [[10, 8], [20, 6]]), 7.0)

    def test_clamps(self):
        self.assertEqual(interpolate(-99, [[0, 2], [10, 5]]), 2)
        self.assertEqual(interpolate(99, [[0, 2], [10, 5]]), 5)

    def test_descending_inverse_metric(self):
        # stdev-style anchors: higher value -> lower score
        self.assertAlmostEqual(interpolate(15, [[10, 6], [20, 3.5]]), 4.75)


class TestDCF(unittest.TestCase):
    def test_flat_fcf_zero_growth(self):
        # FCF/share 100 forever, WACC 10%, terminal 0%: PV = 100/0.10 = 1000
        out = fair_value(100, [0, 0, 0, 0, 0], 10, 0)
        self.assertAlmostEqual(out["fair_value_per_share"], 1000, delta=1.0)

    def test_wacc_build(self):
        cfg = {"risk_free_rate_pct": 4.0, "equity_risk_premium_pct": 5.0,
               "pre_tax_cost_of_debt_pct": 5.0, "tax_rate_pct": 20.0}
        w = build_wacc(cfg, beta=2.0, market_cap=900, total_debt=100)
        self.assertAlmostEqual(w["cost_of_equity_pct"], 14.0)
        self.assertAlmostEqual(w["wacc_pct"], 14.0 * 0.9 + 4.0 * 0.1, places=2)

    def test_growth_fades_and_caps(self):
        cfg = {"projection_years": 5, "terminal_growth_pct": 0.0,
               "growth": {"floor_pct": 0.0, "cap_pct": 25.0}}
        path = growth_path(cfg, 90.0)  # filed CAGR above cap
        self.assertEqual(path[0], 25.0)
        self.assertTrue(all(a > b for a, b in zip(path, path[1:])))


class TestConcentration(unittest.TestCase):
    SNIPPET = ("No customer accounted for 10% or more of total revenue in 2026. "
               "In 2026, over half of our total revenue came from our top ten customers. "
               "Among our end markets, approximately 60% of our total revenue was "
               "concentrated in the data center end market.")

    def test_extracts_all_three(self):
        out = extract_concentration(self.SNIPPET, SEARCH, RULES, latest_fy_year=2026)
        self.assertTrue(out["single_customer"]["no_customer_at_or_above_threshold"])
        self.assertIn("No customer accounted for", out["single_customer"]["quote"])
        self.assertEqual(out["top_ten"]["top_ten_share_pct"], 55)
        self.assertEqual(out["data_center"]["data_center_share_pct"], 60.0)

    def test_score_rule(self):
        out = extract_concentration(self.SNIPPET, SEARCH, RULES, latest_fy_year=2026)
        # base 7.0 - (55-40)*0.04 = 6.4
        self.assertAlmostEqual(concentration_score(out, RULES), 6.4)


class TestTTM(unittest.TestCase):
    """Regression for the annual-as-TTM bug. NVDA-shaped data (values in
    $M): annual FY ended 2026-01-25 of 215,938, two filed quarters into
    the next FY, and NO standalone Q4 row (a fiscal Q4 is reported only
    inside the annual figure). True TTM revenue is ~302,970, so P/S at
    $186.64 on ~24,285M shares must come out ~15x, not the ~21x the
    annual figure produced."""
    ROWS = [
        # latest annual year (FY26): start 2025-01-27, end 2026-01-25
        {"start": "2025-01-27", "end": "2025-04-27", "value": 44062, "filed": "2025-05-28"},   # prior Q1
        {"start": "2025-01-27", "end": "2025-07-27", "value": 90805, "filed": "2025-08-27"},   # prior H1 YTD
        {"start": "2025-04-28", "end": "2025-07-27", "value": 46743, "filed": "2025-08-27"},   # prior Q2 standalone
        {"start": "2025-01-27", "end": "2025-10-26", "value": 147811, "filed": "2025-11-19"},  # prior 9M YTD
        {"start": "2025-01-27", "end": "2026-01-25", "value": 215938, "filed": "2026-02-25"},  # annual (incl. Q4)
        # current FY (FY27): start 2026-01-26
        {"start": "2026-01-26", "end": "2026-04-26", "value": 81615, "filed": "2026-05-20"},   # Q1
        {"start": "2026-01-26", "end": "2026-07-26", "value": 177837, "filed": "2026-08-26"},  # H1 YTD
        {"start": "2026-04-27", "end": "2026-07-26", "value": 96221, "filed": "2026-08-26"},   # Q2 standalone
    ]
    SHARE_ROWS = [
        {"start": "2025-01-27", "end": "2026-01-25", "value": 24514, "filed": "2026-02-25"},  # annual avg
        {"start": "2026-01-26", "end": "2026-07-26", "value": 24338, "filed": "2026-08-26"},  # H1 YTD avg
        {"start": "2026-04-27", "end": "2026-07-26", "value": 24285, "filed": "2026-08-26"},  # Q2 standalone
    ]

    def test_ttm_revenue_not_annual(self):
        t = ttm_from_periods(self.ROWS, "2025-01-27", "2026-01-25", 215938)
        self.assertEqual(t["value"], 215938 + 177837 - 90805)
        self.assertEqual(t["value"], 302970)
        self.assertEqual(t["ttm_end"], "2026-07-26")
        self.assertEqual(t["basis"], "annual_plus_current_fy_ytd_minus_prior_year_ytd")

    def test_ttm_ps_ratio(self):
        t = ttm_from_periods(self.ROWS, "2025-01-27", "2026-01-25", 215938)
        m = {"revenue_ttm": t["value"], "revenue_latest": 215938,
             "diluted_shares_latest_period": _latest_period_value(self.SHARE_ROWS),
             "diluted_shares_latest": 24514, "net_cash_latest": 0,
             "eps_diluted_ttm": 7.91, "fcf_ttm": 127006}
        price = {"last_close": 186.64, "high_52w": 236.54, "low_52w": 164.07}
        m = add_price_metrics(m, price)
        self.assertEqual(m["diluted_shares_latest_period"], 24285)
        self.assertAlmostEqual(m["price_to_sales"], 14.96, places=2)
        self.assertLess(m["price_to_sales"], 16)
        self.assertEqual(m["multiples_basis"], "ttm")

    def test_no_interim_filings_ttm_equals_annual(self):
        rows = [r for r in self.ROWS if r["end"] <= "2026-01-25"]
        t = ttm_from_periods(rows, "2025-01-27", "2026-01-25", 215938)
        self.assertEqual(t["value"], 215938)
        self.assertEqual(t["basis"], "annual_only_no_interim_filings_since_fy_end")


class TestDebtResolution(unittest.TestCase):
    """Regression for the CoreWeave bug: total_debt came back null (the
    filer's debt lived in DebtInstrumentCarryingAmount, which the concept
    list missed) and metrics treated missing debt as $0, so
    net_cash_to_revenue scored a fictitious +0.73 and Business came out
    7.11 for a company carrying ~$35.6B of debt."""

    def test_no_debt_concepts_fail_closed(self):
        series = {
            "revenue": [{"end": "2024-12-31", "value": 100.0},
                        {"end": "2025-12-31", "value": 120.0}],
            "cash": [{"end": "2025-12-31", "value": 50.0}],
            "_periods": {},
            "_balance_latest": {"cash": {"end": "2025-12-31", "value": 50.0,
                                         "filed": "2026-02-01"}},
            "_debt_instants": {"totals": {}, "current": {}, "noncurrent": {}},
        }
        m = compute_metrics(series, 15.0)
        self.assertIsNone(m["total_debt_latest"])
        self.assertIsNone(m["net_cash_latest"])
        self.assertIsNone(m["net_cash_to_revenue"])
        self.assertIsNone(m["leverage_debt_to_revenue_ttm"])
        self.assertTrue(m["debt_not_determinable_fail_closed"])
        business = score_section(cfgmod.load_all()["scoring"]["business"], m)
        # Excluded and renormalised — never scored as zero debt.
        self.assertIn("net_cash_to_revenue", business["missing_metrics"])
        self.assertIn("leverage_debt_to_revenue_ttm", business["missing_metrics"])
        self.assertNotIn("net_cash_to_revenue", business["subscores"])

    def test_crwv_like_carrying_amount_resolves(self):
        # CoreWeave-shaped: at the latest balance date the ONLY debt tag
        # is DebtInstrumentCarryingAmount ($35.55B at 2026-06-30); the
        # LongTermDebt pair exists only in an older period.
        instants = {
            "totals": {"DebtInstrumentCarryingAmount": {"2026-06-30": 35.55e9}},
            "current": {"LongTermDebtCurrent": {"2026-03-31": 7.55e9}},
            "noncurrent": {"LongTermDebtNoncurrent": {"2026-03-31": 17.31e9}},
        }
        res = resolve_total_debt(instants)
        self.assertEqual(res["value"], 35.55e9)
        self.assertEqual(res["as_of"], "2026-06-30")
        self.assertEqual(res["method"], "single_total:DebtInstrumentCarryingAmount")

        series = {
            "revenue": [{"end": "2025-12-31", "value": 7.59e9}],
            "cash": [],
            "_periods": {},
            "_balance_latest": {"cash": {"end": "2026-06-30", "value": 5.524e9,
                                         "filed": "2026-08-12"}},
            "_debt_instants": instants,
        }
        m = compute_metrics(series, 15.0)
        self.assertEqual(m["total_debt_latest"], 35.55e9)
        self.assertAlmostEqual(m["net_cash_latest"], 5.524e9 - 35.55e9)
        self.assertAlmostEqual(m["leverage_debt_to_revenue_ttm"], 35.55e9 / 7.59e9, places=4)
        business = score_section(cfgmod.load_all()["scoring"]["business"], m)
        # Net-cash ratio is ~-3.96: clamped to a 0 score, not +8.91.
        self.assertEqual(business["subscores"]["net_cash_to_revenue"]["score"], 0)
        self.assertLess(business["subscores"]["leverage_debt_to_revenue_ttm"]["score"], 0.5)

    def test_pair_wins_over_total_at_same_date(self):
        instants = {
            "totals": {"LongTermDebt": {"2026-06-30": 90.0}},
            "current": {"LongTermDebtCurrent": {"2026-06-30": 30.0}},
            "noncurrent": {"LongTermDebtNoncurrent": {"2026-06-30": 70.0}},
        }
        res = resolve_total_debt(instants)
        self.assertEqual(res["method"], "current_plus_noncurrent_pair")
        self.assertEqual(res["value"], 100.0)

    def test_business_weights_sum_to_one(self):
        business = cfgmod.load_all()["scoring"]["business"]
        self.assertAlmostEqual(sum(s["weight"] for s in business.values()), 1.0, places=9)
        self.assertIn("leverage_debt_to_revenue_ttm", business)
        self.assertIn("interest_coverage_ttm", business)
        self.assertIn("dilution_share_count_growth_pct", business)


def _flow_series(shares, with_sbc=True):
    ends = ["2023-12-31", "2024-12-31", "2025-12-31"]
    series = {
        "revenue": [{"end": e, "value": v} for e, v in zip(ends, [100.0, 110.0, 120.0])],
        "operating_cash_flow": [{"end": e, "value": v} for e, v in zip(ends, [30.0, 33.0, 36.0])],
        "capex": [{"end": e, "value": 10.0} for e in ends],
        "diluted_shares": [{"end": e, "value": v} for e, v in zip(ends, shares)],
        "_periods": {},
        "_balance_latest": {},
        "_debt_instants": {"totals": {}, "current": {}, "noncurrent": {}},
    }
    if with_sbc:
        series["share_based_compensation"] = [{"end": e, "value": 5.0} for e in ends]
    return series


class TestV3SBCAndDilution(unittest.TestCase):
    """v3: SBC-adjusted owner FCF + dilution sub-score."""

    def test_sbc_adjusted_fcf_and_margin(self):
        m = compute_metrics(_flow_series([100.0, 110.0, 121.0]), 15.0)
        # Raw FCF latest = 36 - 10 = 26; owner FCF = 26 - 5 = 21.
        self.assertEqual(m["fcf_raw_latest"], 26.0)
        self.assertEqual(m["fcf_latest"], 21.0)
        self.assertEqual(m["fcf_adjusted_latest"], 21.0)
        self.assertEqual(m["sbc_latest"], 5.0)
        self.assertFalse(m["sbc_not_reported_fcf_unadjusted_not_used"])
        # Adjusted margins: 15/100, 18/110, 21/120 -> median 18/110.
        self.assertAlmostEqual(m["fcf_margin_median_5y_pct"], 18.0 / 110.0 * 100, places=6)
        self.assertAlmostEqual(m["sbc_pct_of_revenue_latest"], 5.0 / 120.0 * 100, places=6)

    def test_no_sbc_reported_fails_closed(self):
        m = compute_metrics(_flow_series([100.0, 110.0, 121.0], with_sbc=False), 15.0)
        self.assertTrue(m["sbc_not_reported_fcf_unadjusted_not_used"])
        self.assertIsNone(m["fcf_latest"])
        self.assertIsNone(m["fcf_margin_median_5y_pct"])
        self.assertEqual(m["fcf_raw_latest"], 26.0)  # raw still disclosed
        business = score_section(cfgmod.load_all()["scoring"]["business"], m)
        self.assertIn("fcf_margin_median_5y_pct", business["missing_metrics"])
        self.assertNotIn("fcf_margin_median_5y_pct", business["subscores"])

    def test_dilution_rising_share_count(self):
        m = compute_metrics(_flow_series([100.0, 110.0, 121.0]), 15.0)
        # 100 -> 121 over ~2 years = ~+10%/yr CAGR.
        self.assertAlmostEqual(m["dilution_share_count_growth_pct"], 10.0, delta=0.15)
        self.assertEqual(m["dilution_annual_points"], 3)
        business = score_section(cfgmod.load_all()["scoring"]["business"], m)
        self.assertAlmostEqual(
            business["subscores"]["dilution_share_count_growth_pct"]["score"], 1.0, delta=0.1)

    def test_dilution_falling_share_count_scores_high(self):
        m = compute_metrics(_flow_series([121.0, 110.0, 100.0]), 15.0)
        self.assertLess(m["dilution_share_count_growth_pct"], -3.0)
        business = score_section(cfgmod.load_all()["scoring"]["business"], m)
        self.assertEqual(business["subscores"]["dilution_share_count_growth_pct"]["score"], 10)

    def test_dilution_units_break_fails_closed(self):
        # ONDS-shaped: newest filing reports shares in thousands, so the
        # series collapses ~1000x in one step. Not a buyback — missing.
        series = _flow_series([52.74e6, 69917.0, 221769.0])
        m = compute_metrics(series, 15.0)
        self.assertIsNone(m["dilution_share_count_growth_pct"])
        self.assertTrue(m["dilution_share_series_discontinuity"])
        business = score_section(cfgmod.load_all()["scoring"]["business"], m)
        self.assertIn("dilution_share_count_growth_pct", business["missing_metrics"])

    def test_dilution_single_point_missing(self):
        series = _flow_series([100.0, 110.0, 121.0])
        series["diluted_shares"] = [{"end": "2025-12-31", "value": 121.0}]
        m = compute_metrics(series, 15.0)
        self.assertIsNone(m["dilution_share_count_growth_pct"])
        business = score_section(cfgmod.load_all()["scoring"]["business"], m)
        self.assertIn("dilution_share_count_growth_pct", business["missing_metrics"])


class TestV4DebtFamilies(unittest.TestCase):
    """v4: debt families. Applied Digital carries ~$6.4B almost entirely
    as notes payable (LongTermNotesPayable + NotesPayableCurrent); the
    bank family alone resolved a stray $7.19M concept and understated
    its debt ~1000x."""

    APLD_LIKE = {
        "totals": {"LongTermDebt": {"2026-08-31": 7.19e6}},
        "grand_totals": {},
        "families": {
            "bank_debt": {"current": {}, "noncurrent": {}},
            "notes_payable": {
                "current": {"NotesPayableCurrent": {"2026-08-31": 0.11e9}},
                "noncurrent": {"LongTermNotesPayable": {"2026-08-31": 6.26e9}},
            },
        },
    }

    def test_notes_family_beats_stray_minor_total(self):
        res = resolve_total_debt(self.APLD_LIKE)
        self.assertAlmostEqual(res["value"], 6.37e9, delta=1e6)
        self.assertNotEqual(res["value"], 7.19e6)
        self.assertEqual(res["as_of"], "2026-08-31")

    def test_families_sum_across_families(self):
        instants = {
            "totals": {},
            "grand_totals": {},
            "families": {
                "bank_debt": {
                    "current": {"LongTermDebtCurrent": {"2026-06-30": 1.0e9}},
                    "noncurrent": {"LongTermDebtNoncurrent": {"2026-06-30": 3.0e9}},
                },
                "notes_payable": {
                    "current": {"NotesPayableCurrent": {"2026-06-30": 0.5e9}},
                    "noncurrent": {"LongTermNotesPayable": {"2026-06-30": 1.5e9}},
                },
            },
        }
        res = resolve_total_debt(instants)
        self.assertEqual(res["method"], "family_pairs_sum")
        self.assertAlmostEqual(res["value"], 6.0e9)

    def test_grand_total_replaces_families(self):
        instants = dict(self.APLD_LIKE)
        instants["grand_totals"] = {"DebtInstrumentCarryingAmount": {"2026-08-31": 35.55e9}}
        res = resolve_total_debt(instants)
        # Carrying amount replaces components — never added to them.
        self.assertAlmostEqual(res["value"], 35.55e9)
        self.assertEqual(res["method"], "single_total:DebtInstrumentCarryingAmount")


class TestV4ValueChanges(unittest.TestCase):
    """v4: negative-P/E guard, relative EV/S percentile, value weights."""

    def _price_metrics(self, eps_ttm):
        m = {"revenue_ttm": 1000.0, "revenue_latest": 900.0,
             "diluted_shares_latest_period": 100.0, "diluted_shares_latest": 100.0,
             "net_cash_latest": 0.0, "eps_diluted_ttm": eps_ttm, "fcf_ttm": 50.0}
        price = {"last_close": 50.0, "high_52w": 100.0, "low_52w": 25.0}
        return add_price_metrics(m, price)

    def test_negative_eps_pe_not_scored(self):
        m = self._price_metrics(-3.55)
        self.assertIsNone(m["trailing_pe"])
        self.assertTrue(m["negative_trailing_pe_not_scored"])
        value = score_section(cfgmod.load_all()["scoring"]["value"], m)
        self.assertIn("trailing_pe", value["missing_metrics"])
        self.assertNotIn("trailing_pe", value["subscores"])

    def test_positive_eps_pe_scored(self):
        m = self._price_metrics(5.0)
        self.assertAlmostEqual(m["trailing_pe"], 10.0)
        self.assertFalse(m["negative_trailing_pe_not_scored"])

    def test_cheapness_percentile_crwv_anchor(self):
        # CRWV preview: subject 7.32 vs APLD 9.13, CORZ 15.36, WULF
        # 42.04, HUT 47.87, CIFR 53.27 -> cheapest of 6 -> 91.7.
        pct = cheapness_percentile(7.32, [9.13, 15.36, 42.04, 47.87, 53.27])
        self.assertAlmostEqual(pct, 91.7, places=1)
        value = score_section(cfgmod.load_all()["scoring"]["value"],
                              {"relative_ev_to_sales_percentile": pct})
        self.assertAlmostEqual(
            value["subscores"]["relative_ev_to_sales_percentile"]["score"], 9.17, delta=0.01)

    def test_cheapness_percentile_most_expensive(self):
        pct = cheapness_percentile(53.27, [7.32, 9.13, 15.36, 42.04, 47.87])
        self.assertAlmostEqual(pct, (0 + 0.5) / 6 * 100, places=1)

    def test_value_weights_sum_to_one(self):
        value = cfgmod.load_all()["scoring"]["value"]
        self.assertAlmostEqual(sum(s["weight"] for s in value.values()), 1.0, places=9)
        self.assertIn("relative_ev_to_sales_percentile", value)
        self.assertAlmostEqual(value["trailing_pe"]["weight"], 0.10)
        self.assertAlmostEqual(value["dcf_upside_pct"]["weight"], 0.20)
        self.assertAlmostEqual(value["relative_ev_to_sales_percentile"]["weight"], 0.15)


INFLECTION_CFG = {"min_distance_pts": 15, "min_revenue_cagr_pct": 20,
                  "max_latest_ttm_gap_pts": 12}


def _annual_period_rows(metric_values):
    """_periods rows carrying just the latest-FY annual row per metric,
    enough for the TTM bridge to resolve to the annual figure."""
    return {metric: [{"start": "2025-01-01", "end": "2025-12-31",
                      "value": v, "filed": "2026-02-01"}]
            for metric, v in metric_values.items()}


def _inflection_series():
    """CRDO-shaped: five years of hypergrowth, margins crossing from
    negative medians to solidly positive latest-FY/TTM readings.
    Operating margins: -30, -10, -2.5, +6.25, +12.5 (median -2.5).
    Owner-FCF margins: -75, -27.5, -8.75, +1.875, +9.0625 (median -8.75)."""
    ends = ["2021-12-31", "2022-12-31", "2023-12-31", "2024-12-31", "2025-12-31"]
    series = {
        "revenue": [{"end": e, "value": v} for e, v in zip(ends, [100.0, 200.0, 400.0, 800.0, 1600.0])],
        "operating_income": [{"end": e, "value": v} for e, v in zip(ends, [-30.0, -20.0, -10.0, 50.0, 200.0])],
        "operating_cash_flow": [{"end": e, "value": v} for e, v in zip(ends, [-60.0, -40.0, -20.0, 30.0, 160.0])],
        "capex": [{"end": e, "value": 10.0} for e in ends],
        "share_based_compensation": [{"end": e, "value": 5.0} for e in ends],
        "_periods": _annual_period_rows({"revenue": 1600.0, "operating_income": 200.0,
                                         "operating_cash_flow": 160.0, "capex": 10.0,
                                         "share_based_compensation": 5.0}),
        "_balance_latest": {},
        "_debt_instants": {"totals": {}, "current": {}, "noncurrent": {}},
    }
    return series


class TestV5InflectionBlend(unittest.TestCase):
    """v5 Fix 1: sign-flip inflection blend on the two median margin
    sub-scores; a positive-median cyclical must never fire."""

    def test_sign_flip_fires_and_blends(self):
        m = compute_metrics(_inflection_series(), 15.0, INFLECTION_CFG)
        blend = m["margin_inflection_blend"]
        self.assertIn("operating_margin_median_5y_pct", blend)
        self.assertIn("fcf_margin_median_5y_pct", blend)
        # Operating: median -2.5, current min(FY, TTM) = 12.5 -> blend 5.0.
        self.assertAlmostEqual(m["operating_margin_median_5y_pct_raw"], -2.5)
        self.assertAlmostEqual(m["operating_margin_ttm_pct"], 12.5)
        self.assertAlmostEqual(m["operating_margin_latest_pct"], 12.5)
        self.assertAlmostEqual(m["operating_margin_median_5y_pct"], 5.0)
        # FCF: median -8.75, current 9.0625 -> blend 0.15625.
        self.assertAlmostEqual(m["fcf_margin_median_5y_pct_raw"], -8.75)
        self.assertAlmostEqual(m["fcf_margin_ttm_pct"], 9.0625)
        self.assertAlmostEqual(m["fcf_margin_median_5y_pct"], 0.15625, places=4)
        business = score_section(cfgmod.load_all()["scoring"]["business"], m)
        # The blended operating margin scores off the blended value.
        self.assertEqual(business["subscores"]["operating_margin_median_5y_pct"]["value"], 5.0)

    def test_no_config_no_blend(self):
        m = compute_metrics(_inflection_series(), 15.0)
        self.assertEqual(m["margin_inflection_blend"], {})
        self.assertAlmostEqual(m["operating_margin_median_5y_pct"], -2.5)

    def test_mu_shaped_cycle_does_not_fire(self):
        # Positive median (+26.9%) with a high latest year: a cyclical
        # peak, not an inflection. Median must survive untouched.
        ends = ["2021-12-31", "2022-12-31", "2023-12-31", "2024-12-31", "2025-12-31"]
        series = _inflection_series()
        series["revenue"] = [{"end": e, "value": v} for e, v in zip(ends, [100.0, 110.0, 90.0, 130.0, 200.0])]
        series["operating_income"] = [{"end": e, "value": v} for e, v in zip(ends, [30.0, 25.0, -10.0, 35.0, 90.0])]
        series["_periods"] = _annual_period_rows({"revenue": 200.0, "operating_income": 90.0,
                                                  "operating_cash_flow": 160.0, "capex": 10.0,
                                                  "share_based_compensation": 5.0})
        m = compute_metrics(series, 15.0, INFLECTION_CFG)
        self.assertGreater(m["operating_margin_median_5y_pct_raw"], 0)
        self.assertAlmostEqual(m["operating_margin_median_5y_pct"],
                               m["operating_margin_median_5y_pct_raw"])
        self.assertNotIn("operating_margin_median_5y_pct", m["margin_inflection_blend"])


def _zero_debt_series(eligible):
    return {
        "revenue": [{"end": "2024-12-31", "value": 100.0},
                    {"end": "2025-12-31", "value": 120.0}],
        "cash": [{"end": "2025-12-31", "value": 50.0}],
        "_periods": {},
        "_balance_latest": {"cash": {"end": "2025-12-31", "value": 50.0,
                                     "filed": "2026-02-01"}},
        "_debt_instants": {"totals": {}, "grand_totals": {}, "families": {}},
        "_verified_zero_debt": {"eligible": eligible,
                                "checks": {"debt_balance_rows": 0}},
    }


class TestV5VerifiedZeroDebt(unittest.TestCase):
    """v5 Fix 2: verified-zero debt resolves to exactly 0 with credit
    for net cash and leverage, while interest coverage stays missing;
    anything failing the evidence test stays fail-closed."""

    def test_verified_zero_resolves(self):
        m = compute_metrics(_zero_debt_series(True), 15.0)
        self.assertEqual(m["total_debt_latest"], 0)
        self.assertTrue(m["debt_verified_zero"])
        self.assertFalse(m["debt_not_determinable_fail_closed"])
        self.assertEqual(m["total_debt_resolution"]["method"], "verified_zero")
        self.assertAlmostEqual(m["net_cash_latest"], 50.0)
        self.assertAlmostEqual(m["leverage_debt_to_revenue_ttm"], 0.0)
        self.assertIsNone(m["interest_coverage_ttm"])
        business = score_section(cfgmod.load_all()["scoring"]["business"], m)
        self.assertEqual(business["subscores"]["leverage_debt_to_revenue_ttm"]["score"], 10)
        self.assertIn("net_cash_to_revenue", business["subscores"])
        self.assertIn("interest_coverage_ttm", business["missing_metrics"])

    def test_unverified_stays_fail_closed(self):
        m = compute_metrics(_zero_debt_series(False), 15.0)
        self.assertIsNone(m["total_debt_latest"])
        self.assertFalse(m["debt_verified_zero"])
        self.assertTrue(m["debt_not_determinable_fail_closed"])


def _fixture_facts(extra=None):
    us_gaap = {
        "RevenueFromContractWithCustomerExcludingAssessedTax": {"units": {"USD": [
            {"form": "10-K", "fp": "FY", "start": "2024-01-01", "end": "2024-12-31",
             "val": 100.0, "filed": "2025-02-01"},
            {"form": "10-K", "fp": "FY", "start": "2025-01-01", "end": "2025-12-31",
             "val": 120.0, "filed": "2026-02-01"}]}},
        "CashAndCashEquivalentsAtCarryingValue": {"units": {"USD": [
            {"form": "10-K", "fp": "FY", "end": "2025-12-31",
             "val": 50.0, "filed": "2026-02-01"}]}},
        "Liabilities": {"units": {"USD": [
            {"form": "10-K", "fp": "FY", "end": "2025-12-31",
             "val": 80.0, "filed": "2026-02-01"}]}},
        "ShortTermInvestments": {"units": {"USD": [
            {"form": "10-K", "fp": "FY", "end": "2025-12-31",
             "val": 30.0, "filed": "2026-02-01"},
            {"form": "10-Q", "fp": "Q2", "end": "2026-06-30",
             "val": 35.0, "filed": "2026-08-01"}]}},
    }
    if extra:
        us_gaap.update(extra)
    return {"entityName": "Fixture Co", "facts": {"us-gaap": us_gaap}}


class TestV5EdgarEvidence(unittest.TestCase):
    """v5 Fix 2 evidence test on raw companyfacts + the
    ShortTermInvestments concept addition."""

    def test_verified_zero_evidence_passes(self):
        ev = edgarmod.verified_zero_debt_evidence(
            _fixture_facts(), cfgmod.load_all()["sources"])
        self.assertTrue(ev["eligible"])
        self.assertEqual(ev["checks"]["debt_balance_rows"], 0)
        self.assertEqual(ev["checks"]["interest_expense_rows_latest_2fy"], 0)

    def test_interest_expense_blocks_verified_zero(self):
        facts = _fixture_facts({"InterestExpense": {"units": {"USD": [
            {"form": "10-K", "fp": "FY", "start": "2025-01-01", "end": "2025-12-31",
             "val": 5.0, "filed": "2026-02-01"}]}}})
        ev = edgarmod.verified_zero_debt_evidence(facts, cfgmod.load_all()["sources"])
        self.assertFalse(ev["eligible"])
        self.assertEqual(ev["checks"]["interest_expense_rows_latest_2fy"], 1)

    def test_debt_balance_row_blocks_verified_zero(self):
        facts = _fixture_facts({"LongTermDebtNoncurrent": {"units": {"USD": [
            {"form": "10-K", "fp": "FY", "end": "2025-12-31",
             "val": 12.0, "filed": "2026-02-01"}]}}})
        ev = edgarmod.verified_zero_debt_evidence(facts, cfgmod.load_all()["sources"])
        self.assertFalse(ev["eligible"])
        self.assertEqual(ev["checks"]["debt_balance_rows"], 1)

    def test_short_term_investments_extracted(self):
        sources = cfgmod.load_all()["sources"]
        candidates = sources["concepts"]["investments_current"]
        self.assertIn("ShortTermInvestments", candidates)
        series = edgarmod.annual_series(_fixture_facts(), candidates,
                                        "investments_current")
        self.assertEqual(series[-1]["value"], 30.0)
        rows = edgarmod._unit_rows(_fixture_facts(), "ShortTermInvestments",
                                   "investments_current")
        latest = edgarmod._latest_instant(rows)
        self.assertEqual(latest["value"], 35.0)
        self.assertEqual(latest["end"], "2026-06-30")

    def test_revenue_including_assessed_tax_resolves(self):
        # CRWD-shaped (2026-11-09): CrowdStrike tags revenue ONLY as
        # RevenueFromContractWithCustomerIncludingAssessedTax — none of
        # the original three candidates exist in its companyfacts, so
        # the run extracted no revenue and failed closed entirely.
        sources = cfgmod.load_all()["sources"]
        candidates = sources["concepts"]["revenue"]
        self.assertIn("RevenueFromContractWithCustomerIncludingAssessedTax",
                      candidates)
        facts = _fixture_facts({
            "RevenueFromContractWithCustomerIncludingAssessedTax": {"units": {"USD": [
                {"form": "10-K", "fp": "FY", "start": "2024-02-01", "end": "2025-01-31",
                 "val": 3.95e9, "filed": "2025-03-10"},
                {"form": "10-K", "fp": "FY", "start": "2025-02-01", "end": "2026-01-31",
                 "val": 4.81e9, "filed": "2026-03-05"}]}}})
        series = edgarmod.annual_series(facts, candidates, "revenue")
        self.assertEqual(series[-1]["value"], 4.81e9)
        self.assertEqual(series[-1]["end"], "2026-01-31")


class TestReporting(unittest.TestCase):
    """Reporting block: report_date is the latest Item 2.02 8-K filing
    date (a newer non-earnings 8-K must NOT win); with no 2.02 8-K in
    the recent list it falls back to the latest periodic filing date.
    financials_through is the period end of the periodic filing whose
    data the run uses (the TTM end)."""

    def test_earnings_8k_wins_over_newer_non_earnings_8k(self):
        # MU-shaped: a Nov 5 8-K (Item 5.02) is newer than the Sep 30
        # earnings 8-K (Item 2.02); the 10-K period ends Sep 3.
        recent = {
            "form": ["8-K", "10-K", "8-K", "10-Q"],
            "filingDate": ["2026-11-05", "2026-10-09", "2026-09-30", "2026-06-25"],
            "reportDate": ["2026-11-02", "2026-09-03", "2026-09-30", "2026-05-28"],
            "items": ["5.02,9.01", "", "2.02,9.01", ""],
        }
        out = edgarmod.parse_reporting(recent, ttm_end="2026-09-03")
        self.assertEqual(out["report_date"], "2026-09-30")
        self.assertEqual(out["report_form"], "8-K earnings release")
        self.assertEqual(out["financials_through"], "2026-09-03")

    def test_no_earnings_8k_falls_back_to_periodic(self):
        recent = {
            "form": ["10-Q", "8-K"],
            "filingDate": ["2026-11-05", "2026-09-10"],
            "reportDate": ["2026-09-30", "2026-09-10"],
            "items": ["", "5.02,7.01,9.01"],
        }
        out = edgarmod.parse_reporting(recent, ttm_end="2026-09-30")
        self.assertEqual(out["report_date"], "2026-11-05")
        self.assertEqual(out["report_form"], "10-Q")
        self.assertEqual(out["financials_through"], "2026-09-30")


class TestV5DCF(unittest.TestCase):
    """v5 Fix 3: Blume beta adjustment + TTM-first DCF base."""

    BLUME_CFG = {"risk_free_rate_pct": 4.3, "equity_risk_premium_pct": 5.0,
                 "pre_tax_cost_of_debt_pct": 4.5, "tax_rate_pct": 15.0,
                 "beta_adjustment": "blume"}

    def test_blume_beta_math(self):
        w = build_wacc(self.BLUME_CFG, beta=3.0, market_cap=1000, total_debt=0)
        # 0.67*3.0 + 0.33 = 2.34; cost of equity 4.3 + 2.34*5 = 16.0.
        self.assertAlmostEqual(w["inputs"]["beta_raw"], 3.0)
        self.assertAlmostEqual(w["inputs"]["beta_adjusted"], 2.34)
        self.assertAlmostEqual(w["inputs"]["beta"], 2.34)
        self.assertAlmostEqual(w["cost_of_equity_pct"], 16.0)
        self.assertAlmostEqual(w["wacc_pct"], 16.0)

    def test_raw_beta_without_adjustment(self):
        cfg = {k: v for k, v in self.BLUME_CFG.items() if k != "beta_adjustment"}
        w = build_wacc(cfg, beta=3.0, market_cap=1000, total_debt=0)
        self.assertAlmostEqual(w["inputs"]["beta_adjusted"], 3.0)
        self.assertAlmostEqual(w["cost_of_equity_pct"], 19.3)

    def test_ttm_base_preferred(self):
        base, source = fcf_base_per_share(
            {"fcf_ttm": 200.0, "diluted_shares_latest_period": 50.0,
             "fcf_latest": 90.0, "diluted_shares_latest": 45.0})
        self.assertEqual(source, "ttm_owner_fcf")
        self.assertAlmostEqual(base, 4.0)

    def test_fy_base_fallback(self):
        base, source = fcf_base_per_share(
            {"fcf_ttm": None, "diluted_shares_latest_period": 50.0,
             "fcf_latest": 90.0, "diluted_shares_latest": 45.0})
        self.assertEqual(source, "latest_fy_owner_fcf_fallback")
        self.assertAlmostEqual(base, 2.0)
        self.assertEqual(fcf_base_per_share({}), (None, None))

    def test_all_section_weights_sum_to_one(self):
        scoring_cfg = cfgmod.load_all()["scoring"]
        for section in ("business", "value", "moat"):
            self.assertAlmostEqual(
                sum(s["weight"] for s in scoring_cfg[section].values()), 1.0, places=9)


class TestBetaFallbackPrecedence(unittest.TestCase):
    """v5.1 universal beta fallback: config -> computed on the fly
    (same OLS-vs-SPY method as the config betas) -> missing."""

    def test_config_beta_wins_over_computed(self):
        self.assertEqual(pricesmod.beta_precedence(1.5, 9.9), ("config", 1.5))

    def test_computed_used_when_config_absent(self):
        self.assertEqual(pricesmod.beta_precedence(None, 2.2554),
                         ("computed_on_the_fly", 2.2554))

    def test_missing_when_neither_available(self):
        self.assertEqual(pricesmod.beta_precedence(None, None), ("missing", None))

    @staticmethod
    def _series(rets, start=100.0):
        closes, value, ts = {}, start, 1_700_000_000
        closes[ts] = value
        for r in rets:
            ts += 604800
            value *= (1 + r)
            closes[ts] = value
        return closes

    def test_ols_beta_recovers_known_slope(self):
        spy_rets = [0.02, -0.015, 0.03, -0.005, 0.012, -0.02] * 12  # 72 points
        stock_rets = [2 * r + 0.001 for r in spy_rets]
        beta, n = pricesmod.ols_slope_beta(self._series(stock_rets),
                                           self._series(spy_rets))
        self.assertEqual(n, 72)
        self.assertAlmostEqual(beta, 2.0, places=3)

    def test_ols_beta_below_min_points_is_missing(self):
        rets = [0.02, -0.01] * 10  # 20 return points < 52 required
        beta, n = pricesmod.ols_slope_beta(self._series(rets), self._series(rets))
        self.assertIsNone(beta)
        self.assertEqual(n, 20)


class TestRelativeEvToSalesFallback(unittest.TestCase):
    """v5.1 universal relative-EV/S fallback: curated group first,
    broad_universe_fallback second, missing only below the >=4 gate
    on both (or when the subject has no EV/S to rank)."""

    def test_curated_group_wins_over_fallback(self):
        pct, basis = relative_ev_to_sales(10.0, [5.0, 20.0, 30.0], [1.0] * 12, 4)
        self.assertEqual(basis, "curated_group")
        self.assertIsNotNone(pct)

    def test_fallback_fires_without_curated_group(self):
        pct, basis = relative_ev_to_sales(56.17, [], [3.0, 6.0, 9.0, 12.0, 15.0], 4)
        self.assertEqual(basis, "broad_universe_fallback")
        # Subject is the most expensive of 6 -> (0 + 0.5) / 6 * 100
        self.assertAlmostEqual(pct, 8.3)

    def test_fallback_fires_when_curated_group_too_small(self):
        pct, basis = relative_ev_to_sales(10.0, [5.0], [4.0, 6.0, 8.0, 12.0], 4)
        self.assertEqual(basis, "broad_universe_fallback")
        self.assertIsNotNone(pct)

    def test_below_min_group_on_both_stays_missing(self):
        pct, basis = relative_ev_to_sales(10.0, [5.0], [7.0], 4)
        self.assertIsNone(pct)
        self.assertIsNone(basis)

    def test_subject_without_evs_stays_missing(self):
        pct, basis = relative_ev_to_sales(None, [1.0, 2.0, 3.0, 4.0],
                                          [1.0, 2.0, 3.0, 4.0], 4)
        self.assertIsNone(pct)
        self.assertIsNone(basis)


class TestMarketCapMissing(unittest.TestCase):
    """Regression (V / BRK.B): filers that report their share count
    split by share class have no company-level diluted share count in
    EDGAR companyfacts, so market cap is None. build_wacc used to
    divide that raw None by EV and kill the whole run with a TypeError
    whenever the filer also carried debt. The WACC must instead rest
    on the cost of equity alone, flagged, and evaluate() must complete
    with the market-cap-dependent value legs missing (renormalised) —
    never a crash, never an invented share count."""

    WACC_CFG = {"risk_free_rate_pct": 4.0, "equity_risk_premium_pct": 5.0,
                "pre_tax_cost_of_debt_pct": 5.0, "tax_rate_pct": 20.0}

    def test_wacc_market_cap_none_with_debt_does_not_crash(self):
        w = build_wacc(self.WACC_CFG, beta=2.0, market_cap=None, total_debt=100)
        self.assertTrue(w["market_cap_missing"])
        self.assertEqual(w["equity_weight"], 1.0)
        self.assertEqual(w["debt_weight"], 0.0)
        self.assertAlmostEqual(w["wacc_pct"], w["cost_of_equity_pct"])

    def test_wacc_market_cap_present_not_flagged(self):
        w = build_wacc(self.WACC_CFG, beta=2.0, market_cap=900, total_debt=100)
        self.assertFalse(w["market_cap_missing"])
        self.assertAlmostEqual(w["equity_weight"], 0.9)

    def test_evaluate_without_diluted_shares_completes_and_flags(self):
        cfg = cfgmod.load_all()
        cfg["tickers"]["tickers"]["VTST"] = {
            "cik": "0001403161", "name": "Visa-like Test Filer",
            "beta_source": "test fixture"}
        # Filed metrics for a profitable, debt-carrying filer — but NO
        # diluted share count anywhere (the V/BRK.B shape), so
        # add_price_metrics cannot build a market cap.
        fake_metrics = {
            "fiscal_year_end": "2025-09-30",
            "revenue_ttm": 40_000_000_000, "revenue_latest": 38_000_000_000,
            "revenue_growth_latest_pct": 10.0, "revenue_cagr_5y_pct": 8.0,
            "gross_margin_median_5y_pct": 75.0,
            "operating_margin_median_5y_pct": 60.0,
            "fcf_margin_median_5y_pct": 45.0,
            "gross_margin_stdev_5y_pts": 1.0,
            "total_debt_latest": 30_000_000_000,
            "leverage_debt_to_revenue_ttm": 0.75,
            "fcf_ttm": 18_000_000_000, "fcf_latest": 17_000_000_000,
            "roic_median_5y_pct": 30.0,
        }
        filing = {"filing": {"filed": "2025-11-18",
                             "accession": "0001403161-25-000001",
                             "primary_document": "v-20250930.htm"},
                  "concentration": {}}
        price = {"last_close": 300.0, "high_52w": 320.0, "low_52w": 250.0,
                 "source": "test fixture"}
        beta_res = {"value": 1.1, "origin": "config", "points": 100,
                    "source": "test fixture"}
        with mock.patch.object(runmod.edgar, "get_company_metrics",
                               return_value=("VISA-LIKE TEST FILER", {})), \
             mock.patch.object(runmod.metmod, "compute_metrics",
                               return_value=dict(fake_metrics)), \
             mock.patch.object(runmod.prices, "get_price", return_value=price), \
             mock.patch.object(runmod.prices, "resolve_beta", return_value=beta_res), \
             mock.patch.object(runmod.filings, "get_latest_10k_concentration",
                               return_value=filing), \
             mock.patch.object(runmod.edgar, "post_period_earnings_release",
                               return_value=None), \
             mock.patch.object(runmod.edgar, "reporting_info", return_value=None):
            result = runmod.evaluate("VTST", cfg)
        dq = result["data_quality"]
        self.assertTrue(dq["market_cap_missing"])
        self.assertEqual(dq["dcf_missing_reason"], "fcf_base_missing")
        self.assertTrue(result["dcf"]["wacc"]["market_cap_missing"])
        self.assertEqual(result["dcf"]["wacc"]["equity_weight"], 1.0)
        self.assertIsNone(result["dcf"].get("fair_value_per_share"))
        for leg in ("trailing_pe", "price_to_sales", "ev_to_sales",
                    "fcf_yield_latest_pct", "dcf_upside_pct"):
            self.assertIn(leg, dq["missing_value_metrics"])
        self.assertIsNotNone(result["scores"]["business"]["total"])


def _norm_series(revenues, capexes, ocfs, sbcs, shares=10.0):
    """Annual series whose TTM bridge resolves to the latest FY (the
    _annual_period_rows pattern), for the v5.2 normalisation tests."""
    ends = [f"{y}-12-31" for y in range(2021, 2021 + len(revenues))]
    return {
        "revenue": [{"end": e, "value": v} for e, v in zip(ends, revenues)],
        "capex": [{"end": e, "value": v} for e, v in zip(ends, capexes)],
        "operating_cash_flow": [{"end": e, "value": v} for e, v in zip(ends, ocfs)],
        "share_based_compensation": [{"end": e, "value": v} for e, v in zip(ends, sbcs)],
        "diluted_shares": [{"end": e, "value": shares} for e in ends],
        "_periods": _annual_period_rows({
            "revenue": revenues[-1], "capex": capexes[-1],
            "operating_cash_flow": ocfs[-1],
            "share_based_compensation": sbcs[-1], "diluted_shares": shares}),
        "_balance_latest": {},
        "_debt_instants": {"totals": {}, "current": {}, "noncurrent": {}},
    }


class TestV52NormalizedCapexDCF(unittest.TestCase):
    """v5.2 (SPEC_V5.2.md): normalised-capex DCF base, fade floor,
    3% terminal growth. The base rule: normalised capex = min(actual
    TTM capex, median of the last-5-FY filed capex intensities x TTM
    revenue); primary DCF base = TTM OCF - normalised capex - SBC."""

    def test_normalization_binds_meta_shaped(self):
        # Spec test 1: capex intensities 10/20/15/25/40% (median 20%)
        # with a latest-year spike to 40 on revenue of 100.
        m = compute_metrics(
            _norm_series([100.0] * 5, [10.0, 20.0, 15.0, 25.0, 40.0],
                         [50.0, 55.0, 55.0, 58.0, 60.0], [5.0] * 5), 15.0)
        self.assertEqual(m["capex_intensity_observations"], 5)
        self.assertAlmostEqual(m["capex_intensity_median_5y_pct"], 20.0)
        # Normalised capex = min(40, 20% x 100) = 20; trim = 20.
        self.assertAlmostEqual(m["normalized_capex_ttm"], 20.0)
        self.assertTrue(m["capex_normalized_binds"])
        self.assertAlmostEqual(m["capex_trimmed_usd"], 20.0)
        # Actual owner FCF = 60 - 40 - 5 = 15; normalised = 60 - 20 - 5 = 35.
        self.assertAlmostEqual(m["fcf_ttm"], 15.0)
        self.assertAlmostEqual(m["fcf_normalized_ttm"], 35.0)
        bases = dcf_bases(m)
        self.assertTrue(bases["normalized_applied"])
        self.assertFalse(bases["normalization_unavailable"])
        self.assertEqual(bases["primary_source"], "ttm_owner_fcf_normalized_capex")
        self.assertAlmostEqual(bases["primary_usd"], 35.0)
        self.assertAlmostEqual(bases["primary_per_share"], 3.5)
        self.assertAlmostEqual(bases["actual_usd"], 15.0)
        self.assertAlmostEqual(bases["actual_per_share"], 1.5)

    def test_cap_holds_asset_light(self):
        # Spec test 2: median intensity (11%) x revenue (100) = 11 >
        # actual capex 10 — the cap binds at actual spend, no trim,
        # primary base is the actual base.
        m = compute_metrics(
            _norm_series([100.0] * 5, [10.0, 12.0, 11.0, 13.0, 10.0],
                         [50.0, 55.0, 55.0, 58.0, 60.0], [5.0] * 5), 15.0)
        self.assertAlmostEqual(m["capex_intensity_median_5y_pct"], 11.0)
        self.assertAlmostEqual(m["normalized_capex_ttm"], 10.0)
        self.assertFalse(m["capex_normalized_binds"])
        self.assertEqual(m["capex_trimmed_usd"], 0.0)
        self.assertAlmostEqual(m["fcf_normalized_ttm"], m["fcf_ttm"])
        bases = dcf_bases(m)
        self.assertFalse(bases["normalized_applied"])
        self.assertFalse(bases["normalization_unavailable"])
        self.assertEqual(bases["primary_source"], "ttm_owner_fcf")
        self.assertAlmostEqual(bases["primary_per_share"], 4.5)
        self.assertAlmostEqual(bases["primary_per_share"], bases["actual_per_share"])

    def test_history_under_3_years_unavailable(self):
        # Spec test 3: capex filed in only 2 of the last 5 fiscal
        # years -> median unavailable, primary base = actual, flagged.
        series = _norm_series([100.0] * 5, [10.0, 20.0, 15.0, 25.0, 40.0],
                              [50.0, 55.0, 55.0, 58.0, 60.0], [5.0] * 5)
        series["capex"] = [{"end": "2024-12-31", "value": 30.0},
                            {"end": "2025-12-31", "value": 40.0}]
        m = compute_metrics(series, 15.0)
        self.assertEqual(m["capex_intensity_observations"], 2)
        self.assertIsNone(m["capex_intensity_median_5y_pct"])
        self.assertIsNone(m["normalized_capex_ttm"])
        self.assertIsNone(m["fcf_normalized_ttm"])
        bases = dcf_bases(m)
        self.assertTrue(bases["normalization_unavailable"])
        self.assertFalse(bases["normalized_applied"])
        self.assertEqual(bases["primary_source"], "ttm_owner_fcf")
        self.assertAlmostEqual(bases["primary_usd"], 15.0)

    def _evaluate_with_metrics(self, fake_metrics):
        cfg = cfgmod.load_all()
        cfg["tickers"]["tickers"]["NTST"] = {
            "cik": "0000000001", "name": "Normalization Test Filer",
            "beta_source": "test fixture"}
        filing = {"filing": {"filed": "2026-02-01",
                             "accession": "0000000001-26-000001",
                             "primary_document": "ntst-20251231.htm"},
                  "concentration": {}}
        price = {"last_close": 100.0, "high_52w": 120.0, "low_52w": 80.0,
                 "source": "test fixture"}
        beta_res = {"value": 1.0, "origin": "config", "points": 100,
                    "source": "test fixture"}
        with mock.patch.object(runmod.edgar, "get_company_metrics",
                               return_value=("NORMALIZATION TEST FILER", {})), \
             mock.patch.object(runmod.metmod, "compute_metrics",
                               return_value=dict(fake_metrics)), \
             mock.patch.object(runmod.prices, "get_price", return_value=price), \
             mock.patch.object(runmod.prices, "resolve_beta", return_value=beta_res), \
             mock.patch.object(runmod.filings, "get_latest_10k_concentration",
                               return_value=filing), \
             mock.patch.object(runmod.edgar, "post_period_earnings_release",
                               return_value=None), \
             mock.patch.object(runmod.edgar, "reporting_info", return_value=None):
            return runmod.evaluate("NTST", cfg)

    _BASE_FAKE = {
        "fiscal_year_end": "2025-12-31",
        "revenue_ttm": 1000.0, "revenue_latest": 1000.0,
        "revenue_cagr_5y_pct": 10.0,
        "diluted_shares_latest_period": 100.0, "diluted_shares_latest": 100.0,
        "capex_ttm": 400.0, "normalized_capex_ttm": 300.0,
        "capex_trimmed_usd": 100.0, "capex_normalized_binds": True,
        "capex_intensity_median_5y_pct": 30.0, "capex_intensity_observations": 5,
    }

    def test_normalized_base_nonpositive_dcf_missing(self):
        # Spec test 4: the normalised base is still non-positive ->
        # the DCF leg stays missing (non_positive_fcf_base), and the
        # actual base is worse, so no stress floor either.
        fake = dict(self._BASE_FAKE, fcf_ttm=-500.0, fcf_latest=-450.0,
                    fcf_normalized_ttm=-100.0)
        result = self._evaluate_with_metrics(fake)
        dq = result["data_quality"]
        self.assertEqual(dq["dcf_missing_reason"], "non_positive_fcf_base")
        self.assertTrue(dq["capex_normalized"])
        self.assertIsNone(result["dcf"].get("fair_value_per_share"))
        self.assertIsNone(result["dcf"]["stress_floor"])
        self.assertEqual(result["dcf"]["stress_floor_missing_reason"],
                         "non_positive_fcf_base")
        self.assertIn("dcf_upside_pct", dq["missing_value_metrics"])

    def test_actual_negative_normalized_positive_proceeds(self):
        # Spec §2: actual base <= 0 but normalised base > 0 -> the DCF
        # proceeds on the normalised base; the stress floor is missing.
        fake = dict(self._BASE_FAKE, fcf_ttm=-50.0, fcf_latest=-40.0,
                    fcf_normalized_ttm=150.0)
        result = self._evaluate_with_metrics(fake)
        dq = result["data_quality"]
        self.assertIsNone(dq["dcf_missing_reason"])
        self.assertTrue(dq["capex_normalized"])
        self.assertEqual(result["dcf"]["primary_base"], "normalized_capex")
        self.assertEqual(result["dcf"]["fcf_base_source"],
                         "ttm_owner_fcf_normalized_capex")
        self.assertAlmostEqual(result["dcf"]["fcf_per_share_base"], 1.5)
        self.assertIsNotNone(result["dcf"].get("fair_value_per_share"))
        self.assertNotIn("dcf_upside_pct", dq["missing_value_metrics"])
        self.assertIsNone(result["dcf"]["stress_floor"])
        self.assertEqual(result["dcf"]["stress_floor_missing_reason"],
                         "non_positive_fcf_base")

    def test_fade_path_floor_and_terminal(self):
        # Spec test 5: g0 above the 15% floor fades only to the floor;
        # g0 at/below it fades to terminal growth (3%).
        cfg = {"projection_years": 5, "terminal_growth_pct": 3.0,
               "growth": {"floor_pct": 0.0, "cap_pct": 25.0,
                          "fade_floor_pct": 15.0}}
        self.assertEqual(growth_path(cfg, 18.51),
                         [18.51, 17.808, 17.106, 16.404, 15.702])
        self.assertEqual(growth_path(cfg, 10.0), [10.0, 8.6, 7.2, 5.8, 4.4])
        # Config carries the v5.2 values (Change 2 + Change 3).
        live = cfgmod.load_all()["dcf"]["dcf"]
        self.assertEqual(live["terminal_growth_pct"], 3.0)
        self.assertEqual(live["growth"]["fade_floor_pct"], 15.0)

    def test_terminal_value_gordon_3pct(self):
        # Spec test 6: flat FCF/share 100, WACC 10%, terminal 3%:
        # PV = 5y annuity + Gordon TV discounted at year 5.
        out = fair_value(100, [0, 0, 0, 0, 0], 10, 3)
        pv_proj = sum(100 / 1.1 ** i for i in range(1, 6))
        pv_tv = (100 * 1.03 / (0.10 - 0.03)) / 1.1 ** 5
        self.assertAlmostEqual(out["pv_of_projected_fcf"], pv_proj, delta=0.02)
        self.assertAlmostEqual(out["pv_of_terminal_value"], pv_tv, delta=0.02)
        self.assertAlmostEqual(out["fair_value_per_share"], pv_proj + pv_tv,
                               delta=0.05)


MU_10Q_SNIPPET = (
    "In 2018, we announced that our Board of Directors authorized a stock repurchase "
    "program for the discretionary repurchase of up to $10 billion (the 2018 authorization) "
    "of our outstanding common stock through open-market purchases. "
    "The repurchase authorization has no expiration date, does not obligate us to acquire "
    "any common stock, and is subject to market conditions and restrictions. "
    "Through May 28, 2026, we had repurchased an aggregate of $7.84 billion under the authorization. "
    "During the quarter ended May 28, 2026, we did not repurchase any common stock under the "
    "authorization and as of May 28, 2026, $2.16 billion of the authorization remained available "
    "for the repurchase of our common stock. "
    "Period Total number of shares purchased Average price paid per share Total number of shares "
    "purchased as part of publicly announced plans or programs Approximate dollar value of shares "
    "that may yet be purchased under publicly announced plans or programs (in millions) "
    "February 27, 2026 - March 26, 2026 - $ - - March 27, 2026 - April 23, 2026 912 465.66 - "
    "April 24, 2026 - May 28, 2026 - - - 912 $ 465.66 - $ 2,156 ITEM 5. OTHER INFORMATION"
)

MU_10K_INCREASE_SNIPPET = (
    "In 2018, we announced that our Board of Directors authorized a stock repurchase program "
    "for the discretionary repurchase of up to $10 billion (the 2018 authorization) of our "
    "outstanding common stock. "
    "On October 8, 2026, our Board of Directors authorized an increase in the amount authorized "
    "under this stock repurchase program for a total authorization of $35.16 billion. "
    "Any repurchases under the stock repurchase program will be made from December 9, 2026 in "
    "accordance with our CHIPS Act direct funding agreements. "
    "The total amount authorized under the stock repurchase program is based on the "
    "approximately $2.16 billion that remained available for repurchase under the 2018 "
    "authorization, plus 100% of our adjusted free cash flow, less proceeds of CHIPS Act grant "
    "incentives and less dividends paid, for the quarter ended September 3, 2026. "
    "Our stock repurchase program and the new authorization have no expiration date, do not "
    "obligate us to acquire any common stock, and are subject to market conditions and "
    "restrictions applicable under our CHIPS Act direct funding agreements. "
    "Through September 3, 2026, we had repurchased an aggregate of $ 7.84 billion under the "
    "2018 authorization. "
    "We did not repurchase any common stock under the 2018 authorization in the fourth quarter "
    "of 2026, and as of September 3, 2026, $2.16 billion of the 2018 authorization remained "
    "available for the repurchase of our common stock."
)


class TestV53Buybacks(unittest.TestCase):
    def test_mu_10q_narrative_parse(self):
        # Spec test 1 (narrative path): remaining $2.16B, used $7.84B,
        # program $10B, no-expiry conditions captured as history.
        out = extract_buyback(MU_10Q_SNIPPET)
        self.assertEqual(out["remaining_usd"], 2.16e9)
        self.assertEqual(out["remaining_basis"], "narrative_remaining_at_period_end")
        self.assertEqual(out["remaining_as_of"], "May 28, 2026")
        self.assertEqual(out["cumulative_used_usd"], 7.84e9)
        self.assertEqual(out["authorization_total_usd"], 10e9)
        types = [h["type"] for h in out["history"]]
        self.assertIn("program_authorized", types)
        self.assertIn("cumulative_used", types)
        self.assertIn("execution_note", types)
        self.assertNotIn("buyback_remaining_not_disclosed", out["flags"])

    def test_item_703_table_fallback(self):
        # Spec test 1 (table path): narrative removed -> the Item 703
        # final column governs; the 465.66 withholding avg price and
        # the 912 withheld shares are never the remaining figure.
        text = MU_10Q_SNIPPET.replace(
            "During the quarter ended May 28, 2026, we did not repurchase any common stock under the "
            "authorization and as of May 28, 2026, $2.16 billion of the authorization remained available "
            "for the repurchase of our common stock. ", "")
        out = extract_buyback(text)
        self.assertEqual(out["remaining_usd"], 2156e6)
        self.assertEqual(out["remaining_basis"], "item_703_table_may_yet_be_purchased")

    def test_post_period_increase_supersedes(self):
        # Spec test 2: the Oct 8, 2026 increase (with its construction
        # sentence) supersedes the period-end $2.16B; effective-from
        # Dec 9, 2026 captured.
        out = extract_buyback(MU_10K_INCREASE_SNIPPET)
        self.assertEqual(out["remaining_usd"], 35.16e9)
        self.assertEqual(out["authorization_date"], "October 8, 2026")
        self.assertEqual(out["effective_from"], "December 9, 2026")
        self.assertIsNotNone(out["construction_quote"])
        types = [h["type"] for h in out["history"]]
        self.assertIn("authorization_increased", types)
        self.assertIn("remaining_at_period_end", types)

    def test_increase_without_construction_fails_closed(self):
        # Spec test 3 (the trap): an increase figure with NO
        # construction sentence is ambiguous total-vs-remaining and
        # must not be scored; the period-end narrative still governs.
        text = MU_10K_INCREASE_SNIPPET.replace(
            "The total amount authorized under the stock repurchase program is based on the "
            "approximately $2.16 billion that remained available for repurchase under the 2018 "
            "authorization, plus 100% of our adjusted free cash flow, less proceeds of CHIPS Act grant "
            "incentives and less dividends paid, for the quarter ended September 3, 2026. ", "")
        out = extract_buyback(text)
        self.assertIn("buyback_total_remaining_ambiguous", out["flags"])
        self.assertEqual(out["remaining_usd"], 2.16e9)
        self.assertEqual(out["remaining_basis"], "narrative_remaining_at_period_end")

    def test_no_disclosure_fails_closed(self):
        out = extract_buyback("Revenue grew strongly this year. Cash was $5 billion.")
        self.assertIsNone(out["remaining_usd"])
        self.assertIn("buyback_remaining_not_disclosed", out["flags"])

    def test_share_denominated_not_scored(self):
        # Spec test 4: share-denominated authorization is stored as
        # shares, flagged, and never converted at market price.
        text = ("In 2024, our Board of Directors authorized a stock repurchase program "
                "for the discretionary repurchase of up to 50,000,000 shares of our "
                "outstanding common stock.")
        out = extract_buyback(text)
        self.assertIsNone(out["remaining_usd"])
        self.assertIn("buyback_remaining_share_denominated", out["flags"])

    def test_anchors_interpolation_mu(self):
        # Spec test 5a: 4.458% -> 6.048 on the config anchors.
        cfg = cfgmod.load_all()["scoring"]["value"]["forward_buyback_yield_pct"]
        self.assertAlmostEqual(interpolate(4.458, cfg["anchors"]), 6.048, places=2)
        self.assertEqual(cfg["weight"], 0.05)

    def test_overvaluation_cap(self):
        # Spec test 5b: DCF upside -60% with a 15% yield caps at 4.0.
        cfg = cfgmod.load_all()["scoring"]
        res = score_section(cfg["value"], {"forward_buyback_yield_pct": 15.0})
        self.assertAlmostEqual(res["subscores"]["forward_buyback_yield_pct"]["score"], 9.25)
        capped = apply_buyback_overvaluation_cap(res, -60.0)
        self.assertTrue(capped)
        self.assertEqual(res["subscores"]["forward_buyback_yield_pct"]["score"], 4.0)
        self.assertEqual(res["subscores"]["forward_buyback_yield_pct"]["capped_from"], 9.25)
        res2 = score_section(cfg["value"], {"forward_buyback_yield_pct": 15.0})
        self.assertFalse(apply_buyback_overvaluation_cap(res2, 43.08))

    def test_abnb_available_to_repurchases_and_new_program(self):
        # ABNB Q3 FY26 10-Q shape (reported as a dashboard bug
        # 2026-11-09): remaining stated as "available to repurchase",
        # plus a post-period NEW $12B program (Nov 3, 2026) with the
        # prior $6.0B program "remains in effect with $2.7 billion
        # available" -> scored remaining is 12.0 + 2.7 = $14.7B.
        text = (
            "In August 2025, the Company announced that its board of directors approved a new "
            "share repurchase program with authorization to purchase up to $ 6.0 billion of the "
            "Company's Class A common stock. "
            "As of September 30, 2026, the Company had $ 2.7 billion available to repurchase "
            "shares of Class A common stock under its share repurchase program. "
            "During the three months ended September 30, 2026, we repurchased 4.4 million "
            "shares of Class A common stock for $712 million, leaving $2.7 billion available "
            "to repurchase under our share repurchase program. "
            "On November 3, 2026, the Company's board of directors approved a new share "
            "repurchase program with authorization to purchase up to an additional $ 12 "
            "billion of the Company's Class A common stock at management's discretion. "
            "The Company's prior share repurchase program, which authorized $ 6.0 billion, "
            "remains in effect with $ 2.7 billion available as of September 30, 2026."
        )
        out = extract_buyback(text)
        self.assertEqual(out["remaining_usd"], 14.7e9)
        self.assertEqual(out["remaining_basis"],
                         "post_period_new_program_plus_prior_program_remaining")
        self.assertEqual(out["authorization_date"], "November 3, 2026")
        self.assertEqual(out["authorization_total_usd"], 18.0e9)
        types = [h["type"] for h in out["history"]]
        self.assertIn("program_authorized", types)
        self.assertIn("authorization_new_program", types)
        self.assertIn("remaining_at_period_end", types)
        prog = next(h for h in out["history"] if h["type"] == "program_authorized")
        self.assertEqual(prog["amount_usd"], 6.0e9)

    def test_item_703_title_case_header(self):
        # ABNB title-cases the Item 703 header; the fallback search is
        # case-insensitive and still lands on the final-column total.
        text = ("Period Total Number of Shares Purchased Average Price Paid per Share "
                "Total Number of Shares Purchased as Part of Publicly Announced Plans or "
                "Programs Approximate Dollar Value of Shares that May Yet Be Purchased "
                "Under Publicly Announced Plans or Programs (in millions) "
                "July 1, 2026 - July 31, 2026 100 150.00 100 $ 2,700 "
                "August 1, 2026 - August 31, 2026 - $ - - - "
                "Total 100 $ 2,700 ITEM 2. OTHER")
        out = extract_buyback(text)
        self.assertEqual(out["remaining_usd"], 2700e6)
        self.assertEqual(out["remaining_basis"], "item_703_table_may_yet_be_purchased")

    def test_derived_eps_fallback_for_missing_eps_concept(self):
        # ABNB shape: no EarningsPerShareDiluted in companyfacts at
        # all, but net income and diluted shares are filed -> trailing
        # P/E is computed on derived EPS and flagged as derived.
        metrics = {"net_income_ttm": 2.77e9, "net_income_latest": 2.511e9,
                   "diluted_shares_latest_period": 597e6, "diluted_shares_latest": 623e6,
                   "revenue_ttm": 13.933e9, "net_cash_latest": 10.09e9,
                   "fcf_ttm": 3.204e9}
        price = {"last_close": 192.79, "high_52w": 192.79, "low_52w": 111.54}
        out = add_price_metrics(dict(metrics), price)
        self.assertTrue(out["eps_derived_from_net_income"])
        self.assertAlmostEqual(out["eps_used_for_trailing_pe"], 2.77e9 / 597e6, places=4)
        self.assertAlmostEqual(out["trailing_pe"], 192.79 / (2.77e9 / 597e6), places=2)
        # Filed EPS always wins over the derived figure.
        metrics2 = dict(metrics, eps_diluted_ttm=5.0)
        out2 = add_price_metrics(metrics2, price)
        self.assertFalse(out2["eps_derived_from_net_income"])
        self.assertEqual(out2["trailing_pe"], 192.79 / 5.0)
        # Derived loss still trips the negative-P/E guard.
        metrics3 = dict(metrics, net_income_ttm=-1.0e9)
        out3 = add_price_metrics(metrics3, price)
        self.assertIsNone(out3["trailing_pe"])
        self.assertTrue(out3["negative_trailing_pe_not_scored"])

    def test_mu_value_total_matches_spec(self):
        # Spec test 7 (arithmetic, on the V5.2 run's own sub-scores):
        # V5.3 Value for MU = 6.88 with Business/Moat untouched.
        subs = {"trailing_pe": (9.28, 8.287), "price_to_sales": (5.92, 4.219),
                "ev_to_sales": (5.41, 4.561), "fcf_yield_latest_pct": (7.31, 8.480),
                "dcf_upside_pct": (43.08, 7.585), "discount_from_52w_high_pct": (-43.14, 7.314),
                "relative_ev_to_sales_percentile": (72.2, 7.220)}
        cfg = cfgmod.load_all()["scoring"]["value"]
        metrics = {k: v[0] for k, v in subs.items()}
        metrics["forward_buyback_yield_pct"] = 4.458
        res = score_section(cfg, metrics)
        for k, (_, sc) in subs.items():
            self.assertAlmostEqual(res["subscores"][k]["score"], sc, places=2, msg=k)
        self.assertAlmostEqual(res["subscores"]["forward_buyback_yield_pct"]["score"],
                               6.048, places=2)
        self.assertAlmostEqual(res["total"], 6.88, places=2)


class TestBuybackExtractorR2(unittest.TestCase):
    """Extractor revision 2 (rules R1–R8, 2026-11-09) regression tests
    on real filings. Fixtures are trimmed excerpts of the filings of
    record (the Item 703 section plus the narrative carrying the
    figure); the expected values are the filings' own. The nominal
    figures the first extractor produced on these same filings —
    $209 / $7.20 / $256 — are the failures under test."""

    FX = Path(__file__).resolve().parent / "fixtures"

    def _extract(self, name, period_end):
        html_doc = (self.FX / name).read_text(encoding="utf-8")
        return extract_buyback(strip_html(html_doc), raw_html=html_doc,
                               period_end=period_end)

    def test_nvda_billions_table_and_asof_rule(self):
        # NVDA Q2 FY27 10-Q (filed 2026-08-26, period ended July 26,
        # 2026): Item 2 table printed "(In billions)", Total-row
        # remainder blank, Note 12 states the as-of current
        # authorization (R5, validated against the table's final
        # cell). Expected $99.3B — NOT the $235B press figure: no
        # Q3 10-Q and no 8-K exists on the Nov 6 basis, and the
        # extractor never reaches outside periodic filings.
        out = self._extract("nvda_q2fy27_10q.html", "2026-07-26")
        self.assertEqual(out["remaining_usd"], 99.3e9)
        self.assertEqual(out["remaining_basis"],
                         "narrative_asof_current_authorization")
        self.assertEqual(out["remaining_as_of"], "July 26, 2026")
        self.assertNotIn("buyback_remaining_extraction_implausible", out["flags"])

    def test_nvda_flattened_text_only(self):
        # The bounded flattened-text table parse (no raw HTML) must
        # not be poisoned by the post-table prose either (the $209M
        # share-issuance line follows the table in the filing).
        html_doc = (self.FX / "nvda_q2fy27_10q.html").read_text(encoding="utf-8")
        out = extract_buyback(strip_html(html_doc))
        self.assertEqual(out["remaining_usd"], 99.3e9)

    def test_cat_authorization_phrasing(self):
        # CAT Q3 2026 10-Q (filed 2026-11-04): remaining is phrased
        # against "the 2024 Authorization" — the availability sentence
        # carries no 'repurchas' string, so the old gate discarded it
        # and the fallback returned the footnote's bare 7.2 with no
        # unit. Expected $7.192B (table and MD&A agree exactly).
        out = self._extract("cat_q3_2026_10q.html", "2026-09-30")
        self.assertEqual(out["remaining_usd"], 7.192e9)
        self.assertEqual(out["authorization_total_usd"], 20.0e9)
        self.assertNotIn("buyback_remaining_extraction_implausible", out["flags"])

    def test_aapl_multi_program_table_total(self):
        # AAPL FY26 10-K (filed 2026-11-03): Item 5 table in millions,
        # remainder only in the Total row — $114,004M, reconciling
        # with footnote (1)'s decomposition ($14.0B left on the May
        # 2025 program + the undrawn $100B April 2026 program, R6).
        # The table total governs.
        out = self._extract("aapl_fy26_10k.html", "2026-09-26")
        self.assertEqual(out["remaining_usd"], 114.004e9)
        self.assertEqual(out["remaining_basis"],
                         "item_703_table_may_yet_be_purchased")
        types = [h["type"] for h in out["history"]]
        self.assertIn("remaining_multi_program_reconciled", types)

    def test_floor_test_rejects_nominal_extraction(self):
        # R8 guard: a sub-$1M 'remaining' against > $100M of
        # repurchase activity in the same filing is an extraction
        # failure — the leg goes missing with the implausible flag,
        # never scored at the 2.00 anchor floor, and the failure is
        # not misreported as a non-disclosure.
        text = ("During the quarter, the Company repurchased $2.4 billion of its "
                "common stock under the repurchase program. As of June 30, 2026, "
                "$0.4 million remained available under the repurchase program.")
        out = extract_buyback(text)
        self.assertIsNone(out["remaining_usd"])
        self.assertIn("buyback_remaining_extraction_implausible", out["flags"])
        self.assertNotIn("buyback_remaining_not_disclosed", out["flags"])

    def test_post_period_addition_shape(self):
        # R7: 'approved an additional $X in share repurchase
        # authorization' after period end adds to the period-end
        # remaining. (The in-period form is a ledger event only —
        # covered by the NVDA fixture, whose May 2026 +$80B must not
        # supersede the July 26 figure.)
        text = ("In 2024, the Board authorized a share repurchase program of up to "
                "$10.0 billion. As of September 30, 2026, $2.0 billion remained "
                "available under the share repurchase program. On November 5, 2026, "
                "the Board approved an additional $5.0 billion in share repurchase "
                "authorization, without expiration.")
        out = extract_buyback(text)
        self.assertEqual(out["remaining_usd"], 7.0e9)
        self.assertEqual(out["remaining_basis"],
                         "post_period_increase_added_to_period_end_remaining")

    def test_table_scale_inferred_from_narrative(self):
        # R8 unit-consistency: table cells printed in full dollars
        # under a '(in thousands)' caption — the narrative's explicit
        # unit word identifies the true scale, the scale is corrected
        # and flagged, and the figures then agree.
        html_doc = ("<p>As of June 30, 2026, $5.0 billion remained available under "
                    "the share repurchase program.</p>"
                    "<table><tr><td>Period</td><td>Total Number of Shares Purchased</td>"
                    "<td>Average Price Paid per Share</td><td>Total Number of Shares "
                    "Purchased as Part of Publicly Announced Plans or Programs</td>"
                    "<td>Approximate Dollar Value of Shares that May Yet Be Purchased "
                    "Under the Plans or Programs (in thousands)</td></tr>"
                    "<tr><td>April 1, 2026 - June 30, 2026</td><td>1,000</td>"
                    "<td>$ 50.00</td><td>1,000</td><td>$ 5,000</td></tr></table>")
        out = extract_buyback(strip_html(html_doc), raw_html=html_doc)
        self.assertEqual(out["remaining_usd"], 5.0e9)
        self.assertIn("buyback_table_scale_inferred_from_narrative", out["flags"])

    def test_asof_authorization_unvalidated_fails_closed(self):
        # R5 with neither a table nor a buildable ledger to validate
        # against: the as-of figure is recorded, flagged, never scored.
        text = ("As of June 30, 2026, we were authorized to repurchase up to "
                "$40.0 billion of our common stock.")
        out = extract_buyback(text)
        self.assertIsNone(out["remaining_usd"])
        self.assertIn("buyback_asof_authorization_unvalidated", out["flags"])


def _inline_doc(ni_text, facts, start="2025-01-01", end="2025-12-31"):
    """Minimal inline-XBRL document: one undimensioned context (c0) plus
    one class-dimensioned context per member (cA, cB, ...). `facts` is a
    list of (concept, context_id, text_value, scale). Net income uses
    context c0 and scale 6 (millions)."""
    members = sorted({c for _, c, _, _ in facts if c != "c0"})
    ctxs = [f'<xbrli:context id="c0"><xbrli:period><xbrli:startDate>{start}</xbrli:startDate>'
            f'<xbrli:endDate>{end}</xbrli:endDate></xbrli:period></xbrli:context>']
    for m in members:
        ctxs.append(
            f'<xbrli:context id="{m}"><xbrli:period><xbrli:startDate>{start}</xbrli:startDate>'
            f'<xbrli:endDate>{end}</xbrli:endDate></xbrli:period><xbrli:segment>'
            f'<xbrldi:explicitMember dimension="us-gaap:StatementClassOfStockAxis">'
            f'us-gaap:CommonClass{m[-1]}Member</xbrldi:explicitMember></xbrli:segment>'
            f'</xbrli:context>')
    body = [f'<ix:nonFraction name="us-gaap:NetIncomeLoss" contextRef="c0" '
            f'scale="6">{ni_text}</ix:nonFraction>']
    for concept, cref, val, scale in facts:
        body.append(f'<ix:nonFraction name="us-gaap:{concept}" contextRef="{cref}" '
                    f'scale="{scale}">{val}</ix:nonFraction>')
    return ("<html><body><div style=\"display:none\"><ix:header>"
            + "".join(ctxs) + "</ix:header></div>" + "".join(body) + "</body></html>")


class TestClassShareFallback(unittest.TestCase):
    """Generic multi-class diluted-shares fallback (class_shares.py):
    a combined share count is recovered from the filing ONLY when it
    reproduces a reported diluted EPS from filed net income."""

    def test_member_as_converted_validates_sum_rejected(self):
        # Visa-shaped: Class A 1,000M is the combined as-converted count
        # (NI $10.0B / 1,000M = $10.00 = reported Class A diluted EPS);
        # Class B is a standalone class (EPS $20.00). The naive sum
        # (1,100M -> $9.09) reproduces no reported EPS and must NOT win.
        doc = _inline_doc("10,000", [
            ("WeightedAverageNumberOfDilutedSharesOutstanding", "cA", "1,000", "6"),
            ("EarningsPerShareDiluted", "cA", "10.00", "0"),
            ("WeightedAverageNumberOfDilutedSharesOutstanding", "cB", "100", "6"),
            ("EarningsPerShareDiluted", "cB", "20.00", "0")])
        out = classsharesmod.extract_class_diluted_shares(doc)
        self.assertEqual(out["annual"], [{"end": "2025-12-31", "value": 1.0e9}])
        self.assertEqual(out["validations"][0]["method"], "member_as_converted")
        self.assertEqual(out["validations"][0]["member"], "CommonClassAMember")

    def test_equal_eps_sum_validates(self):
        # Equal-participation two-class filer: both classes report the
        # same diluted EPS $5.00; neither class alone reproduces it,
        # the sum (600M + 400M; NI $5.0B / 1,000M = $5.00) does.
        doc = _inline_doc("5,000", [
            ("WeightedAverageNumberOfDilutedSharesOutstanding", "cA", "600", "6"),
            ("EarningsPerShareDiluted", "cA", "5.00", "0"),
            ("WeightedAverageNumberOfDilutedSharesOutstanding", "cB", "400", "6"),
            ("EarningsPerShareDiluted", "cB", "5.00", "0")])
        out = classsharesmod.extract_class_diluted_shares(doc)
        self.assertEqual(out["annual"], [{"end": "2025-12-31", "value": 1.0e9}])
        self.assertEqual(out["validations"][0]["method"], "sum_of_classes")

    def test_no_validation_stays_missing(self):
        doc = _inline_doc("5,000", [
            ("WeightedAverageNumberOfDilutedSharesOutstanding", "cA", "600", "6"),
            ("EarningsPerShareDiluted", "cA", "7.00", "0"),
            ("WeightedAverageNumberOfDilutedSharesOutstanding", "cB", "400", "6"),
            ("EarningsPerShareDiluted", "cB", "9.00", "0")])
        out = classsharesmod.extract_class_diluted_shares(doc)
        self.assertEqual(out["annual"], [])
        self.assertEqual(out["periods"], [])

    def test_conflicting_hypotheses_stay_missing(self):
        # Undimensioned count (900M, EPS $10.00) and Class A count
        # (1,000M, EPS $9.00) EACH reproduce an EPS from NI $9.0B but
        # imply different totals — ambiguous, fail closed.
        doc = _inline_doc("9,000", [
            ("WeightedAverageNumberOfDilutedSharesOutstanding", "c0", "900", "6"),
            ("EarningsPerShareDiluted", "c0", "10.00", "0"),
            ("WeightedAverageNumberOfDilutedSharesOutstanding", "cA", "1,000", "6"),
            ("EarningsPerShareDiluted", "cA", "9.00", "0")])
        out = classsharesmod.extract_class_diluted_shares(doc)
        self.assertEqual(out["annual"], [])

    def test_agreeing_hypotheses_accept_the_shared_count(self):
        # Undimensioned and Class A state the SAME combined count and
        # EPS (statements vs EPS-note presentation): one answer, accept.
        doc = _inline_doc("10,000", [
            ("WeightedAverageNumberOfDilutedSharesOutstanding", "c0", "1,000", "6"),
            ("EarningsPerShareDiluted", "c0", "10.00", "0"),
            ("WeightedAverageNumberOfDilutedSharesOutstanding", "cA", "1,000", "6"),
            ("EarningsPerShareDiluted", "cA", "10.00", "0"),
            ("WeightedAverageNumberOfDilutedSharesOutstanding", "cB", "100", "6"),
            ("EarningsPerShareDiluted", "cB", "20.00", "0")])
        out = classsharesmod.extract_class_diluted_shares(doc)
        self.assertEqual(out["annual"], [{"end": "2025-12-31", "value": 1.0e9}])

    def test_conflicting_facts_for_one_member_fail_the_period(self):
        # The same fact stated twice with the SAME value (income
        # statement + EPS note) is one fact and validates normally...
        doc = _inline_doc("10,000", [
            ("WeightedAverageNumberOfDilutedSharesOutstanding", "cA", "1,000", "6"),
            ("WeightedAverageNumberOfDilutedSharesOutstanding", "cA", "1,000", "6"),
            ("EarningsPerShareDiluted", "cA", "10.00", "0")])
        self.assertEqual(classsharesmod.extract_class_diluted_shares(doc)["annual"],
                         [{"end": "2025-12-31", "value": 1.0e9}])
        # ...but two DIFFERENT values for one member poison the period.
        doc_conflict = _inline_doc("10,000", [
            ("WeightedAverageNumberOfDilutedSharesOutstanding", "cA", "1,000", "6"),
            ("WeightedAverageNumberOfDilutedSharesOutstanding", "cA", "1,100", "6"),
            ("EarningsPerShareDiluted", "cA", "10.00", "0")])
        self.assertEqual(classsharesmod.extract_class_diluted_shares(doc_conflict)["annual"],
                         [])

    def test_fallback_wiring(self):
        # Never fires when companyfacts already has a diluted series.
        series = {"diluted_shares": [{"end": "2025-12-31", "value": 5.0}]}
        periods = {"diluted_shares": []}
        with mock.patch("src.evaluator.class_shares.get_class_diluted_shares") as g:
            prov = edgarmod.apply_class_share_fallback(series, periods, {}, "0000000001")
            g.assert_not_called()
        self.assertFalse(prov["applied"])
        # Fires and injects when the series is empty.
        series = {"diluted_shares": []}
        periods = {}
        fake = {"annual": [{"end": "2025-12-31", "value": 1.0e9, "filed": "2026-02-01"}],
                "periods": [{"start": "2025-01-01", "end": "2025-12-31",
                             "value": 1.0e9, "filed": "2026-02-01"}],
                "validations": [{"method": "member_as_converted"}],
                "filings": [], "error": None}
        with mock.patch("src.evaluator.class_shares.get_class_diluted_shares",
                        return_value=fake):
            prov = edgarmod.apply_class_share_fallback(series, periods, {}, "0001403161")
        self.assertTrue(prov["applied"])
        self.assertEqual(series["diluted_shares"][-1]["value"], 1.0e9)
        self.assertEqual(periods["diluted_shares"][-1]["value"], 1.0e9)
        self.assertEqual(series["_class_share_fallback"]["validations"],
                         [{"method": "member_as_converted"}])


class TestBuybackExtractorR3(unittest.TestCase):
    """Extractor revision 3 (2026-11-09) field fixes, found on the
    first full-universe run of revision 2. Same discipline: real
    filings where a fixture exists, minimal synthetic sentences where
    the construction is the whole point."""

    FX = Path(__file__).resolve().parent / "fixtures"

    def _extract(self, name, period_end):
        html_doc = (self.FX / name).read_text(encoding="utf-8")
        return extract_buyback(strip_html(html_doc), raw_html=html_doc,
                               period_end=period_end)

    def test_lrcx_amount_available_column(self):
        # Lam Research titles the column 'Amount Available Under
        # Repurchase Program' (in thousands) and carries an opening
        # 'Available balance' row: the final balance governs, not
        # the opening one and not the May 2024 $10B origination the
        # as-of rule must leave unvalidated.
        out = self._extract("lrcx_q1fy27_10q.html", "2026-09-27")
        self.assertEqual(out["remaining_usd"], 3032013000.0)
        self.assertEqual(out["remaining_basis"],
                         "item_703_table_may_yet_be_purchased")

    def test_txn_cell_carries_its_own_unit(self):
        # TI prints the may-yet cells as '$ 18.56 billion' — the cell
        # states its own scale; the table has no other unit statement
        # for the column.
        out = self._extract("txn_q3_2026_10q.html", "2026-09-30")
        self.assertEqual(out["remaining_usd"], 18560000000.0)

    def test_atm_issuance_shelf_is_not_a_buyback(self):
        # An at-the-market OFFERING shelf 'remained available to be
        # sold under the ATM Program' is shares to be issued — the
        # opposite of a repurchase remainder.
        text = ("As of September 30, 2026, $461 million of registered "
                "aggregate offering price remained available to be sold "
                "under the ATM Program.")
        out = extract_buyback(text, period_end="2026-09-30")
        self.assertIsNone(out["remaining_usd"])
        self.assertIn("buyback_remaining_not_disclosed", out["flags"])

    def test_completed_program_is_never_summed(self):
        # The filing states the November 2025 program was completed;
        # only the February 2026 program's stated remainder counts.
        text = ("In November 2025, our board of directors authorized a "
                "stock repurchase program of up to $500.0 million. "
                "In February 2026, our board of directors authorized a "
                "stock repurchase program of up to $500.0 million. "
                "We completed the November 2025 program in January 2026, "
                "and the remaining availability under the February 2026 "
                "program as of September 30, 2026 was $150.0 million.")
        out = extract_buyback(text, period_end="2026-09-30")
        self.assertEqual(out["remaining_usd"], 150000000.0)

    def test_of_the_authorization_construction(self):
        # '$A of the $B authorization remained available' — $A is the
        # remainder, $B the program total.
        text = ("As of August 2, 2026, approximately $11.7 billion of "
                "the $15.0 billion share repurchase authorization "
                "remained available.")
        out = extract_buyback(text, period_end="2026-08-02")
        self.assertEqual(out["remaining_usd"], 11700000000.0)

    def test_share_denominated_may_yet_is_not_dollars(self):
        # A 'Maximum NUMBER of Shares That May Yet Be Purchased'
        # column is a share count; the filing states no dollar
        # remainder and the leg fails closed.
        html_doc = ("<html><body><table>"
                    "<tr><td>Period</td><td>Maximum Number of Shares "
                    "That May Yet Be Purchased Under the Program</td></tr>"
                    "<tr><td>July 2026</td><td>9.1</td></tr>"
                    "<tr><td>September 2026</td><td>7.2</td></tr>"
                    "</table></body></html>")
        out = extract_buyback(strip_html(html_doc), raw_html=html_doc,
                              period_end="2026-09-30")
        self.assertIsNone(out["remaining_usd"])


class TestGavCode(unittest.TestCase):
    """GAV-Code (2026-11-09): the code-derived GAV — growth band score
    from TTM revenue growth + the engine's own Value, no model scores."""

    def test_band_edges(self):
        cases = [(100.0, 95), (60.5, 95), (60.0, 80), (40.74, 80), (35.0, 65),
                 (20.27, 65), (20.0, 50), (19.84, 50), (10.14, 50), (10.0, 35),
                 (8.93, 35), (5.0, 20), (4.89, 20), (0.5, 20), (0.0, 5),
                 (-12.0, 5), (None, None)]
        for growth, expected in cases:
            self.assertEqual(growth_band_score(growth), expected, f"g={growth}")

    def test_labels(self):
        self.assertTrue(gav_label(85).startswith("🚀💰 Elite"))
        self.assertTrue(gav_label(84.9).startswith("📈💵 Strong"))
        self.assertTrue(gav_label(55).startswith("⚖️🪙 Fair"))
        self.assertTrue(gav_label(40).startswith("📉💸 Thin"))
        self.assertTrue(gav_label(39.9).startswith("🧊🚫 None"))
        self.assertIsNone(gav_label(None))

    def test_composition(self):
        out = gav_code(40.74, 6.88)   # growth band 80, Value 68.8
        self.assertEqual(out["growth_score"], 80)
        self.assertEqual(out["score"], 74.4)
        self.assertTrue(out["label"].startswith("📈💵 Strong"))
        self.assertIsNone(out["missing_reason"])

    def test_fail_closed(self):
        self.assertEqual(gav_code(None, 5.0)["missing_reason"],
                         "ttm_revenue_growth_missing")
        self.assertIsNone(gav_code(None, 5.0)["score"])
        self.assertEqual(gav_code(20.0, None)["missing_reason"],
                         "value_score_missing")
        self.assertEqual(gav_code(None, None)["missing_reason"],
                         "ttm_revenue_growth_and_value_missing")

    # Synthetic filer: FY23 rev 100, FY24 rev 130; Q3 YTDs 70 / 90 / 120.
    ROWS = [
        {"start": "2023-01-01", "end": "2023-09-30", "value": 70, "filed": "2023-11-01"},
        {"start": "2023-01-01", "end": "2023-12-31", "value": 100, "filed": "2024-02-01"},
        {"start": "2024-01-01", "end": "2024-09-30", "value": 90, "filed": "2024-11-01"},
        {"start": "2024-01-01", "end": "2024-12-31", "value": 130, "filed": "2025-02-01"},
        {"start": "2025-01-01", "end": "2025-09-30", "value": 120, "filed": "2025-11-01"},
    ]

    def _series(self, rows):
        return {"revenue": [{"end": "2023-12-31", "value": 100},
                            {"end": "2024-12-31", "value": 130}],
                "_periods": {"revenue": rows}, "_balance_latest": {}}

    def test_ttm_as_of_cap_rebuilds_prior_year(self):
        cur = ttm_from_periods(self.ROWS, "2024-01-01", "2024-12-31", 130)
        self.assertEqual(cur["value"], 160)            # 130 + 120 - 90
        prior = ttm_from_periods(self.ROWS, "2023-01-01", "2023-12-31", 100,
                                 as_of_end="2024-10-20")
        self.assertEqual(prior["value"], 120)          # 100 + 90 - 70
        self.assertEqual(prior["ttm_end"], "2024-09-30")

    def test_ttm_growth_kpi(self):
        m = compute_metrics(self._series(self.ROWS), 21.0)
        self.assertEqual(m["revenue_ttm"], 160)
        self.assertEqual(m["revenue_ttm_prior"], 120)
        self.assertEqual(m["revenue_ttm_prior_end"], "2024-09-30")
        self.assertAlmostEqual(m["revenue_ttm_growth_pct"], 33.333, places=2)
        self.assertEqual(growth_band_score(m["revenue_ttm_growth_pct"]), 65)

    def test_ttm_growth_fail_closed_without_prior_bridge(self):
        rows = [r for r in self.ROWS if r["end"] != "2023-09-30"]
        m = compute_metrics(self._series(rows), 21.0)
        self.assertEqual(m["revenue_ttm"], 160)
        self.assertIsNone(m["revenue_ttm_prior"])
        self.assertIsNone(m["revenue_ttm_growth_pct"])

    def test_ttm_growth_fail_closed_single_annual_year(self):
        series = self._series(self.ROWS)
        series["revenue"] = [{"end": "2024-12-31", "value": 130}]
        m = compute_metrics(series, 21.0)
        self.assertIsNone(m["revenue_ttm_growth_pct"])


if __name__ == "__main__":
    unittest.main()
