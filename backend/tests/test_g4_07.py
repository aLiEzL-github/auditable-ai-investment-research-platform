"""G4-07 验收测试：preliminary candidate 对象闭包与 approval_subject_root（D-1/D-2）。

基线 B §7 G4-07：任何对象缺失、跨 workflow/scope、版本漂移或未关材料性
开放项都使 release_eligible=false。该闭包只验证 fixture 算法，
不是 Gate 7 最终研究 subject（最终闭合必须执行 G7-00）。
D-1：漏登记一个对象须 FAIL；须报出闭包内对象数（规则 ⑨）。
D-2：构造两个候选 root 须 FAIL 而非任选其一。
"""
import json
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

    # ── OI-PF-198：畸形 open_item 失败关闭（不得 continue 静默跳过）──
    def _closure_with_open_item(self, body) -> dict:
        """把任意 open_item 载荷（dict 或原始字节）接入完整闭包。"""
        m = fx.minimal_closure(self.store, self.key)
        old_oi = next(oid for oid, meta in m["objects"].items()
                      if meta.get("kind") == "open_item")
        payload = (body if isinstance(body, bytes)
                   else json.dumps(body, ensure_ascii=False).encode("utf-8"))
        digest = self.store.store("open_item", payload)   # 内容寻址正确
        m["objects"][digest] = {"kind": "open_item", "refs": []}
        del m["objects"][old_oi]
        cand = m["subject_root"]
        m["objects"][cand] = dict(
            m["objects"][cand],
            refs=[digest if r == old_oi else r
                  for r in m["objects"][cand]["refs"]])
        m["id"] = fx.content_id(m)
        return m

    def test_open_item_not_json_fails_closed(self):
        """内容寻址正确的 b"not-json"：JSON 解析失败 → E-G4-07-007。"""
        m = self._closure_with_open_item(b"not-json")
        with self.assertRaises(ValueError) as cm:
            audit_open_items(self.store, m)
        self.assertIn("E-G4-07-007", str(cm.exception))

    def test_open_item_json_list_fails_closed(self):
        """JSON 非对象（数组）→ E-G4-07-007。"""
        m = self._closure_with_open_item([1, 2, 3])
        with self.assertRaises(ValueError) as cm:
            audit_open_items(self.store, m)
        self.assertIn("E-G4-07-007", str(cm.exception))

    def test_open_item_wrong_body_kind_fails_closed(self):
        """body.kind != open_item → E-G4-07-007（body 与登记元数据不符）。"""
        body = {"schema_version": "1.0.0", "kind": "macro",
                "open_item_id": "OI-X", "status": "CLOSED", "material": False}
        m = self._closure_with_open_item(body)
        with self.assertRaises(ValueError) as cm:
            audit_open_items(self.store, m)
        self.assertIn("E-G4-07-007", str(cm.exception))

    def test_open_item_unknown_status_fails_closed(self):
        """status 不在支持集（OPEN/CLOSED/SUPERSEDED）→ 拒，不默认为 CLOSED。"""
        body = {"schema_version": "1.0.0", "kind": "open_item",
                "open_item_id": "OI-X", "status": "IN_REVIEW", "material": False}
        m = self._closure_with_open_item(body)
        with self.assertRaises(ValueError) as cm:
            audit_open_items(self.store, m)
        self.assertIn("E-G4-07-007", str(cm.exception))
        self.assertIn("IN_REVIEW", str(cm.exception),
                      "未知状态不得被当作 CLOSED 放行")

    def test_open_item_material_not_bool_fails_closed(self):
        """material 非 bool（字符串 "true"）→ E-G4-07-007。"""
        body = {"schema_version": "1.0.0", "kind": "open_item",
                "open_item_id": "OI-X", "status": "OPEN", "material": "true"}
        m = self._closure_with_open_item(body)
        with self.assertRaises(ValueError) as cm:
            audit_open_items(self.store, m)
        self.assertIn("E-G4-07-007", str(cm.exception))

    def test_open_item_missing_open_item_id_fails_closed(self):
        """open_item_id 缺失/非字符串 → E-G4-07-007（唯一 ID 为真实合同字段）。"""
        body = {"schema_version": "1.0.0", "kind": "open_item",
                "status": "CLOSED", "material": False}
        m = self._closure_with_open_item(body)
        with self.assertRaises(ValueError) as cm:
            audit_open_items(self.store, m)
        self.assertIn("E-G4-07-007", str(cm.exception))

    def test_open_item_legal_statuses_do_not_block(self):
        """合法 SUPERSEDED/CLOSED（即使 material=True）与 OPEN+material=false 不阻断。"""
        for status, material in (("SUPERSEDED", True), ("CLOSED", True),
                                 ("CLOSED", False), ("OPEN", False)):
            body = {"schema_version": "1.0.0", "kind": "open_item",
                    "open_item_id": f"OI-{status}", "status": status,
                    "material": material}
            m = self._closure_with_open_item(body)
            self.assertIsNone(audit_open_items(self.store, m),
                              f"{status}+material={material} 不得阻断")

    # ── OI-PF-198 回归：真实合同 OpenItem.to_dict() 接入完整闭包 ─────
    def test_real_contract_open_item_wired_into_closure(self):
        """真实合同 OpenItem(...).to_dict() 正向通过；OPEN+material 仍阻断。

        body = schema_version/kind + OpenItem.to_dict()（唯一 ID = open_item_id，
        不含假合同的 `id`）。CLOSED 与 OPEN+material=false 使 audit_open_items
        与 audit_candidate 正向通过；OPEN+material=true 仍阻断（E-G4-07-005）。
        此测试先断言 keys 含 open_item_id 且不含 id —— 防 fixture 再漂回假合同。
        """
        from open_item_registry import OpenItem
        from publish_engine import audit_candidate
        for status, material, expect_block in (
                ("CLOSED", False, False),
                ("OPEN", False, False),
                ("OPEN", True, True)):
            item = OpenItem(open_item_id="OI-REAL-001", description="真实字段",
                            material=material, owner_role="U-fixture",
                            status=status)
            body = {"schema_version": "1.0.0", "kind": "open_item",
                    **item.to_dict()}
            keys = set(body.keys())
            self.assertIn("open_item_id", keys,
                          "真实合同须含 open_item_id")
            self.assertNotIn("id", keys,
                             "真实合同不得定义假 id 字段")
            m = self._closure_with_open_item(body)
            if expect_block:
                with self.assertRaises(ValueError) as cm:
                    audit_open_items(self.store, m)
                self.assertIn("E-G4-07-005", str(cm.exception))
                a = audit_candidate(self.store, m)
                self.assertFalse(a.release_eligible)
                self.assertEqual(a.gates["open_items"], "FAIL")
            else:
                self.assertIsNone(audit_open_items(self.store, m),
                                  f"{status}+material={material} 不得阻断")
                a = audit_candidate(self.store, m)
                self.assertTrue(a.release_eligible, a.failures)
                self.assertEqual(a.gates["open_items"], "PASS")


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
