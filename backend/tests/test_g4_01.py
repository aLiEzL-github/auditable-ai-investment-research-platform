"""G4-01 验收测试：候选与内容哈希冻结（D-3 CAS + D-7 幂等）。

基线 B §7 G4-01：Candidate、manifest、全目录哈希；改动后哈希和候选身份变化。
D-3：同内容不同路径入库须得同一 id；改一字节须得不同 id。
D-7：同一输入连跑三次，产物哈希一致（不接受跑两次）。
"""
import os
import shutil
import sys
import tempfile
import unittest

APP = os.path.join(os.path.dirname(__file__), "..", "app")
sys.path.insert(0, APP)

from artifact_store import ArtifactStore
from publish_engine import (content_id, directory_hash, freeze_candidate,
                            freeze_manifest, freeze_object)


class TestContentAddressing(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self.store = ArtifactStore(os.path.join(self._tmp, "lib"))

    def tearDown(self):
        shutil.rmtree(self._tmp, ignore_errors=True)

    # ── D-3 验收 1：同内容不同路径入库须得同一 id ──────────────────
    def test_same_content_different_path_same_id(self):
        a = {"schema_version": "1.0.0", "kind": "evidence", "v": "1"}
        b = {"v": "1", "kind": "evidence", "schema_version": "1.0.0"}
        self.assertEqual(content_id(a), content_id(b))
        da = freeze_object(self.store, "evidence", a)
        db = freeze_object(self.store, "evidence", b)
        self.assertEqual(da, db, "同内容不同路径须得同一 id")

    # ── D-3 验收 2：改一字节须得不同 id ─────────────────────────────
    def test_one_byte_change_different_id(self):
        a = {"schema_version": "1.0.0", "kind": "evidence", "v": "1"}
        b = {"schema_version": "1.0.0", "kind": "evidence", "v": "2"}
        self.assertNotEqual(content_id(a), content_id(b))
        da = freeze_object(self.store, "evidence", a)
        db = freeze_object(self.store, "evidence", b)
        self.assertNotEqual(da, db)

    # ── G4-01：改动后候选身份变化 ──────────────────────────────────
    def test_candidate_identity_changes_with_content(self):
        c1 = freeze_candidate(self.store, {"payload": {"x": 1}})
        c2 = freeze_candidate(self.store, {"payload": {"x": 2}})
        self.assertNotEqual(c1, c2)
        self.assertEqual(len(c1), 64)

    # ── D-7：同一输入连跑三次，产物哈希一致 ────────────────────────
    def test_idempotent_three_runs(self):
        cand = {"payload": {"ticker": "FIX-01", "mode": "synthetic"}}
        ids = {freeze_candidate(self.store, cand) for _ in range(3)}
        self.assertEqual(len(ids), 1, "连跑三次须得同一候选 id")
        # manifest 同理
        m = {"id": "0" * 64, "schema_version": "1.0.0", "workflow": "w",
             "scope_id": "s", "current_key": "", "subject_root": "a" * 64,
             "parent": None, "directory_hash": "0" * 64,
             "code_version": "v1", "config_version": "v1",
             "objects": {"a" * 64: {"kind": "claim", "refs": []}}}
        mids = set()
        for _ in range(3):
            m2 = dict(m)
            m2["id"] = content_id(m2)
            mids.add(freeze_manifest(self.store, m2))
        self.assertEqual(len(mids), 1, "连跑三次须得同一 manifest id")

    # ── 全目录哈希：任一字节能改动哈希；幂等 ───────────────────────
    def test_directory_hash_changes_on_any_byte(self):
        d = tempfile.mkdtemp()
        try:
            sub = os.path.join(d, "sub")
            os.makedirs(sub)
            with open(os.path.join(d, "a.txt"), "w") as f:
                f.write("hello")
            with open(os.path.join(sub, "b.txt"), "w") as f:
                f.write("world")
            h1 = directory_hash(d)
            h2 = directory_hash(d)
            self.assertEqual(h1, h2, "同目录哈希须幂等")
            with open(os.path.join(d, "a.txt"), "a") as f:
                f.write("!")
            h3 = directory_hash(d)
            self.assertNotEqual(h1, h3, "改一字节全目录哈希须变化")
        finally:
            shutil.rmtree(d, ignore_errors=True)

    # ── 内容寻址与路径名无关（D-3 语义补充）────────────────────────
    def test_kind_name_does_not_change_digest(self):
        obj = {"kind": "x", "v": 1}
        d1 = freeze_object(self.store, "evidence", obj)
        d2 = freeze_object(self.store, "macro", obj)
        self.assertEqual(d1, d2, "对象 id 由内容决定，与登记名无关")


if __name__ == "__main__":
    unittest.main()
