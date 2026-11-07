import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.evaluator.scoring import interpolate, concentration_score
from src.evaluator.dcf import fair_value, growth_path, build_wacc
from src.evaluator.filings import extract_concentration

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


if __name__ == "__main__":
    unittest.main()
