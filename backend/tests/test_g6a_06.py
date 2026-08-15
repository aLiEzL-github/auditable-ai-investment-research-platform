"""G6A-06 验收测试：candidate 失效的权威查询面与发布链阻断（OI-PF-204）。

基线（OI-PF-204）：
  · 失效事实须同时保留不可变审计证据（内容寻址）并进入权威查询面
    （candidate_invalidation 表，按 old_candidate_id 唯一可查）
  · 写失效前必须 store.load() 并验证 old/new 两端都是完整 candidate 对象
    （JSON object、kind="candidate"、内容摘要匹配）；缺失、内容损坏、
    其他 kind、new 不存在均稳定失败关闭
  · 重复相同失效应幂等；冲突 new/reason 必须拒绝，不得静默覆盖
  · create_approval 写 Approval 前拒绝已失效 subject root；
    is_release_eligible 自行重核；publish_release 只经唯一谓词且拒绝已失效
    candidate —— 不新增 Approval/Release/CurrentPointer；失效前已有 Approval
    保留审计但不可准出
"""
import hashlib
import json
import os
import shutil
import sys
import tempfile
import unittest

APP = os.path.join(os.path.dirname(__file__), "..", "app")
sys.path.insert(0, APP)
sys.path.insert(0, os.path.dirname(__file__))

from artifact_store import ArtifactStore  # noqa: E402
import _g4_fixtures as fx  # noqa: E402
from assumption_snapshot import (  # noqa: E402
    APPROVED, AssumptionProposal, AssumptionRegistry, AssumptionSnapshot,
)
from publish_engine import (  # noqa: E402
    RESEARCH_600089_KEY, canonical_bytes, create_approval, current_release,
    invalidated_candidate, is_release_eligible, publish_release,
)
from recompute import (  # noqa: E402
    OpenItemsPolicy, RecomputeError, ResearchContext,
    freeze_candidate_from_recompute, invalidate_previous, recompute_all,
)
from repository import (  # noqa: E402
    CandidateInvalidation, CurrentPointer, Release, create_repository,
)
from schema_validate import validate_object  # noqa: E402
from valuation_engine import ValuationInputs  # noqa: E402


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


def _ctx(approve=None):
    """确定性研究上下文（与 test_g6a_05 同形态的冻结输入 fixture）。"""
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


def _store_object_count(store) -> int:
    return sum(len(files) for _, _, files in os.walk(str(store.root)))


class TestInvalidationFailClosed(unittest.TestCase):
    """写失效前两端 candidate 校验：缺失/内容损坏/其他 kind/缺 new 均失败关闭，
    且对象库与权威查询面零写入。"""

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self.store = ArtifactStore(os.path.join(self._tmp, "lib"))
        self.repo = create_repository(os.path.join(self._tmp, "inv.sqlite3"))
        self.repo.create_all()
        self.s = self.repo.session()

    def tearDown(self):
        self.s.close()
        self.repo.engine.dispose()
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _cand(self, payload=None):
        return fx.build_candidate(self.store, payload)

    def _invalidate(self, old, new, reason="superseded"):
        return invalidate_previous(self.store, self.repo, old, new, reason,
                                   writer="L7_freeze")

    def _corrupt(self, digest):
        rel = f"{digest[:2]}/{digest[2:4]}/{digest[4:]}"
        fp = os.path.join(self.store.root, rel)
        os.chmod(fp, 0o644)
        with open(fp, "wb") as f:
            f.write(b"corrupted-" + digest.encode())

    def _assert_rejected_no_write(self, old, new, reason="superseded"):
        before = _store_object_count(self.store)
        with self.assertRaises(RecomputeError) as cm:
            self._invalidate(old, new, reason)
        self.assertIn("E-G6A-05-002", str(cm.exception))
        self.assertEqual(self.s.query(CandidateInvalidation).count(), 0,
                         "失败关闭不得写权威查询面")
        self.assertEqual(_store_object_count(self.store), before,
                         "失败关闭不得写内容寻址审计证据")

    def test_missing_old_rejected(self):
        """旧 candidate 不存在（原实现只查路径存在即放行）→ 失败关闭。"""
        new = self._cand({"ticker": "NEW"})
        self._assert_rejected_no_write("0" * 64, new)

    def test_corrupt_old_rejected(self):
        """旧对象内容损坏（内容哈希 ≠ 摘要）→ store.load 兜底拒绝。"""
        old = self._cand({"ticker": "OLD"})
        new = self._cand({"ticker": "NEW"})
        self._corrupt(old)
        self._assert_rejected_no_write(old, new)

    def test_non_candidate_old_rejected(self):
        """旧对象是合法 JSON 但 body.kind ≠ candidate（含无 kind）→ 失败关闭。"""
        new = self._cand({"ticker": "NEW"})
        ev = fx.freeze_object(self.store, "evidence",
                              {"schema_version": "1.0.0", "kind": "evidence",
                               "metric": "x"})
        self._assert_rejected_no_write(ev, new)
        no_kind = self.store.store("candidate", canonical_bytes(
            {"schema_version": "1.0.0", "not_a_candidate": True}))
        self._assert_rejected_no_write(no_kind, new)

    def test_not_json_old_rejected(self):
        """旧对象是内容寻址正确的 b"not-json" → 失败关闭（与 G4-07 同法）。"""
        new = self._cand({"ticker": "NEW"})
        bad = self.store.store("candidate", b"not-json")
        self._assert_rejected_no_write(bad, new)

    def test_missing_new_rejected(self):
        """新 candidate 不存在（原实现从不校验）→ 失败关闭。"""
        old = self._cand({"ticker": "OLD"})
        self._assert_rejected_no_write(old, "1" * 64)

    def test_corrupt_new_rejected(self):
        """新对象内容损坏 → 失败关闭。"""
        old = self._cand({"ticker": "OLD"})
        new = self._cand({"ticker": "NEW"})
        self._corrupt(new)
        self._assert_rejected_no_write(old, new)

    def test_non_candidate_new_rejected(self):
        """新对象非 candidate（其他 kind / 无 kind）→ 失败关闭。"""
        old = self._cand({"ticker": "OLD"})
        ev = fx.freeze_object(self.store, "macro",
                              {"schema_version": "1.0.0", "kind": "macro",
                               "indicator": "GDP"})
        self._assert_rejected_no_write(old, ev)

    def test_empty_reason_rejected(self):
        """reason 缺失/空白 → 拒绝（不得以空理由静默落失效事实）。"""
        old = self._cand({"ticker": "OLD"})
        new = self._cand({"ticker": "NEW"})
        before = _store_object_count(self.store)
        with self.assertRaises(RecomputeError) as cm:
            self._invalidate(old, new, reason="   ")
        self.assertIn("E-G6A-05-008", str(cm.exception))
        self.assertEqual(self.s.query(CandidateInvalidation).count(), 0)
        self.assertEqual(_store_object_count(self.store), before)

    def test_self_invalidation_rejected(self):
        """old/new 相同不能表示后继候选，拒绝且不写失效事实。"""
        candidate = self._cand({"ticker": "SAME"})
        before = _store_object_count(self.store)
        with self.assertRaises(RecomputeError) as cm:
            self._invalidate(candidate, candidate, reason="self")
        self.assertIn("E-G6A-05-008", str(cm.exception))
        self.assertEqual(self.s.query(CandidateInvalidation).count(), 0)
        self.assertEqual(_store_object_count(self.store), before)


class TestInvalidationAuthoritative(unittest.TestCase):
    """成功失效：审计证据与权威查询面同时落地；幂等与冲突。"""

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self.store = ArtifactStore(os.path.join(self._tmp, "lib"))
        self.repo = create_repository(os.path.join(self._tmp, "inv.sqlite3"))
        self.repo.create_all()
        self.s = self.repo.session()

    def tearDown(self):
        self.s.close()
        self.repo.engine.dispose()
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _cand(self, payload=None):
        return fx.build_candidate(self.store, payload)

    def _invalidate(self, old, new, reason="superseded"):
        return invalidate_previous(self.store, self.repo, old, new, reason,
                                   writer="L7_freeze")

    def test_success_writes_audit_evidence_and_authoritative_row(self):
        """失效事实双落地：内容寻址审计证据（不可变）+ 按 old id 权威可查。"""
        old = self._cand({"ticker": "OLD"})
        new = self._cand({"ticker": "NEW"})
        inv_id = self._invalidate(old, new, reason="superseded")
        self.assertRegex(inv_id, r"^[0-9a-f]{64}$")
        row = self.s.query(CandidateInvalidation).filter_by(
            old_candidate_id=old).first()
        self.assertIsNotNone(row, "权威查询面须可按 old candidate id 命中")
        self.assertEqual(row.id, inv_id)
        self.assertEqual(row.new_candidate_id, new)
        self.assertEqual(row.reason, "superseded")
        self.assertEqual(row.status, "INVALIDATED")
        validate_object("candidate_invalidation", {
            "id": row.id, "schema_version": row.schema_version,
            "old_candidate_id": row.old_candidate_id,
            "new_candidate_id": row.new_candidate_id, "reason": row.reason,
            "status": row.status,
            "invalidated_at": row.invalidated_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "version": row.version,
        })
        data = self.store.load(inv_id)      # 不可变审计证据可读（读时哈希校验）
        self.assertEqual(hashlib.sha256(data).hexdigest(), inv_id)
        rec = json.loads(data.decode("utf-8"))
        self.assertEqual(rec["old_candidate_id"], old)
        self.assertEqual(rec["new_candidate_id"], new)
        self.assertEqual(rec["reason"], "superseded")
        # 权威查询面：旧命中、新（后继）未命中
        why = invalidated_candidate(self.s, old)
        self.assertIn("E-G6A-05-002", why)
        self.assertIn(new[:12], why)
        self.assertIsNone(invalidated_candidate(self.s, new))

    def test_repeat_same_idempotent(self):
        """重复相同失效幂等：返回同一证据 id，不新增行/不新增审计对象。"""
        old = self._cand({"ticker": "OLD"})
        new = self._cand({"ticker": "NEW"})
        inv1 = self._invalidate(old, new, reason="r")
        after_first = _store_object_count(self.store)
        inv2 = self._invalidate(old, new, reason="r")
        self.assertEqual(inv1, inv2, "重复相同失效须幂等返回既有证据")
        self.assertEqual(self.s.query(CandidateInvalidation).count(), 1)
        self.assertEqual(_store_object_count(self.store), after_first,
                         "幂等不得新增审计证据对象")

    def test_conflict_new_rejected(self):
        """同一 old 再失效到不同 new → 拒绝，不得静默覆盖。"""
        old = self._cand({"ticker": "OLD"})
        new1 = self._cand({"ticker": "NEW-1"})
        new2 = self._cand({"ticker": "NEW-2"})
        inv1 = self._invalidate(old, new1, reason="r")
        with self.assertRaises(RecomputeError) as cm:
            self._invalidate(old, new2, reason="r")
        self.assertIn("E-G6A-05-008", str(cm.exception))
        row = self.s.query(CandidateInvalidation).filter_by(
            old_candidate_id=old).first()
        self.assertEqual(row.id, inv1)
        self.assertEqual(row.new_candidate_id, new1,
                         "冲突请求不得静默覆盖既有失效事实")

    def test_conflict_reason_rejected(self):
        """同一 old→new 但 reason 不同 → 拒绝，不得静默覆盖。"""
        old = self._cand({"ticker": "OLD"})
        new = self._cand({"ticker": "NEW"})
        inv1 = self._invalidate(old, new, reason="first")
        with self.assertRaises(RecomputeError) as cm:
            self._invalidate(old, new, reason="second")
        self.assertIn("E-G6A-05-008", str(cm.exception))
        row = self.s.query(CandidateInvalidation).filter_by(
            old_candidate_id=old).first()
        self.assertEqual(row.reason, "first", "冲突 reason 不得静默覆盖")

    def test_writer_mandatory(self):
        """writer 必填关键字参数：缺失即 TypeError（OI-PF-184 无合法缺省）。"""
        old = self._cand({"ticker": "OLD"})
        new = self._cand({"ticker": "NEW"})
        with self.assertRaises(TypeError):
            invalidate_previous(self.store, self.repo, old, new, reason="r")

    def test_recompute_candidate_carries_kind_and_blocks_approval(self):
        """recompute candidate 带 G4 candidate schema 的 kind="candidate"；
        失效后批准被拒（零 Approval 残留）。"""
        ctx = _ctx()
        c1 = freeze_candidate_from_recompute(self.store, ctx, "run-1",
                                             recompute_all(ctx))
        self.assertEqual(c1.candidate["kind"], "candidate",
                         "recompute candidate 须与 G4 candidate schema 一致")
        ctx2 = _ctx(approve=["growth"])
        c2 = freeze_candidate_from_recompute(self.store, ctx2, "run-1",
                                             recompute_all(ctx2))
        self.assertNotEqual(c1.candidate_id, c2.candidate_id)
        inv = self._invalidate(c1.candidate_id, c2.candidate_id,
                               reason="growth 批准后全量回算")
        self.assertRegex(inv, r"^[0-9a-f]{64}$")
        self.assertTrue(self.store.exists(c1.candidate_id), "旧候选保留")
        m_old = fx.manifest_of(self.store, RESEARCH_600089_KEY,
                               root=c1.candidate_id,
                               objects={c1.candidate_id: {"kind": "candidate",
                                                          "refs": []}})
        with self.assertRaises(ValueError) as cm:
            create_approval(self.store, self.s, m_old, "U-fixture",
                            RESEARCH_600089_KEY,
                            candidate_digest=c1.candidate_id,
                            approved_at="2026-08-11T07:00:00Z",
                            acknowledged=True)
        self.assertIn("E-G6A-05-002", str(cm.exception))
        self.assertEqual(self.s.query(Release).count(), 0)
        from repository import Approval
        self.assertEqual(self.s.query(Approval).count(), 0,
                         "失效 candidate 不得获得批准（零残留）")


class TestReleaseChainBlocked(unittest.TestCase):
    """端到端：有效 candidate 先建立失效事实 → 批准/准出/发布三层全拒，
    不新增 Approval/Release/CurrentPointer；失效前已有 Approval 保留但不可
    准出；未失效新 candidate 正向通过。"""

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

    def _approve(self, m, key=RESEARCH_600089_KEY, **kw):
        return create_approval(self.store, self.s, m, "U-fixture", key,
                               approved_at="2026-08-11T07:00:00Z",
                               acknowledged=True, **kw)

    def _invalidate(self, old, new, reason="superseded"):
        return invalidate_previous(self.store, self.repo, old, new, reason,
                                   writer="L7_freeze")

    def test_invalidated_candidate_blocked_three_layers(self):
        """端到端负测：有效 candidate 先建立失效事实，再走批准/准出/发布，
        三层均失败；不新增 Approval/Release/CurrentPointer。"""
        m = fx.minimal_closure(self.store, RESEARCH_600089_KEY)
        cand = m["subject_root"]
        # 层 0：失效事实建立前批准可建（对照）；随后建立失效事实
        appr = self._approve(m)
        self.assertEqual(self.s.query(Release).count(), 0, "对照批准不写 release")
        self._invalidate(cand, fx.build_candidate(self.store, {"ticker": "NEW"}),
                         reason="OI-PF-204 失效事实")
        # 层 1：已失效 subject root 再建批准 → 拒，不新增 Approval 行
        with self.assertRaises(ValueError) as cm:
            self._approve(m)
        self.assertIn("E-G6A-05-002", str(cm.exception))
        self.assertEqual(self.s.query(Release).count(), 0)
        self.assertEqual(self.s.query(CurrentPointer).count(), 0)
        # 层 2：唯一准出谓词自行重核 → 拒
        ok, why = is_release_eligible(self.s, self.store, appr, m,
                                      RESEARCH_600089_KEY)
        self.assertFalse(ok, "谓词必须自行重核失效事实")
        self.assertIn("E-G6A-05-002", why)
        # 层 3：发布只经唯一谓词 → 拒
        with self.assertRaises(ValueError) as cm2:
            publish_release(self.store, self.s, m, RESEARCH_600089_KEY, appr,
                            released_at="2026-08-11T07:01:00Z",
                            writer="L11_release")
        self.assertIn("E-G6A-05-002", str(cm2.exception))
        self.assertEqual(self.s.query(Release).count(), 0, "无 release 残留")
        self.assertEqual(self.s.query(CurrentPointer).count(), 0,
                         "无 current 指针残留")
        self.assertIsNone(current_release(self.s, RESEARCH_600089_KEY))
        from repository import Approval
        self.assertEqual(self.s.query(Approval).count(), 1,
                         "只保留失效事实建立前那一条对照批准")

    def test_create_approval_rejects_invalidated_candidate_digest(self):
        """写 Approval 前拒绝已失效 candidate（经 candidate_digest 绑定）零残留。"""
        m = fx.minimal_closure(self.store, RESEARCH_600089_KEY)
        cand = m["subject_root"]
        self._invalidate(cand, fx.build_candidate(self.store, {"ticker": "NEW"}))
        before = self.s.query(Release).count() + self.s.query(CurrentPointer).count()
        from repository import Approval
        before_a = self.s.query(Approval).count()
        with self.assertRaises(ValueError) as cm:
            create_approval(self.store, self.s, m, "U-fixture",
                            RESEARCH_600089_KEY, candidate_digest=cand,
                            approved_at="2026-08-11T07:00:00Z",
                            acknowledged=True)
        self.assertIn("E-G6A-05-002", str(cm.exception))
        self.assertEqual(self.s.query(Approval).count(), before_a,
                         "拒绝后不得新增 Approval 行")
        self.assertEqual(
            self.s.query(Release).count() + self.s.query(CurrentPointer).count(),
            before, "拒绝后无 release/current 残留")

    def test_invalidation_after_first_predicate_check_blocks_final_write(self):
        """TOCTOU 变异：首次准出检查后、最终事务前插入失效事实，发布仍须
        在锁内重核并拒绝，Release/CurrentPointer 零残留。"""
        import publish_engine as engine
        m = fx.minimal_closure(self.store, RESEARCH_600089_KEY,
                               candidate_payload={"ticker": "RACE-OLD"})
        old = m["subject_root"]
        new = fx.build_candidate(self.store, {"ticker": "RACE-NEW"})
        appr = self._approve(m)
        original = engine._lock_candidate_invalidation_for_publish

        def _invalidate_between_checks(session, candidate_ids):
            self._invalidate(old, new, reason="TOCTOU regression")
            return original(session, candidate_ids)

        engine._lock_candidate_invalidation_for_publish = _invalidate_between_checks
        try:
            with self.assertRaises(ValueError) as cm:
                engine.publish_release(
                    self.store, self.s, m, RESEARCH_600089_KEY, appr,
                    released_at="2026-08-11T07:01:00Z", writer="L11_release")
            self.assertIn("E-G6A-05-002", str(cm.exception))
        finally:
            engine._lock_candidate_invalidation_for_publish = original
        self.assertEqual(self.s.query(Release).count(), 0)
        self.assertEqual(self.s.query(CurrentPointer).count(), 0)
        self.assertIsNone(current_release(self.s, RESEARCH_600089_KEY))

    def test_non_invalidated_candidate_passes_all_three(self):
        """正确未失效新 candidate 正向通过：批准 → 准出 → 发布，写 current。"""
        m = fx.minimal_closure(self.store, RESEARCH_600089_KEY,
                               candidate_payload={"ticker": "PASS"})
        cand = m["subject_root"]
        self.assertIsNone(invalidated_candidate(self.s, cand),
                          "未失效候选权威查询应无命中")
        appr = self._approve(m)
        ok, why = is_release_eligible(self.s, self.store, appr, m,
                                      RESEARCH_600089_KEY)
        self.assertTrue(ok, why)
        rel = publish_release(self.store, self.s, m, RESEARCH_600089_KEY, appr,
                              released_at="2026-08-11T07:01:00Z",
                              writer="L11_release")
        cur = current_release(self.s, RESEARCH_600089_KEY)
        self.assertEqual(cur["release_id"], rel.id,
                         "未失效 candidate 正常发布并写 current")


if __name__ == "__main__":
    unittest.main()
