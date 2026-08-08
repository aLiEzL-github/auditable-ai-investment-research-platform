"""G2-14 验收测试：600089 真实 golden baseline（框架）。

基线：
  · 真实 Gate 样本只来自已取得的真实披露
  · 合成 fixture 仅用于自动化负测（不得混入真实 baseline）
  · 材料性事实 100% 人工回源（缺口符合 G2-13 双录合同 → PARTIAL）
"""
import unittest
import os
import sys

APP = os.path.join(os.path.dirname(__file__), "..", "app")
sys.path.insert(0, APP)

from golden_baseline import GoldenBaseline, GoldenBaselineError  # noqa: E402


class TestGoldenBaseline(unittest.TestCase):
    def setUp(self):
        self.b = GoldenBaseline("GB_600089_2026H1", "600089", "2026H1")

    # ── 合成 fixture 禁止混入真实 baseline ──────────────────────────
    def test_synthetic_forbidden_in_real_baseline(self):
        with self.assertRaises(GoldenBaselineError) as ctx:
            self.b.add_source_doc("LOC/synthetic/neg-fixture", synthetic=True)
        self.assertIn("E-G2-14-001", str(ctx.exception))
        # 正例：真实披露（人工导入）
        self.b.add_source_doc("LOC/600089/2026H1/annual.pdf")
        self.assertEqual(len(self.b.source_docs), 1)

    # ── 材料性事实未回源 → 可登记但 PARTIAL ────────────────────────
    def test_material_fact_unbacked_partial(self):
        self.b.add_source_doc("LOC/600089/2026H1/annual.pdf")
        self.b.add_fact("营业收入", "1000000", "CNY", "LOC/600089/p25")
        self.assertEqual(self.b.facts["营业收入"]["value"], "1000000")
        self.assertIn("PARTIAL", self.b.status())
        # 回源后登记可升格
        self.b.add_back_source("营业收入", "LOC/600089/p25",
                               reviewed_by="HR", review_state="VERIFIED")
        self.assertIn("营业收入", self.b.back_source)

    # ── 非材料性事实不需回源 ────────────────────────────────────────
    def test_non_material_no_back_source_required(self):
        self.b.add_fact("非材料注释", "x", "-", "LOC/x", material=False)
        self.assertIn("非材料注释", self.b.facts)

    # ── 材料性 100% 回源；缺口 → PARTIAL ────────────────────────────
    def test_missing_back_source_partial(self):
        self.b.add_source_doc("LOC/600089/2026H1/annual.pdf")
        self.b.add_back_source("营业收入", "LOC/p25", reviewed_by="HR",
                               review_state="VERIFIED")
        self.b.add_fact("营业收入", "1000000", "CNY", "LOC/p25")
        self.b.add_fact("归母净利润", "200000", "CNY", "LOC/p30")  # 未回源
        self.assertIn("PARTIAL", self.b.status())

    def test_complete_when_all_back_sourced(self):
        self.b.add_source_doc("LOC/600089/2026H1/annual.pdf")
        for m, loc in (("营业收入", "p25"), ("归母净利润", "p30")):
            self.b.add_back_source(m, f"LOC/{loc}", reviewed_by="HR",
                                   review_state="VERIFIED")
            self.b.add_fact(m, "1", "CNY", f"LOC/{loc}")
        self.assertEqual(self.b.status(), "COMPLETE")

    # ── 无真实样本 → PARTIAL（诚实标注）─────────────────────────────
    def test_no_real_sample_partial(self):
        self.assertIn("PARTIAL", self.b.status())

    # ── 双录：回源核对人须不同于录入人 ──────────────────────────────
    def test_back_source_reviewer_must_differ(self):
        # 录入人（如手工录入者）不可同时回源（自录自审拒绝）
        self.b._entry_actors = lambda: {"U"}
        with self.assertRaises(GoldenBaselineError) as ctx:
            self.b.add_back_source("营业收入", "LOC/p25",
                                   reviewed_by="U", review_state="VERIFIED")
        self.assertIn("E-G2-14-003", str(ctx.exception))
        # 自动化录入（解析器）+ U 回源：不同实体，合法
        self.b._entry_actors = lambda: {"AUTOMATION"}
        self.b.add_back_source("营业收入", "LOC/p25",
                               reviewed_by="U", review_state="VERIFIED")
        self.assertIn("营业收入", self.b.back_source)


if __name__ == "__main__":
    unittest.main()
