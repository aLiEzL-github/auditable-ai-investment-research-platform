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
    ProductMissing, RecomputeError, ResearchContext,
    freeze_candidate_from_recompute, frozen_inputs_hash, frozen_inputs_payload,
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


def _mk_vi(**over):
    """ValuationInputs fixture：默认五类输入 + 币种 + 时点，可逐字段覆盖。"""
    fields = dict(scope="600089.SH", currency="CNY", as_of="2026-07-01",
                  price="10.00", shares_outstanding="1000000000",
                  net_debt="200000000", minority_interest="0")
    fields.update(over)
    return ValuationInputs(**fields)


def _ctx(approve=None, reject=None, policy=_UNSET, contract=_UNSET,
         facts=_UNSET, macro=_UNSET, formula_specs=_UNSET,
         assumption_defaults=_UNSET, valuation_inputs=_UNSET,
         approved=_UNSET):
    """确定性研究上下文：冻结输入 + 已批准假设快照。

    顶层冻结输入均可逐项覆盖（扰动测试用），默认值与基线 fixture 一致。
    """
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
    return ResearchContext(
        contract=contract if contract is not _UNSET else
        {"contract_id": "C-600089", "scope": "600089.SH"},
        facts=facts if facts is not _UNSET else
        {"fcff": "400000000", "fcfe": "300000000", "eps": "0.60",
         "book_per_share": "5.00"},
        macro=macro if macro is not _UNSET else {"wacc_floor": "0.08"},
        formula_specs=formula_specs if formula_specs is not _UNSET else
        {"fcff": {"formula": "..."}},
        valuation_inputs=valuation_inputs if valuation_inputs is not _UNSET else
        _mk_vi(),
        assumption_defaults=assumption_defaults if assumption_defaults is not _UNSET
        else {"growth": "0.05", "wacc": "0.10", "ke": "0.12",
              "target_pe": "12", "roe": "0.12"},
        approved=approved if approved is not _UNSET else snap,
        open_items_policy=(_policy() if policy is _UNSET else policy),
    )


def _store_object_count(store) -> int:
    """ArtifactStore 对象库内的已存对象数（按对象文件计数）。"""
    return sum(len(files) for _, _, files in os.walk(str(store.root)))


def _registry_state():
    """捕获注册表三表的原对象引用 + 内容快照（OI-PF-196 原地恢复用）。"""
    import recompute as R
    return (R.PRODUCT_ORDER, R.PRODUCT_DEPS, R.GENERATORS,
            dict(R.PRODUCT_DEPS), dict(R.GENERATORS))


def _restore_registry(state):
    """OI-PF-195 变异后恢复：PRODUCT_DEPS/GENERATORS 原地 clear()+update()。

    保留模块外 `from recompute import PRODUCT_DEPS` 等 import alias 的
    对象身份 —— 旧写法重绑定到新字典，首个变异测试后的模块别名会读到
    被变异过的旧字典（顺序相关地污染后续用例）；PRODUCT_ORDER 为不可变
    元组，重绑定回原对象即恢复身份。
    """
    import recompute as R
    order, deps_ref, gens_ref, deps_copy, gens_copy = state
    R.PRODUCT_ORDER = order
    if R.PRODUCT_DEPS is not deps_ref:
        R.PRODUCT_DEPS = deps_ref
    R.PRODUCT_DEPS.clear()
    R.PRODUCT_DEPS.update(deps_copy)
    if R.GENERATORS is not gens_ref:
        R.GENERATORS = gens_ref
    R.GENERATORS.clear()
    R.GENERATORS.update(gens_copy)


class TestFullRecompute(unittest.TestCase):
    def test_production_registry_mutually_consistent(self):
        """OI-PF-195 正例：生产三表互为真源 —— order 无重复，
        set(PRODUCT_ORDER)==set(PRODUCT_DEPS)==set(GENERATORS)，11 项。"""
        import recompute as R
        self.assertEqual(len(R.PRODUCT_ORDER), len(set(R.PRODUCT_ORDER)),
                         "生产 PRODUCT_ORDER 不得有重复")
        self.assertEqual(set(R.PRODUCT_ORDER), set(R.PRODUCT_DEPS),
                         "PRODUCT_ORDER 与 PRODUCT_DEPS 漂移")
        self.assertEqual(set(R.PRODUCT_ORDER), set(R.GENERATORS),
                         "PRODUCT_ORDER 与 GENERATORS 漂移")
        self.assertEqual(set(R.PRODUCT_DEPS), set(R.GENERATORS),
                         "PRODUCT_DEPS 与 GENERATORS 漂移")
        self.assertEqual(len(R.PRODUCT_ORDER), 11,
                         "正常 11 项顺序必须保持")
        self.assertEqual(tuple(R.PRODUCT_ORDER), tuple(R.PRODUCT_DEPS),
                         "order 顺序须与 deps 落库顺序一致（确定性不变）")

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
        """变异注入：PRODUCT_ORDER 摘项而 GENERATORS/PRODUCT_DEPS 仍有
        → 生产 recompute_all 失败关闭抛 ProductMissing（OI-PF-195）。

        旧版只断言「产物减少」—— 那是把「成功少算」当测试成功，生产代码
        并未失败关闭；本版要求 recompute_all **真的抛错**，且报错点名差集
        方向与摘掉的项。
        """
        import recompute as R
        state = _registry_state()
        try:
            victim = "valuation_fcff"
            R.PRODUCT_ORDER = tuple(n for n in state[0] if n != victim)
            self.assertNotIn(victim, R.PRODUCT_ORDER)
            self.assertIn(victim, R.GENERATORS, "变异未生效：生成器仍在")
            self.assertIn(victim, R.PRODUCT_DEPS, "变异未生效：依赖表仍在")
            with self.assertRaises(ProductMissing) as cm:
                recompute_all(_ctx(approve=["growth"]))
            msg = str(cm.exception)
            self.assertIn("E-G6A-05-001", msg)
            self.assertIn(victim, msg)
            self.assertIn("PRODUCT_DEPS 有而 PRODUCT_ORDER 无", msg)
            self.assertIn("GENERATORS 有而 PRODUCT_ORDER 无", msg)
        finally:
            _restore_registry(state)
        self.assertIs(R.PRODUCT_ORDER, state[0], "PRODUCT_ORDER 对象身份未恢复")
        self.assertIs(R.PRODUCT_DEPS, state[1], "PRODUCT_DEPS 身份未恢复（重绑定泄漏）")
        self.assertIs(R.GENERATORS, state[2], "GENERATORS 身份未恢复（重绑定泄漏）")
        self.assertEqual(R.PRODUCT_DEPS, state[3], "PRODUCT_DEPS 内容未恢复")
        self.assertEqual(R.GENERATORS, state[4], "GENERATORS 内容未恢复")
        self.assertEqual(PRODUCT_DEPS, state[3],
                         "模块 import alias 不得读到被变异过的旧字典")
        self.assertEqual(PRODUCT_ORDER, state[0],
                         "模块 import alias 不得读到被变异过的旧 order")

    def test_mutation_generator_add_unregistered_fails(self):
        """变异注入：GENERATORS 新增未登记项（原失败载荷）
        → 失败关闭：生成器在跑但产物不进 order，静默遗漏必须被拒。"""
        import recompute as R
        state = _registry_state()
        try:
            R.GENERATORS["unregistered_probe"] = lambda ctx, v: {"probe": 1}
            self.assertIn("unregistered_probe", R.GENERATORS,
                          "变异未生效")
            self.assertNotIn("unregistered_probe", R.PRODUCT_ORDER)
            self.assertNotIn("unregistered_probe", R.PRODUCT_DEPS)
            with self.assertRaises(ProductMissing) as cm:
                recompute_all(_ctx())
            msg = str(cm.exception)
            self.assertIn("unregistered_probe", msg)
            self.assertIn("GENERATORS 有而 PRODUCT_ORDER 无", msg)
        finally:
            _restore_registry(state)
        self.assertIs(R.GENERATORS, state[2], "GENERATORS 身份未恢复（重绑定泄漏）")
        self.assertEqual(R.GENERATORS, state[4], "GENERATORS 内容未恢复")

    def test_mutation_generator_remove_registered_fails(self):
        """变异注入：GENERATORS 删除已登记项
        → 失败关闭：order 里的产物没有生成器必须被拒。"""
        import recompute as R
        state = _registry_state()
        try:
            victim = "open_items"
            R.GENERATORS = {k: v for k, v in state[2].items() if k != victim}
            self.assertNotIn(victim, R.GENERATORS, "变异未生效")
            self.assertIn(victim, R.PRODUCT_ORDER)
            with self.assertRaises(ProductMissing) as cm:
                recompute_all(_ctx())
            msg = str(cm.exception)
            self.assertIn(victim, msg)
            self.assertIn("PRODUCT_ORDER 有而 GENERATORS 无", msg)
        finally:
            _restore_registry(state)
        self.assertIs(R.GENERATORS, state[2], "GENERATORS 身份未恢复（重绑定泄漏）")
        self.assertEqual(R.GENERATORS, state[4], "GENERATORS 内容未恢复")

    def test_mutation_order_duplicate_fails(self):
        """变异注入：PRODUCT_ORDER 重复项（set 相等但顺序表非法）
        → 失败关闭：三项集合相同但 order 含重复，重复执行即重算同一产物。"""
        import recompute as R
        state = _registry_state()
        try:
            R.PRODUCT_ORDER = (state[0][0],) + state[0]
            self.assertEqual(set(R.PRODUCT_ORDER), set(R.PRODUCT_DEPS),
                             "变异前提：set 应相等")
            self.assertEqual(set(R.PRODUCT_ORDER), set(R.GENERATORS),
                             "变异前提：set 应相等")
            self.assertGreater(len(R.PRODUCT_ORDER), len(set(R.PRODUCT_ORDER)),
                               "变异未生效：无重复")
            with self.assertRaises(ProductMissing) as cm:
                recompute_all(_ctx())
            self.assertIn("重复项", str(cm.exception))
            self.assertIn(state[0][0], str(cm.exception))
        finally:
            _restore_registry(state)
        self.assertIs(R.PRODUCT_ORDER, state[0], "PRODUCT_ORDER 对象身份未恢复")
        self.assertEqual(R.PRODUCT_ORDER, state[0], "PRODUCT_ORDER 内容未恢复")

    def test_mutation_product_deps_add_fails(self):
        """变异注入：PRODUCT_DEPS 新增 order/generators 均无的键
        → 失败关闭：落库依赖表漂移必须被拒（F-4 落库语义不被削弱）。"""
        import recompute as R
        state = _registry_state()
        try:
            R.PRODUCT_DEPS["phantom_product"] = ("growth",)
            self.assertIn("phantom_product", R.PRODUCT_DEPS, "变异未生效")
            self.assertNotIn("phantom_product", R.PRODUCT_ORDER)
            self.assertNotIn("phantom_product", R.GENERATORS)
            with self.assertRaises(ProductMissing) as cm:
                recompute_all(_ctx())
            msg = str(cm.exception)
            self.assertIn("phantom_product", msg)
            self.assertIn("PRODUCT_DEPS 有而 PRODUCT_ORDER 无", msg)
            self.assertIn("PRODUCT_DEPS 有而 GENERATORS 无", msg)
        finally:
            _restore_registry(state)
        self.assertIs(R.PRODUCT_DEPS, state[1], "PRODUCT_DEPS 身份未恢复（重绑定泄漏）")
        self.assertEqual(R.PRODUCT_DEPS, state[3], "PRODUCT_DEPS 内容未恢复")
        self.assertNotIn("phantom_product", PRODUCT_DEPS,
                         "模块 import alias 不得读到被变异过的旧字典")


class TestCandidateFreezeAndInvalidation(unittest.TestCase):
    def test_candidate_frozen_new_id_on_recompute(self):
        """回算后新候选：内容寻址冻结；旧候选失效并保留。

        OI-PF-196：批准前后用**同一 run_id** —— 旧测试把 run-1 改 run-2
        （批准快照也变），即使冻结输入绑定缺失，run_id 变化也足以让测试
        继续绿（run_id 假证明）；候选必须携带 frozen_inputs_hash，且批准
        后冻结输入哈希与候选身份都变化。
        """
        with tempfile.TemporaryDirectory() as td:
            store = ArtifactStore(td)
            ctx = _ctx()
            c1 = freeze_candidate_from_recompute(store, ctx, "same-run",
                                                 recompute_all(ctx))
            self.assertIn("frozen_inputs_hash", c1.candidate,
                          "候选必须绑定规范冻结输入哈希（OI-PF-196）")
            self.assertRegex(c1.candidate["frozen_inputs_hash"],
                             r"^[0-9a-f]{64}$")
            ctx2 = _ctx(approve=["growth"])
            c2 = freeze_candidate_from_recompute(store, ctx2, "same-run",
                                                 recompute_all(ctx2))
            self.assertEqual(c1.candidate["run_id"], c2.candidate["run_id"],
                             "同一 run_id —— 候选变化不得来自 run_id")
            self.assertNotEqual(c1.candidate["frozen_inputs_hash"],
                                c2.candidate["frozen_inputs_hash"],
                                "批准假设后冻结输入哈希必须变")
            self.assertNotEqual(c1.candidate_id, c2.candidate_id,
                                "批准假设后候选必须变（冻结输入哈希驱动）")
            # 失效并保留：旧对象仍可读（保留），失效记录落库（失效）
            inv = invalidate_previous(store, c1.candidate_id,
                                      c2.candidate_id,
                                      reason="G6A-05 回算：growth 假设批准")
            self.assertRegex(inv, r"^[0-9a-f]{64}$")
            store.load(c1.candidate_id)   # 保留：读时哈希校验通过
            self.assertIn("approved_snapshot", c2.candidate)

    def test_frozen_candidate_idempotent_same_inputs_same_run(self):
        """OI-PF-196：同一完整 ctx + 同一 run_id + 同一 recompute 重复冻结
        → candidate 字节/ID 完全一致（内容寻址幂等）。"""
        with tempfile.TemporaryDirectory() as td:
            store = ArtifactStore(td)
            ctx = _ctx(approve=["growth"])
            r = recompute_all(ctx)
            c1 = freeze_candidate_from_recompute(store, ctx, "same-run", r)
            c2 = freeze_candidate_from_recompute(store, ctx, "same-run", r)
            self.assertEqual(c1.candidate_id, c2.candidate_id,
                             "同载荷重复冻结候选 ID 必须一致")
            self.assertEqual(c1.candidate, c2.candidate,
                             "同载荷重复冻结候选字节必须一致")
            self.assertEqual(store.load(c1.candidate_id),
                             store.load(c2.candidate_id))

    def test_frozen_inputs_payload_covers_all_frozen_input_fields(self):
        """OI-PF-196：规范冻结输入载荷字段覆盖（单一实现、固定 key）。"""
        payload = frozen_inputs_payload(_ctx())
        self.assertEqual(
            set(payload),
            {"contract", "facts", "macro", "formula_specs", "valuation_inputs",
             "assumption_defaults", "approved", "open_items_policy"})
        self.assertEqual(
            set(payload["valuation_inputs"]),
            {"scope", "currency", "as_of", "price", "shares_outstanding",
             "net_debt", "minority_interest", "industry_commodity", "statuses"},
            "valuation_inputs 须覆盖全部 dataclass 字段（含 statuses）")
        self.assertEqual(set(payload["open_items_policy"]),
                         {"tolerance", "owner_role", "due_date", "blocks_gate"},
                         "policy 须覆盖全部 dataclass 字段")
        self.assertEqual(set(payload["approved"]),
                         {"snapshot_id", "version", "sha256"},
                         "approved 须携带不可变身份与 sha256")
        self.assertEqual(payload["approved"]["sha256"], _ctx().approved.sha256,
                         "批准快照身份须为不可变 sha256")

    def test_frozen_inputs_hash_pins_every_top_level_input(self):
        """OI-PF-196：相同 run_id 且**显式固定同一 recompute/product_hashes**
        下，逐项扰动各顶层冻结输入 → frozen_inputs_hash 与 candidate_id 必须
        变化 —— 不靠产品输出变化证明。

        覆盖原失败载荷（macro 增加冻结字段）、formula_specs 同长度换内容、
        valuation_inputs 未消费字段（currency/statuses）、policy 字段，
        以及 contract/facts/assumption_defaults/approved。
        """
        base = _ctx()
        r = recompute_all(base)
        base_hash = frozen_inputs_hash(base)
        cases = [
            ("contract 增加字段",
             _ctx(contract={"contract_id": "C-600089", "scope": "600089.SH",
                            "industry": "电新"})),
            ("facts 值变化",
             _ctx(facts={"fcff": "400000000", "fcfe": "300000000",
                         "eps": "0.61", "book_per_share": "5.00"})),
            ("macro 增加冻结字段（原失败载荷）",
             _ctx(macro={"wacc_floor": "0.08", "cb_floor": "0.09"})),
            ("formula_specs 同长度换内容",
             _ctx(formula_specs={"fcff": {"formula": ".x."}})),
            ("valuation_inputs.currency 未消费字段",
             _ctx(valuation_inputs=_mk_vi(currency="USD"))),
            ("valuation_inputs.statuses 未消费字段",
             _ctx(valuation_inputs=_mk_vi(statuses={"price": "READY"}))),
            ("assumption_defaults 值变化",
             _ctx(assumption_defaults={"growth": "0.05", "wacc": "0.10",
                                       "ke": "0.13", "target_pe": "12",
                                       "roe": "0.12"})),
            ("approved 批准 growth",
             _ctx(approve=["growth"])),
            ("open_items_policy.tolerance 变化",
             _ctx(policy=_policy(tolerance="0.20"))),
            ("open_items_policy.owner_role 变化",
             _ctx(policy=_policy(owner_role="L12"))),
        ]
        with tempfile.TemporaryDirectory() as td:
            store = ArtifactStore(td)
            c_base = freeze_candidate_from_recompute(store, base, "same-run", r)
            for label, mut in cases:
                with self.subTest(perturb=label):
                    self.assertNotEqual(frozen_inputs_hash(mut), base_hash,
                                        f"{label} 未改变冻结输入哈希")
                    c2 = freeze_candidate_from_recompute(store, mut,
                                                         "same-run", r)
                    self.assertEqual(c2.candidate["product_hashes"],
                                     c_base.candidate["product_hashes"],
                                     "product_hashes 须固定同一 recompute —— "
                                     "不得变（不靠产品输出变化证明）")
                    self.assertNotEqual(
                        c2.candidate["frozen_inputs_hash"],
                        c_base.candidate["frozen_inputs_hash"],
                        f"{label} 未改变候选内冻结输入哈希")
                    self.assertNotEqual(
                        c2.candidate_id, c_base.candidate_id,
                        f"{label} 未改变候选身份（run_id 相同、product_hashes"
                        f" 相同，只能由冻结输入哈希驱动）")

    def test_frozen_inputs_hash_fails_closed_on_missing_or_malformed_inputs(
            self):
        """OI-PF-196：缺 policy / 字段形态不符 → RecomputeError E-G6A-05-003
        失败关闭，不生成 candidate（对象库原计数不变）。"""
        r = recompute_all(_ctx())
        cases = [
            ("缺 open_items_policy", _ctx(policy=None)),
            ("policy 形态不符（dict）",
             _ctx(policy={"tolerance": "0.15"})),
            ("valuation_inputs 形态不符",
             _ctx(valuation_inputs={"scope": "600089.SH"})),
            ("approved 形态不符", _ctx(approved="SNAP-G6A05")),
            ("contract 形态不符（str）", _ctx(contract="C-600089")),
        ]
        with tempfile.TemporaryDirectory() as td:
            store = ArtifactStore(td)
            before = _store_object_count(store)
            for label, bad in cases:
                with self.subTest(label=label):
                    with self.assertRaises(RecomputeError) as cm:
                        frozen_inputs_hash(bad)
                    self.assertIn("E-G6A-05-003", str(cm.exception),
                                  f"{label} 必须 RecomputeError 失败关闭")
                    with self.assertRaises(RecomputeError) as cm2:
                        freeze_candidate_from_recompute(store, bad,
                                                        "same-run", r)
                    self.assertIn("E-G6A-05-003", str(cm2.exception))
            self.assertEqual(_store_object_count(store), before,
                             "失败关闭不得产生任何 candidate 对象")

    def test_frozen_inputs_hash_fails_closed_on_non_serializable_input(self):
        """OI-PF-196：JSON 不可规范序列化输入（set）→ RecomputeError
        E-G6A-05-003 失败关闭，ArtifactStore 不产生 candidate ——
        按原对象计数证明（不新增对象）。不得用 str/repr 悄悄吞掉。"""
        bad = _ctx(macro={"wacc_floor": "0.08", "basket": {"a", "b"}})
        r = recompute_all(_ctx())
        with tempfile.TemporaryDirectory() as td:
            store = ArtifactStore(td)
            before = _store_object_count(store)
            with self.assertRaises(RecomputeError) as cm:
                freeze_candidate_from_recompute(store, bad, "same-run", r)
            self.assertIn("E-G6A-05-003", str(cm.exception))
            with self.assertRaises(RecomputeError):
                frozen_inputs_hash(bad)
            self.assertEqual(_store_object_count(store), before,
                             "不可序列化输入失败关闭后不得产生任何 candidate")

    def test_invalidate_unknown_candidate_fails(self):
        """失效记录引用不存在的旧候选 → 拒绝（保留无从谈起）。"""
        with tempfile.TemporaryDirectory() as td:
            store = ArtifactStore(td)
            with self.assertRaises(Exception) as ctx:
                invalidate_previous(store, "0" * 64, "1" * 64, reason="x")
            self.assertIn("E-G6A-05-002", str(ctx.exception))


class TestSnapshotIntegrityFailClosed(unittest.TestCase):
    """OI-PF-199：已冻结 AssumptionSnapshot 正文漂移失败关闭与防别名。

    原失败载荷：`ctx.approved.approved["A-GROWTH"]["growth"]="0.99"` 后
    `sha256` 仍返回 build 缓存值、`frozen_inputs_hash` 不变、同 run_id 同
    recompute 下 candidate ID 仍相同 —— 快照「不可变」声明不成立。本类逐项
    证明：正文漂移被可机检失败关闭，浅拷贝/返回值别名都被深拷贝阻断。
    """

    def test_mutate_approved_nested_value_invalidates_sha_and_payloads(self):
        """build 后直接改 snap.approved 嵌套值 → sha256 与 approved_payloads()
        必须失败关闭（可机检失败），不得静默返回缓存值。"""
        from assumption_snapshot import PayloadChanged
        ctx = _ctx(approve=["growth"])
        snap = ctx.approved
        good_sha = snap.sha256
        self.assertRegex(good_sha, r"^[0-9a-f]{64}$", "未篡改快照哈希须可用")
        snap.approved["A-GROWTH"]["growth"] = "0.99"   # 原失败载荷
        with self.assertRaises(PayloadChanged) as cm:
            snap.sha256
        self.assertIn("E-G3-13-010", str(cm.exception))
        with self.assertRaises(PayloadChanged):
            snap.approved_payloads()
        self.assertTrue(snap.invalidated, "正文漂移必须持久置位失效标志")
        with self.assertRaises(PayloadChanged):
            snap.approved_payloads()
        # 篡改值仍在（fail-closed 是拒绝而非静默修正）
        self.assertEqual(snap.approved["A-GROWTH"]["growth"], "0.99")

    def test_mutating_original_proposal_nested_payload_does_not_drift_snapshot(
            self):
        """build 后改原 proposal 的嵌套值 → 快照正文不受影响（防浅拷贝别名）。"""
        from assumption_snapshot import AssumptionRegistry
        reg = AssumptionRegistry()
        p = AssumptionProposal("A-GROWTH",
                               {"growth": "0.08", "meta": {"note": "orig"}},
                               proposed_by="L8")
        reg.propose(p)
        reg.decide(p.proposal_id, APPROVED, "U", "2026-08-12T12:00:00Z",
                   "APPROVE")
        snap = AssumptionSnapshot("SNAP-DEEPCOPY").build(reg)
        sha = snap.sha256
        p.payload["meta"]["note"] = "TAMPERED"   # 构建后改原 proposal 嵌套值
        self.assertEqual(snap.sha256, sha,
                         "快照正文不得被原 proposal 浅拷贝别名漂移")
        self.assertEqual(
            snap.approved_payloads()["A-GROWTH"]["meta"]["note"], "orig")

    def test_mutating_approved_payloads_return_value_does_not_drift_snapshot(
            self):
        """修改 approved_payloads() 返回值 → 快照正文不受影响（防返回值别名）。"""
        ctx = _ctx(approve=["growth"])
        snap = ctx.approved
        sha = snap.sha256
        out = snap.approved_payloads()
        out["A-GROWTH"]["growth"] = "0.99"
        self.assertEqual(snap.sha256, sha,
                         "返回值别名不得反向改变快照正文")
        self.assertEqual(snap.approved_payloads()["A-GROWTH"]["growth"], "0.08")

    def test_tampered_snapshot_freeze_fails_closed_no_candidate_stored(self):
        """同 run_id + 同一 recompute/product_hashes 下直接篡改 snapshot →
        freeze 必须转 RecomputeError E-G6A-05-003，且对象库计数不变。"""
        with tempfile.TemporaryDirectory() as td:
            store = ArtifactStore(td)
            ctx = _ctx(approve=["growth"])
            r = recompute_all(ctx)
            c1 = freeze_candidate_from_recompute(store, ctx, "same-run", r)
            self.assertTrue(store.exists(c1.candidate_id))
            before = _store_object_count(store)
            ctx.approved.approved["A-GROWTH"]["growth"] = "0.88"
            with self.assertRaises(RecomputeError) as cm:
                freeze_candidate_from_recompute(store, ctx, "same-run", r)
            self.assertIn("E-G6A-05-003", str(cm.exception),
                          "冻结 candidate 前必须对已篡改批准正文失败关闭")
            self.assertEqual(_store_object_count(store), before,
                             "失败关闭不得新增任何 candidate 对象")

    def test_frozen_inputs_hash_fails_closed_on_malformed_statuses(self):
        """ValuationInputs.statuses 显式展开的嵌套结构形态校验（OI-PF-199）：
        statuses=None（原失败载荷）/非 dict → RecomputeError E-G6A-05-003，
        不得泄漏裸 TypeError（'NoneType' object is not iterable）。"""
        r = recompute_all(_ctx())
        cases = [
            ("statuses=None（原失败载荷）", _mk_vi(statuses=None)),
            ("statuses 非 dict（list）", _mk_vi(statuses=["READY"])),
        ]
        with tempfile.TemporaryDirectory() as td:
            store = ArtifactStore(td)
            before = _store_object_count(store)
            for label, bad_vi in cases:
                with self.subTest(label=label):
                    bad = _ctx(valuation_inputs=bad_vi)
                    with self.assertRaises(RecomputeError) as cm:
                        frozen_inputs_hash(bad)
                    self.assertIn("E-G6A-05-003", str(cm.exception),
                                  f"{label} 必须 RecomputeError 失败关闭")
                    with self.assertRaises(RecomputeError) as cm2:
                        freeze_candidate_from_recompute(store, bad, "same-run", r)
                    self.assertIn("E-G6A-05-003", str(cm2.exception))
            self.assertEqual(_store_object_count(store), before,
                             "形态非法失败关闭不得产生任何 candidate 对象")

    def test_invalidated_stays_closed_after_body_restored(self):
        """G6-RT-FIX-04：正文漂移触发失效后，即使把正文改回原值，快照仍是
        单向永久失败态 —— sha256/approved_payloads 继续拒绝，同 run_id 同
        recompute 下 freeze 转 RecomputeError 且不新增 candidate。

        原失败载荷（主 Agent 实测）：漂移读 sha256 抛错并置 invalidated 后，
        正文改回 0.08 又返回 64 位哈希、freeze 重新生成 candidate
        （ID 前缀 b542e01b0fd9）。"""
        from assumption_snapshot import PayloadChanged
        ctx = _ctx(approve=["growth"])
        snap = ctx.approved
        r = recompute_all(ctx)
        with tempfile.TemporaryDirectory() as td:
            store = ArtifactStore(td)
            c1 = freeze_candidate_from_recompute(store, ctx, "same-run", r)
            self.assertTrue(store.exists(c1.candidate_id))
            before = _store_object_count(store)
            snap.approved["A-GROWTH"]["growth"] = "0.99"   # 漂移
            with self.assertRaises(PayloadChanged):
                snap.sha256                                 # 触发失效
            self.assertTrue(snap.invalidated, "漂移必须持久置位失效标志")
            snap.approved["A-GROWTH"]["growth"] = "0.08"   # 正文改回原值
            with self.assertRaises(PayloadChanged) as cm:
                snap.sha256
            self.assertIn("E-G3-13-010", str(cm.exception),
                          "已失效快照即使正文恢复也须拒绝 sha256")
            with self.assertRaises(PayloadChanged):
                snap.approved_payloads()
            with self.assertRaises(RecomputeError) as cm2:
                freeze_candidate_from_recompute(store, ctx, "same-run", r)
            self.assertIn("E-G6A-05-003", str(cm2.exception),
                          "永久失效快照 freeze 必须失败关闭")
            self.assertEqual(_store_object_count(store), before,
                             "永久失效快照不得重新生成任何 candidate 对象")

    def test_non_serializable_body_drift_fails_closed(self):
        """G6-RT-FIX-04：正文注入不可序列化值（set）→ sha256 必须归一为
        PayloadChanged E-G3-13-010（保留异常链，不泄漏裸 TypeError）且置
        invalidated；freeze 转 RecomputeError 且不新增 candidate。"""
        from assumption_snapshot import PayloadChanged
        ctx = _ctx(approve=["growth"])
        snap = ctx.approved
        r = recompute_all(ctx)
        snap.approved["A-GROWTH"]["bad"] = {"x"}   # 原失败载荷：不可 JSON 序列化
        with self.assertRaises(PayloadChanged) as cm:
            snap.sha256
        self.assertIn("E-G3-13-010", str(cm.exception),
                      "不可序列化正文漂移必须归一为 PayloadChanged")
        self.assertIn("无法哈希", str(cm.exception))
        self.assertIsInstance(cm.exception.__cause__, TypeError,
                              "异常链必须保留原始 TypeError（不得用 str 吞掉）")
        self.assertTrue(snap.invalidated,
                        "不可序列化正文漂移必须持久置位失效标志")
        with self.assertRaises(PayloadChanged):
            snap.approved_payloads()
        with tempfile.TemporaryDirectory() as td:
            store = ArtifactStore(td)
            before = _store_object_count(store)
            with self.assertRaises(RecomputeError) as cm2:
                freeze_candidate_from_recompute(store, ctx, "same-run", r)
            self.assertIn("E-G6A-05-003", str(cm2.exception))
            self.assertEqual(_store_object_count(store), before,
                             "不可序列化正文漂移不得新增任何 candidate 对象")

    def test_build_time_invalidated_snapshot_rejects_sha_and_freeze(self):
        """G6-RT-FIX-04：build() 时因批准后 proposal 漂移而 _invalidated=True
        的快照（当前正文可哈希）—— sha256 与 approved_payloads() 都必须拒绝，
        freeze 转 RecomputeError 且不新增 candidate（不得生成候选）。"""
        from assumption_snapshot import AssumptionProposal, PayloadChanged
        from assumption_snapshot import APPROVED
        from assumption_snapshot import AssumptionRegistry
        reg = AssumptionRegistry()
        p = AssumptionProposal("A-BUILD", {"g": "8%"}, proposed_by="L8")
        reg.propose(p)
        reg.decide(p.proposal_id, APPROVED, "U", "2026-08-12T12:00:00Z",
                   "APPROVE")
        p.payload["g"] = "80%"   # 批准后、build 前漂移 → build 时置失效
        snap = AssumptionSnapshot("S-BUILD-INVALID").build(reg)
        self.assertTrue(snap.invalidated, "批准 payload 变化必须使 build 失效")
        with self.assertRaises(PayloadChanged) as cm:
            snap.sha256
        self.assertIn("E-G3-13-010", str(cm.exception),
                      "build-time 失效快照 sha256 必须拒绝")
        with self.assertRaises(PayloadChanged):
            snap.approved_payloads()
        with tempfile.TemporaryDirectory() as td:
            store = ArtifactStore(td)
            r = recompute_all(_ctx(approve=["growth"]))   # 健康回算结果
            ctx = _ctx(approved=snap)
            before = _store_object_count(store)
            with self.assertRaises(RecomputeError) as cm2:
                freeze_candidate_from_recompute(store, ctx, "same-run", r)
            self.assertIn("E-G6A-05-003", str(cm2.exception),
                          "build-time 失效快照 freeze 必须失败关闭")
            self.assertEqual(_store_object_count(store), before,
                             "build-time 失效快照不得新增任何 candidate 对象")


if __name__ == "__main__":
    unittest.main()
