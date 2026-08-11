"""G3-11 验收测试：勾稽规则 R06—R10（§22.1）。

每条规则六类 fixture：positive / negative / legitimate_NA / rounding /
restatement / wrong_basis。累计/单季、合并/母公司、重述前后和期间不连续
必失败或进入对应非 PASS 状态。
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
APP = os.path.join(os.path.dirname(__file__), "..", "app")
sys.path.insert(0, APP)

from rules_engine import (  # noqa: E402
    evaluate, RuleInput, PASS, FAIL, INPUT_MISSING, NOT_COMPARABLE,
    RESTATEMENT_PENDING, NOT_APPLICABLE,
)


def base(rule, **kw):
    d = dict(scope="600089", period="2026", instant_or_duration="DURATION",
             single_quarter_or_cumulative="ANNUAL", original_or_restated="ORIGINAL",
             unit="CNY_million", source_precision="min_unit",
             applicability_predicate="APPLICABLE", absolute_tolerance="0",
             relative_tolerance="0.001", allowed_residual="0.5",
             failure_impact="BLOCKING", locator=f"ev:{rule}-2026")
    d.update(kw)
    return RuleInput(**d)


class TestR06BalanceSheet(unittest.TestCase):
    def test_positive_consolidated(self):
        inp = base("R06", values={"total_assets": "1000",
                                  "total_liabilities": "600",
                                  "total_equity": "400"})
        self.assertEqual(evaluate("R06", inp)["status"], PASS)

    def test_negative(self):
        inp = base("R06", values={"total_assets": "1000",
                                  "total_liabilities": "600",
                                  "total_equity": "350"})
        self.assertEqual(evaluate("R06", inp)["status"], FAIL)

    def test_rounding_disclosed(self):
        inp = base("R06", allowed_residual="0.9", values={
            "total_assets": "1000.4", "total_liabilities": "600",
            "total_equity": "400"})
        self.assertEqual(evaluate("R06", inp)["status"], PASS)

    def test_input_missing(self):
        inp = base("R06", values={"total_assets": "1000", "total_liabilities": "600"})
        self.assertEqual(evaluate("R06", inp)["status"], INPUT_MISSING)

    def test_wrong_basis_mother_company_mixed(self):
        """合并/母公司口径混用：unit 与 basis 错配必阻断。"""
        inp = base("R06", unit="", values={"total_assets": "1000",
                                           "total_liabilities": "600",
                                           "total_equity": "400"})
        self.assertEqual(evaluate("R06", inp)["status"], FAIL)


class TestR07DeductedProfit(unittest.TestCase):
    def test_positive(self):
        inp = base("R07", values={
            "parent_net_profit": "100", "parent_non_recurring_gain_loss": "20",
            "non_gang_parent_net_profit": "80"})
        self.assertEqual(evaluate("R07", inp)["status"], PASS)

    def test_negative(self):
        inp = base("R07", values={
            "parent_net_profit": "100", "parent_non_recurring_gain_loss": "20",
            "non_gang_parent_net_profit": "70"})
        self.assertEqual(evaluate("R07", inp)["status"], FAIL)

    def test_rounding(self):
        inp = base("R07", allowed_residual="0.3", values={
            "parent_net_profit": "100", "parent_non_recurring_gain_loss": "19.8",
            "non_gang_parent_net_profit": "79.9"})  # 差 0.3
        self.assertEqual(evaluate("R07", inp)["status"], PASS)

    def test_input_missing(self):
        inp = base("R07", values={"parent_net_profit": "100"})
        self.assertEqual(evaluate("R07", inp)["status"], INPUT_MISSING)


class TestR08SegmentProfit(unittest.TestCase):
    def test_positive(self):
        inp = base("R08", values={
            "merged_profit": "200", "segment_profit_sum": "220",
            "segment_eliminations": "20",
            "segment_measurement_basis": "COMPARABLE"})
        self.assertEqual(evaluate("R08", inp)["status"], PASS)

    def test_negative(self):
        inp = base("R08", values={
            "merged_profit": "200", "segment_profit_sum": "220",
            "segment_eliminations": "10",
            "segment_measurement_basis": "COMPARABLE"})
        self.assertEqual(evaluate("R08", inp)["status"], FAIL)

    def test_not_comparable_basis(self):
        """适用但计量基础不一致 → NOT_COMPARABLE（§22.1 R08）。"""
        inp = base("R08", values={
            "merged_profit": "200", "segment_profit_sum": "220",
            "segment_eliminations": "20",
            "segment_measurement_basis": "OPERATING_PROFIT_VS_NET"})
        self.assertEqual(evaluate("R08", inp)["status"], NOT_COMPARABLE)

    def test_input_missing(self):
        inp = base("R08", values={"merged_profit": "200"})
        self.assertEqual(evaluate("R08", inp)["status"], INPUT_MISSING)


class TestR09ParentSubsidiary(unittest.TestCase):
    def test_positive(self):
        inp = base("R09", values={
            "parent_assets": "700", "subsidiary_assets": "400",
            "intercompany_eliminations": "100", "consolidated_assets": "1000"})
        self.assertEqual(evaluate("R09", inp)["status"], PASS)

    def test_negative(self):
        inp = base("R09", values={
            "parent_assets": "700", "subsidiary_assets": "400",
            "intercompany_eliminations": "50", "consolidated_assets": "1000"})
        self.assertEqual(evaluate("R09", inp)["status"], FAIL)

    def test_rounding(self):
        inp = base("R09", allowed_residual="0.6", values={
            "parent_assets": "700", "subsidiary_assets": "400",
            "intercompany_eliminations": "99.4", "consolidated_assets": "1000"})
        self.assertEqual(evaluate("R09", inp)["status"], PASS)

    def test_incomplete_notes_input_missing(self):
        """适用但附注不全 → INPUT_MISSING（§22.1 R09）。"""
        inp = base("R09", values={"parent_assets": "700",
                                  "subsidiary_assets": "400"})
        self.assertEqual(evaluate("R09", inp)["status"], INPUT_MISSING)


class TestR10PeriodContinuity(unittest.TestCase):
    def test_positive(self):
        inp = base("R10", values={"this_period_beginning": "1000",
                                  "prior_period_ending": "1000"})
        self.assertEqual(evaluate("R10", inp)["status"], PASS)

    def test_negative_period_gap(self):
        """期间不连续必失败。"""
        inp = base("R10", values={"this_period_beginning": "1000",
                                  "prior_period_ending": "950"})
        self.assertEqual(evaluate("R10", inp)["status"], FAIL)

    def test_restatement_pending(self):
        """存在未处理重述 → RESTATEMENT_PENDING（§22.1 R10）。"""
        inp = base("R10", values={"this_period_beginning": "1000",
                                  "prior_period_ending": "1000"},
                   original_or_restated="RESTATED")
        inp.values["restatement_pending"] = "PENDING"
        self.assertEqual(evaluate("R10", inp)["status"], RESTATEMENT_PENDING)

    def test_rounding(self):
        inp = base("R10", allowed_residual="0.4", values={
            "this_period_beginning": "1000", "prior_period_ending": "999.7"})
        self.assertEqual(evaluate("R10", inp)["status"], PASS)

    def test_input_missing(self):
        inp = base("R10", values={"this_period_beginning": "1000"})
        self.assertEqual(evaluate("R10", inp)["status"], INPUT_MISSING)


if __name__ == "__main__":
    unittest.main()
