"""G3-14 验收测试：OpenItemRegistry、强类型渲染与对抗语料。

基线：
  · owner、截止、阻断 Gate、closure evidence
  · 材料性开放项未关 → PARTIAL（release_eligible=false）
  · 任何可见材料性内容不能绕过 Claim 图（渲染绑定）
  · 篡改必失败（closure evidence 哈希锚定）
  · 对抗语料：Unicode/中文数字/HTML/代码/图片/跨快照/渲染后篡改
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
APP = os.path.join(os.path.dirname(__file__), "..", "app")
sys.path.insert(0, APP)

from open_item_registry import (  # noqa: E402
    OpenItem, OpenItemRegistry, OpenItemError, OPEN, CLOSED,
)
from claim_engine import (  # noqa: E402
    ClaimNode, ClaimGraph, EmissionMap, F,
)


class TestRegistryBasics(unittest.TestCase):
    def setUp(self):
        self.reg = OpenItemRegistry()
        self.reg.register(OpenItem("OI-1", "缺副源核对", True, "U",
                                   due_date="2026-09-01",
                                   blocks_gate="G3-05"))

    def test_register_and_duplicate(self):
        with self.assertRaises(OpenItemError) as ctx:
            self.reg.register(OpenItem("OI-1", "x", True, "U"))
        self.assertIn("E-G3-14-003", str(ctx.exception))

    def test_close_requires_evidence(self):
        with self.assertRaises(OpenItemError) as ctx:
            self.reg.close("OI-1", "")
        self.assertIn("E-G3-14-001", str(ctx.exception))

    def test_material_open_blocks_release(self):
        """材料性开放项未关 → release_eligible=false（PARTIAL，不得准出）。"""
        self.assertFalse(self.reg.release_eligible())
        self.assertEqual(self.reg.open_material_count(), 1)

    def test_close_makes_eligible(self):
        self.reg.close("OI-1", "PR #99 merged")
        self.assertTrue(self.reg.release_eligible())

    def test_non_material_open_does_not_block(self):
        reg = OpenItemRegistry()
        reg.register(OpenItem("OI-2", "装饰性缺口", False, "U"))
        self.assertTrue(reg.release_eligible())


class TestTamperDetection(unittest.TestCase):
    def test_closure_evidence_hash_bound(self):
        """篡改必败：closure evidence 哈希不符 → 拒绝闭合。"""
        reg = OpenItemRegistry()
        reg.register(OpenItem("OI-3", "x", True, "U"))
        with self.assertRaises(OpenItemError) as ctx:
            reg.close("OI-3", "PR #1 merged", evidence_sha256="0" * 64)
        self.assertIn("E-G3-14-002", str(ctx.exception))

    def test_close_with_correct_hash(self):
        reg = OpenItemRegistry()
        reg.register(OpenItem("OI-4", "x", True, "U"))
        ev = "PR #2 merged"
        import hashlib
        h = hashlib.sha256(ev.encode()).hexdigest()
        reg.close("OI-4", ev, evidence_sha256=h)
        self.assertEqual(reg.items["OI-4"].status, CLOSED)


class TestRenderBinding(unittest.TestCase):
    def test_unbound_span_not_in_rendered(self):
        reg = OpenItemRegistry()
        reg.register(OpenItem("OI-5", "x", True, "U"))
        reg.bind("估值区间 10-12 元", "D-1")
        # 材料性内容已绑定 → 渲染须包含
        with self.assertRaises(OpenItemError) as ctx:
            reg.verify_render("报告正文无估值")
        self.assertIn("E-G3-14-006", str(ctx.exception))
        # 渲染包含 → OK
        self.assertIn("OK", reg.verify_render("估值区间 10-12 元 报告"))

    def test_duplicate_binding_rejected(self):
        reg = OpenItemRegistry()
        reg.bind("span-1", "D-1")
        with self.assertRaises(OpenItemError):
            reg.bind("span-1", "F-2")


class TestAdversarialCorpus(unittest.TestCase):
    """对抗语料：Unicode/中文数字/HTML/代码/图片/跨快照/渲染后篡改。"""

    def _g(self):
        g = ClaimGraph()
        g.register_evidence("EV-1")
        return g

    def test_chinese_numbers_in_claims(self):
        """材料性中文数字必须在 Claim 绑定中（不在 C/L 白名单）。"""
        g = self._g()
        # 中文数字作为 F 值（合法，绑定 evidence）
        g.add(ClaimNode(node_type=F, ref_id="F-CN", rendered_value="五亿元",
                        scope="600089", snapshot="S1", evidence_refs=["EV-1"]))
        self.assertIn("0 orphans", g.verify_closure())
        # 中文数字试图进 [L:] → 拒绝
        from claim_engine import L, ClaimError
        with self.assertRaises(ClaimError):
            g.add(ClaimNode(node_type=L, ref_id="L-CN", rendered_value="五亿元",
                            scope="600089", snapshot="S1"))

    def test_html_image_code_cannot_bypass(self):
        """HTML/图片/代码中的决策数字须绑定（emission 扫描抓注入）。"""
        g = self._g()
        g.add(ClaimNode(node_type=F, ref_id="F-1", rendered_value="10",
                        scope="600089", snapshot="S1", evidence_refs=["EV-1"],
                        output_path="r.md", byte_span="0-2"))
        em = EmissionMap()
        for n in g.nodes.values():
            em.add(n)
        # 渲染后注入：HTML 标签里夹带数字（在已绑定 span 之后 → 命中 009）
        injected = "10" + "<img alt=\"10.5亿\">"
        with self.assertRaises(Exception) as ctx:
            em.verify_report("r.md", injected)
        self.assertIn("E-G3-05-009", str(ctx.exception))

    def test_cross_snapshot_rejected(self):
        """跨快照：Claim 引用旧 snapshot → 拒绝（复用 claim_engine C-9）。"""
        from claim_engine import verify_cross_dimension, ResearchContract, \
            CrossDimensionError
        c = ResearchContract(scope="600089", period="2026", unit="CNY_million",
                             vintage="ORIGINAL", snapshot="S1",
                             security_code="600089", company_id="TBEA",
                             as_of="2026-08-11", version="v1")
        n = ClaimNode(node_type=F, ref_id="F-X", rendered_value="1",
                      scope="600089", snapshot="S-OLD",
                      evidence_refs=["EV-1"])
        with self.assertRaises(CrossDimensionError):
            verify_cross_dimension(n, c)


if __name__ == "__main__":
    unittest.main()
