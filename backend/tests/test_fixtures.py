import unittest
import random
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))
import gen_fixtures as gf


class TestFixtureGenerator(unittest.TestCase):
    def test_constraint_2_coherent(self):
        r = gf.rng(0xC0FFEE)
        report = gf.gen_report(r, "FICT-01", "2026Q2", 500_000_000)
        self.assertTrue(report["check"]["assets_equals_liab_plus_equity"])
        self.assertTrue(report["check"]["eps_equals_profit_over_shares"])
        self.assertTrue(report["check"]["ocf_equals_profit_plus_depreciation"])

    def test_constraint_3_negatives(self):
        r = gf.rng(0xC0FFEE)
        neg = gf.gen_negatives(r, "FICT-01", "2026Q2", 500_000_000)
        defects = [x["defect"] for x in neg]
        self.assertEqual(len(neg), 6)
        for d in ("wrong_scope", "wrong_period", "unit_mismatch",
                  "cumulative_single_mixed", "restatement_unhandled", "cutoff_drift"):
            self.assertIn(d, defects)

    def test_reproducible(self):
        r1 = gf.rng(0xC0FFEE)
        r2 = gf.rng(0xC0FFEE)
        self.assertEqual(gf.gen_report(r1, "FICT-01", "P", 100),
                         gf.gen_report(r2, "FICT-01", "P", 100))


if __name__ == "__main__":
    unittest.main()
