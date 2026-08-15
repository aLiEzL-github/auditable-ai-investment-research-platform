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
                            current_release, inputs_hash, is_release_eligible,
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
                               approved_at="2026-08-11T07:00:00Z",
                               acknowledged=True)
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
                               approved_at="2026-08-11T07:00:00Z",
                               acknowledged=True)
        self.assertEqual(appr.workflow, RESEARCH_600089_KEY.workflow)
        self.assertEqual(appr.scope_id, RESEARCH_600089_KEY.scope_id)
        self.assertEqual(appr.current_key, RESEARCH_600089_KEY.current_key)

    # ── 聊天“继续”不算批准 ────────────────────────────────────────
    def test_chat_continue_not_approval(self):
        with self.assertRaises(ValueError) as cm:
            create_approval(self.store, self.s, self._m(), "U-fixture",
                            RESEARCH_600089_KEY, token="继续",
                            acknowledged=True)
        self.assertIn("E-G4-04-002", str(cm.exception))
        with self.assertRaises(ValueError):
            create_approval(self.store, self.s, self._m(), "U-fixture",
                            RESEARCH_600089_KEY, token="go ahead",
                            acknowledged=True)

    # ── 任一输入变化批准失效 ───────────────────────────────────────
    def test_input_change_invalidates_approval(self):
        m = self._m()
        appr = create_approval(self.store, self.s, m, "U-fixture",
                               RESEARCH_600089_KEY,
                               approved_at="2026-08-11T07:00:00Z",
                               acknowledged=True)
        ok, _ = is_release_eligible(self.s, self.store, appr, m,
                                    RESEARCH_600089_KEY)
        self.assertTrue(ok)
        # 变异：输入变化（候选内容改动 → manifest 变）
        m2 = self._m()
        m2["code_version"] = "v1.1"          # 任一输入变化
        m2["id"] = fx.content_id(m2)
        ok2, why = is_release_eligible(self.s, self.store, appr, m2,
                                       RESEARCH_600089_KEY)
        self.assertFalse(ok2)
        self.assertIn("E-G4-04-004", why)

    # ── 人工风险接受不能绕门 ───────────────────────────────────────
    def test_manual_risk_acceptance_cannot_bypass(self):
        m = self._m()
        appr = create_approval(self.store, self.s, m, "U-fixture",
                               RESEARCH_600089_KEY,
                               approved_at="2026-08-11T07:00:00Z",
                               acknowledged=True)
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
        ok, why = is_release_eligible(self.s, self.store, appr, m,
                                      RESEARCH_600089_KEY, audit=audit)
        self.assertFalse(ok, "人工风险接受不能绕门（审计门未全 PASS）")
        self.assertIn("E-G4-04-005", why)
        with self.assertRaises(ValueError) as cm:
            publish_release(self.store, self.s, m, RESEARCH_600089_KEY, appr,
                            released_at="2026-08-11T07:01:00Z",
                            writer="L11_release")
        self.assertIn("E-G4-04-005", str(cm.exception))

    # ── B-2b (i)：写权矩阵机器强制（M5 负测）────────────────────────
    def test_write_gate_matrix_enforced(self):
        """发布/指针/批准写权须经 assert_writer —— 矩阵 never 名单必拒。

        变异注入：移除 assert_writer 调用 → 本用例 FAIL；
        arch_import_check 的 B-2c 断言（publish_engine 须含 assert_writer）亦 FAIL。
        """
        from schema_validate import SchemaError
        m = self._m()
        appr = create_approval(self.store, self.s, m, "U-fixture",
                               RESEARCH_600089_KEY,
                               approved_at="2026-08-11T07:00:00Z",
                               acknowledged=True)
        # LLM 永远不能写 release/current_pointer（writers.json never 名单）
        with self.assertRaises(SchemaError) as cm:
            publish_release(self.store, self.s, m, RESEARCH_600089_KEY, appr,
                            writer="LLM", released_at="2026-08-11T07:01:00Z")
        self.assertEqual(cm.exception.code, "E-WRITE-002")
        # MANUAL 前置（人工发起）未经确认 → 批准写不得放行
        with self.assertRaises(SchemaError) as cm2:
            create_approval(self.store, self.s, m, "U-fixture",
                            RESEARCH_600089_KEY,
                            approved_at="2026-08-11T07:00:00Z",
                            acknowledged=False)
        self.assertEqual(cm2.exception.code, "E-PRECOND-002")

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
        ok, why = is_release_eligible(self.s, self.store, dummy, m,
                                      RESEARCH_600089_KEY)
        self.assertFalse(ok)
        self.assertIn("E-G4-04-003", why)
        with self.assertRaises(Exception):
            publish_release(self.store, self.s, m, RESEARCH_600089_KEY, dummy,
                            released_at="2026-08-11T07:00:00Z",
                            writer="L11_release")

    # ── OI-PF-193：审计不可省略 / acknowledged 不可省略 ─────────────
    def test_is_release_eligible_pins_no_audit_bypass(self):
        """谓词须自行从 store+manifest+candidate_digest 重算审计。

        签名钉死：参数表须含 store（审计由谓词重算的载体）。行为钉死：
        OPEN+material 清单**不传 audit** 也必须拒绝 —— 不得 audit=None 放行。
        """
        import inspect
        sig = inspect.signature(is_release_eligible)
        self.assertIn("store", list(sig.parameters),
                      "谓词签名须含 store —— 审计须由谓词自行重算")
        self.assertIn("key", list(sig.parameters),
                      "谓词签名须含目标 key —— 使用时重新核对持久化批准（OI-PF-197）")
        self.assertIs(
            sig.parameters["key"].default, inspect.Parameter.empty,
            "目标 key 不可省略（不可用缺省值绕过跨域核对）")
        m = fx.minimal_closure(self.store, RESEARCH_600089_KEY,
                               open_item_status="OPEN", open_item_material=True)
        appr = create_approval(self.store, self.s, m, "U-fixture",
                               RESEARCH_600089_KEY,
                               approved_at="2026-08-11T07:00:00Z",
                               acknowledged=True)
        ok, why = is_release_eligible(self.s, self.store, appr, m,
                                      RESEARCH_600089_KEY)
        self.assertFalse(ok, "不传审计也必须拒绝（不得 audit=None -> 放行）")
        self.assertIn("E-G4-04-005", why)

    def test_acknowledged_required_and_boolean(self):
        """人工确认不可省略：缺失（TypeError）/False（E-PRECOND-002）拒，显式 True 过。"""
        from schema_validate import SchemaError
        m = self._m()
        with self.assertRaises(TypeError):
            create_approval(self.store, self.s, m, "U-fixture",
                            RESEARCH_600089_KEY,
                            approved_at="2026-08-11T07:00:00Z")
        with self.assertRaises(SchemaError) as cm:
            create_approval(self.store, self.s, m, "U-fixture",
                            RESEARCH_600089_KEY,
                            approved_at="2026-08-11T07:00:00Z",
                            acknowledged=False)
        self.assertEqual(cm.exception.code, "E-PRECOND-002")
        appr = create_approval(self.store, self.s, m, "U-fixture",
                               RESEARCH_600089_KEY,
                               approved_at="2026-08-11T07:00:00Z",
                               acknowledged=True)
        self.assertEqual(appr.status, "ACTIVE")

    # ── OI-PF-197：批准 key 必须与清单 CurrentKey 完整一致 ──────────
    def test_create_approval_wrong_key_rejected(self):
        """错 key 批准在**创建层**被拒（E-G4-04-006），且 Approval 计数不变。

        原失败载荷：研究 manifest 用 SYS_DESIGN_KEY 批准成功 → 谓词放行 →
        研究 release/current 被写入。现 key.workflow/scope_id/current_key 与
        manifest 任一不符即拒绝，且校验先于任何 DB 写入，不留行。
        """
        from repository import Approval
        m = self._m()                                   # 研究清单
        before = self.s.query(Approval).count()
        with self.assertRaises(ValueError) as cm:
            create_approval(self.store, self.s, m, "U-fixture", SYS_DESIGN_KEY,
                            approved_at="2026-08-11T07:00:00Z",
                            acknowledged=True)
        self.assertIn("E-G4-04-006", str(cm.exception))
        self.assertEqual(self.s.query(Approval).count(), before,
                         "错 key 批准不得留任何 Approval 行")
        # 反向：系统设计清单 + 研究 key 同样拒
        m_sys = fx.minimal_closure(self.store, SYS_DESIGN_KEY)
        with self.assertRaises(ValueError) as cm2:
            create_approval(self.store, self.s, m_sys, "U-fixture",
                            RESEARCH_600089_KEY,
                            approved_at="2026-08-11T07:00:00Z",
                            acknowledged=True)
        self.assertIn("E-G4-04-006", str(cm2.exception))
        self.assertEqual(self.s.query(Approval).count(), before)
        # 正确 key 正向通过
        appr = create_approval(self.store, self.s, m, "U-fixture",
                               RESEARCH_600089_KEY,
                               approved_at="2026-08-11T07:00:00Z",
                               acknowledged=True)
        self.assertEqual(self.s.query(Approval).count(), before + 1)
        self.assertEqual(appr.workflow, RESEARCH_600089_KEY.workflow)

    # ── OI-PF-197：持久化批准逐项篡改 → 谓词与发布均拒（表驱动）─────
    def test_persisted_approval_tamper_rejected(self):
        """直接构造/持久化后**篡改** Approval 行，谓词与发布仍拒。

        证明谓词在**使用时**重新核对持久化批准，不是只在创建层检查
        （E 要求：避免「只在入口校验」的空转）。workflow/scope_id/
        current_key/object_ref/subject_root_hash 任一被改 →
        is_release_eligible 与 publish_release 均拒（E-G4-04-006），
        且不留下任何 release/current 行。每轮全新 repo/store，隔离恢复。
        """
        import shutil as _sh
        from repository import Approval as _Approval
        from repository import CurrentPointer, Release, create_repository
        tamper = {"workflow": SYS_DESIGN_KEY.workflow,
                  "scope_id": "other-scope",
                  "current_key": "other-key",
                  "object_ref": "0" * 64,
                  "subject_root_hash": "0" * 64}
        for field in ("workflow", "scope_id", "current_key",
                      "object_ref", "subject_root_hash"):
            tmp = tempfile.mkdtemp()
            try:
                store = ArtifactStore(os.path.join(tmp, "lib"))
                repo = create_repository(os.path.join(tmp, "ap.sqlite3"))
                repo.create_all()
                s = repo.session()
                m = fx.minimal_closure(store, RESEARCH_600089_KEY)
                appr = create_approval(store, s, m, "U-fixture",
                                       RESEARCH_600089_KEY,
                                       approved_at="2026-08-11T07:00:00Z",
                                       acknowledged=True)
                row = s.get(_Approval, appr.id)
                setattr(row, field, tamper[field])
                s.commit()
                s.expire_all()
                with self.subTest(field=field):
                    ok, why = is_release_eligible(
                        s, store, appr, m, RESEARCH_600089_KEY)
                    self.assertFalse(ok, f"篡改 {field} 后谓词不得放行")
                    self.assertIn("E-G4-04-006", why)
                    with self.assertRaises(ValueError) as cm:
                        publish_release(store, s, m, RESEARCH_600089_KEY, appr,
                                        released_at="2026-08-11T07:01:00Z",
                                        writer="L11_release")
                    self.assertIn("E-G4-04-006", str(cm.exception))
                    self.assertIsNone(current_release(s, RESEARCH_600089_KEY),
                                      "篡改被拒后不得留下 current 指针")
                    self.assertEqual(s.query(Release).count(), 0)
                    self.assertEqual(s.query(CurrentPointer).count(), 0,
                                     "篡改被拒后无 DB 残留")
                s.close()
                repo.engine.dispose()
            finally:
                _sh.rmtree(tmp, ignore_errors=True)

    # ── OI-PF-197：谓词须直接核对目标 key 与清单 CurrentKey ─────────
    def test_target_key_must_match_manifest_current_key(self):
        """错域 manifest 不得被谓词放行 —— 目标 key 与清单 CurrentKey 直接一致。

        仅「批准与 key 一致」不够（发布侧才查跨域）：伪造一份 workflow/scope_id
        属系统设计域、但 object_ref/subject_root_hash 都真实绑定**研究清单**的
        批准，直接调用谓词传 SYS_DESIGN_KEY —— 修复前谓词报 True（跨域准出），
        现须 E-G4-04-006 失败关闭。
        """
        from datetime import datetime
        from repository import Approval
        m = self._m()                                   # 研究清单
        forged = Approval(
            id="APR_FORGED0002", schema_version="1.0.0",
            object_ref=m["subject_root"], approver="U-fixture",
            approved_at=datetime(2026, 8, 11),
            subject_root_hash=approval_subject_root(self.store, m),
            workflow=SYS_DESIGN_KEY.workflow,
            scope_id=SYS_DESIGN_KEY.scope_id,
            current_key=SYS_DESIGN_KEY.current_key,
            inputs_hash=inputs_hash(m), status="ACTIVE",
            token="APPROVE", version=1)
        self.s.add(forged)
        self.s.commit()
        ok, why = is_release_eligible(self.s, self.store, forged, m,
                                      SYS_DESIGN_KEY)
        self.assertFalse(ok, "错域 manifest 不得被谓词放行（跨域准出）")
        self.assertIn("E-G4-04-006", why)
        # 正确域（研究清单 + 研究 key）正向通过，防误红
        appr = create_approval(self.store, self.s, m, "U-fixture",
                               RESEARCH_600089_KEY,
                               approved_at="2026-08-11T07:00:00Z",
                               acknowledged=True)
        ok2, why2 = is_release_eligible(self.s, self.store, appr, m,
                                        RESEARCH_600089_KEY)
        self.assertTrue(ok2, why2)


if __name__ == "__main__":
    unittest.main()
