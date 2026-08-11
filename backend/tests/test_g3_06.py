"""G3-06 验收测试：四路估值与确定性三情景。

基线：
  · 统一且可回源的价格/股本/净债务/少数股东权益/币种/时点（G2-15）
  · 四路：FCFF/FCFE、相对估值、PE—ROE—PB、SOTP
  · 悲观/基准/乐观三情景、触发器、安全边际
  · 路由不适用有证据；SOTP 不双算；交叉验证不一致 → OpenItem
  · 不给交易动作或单一伪精确目标（输出区间）
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
APP = os.path.join(os.path.dirname(__file__), "..", "app")
sys.path.insert(0, APP)

from valuation_engine import (  # noqa: E402
    ValuationInputs, ValuationResult, ScenarioSet, PESSIMISTIC, BASE,
    OPTIMISTIC, fcff_valuation, fcfe_valuation, relative_valuation,
    pe_roe_pb_valuation, sotp_valuation, cross_check, ValuationError,
    RouteNotApplicable, SotpDoubleCount,
)


def inputs(**kw):
    d = dict(scope="600089", currency="CNY", as_of="2026-08-11",
             price="10.0", shares_outstanding="1000", net_debt="100",
             minority_interest="50", industry_commodity=None,
             statuses={"price": "READY", "shares_outstanding": "READY",
                       "net_debt": "READY", "minority_interest": "READY"})
    d.update(kw)
    return ValuationInputs(**d)


class TestInputsReady(unittest.TestCase):
    def test_missing_input_blocks(self):
        inp = inputs(shares_outstanding=None)
        self.assertFalse(inp.ready())
        with self.assertRaises(ValuationError) as ctx:
            fcff_valuation(inp, BASE, "100", "0.05", "0.10")
        self.assertIn("E-G3-06-001", str(ctx.exception))

    def test_partial_status_blocks(self):
        inp = inputs(statuses={"price": "PARTIAL（无主源）"})
        with self.assertRaises(ValuationError):
            fcff_valuation(inp, BASE, "100", "0.05", "0.10")


class TestFourMethods(unittest.TestCase):
    def test_fcff(self):
        r = fcff_valuation(inputs(), BASE, "100", "0.05", "0.10")
        self.assertEqual(r.method, "FCFF")
        self.assertLess(float(r.per_share_low), float(r.per_share_base))
        self.assertGreater(float(r.per_share_high), float(r.per_share_base))
        self.assertEqual(r.scenario, BASE)

    def test_fcff_wacc_less_than_growth_fails(self):
        with self.assertRaises(ValuationError) as ctx:
            fcff_valuation(inputs(), BASE, "100", "0.05", "0.08",
                           terminal_growth="0.09")
        self.assertIn("E-G3-06-002", str(ctx.exception))

    def test_fcff_negative_equity_fails(self):
        """模型不适用失败关闭。"""
        inp = inputs(net_debt="5000", minority_interest="1000")
        with self.assertRaises(ValuationError) as ctx:
            fcff_valuation(inp, BASE, "100", "0.05", "0.10")
        self.assertIn("E-G3-06-003", str(ctx.exception))

    def test_fcfe(self):
        r = fcfe_valuation(inputs(), BASE, "80", "0.05", "0.12")
        self.assertEqual(r.method, "FCFE")

    def test_relative(self):
        r = relative_valuation(inputs(), BASE, "15", "0.8")
        self.assertEqual(r.method, "RELATIVE_PE")
        self.assertAlmostEqual(float(r.per_share_base), 12.0)

    def test_pe_roe_pb(self):
        r = pe_roe_pb_valuation(inputs(), BASE, "0.12", "6.0", "15")
        self.assertEqual(r.method, "PE_ROE_PB")
        self.assertAlmostEqual(float(r.per_share_base), 10.8)


class TestSotp(unittest.TestCase):
    def test_route_not_applicable_with_evidence(self):
        """分部披露不完整 → 路由不适用（有证据，不得静默跳过）。"""
        with self.assertRaises(RouteNotApplicable) as ctx:
            sotp_valuation(inputs(), BASE, {}, {})
        self.assertIn("E-G3-06-004", str(ctx.exception))

    def test_sotp_ok(self):
        r = sotp_valuation(inputs(), BASE,
                           {"seg1": "800", "seg2": "500"},
                           {"inter": "100"})
        self.assertEqual(r.method, "SOTP")

    def test_double_count_rejected(self):
        """SOTP 不双算：重叠 > 分部合计 50% → 拒绝。"""
        with self.assertRaises(SotpDoubleCount) as ctx:
            sotp_valuation(inputs(), BASE,
                           {"seg1": "800", "seg2": "500"},
                           {"inter": "700"})
        self.assertIn("E-G3-06-005", str(ctx.exception))


class TestScenarios(unittest.TestCase):
    def test_three_scenarios_and_triggers(self):
        s = ScenarioSet("FCFF")
        s.add(fcff_valuation(inputs(), PESSIMISTIC, "80", "0.04", "0.11"))
        s.add(fcff_valuation(inputs(), BASE, "100", "0.05", "0.10"))
        s.add(fcff_valuation(inputs(), OPTIMISTIC, "120", "0.06", "0.09"))
        self.assertEqual(len(s.scenarios), 3)
        # 悲观 < 基准 < 乐观
        self.assertLess(float(s.scenarios[PESSIMISTIC].per_share_base),
                        float(s.scenarios[BASE].per_share_base))
        self.assertLess(float(s.scenarios[BASE].per_share_base),
                        float(s.scenarios[OPTIMISTIC].per_share_base))
        self.assertIn("triggers", s.scenarios[BASE].to_dict())
        s.compute_margin("10.0")
        self.assertIsNotNone(s.margin_of_safety)

    def test_duplicate_scenario_rejected(self):
        s = ScenarioSet("FCFF")
        s.add(fcff_valuation(inputs(), BASE, "100", "0.05", "0.10"))
        with self.assertRaises(ValuationError) as ctx:
            s.add(fcff_valuation(inputs(), BASE, "100", "0.05", "0.10"))
        self.assertIn("E-G3-06-008", str(ctx.exception))

    def test_mismatched_method_rejected(self):
        s = ScenarioSet("FCFF")
        with self.assertRaises(ValuationError):
            s.add(relative_valuation(inputs(), BASE, "15", "0.8"))

    def test_margin_requires_base(self):
        s = ScenarioSet("FCFF")
        with self.assertRaises(ValuationError):
            s.compute_margin("10.0")


class TestCrossCheck(unittest.TestCase):
    def test_consistent_no_openitem(self):
        r1 = ValuationResult("FCFF", BASE, "9", "11", "10")
        r2 = ValuationResult("FCFE", BASE, "9.2", "11.2", "10.3")
        self.assertEqual(cross_check([r1, r2], "0.15"), [])

    def test_mismatch_creates_openitem(self):
        """交叉验证不一致 → OpenItem（阻断）。"""
        r1 = ValuationResult("FCFF", BASE, "9", "11", "10")
        r2 = ValuationResult("RELATIVE_PE", BASE, "14", "16", "15")
        ois = cross_check([r1, r2], "0.15")
        self.assertEqual(len(ois), 1)
        self.assertTrue(ois[0]["blocking"])
        self.assertIn("OI-VAL-", ois[0]["open_item_id"])

    def test_sotp_not_double_counted(self):
        """SOTP 与其他路不双算（结构上跳过强制比对）。"""
        r1 = ValuationResult("SOTP", BASE, "8", "12", "10")
        r2 = ValuationResult("FCFF", BASE, "9", "11", "10")
        self.assertEqual(cross_check([r1, r2]), [])


if __name__ == "__main__":
    unittest.main()
