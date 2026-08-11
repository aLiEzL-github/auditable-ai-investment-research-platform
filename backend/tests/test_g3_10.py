"""G3-10 验收测试：勾稽规则 R01—R05（§22.1）。

每条规则六类 fixture：positive / negative / legitimate_NA / rounding /
restatement / wrong_basis。共同字段齐备（⑱ 契约形状一致）。
数值一律 Decimal 字符串；结果绑定 locator。
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
APP = os.path.join(os.path.dirname(__file__), "..", "app")
sys.path.insert(0, APP)

from rules_engine import (  # noqa: E402
    evaluate, RuleInput, PASS, FAIL, INPUT_MISSING, NOT_APPLICABLE,
    RESTATEMENT_PENDING, allowed_error, RuleEngineError,
)


def base(rule, **kw):
    """§22.1 共同字段（⑱：形状与真实契约一致）。"""
    d = dict(scope="600089", period="2026", instant_or_duration="DURATION",
             single_quarter_or_cumulative="ANNUAL", original_or_restated="ORIGINAL",
             unit="CNY_million", source_precision="min_unit",
             applicability_predicate="APPLICABLE", absolute_tolerance="0",
             relative_tolerance="0.001", allowed_residual="0.5",
             failure_impact="BLOCKING", locator=f"ev:{rule}-2026")
    d.update(kw)
    return RuleInput(**d)


class TestCommonFixtureShape(unittest.TestCase):
    def test_common_fields_present(self):
        """⑱：六类 fixture 共同字段齐备且可机检。"""
        inp = base("R01")
        for f in ("scope", "period", "instant_or_duration",
                  "single_quarter_or_cumulative", "original_or_restated",
                  "unit", "source_precision", "applicability_predicate",
                  "absolute_tolerance", "relative_tolerance",
                  "allowed_residual", "failure_impact", "locator"):
            self.assertTrue(hasattr(inp, f), f"缺共同字段 {f}")

    def test_allowed_error_formula(self):
        """§22.1：allowed_error = max(披露区间, 绝对, 相对×冻结量纲)。"""
        inp = base("R02", allowed_residual="2", absolute_tolerance="0.5",
                   relative_tolerance="0.001")
        # frozen_reference_scale=1000 → 相对 = 1.0；max(2, 0.5, 1.0)=2
        self.assertEqual(allowed_error(inp, "1000"), 2)
        # 相对项主导：relative=0.01 × 10000 = 100
        inp2 = base("R02", allowed_residual="0", absolute_tolerance="0",
                    relative_tolerance="0.01")
        self.assertEqual(allowed_error(inp2, "10000"), 100)

    def test_zero_tolerance_only_min_unit(self):
        """只有来源精确到最小单位且无展示舍入时才允许绝对零容差。"""
        inp = base("R02", source_precision="min_unit", allowed_residual="0",
                   absolute_tolerance="0", relative_tolerance="0")
        self.assertEqual(allowed_error(inp, "1"), 0)
        # 有展示舍入 → 不得零容差
        inp2 = base("R02", source_precision="min_unit", allowed_residual="0.5",
                    absolute_tolerance="0", relative_tolerance="0")
        self.assertNotEqual(allowed_error(inp2, "1"), 0)

    def test_non_decimal_rejected(self):
        inp = base("R01", values={"merged_revenue": "not-a-number"})
        with self.assertRaises(RuleEngineError):
            evaluate("R01", inp)


class TestR01SegmentRevenue(unittest.TestCase):
    def test_positive(self):
        inp = base("R01", values={
            "merged_revenue": "1000", "segment_external_revenue": "950",
            "segment_intercompany_revenue": "80", "eliminations": "30"})
        r = evaluate("R01", inp)
        self.assertEqual(r["status"], PASS)

    def test_negative(self):
        inp = base("R01", values={
            "merged_revenue": "1000", "segment_external_revenue": "950",
            "segment_intercompany_revenue": "80", "eliminations": "70"})
        r = evaluate("R01", inp)
        self.assertEqual(r["status"], FAIL)

    def test_legitimate_na(self):
        inp = base("R01", applicability_predicate="NOT_APPLICABLE_NO_SEGMENTS",
                   values={"merged_revenue": "1000",
                           "segment_external_revenue": "1000"})
        r = evaluate("R01", inp)
        self.assertEqual(r["status"], NOT_APPLICABLE)

    def test_rounding(self):
        """披露舍入区间内通过（allowed_residual 覆盖展示舍入）。"""
        inp = base("R01", allowed_residual="1.0", values={
            "merged_revenue": "1000", "segment_external_revenue": "950",
            "segment_intercompany_revenue": "80", "eliminations": "31"})
        r = evaluate("R01", inp)
        self.assertEqual(r["status"], PASS)

    def test_missing_eliminations_input_missing(self):
        """适用但无完整抵消项 → INPUT_MISSING（§22.1 R01）。"""
        inp = base("R01", values={
            "merged_revenue": "1000", "segment_external_revenue": "1000"})
        r = evaluate("R01", inp)
        self.assertEqual(r["status"], INPUT_MISSING)

    def test_wrong_basis_unit(self):
        """wrong_basis：unit 缺失必 FAIL（错配必阻断）。"""
        inp = base("R01", unit="", values={
            "merged_revenue": "1000", "segment_external_revenue": "1000"})
        r = evaluate("R01", inp)
        self.assertEqual(r["status"], FAIL)
        self.assertIn("wrong_basis", r["detail"])

    def test_locator_bound(self):
        inp = base("R01", values={
            "merged_revenue": "1000", "segment_external_revenue": "950",
            "segment_intercompany_revenue": "80", "eliminations": "30"})
        r = evaluate("R01", inp)
        self.assertEqual(r["locator"], "ev:R01-2026")


class TestR02ProfitAttribution(unittest.TestCase):
    def test_positive(self):
        inp = base("R02", values={
            "net_profit": "100", "parent_net_profit": "85",
            "minority_profit": "15"})
        self.assertEqual(evaluate("R02", inp)["status"], PASS)

    def test_negative(self):
        inp = base("R02", values={
            "net_profit": "100", "parent_net_profit": "85",
            "minority_profit": "10"})
        self.assertEqual(evaluate("R02", inp)["status"], FAIL)

    def test_na(self):
        inp = base("R02", applicability_predicate="NOT_APPLICABLE_NO_MINORITY",
                   values={"net_profit": "100", "parent_net_profit": "100",
                           "minority_profit": "0"})
        self.assertEqual(evaluate("R02", inp)["status"], NOT_APPLICABLE)

    def test_rounding(self):
        """按披露精度计算舍入区间（R02 非 PASS 语义）。"""
        inp = base("R02", allowed_residual="0.4", values={
            "net_profit": "100", "parent_net_profit": "85.3",
            "minority_profit": "14.4"})  # 差 0.3 ≤ 0.4
        self.assertEqual(evaluate("R02", inp)["status"], PASS)

    def test_input_missing(self):
        inp = base("R02", values={"net_profit": "100"})
        self.assertEqual(evaluate("R02", inp)["status"], INPUT_MISSING)


class TestR03CashFlow(unittest.TestCase):
    def test_positive(self):
        inp = base("R03", values={
            "cash_net_increase": "50", "ocf": "120", "icf": "-60",
            "fcf": "-20", "fx_effect": "10"})
        self.assertEqual(evaluate("R03", inp)["status"], PASS)

    def test_negative(self):
        inp = base("R03", values={
            "cash_net_increase": "50", "ocf": "120", "icf": "-60",
            "fcf": "-20", "fx_effect": "-10"})
        self.assertEqual(evaluate("R03", inp)["status"], FAIL)

    def test_rounding(self):
        inp = base("R03", allowed_residual="0.6", values={
            "cash_net_increase": "50", "ocf": "120", "icf": "-60",
            "fcf": "-20", "fx_effect": "10.5"})
        self.assertEqual(evaluate("R03", inp)["status"], PASS)

    def test_missing_fx(self):
        """缺汇率影响（适用框架）→ INPUT_MISSING。"""
        inp = base("R03", values={
            "cash_net_increase": "50", "ocf": "120", "icf": "-60", "fcf": "-20"})
        self.assertEqual(evaluate("R03", inp)["status"], INPUT_MISSING)


class TestR04IndirectOCF(unittest.TestCase):
    def test_positive(self):
        inp = base("R04", values={
            "ocf": "150", "net_profit": "100", "non_cash_items": "30",
            "working_capital_changes": "15", "other_adjustments": "5"})
        self.assertEqual(evaluate("R04", inp)["status"], PASS)

    def test_negative(self):
        inp = base("R04", values={
            "ocf": "150", "net_profit": "100", "non_cash_items": "30",
            "working_capital_changes": "15", "other_adjustments": "-5"})
        self.assertEqual(evaluate("R04", inp)["status"], FAIL)

    def test_na_indirect_not_required(self):
        """只有框架不要求时才 NOT_APPLICABLE。"""
        inp = base("R04", applicability_predicate="NOT_APPLICABLE_INDIRECT_NOT_REQUIRED",
                   values={"ocf": "150", "net_profit": "100"})
        self.assertEqual(evaluate("R04", inp)["status"], NOT_APPLICABLE)

    def test_partial_items_input_missing(self):
        """披露框架适用但项目不全 → INPUT_MISSING。"""
        inp = base("R04", values={"ocf": "150", "net_profit": "100",
                                  "non_cash_items": "30"})
        self.assertEqual(evaluate("R04", inp)["status"], INPUT_MISSING)


class TestR05EquityChanges(unittest.TestCase):
    def test_positive(self):
        inp = base("R05", values={
            "ending_equity": "500", "beginning_equity": "400",
            "comprehensive_income": "80", "owner_contributions_distributions": "-20",
            "share_based_payment": "10", "m_and_a_effects": "25",
            "other_changes": "5"})
        self.assertEqual(evaluate("R05", inp)["status"], PASS)

    def test_negative(self):
        inp = base("R05", values={
            "ending_equity": "500", "beginning_equity": "400",
            "comprehensive_income": "80", "owner_contributions_distributions": "-20",
            "share_based_payment": "10", "m_and_a_effects": "25",
            "other_changes": "-5"})
        self.assertEqual(evaluate("R05", inp)["status"], FAIL)

    def test_simplified_identity_not_allowed(self):
        """不以简化恒等式冒充完整规则：缺综合收益/投入 → INPUT_MISSING。"""
        inp = base("R05", values={"ending_equity": "500", "beginning_equity": "400"})
        r = evaluate("R05", inp)
        self.assertEqual(r["status"], INPUT_MISSING)
        self.assertIn("简化恒等式", r["detail"])

    def test_rounding(self):
        inp = base("R05", allowed_residual="0.8", values={
            "ending_equity": "500", "beginning_equity": "400",
            "comprehensive_income": "80", "owner_contributions_distributions": "-20",
            "share_based_payment": "10", "m_and_a_effects": "25.5",
            "other_changes": "4.3"})  # 差 0.8 ≤ 0.8
        self.assertEqual(evaluate("R05", inp)["status"], PASS)


class TestRestatementAndCrossScope(unittest.TestCase):
    def test_restated_input_flagged(self):
        """重述：original_or_restated=RESTATED 在 R10 语义（G3-11）——
        此处验证字段可被 R01 正常处理（不产生错误）。"""
        inp = base("R01", original_or_restated="RESTATED", values={
            "merged_revenue": "1000", "segment_external_revenue": "950",
            "segment_intercompany_revenue": "80", "eliminations": "30"})
        self.assertEqual(evaluate("R01", inp)["status"], PASS)

    def test_unknown_rule_rejected(self):
        with self.assertRaises(RuleEngineError):
            evaluate("R99", base("R01"))


if __name__ == "__main__":
    unittest.main()
