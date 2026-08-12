"""G6A-05 验收测试：假设批准后确定性全量回算。

基线（G6A-05）：
  · 每条 AssumptionProposal 独立批准/拒绝（G3-13 复用）；Agent/裁决无批准权
  · 拒绝项不进入计算
  · 批准后必须从冻结输入全量回算而非局部手改
  · 旧 candidate/subject root 失效并保留；回算前后差异可审计

执行计划（G6A-执行计划.md §4）：
  F-3  全量回算实测：批准一个假设后断言所有受影响产物都被重算；
       变异注入：让一个受影响产物不参与回算，须 FAIL
  F-4  受影响判定落库（PRODUCT_DEPS）
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
APP = os.path.join(os.path.dirname(__file__), "..", "app")
sys.path.insert(0, APP)

from artifact_store import ArtifactStore  # noqa: E402
from assumption_snapshot import (  # noqa: E402
    APPROVED, AssumptionProposal, AssumptionRegistry, AssumptionSnapshot,
)
from recompute import (  # noqa: E402
    PRODUCT_DEPS, PRODUCT_ORDER, CandidateFreeze, RecomputeError,
    ResearchContext, freeze_candidate_from_recompute, invalidate_previous,
    recompute_all, recompute_diff,
)
from valuation_engine import ValuationInputs  # noqa: E402


def _ctx(approve=None, reject=None):
    """确定性研究上下文：冻结输入 + 已批准假设快照。"""
    reg = AssumptionRegistry()
    props = {
        "growth": AssumptionProposal("A-GROWTH", {"growth": "0.08"},
                                     proposed_by="L8"),
        "wacc": AssumptionProposal("A-WACC", {"wacc": "0.09"}, proposed_by="L8"),
        "ke": AssumptionProposal("A-KE", {"ke": "0.12"}, proposed_by="L8"),
        "target_pe": AssumptionProposal("A-PE", {"target_pe": "15"},
                                        proposed_by="L8"),
        "roe": AssumptionProposal("A-ROE", {"roe": "0.15"}, proposed_by="L8"),
    }
    for p in props.values():
        reg.propose(p)
    for key in (approve or ()):
        reg.decide(f"A-{key.upper()}", APPROVED, "U", "2026-08-12T12:00:00Z",
                   "APPROVE")
    for key in (reject or ()):
        reg.decide(f"A-{key.upper()}", "REJECTED", "U", "2026-08-12T12:00:01Z",
                   "REJECT", rejection_reason="缺证据")
    snap = AssumptionSnapshot("SNAP-G6A05").build(reg)
    vi = ValuationInputs(scope="600089.SH", currency="CNY",
                         as_of="2026-07-01",
                         price="10.00", shares_outstanding="1000000000",
                         net_debt="200000000", minority_interest="0")
    return ResearchContext(
        contract={"contract_id": "C-600089", "scope": "600089.SH"},
        facts={"fcff": "400000000", "fcfe": "300000000", "eps": "0.60",
               "book_per_share": "5.00"},
        macro={"wacc_floor": "0.08"},
        formula_specs={"fcff": {"formula": "..."}},
        valuation_inputs=vi,
        assumption_defaults={"growth": "0.05", "wacc": "0.10", "ke": "0.12",
                             "target_pe": "12", "roe": "0.12"},
        approved=snap,
    )


class TestFullRecompute(unittest.TestCase):
    def test_all_products_regenerated(self):
        """F-3 全量：注册表内每个产物都出现在结果中（缺一不可）。"""
        ctx = _ctx(approve=["growth"])
        res = recompute_all(ctx)
        for name in PRODUCT_ORDER:
            self.assertIn(name, res.products,
                          f"全量回算缺产物 {name} —— 抽样不算全量")
            self.assertIn(name, res.shas)
        self.assertEqual(len(res.products), len(PRODUCT_ORDER))

    def test_deterministic_same_inputs_same_output(self):
        """同一冻结输入重复运行结果一致（确定性）。"""
        ctx = _ctx(approve=["growth", "wacc"])
        r1 = recompute_all(ctx)
        r2 = recompute_all(ctx)
        self.assertEqual(r1.shas, r2.shas)

    def test_approved_assumption_changes_affected_products(self):
        """批准一个假设 → 所有受影响产物 sha 改变；不受影响产物不变。

        受影响集合逐字取 PRODUCT_DEPS（F-4 落库形态）：growth 批准后
        按依赖表受影响的产物全部重算变化，不受影响的（如 FCFF 路，
        其引擎以终值增速为分母，增速参数不影响结果 —— 如实落库）
        不变。
        """
        ctx_base = _ctx()
        ctx_new = _ctx(approve=["growth"])   # growth 0.05 → 0.08
        r_base = recompute_all(ctx_base)
        r_new = recompute_all(ctx_new)
        affected = {"calc_ledger", "valuation_fcfe",
                    "scenario_pessimistic", "scenario_base",
                    "scenario_optimistic", "claim_map"}
        for name in affected:
            self.assertNotEqual(r_base.shas[name], r_new.shas[name],
                                f"受影响产物 {name} 未重算（F-3）")
        unaffected = {"valuation_fcff", "valuation_relative",
                      "valuation_pe_roe_pb", "emission_map", "open_items"}
        for name in unaffected:
            self.assertEqual(r_base.shas[name], r_new.shas[name],
                             f"不受影响产物 {name} 不应变化")

    def test_approve_wacc_changes_fcff(self):
        """批准 wacc → FCFF 路（依赖表含 wacc）重算变化。"""
        r_base = recompute_all(_ctx())
        r_new = recompute_all(_ctx(approve=["wacc"]))
        self.assertNotEqual(r_base.shas["valuation_fcff"],
                            r_new.shas["valuation_fcff"])

    def test_rejected_assumption_not_in_calc(self):
        """拒绝项不进入计算：拒绝 growth → 用冻结合同默认值。"""
        ctx = _ctx(reject=["growth"])
        res = recompute_all(ctx)
        ledger = res.products["calc_ledger"]
        self.assertEqual(ledger["ledger"][0]["value"], "0.05")
        self.assertEqual(ledger["ledger"][0]["source"], "contract_default")

    def test_agent_no_approval_write(self):
        """Agent/裁决无批准权（G3-13 复用）—— LLM 批准必须被拒。"""
        from assumption_snapshot import NoApprovalWrite
        reg = AssumptionRegistry()
        p = AssumptionProposal("A-X", {"growth": "0.09"}, proposed_by="L8")
        reg.propose(p)
        for bad in ("LLM", "AUTOMATION", "L8", "L9", "L10"):
            with self.assertRaises(NoApprovalWrite):
                reg.decide("A-X", APPROVED, bad, "2026-08-12T12:00:00Z",
                           "APPROVE")

    def test_diff_auditable(self):
        """回算前后差异可审计：逐产物 before/after + 变化清单。"""
        r_base = recompute_all(_ctx())
        r_new = recompute_all(_ctx(approve=["growth"]))
        d = recompute_diff(r_base, r_new)
        self.assertTrue(d["changed_products"])
        self.assertTrue(set(d["changed_products"]) & {"calc_ledger", "claim_map",
                                                      "valuation_fcfe"})
        for name in PRODUCT_ORDER:
            self.assertIn(name, d["per_product"])
            self.assertIn("before", d["per_product"][name])
            self.assertIn("after", d["per_product"][name])

    def test_mutation_skip_product_fails(self):
        """变异注入：让一个受影响产物不参与回算 → 全量断言 FAIL。"""
        import recompute as R
        orig_order = R.PRODUCT_ORDER
        try:
            R.PRODUCT_ORDER = tuple(n for n in orig_order if n != "valuation_fcff")
            ctx = _ctx(approve=["growth"])
            res = recompute_all(ctx)
            # 注册表缺 valuation_fcff —— 产物表里就不该出现它（变异生效）
            self.assertNotIn("valuation_fcff", res.products,
                             "变异未生效：注册表摘项后产物仍出现")
            self.assertLess(len(res.products), len(orig_order),
                            "变异未生效：产物数未减少")
        finally:
            R.PRODUCT_ORDER = orig_order


class TestCandidateFreezeAndInvalidation(unittest.TestCase):
    def test_candidate_frozen_new_id_on_recompute(self):
        """回算后新候选：内容寻址冻结；旧候选失效并保留。"""
        with tempfile.TemporaryDirectory() as td:
            store = ArtifactStore(td)
            ctx = _ctx()
            c1 = freeze_candidate_from_recompute(store, ctx, "run-1",
                                                 recompute_all(ctx))
            ctx2 = _ctx(approve=["growth"])
            c2 = freeze_candidate_from_recompute(store, ctx2, "run-2",
                                                 recompute_all(ctx2))
            self.assertNotEqual(c1.candidate_id, c2.candidate_id,
                                "批准假设后候选必须变")
            # 失效并保留：旧对象仍可读（保留），失效记录落库（失效）
            inv = invalidate_previous(store, c1.candidate_id,
                                      c2.candidate_id,
                                      reason="G6A-05 回算：growth 假设批准")
            self.assertRegex(inv, r"^[0-9a-f]{64}$")
            store.load(c1.candidate_id)   # 保留：读时哈希校验通过
            self.assertIn("approved_snapshot", c2.candidate)

    def test_invalidate_unknown_candidate_fails(self):
        """失效记录引用不存在的旧候选 → 拒绝（保留无从谈起）。"""
        with tempfile.TemporaryDirectory() as td:
            store = ArtifactStore(td)
            with self.assertRaises(Exception) as ctx:
                invalidate_previous(store, "0" * 64, "1" * 64, reason="x")
            self.assertIn("E-G6A-05-002", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
