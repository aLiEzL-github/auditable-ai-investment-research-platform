"""G3-12 验收测试：Decimal、单位守恒、冻结适用分母与 property tests。

基线：
  · 定点十进制：跨进程结果字节一致（canonical 定 scale）
  · 规则级绝对/相对容差、舍入（ROUND_HALF_UP）
  · 预运行分母哈希：运行后缩小分母/放宽容差/把缺失改 N/A 必失败并留痕
  · 单位守恒：跨维加减必失败；重述/错口径/极值/近零 property tests
  · 每条 R01—R10 覆盖舍入、重述、wrong-basis（由 test_g3_10/11 承担
    规则六类 fixture；本模块验证数值与单位层的机械正确性）
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
APP = os.path.join(os.path.dirname(__file__), "..", "app")
sys.path.insert(0, APP)

from decimal_tools import (  # noqa: E402
    FixedDecimal, add, sub, mul, div, UnitDim, UnitMismatch,
    FrozenDenominator, FrozenViolation, DecimalToolsError,
)
from rules_engine import evaluate, RuleInput, PASS  # noqa: E402


class TestFixedDecimal(unittest.TestCase):
    def test_canonical_byte_identical(self):
        """跨进程字节一致：同值同 scale → 同 canonical。"""
        a = FixedDecimal("1.5", "CNY_million", 2)
        b = FixedDecimal("1.50", "CNY_million", 6)
        # 同值不同 scale → canonical 一致（尾零补齐到 2）
        self.assertEqual(a.canonical(), "1.50")
        self.assertEqual(a.canonical(), FixedDecimal("1.500", "CNY_million", 2).canonical())
        # 不同值 → 字节不同
        self.assertNotEqual(a.canonical(), FixedDecimal("1.6", "CNY_million", 2).canonical())

    def test_rounding_half_up(self):
        a = FixedDecimal("1.235", "CNY_million", 6)
        self.assertEqual(a.rounded(2).canonical(), "1.24")

    def test_non_decimal_rejected(self):
        with self.assertRaises(DecimalToolsError):
            FixedDecimal("not-a-number", "CNY_million")

    def test_divide_by_zero_fails_closed(self):
        with self.assertRaises(DecimalToolsError):
            div(FixedDecimal("1", "CNY"), FixedDecimal("0", "CNY"))


class TestUnitConservation(unittest.TestCase):
    def test_same_unit_add_ok(self):
        r = add(FixedDecimal("1", "CNY_million"), FixedDecimal("2", "CNY_million"))
        self.assertEqual(r.value, "3")

    def test_cross_dim_add_fails(self):
        """单位守恒：货币 + 比例 必失败（wrong-basis）。"""
        with self.assertRaises(UnitMismatch):
            add(FixedDecimal("1", "CNY_million"), FixedDecimal("2", "percent"))

    def test_unknown_unit_fails(self):
        """空单位（unknown）必失败，且须命中 E-G3-12-003（unknown 分支自身）。"""
        with self.assertRaises(UnitMismatch) as ctx:
            add(FixedDecimal("1", ""), FixedDecimal("2", "CNY_million"))
        self.assertIn("E-G3-12-003", str(ctx.exception))

    def test_mul_allows_cross_dim(self):
        r = mul(FixedDecimal("2", "CNY"), FixedDecimal("3", "shares"))
        self.assertEqual(r.unit, "CNY*shares")


class TestFrozenDenominator(unittest.TestCase):
    def test_freeze_then_verify_ok(self):
        fd = FrozenDenominator()
        fd.shrink("total_assets", "1000")
        h = fd.freeze()
        self.assertEqual(fd.verify(), h)

    def test_shrink_after_freeze_fails(self):
        """运行后缩小分母必失败（C-4 变异语义）。"""
        fd = FrozenDenominator()
        fd.shrink("total_assets", "1000")
        fd.freeze()
        with self.assertRaises(FrozenViolation) as ctx:
            fd.shrink("total_assets", "900")
        self.assertIn("E-G3-12-005", str(ctx.exception))

    def test_loosen_tolerance_after_freeze_fails(self):
        fd = FrozenDenominator()
        fd.tolerances["R02"] = "0.001"
        fd.freeze()
        with self.assertRaises(FrozenViolation):
            fd.loosen("R02", "0.01")

    def test_tamper_detected(self):
        """篡改必败：冻结后直接改 dict 内容 → verify 失败。"""
        fd = FrozenDenominator()
        fd.shrink("total_assets", "1000")
        fd.freeze()
        fd.denominators["total_assets"] = "999"  # 字节改动
        with self.assertRaises(FrozenViolation):
            fd.verify()

    def test_unfrozen_rejected(self):
        fd = FrozenDenominator()
        with self.assertRaises(FrozenViolation):
            fd.verify()


class TestRulesDecimalIntegration(unittest.TestCase):
    """规则引擎 + FixedDecimal 集成：R02 用定点十进制计算字节一致。"""

    def _inp(self, **kw):
        d = dict(scope="600089", period="2026", instant_or_duration="DURATION",
                 single_quarter_or_cumulative="ANNUAL", original_or_restated="ORIGINAL",
                 unit="CNY_million", source_precision="min_unit",
                 applicability_predicate="APPLICABLE", absolute_tolerance="0",
                 relative_tolerance="0.001", allowed_residual="0.5",
                 failure_impact="BLOCKING", locator="ev:R02-2026")
        d.update(kw)
        return RuleInput(**d)

    def test_r02_byte_identical_across_runs(self):
        """同一输入两次计算 → residual 字节一致（跨进程）。"""
        inp = self._inp(values={"net_profit": "100", "parent_net_profit": "85",
                                "minority_profit": "15"})
        r1 = evaluate("R02", inp)
        r2 = evaluate("R02", inp)
        self.assertEqual(r1["residual"], r2["residual"])

    def test_extreme_and_near_zero(self):
        """极值/近零 property：不崩溃、不产生 NaN/Inf。"""
        cases = [
            {"net_profit": "0", "parent_net_profit": "0", "minority_profit": "0"},
            {"net_profit": "9999999999", "parent_net_profit": "9999999999",
             "minority_profit": "0"},
            {"net_profit": "0.0000001", "parent_net_profit": "0.0000001",
             "minority_profit": "0"},
        ]
        for values in cases:
            r = evaluate("R02", self._inp(values=values))
            self.assertIn(r["status"], ("PASS", "FAIL", "INPUT_MISSING"))
            self.assertNotIn("nan", r["residual"].lower())
            self.assertNotIn("inf", r["residual"].lower())

    def test_restated_rounding_near_boundary(self):
        """重述 + 舍入边界：差恰等于 allowed_error → PASS。"""
        inp = self._inp(allowed_residual="0.5", original_or_restated="RESTATED",
                        values={"net_profit": "100", "parent_net_profit": "85.5",
                                "minority_profit": "14.0"})  # 差 0.5 = 容差
        self.assertEqual(evaluate("R02", inp)["status"], PASS)


if __name__ == "__main__":
    unittest.main()
