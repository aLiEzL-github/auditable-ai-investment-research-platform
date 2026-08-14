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
    PRODUCT_DEPS, PRODUCT_ORDER, CandidateFreeze, OpenItemsPolicy,
    RecomputeError, ResearchContext, freeze_candidate_from_recompute,
    invalidate_previous, recompute_all, recompute_diff,
)
from valuation_engine import ValuationInputs  # noqa: E402


def _policy(tolerance="0.15", owner_role="U", due_date="2026-08-31",
            blocks_gate="G3-06"):
    """冻结 OpenItemsPolicy（OI-PF-170）：owner/due_date/blocks_gate/tolerance
    一律来自冻结输入，不得读墙钟/环境变量/硬编码当前日期。"""
    return OpenItemsPolicy(tolerance=tolerance, owner_role=owner_role,
                           due_date=due_date, blocks_gate=blocks_gate)


_UNSET = object()


def _ctx(approve=None, reject=None, policy=_UNSET):
    """确定性研究上下文：冻结输入 + 已批准假设快照。"""
    reg = AssumptionRegistry()
    props = {
        "growth": AssumptionProposal("A-GROWTH", {"growth": "0.08"},
                                     proposed_by="L8"),
        "wacc": AssumptionProposal("A-WACC", {"wacc": "0.09"}, proposed_by="L8"),
        "ke": AssumptionProposal("A-KE", {"ke": "0.13"}, proposed_by="L8"),
        "target_pe": AssumptionProposal("A-PE", {"target_pe": "15"},
                                        proposed_by="L8"),
        "roe": AssumptionProposal("A-ROE", {"roe": "0.15"}, proposed_by="L8"),
    }
    for p in props.values():
        reg.propose(p)
    # OI-PF-191：proposal_id 必须取自已构造的 proposal 对象，不能从 key 猜。
    # target_pe 的真实 ID 是 A-PE（不是 f"A-TARGET_PE"）—— 旧写法使 target_pe
    # 批准路径永远抛 E-G3-13-004 提案不存在。
    for key in (approve or ()):
        reg.decide(props[key].proposal_id, APPROVED, "U", "2026-08-12T12:00:00Z",
                   "APPROVE")
    for key in (reject or ()):
        reg.decide(props[key].proposal_id, "REJECTED", "U", "2026-08-12T12:00:01Z",
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
        open_items_policy=(_policy() if policy is _UNSET else policy),
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
        # OI-PF-169 修复后 emission_map 移入受影响集合：它由 claim_map 派生、
        # 依赖 growth/wacc。**此前它在 unaffected 里，而那是照着缺陷写的断言** ——
        # 旧实现 `return {"emissions": sorted(PRODUCT_DEPS["emission_map"])}`
        # 恒返回空，当然不随任何假设变化。用例因此记录了缺陷的行为而非正确行为，
        # 并使该缺陷在回归中**看起来是被覆盖的**。
        affected = {"calc_ledger", "valuation_fcfe",
                    "scenario_pessimistic", "scenario_base",
                    "scenario_optimistic", "claim_map", "emission_map",
                    # OI-PF-170：open_items 由四路估值交叉验证派生 ——
                    # growth 批准改变 FCFE 路基准价 → 交叉验证差异/项随之变，
                    # 故从 unaffected 移入 affected（不再是恒空产物）。
                    "open_items"}
        for name in affected:
            self.assertNotEqual(r_base.shas[name], r_new.shas[name],
                                f"受影响产物 {name} 未重算（F-3）")
        unaffected = {"valuation_fcff", "valuation_relative",
                      "valuation_pe_roe_pb"}
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

    def test_product_deps_matrix_matches_real_sensitivity(self):
        """行为矩阵（OI-PF-171）：对每个假设键，仅批准该键时
        changed_products 必须**精确**等于 {p | key in PRODUCT_DEPS[p]}。

        既不允许少报（真实敏感的产物漏列依赖 → 批准后 sha 变了但 diff 漏记）
        也不允许多报（PRODUCT_DEPS 声明了生成器实际不读的键 → 批准后 sha 没变
        但矩阵说它受影响）。每个键的批准值均与默认值不同（ke 0.12→0.13），
        避免「读取了但输入值相同」造成的假阴性。

        target_pe 键自然经过 A-PE 批准路径（OI-PF-191）—— 旧 _ctx 从 key 猜
        ID 时本测试在 target_pe 行即抛 E-G3-13-004 提案不存在。
        """
        cases = {"growth": ("0.05", "0.08"), "wacc": ("0.10", "0.09"),
                 "ke": ("0.12", "0.13"), "target_pe": ("12", "15"),
                 "roe": ("0.12", "0.15")}
        for key, (dflt, proposed) in cases.items():
            self.assertNotEqual(dflt, proposed,
                                f"{key} 批准值须与默认值不同，否则该键的敏感"
                                f"性断言是假阴性（读到了但值相同）")
            r_base = recompute_all(_ctx())
            r_new = recompute_all(_ctx(approve=[key]))
            diff = recompute_diff(r_base, r_new)
            expected = {p for p, deps in PRODUCT_DEPS.items() if key in deps}
            with self.subTest(key=key, expected=sorted(expected)):
                self.assertEqual(set(diff["changed_products"]), expected,
                                 f"{key} 批准后 changed_products 与 PRODUCT_DEPS"
                                 f" 不一致（不允许多报/少报）")
        # target_pe 判定显式覆盖四个产物：relative/pe_roe_pb 真实读取须在列，
        # calc_ledger/claim_map 生成器不读 target_pe 须不在列（不随声明虚报）。
        r_base = recompute_all(_ctx())
        r_new = recompute_all(_ctx(approve=["target_pe"]))
        changed = set(recompute_diff(r_base, r_new)["changed_products"])
        self.assertIn("valuation_relative", changed)
        self.assertIn("valuation_pe_roe_pb", changed)
        self.assertNotIn("calc_ledger", changed)
        self.assertNotIn("claim_map", changed)

    def test_open_items_mismatch_produces_strong_typed_items_and_diagnostics(
            self):
        """OI-PF-170：真实交叉验证不一致 → 非空强类型 OpenItem + 原样诊断。

        判定「不是占位项」：项 ID 与 valuation_engine.cross_check 的诊断一一
        对应，且诊断 diff 必须等于**估值产物** per_share_base 的相对差 ——
        证明项来自真实交叉验证（单一实现路径），不是硬编码或「总是非空」。
        """
        from decimal import Decimal
        res = recompute_all(_ctx())   # 冻结容差 0.15 → 本 fixture 真实不一致
        prod = res.products["open_items"]
        items = prod["open_items"]
        cc = prod["cross_check"]
        self.assertTrue(items, "交叉验证不一致须产生非空开放项（禁止恒空）")
        self.assertTrue(cc, "交叉验证诊断须保留")
        for it in items:
            self.assertTrue(it["open_item_id"].startswith("OI-VAL-BASE-"),
                            f"非法 open_item_id: {it['open_item_id']!r}")
            self.assertTrue(it["description"])
            self.assertIs(it["material"], True,
                          "交叉验证不一致为材料性项，material 必须为 true")
            self.assertEqual(it["owner_role"], "U")
            self.assertEqual(it["due_date"], "2026-08-31")
            self.assertEqual(it["blocks_gate"], "G3-06")
            self.assertIsNone(it["closure_evidence"])
            self.assertEqual(it["status"], "OPEN")
        for d in cc:
            for key in ("scenario", "method_a", "method_b", "diff",
                        "tolerance"):
                self.assertIn(key, d, f"诊断缺 {key}")
            self.assertIs(d["blocking"], True)
        self.assertEqual(
            {i["open_item_id"] for i in items},
            {d["open_item_id"] for d in cc},
            "OpenItem 与交叉验证诊断必须一一对应")
        fcff = Decimal(res.products["valuation_fcff"]["per_share_base"])
        fcfe = Decimal(res.products["valuation_fcfe"]["per_share_base"])
        self.assertGreater(abs(fcff - fcfe) / abs(fcff),
                           Decimal("0.15"),
                           "fixture 前提：FCFF/FCFE 默认下确实不一致")
        for d in cc:
            if {d["method_a"], d["method_b"]} == {"FCFF", "FCFE"}:
                self.assertEqual(Decimal(d["diff"]),
                                 abs(fcff - fcfe) / abs(fcff),
                                 "诊断 diff 必须与估值产物基准价一致 —— "
                                 "项由真实交叉验证产生")

    def test_wide_frozen_tolerance_yields_empty_open_items(self):
        """OI-PF-170：冻结容差足够宽 → 允许空集（一致即不产生项）。

        空集必须来自**冻结容差**而非硬编码：同一输入换窄容差必须产生项。
        """
        wide = _ctx(policy=_policy(tolerance="2"))
        prod = recompute_all(wide).products["open_items"]
        self.assertEqual(prod["open_items"], [],
                         "宽冻结容差下一致结果允许空集")
        self.assertEqual(prod["cross_check"], [])
        narrow = _ctx(policy=_policy(tolerance="0.15"))
        self.assertTrue(
            recompute_all(narrow).products["open_items"]["open_items"],
            "空集须由容差门控 —— 同一输入窄容差必须产生项")

    def test_open_items_policy_fail_closed(self):
        """OI-PF-170：冻结 policy 缺失/空字段/非法容差 → 失败关闭。

        不默认补值：owner/due_date/blocks_gate/tolerance 缺一即拒绝。
        """
        cases = [
            ("缺 policy（None）", None),
            ("tolerance 为空", _policy(tolerance="")),
            ("tolerance 非数字", _policy(tolerance="abc")),
            ("tolerance 为零", _policy(tolerance="0")),
            ("tolerance 为负", _policy(tolerance="-0.1")),
            ("owner_role 为空", _policy(owner_role="")),
            ("due_date 为空", _policy(due_date="")),
            ("blocks_gate 为空", _policy(blocks_gate="")),
        ]
        for label, policy in cases:
            with self.subTest(label=label):
                with self.assertRaises(RecomputeError) as cm:
                    recompute_all(_ctx(policy=policy))
                self.assertIn("E-OI-PF-170", str(cm.exception),
                              f"{label} 必须 RecomputeError 失败关闭")

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
