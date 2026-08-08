"""G2-15 验收测试：估值与行业输入的独立来源合同。

基线：
  · 每个估值输入有 source_role/rights/as_of/period/basis/locator
  · 同源镜像不算独立证据
  · 主源或权利不足保持 MISSING/PARTIAL，不得静默用聚合器或手工值升格
"""
import unittest
import os
import sys

APP = os.path.join(os.path.dirname(__file__), "..", "app")
sys.path.insert(0, APP)

from valuation_contract import (ValuationContract, ValuationInput,
                                ValuationContractError, INPUTS)


def _src(**kw):
    base = dict(source_id="SRC_A", source_role="PRIMARY", rights="ALLOWED",
                as_of="2026-06-30", period="2026H1", basis="官方披露",
                locator="LOC/p25")
    base.update(kw)
    return base


class TestValuationContract(unittest.TestCase):
    def setUp(self):
        self.c = ValuationContract()

    # ── 五类输入 + 六要素 ───────────────────────────────────────────
    def test_five_inputs(self):
        self.assertEqual(set(INPUTS),
                         {"price", "shares_outstanding", "net_debt",
                          "minority_interest", "industry_commodity"})

    def test_six_elements_registered(self):
        v = ValuationInput("price")
        v.add_source(**_src())
        s = v.sources[0]
        for k in ("source_role", "rights", "as_of", "period", "basis", "locator"):
            self.assertIn(k, s, f"缺 {k}")

    # ── 同源镜像不算独立证据 ────────────────────────────────────────
    def test_mirror_rejected(self):
        v = ValuationInput("price")
        v.add_source(**_src())
        with self.assertRaises(ValuationContractError) as ctx:
            v.add_source(**_src())  # 同 source_id 镜像
        self.assertIn("E-G2-15-002", str(ctx.exception))

    # ── 状态判定：主源/权利不足保持 MISSING/PARTIAL ─────────────────
    def test_empty_missing(self):
        self.assertEqual(ValuationInput("price").status(), "MISSING")

    def test_secondary_only_partial(self):
        v = ValuationInput("price")
        v.add_source(**_src(source_role="SECONDARY"))
        self.assertIn("PARTIAL", v.status())

    def test_primary_rights_insufficient_partial(self):
        v = ValuationInput("price")
        v.add_source(**_src(rights="PROHIBITED"))
        self.assertIn("PARTIAL", v.status())

    def test_ready_when_primary_ok(self):
        v = ValuationInput("price")
        v.add_source(**_src())
        self.assertEqual(v.status(), "READY")

    # ── 不得静默升格（聚合器/手工值）───────────────────────────────
    def test_no_silent_promotion(self):
        v = ValuationInput("net_debt")
        v.add_source(**_src(source_role="SECONDARY", source_id="AGGREGATOR"))
        self.assertIn("PARTIAL", v.status())  # 副源不得升格
        v2 = ValuationInput("price")
        v2.add_source(**_src(source_id="AGGREGATOR", kind="AGGREGATOR"))
        self.assertIn("PARTIAL", v2.status())  # 聚合器不得升格
        v3 = ValuationInput("price")
        v3.add_source(**_src(source_id="MANUAL", kind="MANUAL"))
        self.assertIn("PARTIAL", v3.status())  # 手工值不得静默升格

    # ── 合同整体 ────────────────────────────────────────────────────
    def test_contract_summary(self):
        self.c.register("price", **_src())
        s = self.c.summary()
        self.assertEqual(s["price"], "READY")
        self.assertEqual(s["shares_outstanding"], "MISSING")
        self.assertEqual(s["net_debt"], "MISSING")  # 未登记 = MISSING


if __name__ == "__main__":
    unittest.main()
