"""G6A-06 PARTIAL 验收测试：显式估值路由声明驱动的 PARTIAL 最终候选。

冻结于 2026-07-24 历史红队目标（非发布/非交易）：当特定估值路由显式声明
INPUT_MISSING / NOT_EVALUATED 时，生成**真** PARTIAL 最终候选，不发明数值。

本文件验证（对照实现要求）：
  ① 受管请求契约：四路声明（fcff/fcfe/relative/pe_roe_pb）必填，状态只允许
     READY/INPUT_MISSING/NOT_EVALUATED；非 READY 必须带非空 reason + 非空
     证据引用列表；声明与 facts 数值事实相互矛盾、未知路由/状态/字段、缺失
     声明 → 稳定 E-G6A-06-020 失败关闭。
  ② 确定性 typed 产物：11 项产品全部保留；非 READY 估值/情景产品为 typed
     状态产品且不含 per-share 数值；READY 估值产品带显式 PASS 状态。
  ③ 开放项：每个声明非 READY 的路由登记确定性 material OPEN 开放项
     （owner/due_date/blocks_gate 取冻结 OpenItemsPolicy，reason/证据引用
     保留）；交叉验证只取 READY 路由，空/单路集不得冒充全局 PASS。
  ④ 候选根：quality_status / release_eligible 只由 canonical 产物派生；
     任一 material OPEN 项或非 READY 路由 → PARTIAL 且不可发布。
  ⑤ 发布边界：批准/准出/发布拒绝 PARTIAL 最终候选（即使无单独 DB 开放项），
     缺失/畸形资格元数据失败关闭；legacy G4 candidate 不受影响。
  ⑥ bundle 复验：根质量/发布字段必须与实际加载产物一致，篡改失败关闭。

全部 fixture 为本地合成值；不读写任何原始证据区。
"""
import os
import shutil
import sys
import tempfile
import unittest

import json  # noqa: E402

APP = os.path.join(os.path.dirname(__file__), "..", "app")
sys.path.insert(0, APP)
sys.path.insert(0, os.path.dirname(__file__))

from artifact_store import ArtifactStore  # noqa: E402
import copy  # noqa: E402
import _g4_fixtures as fx  # noqa: E402
from assumption_snapshot import (  # noqa: E402
    APPROVED, AssumptionProposal, AssumptionRegistry, AssumptionSnapshot,
)
from candidate_service import (  # noqa: E402
    CandidateFreezeService, CandidateRequestError, CandidateVerificationError,
    FINAL_CANDIDATE_REQUEST_KIND, bundle_manifest_objects,
    freeze_final_candidate_from_payload,
)
from publish_engine import (  # noqa: E402
    RESEARCH_600089_KEY, audit_candidate, canonical_bytes, compute_closure,
    content_id, create_approval, final_candidate_release_gate, freeze_object,
    gc_orphans, is_release_eligible, publish_release,
)
from recompute import (  # noqa: E402
    PRODUCT_ORDER, ROUTE_FACT_KEYS, ROUTE_INPUT_MISSING, ROUTE_NOT_EVALUATED,
    ROUTE_READY, VALUATION_PRODUCT_NAMES, VALUATION_ROUTES, OpenItemsPolicy,
    QualityError, RecomputeError,
    ResearchContext, RouteDeclaration, ValuationRoutes,
    freeze_candidate_from_recompute, frozen_inputs_hash, quality_from_products,
    recompute_all,
)
from repository import (  # noqa: E402
    Approval, CurrentPointer, Release, create_repository,
)
from schema_validate import SchemaError, validate_object  # noqa: E402
from valuation_engine import ValuationInputs  # noqa: E402

_REV_A = "a" * 40
_TREE_A = "b" * 40
_EXPECTED = {"expected_source_commit": _REV_A,
             "expected_source_tree": _TREE_A}

_ALL_ROUTE_KEYS = ("fcff", "fcfe", "relative", "pe_roe_pb")


def _routes(**over):
    """四路 RouteDeclaration 容器：默认全 READY，可逐路覆盖。"""
    base = {r: RouteDeclaration(ROUTE_READY) for r in VALUATION_ROUTES}
    base.update(over)
    return ValuationRoutes(base)


def _decl(route, state, reason="", refs=(), missing=()):
    return RouteDeclaration(state, reason, refs, missing)


def _routes_dict(**over):
    """四路声明的 JSON dict 形态（供受管请求）。默认全 READY；覆盖项原样
    透传（READY 带 reason/未知字段等畸形组合由被测代码负责拒绝）。"""
    base = {r: {"state": ROUTE_READY} for r in VALUATION_ROUTES}
    for r, d in over.items():
        base[r] = dict(d)
    return base


def _facts_for_routes(routes):
    """按声明生成一致 facts：非 READY 路由不夹带该路数值事实。"""
    facts = {"fcff": "400000000", "fcfe": "300000000", "eps": "0.60",
             "book_per_share": "5.00"}
    for r, decl in routes.routes.items():
        if decl.state != ROUTE_READY:
            facts.pop(ROUTE_FACT_KEYS[r], None)
    return facts


def _policy(tolerance="0.15", owner_role="U", due_date="2026-08-31",
            blocks_gate="G3-06"):
    return OpenItemsPolicy(tolerance=tolerance, owner_role=owner_role,
                           due_date=due_date, blocks_gate=blocks_gate)


def _mk_vi(**over):
    fields = dict(scope="600089.SH", currency="CNY", as_of="2026-07-01",
                  price="10.00", shares_outstanding="1000000000",
                  net_debt="200000000", minority_interest="0")
    fields.update(over)
    return ValuationInputs(**fields)


def _ctx(approve=None, *, routes=None, facts=None, policy=None):
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
    for key in (approve or ()):
        reg.decide(props[key].proposal_id, APPROVED, "U",
                   "2026-08-12T12:00:00Z", "APPROVE")
    snap = AssumptionSnapshot("SNAP-PARTIAL").build(reg)
    routes = routes or _routes()
    return ResearchContext(
        contract={"contract_id": "C-600089", "scope": "600089.SH"},
        facts=facts if facts is not None else _facts_for_routes(routes),
        macro={"wacc_floor": "0.08"},
        formula_specs={"fcff": {"formula": "..."}},
        valuation_inputs=_mk_vi(),
        assumption_defaults={"growth": "0.05", "wacc": "0.10", "ke": "0.12",
                             "target_pe": "12", "roe": "0.12"},
        approved=snap,
        open_items_policy=_policy() if policy is None else policy,
        valuation_routes=routes,
    )


def _request_payload(routes_dict=None, facts=None, *, approve=None,
                     policy=None, run_id="partial-run",
                     source_commit=_REV_A, source_tree=_TREE_A):
    """受管 JSON 请求（合成）：proposal + decision 重建批准快照。

    approve 为需批准（写入 decisions）的假设键列表；缺省与历史 fixture 一致
    只批准 growth，供 schema 契约测试与请求入口测试使用。
    """
    values = {
        "growth": ("A-GROWTH", "0.08"),
        "wacc": ("A-WACC", "0.09"),
        "ke": ("A-KE", "0.13"),
        "target_pe": ("A-PE", "15"),
        "roe": ("A-ROE", "0.15"),
    }
    proposals = [
        {"proposal_id": pid, "payload": {key: value}, "proposed_by": "L8"}
        for key, (pid, value) in values.items()
    ]
    decisions = [
        {"proposal_id": values[key][0], "decision": "APPROVED",
         "approver": "U", "decided_at": "2026-08-12T12:00:00Z",
         "token": "APPROVE"}
        for key in (("growth",) if approve is None else approve)
    ]
    default_facts = {"fcff": "400000000", "fcfe": "300000000",
                     "eps": "0.60", "book_per_share": "5.00"}
    rdict = routes_dict or _routes_dict()
    if facts is None:
        facts = dict(default_facts)
        for r, d in rdict.items():
            if d["state"] != ROUTE_READY:
                facts.pop(ROUTE_FACT_KEYS[r], None)
    else:
        facts = copy.deepcopy(facts)   # 防冻结输入与载荷别名（载荷改动不得漂移 ctx）
    pol = policy or {
        "tolerance": "0.15", "owner_role": "U",
        "due_date": "2026-08-31", "blocks_gate": "G3-06",
    }
    return {
        "schema_version": "1.1.0",
        "run_id": run_id,
        "source_revision": {"source_commit": source_commit,
                            "source_tree": source_tree},
        "context": {
            "contract": {"contract_id": "C-600089", "scope": "600089.SH"},
            "facts": facts,
            "macro": {"wacc_floor": "0.08"},
            "formula_specs": {"fcff": {"formula": "..."}},
            "valuation_inputs": {
                "scope": "600089.SH", "currency": "CNY",
                "as_of": "2026-07-01", "price": "10.00",
                "shares_outstanding": "1000000000",
                "net_debt": "200000000", "minority_interest": "0",
            },
            "assumption_defaults": {
                "growth": "0.05", "wacc": "0.10", "ke": "0.12",
                "target_pe": "12", "roe": "0.12"},
            "approved_snapshot": {
                "snapshot_id": "SNAP-PARTIAL", "version": 1,
                "proposals": proposals, "decisions": decisions,
            },
            "open_items_policy": pol,
            "valuation_routes": rdict,
        },
    }


def _routes_dict_from_ctx(ctx):
    """把已构建 ctx 的四路声明转回 JSON dict 形态（供冻结绑定 payload）。"""
    return {r: ctx.valuation_routes.routes[r].to_dict()
            for r in VALUATION_ROUTES}


def _payload_from_ctx(ctx, run_id="partial-run"):
    """由已构建 ResearchContext 生成**等价**受管请求载荷（G6A-06 冻结绑定）。

    用于直接调用 freeze_final_candidate 的测试：请求经 final_candidate_request
    重建必须与 ctx 逐字等价（run_id/冻结输入/批准快照/contract/scope/as_of）。
    """
    p = ctx.open_items_policy
    return _request_payload(
        routes_dict=_routes_dict_from_ctx(ctx), facts=ctx.facts,
        approve=sorted(ctx.approved_keys()),
        policy={"tolerance": p.tolerance, "owner_role": p.owner_role,
                "due_date": p.due_date, "blocks_gate": p.blocks_gate},
        run_id=run_id)


def _complete_manifest_objects(store, bundle, report_digest=None):
    """已验证 bundle → 精确闭包登记表：candidate refs = request_hash + 11
    项产品哈希（`bundle_manifest_objects` 生产助手），request/产品为叶子条目；
    report 可选挂到 request 条目下以通过 G4 审计（render_report_text 门）。"""
    objects = bundle_manifest_objects(bundle)
    if report_digest:
        objects[bundle.request_hash] = {
            "kind": FINAL_CANDIDATE_REQUEST_KIND, "refs": [report_digest]}
        objects[report_digest] = {"kind": "report", "refs": []}
    return objects


class _StoreBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self.store = ArtifactStore(os.path.join(self._tmp, "lib"))

    def tearDown(self):
        shutil.rmtree(self._tmp, ignore_errors=True)

    def freeze(self, ctx=None, run_id="partial-run"):
        ctx = ctx or _ctx()
        service = CandidateFreezeService(self.store)
        return service.freeze_final_candidate(
            ctx, run_id, _REV_A, _TREE_A, recompute_all(ctx),
            request_payload=_payload_from_ctx(ctx, run_id))


class TestPartialRequestContract(unittest.TestCase):
    """① 受管请求契约：四路声明必填、状态合法、非 READY 须 reason/证据、
    声明与 facts 数值事实不得矛盾。"""

    def _reject(self, payload, label=None):
        with tempfile.TemporaryDirectory() as tmp:
            store = ArtifactStore(os.path.join(tmp, "objects"))
            with self.assertRaises(CandidateRequestError) as cm:
                freeze_final_candidate_from_payload(
                    store, payload, source_commit=_REV_A, source_tree=_TREE_A)
            self.assertIn("E-G6A-06-020", str(cm.exception), label or "")
            count = sum(len(fs) for _, _, fs in os.walk(str(store.root)))
            self.assertEqual(count, 0, f"{label} 失败关闭不得写任何对象")

    def test_missing_route_declaration_rejected(self):
        """缺一路声明（仅三路）→ E-G6A-06-020 失败关闭。"""
        rd = _routes_dict()
        del rd["relative"]
        self._reject(_request_payload(routes_dict=rd),
                     "缺 relative 声明")

    def test_unknown_route_rejected(self):
        """声明中出现非注册路由 → E-G6A-06-020 失败关闭。"""
        rd = _routes_dict()
        rd["sotp"] = {"state": ROUTE_READY}
        self._reject(_request_payload(routes_dict=rd), "未知路由 sotp")

    def test_unknown_state_rejected(self):
        """状态不是 READY/INPUT_MISSING/NOT_EVALUATED → 失败关闭。"""
        rd = _routes_dict(fcff={"state": "PARTIAL_CALC", "reason": "r",
                                "evidence_refs": ["EV-1"]})
        self._reject(_request_payload(routes_dict=rd), "未知状态")

    def test_ready_route_missing_fact_rejected(self):
        """声明 READY 但 facts 缺该路必需事实 → 声明/事实矛盾。"""
        facts = {"fcff": "400000000", "fcfe": "300000000",
                 "eps": "0.60"}
        self._reject(_request_payload(facts=facts),
                     "READY pe_roe_pb 缺 book_per_share")

    def test_non_ready_route_smuggled_fact_rejected(self):
        """非 READY 路由不得夹带该路数值事实 → 声明/事实矛盾。"""
        rd = _routes_dict(fcfe={"state": ROUTE_INPUT_MISSING,
                                "reason": "缺少净债务",
                                "evidence_refs": ["EV-9"],
                                "missing_inputs": ["net_debt"]})
        facts = {"fcff": "400000000", "fcfe": "300000000",
                 "eps": "0.60", "book_per_share": "5.00"}
        self._reject(_request_payload(routes_dict=rd, facts=facts),
                     "fcfe 非 READY 仍带 fcfe 数值事实")

    def test_ready_route_with_reason_rejected(self):
        """READY 路由携带 reason/evidence_refs → 失败关闭（不合法组合）。"""
        rd = _routes_dict(fcff={"state": ROUTE_READY, "reason": "r",
                                "evidence_refs": ["EV-1"]})
        self._reject(_request_payload(routes_dict=rd), "READY 带 reason")

    def test_non_ready_missing_reason_rejected(self):
        """非 READY 缺 reason → 失败关闭。"""
        rd = _routes_dict(relative={"state": ROUTE_NOT_EVALUATED,
                                    "reason": "",
                                    "evidence_refs": ["EV-2"]})
        self._reject(_request_payload(routes_dict=rd), "非 READY 缺 reason")

    def test_non_ready_empty_evidence_refs_rejected(self):
        """非 READY 证据引用列表为空 → 失败关闭。"""
        rd = _routes_dict(pe_roe_pb={"state": ROUTE_INPUT_MISSING,
                                     "reason": "缺 ROE",
                                     "evidence_refs": []})
        self._reject(_request_payload(routes_dict=rd), "空 evidence_refs")

    def test_unknown_declaration_field_rejected(self):
        """声明内出现未知字段 → 失败关闭。"""
        rd = _routes_dict(fcff={"state": ROUTE_READY, "extra": 1})
        self._reject(_request_payload(routes_dict=rd), "未知声明字段")

    def test_input_missing_declaration_allowed_with_missing_inputs(self):
        """INPUT_MISSING 可显式列出 missing_inputs；健康声明被接受。"""
        rd = _routes_dict(fcfe={"state": ROUTE_INPUT_MISSING,
                                "reason": "缺少净债务",
                                "evidence_refs": ["EV-9"],
                                "missing_inputs": ["net_debt"]})
        payload = _request_payload(routes_dict=rd)
        with tempfile.TemporaryDirectory() as tmp:
            store = ArtifactStore(os.path.join(tmp, "objects"))
            result = freeze_final_candidate_from_payload(
                store, payload, source_commit=_REV_A, source_tree=_TREE_A)
            self.assertEqual(result.candidate["quality_status"], "PARTIAL")


class TestPartialRecomputeProducts(unittest.TestCase):
    """② 确定性 typed 产物与 ③ material 开放项。"""

    def test_eleven_products_preserved_with_non_ready_routes(self):
        """非 READY 路由存在时 11 项产品仍全部生成（不丢产品）。"""
        ctx = _ctx(routes=_routes(
            fcfe=_decl("fcfe", ROUTE_INPUT_MISSING, "缺少净债务",
                       ("EV-9",), ("net_debt",))))
        res = recompute_all(ctx)
        self.assertEqual(tuple(res.products), tuple(PRODUCT_ORDER))
        self.assertEqual(len(res.products), 11)
        r1 = recompute_all(ctx)
        self.assertEqual(res.shas, r1.shas, "同冻结输入确定性一致")

    def test_non_ready_valuation_typed_status_product_no_per_share(self):
        """非 READY 估值产品为 typed 状态产品：无 per-share 数值字段，
        携带 reason/evidence_refs；READY 产品带 PASS 与完整数值。"""
        ctx = _ctx(routes=_routes(
            fcfe=_decl("fcfe", ROUTE_INPUT_MISSING, "缺少净债务",
                       ("EV-9",), ("net_debt",)),
            fcff=_decl("fcff", ROUTE_NOT_EVALUATED, "无证据", ("EV-1",))))
        res = recompute_all(ctx)
        for name, route in (("valuation_fcfe", "fcfe"),
                            ("valuation_fcff", "fcff")):
            prod = res.products[name]
            self.assertIn(prod["status"], (ROUTE_INPUT_MISSING,
                                           ROUTE_NOT_EVALUATED))
            for key in ("per_share_low", "per_share_high", "per_share_base"):
                self.assertNotIn(key, prod, f"{name} 不得夹带 per-share 数值")
            self.assertTrue(prod["reason"])
            self.assertTrue(prod["evidence_refs"])
        for name in ("valuation_relative", "valuation_pe_roe_pb"):
            prod = res.products[name]
            self.assertEqual(prod["status"], "PASS")
            self.assertIn("per_share_base", prod)

    def test_scenarios_propagate_fcfe_non_ready_state(self):
        """FCFE 非 READY → 三情景产品传播 typed 状态，无数值。"""
        ctx = _ctx(routes=_routes(
            fcfe=_decl("fcfe", ROUTE_NOT_EVALUATED, "无 FCFE 证据",
                       ("EV-3",))))
        res = recompute_all(ctx)
        for name in ("scenario_pessimistic", "scenario_base",
                     "scenario_optimistic"):
            prod = res.products[name]
            self.assertEqual(prod["status"], ROUTE_NOT_EVALUATED)
            self.assertEqual(prod["method"], "FCFE")
            self.assertNotIn("per_share_base", prod)
            self.assertTrue(prod["evidence_refs"])

    def test_open_items_material_for_each_non_ready_route(self):
        """每个声明非 READY 路由 → 确定性 material OPEN 开放项，owner/
        due_date/blocks_gate 取冻结 policy，reason/证据引用保留。"""
        ctx = _ctx(routes=_routes(
            fcfe=_decl("fcfe", ROUTE_INPUT_MISSING, "缺少净债务",
                       ("EV-9",), ("net_debt",)),
            relative=_decl("relative", ROUTE_NOT_EVALUATED, "无对标",
                           ("EV-2",))))
        prod = recompute_all(ctx).products["open_items"]
        items = {it["open_item_id"]: it for it in prod["open_items"]}
        self.assertIn("OI-G6A06-RC-FCFE-INPUT_MISSING", items)
        self.assertIn("OI-G6A06-RC-RELATIVE-NOT_EVALUATED", items)
        for oid, it in items.items():
            if oid.startswith("OI-G6A06-RC-"):
                self.assertIs(it["material"], True)
                self.assertEqual(it["status"], "OPEN")
                self.assertEqual(it["owner_role"], "U")
                self.assertEqual(it["due_date"], "2026-08-31")
                self.assertEqual(it["blocks_gate"], "G3-06")
                self.assertIn("缺少净债务" if "FCFE" in oid else "无对标",
                              it["description"])
        self.assertEqual(prod["route_statuses"]["fcfe"], ROUTE_INPUT_MISSING)
        self.assertEqual(prod["route_statuses"]["relative"],
                         ROUTE_NOT_EVALUATED)

    def test_cross_check_only_ready_routes(self):
        """交叉验证只取 READY 路由：FCFE 非 READY 时诊断不得出现 FCFE。"""
        ctx = _ctx(routes=_routes(
            fcfe=_decl("fcfe", ROUTE_INPUT_MISSING, "缺少净债务",
                       ("EV-9",), ("net_debt",))))
        prod = recompute_all(ctx).products["open_items"]
        for d in prod["cross_check"]:
            self.assertNotIn("FCFE", (d["method_a"], d["method_b"]),
                             "非 READY 路由不得参与交叉验证")
        self.assertEqual(prod["route_statuses"]["fcfe"], ROUTE_INPUT_MISSING)

    def test_fully_ready_wide_tolerance_full_quality(self):
        """全 READY + 宽容差 → 无开放项、质量 FULL、可发布；PASS 形状不变。"""
        ctx = _ctx(routes=_routes(), policy=_policy(tolerance="2"))
        res = recompute_all(ctx)
        prod = res.products["open_items"]
        self.assertEqual(prod["open_items"], [])
        self.assertEqual(prod["cross_check"], [])
        for name in ("valuation_fcff", "valuation_fcfe",
                     "valuation_relative", "valuation_pe_roe_pb",
                     "scenario_pessimistic", "scenario_base",
                     "scenario_optimistic"):
            self.assertEqual(res.products[name]["status"], "PASS",
                             "全 READY 估值/情景产物带显式 PASS 状态")
        quality, eligible = quality_from_products(res.products)
        self.assertEqual((quality, eligible), ("FULL", True))

    def test_partial_quality_even_when_only_one_route_ready(self):
        """只评估一路（其余全非 READY）也不得冒充全局 PASS —— 质量 PARTIAL。"""
        ctx = _ctx(routes=_routes(
            fcfe=_decl("fcfe", ROUTE_INPUT_MISSING, "缺输入", ("EV-9",), ("net_debt",)),
            relative=_decl("relative", ROUTE_NOT_EVALUATED, "无对标", ("EV-2",)),
            pe_roe_pb=_decl("pe_roe_pb", ROUTE_NOT_EVALUATED, "无数据",
                            ("EV-4",))))
        res = recompute_all(ctx)
        quality, eligible = quality_from_products(res.products)
        self.assertEqual((quality, eligible), ("PARTIAL", False))
        oi = res.products["open_items"]["open_items"]
        self.assertGreaterEqual(len(oi), 3,
                                "三个非 READY 路由须各登记 material 项")

    def test_frozen_inputs_hash_pins_route_declarations(self):
        """路由声明是冻结输入：改声明 → 冻结输入哈希变化。"""
        base = _ctx(routes=_routes())
        mut = _ctx(routes=_routes(
            fcfe=_decl("fcfe", ROUTE_INPUT_MISSING, "缺输入", ("EV-9",), ("net_debt",))))
        self.assertNotEqual(frozen_inputs_hash(base), frozen_inputs_hash(mut))


class TestPartialCandidateMetadata(_StoreBase):
    """④ 候选根：quality_status / release_eligible 只由产物派生。"""

    def test_partial_candidate_root_not_release_eligible(self):
        fr = self.freeze(_ctx(routes=_routes(
            fcfe=_decl("fcfe", ROUTE_INPUT_MISSING, "缺输入", ("EV-9",), ("net_debt",)))))
        self.assertEqual(fr.candidate["quality_status"], "PARTIAL")
        self.assertIs(fr.candidate["release_eligible"], False)

    def test_full_candidate_root_release_eligible(self):
        fr = self.freeze(_ctx(routes=_routes(),
                              policy=_policy(tolerance="2")))
        self.assertEqual(fr.candidate["quality_status"], "FULL")
        self.assertIs(fr.candidate["release_eligible"], True)

    def test_payload_entry_produces_partial_candidate(self):
        rd = _routes_dict(fcfe={"state": ROUTE_INPUT_MISSING,
                                "reason": "缺少净债务",
                                "evidence_refs": ["EV-9"],
                                "missing_inputs": ["net_debt"]})
        with tempfile.TemporaryDirectory() as tmp:
            store = ArtifactStore(os.path.join(tmp, "objects"))
            result = freeze_final_candidate_from_payload(
                store, _request_payload(routes_dict=rd),
                source_commit=_REV_A, source_tree=_TREE_A)
            self.assertEqual(result.candidate["quality_status"], "PARTIAL")
            self.assertIs(result.candidate["release_eligible"], False)


class TestPartialBundleVerify(_StoreBase):
    """⑥ bundle 复验：根质量/发布字段须与实际加载产物一致。"""

    def test_bundle_verify_partial_consistent(self):
        fr = self.freeze(_ctx(routes=_routes(
            fcfe=_decl("fcfe", ROUTE_INPUT_MISSING, "缺输入", ("EV-9",), ("net_debt",)))))
        service = CandidateFreezeService(self.store)
        b = service.load_candidate_bundle(fr.candidate_id, **_EXPECTED)
        self.assertEqual(b.candidate["quality_status"], "PARTIAL")
        self.assertIs(b.candidate["release_eligible"], False)
        v = service.verify_candidate_bundle(fr.candidate_id, **_EXPECTED)
        self.assertEqual(v["product_count"], 11)

    def test_tampered_quality_status_rejected(self):
        fr = self.freeze(_ctx(routes=_routes(
            fcfe=_decl("fcfe", ROUTE_INPUT_MISSING, "缺输入", ("EV-9",),
                       ("net_debt",)))))
        cand = dict(fr.candidate)
        self.assertEqual(cand["quality_status"], "PARTIAL")
        # 契约强制 FULL⇔true：同步改两字段保持 schema 一致，但产物仍是 PARTIAL
        # —— 根/产物不一致须被 bundle 复验以 E-G6A-06-018 失败关闭。
        cand["quality_status"] = "FULL"
        cand["release_eligible"] = True
        service = CandidateFreezeService(self.store)
        with self.assertRaises(CandidateVerificationError) as cm:
            service._verify_dict(cand, **_EXPECTED)
        self.assertIn("E-G6A-06-018", str(cm.exception))

    def test_tampered_release_eligible_rejected(self):
        fr = self.freeze(_ctx(routes=_routes(), policy=_policy(tolerance="2")))
        cand = dict(fr.candidate)
        self.assertEqual(cand["quality_status"], "FULL")
        cand["release_eligible"] = False
        cand["quality_status"] = "PARTIAL"
        service = CandidateFreezeService(self.store)
        with self.assertRaises(CandidateVerificationError) as cm:
            service._verify_dict(cand, **_EXPECTED)
        self.assertIn("E-G6A-06-018", str(cm.exception))

    def test_missing_quality_metadata_fails_closed(self):
        fr = self.freeze(_ctx(routes=_routes()))
        cand = dict(fr.candidate)
        del cand["quality_status"]
        service = CandidateFreezeService(self.store)
        with self.assertRaises(CandidateVerificationError) as cm:
            service._verify_dict(cand, **_EXPECTED)
        self.assertIn("E-G6A-06-013", str(cm.exception))


class TestPartialReleaseBoundary(unittest.TestCase):
    """⑤ 发布边界：批准/准出/发布拒绝 PARTIAL 最终候选（即使无 DB 开放项），
    缺失/畸形资格元数据失败关闭；legacy G4 candidate 不受影响。"""

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self.store = ArtifactStore(os.path.join(self._tmp, "lib"))
        self.repo = create_repository(os.path.join(self._tmp, "pub.sqlite3"))
        self.repo.create_all()
        self.s = self.repo.session()

    def tearDown(self):
        self.s.close()
        self.repo.engine.dispose()
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _freeze_partial(self):
        from candidate_service import CandidateFreezeService
        ctx = _ctx(routes=_routes(fcfe=_decl(
            "fcfe", ROUTE_INPUT_MISSING, "缺输入", ("EV-9",),
            ("net_debt",))))
        service = CandidateFreezeService(self.store)
        return service.freeze_final_candidate(
            ctx, "partial-run", _REV_A, _TREE_A, recompute_all(ctx),
            request_payload=_payload_from_ctx(ctx))

    def _approval_for(self, manifest, cand):
        from publish_engine import inputs_hash
        appr = Approval(
            id="APR_PARTIAL_GATE",
            schema_version="1.0.0",
            object_ref=manifest["subject_root"],
            approver="U",
            approved_at=__import__("datetime").datetime(2026, 8, 11, 7, 0, 0),
            subject_root_hash="0" * 64,
            workflow=manifest["workflow"],
            scope_id=manifest["scope_id"],
            current_key=manifest["current_key"],
            inputs_hash=inputs_hash(manifest, cand),
            status="ACTIVE", token="APPROVE", version=1)
        self.s.add(appr)
        self.s.commit()
        return appr

    def test_approval_rejects_partial_final_candidate_no_db_open_item(self):
        """批准拒绝 PARTIAL 最终候选：即使没有任何 DB 开放项行（元数据承载）。"""
        fr = self._freeze_partial()
        self.assertEqual(self.s.query(Approval).count(), 0,
                         "fixture 前提：无任何 DB 开放项/批准行")
        m = fx.manifest_of(self.store, RESEARCH_600089_KEY,
                           root=fr.candidate_id,
                           objects={fr.candidate_id: {"kind": "candidate",
                                                      "refs": []}})
        with self.assertRaises(ValueError) as cm:
            create_approval(self.store, self.s, m, "U-fixture",
                            RESEARCH_600089_KEY,
                            approved_at="2026-08-11T07:00:00Z",
                            acknowledged=True)
        self.assertIn("E-G6A-06-031", str(cm.exception))
        self.assertEqual(self.s.query(Approval).count(), 0,
                         "拒绝后不得新增 Approval 行")

    def test_release_eligible_rejects_partial(self):
        fr = self._freeze_partial()
        m = fx.manifest_of(self.store, RESEARCH_600089_KEY,
                           root=fr.candidate_id,
                           objects={fr.candidate_id: {"kind": "candidate",
                                                      "refs": []}})
        appr = self._approval_for(m, fr.candidate_id)
        ok, why = is_release_eligible(self.s, self.store, appr, m,
                                      RESEARCH_600089_KEY,
                                      candidate_digest=fr.candidate_id)
        self.assertFalse(ok)
        self.assertIn("E-G6A-06-031", why)

    def test_publish_rejects_partial(self):
        fr = self._freeze_partial()
        m = fx.manifest_of(self.store, RESEARCH_600089_KEY,
                           root=fr.candidate_id,
                           objects={fr.candidate_id: {"kind": "candidate",
                                                      "refs": []}})
        appr = self._approval_for(m, fr.candidate_id)
        with self.assertRaises(ValueError) as cm:
            publish_release(self.store, self.s, m, RESEARCH_600089_KEY, appr,
                            candidate_digest=fr.candidate_id,
                            released_at="2026-08-11T07:01:00Z",
                            writer="L11_release")
        self.assertIn("E-G6A-06-031", str(cm.exception))

    def test_missing_metadata_fails_closed(self):
        """最终候选形状缺 quality_status/release_eligible → 失败关闭。"""
        bad = fx.freeze_object(
            self.store, "candidate",
            {"schema_version": "1.1.0", "kind": "candidate",
             "product_hashes": {}})
        why = final_candidate_release_gate(self.store, bad)
        self.assertIn("E-G6A-06-030", why)
        m = fx.manifest_of(self.store, RESEARCH_600089_KEY, root=bad,
                           objects={bad: {"kind": "candidate", "refs": []}})
        with self.assertRaises(ValueError) as cm:
            create_approval(self.store, self.s, m, "U-fixture",
                            RESEARCH_600089_KEY,
                            approved_at="2026-08-11T07:00:00Z",
                            acknowledged=True)
        self.assertIn("E-G6A-06-030", str(cm.exception))

    def test_legacy_candidate_skips_gate(self):
        """legacy G4 candidate（kind=candidate + payload，无 product_hashes）
        不适用最终候选资格门 —— 保持既有行为。"""
        legacy = fx.build_candidate(self.store, {"ticker": "LEGACY"})
        self.assertIsNone(final_candidate_release_gate(self.store, legacy))

    def test_stripped_candidate_cannot_downgrade_to_legacy_e2e(self):
        """端到端变异：冻结 PARTIAL → 删光全部强最终标记 + 降级版本、保留其余
        最终字段 → 重建**合法 G4 闭包**，批准/准出/发布三层仍全拒，且
        Approval/Release/CurrentPointer 计数保持为零；精确 legacy fixture 仍
        通过本门（防过度修复）。"""
        fr = self._freeze_partial()
        stripped = dict(fr.candidate)
        for key in ("product_hashes", "request_hash", "source_commit",
                    "source_tree", "frozen_inputs_hash", "quality_status",
                    "release_eligible"):
            stripped.pop(key)
        stripped["schema_version"] = "1.0.0"
        self.assertNotIn("payload", stripped,
                         "前提：剥离后仍保留最终字段、无 legacy payload")
        stripped_id = self.store.store("candidate", canonical_bytes(stripped))

        # 合法 G4 闭包（subject root = 剥离候选；闭包完整、G4 审计可过）。
        src = fx.sse_source()
        ev = fx.build_evidence(self.store, src)
        macro = fx.build_macro(self.store)
        ass = fx.build_assumption(self.store)
        calc = fx.build_calc(self.store, [ev, macro, ass])
        claim = fx.build_claim(self.store, "fixture 结论（合成）", [ev, calc],
                               materiality="CRITICAL")
        ws = fx.freeze_object(self.store, "worksheet",
                              {"schema_version": "1.0.0",
                               "kind": "worksheet", "rows": "[]"})
        t = fx.freeze_object(self.store, "test",
                             {"schema_version": "1.0.0", "kind": "test",
                              "result": "PASS"})
        cc = fx.freeze_object(self.store, "code_config",
                              {"schema_version": "1.0.0", "kind": "code_config",
                               "code_version": "v1.0",
                               "config_version": "v1.0"})
        oi = fx.build_open_item(self.store, status="CLOSED")
        # build_macro 的 source_domain 恒为 NBS_DOMAIN（D-12 适用）—— 报告须
        # 带署名，否则 G4 审计 rights 门失败（fixture 一致性）。
        report = fx.build_report(self.store, with_nbs_attribution=True)
        objects = {
            stripped_id: {"kind": "candidate",
                          "refs": [claim, report, ws, t, cc, oi]},
            claim: {"kind": "claim", "refs": [ev, calc, macro, ass]},
            ev: {"kind": "evidence", "refs": []},
            macro: {"kind": "macro", "refs": []},
            ass: {"kind": "assumption", "refs": []},
            calc: {"kind": "calc", "refs": [ev, macro, ass]},
            ws: {"kind": "worksheet", "refs": []},
            t: {"kind": "test", "refs": []},
            cc: {"kind": "code_config", "refs": []},
            oi: {"kind": "open_item", "refs": []},
            report: {"kind": "report", "refs": []},
        }
        m = fx.manifest_of(self.store, RESEARCH_600089_KEY, root=stripped_id,
                           objects=objects)
        self.assertTrue(compute_closure(self.store, m).complete,
                        "前提：G4 闭包完整（剥离候选在闭包内）")
        self.assertTrue(audit_candidate(self.store, m, stripped_id)
                        .release_eligible,
                        "前提：G4 审计可过 —— 本门是唯一拒绝点")

        # ① 发布资格门拒绝剥离候选（strict-final，E-G6A-06-030）。
        why = final_candidate_release_gate(self.store, stripped_id)
        self.assertIn("E-G6A-06-030", why)
        # ② 批准拒绝且不留 Approval 行。
        with self.assertRaises(ValueError) as cm:
            create_approval(self.store, self.s, m, "U-fixture",
                            RESEARCH_600089_KEY,
                            approved_at="2026-08-11T07:00:00Z",
                            acknowledged=True)
        self.assertIn("E-G6A-06-030", str(cm.exception))
        self.assertEqual(self.s.query(Approval).count(), 0)
        # ③ 准出拒绝（直接构造批准也过不了谓词）。
        appr = self._approval_for(m, stripped_id)
        ok, why2 = is_release_eligible(self.s, self.store, appr, m,
                                       RESEARCH_600089_KEY,
                                       candidate_digest=stripped_id)
        self.assertFalse(ok)
        self.assertIn("E-G6A-06-030", why2)
        # ④ 发布拒绝且 Release/CurrentPointer 保持为零。
        with self.assertRaises(ValueError) as cm3:
            publish_release(self.store, self.s, m, RESEARCH_600089_KEY, appr,
                            candidate_digest=stripped_id,
                            released_at="2026-08-11T07:01:00Z",
                            writer="L11_release")
        self.assertIn("E-G6A-06-030", str(cm3.exception))
        self.assertEqual(self.s.query(Release).count(), 0)
        self.assertEqual(self.s.query(CurrentPointer).count(), 0)
        # ⑤ 防过度修复：精确 legacy fixture 仍通过本门。
        legacy = fx.build_candidate(self.store, {"ticker": "LEGACY"})
        self.assertIsNone(final_candidate_release_gate(self.store, legacy))

    def test_stripped_candidate_kind_removed_still_rejected_e2e(self):
        """端到端回归：剥离候选正文再**删掉 kind**（只留通用残留），清单元数据
        仍声明 kind=candidate → 批准/准出/发布仍全拒且零 DB 行 —— 候选身份由
        清单/调用点声明派生（expected_candidate），不因正文缺 kind 而跳过资格门。"""
        fr = self._freeze_partial()
        stripped = dict(fr.candidate)
        for key in ("product_hashes", "request_hash", "source_commit",
                    "source_tree", "frozen_inputs_hash", "quality_status",
                    "release_eligible"):
            stripped.pop(key)
        stripped.pop("kind", None)
        stripped["schema_version"] = "1.0.0"
        self.assertNotIn("kind", stripped, "前提：kind 已删")
        self.assertNotIn("payload", stripped,
                         "前提：剥离后仍保留最终字段、无 legacy payload")
        stripped_id = self.store.store("candidate", canonical_bytes(stripped))

        # 合法 G4 闭包（subject root = 剥离候选；闭包完整、G4 审计可过）。
        src = fx.sse_source()
        ev = fx.build_evidence(self.store, src)
        macro = fx.build_macro(self.store)
        ass = fx.build_assumption(self.store)
        calc = fx.build_calc(self.store, [ev, macro, ass])
        claim = fx.build_claim(self.store, "fixture 结论（合成）", [ev, calc],
                               materiality="CRITICAL")
        ws = fx.freeze_object(self.store, "worksheet",
                              {"schema_version": "1.0.0",
                               "kind": "worksheet", "rows": "[]"})
        t = fx.freeze_object(self.store, "test",
                             {"schema_version": "1.0.0", "kind": "test",
                              "result": "PASS"})
        cc = fx.freeze_object(self.store, "code_config",
                              {"schema_version": "1.0.0", "kind": "code_config",
                               "code_version": "v1.0",
                               "config_version": "v1.0"})
        oi = fx.build_open_item(self.store, status="CLOSED")
        # build_macro 的 source_domain 恒为 NBS_DOMAIN（D-12 适用）—— 报告须
        # 带署名，否则 G4 审计 rights 门失败（fixture 一致性）。
        report = fx.build_report(self.store, with_nbs_attribution=True)
        objects = {
            stripped_id: {"kind": "candidate",
                          "refs": [claim, report, ws, t, cc, oi]},
            claim: {"kind": "claim", "refs": [ev, calc, macro, ass]},
            ev: {"kind": "evidence", "refs": []},
            macro: {"kind": "macro", "refs": []},
            ass: {"kind": "assumption", "refs": []},
            calc: {"kind": "calc", "refs": [ev, macro, ass]},
            ws: {"kind": "worksheet", "refs": []},
            t: {"kind": "test", "refs": []},
            cc: {"kind": "code_config", "refs": []},
            oi: {"kind": "open_item", "refs": []},
            report: {"kind": "report", "refs": []},
        }
        m = fx.manifest_of(self.store, RESEARCH_600089_KEY, root=stripped_id,
                           objects=objects)
        self.assertTrue(compute_closure(self.store, m).complete,
                        "前提：G4 闭包完整（正文无最终依赖标记，不触发 refs 门）")
        self.assertTrue(audit_candidate(self.store, m, stripped_id)
                        .release_eligible,
                        "前提：G4 审计可过 —— 本门是唯一拒绝点")

        # ① 门直接调用：声明候选（expected_candidate=True）→ strict-final 拒绝；
        # 默认未声明（非候选形状）则跳过 —— 差异正是调用点派生的作用面。
        self.assertIn("E-G6A-06-030",
                      final_candidate_release_gate(self.store, stripped_id,
                                                   expected_candidate=True))
        # ② 批准拒绝且零 Approval 行（subject root 元数据声明候选 → 派生拒绝）。
        with self.assertRaises(ValueError) as cm:
            create_approval(self.store, self.s, m, "U-fixture",
                            RESEARCH_600089_KEY,
                            approved_at="2026-08-11T07:00:00Z",
                            acknowledged=True)
        self.assertIn("E-G6A-06-030", str(cm.exception))
        self.assertEqual(self.s.query(Approval).count(), 0)
        # ③ 准出拒绝（直接构造批准也过不了谓词）。
        appr = self._approval_for(m, stripped_id)
        ok, why2 = is_release_eligible(self.s, self.store, appr, m,
                                       RESEARCH_600089_KEY,
                                       candidate_digest=stripped_id)
        self.assertFalse(ok)
        self.assertIn("E-G6A-06-030", why2)
        # ④ 发布拒绝且 Release/CurrentPointer 保持为零。
        with self.assertRaises(ValueError) as cm3:
            publish_release(self.store, self.s, m, RESEARCH_600089_KEY, appr,
                            candidate_digest=stripped_id,
                            released_at="2026-08-11T07:01:00Z",
                            writer="L11_release")
        self.assertIn("E-G6A-06-030", str(cm3.exception))
        self.assertEqual(self.s.query(Release).count(), 0)
        self.assertEqual(self.s.query(CurrentPointer).count(), 0)
        # ⑤ 防过度修复：精确 legacy fixture 在声明候选下仍通过本门。
        legacy = fx.build_candidate(self.store, {"ticker": "LEGACY"})
        self.assertIsNone(final_candidate_release_gate(self.store, legacy,
                                                       expected_candidate=True))


class TestPartialSchemaContract(unittest.TestCase):
    """D/E：canonical schema 用 definitions/oneOf 表达状态条件 —— 仅 schema
    校验即可拒绝每个非法状态/字段组合，不依赖 app 代码；final_candidate 的
    products/product_hashes 形状收紧与 FULL⇔true / PARTIAL⇔false 关系也由
    契约强制。"""

    def _req(self, rd, label=None):
        with self.assertRaises(SchemaError) as cm:
            validate_object("final_candidate_request",
                            _request_payload(routes_dict=rd))
        self.assertIn("E-SCHEMA", str(cm.exception), label or "")

    def _route(self, **over):
        rd = _routes_dict()
        rd.update(over)
        return rd

    def test_schema_rejects_ready_with_reason(self):
        self._req(self._route(fcff={"state": ROUTE_READY, "reason": "r"}))

    def test_schema_rejects_ready_with_evidence_refs(self):
        self._req(self._route(fcff={"state": ROUTE_READY,
                                    "evidence_refs": ["EV-1"]}))

    def test_schema_rejects_ready_with_missing_inputs(self):
        self._req(self._route(fcff={"state": ROUTE_READY,
                                    "missing_inputs": ["x"]}))

    def test_schema_rejects_input_missing_without_missing_inputs(self):
        self._req(self._route(fcfe={"state": ROUTE_INPUT_MISSING,
                                    "reason": "r",
                                    "evidence_refs": ["EV-1"]}))

    def test_schema_rejects_input_missing_empty_missing_inputs(self):
        self._req(self._route(fcfe={"state": ROUTE_INPUT_MISSING,
                                    "reason": "r",
                                    "evidence_refs": ["EV-1"],
                                    "missing_inputs": []}))

    def test_schema_rejects_not_evaluated_with_missing_inputs(self):
        self._req(self._route(relative={"state": ROUTE_NOT_EVALUATED,
                                        "reason": "r",
                                        "evidence_refs": ["EV-1"],
                                        "missing_inputs": ["x"]}))

    def test_schema_rejects_not_evaluated_without_reason(self):
        self._req(self._route(relative={"state": ROUTE_NOT_EVALUATED,
                                        "evidence_refs": ["EV-1"]}))

    def test_schema_rejects_empty_evidence_refs(self):
        self._req(self._route(pe_roe_pb={"state": ROUTE_NOT_EVALUATED,
                                         "reason": "r",
                                         "evidence_refs": []}))

    def test_schema_rejects_blank_evidence_ref_item(self):
        self._req(self._route(pe_roe_pb={"state": ROUTE_NOT_EVALUATED,
                                         "reason": "r",
                                         "evidence_refs": ["  "]}))

    def test_schema_rejects_unknown_state(self):
        self._req(self._route(fcff={"state": "PARTIAL_CALC"}))

    def test_schema_rejects_unknown_route(self):
        rd = self._route(sotp={"state": ROUTE_READY})
        self._req(rd)

    def test_schema_rejects_missing_route(self):
        rd = _routes_dict()
        del rd["relative"]
        self._req(rd)

    def test_schema_accepts_valid_state_combinations(self):
        rd = self._route(
            fcfe={"state": ROUTE_INPUT_MISSING, "reason": "r",
                  "evidence_refs": ["EV-1"], "missing_inputs": ["x"]},
            relative={"state": ROUTE_NOT_EVALUATED, "reason": "r",
                      "evidence_refs": ["EV-2"]})
        validate_object("final_candidate_request",
                        _request_payload(routes_dict=rd))

    def _full_cand(self) -> dict:
        with tempfile.TemporaryDirectory() as tmp:
            store = ArtifactStore(os.path.join(tmp, "lib"))
            ctx = _ctx(routes=_routes(), policy=_policy(tolerance="2"))
            return CandidateFreezeService(store).freeze_final_candidate(
                ctx, "schema-run", _REV_A, _TREE_A, recompute_all(ctx),
                request_payload=_payload_from_ctx(ctx, "schema-run")).candidate

    def _cand_reject(self, cand, label=None):
        with self.assertRaises(SchemaError) as cm:
            validate_object("final_candidate", cand)
        self.assertIn("E-SCHEMA", str(cm.exception), label or "")

    def test_schema_rejects_final_products_duplicate(self):
        cand = self._full_cand()
        cand["products"] = cand["products"] + ["calc_ledger"]
        self._cand_reject(cand)

    def test_schema_rejects_final_products_unknown_name(self):
        cand = self._full_cand()
        cand["products"] = ["phantom" if p == "calc_ledger" else p
                            for p in cand["products"]]
        self._cand_reject(cand)

    def test_schema_rejects_final_products_short(self):
        cand = self._full_cand()
        cand["products"] = cand["products"][:5]
        self._cand_reject(cand)

    def test_schema_rejects_product_hashes_wrong_key(self):
        cand = self._full_cand()
        hashes = dict(cand["product_hashes"])
        del hashes["claim_map"]
        hashes["phantom"] = "0" * 64
        cand["product_hashes"] = hashes
        self._cand_reject(cand)

    def test_schema_rejects_product_hash_non_sha(self):
        cand = self._full_cand()
        hashes = dict(cand["product_hashes"])
        hashes["calc_ledger"] = "not-sha"
        cand["product_hashes"] = hashes
        self._cand_reject(cand)

    def test_schema_rejects_full_with_false(self):
        cand = self._full_cand()
        cand["quality_status"] = "FULL"
        cand["release_eligible"] = False
        self._cand_reject(cand, "FULL 必须 ⇔ release_eligible=true")

    def test_schema_rejects_partial_with_true(self):
        cand = self._full_cand()
        cand["quality_status"] = "PARTIAL"
        cand["release_eligible"] = True
        self._cand_reject(cand, "PARTIAL 必须 ⇔ release_eligible=false")

    def test_schema_accepts_full_true(self):
        cand = self._full_cand()
        cand["quality_status"] = "FULL"
        cand["release_eligible"] = True
        validate_object("final_candidate", cand)

    def test_schema_rejects_null_quality_status(self):
        """quality_status=null 不得绕过 const/enum —— 通用 const 检查无
        `node.get(k) is not None` 空值旁路。"""
        cand = self._full_cand()
        cand["quality_status"] = None
        self._cand_reject(cand, "quality_status null 必须拒")

    def test_schema_rejects_null_release_eligible(self):
        cand = self._full_cand()
        cand["release_eligible"] = None
        self._cand_reject(cand, "release_eligible null 必须拒")


class TestPartialDirectBoundary(unittest.TestCase):
    """C：direct ResearchContext/recompute 边界同样失败关闭 E-G6A-06-020，
    不泄漏 KeyError、不静默忽略矛盾。"""

    def _reject_entry(self, rd, label=None):
        with self.assertRaises(CandidateRequestError) as cm:
            with tempfile.TemporaryDirectory() as tmp:
                store = ArtifactStore(os.path.join(tmp, "objects"))
                freeze_final_candidate_from_payload(
                    store, _request_payload(routes_dict=rd),
                    source_commit=_REV_A, source_tree=_TREE_A)
        self.assertIn("E-G6A-06-020", str(cm.exception), label or "")

    def test_request_input_missing_without_missing_inputs_rejected(self):
        self._reject_entry(_routes_dict(
            fcfe={"state": ROUTE_INPUT_MISSING, "reason": "r",
                  "evidence_refs": ["EV-1"]}))

    def test_request_not_evaluated_with_missing_inputs_rejected(self):
        self._reject_entry(_routes_dict(
            relative={"state": ROUTE_NOT_EVALUATED, "reason": "r",
                      "evidence_refs": ["EV-1"], "missing_inputs": ["x"]}))

    def test_direct_input_missing_without_missing_inputs_rejected(self):
        with self.assertRaises(RecomputeError) as cm:
            RouteDeclaration(ROUTE_INPUT_MISSING, "r", ("EV-1",))
        self.assertIn("E-G6A-06-020", str(cm.exception))

    def test_direct_not_evaluated_with_missing_inputs_rejected(self):
        with self.assertRaises(RecomputeError) as cm:
            RouteDeclaration(ROUTE_NOT_EVALUATED, "r", ("EV-1",), ("x",))
        self.assertIn("E-G6A-06-020", str(cm.exception))

    def test_direct_ready_with_reason_rejected(self):
        with self.assertRaises(RecomputeError) as cm:
            RouteDeclaration(ROUTE_READY, "r", ("EV-1",))
        self.assertIn("E-G6A-06-020", str(cm.exception))

    def test_direct_unknown_state_rejected(self):
        with self.assertRaises(RecomputeError) as cm:
            RouteDeclaration("BOGUS")
        self.assertIn("E-G6A-06-020", str(cm.exception))

    def test_direct_ready_route_missing_fact_rejected(self):
        ctx = _ctx(routes=_routes(),
                   facts={"fcff": "400000000", "fcfe": "300000000",
                          "eps": "0.60"})
        with self.assertRaises(RecomputeError) as cm:
            recompute_all(ctx)
        self.assertIn("E-G6A-06-020", str(cm.exception))

    def test_direct_non_ready_route_carrying_fact_rejected(self):
        routes = _routes(fcfe=_decl("fcfe", ROUTE_INPUT_MISSING, "缺输入",
                                    ("EV-9",), ("net_debt",)))
        ctx = _ctx(routes=routes,
                   facts={"fcff": "400000000", "fcfe": "300000000",
                          "eps": "0.60", "book_per_share": "5.00"})
        with self.assertRaises(RecomputeError) as cm:
            recompute_all(ctx)
        self.assertIn("E-G6A-06-020", str(cm.exception))

    def test_direct_route_key_set_drift_rejected(self):
        base = {r: RouteDeclaration(ROUTE_READY) for r in VALUATION_ROUTES}
        del base["fcfe"]
        with self.assertRaises(RecomputeError) as cm:
            recompute_all(_ctx(routes=ValuationRoutes(base)))
        self.assertIn("E-G6A-06-020", str(cm.exception))


class TestMissingInputsPropagation(unittest.TestCase):
    """C/E：missing_inputs 是冻结输入且不得在产物/开放项中被丢弃。"""

    def test_missing_inputs_propagate_into_products_and_open_items(self):
        ctx = _ctx(routes=_routes(
            fcfe=_decl("fcfe", ROUTE_INPUT_MISSING, "缺少净债务",
                       ("EV-9",), ("net_debt", "sweep"))))
        res = recompute_all(ctx)
        v = res.products["valuation_fcfe"]
        self.assertEqual(v["status"], ROUTE_INPUT_MISSING)
        self.assertEqual(v["missing_inputs"], ["net_debt", "sweep"])
        for name in ("scenario_pessimistic", "scenario_base",
                     "scenario_optimistic"):
            s = res.products[name]
            self.assertEqual(s["status"], ROUTE_INPUT_MISSING)
            self.assertEqual(s["missing_inputs"], ["net_debt", "sweep"],
                             f"{name} 必须传播 missing_inputs")
        oi = res.products["open_items"]
        item = next(it for it in oi["open_items"]
                    if it["open_item_id"] == "OI-G6A06-RC-FCFE-INPUT_MISSING")
        self.assertIn("net_debt", item["description"])
        self.assertIn("sweep", item["description"])

    def test_not_evaluated_products_carry_no_missing_inputs(self):
        ctx = _ctx(routes=_routes(
            fcfe=_decl("fcfe", ROUTE_NOT_EVALUATED, "无 FCFE 证据",
                       ("EV-3",))))
        res = recompute_all(ctx)
        self.assertEqual(res.products["valuation_fcfe"]["status"],
                         ROUTE_NOT_EVALUATED)
        self.assertNotIn("missing_inputs", res.products["valuation_fcfe"])
        for name in ("scenario_pessimistic", "scenario_base",
                     "scenario_optimistic"):
            self.assertEqual(res.products[name]["status"],
                             ROUTE_NOT_EVALUATED)
            self.assertNotIn("missing_inputs", res.products[name])


class TestPartialReleaseGateAdversarial(unittest.TestCase):
    """E：发布资格门对自证 FULL/true 及各类畸形最终候选一律 E-G6A-06-030
    拒绝（零 Approval/Release/Current 写入）；真 FULL 不误伤（防过度修复）。"""

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self.store = ArtifactStore(os.path.join(self._tmp, "lib"))
        self.repo = create_repository(os.path.join(self._tmp, "pub.sqlite3"))
        self.repo.create_all()
        self.s = self.repo.session()

    def tearDown(self):
        self.s.close()
        self.repo.engine.dispose()
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _freeze_full(self):
        service = CandidateFreezeService(self.store)
        ctx = _ctx(routes=_routes(), policy=_policy(tolerance="2"))
        return service.freeze_final_candidate(ctx, "full-run", _REV_A,
                                              _TREE_A, recompute_all(ctx),
                                              request_payload=_payload_from_ctx(
                                                  ctx, "full-run"))

    def _freeze_partial(self):
        service = CandidateFreezeService(self.store)
        ctx = _ctx(routes=_routes(fcfe=_decl(
            "fcfe", ROUTE_INPUT_MISSING, "缺输入", ("EV-9",), ("net_debt",))))
        return service.freeze_final_candidate(ctx, "partial-run", _REV_A,
                                              _TREE_A, recompute_all(ctx),
                                              request_payload=_payload_from_ctx(
                                                  ctx))

    def _forge(self, fr, *, cand_mut=None, product_bodies=None,
               hash_override=None, drop_products=()):
        """内容寻址合法但语义被篡改的最终候选 id（正文替换/哈希改写/元数据
        篡改后重新 store，candidate 自身内容哈希仍自洽）。"""
        c = dict(fr.candidate)
        if cand_mut:
            c = cand_mut(c)
        if product_bodies:
            hashes = dict(c["product_hashes"])
            for name, body in product_bodies.items():
                hashes[name] = self.store.store(
                    "recompute_product", canonical_bytes(body))
            c["product_hashes"] = hashes
        if hash_override:
            hashes = dict(c["product_hashes"])
            hashes.update(hash_override)
            c["product_hashes"] = hashes
        if drop_products:
            c["product_hashes"] = {k: v for k, v in c["product_hashes"].items()
                                   if k not in drop_products}
            c["products"] = [p for p in c["products"] if p not in drop_products]
        return self.store.store("candidate", canonical_bytes(c))

    def _approval_for(self, manifest, cand):
        from publish_engine import inputs_hash
        appr = Approval(
            id="APR_FORGE", schema_version="1.0.0",
            object_ref=manifest["subject_root"], approver="U",
            approved_at=__import__("datetime").datetime(2026, 8, 11, 7, 0, 0),
            subject_root_hash="0" * 64, workflow=manifest["workflow"],
            scope_id=manifest["scope_id"],
            current_key=manifest["current_key"],
            inputs_hash=inputs_hash(manifest, cand),
            status="ACTIVE", token="APPROVE", version=1)
        self.s.add(appr)
        self.s.commit()
        return appr

    def _manifest(self, root):
        return fx.manifest_of(self.store, RESEARCH_600089_KEY, root=root,
                              objects={root: {"kind": "candidate",
                                              "refs": []}})

    def test_genuine_full_bundle_passes_gate(self):
        """防过度修复：真 FULL 最终候选 bundle 必须通过发布资格门。"""
        fr = self._freeze_full()
        self.assertEqual(fr.candidate["quality_status"], "FULL")
        self.assertIsNone(final_candidate_release_gate(self.store,
                                                       fr.candidate_id))

    def _complete_manifest(self, fr):
        """真 FULL 最终候选的完整闭包清单：candidate refs = request_hash +
        11 项产品哈希，request 挂 report（供审计），全部依赖已登记。"""
        service = CandidateFreezeService(self.store)
        b = service.load_candidate_bundle(fr.candidate_id, **_EXPECTED)
        report = fx.build_report(self.store, with_nbs_attribution=False)
        objects = _complete_manifest_objects(self.store, b, report)
        return fx.manifest_of(self.store, RESEARCH_600089_KEY,
                              root=fr.candidate_id, objects=objects)

    def test_genuine_full_bundle_approval_allowed(self):
        """防过度修复：真 FULL 最终候选 + 完整闭包（candidate refs + 12 个已
        登记依赖）可正常批准；闭包完整、审计通过。"""
        fr = self._freeze_full()
        m = self._complete_manifest(fr)
        c = compute_closure(self.store, m)
        self.assertTrue(c.complete, f"闭包须完整（refs_violation="
                                    f"{c.refs_violation}）")
        audit = audit_candidate(self.store, m, fr.candidate_id)
        self.assertTrue(audit.release_eligible)
        before = self.s.query(Approval).count()
        create_approval(self.store, self.s, m, "U-fixture",
                        RESEARCH_600089_KEY,
                        approved_at="2026-08-11T07:00:00Z",
                        acknowledged=True)
        self.assertEqual(self.s.query(Approval).count(), before + 1,
                         "真 FULL 最终候选可正常批准")

    def test_forged_full_true_root_over_partial_products_rejected_all_layers(
            self):
        """伪造根自证 FULL/true、正文却实为 PARTIAL → 批准/准出/发布全拒，
        零 Approval/Release/Current 写入。"""
        fr = self._freeze_partial()
        forged = self._forge(
            fr, cand_mut=lambda c: c.update(quality_status="FULL",
                                            release_eligible=True) or c)
        why = final_candidate_release_gate(self.store, forged)
        self.assertIn("E-G6A-06-030", why)
        m = self._manifest(forged)
        before_a = self.s.query(Approval).count()
        with self.assertRaises(ValueError) as cm:
            create_approval(self.store, self.s, m, "U-fixture",
                            RESEARCH_600089_KEY,
                            approved_at="2026-08-11T07:00:00Z",
                            acknowledged=True)
        self.assertIn("E-G6A-06-030", str(cm.exception))
        self.assertEqual(self.s.query(Approval).count(), before_a,
                         "伪造 FULL 根不得获得 Approval 行")
        appr = self._approval_for(m, forged)
        ok, why2 = is_release_eligible(self.s, self.store, appr, m,
                                       RESEARCH_600089_KEY,
                                       candidate_digest=forged)
        self.assertFalse(ok)
        self.assertIn("E-G6A-06-030", why2)
        with self.assertRaises(ValueError) as cm2:
            publish_release(self.store, self.s, m, RESEARCH_600089_KEY, appr,
                            candidate_digest=forged,
                            released_at="2026-08-11T07:01:00Z",
                            writer="L11_release")
        self.assertIn("E-G6A-06-030", str(cm2.exception))
        self.assertEqual(self.s.query(Release).count(), 0)
        self.assertEqual(self.s.query(CurrentPointer).count(), 0)

    def test_candidate_missing_source_revision_rejected(self):
        fr = self._freeze_partial()

        def _mut(c):
            c.pop("source_commit")
            c.pop("source_tree")
            return c

        forged = self._forge(fr, cand_mut=_mut)
        self.assertIn("E-G6A-06-030",
                      final_candidate_release_gate(self.store, forged))

    def test_internal_freeze_artifact_rejected_as_malformed_final_candidate(
            self):
        """G6A-05 内部 freeze_candidate_from_recompute 产物含 product_hashes
        但无 source revision → 发布门按畸形最终候选拒绝（不得冒充可发布）。"""
        ctx = _ctx(routes=_routes(), policy=_policy(tolerance="2"))
        fr = freeze_candidate_from_recompute(self.store, ctx, "g6a05-run",
                                             recompute_all(ctx))
        self.assertIn("product_hashes", fr.candidate)
        self.assertNotIn("source_commit", fr.candidate)
        self.assertIn("E-G6A-06-030",
                      final_candidate_release_gate(self.store, fr.candidate_id))

    def test_unknown_product_status_rejected(self):
        fr = self._freeze_partial()
        forged = self._forge(
            fr, product_bodies={"valuation_fcff": {"method": "FCFF",
                                                   "scenario": "BASE",
                                                   "status": "GARBAGE"}})
        self.assertIn("E-G6A-06-030",
                      final_candidate_release_gate(self.store, forged))

    def test_missing_product_status_rejected(self):
        fr = self._freeze_partial()
        forged = self._forge(
            fr, product_bodies={"valuation_fcff": {"method": "FCFF",
                                                   "scenario": "BASE"}})
        self.assertIn("E-G6A-06-030",
                      final_candidate_release_gate(self.store, forged))

    def test_route_status_mismatch_rejected(self):
        fr = self._freeze_partial()
        oi = copy.deepcopy(fr.recompute.products["open_items"])
        oi["route_statuses"]["fcff"] = ROUTE_INPUT_MISSING
        forged = self._forge(fr, product_bodies={"open_items": oi})
        self.assertIn("E-G6A-06-030",
                      final_candidate_release_gate(self.store, forged))

    def test_malformed_open_items_rejected(self):
        fr = self._freeze_partial()
        forged = self._forge(fr, product_bodies={"open_items":
                                                 {"cross_check": []}})
        self.assertIn("E-G6A-06-030",
                      final_candidate_release_gate(self.store, forged))

    def test_missing_product_rejected(self):
        fr = self._freeze_partial()
        forged = self._forge(fr, hash_override={"calc_ledger": "0" * 64})
        self.assertIn("E-G6A-06-030",
                      final_candidate_release_gate(self.store, forged))

    def test_bad_digest_body_rejected(self):
        """候选把估值产物哈希指向**另一产品的正文**（记录哈希与正文自洽但
        该名该体不成立）→ 严格质量派生拒绝。"""
        fr = self._freeze_partial()
        forged = self._forge(
            fr, hash_override={"valuation_fcff":
                               fr.candidate["product_hashes"]["open_items"]})
        self.assertIn("E-G6A-06-030",
                      final_candidate_release_gate(self.store, forged))

    def test_non_object_product_body_rejected(self):
        """记录哈希指向 JSON 数组正文（非对象）→ 正文形态校验失败关闭。"""
        fr = self._freeze_partial()
        bad_digest = self.store.store(
            "recompute_product", canonical_bytes(["not", "a", "product"]))
        forged = self._forge(fr, hash_override={"calc_ledger": bad_digest})
        self.assertIn("E-G6A-06-030",
                      final_candidate_release_gate(self.store, forged))

    def test_drop_products_key_cannot_downgrade_to_legacy(self):
        """畸形最终候选删掉 products 键仍含 product_hashes/quality 等最终专属
        字段 → 不得降级为 legacy 跳过本门。"""
        fr = self._freeze_partial()

        def _mut(c):
            c.pop("products")
            return c

        forged = self._forge(fr, cand_mut=_mut)
        self.assertIn("E-G6A-06-030",
                      final_candidate_release_gate(self.store, forged))

    def test_drop_product_hashes_key_cannot_downgrade_to_legacy(self):
        fr = self._freeze_partial()

        def _mut(c):
            c.pop("product_hashes")
            return c

        forged = self._forge(fr, cand_mut=_mut)
        self.assertIn("E-G6A-06-030",
                      final_candidate_release_gate(self.store, forged))


class TestProductSemanticExact(unittest.TestCase):
    """G6A-06 PARTIAL 返工：产物语义校验精确到 method/scenario/键集/值域，
    不再是字段存在即可。健康 FULL/PARTIAL 保持有效（防过度修复）。"""

    def _products_full(self):
        ctx = _ctx(routes=_routes(), policy=_policy(tolerance="2"))
        return copy.deepcopy(recompute_all(ctx).products)

    def _products_partial(self):
        ctx = _ctx(routes=_routes(fcfe=_decl(
            "fcfe", ROUTE_INPUT_MISSING, "缺输入", ("EV-9",), ("net_debt",))))
        return copy.deepcopy(recompute_all(ctx).products)

    def _reject(self, products, label=None):
        with self.assertRaises(QualityError) as cm:
            quality_from_products(products)
        self.assertIn("E-G6A-06-018", str(cm.exception), label or "")

    def test_healthy_full_and_partial_remain_valid(self):
        quality, eligible = quality_from_products(self._products_full())
        self.assertEqual((quality, eligible), ("FULL", True))
        quality, eligible = quality_from_products(self._products_partial())
        self.assertEqual((quality, eligible), ("PARTIAL", False))

    def test_wrong_method_rejected(self):
        p = self._products_full()
        p["valuation_fcff"]["method"] = "FCFE"
        self._reject(p, "FCFF 路由不得标成 FCFE")

    def test_wrong_scenario_rejected(self):
        p = self._products_full()
        p["scenario_pessimistic"]["scenario"] = "BASE"
        self._reject(p, "PESSIMISTIC 情景不得标成 BASE")

    def test_scenario_hash_swapped_with_valuation_body_rejected(self):
        p = self._products_full()
        p["scenario_pessimistic"] = copy.deepcopy(p["valuation_fcfe"])
        self._reject(p, "情景产品不得换用估值产品正文")

    def test_non_numeric_per_share_rejected(self):
        p = self._products_full()
        p["valuation_fcff"]["per_share_base"] = "not-a-number"
        self._reject(p, "非数字 per-share")

    def test_nan_per_share_rejected(self):
        p = self._products_full()
        p["valuation_fcff"]["per_share_base"] = float("nan")
        self._reject(p, "NaN per-share 不得推导出 FULL")

    def test_infinity_per_share_rejected(self):
        p = self._products_full()
        p["valuation_relative"]["per_share_high"] = float("inf")
        self._reject(p, "Infinity per-share 不得推导出 FULL")

    def test_per_share_out_of_order_rejected(self):
        p = self._products_full()
        p["valuation_fcff"]["per_share_low"] = "99.0"
        p["valuation_fcff"]["per_share_base"] = "5.0"
        self._reject(p, "per-share 须 low ≤ base ≤ high")

    def test_extra_field_rejected(self):
        p = self._products_full()
        p["valuation_fcfe"]["hacked"] = 1
        self._reject(p, "PASS 产物多余字段")

    def test_calc_claim_emission_cross_wire_rejected(self):
        p = self._products_full()
        p["claim_map"], p["emission_map"] = p["emission_map"], p["claim_map"]
        self._reject(p, "claim/emission 交叉换配")

    def test_calc_ledger_wrong_shape_rejected(self):
        p = self._products_full()
        p["calc_ledger"] = {"ledger": []}
        self._reject(p, "calc_ledger.ledger 非空数组")

    def test_claim_emission_value_cross_consistency_rejected(self):
        p = self._products_full()
        p["emission_map"]["emissions"][0]["rendered_value"] = "0.99"
        self._reject(p, "emission rendered_value 须与 claim value 一致")

    def test_open_item_material_string_rejected(self):
        p = self._products_partial()
        for it in p["open_items"]["open_items"]:
            if it["open_item_id"].startswith("OI-G6A06-RC-"):
                it["material"] = "true"
        self._reject(p, "material 字符串不得静默逃离 PARTIAL")

    def test_open_item_unknown_status_rejected(self):
        p = self._products_partial()
        p["open_items"]["open_items"][0]["status"] = "GARBAGE"
        self._reject(p, "未知 open item status")

    def test_open_item_duplicate_id_rejected(self):
        p = self._products_partial()
        dup = copy.deepcopy(p["open_items"]["open_items"][0])
        p["open_items"]["open_items"].append(dup)
        self._reject(p, "重复 open_item_id")

    def test_missing_required_route_item_rejected(self):
        p = self._products_partial()
        p["open_items"]["open_items"] = [
            it for it in p["open_items"]["open_items"]
            if not it["open_item_id"].startswith("OI-G6A06-RC-")]
        self._reject(p, "非 READY 路由缺确定性 material 路由项")

    def test_ready_route_extra_route_item_rejected(self):
        p = self._products_full()
        p["open_items"]["open_items"].append({
            "open_item_id": "OI-G6A06-RC-FCFF-READY",
            "description": "dummy", "material": True, "owner_role": "U",
            "due_date": "2026-08-31", "blocks_gate": "G3-06",
            "closure_evidence": None, "status": "OPEN"})
        self._reject(p, "READY 路由不得带路由状态项")


class TestRouteDeclarationImmutability(unittest.TestCase):
    """G6A-06 PARTIAL 返工：冻结 dataclass 内不得保留可变 list 别名；
    直接上下文拒绝重复 evidence_refs/missing_inputs（与受管 schema 同语义）。"""

    def test_list_inputs_canonicalized_to_tuple(self):
        refs = ["EV-1", "EV-2"]
        missing = ["net_debt"]
        decl = RouteDeclaration(ROUTE_INPUT_MISSING, "r", refs, missing)
        self.assertIsInstance(decl.evidence_refs, tuple)
        self.assertIsInstance(decl.missing_inputs, tuple)

    def test_caller_list_mutation_does_not_drift_declaration(self):
        refs = ["EV-1", "EV-2"]
        missing = ["net_debt"]
        decl = RouteDeclaration(ROUTE_INPUT_MISSING, "r", refs, missing)
        refs.append("EV-3")
        missing.append("sweep")
        self.assertEqual(decl.evidence_refs, ("EV-1", "EV-2"),
                         "调用方改 list 不得反向漂移声明")
        self.assertEqual(decl.missing_inputs, ("net_debt",))

    def test_clear_list_after_build_keeps_declaration(self):
        refs = ["EV-1"]
        decl = RouteDeclaration(ROUTE_NOT_EVALUATED, "r", refs)
        refs.clear()
        self.assertEqual(decl.evidence_refs, ("EV-1",))

    def test_duplicate_evidence_refs_rejected(self):
        with self.assertRaises(RecomputeError) as cm:
            RouteDeclaration(ROUTE_NOT_EVALUATED, "r", ("EV-1", "EV-1"))
        self.assertIn("E-G6A-06-020", str(cm.exception))

    def test_duplicate_missing_inputs_rejected(self):
        with self.assertRaises(RecomputeError) as cm:
            RouteDeclaration(ROUTE_INPUT_MISSING, "r", ("EV-1",), ("x", "x"))
        self.assertIn("E-G6A-06-020", str(cm.exception))

    def test_duplicate_in_list_form_rejected(self):
        with self.assertRaises(RecomputeError) as cm:
            RouteDeclaration(ROUTE_NOT_EVALUATED, "r", ["EV-1", "EV-1"])
        self.assertIn("E-G6A-06-020", str(cm.exception))


class TestRequestBindingGate(unittest.TestCase):
    """G6A-06 request 绑定：候选必须锚定产生它的受管请求；任何自洽伪造 /
    请求体/请求身份/请求字段篡改都在发布门 E-G6A-06-030 全层拒绝，零写入；
    真 FULL 受管 bundle 通过并暴露 request_hash。"""

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self.store = ArtifactStore(os.path.join(self._tmp, "lib"))
        self.repo = create_repository(os.path.join(self._tmp, "pub.sqlite3"))
        self.repo.create_all()
        self.s = self.repo.session()

    def tearDown(self):
        self.s.close()
        self.repo.engine.dispose()
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _freeze_full(self):
        service = CandidateFreezeService(self.store)
        ctx = _ctx(routes=_routes(), policy=_policy(tolerance="2"))
        return service.freeze_final_candidate(ctx, "full-run", _REV_A,
                                              _TREE_A, recompute_all(ctx),
                                              request_payload=_payload_from_ctx(
                                                  ctx, "full-run"))

    def _freeze_partial(self):
        service = CandidateFreezeService(self.store)
        ctx = _ctx(routes=_routes(fcfe=_decl(
            "fcfe", ROUTE_INPUT_MISSING, "缺输入", ("EV-9",), ("net_debt",))))
        return service.freeze_final_candidate(ctx, "partial-run", _REV_A,
                                              _TREE_A, recompute_all(ctx),
                                              request_payload=_payload_from_ctx(
                                                  ctx))

    def _forge(self, fr, *, cand_mut=None, product_bodies=None,
               hash_override=None, drop_products=()):
        """内容寻址合法但语义被篡改的最终候选 id（正文替换/哈希改写/元数据
        篡改后重新 store，candidate 自身内容哈希仍自洽）。"""
        c = dict(fr.candidate)
        if cand_mut:
            c = cand_mut(c)
        if product_bodies:
            hashes = dict(c["product_hashes"])
            for name, body in product_bodies.items():
                hashes[name] = self.store.store(
                    "recompute_product", canonical_bytes(body))
            c["product_hashes"] = hashes
        if hash_override:
            hashes = dict(c["product_hashes"])
            hashes.update(hash_override)
            c["product_hashes"] = hashes
        if drop_products:
            c["product_hashes"] = {k: v for k, v in c["product_hashes"].items()
                                   if k not in drop_products}
            c["products"] = [p for p in c["products"] if p not in drop_products]
        return self.store.store("candidate", canonical_bytes(c))

    def _approval_for(self, manifest, cand):
        from publish_engine import inputs_hash
        appr = Approval(
            id="APR_BIND", schema_version="1.0.0",
            object_ref=manifest["subject_root"], approver="U",
            approved_at=__import__("datetime").datetime(2026, 8, 11, 7, 0, 0),
            subject_root_hash="0" * 64, workflow=manifest["workflow"],
            scope_id=manifest["scope_id"],
            current_key=manifest["current_key"],
            inputs_hash=inputs_hash(manifest, cand),
            status="ACTIVE", token="APPROVE", version=1)
        self.s.add(appr)
        self.s.commit()
        return appr

    def _manifest(self, root):
        return fx.manifest_of(self.store, RESEARCH_600089_KEY, root=root,
                              objects={root: {"kind": "candidate",
                                              "refs": []}})

    def _forge_products(self, fr, products):
        """整组替换候选产品（正文重新落库 + 哈希重算 + 根元数据自洽 FULL），
        返回重新 store 的 candidate id —— 内容寻址自洽但不来自绑定请求。"""
        c = dict(fr.candidate)
        hashes = {}
        for name, body in products.items():
            hashes[name] = self.store.store(
                "recompute_product", canonical_bytes(body))
        c["product_hashes"] = hashes
        c["products"] = sorted(products)
        c["quality_status"] = "FULL"
        c["release_eligible"] = True
        return self.store.store("candidate", canonical_bytes(c))

    def test_fabricated_self_consistent_full_not_matching_request_rejected_all_layers(
            self):
        """盲审关闭：任意自洽全 PASS 产物 + 空 open_items 不得被批准。

        伪造一套**完全自洽**的 FULL 产物（全部 PASS、open_items 空、哈希重算
        自洽、根元数据 FULL/true）但**不是绑定请求的重放结果** —— 批准/准出/
        发布三层全拒，零 Approval/Release/Current 写入。
        """
        full = self._freeze_full()
        products = copy.deepcopy(full.recompute.products)
        products["calc_ledger"]["ledger"][0]["value"] = "0.09"
        forged = self._forge_products(full, products)
        why = final_candidate_release_gate(self.store, forged)
        self.assertIn("E-G6A-06-030", why)
        m = self._manifest(forged)
        before_a = self.s.query(Approval).count()
        with self.assertRaises(ValueError) as cm:
            create_approval(self.store, self.s, m, "U-fixture",
                            RESEARCH_600089_KEY,
                            approved_at="2026-08-11T07:00:00Z",
                            acknowledged=True)
        self.assertIn("E-G6A-06-030", str(cm.exception))
        self.assertEqual(self.s.query(Approval).count(), before_a,
                         "伪造自洽 FULL 不得获得 Approval 行")
        appr = self._approval_for(m, forged)
        ok, why2 = is_release_eligible(self.s, self.store, appr, m,
                                       RESEARCH_600089_KEY,
                                       candidate_digest=forged)
        self.assertFalse(ok)
        self.assertIn("E-G6A-06-030", why2)
        with self.assertRaises(ValueError) as cm2:
            publish_release(self.store, self.s, m, RESEARCH_600089_KEY, appr,
                            candidate_digest=forged,
                            released_at="2026-08-11T07:01:00Z",
                            writer="L11_release")
        self.assertIn("E-G6A-06-030", str(cm2.exception))
        self.assertEqual(self.s.query(Release).count(), 0)
        self.assertEqual(self.s.query(CurrentPointer).count(), 0)

    def test_all_pass_products_over_partial_request_rejected(self):
        """绑定请求声明 PARTIAL（fcfe 缺输入），却伪造自洽全 PASS 产物 +
        空 open_items → 重放对比失败拒绝。"""
        partial = self._freeze_partial()
        full_ctx = _ctx(routes=_routes(), policy=_policy(tolerance="2"))
        forged = self._forge_products(partial, recompute_all(full_ctx).products)
        self.assertIn("E-G6A-06-030",
                      final_candidate_release_gate(self.store, forged))

    def test_mutated_request_hash_to_fabricated_request_rejected(self):
        """候选把 request_hash 指向另一份**合法**请求 → 重放批准/产物不一致。"""
        full = self._freeze_full()
        other = _request_payload(approve=["growth"])   # 批准 growth 的合法请求
        other_hash = self.store.store(FINAL_CANDIDATE_REQUEST_KIND,
                                      canonical_bytes(other))
        forged = self._forge(full, cand_mut=lambda c:
                             c.update(request_hash=other_hash) or c)
        self.assertIn("E-G6A-06-030",
                      final_candidate_release_gate(self.store, forged))

    def test_mutated_request_hash_nonexistent_rejected(self):
        full = self._freeze_full()
        forged = self._forge(full, cand_mut=lambda c:
                             c.update(request_hash="0" * 64) or c)
        self.assertIn("E-G6A-06-030",
                      final_candidate_release_gate(self.store, forged))

    def test_tampered_request_body_rejected(self):
        """请求对象在库内被原地篡改 → store.load 读时哈希校验拒（E-G6A-06-018
        归一 E-G6A-06-030），不得把篡改请求当作绑定来源。"""
        full = self._freeze_full()
        req_hash = full.candidate["request_hash"]
        target = os.path.join(str(self.store.root), req_hash[:2],
                              req_hash[2:4], req_hash[4:])
        os.chmod(target, 0o600)
        with open(target, "wb") as fh:
            fh.write(b'tampered-request-bytes')
        self.assertIn("E-G6A-06-030",
                      final_candidate_release_gate(self.store,
                                                   full.candidate_id))

    def test_mutated_request_run_id_rejected(self):
        full = self._freeze_full()
        forged = self._forge(full, cand_mut=lambda c:
                             c.update(run_id="other-run") or c)
        self.assertIn("E-G6A-06-030",
                      final_candidate_release_gate(self.store, forged))

    def test_mutated_request_context_identity_rejected(self):
        full = self._freeze_full()
        forged = self._forge(full, cand_mut=lambda c:
                             c.update(contract="C-OTHER") or c)
        self.assertIn("E-G6A-06-030",
                      final_candidate_release_gate(self.store, forged))

    def test_mutated_approved_snapshot_rejected(self):
        full = self._freeze_full()
        forged = self._forge(full, cand_mut=lambda c:
                             c.update(approved_snapshot="1" * 64) or c)
        self.assertIn("E-G6A-06-030",
                      final_candidate_release_gate(self.store, forged))

    def test_mutated_frozen_inputs_hash_rejected(self):
        full = self._freeze_full()
        forged = self._forge(full, cand_mut=lambda c:
                             c.update(frozen_inputs_hash="2" * 64) or c)
        self.assertIn("E-G6A-06-030",
                      final_candidate_release_gate(self.store, forged))

    def test_mutated_product_body_with_recomputed_root_hash_rejected(self):
        """改产品正文并重算候选根哈希（自洽但非请求重放）→ 发布门拒绝。"""
        full = self._freeze_full()
        prod = copy.deepcopy(full.recompute.products["valuation_fcff"])
        prod["per_share_base"] = "950.0"
        prod["per_share_low"] = "900.0"
        prod["per_share_high"] = "1000.0"
        forged = self._forge(full, product_bodies={"valuation_fcff": prod})
        self.assertIn("E-G6A-06-030",
                      final_candidate_release_gate(self.store, forged))

    def test_genuine_full_managed_bundle_passes_and_exposes_request_hash(self):
        """防过度修复：真 FULL 受管 bundle 通过门；复验暴露绑定 request_hash，
        请求对象真实落库。"""
        fr = self._freeze_full()
        self.assertIsNone(final_candidate_release_gate(self.store,
                                                       fr.candidate_id))
        service = CandidateFreezeService(self.store)
        b = service.load_candidate_bundle(fr.candidate_id, **_EXPECTED)
        self.assertEqual(b.request_hash, fr.candidate["request_hash"])
        self.assertRegex(b.request_hash, r"^[0-9a-f]{64}$")
        v = service.verify_candidate_bundle(fr.candidate_id, **_EXPECTED)
        self.assertEqual(v["request_hash"], fr.candidate["request_hash"])
        self.assertTrue(self.store.exists(fr.candidate["request_hash"]))
        req = json.loads(
            self.store.load(fr.candidate["request_hash"]).decode("utf-8"))
        self.assertEqual(req["schema_version"], "1.1.0")
        self.assertEqual(req["run_id"], "full-run")

    def test_freeze_without_request_dependency_fails_closed(self):
        """不允许无受管请求依赖的 canonical 最终候选 —— request_payload 必填
        关键字参数；显式 None 同样失败关闭。"""
        service = CandidateFreezeService(self.store)
        ctx = _ctx(routes=_routes(), policy=_policy(tolerance="2"))
        with self.assertRaises(TypeError):
            service.freeze_final_candidate(ctx, "full-run", _REV_A, _TREE_A,
                                           recompute_all(ctx))
        with self.assertRaises(RecomputeError) as cm:
            service.freeze_final_candidate(
                ctx, "full-run", _REV_A, _TREE_A, recompute_all(ctx),
                request_payload=None)
        self.assertIn("E-G6A-06-002", str(cm.exception))
        self.assertEqual(sum(len(fs) for _, _, fs in
                             os.walk(str(self.store.root))), 0,
                         "失败关闭不得写任何对象")

    def _store_count(self):
        return sum(len(fs) for _, _, fs in os.walk(str(self.store.root)))

    def test_non_finite_request_rejected_before_candidate_write(self):
        """请求任意位置出现 NaN/Infinity → 严格 JSON（allow_nan=False）在写入
        前以 E-G6A-06-002 失败关闭，按对象库计数证明零对象写入。"""
        injectors = {
            "facts 数值 NaN":
                lambda p: p["context"]["facts"].update(fcff=float("nan")),
            "容差 Infinity":
                lambda p: p["context"]["open_items_policy"].update(
                    tolerance=float("inf")),
            "决策字段 NaN":
                lambda p: p["context"]["approved_snapshot"]["decisions"][0]
                .update(decided_at=float("nan")),
        }
        for label, inject in injectors.items():
            with self.subTest(label=label):
                ctx = _ctx(approve=["growth"], routes=_routes(),
                           policy=_policy(tolerance="2"))
                payload = _payload_from_ctx(ctx, "full-run")
                inject(payload)
                before = self._store_count()
                service = CandidateFreezeService(self.store)
                with self.assertRaises(RecomputeError) as cm:
                    service.freeze_final_candidate(
                        ctx, "full-run", _REV_A, _TREE_A,
                        recompute_all(ctx), request_payload=payload)
                self.assertIn("E-G6A-06-002", str(cm.exception))
                self.assertEqual(self._store_count(), before,
                                 f"{label} 不得写任何对象")

    def test_caller_payload_mutation_after_freeze_cannot_change_stored_request(
            self):
        """冻结后调用方改动原载荷：已存请求字节必须仍是**冻结时的那一份**，
        request_hash 不变、bundle 复验仍通过（TOCTOU 关闭）。"""
        ctx = _ctx(routes=_routes(), policy=_policy(tolerance="2"))
        payload = _payload_from_ctx(ctx, "full-run")
        expected = canonical_bytes(payload)
        service = CandidateFreezeService(self.store)
        fr = service.freeze_final_candidate(
            ctx, "full-run", _REV_A, _TREE_A, recompute_all(ctx),
            request_payload=payload)
        # 冻结返回后把调用方原载荷改得面目全非（会改变请求哈希若被重读）。
        payload["run_id"] = "hijacked-run"
        payload["context"]["facts"]["fcff"] = "999999999"
        stored = self.store.load(fr.candidate["request_hash"])
        self.assertEqual(stored, expected,
                         "已存请求字节必须等于冻结时的单一规范映像")
        obj = json.loads(stored.decode("utf-8"))
        self.assertEqual(obj["run_id"], "full-run",
                         "调用方后续改动不得影响已存请求对象")
        service2 = CandidateFreezeService(self.store)
        b = service2.load_candidate_bundle(fr.candidate_id, **_EXPECTED)
        self.assertEqual(b.request_hash, fr.candidate["request_hash"])

    def test_caller_payload_mutation_during_binding_cannot_change_stored_request(
            self):
        """冻结进行中（绑定重放阶段）调用方改动原载荷：已存请求字节仍为
        冻结开始时规范化的那一份 —— `_bind_managed_request` 只重放 immutable
        字节映像，不读调用方可变对象。"""
        import candidate_service as S
        ctx = _ctx(routes=_routes(), policy=_policy(tolerance="2"))
        payload = _payload_from_ctx(ctx, "full-run")
        expected = canonical_bytes(payload)
        real = S.recompute_all
        calls = {"n": 0}

        def _mutate_during(c):
            calls["n"] += 1
            if calls["n"] == 1:
                payload["run_id"] = "mutated-during-bind"
                payload["context"]["facts"]["fcff"] = "123456789"
            return real(c)

        try:
            S.recompute_all = _mutate_during
            service = CandidateFreezeService(self.store)
            fr = service.freeze_final_candidate(
                ctx, "full-run", _REV_A, _TREE_A,
                real(ctx), request_payload=payload)
        finally:
            S.recompute_all = real
        self.assertGreaterEqual(calls["n"], 1, "绑定重放确已执行")
        self.assertEqual(self.store.load(fr.candidate["request_hash"]),
                         expected,
                         "绑定期调用方改动不得改变已存请求字节")
        obj = json.loads(
            self.store.load(fr.candidate["request_hash"]).decode("utf-8"))
        self.assertEqual(obj["run_id"], "full-run")

    def test_stored_request_reload_rejects_non_standard_constants(self):
        """库内请求对象带非标准 JSON 常量（NaN 字面量）→ 重载用
        parse_constant 严格拒绝，E-G6A-06-018 归一 E-G6A-06-030。"""
        ctx = _ctx(routes=_routes(), policy=_policy(tolerance="2"))
        payload = _payload_from_ctx(ctx, "full-run")
        payload["context"]["facts"]["fcff"] = float("nan")
        bad_bytes = canonical_bytes(payload)   # allow_nan 缺省 → 含 NaN 字面量
        self.assertIn(b"NaN", bad_bytes, "前提：字节映像含非标准常量")
        bad_hash = self.store.store(FINAL_CANDIDATE_REQUEST_KIND, bad_bytes)
        full = self._freeze_full()
        forged = self._forge(full, cand_mut=lambda c:
                             c.update(request_hash=bad_hash) or c)
        self.assertIn("E-G6A-06-030",
                      final_candidate_release_gate(self.store, forged))

    def test_freeze_direct_request_revision_mismatch_zero_write(self):
        """直接冻结路径：请求声明的 source revision ≠ 显式提供的干净 checkout
        → E-G6A-06-002 失败关闭，零对象写入。"""
        ctx = _ctx(routes=_routes(), policy=_policy(tolerance="2"))
        payload = _payload_from_ctx(ctx, "full-run")
        payload["source_revision"]["source_commit"] = "f" * 40
        before = self._store_count()
        service = CandidateFreezeService(self.store)
        with self.assertRaises(RecomputeError) as cm:
            service.freeze_final_candidate(
                ctx, "full-run", _REV_A, _TREE_A, recompute_all(ctx),
                request_payload=payload)
        self.assertIn("E-G6A-06-002", str(cm.exception))
        self.assertEqual(self._store_count(), before, "不得写任何对象")

    def test_request_byte_change_in_decision_metadata_changes_candidate_identity(
            self):
        """请求字节任一变化（批准决定的时间戳）→ request_hash 与 candidate_id
        变化，即使批准 payload 与产物正文完全不变（决定元数据绑定进请求身份）。"""
        ctx = _ctx(approve=["growth"], routes=_routes(),
                   policy=_policy(tolerance="2"))
        service = CandidateFreezeService(self.store)
        base = _payload_from_ctx(ctx, "full-run")
        a = service.freeze_final_candidate(
            ctx, "full-run", _REV_A, _TREE_A, recompute_all(ctx),
            request_payload=base)
        mut = copy.deepcopy(base)
        self.assertTrue(mut["context"]["approved_snapshot"]["decisions"])
        mut["context"]["approved_snapshot"]["decisions"][0]["decided_at"] = \
            "2026-08-13T12:00:00Z"
        b = service.freeze_final_candidate(
            ctx, "full-run", _REV_A, _TREE_A, recompute_all(ctx),
            request_payload=mut)
        self.assertEqual(a.recompute.products, b.recompute.products,
                         "批准 payload 与产物正文须完全不变")
        self.assertEqual(a.candidate["approved_snapshot"],
                         b.candidate["approved_snapshot"],
                         "快照正文哈希不变（决定元数据不进快照正文）")
        self.assertEqual(a.candidate["frozen_inputs_hash"],
                         b.candidate["frozen_inputs_hash"])
        self.assertNotEqual(a.candidate["request_hash"],
                            b.candidate["request_hash"],
                            "决定时间戳字节变化必须改变请求身份")
        self.assertNotEqual(a.candidate_id, b.candidate_id,
                            "请求身份变化必须改变候选身份")


class TestFinalCandidateShapeScoping(unittest.TestCase):
    """G6A-06 请求绑定/partial-route 硬化：只有**精确** legacy G4 形状
    （schema_version 1.0.0 + kind=candidate + payload object，根键集恰好三项）
    跳过发布资格门；任何其他 candidate 对象（含剥离全部强标记但保留
    run_id/contract/scope/as_of/products/approved_snapshot 的形态）都是
    strict-final，按 canonical 1.1 校验或 E-G6A-06-030 失败关闭；非 candidate
    对象仅带通用溯源/状态字段不误挡，仅带明确最终依赖标记才判 malformed-final。"""

    def _store(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        return ArtifactStore(os.path.join(tmp, "objects"))

    def test_exact_legacy_candidate_skips_gate(self):
        """精确 legacy G4 形状（1.0.0 + kind + payload object）不受本门影响。"""
        store = self._store()
        legacy = fx.build_candidate(store, {"ticker": "LEGACY"})
        self.assertIsNone(final_candidate_release_gate(store, legacy))

    def test_legacy_candidate_with_generic_products_field_is_strict_final(self):
        """legacy candidate 根部**多出**通用 products 字段即不再是精确 legacy
        形状 → strict-final，畸形字段拒绝（不再有「可加通用字段」的例外）。"""
        store = self._store()
        legacy = fx.freeze_object(store, "candidate", {
            "schema_version": "1.0.0", "kind": "candidate",
            "payload": {"ticker": "LEGACY"}, "products": ["x", "y"]})
        self.assertIn("E-G6A-06-030", final_candidate_release_gate(store, legacy))

    def test_legacy_candidate_with_approved_snapshot_field_is_strict_final(self):
        store = self._store()
        legacy = fx.freeze_object(store, "candidate", {
            "schema_version": "1.0.0", "kind": "candidate",
            "payload": {"ticker": "LEGACY"},
            "approved_snapshot": {"snapshot_id": "S"}})
        self.assertIn("E-G6A-06-030", final_candidate_release_gate(store, legacy))

    def test_stripped_11_candidate_without_strong_markers_is_strict_final(self):
        """剥离全部强标记、降级版本但仍保留最终字段（run_id/contract/scope/
        as_of/products/approved_snapshot）的 candidate 不是精确 legacy →
        strict-final，E-G6A-06-030 拒绝（PARTIAL→legacy 降级关闭）。"""
        store = self._store()
        stripped = fx.freeze_object(store, "candidate", {
            "schema_version": "1.0.0", "kind": "candidate",
            "run_id": "partial-run", "contract": "C-600089",
            "scope": "600089.SH", "as_of": "2026-07-01",
            "products": ["calc_ledger"],
            "approved_snapshot": "0" * 64})
        self.assertIn("E-G6A-06-030", final_candidate_release_gate(store, stripped))

    def test_11_candidate_malformed_fields_rejected(self):
        """schema_version 1.1 的 candidate 是 strict-final —— 畸形字段拒绝。"""
        store = self._store()
        bad = fx.freeze_object(store, "candidate", {
            "schema_version": "1.1.0", "kind": "candidate",
            "quality_status": "FULL", "release_eligible": True})
        self.assertIn("E-G6A-06-030", final_candidate_release_gate(store, bad))

    def test_object_with_single_strong_final_marker_is_strict_final(self):
        store = self._store()
        obj = fx.freeze_object(store, "candidate", {"request_hash": "0" * 64})
        self.assertIn("E-G6A-06-030", final_candidate_release_gate(store, obj))

    def test_non_candidate_report_with_provenance_fields_not_blocked(self):
        """防过度拦截：非 candidate 对象仅带 source_commit/quality_status 等
        通用溯源/状态字段 → 不判 malformed-final，交 G4 治理。"""
        store = self._store()
        report = fx.freeze_object(store, "report", {
            "schema_version": "1.0.0", "kind": "report",
            "text": "fixture 报告（合成）", "source_commit": _REV_A,
            "quality_status": "PARTIAL", "run_id": "r", "as_of": "2026-07-01"})
        self.assertIsNone(final_candidate_release_gate(store, report))

    def test_non_candidate_report_with_request_hash_is_malformed_final(self):
        """非 candidate 对象带 `request_hash`/`product_hashes` 这类**明确最终
        依赖标记** → 判 malformed-final，E-G6A-06-030 拒绝。"""
        store = self._store()
        report = fx.freeze_object(store, "report", {
            "schema_version": "1.0.0", "kind": "report",
            "text": "fixture 报告（合成）", "source_commit": _REV_A,
            "request_hash": "0" * 64})
        self.assertIn("E-G6A-06-030", final_candidate_release_gate(store, report))

    def test_declared_candidate_body_without_kind_is_strict_final(self):
        """调用点声明候选（expected_candidate=True）时，正文缺 kind 且仅带通用
        残留字段 → 不借非候选形状跳过，E-G6A-06-030。"""
        store = self._store()
        stripped = fx.freeze_object(store, "candidate", {
            "schema_version": "1.0.0",
            "run_id": "partial-run", "contract": "C-600089",
            "scope": "600089.SH", "as_of": "2026-07-01",
            "products": ["calc_ledger"], "approved_snapshot": "0" * 64})
        self.assertIsNone(final_candidate_release_gate(store, stripped),
                          "未声明候选（非最终形状）默认跳过")
        self.assertIn("E-G6A-06-030",
                      final_candidate_release_gate(store, stripped,
                                                   expected_candidate=True),
                      "声明候选下缺 kind 正文必须 strict-final/malformed")

    def test_declared_candidate_report_body_is_strict_final(self):
        """调用点声明候选但正文是报告（仅通用溯源/状态字段）→ 判 malformed-final
        E-G6A-06-030，不借「非 candidate 不带最终标记」跳过；未声明候选时不受
        影响（防过度拦截）。"""
        store = self._store()
        report = fx.freeze_object(store, "report", {
            "schema_version": "1.0.0", "kind": "report",
            "text": "fixture 报告（合成）", "source_commit": _REV_A,
            "quality_status": "PARTIAL", "run_id": "r", "as_of": "2026-07-01"})
        self.assertIsNone(final_candidate_release_gate(store, report),
                          "未声明候选（非 candidate 根）默认不挡")
        self.assertIn("E-G6A-06-030",
                      final_candidate_release_gate(store, report,
                                                   expected_candidate=True),
                      "声明候选下正文非候选形状 → strict-final/malformed")

    def test_exact_legacy_skips_even_when_declared_candidate(self):
        """expected_candidate=True 时精确 legacy G4 形状仍跳过（G4 治理防误伤）。"""
        store = self._store()
        legacy = fx.build_candidate(store, {"ticker": "LEGACY"})
        self.assertIsNone(final_candidate_release_gate(store, legacy,
                                                       expected_candidate=True))


class TestFinalCandidateClosure(unittest.TestCase):
    """G6A-06 最终候选依赖进闭包/GC 语义：canonical 最终候选内部恰好引用
    request_hash + 11 项产品哈希（共 12 个依赖 digest）；清单 candidate refs
    必须精确等于这 12 个、且全部已登记 —— 缺失/多余/错配都使闭包不完整、
    批准失败；GC 只移除真正无关的孤儿。"""

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self.store = ArtifactStore(os.path.join(self._tmp, "lib"))
        self.repo = create_repository(os.path.join(self._tmp, "pub.sqlite3"))
        self.repo.create_all()
        self.s = self.repo.session()

    def tearDown(self):
        self.s.close()
        self.repo.engine.dispose()
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _freeze_full(self):
        service = CandidateFreezeService(self.store)
        ctx = _ctx(routes=_routes(), policy=_policy(tolerance="2"))
        return service.freeze_final_candidate(ctx, "full-run", _REV_A,
                                              _TREE_A, recompute_all(ctx),
                                              request_payload=_payload_from_ctx(
                                                  ctx, "full-run"))

    def _freeze_partial(self):
        service = CandidateFreezeService(self.store)
        ctx = _ctx(routes=_routes(fcfe=_decl(
            "fcfe", ROUTE_INPUT_MISSING, "缺输入", ("EV-9",), ("net_debt",))))
        return service.freeze_final_candidate(ctx, "partial-run", _REV_A,
                                              _TREE_A, recompute_all(ctx),
                                              request_payload=_payload_from_ctx(
                                                  ctx))

    def _bundle(self, fr):
        service = CandidateFreezeService(self.store)
        return service.load_candidate_bundle(fr.candidate_id, **_EXPECTED)

    def _complete_manifest(self, fr, *, with_report=True):
        b = self._bundle(fr)
        report = None
        if with_report:
            report = fx.build_report(self.store, with_nbs_attribution=False)
        objects = _complete_manifest_objects(self.store, b, report)
        return fx.manifest_of(self.store, RESEARCH_600089_KEY,
                              root=fr.candidate_id, objects=objects)

    def test_candidate_manifest_refs_exactly_equal_twelve_dependencies(self):
        """candidate 清单 refs 精确 = request_hash + 全部 11 项产品哈希（列表
        12 项；内容寻址下正文可去重，集合判等），由生产助手
        bundle_manifest_objects 生成、不手抄。"""
        fr = self._freeze_full()
        m = self._complete_manifest(fr)
        expected = ({fr.candidate["request_hash"]}
                    | set(fr.candidate["product_hashes"].values()))
        refs = m["objects"][fr.candidate_id]["refs"]
        self.assertEqual(len(refs), 12,
                         "refs 列表 = request_hash + 11 项产品哈希")
        self.assertEqual(set(refs), expected,
                         "refs 集合精确等于 12 依赖 digest 集合")
        self.assertTrue(set(m["objects"]) >= (expected
                                              | {fr.candidate_id,
                                                 fr.candidate["request_hash"]}),
                        "全部依赖（含内容寻址去重）须已登记")

    def test_root_only_final_candidate_manifest_incomplete_approval_fails(self):
        """仅候选根、无依赖登记的清单：闭包不完整（refs_violation）、审计
        完整性门 FAIL、批准 E-G4-04-001 拒绝且不留 Approval 行。"""
        fr = self._freeze_full()
        m = fx.manifest_of(self.store, RESEARCH_600089_KEY,
                           root=fr.candidate_id,
                           objects={fr.candidate_id: {"kind": "candidate",
                                                      "refs": []}})
        c = compute_closure(self.store, m)
        self.assertFalse(c.complete)
        self.assertIn("E-G4-07-008", c.refs_violation)
        audit = audit_candidate(self.store, m, fr.candidate_id)
        self.assertIn("E-G4-02-001", audit.failures[0])
        self.assertFalse(audit.release_eligible)
        with self.assertRaises(ValueError) as cm:
            create_approval(self.store, self.s, m, "U-fixture",
                            RESEARCH_600089_KEY,
                            approved_at="2026-08-11T07:00:00Z",
                            acknowledged=True)
        self.assertIn("E-G4-04-001", str(cm.exception))
        self.assertEqual(self.s.query(Approval).count(), 0,
                         "闭包不完整不得留下 Approval 行")

    def test_complete_manifest_passes_closure_audit_approval_when_full(self):
        """完整闭包（candidate refs + 12 个已登记依赖 + report）在 FULL 时
        闭包完整、审计通过、批准成功。"""
        fr = self._freeze_full()
        m = self._complete_manifest(fr)
        c = compute_closure(self.store, m)
        self.assertTrue(c.complete, f"闭包须完整（{c.refs_violation}）")
        audit = audit_candidate(self.store, m, fr.candidate_id)
        self.assertTrue(audit.release_eligible)
        before = self.s.query(Approval).count()
        create_approval(self.store, self.s, m, "U-fixture",
                        RESEARCH_600089_KEY,
                        approved_at="2026-08-11T07:00:00Z",
                        acknowledged=True)
        self.assertEqual(self.s.query(Approval).count(), before + 1)

    def test_candidate_extra_ref_makes_closure_incomplete(self):
        """candidate refs 多出与内部依赖无关的引用 → refs_violation，闭包不完整。"""
        fr = self._freeze_full()
        m = self._complete_manifest(fr)
        report = fx.build_report(self.store, with_nbs_attribution=False)
        m["objects"][fr.candidate_id]["refs"].append(report)
        m["objects"][report] = {"kind": "report", "refs": []}
        m["id"] = content_id(m)
        c = compute_closure(self.store, m)
        self.assertFalse(c.complete)
        self.assertIn("E-G4-07-008", c.refs_violation)
        self.assertIn("多", c.refs_violation)
        with self.assertRaises(ValueError):
            create_approval(self.store, self.s, m, "U-fixture",
                            RESEARCH_600089_KEY,
                            approved_at="2026-08-11T07:00:00Z",
                            acknowledged=True)

    def test_candidate_missing_dependency_registration_makes_closure_incomplete(
            self):
        """candidate refs 含 12 个 digest 但其中一项未登记 → dangling +
        依赖未登记违规，闭包不完整、批准失败。"""
        fr = self._freeze_full()
        m = self._complete_manifest(fr)
        victim = fr.candidate["product_hashes"]["calc_ledger"]
        del m["objects"][victim]
        m["id"] = content_id(m)
        c = compute_closure(self.store, m)
        self.assertFalse(c.complete)
        self.assertTrue(victim in c.dangling,
                        "未登记依赖须进入 dangling")
        self.assertIn("E-G4-07-008", c.refs_violation)
        with self.assertRaises(ValueError):
            create_approval(self.store, self.s, m, "U-fixture",
                            RESEARCH_600089_KEY,
                            approved_at="2026-08-11T07:00:00Z",
                            acknowledged=True)

    def test_final_body_mislabeled_as_report_root_only_refs_violation(self):
        """最终候选正文被错标为 report、refs 为空且无依赖登记 → 正文驱动的闭包
        核定仍返回 E-G4-07-008（不信任清单元数据 kind）。"""
        fr = self._freeze_full()
        m = fx.manifest_of(self.store, RESEARCH_600089_KEY,
                           root=fr.candidate_id,
                           objects={fr.candidate_id: {"kind": "report",
                                                      "refs": []}})
        c = compute_closure(self.store, m)
        self.assertFalse(c.complete)
        self.assertIn("E-G4-07-008", c.refs_violation)
        self.assertIn("kind", c.refs_violation)

    def test_final_body_mislabeled_full_deps_still_refs_violation(self):
        """最终候选正文被错标为 report、但 12 依赖 refs/登记全部正确 → 仍
        E-G4-07-008（正文驱动：元数据 kind 与正文不符即闭包不完整）。"""
        fr = self._freeze_full()
        b = self._bundle(fr)
        objects = bundle_manifest_objects(b)
        objects[fr.candidate_id] = {"kind": "report",
                                    "refs": objects[fr.candidate_id]["refs"]}
        m = fx.manifest_of(self.store, RESEARCH_600089_KEY,
                           root=fr.candidate_id, objects=objects)
        c = compute_closure(self.store, m)
        self.assertFalse(c.complete)
        self.assertIn("E-G4-07-008", c.refs_violation)
        self.assertIn("kind", c.refs_violation)

    def test_malformed_product_hash_value_type_fails_closed(self):
        """畸形 product_hash 值类型（非字符串）→ 归一为 E-G4-07-008 违规，不抛
        裸 TypeError/OSError。"""
        fr = self._freeze_full()
        body = dict(fr.candidate)
        body["product_hashes"] = dict(fr.candidate["product_hashes"])
        body["product_hashes"]["calc_ledger"] = 12345
        bad = self.store.store("candidate", canonical_bytes(body))
        m = fx.manifest_of(self.store, RESEARCH_600089_KEY, root=bad,
                           objects={bad: {"kind": "candidate", "refs": []}})
        c = compute_closure(self.store, m)
        self.assertFalse(c.complete)
        self.assertIn("E-G4-07-008", c.refs_violation)

    def test_malformed_refs_type_fails_closed(self):
        """清单 refs 非列表（None）→ 归一 E-G4-07-008 违规，BFS 不裸崩。"""
        fr = self._freeze_full()
        m = fx.manifest_of(self.store, RESEARCH_600089_KEY,
                           root=fr.candidate_id,
                           objects={fr.candidate_id: {"kind": "candidate",
                                                      "refs": None}})
        c = compute_closure(self.store, m)
        self.assertFalse(c.complete)
        self.assertIn("E-G4-07-008", c.refs_violation)

    def test_candidate_refs_list_with_unhashable_item_fails_closed(self):
        """清单 candidate refs 列表含 JSON 对象/list（不可哈希项）→ 归一
        E-G4-07-008 refs_violation、闭包不完整；set/sorting 在混合类型下
        不抛裸 TypeError。"""
        fr = self._freeze_full()
        for bad in ({"kind": "report"}, ["0" * 64], "", 123):
            with self.subTest(bad=bad):
                m = self._complete_manifest(fr)
                m["objects"][fr.candidate_id]["refs"] = [bad]
                m["id"] = content_id(m)
                c = compute_closure(self.store, m)
                self.assertFalse(c.complete,
                                 f"畸形 refs 项 {bad!r} 须失败关闭")
                self.assertIn("E-G4-07-008", c.refs_violation,
                              f"畸形 refs 项 {bad!r} 须报 E-G4-07-008")

    def test_generic_refs_list_with_unhashable_item_marks_dangling(self):
        """泛型（legacy G4 候选）清单 refs 含 JSON 对象 → BFS 归一为 dangling
        标记、闭包不完整，不抛裸 TypeError（防 BFS 裸崩）。"""
        m = fx.minimal_closure(self.store, RESEARCH_600089_KEY)
        claim_oid = next(oid for oid, meta in m["objects"].items()
                         if meta.get("kind") == "claim")
        m["objects"][claim_oid]["refs"] = [{"bad": True}]
        m["id"] = content_id(m)
        c = compute_closure(self.store, m)
        self.assertFalse(c.complete)
        self.assertTrue(any(r.startswith("<malformed-ref:")
                            for r in c.dangling),
                        "畸形 ref 项须进入 dangling 标记（fail-closed）")

    def test_gc_only_preserves_deps_of_complete_correctly_labeled_manifest(self):
        """gc 只对正确标定且登记完整的清单保留依赖 —— 最终候选正文被错标为
        report 的清单（闭包不完整）不得借 gc 保住依赖。"""
        fr = self._freeze_full()
        b = self._bundle(fr)
        objects = bundle_manifest_objects(b)
        objects[fr.candidate_id] = {"kind": "report",
                                    "refs": objects[fr.candidate_id]["refs"]}
        m = fx.manifest_of(self.store, RESEARCH_600089_KEY,
                           root=fr.candidate_id, objects=objects)
        orphans = gc_orphans(self.store, [m])
        self.assertIn(fr.candidate["request_hash"], orphans,
                      "错标清单的请求依赖不得被 gc 保留")
        for digest in fr.candidate["product_hashes"].values():
            self.assertIn(digest, orphans,
                          "错标/不完整清单的产品依赖不得被 gc 保留")

    def test_partial_complete_closure_still_rejected_by_final_gate(self):
        """PARTIAL 最终候选即使闭包完整也仍被资格门 E-G6A-06-031 拒绝，批准
        零 Approval 行 —— 闭包完整性不豁免质量门。"""
        fr = self._freeze_partial()
        m = self._complete_manifest(fr)
        self.assertTrue(compute_closure(self.store, m).complete)
        self.assertIn("E-G6A-06-031",
                      final_candidate_release_gate(self.store, fr.candidate_id))
        with self.assertRaises(ValueError) as cm:
            create_approval(self.store, self.s, m, "U-fixture",
                            RESEARCH_600089_KEY,
                            approved_at="2026-08-11T07:00:00Z",
                            acknowledged=True)
        self.assertIn("E-G6A-06-031", str(cm.exception))
        self.assertEqual(self.s.query(Approval).count(), 0)

    def test_gc_orphans_preserves_candidate_request_products_only_removes_orphan(
            self):
        """gc_orphans：完整闭包保留 candidate / 绑定请求 / 11 项产品，只移除
        真正无关的孤儿对象。"""
        fr = self._freeze_full()
        m = self._complete_manifest(fr)
        orphan = self.store.store(
            "recompute_product",
            canonical_bytes({"kind": "recompute_product", "orphan": True}))
        orphans = gc_orphans(self.store, [m])
        self.assertIn(orphan, orphans, "无关孤儿须被移除")
        self.assertNotIn(fr.candidate_id, orphans)
        self.assertNotIn(fr.candidate["request_hash"], orphans)
        for digest in fr.candidate["product_hashes"].values():
            self.assertNotIn(digest, orphans)
        self.assertTrue(self.store.exists(fr.candidate_id))
        self.assertTrue(self.store.exists(fr.candidate["request_hash"]))
        for digest in fr.candidate["product_hashes"].values():
            self.assertTrue(self.store.exists(digest),
                            "闭包内产品正文不得被回收")
        self.assertFalse(self.store.exists(orphan))


class TestAssumptionMaterialization(unittest.TestCase):
    """G6A-06 partial-route 返工：账本/声明/映射产物**只含 READY 路由实际
    消费的假设**（canonical ROUTE_ASSUMPTIONS 单点真源）。

      · 全部四路非 READY → calc_ledger/claim_map/emission_map 为空
        （formula_count 保留），不把调用方无关默认/提案发明成假设数值；
      · READY 路由缺所需假设 → 回算边界 E-G6A-06-020 失败关闭、零 candidate；
      · 混合 READY/non-READY → 只消费 READY 路由假设，非 READY 路由的
        无关假设不泄漏进产物。
    """

    def _non_ready_ctx(self, *, with_defaults=True, approved=None):
        """直接 ResearchContext：全部非 READY（NOT_EVALUATED），空 facts、
        最小 valuation_inputs、可空 assumption_defaults/批准快照。"""
        reg = AssumptionRegistry()
        if approved is None:
            props = {
                "growth": AssumptionProposal("A-GROWTH", {"growth": "0.08"},
                                             proposed_by="L8"),
                "wacc": AssumptionProposal("A-WACC", {"wacc": "0.09"},
                                           proposed_by="L8"),
                "ke": AssumptionProposal("A-KE", {"ke": "0.13"},
                                         proposed_by="L8"),
                "target_pe": AssumptionProposal("A-PE", {"target_pe": "15"},
                                                proposed_by="L8"),
                "roe": AssumptionProposal("A-ROE", {"roe": "0.15"},
                                          proposed_by="L8"),
            }
            for p in props.values():
                reg.propose(p)
            for key in (approved or ()):
                reg.decide(props[key].proposal_id, APPROVED, "U",
                           "2026-08-12T12:00:00Z", "APPROVE")
        snap = AssumptionSnapshot("SNAP-NONREADY", 1).build(reg)
        routes = {r: _decl(r, ROUTE_NOT_EVALUATED, f"无 {r} 证据", (f"EV-{r}",))
                  for r in VALUATION_ROUTES}
        return ResearchContext(
            contract={"contract_id": "C-600089", "scope": "600089.SH"},
            facts={},
            macro={"wacc_floor": "0.08"},
            formula_specs={"fcff": {"formula": "..."}},
            valuation_inputs=_mk_vi(),
            assumption_defaults=(dict(growth="0.05", wacc="0.10", ke="0.12",
                                      target_pe="12", roe="0.12")
                                 if with_defaults else {}),
            approved=snap,
            open_items_policy=_policy(),
            valuation_routes=ValuationRoutes(routes),
        )

    def test_all_non_ready_recompute_empty_assumption_products(self):
        """全部非 READY → 11 项产品齐全，账本/声明/映射为空（formula_count
        保留）；同一输入确定性一致。"""
        ctx = self._non_ready_ctx()
        res = recompute_all(ctx)
        self.assertEqual(tuple(res.products), tuple(PRODUCT_ORDER))
        self.assertEqual(len(res.products), 11)
        self.assertEqual(res.products["calc_ledger"]["ledger"], [])
        self.assertEqual(res.products["calc_ledger"]["formula_count"], 1)
        self.assertEqual(res.products["claim_map"]["claims"], [])
        self.assertEqual(res.products["emission_map"]["emissions"], [])
        for name in VALUATION_PRODUCT_NAMES:
            self.assertIn(res.products[name]["status"],
                          (ROUTE_INPUT_MISSING, ROUTE_NOT_EVALUATED))
        r2 = recompute_all(ctx)
        self.assertEqual(res.shas, r2.shas, "全部非 READY 也必须确定性一致")

    def test_all_non_ready_managed_request_partial_eleven_products(self):
        """受管请求：四路全非 READY + facts={} + 最小 valuation_inputs +
        assumption_defaults={} + 空 proposals/decisions → PARTIAL/false、
        11 项产品、账本/声明/映射为空，产物不含任何发明的假设数值。"""
        rd = {r: {"state": ROUTE_NOT_EVALUATED, "reason": f"无 {r} 证据",
                  "evidence_refs": [f"EV-{r}"]} for r in VALUATION_ROUTES}
        payload = _request_payload(
            routes_dict=rd, facts={}, approve=[],
            policy={"tolerance": "0.15", "owner_role": "U",
                    "due_date": "2026-08-31", "blocks_gate": "G3-06"})
        payload["context"]["assumption_defaults"] = {}
        payload["context"]["approved_snapshot"] = {
            "snapshot_id": "SNAP-EMPTY", "version": 1,
            "proposals": [], "decisions": []}
        payload["context"]["valuation_inputs"] = {
            "scope": "600089.SH", "currency": "CNY", "as_of": "2026-07-01"}
        with tempfile.TemporaryDirectory() as tmp:
            store = ArtifactStore(os.path.join(tmp, "objects"))
            result = freeze_final_candidate_from_payload(
                store, payload, source_commit=_REV_A, source_tree=_TREE_A)
            cand = result.candidate
            self.assertEqual(cand["quality_status"], "PARTIAL")
            self.assertIs(cand["release_eligible"], False)
            self.assertEqual(len(cand["products"]), 11)
            self.assertEqual(len(cand["product_hashes"]), 11)
            prod = result.recompute.products
            self.assertEqual(prod["calc_ledger"]["ledger"], [])
            self.assertEqual(prod["calc_ledger"]["formula_count"], 1)
            self.assertEqual(prod["claim_map"]["claims"], [])
            self.assertEqual(prod["emission_map"]["emissions"], [])
            for name in VALUATION_PRODUCT_NAMES:
                p = prod[name]
                self.assertNotIn("per_share_base", p,
                                 f"{name} 不得夹带 per-share 数值")
            self.assertEqual(
                prod["open_items"]["route_statuses"],
                {r: ROUTE_NOT_EVALUATED for r in VALUATION_ROUTES})
            # 产物不包含任何假设键数值（growth/wacc/ke/target_pe/roe 均不发明）。
            blob = json.dumps(prod, ensure_ascii=False)
            for key in ("growth", "wacc", "ke", "target_pe", "roe"):
                self.assertNotIn(f'"{key}"', blob,
                                 f"全部非 READY 不得含假设键 {key}")

    def test_each_ready_route_missing_assumption_rejects_zero_write(self):
        """每个 READY 路由缺其一所需假设 → 受管请求与直接上下文都以
        E-G6A-06-020 失败关闭，且零 candidate/对象写入。"""
        needs = {"fcff": ("wacc",), "fcfe": ("growth", "ke"),
                 "relative": ("target_pe",), "pe_roe_pb": ("roe", "target_pe")}
        for route, keys in needs.items():
            with self.subTest(route=route, keys=keys):
                rd = {r: {"state": ROUTE_NOT_EVALUATED,
                          "reason": f"无 {r} 证据", "evidence_refs": [f"EV-{r}"]}
                      for r in VALUATION_ROUTES}
                rd[route] = {"state": ROUTE_READY}
                facts = {"fcff": "400000000", "fcfe": "300000000",
                         "eps": "0.60", "book_per_share": "5.00"}
                for r in VALUATION_ROUTES:
                    if rd[r]["state"] != ROUTE_READY:
                        facts.pop(ROUTE_FACT_KEYS[r], None)
                payload = _request_payload(
                    routes_dict=rd, facts=facts, approve=[],
                    policy={"tolerance": "0.15", "owner_role": "U",
                            "due_date": "2026-08-31", "blocks_gate": "G3-06"})
                payload["context"]["assumption_defaults"] = {}
                payload["context"]["approved_snapshot"] = {
                    "snapshot_id": "SNAP-EMPTY", "version": 1,
                    "proposals": [], "decisions": []}
                # 受管请求：失败关闭且零对象写入。
                with tempfile.TemporaryDirectory() as tmp:
                    store = ArtifactStore(os.path.join(tmp, "objects"))
                    with self.assertRaises(RecomputeError) as cm:
                        freeze_final_candidate_from_payload(
                            store, payload, source_commit=_REV_A,
                            source_tree=_TREE_A)
                    self.assertIn("E-G6A-06-020", str(cm.exception))
                    count = sum(len(fs) for _, _, fs in os.walk(str(store.root)))
                    self.assertEqual(count, 0, "失败关闭不得写任何对象")
                # 直接上下文：同样失败关闭。
                ctx = self._non_ready_ctx(with_defaults=False)
                decls = {r: _decl(r, ROUTE_NOT_EVALUATED, f"无 {r} 证据",
                                  (f"EV-{r}",)) for r in VALUATION_ROUTES}
                decls[route] = _decl(route, ROUTE_READY)
                facts_direct = {"fcff": "400000000", "fcfe": "300000000",
                                "eps": "0.60", "book_per_share": "5.00"}
                for r in VALUATION_ROUTES:
                    if decls[r].state != ROUTE_READY:
                        facts_direct.pop(ROUTE_FACT_KEYS[r], None)
                ctx.facts = facts_direct
                ctx.valuation_routes = ValuationRoutes(decls)
                with self.assertRaises(RecomputeError) as cm2:
                    recompute_all(ctx)
                self.assertIn("E-G6A-06-020", str(cm2.exception))

    def test_mixed_ready_consumes_only_ready_assumptions(self):
        """混合 READY：账本/声明/映射只含 READY 路由假设；调用方为非 READY
        路由提供的无关默认/提案不泄漏进产物。"""
        ctx = _ctx(routes=_routes(
            fcff=_decl("fcff", ROUTE_READY),
            fcfe=_decl("fcfe", ROUTE_INPUT_MISSING, "缺输入", ("EV-9",),
                       ("net_debt",)),
            relative=_decl("relative", ROUTE_READY),
            pe_roe_pb=_decl("pe_roe_pb", ROUTE_NOT_EVALUATED, "无数据",
                            ("EV-4",))))
        res = recompute_all(ctx)
        # READY 路由 fcff(→wacc) + relative(→target_pe)：规范顺序 wacc→target_pe。
        ledger = res.products["calc_ledger"]["ledger"]
        self.assertEqual([e["metric"] for e in ledger],
                         ["wacc_assumption", "target_pe_assumption"])
        claims = res.products["claim_map"]["claims"]
        self.assertEqual([c["assumption"] for c in claims], ["wacc", "target_pe"])
        emissions = res.products["emission_map"]["emissions"]
        self.assertEqual([e["assumption"] for e in emissions],
                         ["wacc", "target_pe"])
        # 非 READY 路由（fcfe/pe_roe_pb）的假设键（growth/ke/roe）不得出现。
        for key in ("growth", "ke", "roe"):
            self.assertNotIn(key, [e["metric"] for e in ledger],
                             f"非 READY 路由假设 {key} 不得进账本")
            self.assertNotIn(key, [c["assumption"] for c in claims],
                             f"非 READY 路由假设 {key} 不得进声明")
            self.assertNotIn(key, [e["assumption"] for e in emissions],
                             f"非 READY 路由假设 {key} 不得进映射")
        # 严格质量派生仍通过（mixed PARTIAL）。
        quality, eligible = quality_from_products(res.products)
        self.assertEqual((quality, eligible), ("PARTIAL", False))

    def test_fabricated_unapproved_assumptions_not_emitted_for_non_ready(self):
        """调用方为**全部非 READY** 上下文提供全套默认与批准提案 → 账本/声明/
        映射仍为空（不把无关输入发明成假设数值）。"""
        ctx = self._non_ready_ctx(with_defaults=True, approved=("growth",))
        res = recompute_all(ctx)
        self.assertEqual(res.products["calc_ledger"]["ledger"], [])
        self.assertEqual(res.products["claim_map"]["claims"], [])
        self.assertEqual(res.products["emission_map"]["emissions"], [])
        quality, eligible = quality_from_products(res.products)
        self.assertEqual((quality, eligible), ("PARTIAL", False))


if __name__ == "__main__":
    unittest.main()
