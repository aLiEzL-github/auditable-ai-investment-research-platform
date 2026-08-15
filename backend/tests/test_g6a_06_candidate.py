"""G6A-06 / OI-PF-203 验收测试：权威最终候选冻结与可复验 bundle。

缺陷（OI-PF-203）：
  · G6A-06 没有权威最终 candidate hash —— 旧 freeze_candidate_from_recompute
    仅测试调用；冻结只存 candidate 摘要，11 项 product 正文不落库；
    candidate 不绑定代码 commit/tree，无法独立复验。
  · 临时测试 candidate 不得冒充最终候选。

本文件验证（对照 OI-PF-203 实现要求）：
  ① 权威冻结入口 `CandidateFreezeService.freeze_final_candidate` 写入 11 项
     产品正文（内容寻址），实际 digest 逐字等于 RecomputeResult.shas，
     candidate 内 product_hashes 全部可 store.load() 并重算。
  ② candidate 含 kind="candidate" + 显式 source_commit/source_tree；
     非 40 位小写十六进制 revision 失败关闭；revision 改一字节改变候选身份。
  ③ 可复验 bundle API：仅从 ArtifactStore + candidate id 出发，校验候选自身
     内容哈希、产品键集、产品正文哈希、source revision；缺失/篡改/错键集/
     错 revision 稳定失败关闭。
  ④ OI-PF-200 保留：调用方回算结果不是权威（E-G6A-05-005/006），写入前漂移
     失败关闭（E-G6A-05-007）；失败不写 candidate，已写产品可作孤儿但不得
     被当成完整 bundle。
  ⑤ 受管 JSON → app 服务 → 本地 CLI 是真实非测试调用链；CLI 从干净 Git
     checkout 取真实 commit/tree。入口只生成 candidate，不写发布三表。
  ⑥ 健康路径确定性：相同 ctx/run/revision 得相同 candidate id 与 bundle。
"""
import importlib.util
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
APP = os.path.join(os.path.dirname(__file__), "..", "app")
sys.path.insert(0, APP)

import json  # noqa: E402

from artifact_store import ArtifactStore  # noqa: E402
from assumption_snapshot import (  # noqa: E402
    APPROVED, AssumptionProposal, AssumptionRegistry, AssumptionSnapshot,
)
from candidate_service import (  # noqa: E402
    CandidateBundle, CandidateFreezeService, CandidateRequestError,
    CandidateVerificationError, SOURCE_REVISION_RE,
    freeze_final_candidate_from_payload,
)
from publish_engine import canonical_bytes  # noqa: E402
from recompute import (  # noqa: E402
    PRODUCT_ORDER, OpenItemsPolicy, RecomputeError, ResearchContext,
    _prod_sha, recompute_all,
)
from valuation_engine import ValuationInputs  # noqa: E402

_REV_A = "a" * 40
_TREE_A = "b" * 40
_REV_B = "a" * 39 + "b"   # 与 _REV_A 差一字节
_EXPECTED = {"expected_source_commit": _REV_A,
             "expected_source_tree": _TREE_A}


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


def _ctx(approve=None, **over):
    """确定性研究上下文（合成 fixture，与 test_g6a_05 同源）。"""
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
        reg.decide(props[key].proposal_id, APPROVED, "U", "2026-08-12T12:00:00Z",
                   "APPROVE")
    snap = AssumptionSnapshot("SNAP-G6A06").build(reg)
    return ResearchContext(
        contract={"contract_id": "C-600089", "scope": "600089.SH"},
        facts={"fcff": "400000000", "fcfe": "300000000", "eps": "0.60",
               "book_per_share": "5.00"},
        macro={"wacc_floor": "0.08"},
        formula_specs={"fcff": {"formula": "..."}},
        valuation_inputs=_mk_vi(),
        assumption_defaults={"growth": "0.05", "wacc": "0.10", "ke": "0.12",
                             "target_pe": "12", "roe": "0.12"},
        approved=snap,
        open_items_policy=_policy(),
    )


def _routes():
    """G6A-06 PARTIAL：四路估值声明，默认全 READY。"""
    return {r: {"state": "READY"} for r in ("fcff", "fcfe", "relative",
                                            "pe_roe_pb")}


def _request_payload(approve=None, *, routes=None, facts=None,
                     source_commit=_REV_A, source_tree=_TREE_A):
    """受管 JSON 入口的合成请求；批准正文只能由 proposal + decision 重建。"""
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
        for key in (approve or ())
    ]
    return {
        "schema_version": "1.1.0",
        "run_id": "same-run",
        "source_revision": {"source_commit": source_commit,
                            "source_tree": source_tree},
        "context": {
            "contract": {"contract_id": "C-600089", "scope": "600089.SH"},
            "facts": facts if facts is not None else {
                "fcff": "400000000", "fcfe": "300000000",
                "eps": "0.60", "book_per_share": "5.00"},
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
                "snapshot_id": "SNAP-G6A06", "version": 1,
                "proposals": proposals, "decisions": decisions,
            },
            "open_items_policy": {
                "tolerance": "0.15", "owner_role": "U",
                "due_date": "2026-08-31", "blocks_gate": "G3-06",
            },
            "valuation_routes": _routes() if routes is None else routes,
        },
    }


def _store_object_count(store) -> int:
    return sum(len(files) for _, _, files in os.walk(str(store.root)))


def _walk_objects(store):
    """对象库内全部对象（rel 路径 → bytes）。"""
    out = {}
    for dp, _, fns in os.walk(str(store.root)):
        for fn in fns:
            fp = os.path.join(dp, fn)
            rel = os.path.relpath(fp, str(store.root))
            if not re.fullmatch(r"[0-9a-f]{2}/[0-9a-f]{2}/[0-9a-f]{60}", rel):
                continue
            with open(fp, "rb") as f:
                out[rel.replace("/", "")] = f.read()
    return out


def _write_bytes_unlocked(store, digest, data):
    """读时哈希校验用：对象以 0o444 落库（不可变），篡改测试须先解锁再写。"""
    target = os.path.join(str(store.root), digest[:2], digest[2:4], digest[4:])
    os.chmod(target, 0o600)
    with open(target, "wb") as f:
        f.write(data)


def _load_candidate_tool():
    path = os.path.join(os.path.dirname(__file__), "..", "tools",
                        "final_candidate.py")
    spec = importlib.util.spec_from_file_location("final_candidate_tool_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _StoreBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self.store = ArtifactStore(os.path.join(self._tmp, "lib"))

    def tearDown(self):
        shutil.rmtree(self._tmp, ignore_errors=True)

    def freeze(self, ctx=None, run_id="same-run", rev=_REV_A, tree=_TREE_A,
               recompute=None):
        service = CandidateFreezeService(self.store)
        ctx = ctx or _ctx()
        recompute = recompute or recompute_all(ctx)
        payload = _request_payload(sorted(ctx.approved_keys()),
                                   source_commit=rev, source_tree=tree)
        return service.freeze_final_candidate(ctx, run_id, rev, tree,
                                              recompute,
                                              request_payload=payload)


class TestFinalCandidateFreeze(_StoreBase):
    def test_freezes_eleven_products_content_addressed_and_recomputable(self):
        """① 冻结写入 11 项产品正文；digest == RecomputeResult.shas；
        candidate.product_hashes 全部可 load 并重算一致。"""
        fr = self.freeze(ctx=_ctx(approve=["growth"]))
        cand = fr.candidate
        self.assertEqual(tuple(cand["products"]), tuple(sorted(PRODUCT_ORDER)),
                         "产品键集 = 排序后的生产注册表")
        self.assertEqual(set(cand["product_hashes"]), set(PRODUCT_ORDER))
        # 每条 product_hashes 都能 store.load 且重算 = 记录值
        for name in PRODUCT_ORDER:
            data = self.store.load(cand["product_hashes"][name])
            self.assertEqual(data, canonical_bytes(fr.recompute.products[name]),
                             f"{name} 正文落库字节须等于 canonical 规范字节")
            self.assertEqual(
                _prod_sha(json.loads(data.decode("utf-8"))),
                cand["product_hashes"][name], f"{name} 正文重算哈希须一致")
        # 候选自身可 load
        self.assertTrue(self.store.exists(fr.candidate_id))
        self.assertEqual(self.store.load(fr.candidate_id),
                         canonical_bytes(fr.candidate))

    def test_candidate_has_kind_and_source_revision(self):
        """② candidate 含 kind=candidate + 显式 source_commit/source_tree。"""
        fr = self.freeze()
        self.assertEqual(fr.candidate["kind"], "candidate")
        self.assertEqual(fr.candidate["source_commit"], _REV_A)
        self.assertEqual(fr.candidate["source_tree"], _TREE_A)
        self.assertRegex(fr.candidate["source_commit"], SOURCE_REVISION_RE)

    def test_invalid_revision_fails_closed_no_candidate_written(self):
        """② 非 40 位小写十六进制 revision 一律失败关闭，不写任何对象。"""
        bad = [("大写", "A" * 40), ("长度不足", "a" * 39),
               ("非十六进制", "z" * 40), ("空串", "")]
        for label, rev in bad:
            with self.subTest(revision=label):
                with self.assertRaises(RecomputeError) as cm:
                    self.freeze(rev=rev)
                self.assertIn("E-G6A-06-001", str(cm.exception),
                              f"{label} 必须失败关闭")
                self.assertEqual(_store_object_count(self.store), 0,
                                 f"{label} 不得写任何对象")
        for label, tree in bad:
            with self.subTest(tree=label):
                with self.assertRaises(RecomputeError):
                    self.freeze(tree=tree)
                self.assertEqual(_store_object_count(self.store), 0,
                                 f"{label} 不得写任何对象")

    def test_deterministic_same_inputs_same_bundle(self):
        """⑥ 相同 ctx/run/revision 重复冻结 → 相同 candidate id 与 bundle。"""
        c1 = self.freeze(ctx=_ctx(approve=["growth"]))
        c2 = self.freeze(ctx=_ctx(approve=["growth"]))
        self.assertEqual(c1.candidate_id, c2.candidate_id)
        self.assertEqual(c1.candidate, c2.candidate)
        self.assertEqual(self.store.load(c1.candidate_id),
                         self.store.load(c2.candidate_id))

    def test_revision_byte_change_changes_candidate_id(self):
        """⑥ revision 改一字节 → candidate id 改变；source 参与内容寻址身份。"""
        c1 = self.freeze(ctx=_ctx(approve=["growth"]), rev=_REV_A)
        c2 = self.freeze(ctx=_ctx(approve=["growth"]), rev=_REV_B)
        self.assertNotEqual(c1.candidate_id, c2.candidate_id,
                            "source commit 参与候选内容寻址身份")
        c3 = self.freeze(ctx=_ctx(approve=["growth"]), tree="c" * 40)
        self.assertNotEqual(c1.candidate_id, c3.candidate_id,
                            "source tree 参与候选内容寻址身份")

    def test_caller_recompute_not_authoritative_rejected(self):
        """④ 调用方回算结果不是权威：另一上下文的回算结果被拒 E-G6A-05-005，
        不写 candidate（按对象库计数证明）。"""
        r_bad = recompute_all(_ctx(approve=["growth"]))
        before = _store_object_count(self.store)
        with self.assertRaises(RecomputeError) as cm:
            self.freeze(ctx=_ctx(), recompute=r_bad)   # 绑定哈希不符
        self.assertIn("E-G6A-05-005", str(cm.exception))
        self.assertEqual(_store_object_count(self.store), before,
                         "拒绝不得写任何对象")

    def test_post_canonical_drift_fails_closed_products_orphans(self):
        """④ 写入边界漂移 E-G6A-05-007：canonical 返回后上下文漂移 → 失败
        关闭；不写 candidate（产品可作孤儿，但不得被当成完整 bundle）。"""
        import candidate_service as S
        real = S.recompute_all
        ctx = _ctx()

        def _drift_after_canonical(c):
            res = real(c)
            c.contract["drift"] = "post-canonical"
            return res

        r = recompute_all(ctx)
        S.recompute_all = _drift_after_canonical
        try:
            with self.assertRaises(RecomputeError) as cm:
                self.freeze(ctx=ctx, recompute=r)
            self.assertIn("E-G6A-05-007", str(cm.exception))
        finally:
            S.recompute_all = real
        # 失败不得写 candidate：对象库内不存在任何 kind=candidate 对象
        import json as _json
        cand_ids = []
        for oid, data in _walk_objects(self.store).items():
            try:
                obj = _json.loads(data.decode("utf-8"))
            except (UnicodeDecodeError, ValueError):
                continue
            if isinstance(obj, dict) and obj.get("kind") == "candidate":
                cand_ids.append(oid)
        self.assertEqual(cand_ids, [], "漂移失败不得落库任何 candidate 对象")
        # 孤儿产品可能存在（对象库里只剩产品正文），但不能复验成 bundle
        objs = _walk_objects(self.store)
        service = CandidateFreezeService(self.store)
        for oid, data in objs.items():
            with self.assertRaises(CandidateVerificationError):
                service.load_candidate_bundle(oid, **_EXPECTED)


class TestCandidateBundleVerify(_StoreBase):
    def test_bundle_load_and_verify_healthy(self):
        """③ 健康路径：从 ArtifactStore + candidate id 加载候选与全部产品正文，
        校验候选自身哈希、键集、产品哈希、source revision 全通过。"""
        fr = self.freeze(ctx=_ctx(approve=["growth"]))
        service = CandidateFreezeService(self.store)
        b = service.load_candidate_bundle(fr.candidate_id, **_EXPECTED)
        self.assertIsInstance(b, CandidateBundle)
        self.assertEqual(b.candidate_id, fr.candidate_id)
        self.assertEqual(set(b.products), set(PRODUCT_ORDER))
        self.assertEqual(b.candidate["kind"], "candidate")
        self.assertEqual(b.candidate["source_commit"], _REV_A)
        v = service.verify_candidate_bundle(fr.candidate_id, **_EXPECTED)
        self.assertEqual(v["product_count"], 11)
        self.assertEqual(v["products"], sorted(PRODUCT_ORDER))
        self.assertEqual(v["kind"], "candidate")
        self.assertEqual(v["source_commit"], _REV_A)

    def test_bundle_missing_product_fails_closed(self):
        """③ 产品正文缺失 → 稳定失败关闭（E-G6A-06-014）。"""
        fr = self.freeze(ctx=_ctx(approve=["growth"]))
        victim = fr.candidate["product_hashes"]["calc_ledger"]
        target = os.path.join(str(self.store.root),
                              victim[:2], victim[2:4], victim[4:])
        os.remove(target)
        service = CandidateFreezeService(self.store)
        with self.assertRaises(CandidateVerificationError) as cm:
            service.load_candidate_bundle(fr.candidate_id, **_EXPECTED)
        self.assertIn("E-G6A-06-014", str(cm.exception))

    def test_bundle_tampered_product_fails_closed(self):
        """③ 产品正文篡改 → store.load 哈希校验拒（E-G6A-06-014）。"""
        fr = self.freeze(ctx=_ctx(approve=["growth"]))
        victim = fr.candidate["product_hashes"]["calc_ledger"]
        _write_bytes_unlocked(self.store, victim,
                              b"tampered-not-the-real-body")
        service = CandidateFreezeService(self.store)
        with self.assertRaises(CandidateVerificationError) as cm:
            service.load_candidate_bundle(fr.candidate_id, **_EXPECTED)
        self.assertIn("E-G6A-06-014", str(cm.exception))

    def test_bundle_wrong_key_set_fails_closed(self):
        """③ 错键集：候选内产品键集 ≠ 生产注册表 → 稳定失败关闭。"""
        fr = self.freeze(ctx=_ctx(approve=["growth"]))
        cand = dict(fr.candidate)
        cand["products"] = [p for p in PRODUCT_ORDER if p != "claim_map"]
        service = CandidateFreezeService(self.store)
        with self.assertRaises(CandidateVerificationError) as cm:
            service._verify_dict(cand, **_EXPECTED)
        self.assertIn("E-G6A-06-013", str(cm.exception))

    def test_bundle_wrong_revision_fails_closed(self):
        """③ 错 revision：source_commit/source_tree 非严格 40 位十六进制 →
        稳定失败关闭。"""
        fr = self.freeze(ctx=_ctx(approve=["growth"]))
        cand = dict(fr.candidate)
        cand["source_commit"] = "UPPERCASE-IS-NOT-ALLOWED"
        service = CandidateFreezeService(self.store)
        with self.assertRaises(CandidateVerificationError) as cm:
            service._verify_dict(cand, **_EXPECTED)
        self.assertIn("E-G6A-06-012", str(cm.exception))

    def test_bundle_missing_kind_fails_closed(self):
        """③ 临时测试 candidate 不得冒充最终候选：kind != candidate → 拒。"""
        fr = self.freeze(ctx=_ctx(approve=["growth"]))
        cand = dict(fr.candidate)
        cand["kind"] = "candidate_invalidation"
        service = CandidateFreezeService(self.store)
        with self.assertRaises(CandidateVerificationError) as cm:
            service._verify_dict(cand, **_EXPECTED)
        self.assertIn("E-G6A-06-011", str(cm.exception))

    def test_bundle_tampered_candidate_hash_fails_closed(self):
        """③ candidate 自身被篡改：store.load 读时哈希校验拒（E-G6A-06-016）。"""
        fr = self.freeze(ctx=_ctx(approve=["growth"]))
        _write_bytes_unlocked(self.store, fr.candidate_id,
                              b"tampered-candidate-bytes")
        service = CandidateFreezeService(self.store)
        with self.assertRaises(CandidateVerificationError) as cm:
            service.load_candidate_bundle(fr.candidate_id, **_EXPECTED)
        self.assertIn("E-G6A-06-016", str(cm.exception))

    def test_orphan_products_not_a_complete_bundle(self):
        """④ 孤儿产品不构成完整 bundle：单独落库产品无法作为 bundle 复验。"""
        ctx = _ctx(approve=["growth"])
        canonical = recompute_all(ctx)
        service = CandidateFreezeService(self.store)
        for name in PRODUCT_ORDER:
            self.store.store("recompute_product",
                             __import__("json").dumps(
                                 canonical.products[name], sort_keys=True,
                                 ensure_ascii=False,
                                 separators=(",", ":")).encode("utf-8"))
        # 无 candidate 对象 → 无法从 store 复验为 bundle
        for oid in _walk_objects(self.store):
            with self.assertRaises(CandidateVerificationError):
                service.load_candidate_bundle(oid, **_EXPECTED)


class TestManagedProductionEntry(unittest.TestCase):
    """⑤ 受管输入与真实 CLI 调用链；冻结不写发布三表。"""

    def test_payload_entry_from_empty_store_no_publish_rows(self):
        from repository import (Approval, CurrentPointer, Release,
                                create_repository)
        with tempfile.TemporaryDirectory() as tmp:
            repo = create_repository(os.path.join(tmp, "state.sqlite3"))
            repo.create_all()
            session = repo.session()
            store = ArtifactStore(os.path.join(tmp, "objects"))
            try:
                result = freeze_final_candidate_from_payload(
                    store, _request_payload(["growth"]),
                    source_commit=_REV_A, source_tree=_TREE_A)
                verified = CandidateFreezeService(store).verify_candidate_bundle(
                    result.candidate_id, **_EXPECTED)
                self.assertEqual(verified["product_count"], 11)
                self.assertEqual(session.query(Approval).count(), 0)
                self.assertEqual(session.query(Release).count(), 0)
                self.assertEqual(session.query(CurrentPointer).count(), 0)
            finally:
                session.close()
                repo.engine.dispose()

    def test_payload_rejects_direct_approved_body_and_key_collision(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ArtifactStore(os.path.join(tmp, "objects"))
            direct = _request_payload()
            direct["context"]["approved_snapshot"]["approved"] = {
                "A-FAKE": {"growth": "0.99"}}
            with self.assertRaises(CandidateRequestError) as cm:
                freeze_final_candidate_from_payload(
                    store, direct, source_commit=_REV_A, source_tree=_TREE_A)
            self.assertIn("E-G6A-06-020", str(cm.exception))
            self.assertEqual(_store_object_count(store), 0)

            conflict = _request_payload(["growth"])
            snap = conflict["context"]["approved_snapshot"]
            snap["proposals"].append(
                {"proposal_id": "A-GROWTH-2", "payload": {"growth": "0.20"},
                 "proposed_by": "L8"})
            snap["decisions"].append(
                {"proposal_id": "A-GROWTH-2", "decision": "APPROVED",
                 "approver": "U", "decided_at": "2026-08-12T12:00:01Z",
                 "token": "APPROVE"})
            with self.assertRaises(CandidateRequestError) as cm2:
                freeze_final_candidate_from_payload(
                    store, conflict, source_commit=_REV_A, source_tree=_TREE_A)
            self.assertIn("E-G3-13-012", str(cm2.exception))
            self.assertEqual(_store_object_count(store), 0)

    def test_wrong_expected_code_version_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ArtifactStore(os.path.join(tmp, "objects"))
            result = freeze_final_candidate_from_payload(
                store, _request_payload(["growth"]),
                source_commit=_REV_A, source_tree=_TREE_A)
            with self.assertRaises(CandidateVerificationError) as cm:
                CandidateFreezeService(store).verify_candidate_bundle(
                    result.candidate_id, expected_source_commit=_REV_B,
                    expected_source_tree=_TREE_A)
            self.assertIn("E-G6A-06-017", str(cm.exception))

    def test_request_source_revision_mismatch_zero_write(self):
        """请求声明的 source revision ≠ 显式干净 checkout → E-G6A-06-002
        失败关闭，按对象库计数证明零对象写入。"""
        with tempfile.TemporaryDirectory() as tmp:
            store = ArtifactStore(os.path.join(tmp, "objects"))
            payload = _request_payload(["growth"], source_commit=_REV_B)
            with self.assertRaises(RecomputeError) as cm:
                freeze_final_candidate_from_payload(
                    store, payload, source_commit=_REV_A, source_tree=_TREE_A)
            self.assertIn("E-G6A-06-002", str(cm.exception))
            self.assertEqual(_store_object_count(store), 0,
                             "revision 不符不得写任何对象")

    def test_request_source_revision_tree_mismatch_zero_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ArtifactStore(os.path.join(tmp, "objects"))
            payload = _request_payload(["growth"], source_tree="c" * 40)
            with self.assertRaises(RecomputeError) as cm:
                freeze_final_candidate_from_payload(
                    store, payload, source_commit=_REV_A, source_tree=_TREE_A)
            self.assertIn("E-G6A-06-002", str(cm.exception))
            self.assertEqual(_store_object_count(store), 0)

    def test_request_source_revision_invalid_zero_write(self):
        """请求 source_revision 非严格 40 位小写十六进制 → E-G6A-06-020
        失败关闭，零写入。"""
        bad = [("大写", "A" * 40), ("长度不足", "a" * 39),
               ("非十六进制", "z" * 40), ("缺字段", None)]
        for label, rev in bad:
            with self.subTest(revision=label):
                with tempfile.TemporaryDirectory() as tmp:
                    store = ArtifactStore(os.path.join(tmp, "objects"))
                    payload = _request_payload(["growth"])
                    if rev is None:
                        del payload["source_revision"]["source_commit"]
                    else:
                        payload["source_revision"]["source_commit"] = rev
                    with self.assertRaises(CandidateRequestError) as cm:
                        freeze_final_candidate_from_payload(
                            store, payload,
                            source_commit=_REV_A, source_tree=_TREE_A)
                    self.assertIn("E-G6A-06-020", str(cm.exception),
                                  f"{label} 必须失败关闭")
                    self.assertEqual(_store_object_count(store), 0,
                                     f"{label} 不得写任何对象")

    def test_cli_freeze_and_verify_call_app_service(self):
        tool = _load_candidate_tool()
        with tempfile.TemporaryDirectory() as tmp:
            repo_root = os.path.join(tmp, "repo")
            subprocess.run(["git", "init", "-q", repo_root], check=True)
            tracked = os.path.join(repo_root, "tracked.txt")
            with open(tracked, "w", encoding="utf-8") as fh:
                fh.write("clean\n")
            subprocess.run(["git", "-C", repo_root, "add", "tracked.txt"],
                           check=True)
            subprocess.run(
                ["git", "-C", repo_root, "-c", "user.name=Fixture",
                 "-c", "user.email=fixture@example.invalid", "commit", "-qm",
                 "fixture"], check=True)
            original_root = tool.ROOT
            try:
                tool.ROOT = repo_root
                commit, tree = tool.source_revision()
                self.assertRegex(commit, r"^[0-9a-f]{40}$")
                self.assertRegex(tree, r"^[0-9a-f]{40}$")
                with open(tracked, "a", encoding="utf-8") as fh:
                    fh.write("dirty\n")
                with self.assertRaises(CandidateRequestError) as cm:
                    tool.source_revision()
                self.assertIn("E-G6A-06-021", str(cm.exception))
            finally:
                tool.ROOT = original_root

            request_path = os.path.join(tmp, "request.json")
            store_path = os.path.join(tmp, "objects")
            with open(request_path, "w", encoding="utf-8") as fh:
                json.dump(_request_payload(["growth"]), fh)
            original = tool.source_revision
            try:
                tool.source_revision = lambda: (_REV_A, _TREE_A)
                frozen = tool.freeze(request_path, store_path)
                self.assertRegex(frozen["candidate_id"], r"^[0-9a-f]{64}$")
                self.assertEqual(frozen["product_count"], 11)
                verified = tool.verify(frozen["candidate_id"], store_path)
                self.assertEqual(verified["source_commit"], _REV_A)
                tool.source_revision = lambda: (_REV_B, _TREE_A)
                with self.assertRaises(CandidateVerificationError):
                    tool.verify(frozen["candidate_id"], store_path)
            finally:
                tool.source_revision = original


if __name__ == "__main__":
    unittest.main()
