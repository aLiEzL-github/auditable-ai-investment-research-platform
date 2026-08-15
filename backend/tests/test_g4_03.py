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
                            approval_subject_root, create_approval,
                            current_release, gc_orphans, inputs_hash,
                            publish_release, resolve_subject_root)


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
                               approved_at="2026-08-11T07:00:00Z",
                               acknowledged=True)

    def _publish_first(self, key, with_nbs=True, **closure_kw):
        m = fx.minimal_closure(self.store, key, with_nbs=with_nbs,
                               **closure_kw)
        appr = self._approve(m, key)
        rel = publish_release(self.store, self.s, m, key, appr,
                              released_at="2026-08-11T07:01:00Z",
                              writer="L11_release")
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
                            released_at="2026-08-11T07:01:00Z",
                            writer="L11_release")
        self.assertIn("E-G4-03-005", str(cm.exception))

    def test_same_subject_root_idempotent(self):
        m, appr, rel = self._publish_first(RESEARCH_600089_KEY)
        # 同 subject root 重复发布 → 返回既有 release，指针不动
        rel2 = publish_release(self.store, self.s, m, RESEARCH_600089_KEY, appr,
                               released_at="2026-08-11T07:02:00Z",
                               writer="L11_release")
        self.assertEqual(rel2.id, rel.id)
        cur = current_release(self.s, RESEARCH_600089_KEY)
        self.assertEqual(cur["seq"], 1, "幂等发布不得新增指针记录")

    def test_matching_candidates_through_approval_and_publish(self):
        """OI-PF-202 正向：匹配双字段（candidates=[subject_root]）批准并发布。

        持久化的 Release.subject_root_hash 必须等于 resolve_subject_root
        (manifest)（幂等比较与持久化都只用解析根，不读未校验原始字段）；
        匹配双字段与普通单一 subject_root 同语义，幂等二次发布仍返回既有 release。
        """
        m = fx.minimal_closure(self.store, RESEARCH_600089_KEY)
        root = resolve_subject_root(m)
        m["subject_root_candidates"] = [root]
        m["subject_root"] = root
        m["id"] = fx.content_id(m)
        appr = self._approve(m, RESEARCH_600089_KEY)
        rel = publish_release(self.store, self.s, m, RESEARCH_600089_KEY, appr,
                              released_at="2026-08-11T07:01:00Z",
                              writer="L11_release")
        self.assertEqual(rel.subject_root_hash, resolve_subject_root(m),
                         "持久化 release 根必须等于 resolve_subject_root(manifest)")
        cur = current_release(self.s, RESEARCH_600089_KEY)
        self.assertEqual(cur["subject_root_hash"], resolve_subject_root(m),
                         "current 指针的根哈希必须等于解析根")
        # 匹配双字段幂等：同清单再次发布返回既有 release，指针不动
        rel2 = publish_release(self.store, self.s, m, RESEARCH_600089_KEY, appr,
                               released_at="2026-08-11T07:02:00Z",
                               writer="L11_release")
        self.assertEqual(rel2.id, rel.id)
        self.assertEqual(current_release(self.s, RESEARCH_600089_KEY)["seq"], 1,
                         "匹配双字段幂等发布不得新增指针记录")

    def test_different_root_hard_fail(self):
        m, appr, _ = self._publish_first(RESEARCH_600089_KEY)
        m2 = fx.minimal_closure(self.store, RESEARCH_600089_KEY,
                                candidate_payload={"ticker": "OTHER"})
        appr2 = self._approve(m2, RESEARCH_600089_KEY)
        with self.assertRaises(ValueError) as cm:
            publish_release(self.store, self.s, m2, RESEARCH_600089_KEY, appr2,
                            released_at="2026-08-11T07:03:00Z",
                            writer="L11_release")
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
                            released_at="2026-08-11T07:04:00Z",
                            writer="L11_release")
        self.assertIn("E-G4-03-007", str(cm.exception))
        # 正确父 → 成功，版本升级
        m3 = fx.minimal_closure(self.store, RESEARCH_600089_KEY)
        m3["parent"] = rel1.manifest_hash
        m3["id"] = fx.content_id(m3)
        appr3 = self._approve(m3, RESEARCH_600089_KEY)
        rel2 = publish_release(self.store, self.s, m3, RESEARCH_600089_KEY,
                               appr3, released_at="2026-08-11T07:05:00Z",
                               writer="L11_release")
        self.assertEqual(rel2.version, "1.2.0")
        cur = current_release(self.s, RESEARCH_600089_KEY)
        self.assertEqual(cur["release_id"], rel2.id)
        # 历史不可回写：rel1 记录字节不变
        self.assertEqual(cur["seq"], 2)

    def test_cross_workflow_scope_key_rejected(self):
        """跨 workflow/scope/key 发布被拒，且无指针残留。

        OI-PF-197：跨 key 批准现在在**创建层**就被拒（E-G4-04-006）。
        即使伪造一个绑定到目标 key、object_ref/hash 与清单内部一致的批准，
        唯一准出谓词也须直接核对**目标 key 与清单 CurrentKey**（E-G4-04-006，
        不再等到发布侧的 E-G4-03-008），跨域发布被拒且不得留下任何指针。
        """
        from datetime import datetime
        from repository import Approval
        m = fx.minimal_closure(self.store, SYS_DESIGN_KEY)   # 系统设计域清单
        with self.assertRaises(ValueError) as cm:
            self._approve(m, RESEARCH_600089_KEY)            # 跨 key 批准创建层被拒
        self.assertIn("E-G4-04-006", str(cm.exception))
        self.assertEqual(self.s.query(Approval).count(), 0, "错 key 批准不得留行")
        forged = Approval(
            id="APR_FORGED0001", schema_version="1.0.0",
            object_ref=m["subject_root"], approver="U-fixture",
            approved_at=datetime(2026, 8, 11),
            subject_root_hash=approval_subject_root(self.store, m),
            workflow=RESEARCH_600089_KEY.workflow,
            scope_id=RESEARCH_600089_KEY.scope_id,
            current_key=RESEARCH_600089_KEY.current_key,
            inputs_hash=inputs_hash(m), status="ACTIVE",
            token="APPROVE", version=1)
        self.s.add(forged)
        self.s.commit()
        with self.assertRaises(ValueError) as cm2:
            publish_release(self.store, self.s, m, RESEARCH_600089_KEY, forged,
                            released_at="2026-08-11T07:06:00Z",
                            writer="L11_release")
        self.assertIn("E-G4-04-006", str(cm2.exception),
                      "唯一准出谓词须先拒绝跨域（目标 key ≠ 清单 CurrentKey）")
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
                            released_at="2026-08-11T07:07:00Z",
                            writer="L11_release")
        # OI-PF-193：唯一准出谓词先行重算审计 —— 对象缺失先被审计完整性门
        # 抓到（E-G4-04-005），比发布侧的 E-G4-03-004 更早、更严格。
        self.assertIn("E-G4-04-005", str(cm.exception))
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
                            approved_at="2026-08-11T07:08:00Z",
                            acknowledged=True)
        self.assertIn("E-G4-04-001", str(cm.exception),
                      "孤儿清单无法通过批准（闭包不完整）")
        # 路径 2：即使拿旧批准硬发，发布也拒绝（输入绑定 + 闭包校验）
        with self.assertRaises(ValueError):
            publish_release(self.store, self.s, orphan_manifest,
                            RESEARCH_600089_KEY, appr,
                            released_at="2026-08-11T07:08:00Z",
                            writer="L11_release")
        # 孤儿回收后可删；回收后再发布仍须失败（不存在 = 工件缺失）
        gc_orphans(self.store, [m])
        orphan_manifest2 = fx.manifest_of(
            self.store, RESEARCH_600089_KEY, root=orphan,
            objects={orphan: {"kind": "macro", "refs": []}})
        with self.assertRaises(ValueError):
            publish_release(self.store, self.s, orphan_manifest2,
                            RESEARCH_600089_KEY, appr,
                            released_at="2026-08-11T07:09:00Z",
                            writer="L11_release")
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
                        released_at="2026-08-11T07:10:00Z",
                        writer="L11_release")
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


class TestReleaseOpenItemGate(TestPublishBase):
    """OI-PF-193：未关材料性 OpenItem 使三层全部拒绝（端到端原失败载荷）。"""

    def test_open_material_item_blocks_end_to_end(self):
        """OPEN+material → 批准可建、发布必拒，且 release/current 行数均为 0。"""
        m = fx.minimal_closure(self.store, RESEARCH_600089_KEY,
                               open_item_status="OPEN", open_item_material=True)
        appr = self._approve(m, RESEARCH_600089_KEY)
        with self.assertRaises(ValueError) as cm:
            publish_release(self.store, self.s, m, RESEARCH_600089_KEY, appr,
                            released_at="2026-08-11T07:01:00Z",
                            writer="L11_release")
        self.assertIn("E-G4-04-005", str(cm.exception),
                      "未关材料性开放项须先被唯一准出谓词的审计门拒绝")
        self.assertIsNone(current_release(self.s, RESEARCH_600089_KEY),
                          "拒绝后不得留下 current 指针")
        from repository import CurrentPointer, Release
        self.assertEqual(self.s.query(Release).count(), 0, "不得写 release 行")
        self.assertEqual(self.s.query(CurrentPointer).count(), 0,
                         "不得写 current 指针行（无 DB 残留）")

    def test_non_material_open_item_does_not_block_release(self):
        """OPEN 但 material=False（非材料性）→ 正向发布成功（防过度修复）。"""
        m = fx.minimal_closure(self.store, RESEARCH_600089_KEY,
                               open_item_status="OPEN", open_item_material=False)
        appr = self._approve(m, RESEARCH_600089_KEY)
        rel = publish_release(self.store, self.s, m, RESEARCH_600089_KEY, appr,
                              released_at="2026-08-11T07:01:00Z",
                              writer="L11_release")
        cur = current_release(self.s, RESEARCH_600089_KEY)
        self.assertEqual(cur["release_id"], rel.id,
                         "非材料性开放项不阻断发布")
        # CLOSED 同样成功（版本 2，parent = 当前 manifest 哈希）
        m2 = fx.minimal_closure(self.store, RESEARCH_600089_KEY,
                                open_item_status="CLOSED",
                                open_item_material=False)
        m2["parent"] = rel.manifest_hash
        m2["id"] = fx.content_id(m2)
        appr2 = self._approve(m2, RESEARCH_600089_KEY)
        rel2 = publish_release(self.store, self.s, m2, RESEARCH_600089_KEY,
                               appr2, released_at="2026-08-11T07:02:00Z",
                               writer="L11_release")
        self.assertEqual(rel2.version, "1.2.0",
                         "CLOSED 开放项发布成功（版本升级）")

    def test_missing_writer_rejected(self):
        """writer 无合法缺省：不传即 TypeError；非法写者被矩阵拒。"""
        m = fx.minimal_closure(self.store, RESEARCH_600089_KEY)
        appr = self._approve(m, RESEARCH_600089_KEY)
        with self.assertRaises(TypeError):
            publish_release(self.store, self.s, m, RESEARCH_600089_KEY, appr,
                            released_at="2026-08-11T07:01:00Z")
        from schema_validate import SchemaError
        with self.assertRaises(SchemaError) as cm:
            publish_release(self.store, self.s, m, RESEARCH_600089_KEY, appr,
                            released_at="2026-08-11T07:01:00Z",
                            writer="L9_claim")
        self.assertEqual(cm.exception.code, "E-WRITE-001",
                         "非 release 白名单的写者须被拒（E-WRITE-001）")

    # ── OI-PF-198：畸形 open_item（not-json）在发布链失败且无残留 ────
    def test_malformed_open_item_blocks_end_to_end(self):
        """内容寻址正确的 b"not-json" 接入完整闭包 → 发布链三层全部拒绝。

        audit_open_items（E-G4-07-007）→ audit_candidate（open_items FAIL）
        → 唯一谓词/发布（E-G4-04-005）；release/current 行数均为 0。
        """
        m = fx.minimal_closure(self.store, RESEARCH_600089_KEY)
        old_oi = next(oid for oid, meta in m["objects"].items()
                      if meta.get("kind") == "open_item")
        notjson = self.store.store("open_item", b"not-json")   # 内容寻址正确
        m["objects"][notjson] = {"kind": "open_item", "refs": []}
        del m["objects"][old_oi]
        cand = m["subject_root"]
        m["objects"][cand] = dict(
            m["objects"][cand],
            refs=[notjson if r == old_oi else r
                  for r in m["objects"][cand]["refs"]])
        m["id"] = fx.content_id(m)
        from publish_engine import audit_open_items
        with self.assertRaises(ValueError) as cm:
            audit_open_items(self.store, m)
        self.assertIn("E-G4-07-007", str(cm.exception))
        appr = self._approve(m, RESEARCH_600089_KEY)
        with self.assertRaises(ValueError) as cm2:
            publish_release(self.store, self.s, m, RESEARCH_600089_KEY, appr,
                            released_at="2026-08-11T07:01:00Z",
                            writer="L11_release")
        self.assertIn("E-G4-04-005", str(cm2.exception),
                      "唯一谓词/发布链须被开放项审计门拒绝")
        self.assertIsNone(current_release(self.s, RESEARCH_600089_KEY),
                          "畸形开放项被拒后不得留下 current 指针")
        from repository import CurrentPointer, Release
        self.assertEqual(self.s.query(Release).count(), 0, "不得写 release 行")
        self.assertEqual(self.s.query(CurrentPointer).count(), 0,
                         "不得写 current 指针行（无 DB 残留）")

    # ── OI-PF-197/198 正向：正确 key + 合法 OpenItem 发布成功 ────────
    def test_correct_key_legal_open_item_publish(self):
        """正确 key + 合法 OpenItem（SUPERSEDED / CLOSED+material）发布成功。

        防过度修复：SUPERSEDED 与 CLOSED（即使 material=True）都不阻断；
        OPEN+material=false 也不阻断（既有无残留语义）。
        """
        for status, material in (("SUPERSEDED", True),
                                 ("CLOSED", True),
                                 ("OPEN", False)):
            with self.subTest(status=status, material=material):
                tmp = tempfile.mkdtemp()
                try:
                    from repository import create_repository
                    store = ArtifactStore(os.path.join(tmp, "lib"))
                    repo = create_repository(os.path.join(tmp, "db.sqlite3"))
                    repo.create_all()
                    s = repo.session()
                    m = fx.minimal_closure(store, RESEARCH_600089_KEY,
                                           open_item_status=status,
                                           open_item_material=material)
                    appr = create_approval(store, s, m, "U-fixture",
                                           RESEARCH_600089_KEY,
                                           approved_at="2026-08-11T07:00:00Z",
                                           acknowledged=True)
                    rel = publish_release(store, s, m, RESEARCH_600089_KEY,
                                          appr,
                                          released_at="2026-08-11T07:01:00Z",
                                          writer="L11_release")
                    cur = current_release(s, RESEARCH_600089_KEY)
                    self.assertEqual(cur["release_id"], rel.id,
                                     f"{status}+material={material} 不阻断发布")
                    s.close()
                    repo.engine.dispose()
                finally:
                    shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
