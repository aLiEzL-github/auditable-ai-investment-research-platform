"""G4-05 验收测试：UpdateDiff 与父子版本。

基线 B §7 G4-05：差异、受影响结论、父子链；
新证据生成新版本，不回写历史。
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
from publish_engine import update_diff


class TestUpdateDiff(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self.store = ArtifactStore(os.path.join(self._tmp, "lib"))
        self.key = __import__("publish_engine").CurrentKey(
            "a-share-single-company-research", "600089.SH")

    def tearDown(self):
        shutil.rmtree(self._tmp, ignore_errors=True)

    # ── 新证据生成新版本：差异列出改动对象 ─────────────────────────
    def test_new_evidence_generates_new_version(self):
        m1 = fx.minimal_closure(self.store, self.key)
        # 版本 2：新证据 + 旧内容全部保留（新证据挂到 claim）
        new_ev = fx.build_evidence(self.store, fx.sse_source(),
                                   metric="revenue", value="9.9")
        m2 = fx.manifest_of(self.store, self.key, root=m1["subject_root"],
                            objects=dict(m1["objects"]),
                            parent=m1["id"])
        claim = next(oid for oid, meta in m1["objects"].items()
                     if meta.get("kind") == "claim")
        m2["objects"][new_ev] = {"kind": "evidence", "refs": []}
        m2["objects"][claim] = {"kind": "claim",
                                "refs": m1["objects"][claim]["refs"] + [new_ev]}
        m2["id"] = fx.content_id(m2)

        d = update_diff(m1, m2)
        self.assertIn(new_ev, d["changed_objects"])
        self.assertIn(claim, d["affected_conclusions"],
                      "受影响结论 = 从改动对象可达的 claim")
        self.assertEqual(d["old_version"], m1["id"])
        self.assertEqual(d["new_version"], m2["id"])
        self.assertIn(m1["id"], d["parent_chain"])

    # ── 回写历史被禁：旧 manifest 对象字节不变 ─────────────────────
    def test_history_not_rewritten(self):
        m1 = fx.minimal_closure(self.store, self.key)
        d1 = fx.freeze_manifest(self.store, m1)      # 历史入库存证
        snapshot = self.store.load(d1)
        # 产生新版本（内容寻址 → 新 id；旧对象仍在库、字节不变）
        m2 = fx.manifest_of(self.store, self.key, root=m1["subject_root"],
                            objects=dict(m1["objects"]),
                            parent=m1["id"])
        m2["code_version"] = "v1.1"
        m2["id"] = fx.content_id(m2)
        d2 = fx.freeze_manifest(self.store, m2)
        diff = update_diff(m1, m2)
        self.assertNotEqual(m1["id"], m2["id"])
        self.assertEqual(self.store.load(d1), snapshot,
                         "历史 manifest 字节必须逐字不变（不回写）")
        self.assertEqual(self.store.load(d2), fx.canonical_bytes(m2))

    # ── 内容级变更（同 id 不同登记）也算改动 ───────────────────────
    def test_meta_change_counted(self):
        m1 = fx.minimal_closure(self.store, self.key)
        m2 = fx.manifest_of(self.store, self.key, root=m1["subject_root"],
                            objects=dict(m1["objects"]), parent=m1["id"])
        oid = next(iter(m1["objects"]))
        meta = m1["objects"][oid]
        m2["objects"][oid] = dict(meta, refs=meta.get("refs", []) + ["0" * 64])
        m2["id"] = fx.content_id(m2)
        d = update_diff(m1, m2)
        self.assertIn(oid, d["changed_objects"])


class TestSignatureIntegrity(unittest.TestCase):
    """OI-PF-175/188/190：签名不得承诺实现没做的事。

    **载荷即原缺陷形态**：这些调用在修复前都是合法签名。
    """

    def test_update_diff_takes_no_store(self):
        """OI-PF-175：store 从不被读取（传 None 也照跑），已去掉。"""
        import inspect
        self.assertNotIn("store", inspect.signature(update_diff).parameters)

    def test_evaluate_takes_no_constants_override(self):
        """OI-PF-188：**调用方意图被吞掉** —— 传 0.99 与不传输出逐字相同
        （250 vs 应为 990），连 inputs_hash 都一样，CalcLedger 里不可分辨。
        """
        import inspect
        import sys as _s
        import os as _o
        _s.path.insert(0, _o.path.join(_o.path.dirname(__file__), "..", "app"))
        from formula_registry import FormulaRegistry
        self.assertNotIn("constants_override",
                         inspect.signature(FormulaRegistry.evaluate).parameters)

    def test_cas_update_takes_no_session(self):
        """OI-PF-190：只改内存版本号、不提交，带 session 会让人以为它持久化。"""
        import inspect
        import sys as _s
        import os as _o
        _s.path.insert(0, _o.path.join(_o.path.dirname(__file__), "..", "app"))
        from repository import Repository
        self.assertNotIn("session",
                         inspect.signature(Repository.cas_update).parameters)


if __name__ == "__main__":
    unittest.main()
