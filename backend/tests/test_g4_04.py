"""G4-04 验收测试：唯一准出谓词与哈希绑定人工批准。

基线 B §7 G4-04：
  · subject root 排除批准事件本身，final manifest 再纳入批准事件
  · 批准必须绑定完整 CurrentKey
  · 人工风险接受不能绕门；聊天“继续”不算批准
  · 任一输入变化批准失效
"""
import os
import shutil
import sys
import tempfile
import unittest

APP = os.path.join(os.path.dirname(__file__), "..", "app")
sys.path.insert(0, APP)
sys.path.insert(0, os.path.dirname(__file__))

from artifact_store import ArtifactStore
import _g4_fixtures as fx
from publish_engine import (RESEARCH_600089_KEY, SYS_DESIGN_KEY,
                            approval_subject_root, create_approval,
                            inputs_hash, is_release_eligible,
                            publish_release)


class TestApprovalFlow(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self.store = ArtifactStore(os.path.join(self._tmp, "lib"))
        from repository import create_repository
        self.repo = create_repository(os.path.join(self._tmp, "ap.sqlite3"))
        self.repo.create_all()
        self.s = self.repo.session()

    def tearDown(self):
        self.s.close()
        self.repo.engine.dispose()
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _m(self):
        return fx.minimal_closure(self.store, RESEARCH_600089_KEY)

    # ── subject root 排除批准事件本身 ──────────────────────────────
    def test_subject_root_excludes_approval_event(self):
        m = self._m()
        root_before = approval_subject_root(self.store, m)
        appr = create_approval(self.store, self.s, m, "U-fixture",
                               RESEARCH_600089_KEY,
                               approved_at="2026-08-11T07:00:00Z")
        # final manifest 纳入批准事件：批准对象入闭包后根哈希变化
        appr_obj = {"schema_version": "1.0.0", "kind": "approval",
                    "id": appr.id, "approver": "U-fixture",
                    "subject_root_hash": appr.subject_root_hash}
        appr_digest = fx.freeze_object(self.store, "approval", appr_obj)
        m2 = fx.manifest_of(self.store, RESEARCH_600089_KEY,
                            root=m["subject_root"],
                            objects=dict(m["objects"],
                                         **{appr_digest: {"kind": "approval",
                                                          "refs": []}}),
                            parent=m.get("parent"))
        cand = m["subject_root"]
        m2["objects"][cand] = dict(m2["objects"][cand],
                                   refs=m2["objects"][cand]["refs"] + [appr_digest])
        m2["id"] = fx.content_id(m2)
        root_after = approval_subject_root(self.store, m2)
        self.assertEqual(root_before, appr.subject_root_hash,
                         "批准绑定 subject root（排除批准事件）")
        self.assertNotEqual(m2["id"], m["id"],
                            "final manifest 纳入批准事件 → 清单内容哈希须变化"
                            "（批准事件被绑定进最终清单）")

    # ── 批准绑定完整 CurrentKey ────────────────────────────────────
    def test_approval_binds_full_current_key(self):
        appr = create_approval(self.store, self.s, self._m(), "U-fixture",
                               RESEARCH_600089_KEY,
                               approved_at="2026-08-11T07:00:00Z")
        self.assertEqual(appr.workflow, RESEARCH_600089_KEY.workflow)
        self.assertEqual(appr.scope_id, RESEARCH_600089_KEY.scope_id)
        self.assertEqual(appr.current_key, RESEARCH_600089_KEY.current_key)

    # ── 聊天“继续”不算批准 ────────────────────────────────────────
    def test_chat_continue_not_approval(self):
        with self.assertRaises(ValueError) as cm:
            create_approval(self.store, self.s, self._m(), "U-fixture",
                            RESEARCH_600089_KEY, token="继续")
        self.assertIn("E-G4-04-002", str(cm.exception))
        with self.assertRaises(ValueError):
            create_approval(self.store, self.s, self._m(), "U-fixture",
                            RESEARCH_600089_KEY, token="go ahead")

    # ── 任一输入变化批准失效 ───────────────────────────────────────
    def test_input_change_invalidates_approval(self):
        m = self._m()
        appr = create_approval(self.store, self.s, m, "U-fixture",
                               RESEARCH_600089_KEY,
                               approved_at="2026-08-11T07:00:00Z")
        ok, _ = is_release_eligible(self.s, appr, m)
        self.assertTrue(ok)
        # 变异：输入变化（候选内容改动 → manifest 变）
        m2 = self._m()
        m2["code_version"] = "v1.1"          # 任一输入变化
        m2["id"] = fx.content_id(m2)
        ok2, why = is_release_eligible(self.s, appr, m2)
        self.assertFalse(ok2)
        self.assertIn("E-G4-04-004", why)

    # ── 人工风险接受不能绕门 ───────────────────────────────────────
    def test_manual_risk_acceptance_cannot_bypass(self):
        m = self._m()
        appr = create_approval(self.store, self.s, m, "U-fixture",
                               RESEARCH_600089_KEY,
                               approved_at="2026-08-11T07:00:00Z")
        # 批准后工件被破坏：manifest 未变（inputs_hash 一致），
        # 但审计门此时不 PASS —— 仅凭人工风险接受不得放行
        victim = next(oid for oid, meta in m["objects"].items()
                      if meta.get("kind") == "evidence")
        rel = f"{victim[:2]}/{victim[2:4]}/{victim[4:]}"
        fp = os.path.join(self.store.root, rel)
        try:
            os.remove(fp)
        except OSError:
            os.chmod(fp, 0o644)
            os.remove(fp)
        from publish_engine import audit_candidate
        audit = audit_candidate(self.store, m)
        self.assertFalse(audit.release_eligible)
        ok, why = is_release_eligible(self.s, appr, m, audit=audit)
        self.assertFalse(ok, "人工风险接受不能绕门（审计门未全 PASS）")
        self.assertIn("E-G4-04-005", why)
        with self.assertRaises(ValueError) as cm:
            publish_release(self.store, self.s, m, RESEARCH_600089_KEY, appr,
                            released_at="2026-08-11T07:01:00Z")
        self.assertIn("E-G4-03-004", str(cm.exception))

    # ── 未批准不得发布 ─────────────────────────────────────────────
    def test_unapproved_cannot_publish(self):
        from datetime import datetime
        from repository import Approval
        m = self._m()
        dummy = Approval(id="APR_DUMMY0001", schema_version="1.0.0",
                         object_ref=m["subject_root"], approver="x",
                         approved_at=datetime(2026, 8, 11),
                         subject_root_hash="0" * 64,
                         workflow=RESEARCH_600089_KEY.workflow,
                         scope_id=RESEARCH_600089_KEY.scope_id,
                         current_key="", inputs_hash="0" * 64,
                         status="INVALIDATED", token="APPROVE", version=1)
        self.s.add(dummy)
        self.s.commit()
        ok, why = is_release_eligible(self.s, dummy, m)
        self.assertFalse(ok)
        self.assertIn("E-G4-04-003", why)
        with self.assertRaises(Exception):
            publish_release(self.store, self.s, m, RESEARCH_600089_KEY, dummy,
                            released_at="2026-08-11T07:00:00Z")


if __name__ == "__main__":
    unittest.main()
