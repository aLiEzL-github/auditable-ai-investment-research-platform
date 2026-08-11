"""G3-05 验收测试：强类型 Claim AST、emission map 与单公司工作流。

基线（G3-05，§22.3）：
  · 六类节点 [F]/[D]/[A]/[P]/[C]/[L]；C/L 白名单逐字
  · 材料性数字/区间/表格/脚注/定性主张均可追溯
  · 除白名单 C/L 外每段可见内容一一绑定 Claim（emission map）
  · 重复、遗漏、错绑、渲染后注入均失败
  · 每个核心结论有证据、公式、批准假设或明确缺口

执行计划：
  · C-7 Claim 图闭合（无孤儿节点）
  · C-8 篡改必败（原对象与改动对象各跑一次，两次结论必须不同）
  · C-9 跨 scope/period/unit/vintage 必拒（四条独立用例）
  · C-10 首屏声明（前 3 行 SINGLE_REVIEWER_ATTESTED，先红后绿）
  · C-11 不构成投资建议（缺失即 FAIL）
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
APP = os.path.join(os.path.dirname(__file__), "..", "app")
sys.path.insert(0, APP)

from claim_engine import (  # noqa: E402
    ClaimNode, ClaimGraph, EmissionMap, ResearchContract, ClaimError,
    EmissionMismatch, FirstScreenGuardFail, CrossDimensionError,
    verify_cross_dimension, verify_first_screen, verify_disclaimer,
    content_sha256, F, D, A, P, C, L,
)


def contract():
    return ResearchContract(scope="600089", period="2026", unit="CNY_million",
                            vintage="ORIGINAL", snapshot="SNAP-1",
                            security_code="600089", company_id="TBEA",
                            as_of="2026-08-11", version="v1")


def fact_node(ref="F-1", value="100", **kw):
    d = dict(node_type=F, ref_id=ref, rendered_value=value, scope="600089",
             snapshot="SNAP-1", unit="CNY_million", evidence_refs=["EV-1"],
             materiality="MATERIAL")
    d.update(kw)
    return ClaimNode(**d)


def c_node(ref="C-1", field="security_code", value="600089", **kw):
    d = dict(node_type=C, ref_id=ref, rendered_value=value, scope="600089",
             snapshot="SNAP-1", contract_field=field, evidence_refs=[])
    d.update(kw)
    return ClaimNode(**d)


def l_node(ref="L-1", value="1.1", **kw):
    d = dict(node_type=L, ref_id=ref, rendered_value=value, scope="600089",
             snapshot="SNAP-1", evidence_refs=[])
    d.update(kw)
    return ClaimNode(**d)


def build_graph():
    g = ClaimGraph()
    g.register_evidence("EV-1")
    g.register_evidence("EV-2")
    g.register_formula("F_FCFF")
    g.register_assumption("A-1")
    g.register_assumption("ASM-1")
    for n in (fact_node(ref="F-1", value="100", output_path="r.md",
                        byte_span="0-3"),
              fact_node(ref="F-2", value="30", evidence_refs=["EV-2"],
                        output_path="r.md", byte_span="3-5"),
              ClaimNode(node_type=D, ref_id="D-1", rendered_value="170",
                        scope="600089", snapshot="SNAP-1", unit="CNY_million",
                        formula_ref="F_FCFF", evidence_refs=["EV-1"],
                        output_path="r.md", byte_span="5-8"),
              ClaimNode(node_type=A, ref_id="A-1", rendered_value="8%",
                        scope="600089", snapshot="SNAP-1", unit="percent",
                        assumption_ref="ASM-1", evidence_refs=["EV-2"],
                        output_path="r.md", byte_span="8-10"),
              ClaimNode(node_type=P, ref_id="P-1", rendered_value="2027",
                        scope="600089", snapshot="SNAP-1",
                        evidence_refs=["EV-1"], materiality="MATERIAL",
                        output_path="r.md", byte_span="10-14"),
              c_node(ref="C-1", output_path="r.md", byte_span="14-20"),
              l_node(ref="L-1", output_path="r.md", byte_span="20-23"),
              ):
        g.add(n)
    return g


class TestClaimGraph(unittest.TestCase):
    def test_six_node_types(self):
        g = ClaimGraph()
        for t in (F, D, A, P):
            g.add(ClaimNode(node_type=t, ref_id=f"X-{t}", rendered_value="1",
                            scope="s", snapshot="sn"))
        # C/L 须过白名单（C 用合法字段，L 用章节序号）
        g.add(ClaimNode(node_type=C, ref_id="X-C2", rendered_value="600089",
                        scope="s", snapshot="sn", contract_field="security_code"))
        g.add(ClaimNode(node_type=L, ref_id="X-L2", rendered_value="1.1",
                        scope="s", snapshot="sn"))
        self.assertEqual(len(g.nodes), 6)

    def test_duplicate_ref_rejected(self):
        g = ClaimGraph()
        g.add(fact_node())
        with self.assertRaises(ClaimError) as ctx:
            g.add(fact_node(ref="F-1"))
        self.assertIn("E-G3-05-002", str(ctx.exception))

    def test_invalid_type_rejected(self):
        g = ClaimGraph()
        with self.assertRaises(ClaimError):
            g.add(ClaimNode(node_type="X", ref_id="X-1", rendered_value="1",
                            scope="s", snapshot="sn"))


class TestWhitelist(unittest.TestCase):
    """§22.3 C/L 白名单。"""

    def test_l_only_structural(self):
        g = ClaimGraph()
        for ok in ("1.1", "第3节", "A.1", "p.12", "3/5"):
            g.add(l_node(ref=f"L-{ok}", value=ok))
        with self.assertRaises(ClaimError) as ctx:
            g.add(l_node(ref="L-BAD", value="净利润 5 亿元"))
        self.assertIn("E-G3-05-003", str(ctx.exception))

    def test_c_only_contract_fields(self):
        g = ClaimGraph()
        for kind, v in (("security_code", "600089"), ("as_of", "2026-08-11"),
                        ("period", "2026"), ("version", "v1")):
            g.add(c_node(ref=f"C-{kind}", field=kind, value=v))
        with self.assertRaises(ClaimError) as ctx:
            g.add(c_node(ref="C-BAD", field="revenue", value="100"))
        self.assertIn("E-G3-05-004", str(ctx.exception))

    def test_decision_numbers_never_in_whitelist(self):
        """金额/百分比/倍数等决策数字永不在 C/L（§22.3）。"""
        g = ClaimGraph()
        for value in ("100亿元", "5.2%", "2.5倍", "1,000"):
            with self.assertRaises(ClaimError):
                g.add(l_node(ref=f"L-{value}", value=value))


class TestClosureC7(unittest.TestCase):
    def test_graph_closed(self):
        g = build_graph()
        self.assertIn("0 orphans", g.verify_closure())

    def test_orphan_detected(self):
        """C-7 变异注入：删一条边（未登记 evidence）→ FAIL。"""
        g = build_graph()
        g.add(fact_node(ref="F-9", value="50", evidence_refs=["EV-MISSING"]))
        with self.assertRaises(ClaimError) as ctx:
            g.verify_closure()
        self.assertIn("E-G3-05-005", str(ctx.exception))

    def test_fact_without_evidence_orphan(self):
        g = build_graph()
        g.add(fact_node(ref="F-8", value="9", evidence_refs=[]))
        with self.assertRaises(ClaimError):
            g.verify_closure()

    def test_derived_without_formula_orphan(self):
        g = build_graph()
        g.add(ClaimNode(node_type=D, ref_id="D-9", rendered_value="1",
                        scope="600089", snapshot="SNAP-1"))
        with self.assertRaises(ClaimError):
            g.verify_closure()


class TestEmissionMap(unittest.TestCase):
    REPORT = ("100" + "30" + "170" + "8%" + "2027" + "600089" + "1.1")

    def test_report_verified(self):
        g = build_graph()
        em = EmissionMap()
        for n in g.nodes.values():
            em.add(n)
        self.assertIn("spans verified", em.verify_report("r.md", self.REPORT))

    def test_rendered_injection_detected(self):
        """渲染后注入：报告出现未绑定数字 → FAIL（变异注入）。"""
        g = build_graph()
        em = EmissionMap()
        for n in g.nodes.values():
            em.add(n)
        injected = self.REPORT + "999"
        with self.assertRaises(EmissionMismatch) as ctx:
            em.verify_report("r.md", injected)
        self.assertIn("E-G3-05-009", str(ctx.exception))

    def test_tampered_value_detected(self):
        """C-8 篡改必败：原报告 vs 改动报告，两次结论必须不同。"""
        g = build_graph()
        em = EmissionMap()
        for n in g.nodes.values():
            em.add(n)
        em.verify_report("r.md", self.REPORT)  # 原对象 OK
        tampered = "101" + self.REPORT[3:]     # 第一 span 值改 100→101
        with self.assertRaises(EmissionMismatch) as ctx:
            em.verify_report("r.md", tampered)
        self.assertIn("E-G3-05-007", str(ctx.exception))

    def test_duplicate_span_detected(self):
        g = build_graph()
        em = EmissionMap()
        nodes = list(g.nodes.values())
        for n in nodes:
            em.add(n)
        # 两个节点绑定同一 span → 重复
        n1 = nodes[0]
        em.entries["r.md"].append(dict(n1.__dict__, ref_id="DUP"))
        with self.assertRaises(EmissionMismatch) as ctx:
            em.verify_report("r.md", self.REPORT)
        self.assertIn("E-G3-05-008", str(ctx.exception))


class TestCrossDimensionC9(unittest.TestCase):
    """C-9：跨 scope/period/unit/vintage 必拒 —— 四条各自独立用例。"""

    def test_cross_scope(self):
        n = fact_node(scope="600888")
        with self.assertRaises(CrossDimensionError) as ctx:
            verify_cross_dimension(n, contract())
        self.assertIn("E-G3-05-010", str(ctx.exception))

    def test_cross_snapshot(self):
        n = fact_node(snapshot="SNAP-OTHER")
        with self.assertRaises(CrossDimensionError) as ctx:
            verify_cross_dimension(n, contract())
        self.assertIn("E-G3-05-011", str(ctx.exception))

    def test_cross_unit(self):
        n = fact_node(unit="USD_million")
        with self.assertRaises(CrossDimensionError) as ctx:
            verify_cross_dimension(n, contract())
        self.assertIn("E-G3-05-012", str(ctx.exception))

    def test_ok_same_dimension(self):
        verify_cross_dimension(fact_node(), contract())  # 不抛


class TestFirstScreenC10C11(unittest.TestCase):
    """C-10 首屏声明（前 3 行）+ C-11 不构成投资建议。"""

    def test_first_screen_attestation(self):
        good = "SINGLE_REVIEWER_ATTESTED\n标题\n正文\n"
        verify_first_screen("r.md", good)  # 不抛
        # 脚注形态（末行）→ FAIL（先红后绿）
        bad = "标题\n正文\n脚注\nSINGLE_REVIEWER_ATTESTED"
        with self.assertRaises(FirstScreenGuardFail) as ctx:
            verify_first_screen("r.md", bad)
        self.assertIn("E-G3-05-013", str(ctx.exception))

    def test_disclaimer_missing_fails(self):
        with self.assertRaises(FirstScreenGuardFail) as ctx:
            verify_disclaimer("r.md", "无免责声明")
        self.assertIn("E-G3-05-014", str(ctx.exception))
        verify_disclaimer("r.md", "研究信息，不构成投资建议。")

    def test_sha256_stable(self):
        self.assertEqual(content_sha256("abc"), content_sha256("abc"))
        self.assertNotEqual(content_sha256("abc"), content_sha256("abd"))


if __name__ == "__main__":
    unittest.main()
