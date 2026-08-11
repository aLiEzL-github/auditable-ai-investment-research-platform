"""G4-07 验收测试：preliminary candidate 对象闭包与 approval_subject_root（D-1/D-2）。

基线 B §7 G4-07：任何对象缺失、跨 workflow/scope、版本漂移或未关材料性
开放项都使 release_eligible=false。该闭包只验证 fixture 算法，
不是 Gate 7 最终研究 subject（最终闭合必须执行 G7-00）。
D-1：漏登记一个对象须 FAIL；须报出闭包内对象数（规则 ⑨）。
D-2：构造两个候选 root 须 FAIL 而非任选其一。
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
from publish_engine import (audit_open_items, approval_subject_root,
                            assert_cross_domain_clean, compute_closure,
                            resolve_subject_root)


class TestClosure(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self.store = ArtifactStore(os.path.join(self._tmp, "lib"))
        self.key = __import__("publish_engine").CurrentKey(
            "a-share-single-company-research", "600089.SH")

    def tearDown(self):
        shutil.rmtree(self._tmp, ignore_errors=True)

    # ── D-1 正例：闭包完整，报出对象数 ─────────────────────────────
    def test_closure_complete_reports_count(self):
        m = fx.minimal_closure(self.store, self.key)
        c = compute_closure(self.store, m)
        self.assertTrue(c.complete)
        self.assertGreaterEqual(c.count, 9, "闭包须报出对象数（⑨）")
        self.assertEqual(c.count, len(c.registered))

    # ── ⑨：「闭包内 0 个对象」与「闭包完整」必须可分辨 ─────────────
    def test_zero_object_distinct_from_complete(self):
        m = fx.minimal_closure(self.store, self.key)
        # 完整闭包：报 complete=True + 明确对象数
        c = compute_closure(self.store, m)
        self.assertTrue(c.complete)
        self.assertEqual(c.count, len(m["objects"]))
        # 变异：登记表清空（0 个对象可检查）→ 必须硬失败，
        # 不得误报「闭包完整」—— 与 complete=True 的结论可分辨
        m["objects"] = {}
        m["id"] = fx.content_id(m)
        with self.assertRaises(ValueError) as cm:
            compute_closure(self.store, m)
        self.assertIn("E-G4-07-004", str(cm.exception),
                      "0 个对象须 FAIL 而非报完整（⑨）")

    # ── D-1 变异注入：漏登记一个对象（删除登记表行）→ FAIL ─────────
    def test_missing_registration_fails(self):
        m = fx.minimal_closure(self.store, self.key)
        victim = next(oid for oid, meta in m["objects"].items()
                      if meta.get("kind") == "evidence")
        del m["objects"][victim]               # 变异：漏登记
        m["id"] = fx.content_id(m)
        c = compute_closure(self.store, m)
        self.assertFalse(c.complete)
        self.assertIn(victim, c.dangling, "被引用而未登记须报 dangling")
        with self.assertRaises(ValueError):
            approval_subject_root(self.store, m)

    # ── D-1 变异注入：死对象（登记了但不可达）→ FAIL ───────────────
    def test_dead_object_fails(self):
        m = fx.minimal_closure(self.store, self.key)
        m["objects"][fx.build_macro(self.store, value="9.9")] = {
            "kind": "macro", "refs": []}   # 变异：孤儿登记（内容寻址新 id）
        m["id"] = fx.content_id(m)
        c = compute_closure(self.store, m)
        self.assertFalse(c.complete)
        self.assertEqual(len(c.dead), 1)

    # ── 篡改（版本漂移）：对象内容与摘要不符 → FAIL ─────────────────
    def test_tampered_object_fails(self):
        m = fx.minimal_closure(self.store, self.key)
        oid = next(iter(m["objects"]))
        # 绕过 store 直接改写库内字节（0o444 只读，需 chmod）
        rel = f"{oid[:2]}/{oid[2:4]}/{oid[4:]}"
        fp = os.path.join(self.store.root, rel)
        try:
            with open(fp, "wb") as f:
                f.write(b"EVIL")
        except PermissionError:
            os.chmod(fp, 0o644)
            with open(fp, "wb") as f:
                f.write(b"EVIL")
        c = compute_closure(self.store, m)
        self.assertFalse(c.complete)
        self.assertIn(oid, c.mismatch)

    # ── 跨 workflow/scope：闭包对象跨域 → FAIL ──────────────────────
    def test_cross_workflow_scope_fails(self):
        m = fx.minimal_closure(self.store, self.key)
        m["objects"][list(m["objects"])[1]] = {
            "kind": "evidence", "refs": [], "workflow": "other-domain"}
        m["id"] = fx.content_id(m)
        with self.assertRaises(ValueError) as cm:
            assert_cross_domain_clean(m)
        self.assertIn("E-G4-07-006", str(cm.exception))

    # ── 未关材料性开放项在闭包内 → release_eligible=false ──────────
    def test_open_material_item_blocks(self):
        m = fx.minimal_closure(self.store, self.key,
                               open_item_status="OPEN",
                               open_item_material=True)
        with self.assertRaises(ValueError) as cm:
            audit_open_items(self.store, m)
        self.assertIn("E-G4-07-005", str(cm.exception))
        # 闭合后恢复
        m2 = fx.minimal_closure(self.store, self.key,
                                open_item_status="CLOSED",
                                open_item_material=False)
        self.assertIsNone(audit_open_items(self.store, m2))


class TestSubjectRoot(unittest.TestCase):
    """D-2：subject root 单一且明确 —— 两个候选 root 须 FAIL 而非任选其一。"""

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self.store = ArtifactStore(os.path.join(self._tmp, "lib"))
        self.key = __import__("publish_engine").CurrentKey(
            "a-share-single-company-research", "600089.SH")

    def tearDown(self):
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_single_root_resolved(self):
        m = fx.minimal_closure(self.store, self.key)
        root = resolve_subject_root(m)
        self.assertEqual(len(root), 64)
        self.assertIn(root, m["objects"])

    def test_two_candidates_fail_not_pick(self):
        m = fx.minimal_closure(self.store, self.key)
        root = resolve_subject_root(m)
        other = fx.build_candidate(self.store, {"payload": {"ticker": "OTHER"}})
        m["subject_root_candidates"] = [root, other]    # 变异：两个候选
        m["id"] = fx.content_id(m)
        with self.assertRaises(ValueError) as cm:
            resolve_subject_root(m)
        self.assertIn("E-G4-07-003", str(cm.exception))
        # 不得任选其一：compute_closure 也须失败
        with self.assertRaises(ValueError):
            compute_closure(self.store, m)

    def test_missing_root_fails(self):
        m = fx.minimal_closure(self.store, self.key)
        del m["subject_root"]
        if "subject_root_candidates" in m:
            del m["subject_root_candidates"]
        with self.assertRaises(ValueError) as cm:
            resolve_subject_root(m)
        self.assertIn("E-G4-07-003", str(cm.exception))


if __name__ == "__main__":
    unittest.main()
