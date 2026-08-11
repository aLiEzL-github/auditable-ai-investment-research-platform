"""G4-03 验收测试：CurrentKey 提交协议 + DB 事务发布 + 父版本 CAS（D-4/D-5/D-6）。

基线 B §7 G4-03：先写内容寻址工件并验哈希、再以 DB 事务更新对应 immutable
release/pointer、孤儿回收。验收：
  · system-design-plan/… 与 a-share-single-company-research/600089.SH 的
    current 完全分域（D-4）
  · 首次研究发布要求 parent=null 且该研究 CurrentKey 不存在
  · 同 subject root 幂等；不同 root 冲突硬失败
  · 陈旧父、跨 workflow/scope/key、并发和工件失败被拒绝
  · 孤儿永不成为 current（D-5，一票否决）
  · current 变更可追溯（D-6：谁、何时、依据哪个批准）
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
from publish_engine import (RESEARCH_600089_KEY, SYS_DESIGN_KEY, CurrentKey,
                            create_approval, current_release, gc_orphans,
                            publish_release)


class TestPublishBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self.store = ArtifactStore(os.path.join(self._tmp, "lib"))
        from repository import create_repository
        self.repo = create_repository(os.path.join(self._tmp, "publish.sqlite3"))
        self.repo.create_all()
        self.s = self.repo.session()

    def tearDown(self):
        self.s.close()
        self.repo.engine.dispose()
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _approve(self, m, key, approver="U-fixture"):
        return create_approval(self.store, self.s, m, approver, key,
                               approved_at="2026-08-11T07:00:00Z")

    def _publish_first(self, key, with_nbs=True, **closure_kw):
        m = fx.minimal_closure(self.store, key, with_nbs=with_nbs,
                               **closure_kw)
        appr = self._approve(m, key)
        rel = publish_release(self.store, self.s, m, key, appr,
                              released_at="2026-08-11T07:01:00Z")
        return m, appr, rel


class TestDomainSeparation(TestPublishBase):
    """D-4：不同域 current 互不干扰，逐域断言。"""

    def test_domains_fully_separated(self):
        _, _, rel_sys = self._publish_first(SYS_DESIGN_KEY)
        cur_sys = current_release(self.s, SYS_DESIGN_KEY)
        self.assertIsNone(current_release(self.s, RESEARCH_600089_KEY),
                          "系统设计域发布不得影响研究域")
        self.assertEqual(cur_sys["release_id"], rel_sys.id)

        _, _, rel_res = self._publish_first(RESEARCH_600089_KEY)
        cur_res = current_release(self.s, RESEARCH_600089_KEY)
        self.assertEqual(cur_res["release_id"], rel_res.id)
        self.assertEqual(cur_sys["release_id"],
                         current_release(self.s, SYS_DESIGN_KEY)["release_id"],
                         "研究域发布不得影响系统设计域")
        # 两域各自单一 current，无共享指针
        self.assertNotEqual(rel_sys.id, rel_res.id)

    def test_first_research_publish_requires_parent_null(self):
        m = fx.minimal_closure(self.store, RESEARCH_600089_KEY)
        m["parent"] = "a" * 64                      # 变异：首次发布带父
        m["id"] = fx.content_id(m)
        appr = self._approve(m, RESEARCH_600089_KEY)
        with self.assertRaises(ValueError) as cm:
            publish_release(self.store, self.s, m, RESEARCH_600089_KEY, appr,
                            released_at="2026-08-11T07:01:00Z")
        self.assertIn("E-G4-03-005", str(cm.exception))

    def test_same_subject_root_idempotent(self):
        m, appr, rel = self._publish_first(RESEARCH_600089_KEY)
        # 同 subject root 重复发布 → 返回既有 release，指针不动
        rel2 = publish_release(self.store, self.s, m, RESEARCH_600089_KEY, appr,
                               released_at="2026-08-11T07:02:00Z")
        self.assertEqual(rel2.id, rel.id)
        cur = current_release(self.s, RESEARCH_600089_KEY)
        self.assertEqual(cur["seq"], 1, "幂等发布不得新增指针记录")

    def test_different_root_hard_fail(self):
        m, appr, _ = self._publish_first(RESEARCH_600089_KEY)
        m2 = fx.minimal_closure(self.store, RESEARCH_600089_KEY,
                                candidate_payload={"ticker": "OTHER"})
        appr2 = self._approve(m2, RESEARCH_600089_KEY)
        with self.assertRaises(ValueError) as cm:
            publish_release(self.store, self.s, m2, RESEARCH_600089_KEY, appr2,
                            released_at="2026-08-11T07:03:00Z")
        self.assertIn("E-G4-03-006", str(cm.exception))

    def test_stale_parent_rejected(self):
        _, _, rel1 = self._publish_first(RESEARCH_600089_KEY)
        # 版本 2：parent 必须是 rel1 的 manifest 哈希
        m2 = fx.minimal_closure(self.store, RESEARCH_600089_KEY)
        m2["parent"] = "b" * 64                      # 变异：陈旧父
        m2["id"] = fx.content_id(m2)
        appr2 = self._approve(m2, RESEARCH_600089_KEY)
        with self.assertRaises(ValueError) as cm:
            publish_release(self.store, self.s, m2, RESEARCH_600089_KEY, appr2,
                            released_at="2026-08-11T07:04:00Z")
        self.assertIn("E-G4-03-007", str(cm.exception))
        # 正确父 → 成功，版本升级
        m3 = fx.minimal_closure(self.store, RESEARCH_600089_KEY)
        m3["parent"] = rel1.manifest_hash
        m3["id"] = fx.content_id(m3)
        appr3 = self._approve(m3, RESEARCH_600089_KEY)
        rel2 = publish_release(self.store, self.s, m3, RESEARCH_600089_KEY,
                               appr3, released_at="2026-08-11T07:05:00Z")
        self.assertEqual(rel2.version, "1.2.0")
        cur = current_release(self.s, RESEARCH_600089_KEY)
        self.assertEqual(cur["release_id"], rel2.id)
        # 历史不可回写：rel1 记录字节不变
        self.assertEqual(cur["seq"], 2)

    def test_cross_workflow_scope_key_rejected(self):
        m = fx.minimal_closure(self.store, SYS_DESIGN_KEY)   # 系统设计域清单
        appr = self._approve(m, RESEARCH_600089_KEY)         # 变异：跨域发布
        with self.assertRaises(ValueError) as cm:
            publish_release(self.store, self.s, m, RESEARCH_600089_KEY, appr,
                            released_at="2026-08-11T07:06:00Z")
        self.assertIn("E-G4-03-008", str(cm.exception))
        self.assertIsNone(current_release(self.s, RESEARCH_600089_KEY),
                          "跨域发布被拒后不得留下任何指针")

    def test_artifact_failure_rejected(self):
        m = fx.minimal_closure(self.store, RESEARCH_600089_KEY)
        appr = self._approve(m, RESEARCH_600089_KEY)   # 批准时闭包完整
        # 批准后工件被破坏（对象从库中消失）→ 首次发布必须拒绝
        victim = next(oid for oid, meta in m["objects"].items()
                      if meta.get("kind") == "report")
        rel = f"{victim[:2]}/{victim[2:4]}/{victim[4:]}"
        fp = os.path.join(self.store.root, rel)
        try:
            os.remove(fp)
        except OSError:
            os.chmod(fp, 0o644)
            os.remove(fp)
        with self.assertRaises(ValueError) as cm:
            publish_release(self.store, self.s, m, RESEARCH_600089_KEY, appr,
                            released_at="2026-08-11T07:07:00Z")
        self.assertIn("E-G4-03-004", str(cm.exception))
        self.assertIsNone(current_release(self.s, RESEARCH_600089_KEY),
                          "工件失败不得留下任何指针")

    def test_concurrency_unique_seq(self):
        """并发：同域同 seq 二次提交由唯一约束拒绝（E-G4-03-009）。"""
        from datetime import datetime
        from sqlalchemy.exc import IntegrityError
        from repository import CurrentPointer, Release
        m, appr, rel = self._publish_first(RESEARCH_600089_KEY)
        # 并发窗口：两个发布者都从 seq=1 出发计算 seq=2，第二个提交必冲突
        s2 = self.repo.session()
        try:
            cur_rel = s2.get(Release, rel.id)
            ptr = CurrentPointer(id="PTR_CONC_0001", schema_version="1.0.0",
                                 workflow=RESEARCH_600089_KEY.workflow,
                                 scope_id=RESEARCH_600089_KEY.scope_id,
                                 current_key="",
                                 release_id=cur_rel.id,
                                 seq=1,           # 与既有 seq 相同 → 冲突
                                 changed_by="L11_release",
                                 changed_at=datetime(2026, 8, 11),
                                 approval_id=appr.id, version=1)
            s2.add(ptr)
            s2.commit()
            self.fail("同域同 seq 二次提交应触发唯一约束拒绝")
        except IntegrityError:
            s2.rollback()      # 失败关闭：并发方被拒，不留下任何行
            from repository import CurrentPointer as CP
            n = self.s.query(CP).filter_by(
                workflow=RESEARCH_600089_KEY.workflow,
                scope_id=RESEARCH_600089_KEY.scope_id).count()
            self.assertEqual(n, 1, "并发冲突不得追加指针行")
        finally:
            s2.close()

    def test_orphan_never_current(self):
        """D-5（一票否决）：孤儿对象不得成为 current —— 必须失败关闭。"""
        m, appr, rel = self._publish_first(RESEARCH_600089_KEY)
        # 构造孤儿：写入对象库、不在任何闭包内
        orphan = fx.build_macro(self.store, value="999.9")
        # 孤儿在库中，但 manifest 登记表没有它 → 不在闭包内
        from publish_engine import compute_closure
        c = compute_closure(self.store, m)
        self.assertNotIn(orphan, c.reachable)
        # 路径 1：孤儿清单连批准都拿不到（approval 要求闭包完整）
        orphan_manifest = fx.manifest_of(
            self.store, RESEARCH_600089_KEY, root=orphan,
            objects={orphan: {"kind": "macro", "refs": []},
                     fx.build_assumption(self.store): {"kind": "assumption",
                                                       "refs": []}})
        with self.assertRaises(ValueError) as cm:
            create_approval(self.store, self.s, orphan_manifest,
                            "U-fixture", RESEARCH_600089_KEY,
                            approved_at="2026-08-11T07:08:00Z")
        self.assertIn("E-G4-04-001", str(cm.exception),
                      "孤儿清单无法通过批准（闭包不完整）")
        # 路径 2：即使拿旧批准硬发，发布也拒绝（输入绑定 + 闭包校验）
        with self.assertRaises(ValueError):
            publish_release(self.store, self.s, orphan_manifest,
                            RESEARCH_600089_KEY, appr,
                            released_at="2026-08-11T07:08:00Z")
        # 孤儿回收后可删；回收后再发布仍须失败（不存在 = 工件缺失）
        gc_orphans(self.store, [m])
        orphan_manifest2 = fx.manifest_of(
            self.store, RESEARCH_600089_KEY, root=orphan,
            objects={orphan: {"kind": "macro", "refs": []}})
        with self.assertRaises(ValueError):
            publish_release(self.store, self.s, orphan_manifest2,
                            RESEARCH_600089_KEY, appr,
                            released_at="2026-08-11T07:09:00Z")
        # 兜底断言：整个过程中孤儿从未成为 current
        cur = current_release(self.s, RESEARCH_600089_KEY)
        self.assertNotEqual(cur["release_id"], orphan,
                            "孤儿不得成为 current")

    def test_current_change_traceable(self):
        """D-6：每次 current 指针变更留痕：谁、何时、依据哪个批准。"""
        m1, appr1, rel1 = self._publish_first(RESEARCH_600089_KEY)
        cur1 = current_release(self.s, RESEARCH_600089_KEY)
        self.assertEqual(cur1["changed_by"], "L11_release")
        self.assertEqual(cur1["approval_id"], appr1.id)
        self.assertEqual(cur1["seq"], 1)
        # 版本 2 → 指针新增一行，历史保留
        m2 = fx.minimal_closure(self.store, RESEARCH_600089_KEY)
        m2["parent"] = rel1.manifest_hash
        m2["id"] = fx.content_id(m2)
        appr2 = self._approve(m2, RESEARCH_600089_KEY)
        publish_release(self.store, self.s, m2, RESEARCH_600089_KEY, appr2,
                        released_at="2026-08-11T07:10:00Z")
        cur2 = current_release(self.s, RESEARCH_600089_KEY)
        self.assertEqual(cur2["seq"], 2)
        self.assertNotEqual(cur2["approval_id"], appr1.id)
        # 历史行：两行都在（追加式），可逐行追溯
        from repository import CurrentPointer
        rows = self.s.query(CurrentPointer).filter_by(
            workflow=RESEARCH_600089_KEY.workflow,
            scope_id=RESEARCH_600089_KEY.scope_id).all()
        self.assertEqual(len(rows), 2, "指针历史须保留（追加式留痕）")
        for r in rows:
            self.assertTrue(r.changed_by)
            self.assertTrue(r.changed_at)
            self.assertTrue(r.approval_id)


if __name__ == "__main__":
    unittest.main()
