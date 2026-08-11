"""G2-08 验收测试：Snapshot/vintage、cutoff 语义与黄金 fixture。

基线：
  · 同一运行不混用事后数据（cutoff 后对象拒绝）
  · 离线 CI 可复跑（合成 fixture 同 seed 同字节）
BF-01 回填：cutoff 后对象、错 scope、漂移输入必拒绝。
"""
import unittest
import tempfile
import shutil
import os
import sys
import json
from datetime import datetime, timedelta, timezone

APP = os.path.join(os.path.dirname(__file__), "..", "app")
sys.path.insert(0, APP)

from repository import create_repository, Source, RawArtifact, FactRecord  # noqa: E402
from snapshot_service import SnapshotService  # noqa: E402


def _fact(session, sid, scope, value, acquired_at, art_id):
    import hashlib
    session.add(RawArtifact(id=art_id, schema_version="1.0", source_id="SRC_SSE",
                            sha256=hashlib.sha256(art_id.encode()).hexdigest(), bytes=1,
                            content_type="text/plain", acquired_at=acquired_at, version=1))
    session.commit()
    return FactRecord(id=sid, schema_version="1.0", artifact_id=art_id,
                      source_id="SRC_SSE", metric="REVENUE", value=value,
                      unit="CNY_M", period="2026-06", scope=scope,
                      basis="IFRS", vintage="v1", parser_version="p1", version=1)


class TestSnapshot(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self.repo = create_repository(os.path.join(self._tmp, "g2_08.sqlite3"))
        self.repo.create_all()
        self.s = self.repo.session()
        self.s.add(Source(id="SRC_SSE", schema_version="1.0", kind="PRIMARY",
                          name="上交所", status="ALLOWED", legal_basis="G2-08", version=1))
        self.s.commit()
        self.svc = SnapshotService(self.s)
        self.cutoff = datetime(2026, 8, 1, tzinfo=timezone.utc)

    def tearDown(self):
        self.s.close()
        self.repo.engine.dispose()
        shutil.rmtree(self._tmp, ignore_errors=True)

    # ── 基线：同一运行不混用事后数据（cutoff）──────────────────────
    def test_cutoff_rejects_late_data(self):
        snap = self.svc.create_snapshot("SNAP_A", self.cutoff, scope_set=["600089"])
        f_before = _fact(self.s, "FAC_PRE", "600089", "100",
                         self.cutoff - timedelta(days=1), "ART_PRE")
        self.svc.bind_fact("SNAP_A", f_before)  # cutoff 前 → 通过
        f_after = _fact(self.s, "FAC_POST", "600089", "100",
                        self.cutoff + timedelta(days=1), "ART_POST")
        with self.assertRaises(ValueError) as ctx:
            self.svc.bind_fact("SNAP_A", f_after)  # cutoff 后 → 拒绝
        self.assertIn("E-G2-08-001", str(ctx.exception))

    # ── BF-01：错 scope 必拒绝 ──────────────────────────────────────
    def test_wrong_scope_rejected(self):
        snap = self.svc.create_snapshot("SNAP_B", self.cutoff, scope_set=["600089"])
        f = _fact(self.s, "FAC_WRONG", "600188", "100",  # 错 scope
                  self.cutoff - timedelta(days=1), "ART_WRONG")
        with self.assertRaises(ValueError) as ctx:
            self.svc.bind_fact("SNAP_B", f)
        self.assertIn("E-G2-08-002", str(ctx.exception))

    # ── BF-01：漂移输入必拒绝（黄金 snapshot）───────────────────────
    def test_drift_input_rejected(self):
        snap = self.svc.create_snapshot("SNAP_G", self.cutoff, golden=True,
                                        scope_set=["600089"])
        f = _fact(self.s, "FAC_G1", "600089", "999999",  # 漂移值（非 fixture 声明）
                  self.cutoff - timedelta(days=1), "ART_G1")
        with self.assertRaises(ValueError) as ctx:
            self.svc.bind_fact("SNAP_G", f)
        self.assertIn("E-G2-08-003", str(ctx.exception))

    def test_golden_exact_input_accepted(self):
        from snapshot_service import _golden_hash
        snap = self.svc.create_snapshot("SNAP_G2", self.cutoff, golden=True,
                                        scope_set=["600089"])
        expect = _golden_hash("SNAP_G2", "FAC_G2")
        f = _fact(self.s, "FAC_G2", "600089", expect["value"],
                  self.cutoff - timedelta(days=1), "ART_G2")
        f.period = expect["period"]
        self.svc.bind_fact("SNAP_G2", f)  # 精确匹配 → 通过

    # ── 冻结后不可再绑定（writers 前置 cutoff_frozen 语义）──────────
    def test_frozen_rejects_new_bind(self):
        snap = self.svc.create_snapshot("SNAP_F", self.cutoff, scope_set=["600089"])
        self.svc.freeze("SNAP_F")
        f = _fact(self.s, "FAC_F", "600089", "100",
                  self.cutoff - timedelta(days=1), "ART_F")
        with self.assertRaises(ValueError) as ctx:
            self.svc.bind_fact("SNAP_F", f)
        self.assertIn("E-G2-08-005", str(ctx.exception))

    # ── OI-PF-031：未冻结对象参与下游计算必须失败关闭 ───────────────
    def test_unfrozen_rejected_for_computation(self):
        snap = self.svc.create_snapshot("SNAP_C", self.cutoff, scope_set=["600089"])
        with self.assertRaises(ValueError) as ctx:
            self.svc.snapshot_for_computation("SNAP_C")
        self.assertIn("E-G2-08-007", str(ctx.exception))
        # 冻结后即可供计算层读取
        self.svc.freeze("SNAP_C")
        got = self.svc.snapshot_for_computation("SNAP_C")
        self.assertTrue(got.frozen)

    # ── 离线 CI 可复跑（OI-PF-038：同 seed 同字节）─────────────────
    def test_fixture_reproducible_offline(self):
        """gen_fixtures 同 seed 产出同字节（黄金 fixture 集成，离线可复跑）。"""
        import subprocess
        d1 = os.path.join(self._tmp, "fx1")
        d2 = os.path.join(self._tmp, "fx2")
        for d in (d1, d2):
            r = subprocess.run(
                [sys.executable, os.path.join(os.path.dirname(__file__), "..",
                                              "tools", "gen_fixtures.py"),
                 "--out", d, "--seed", "0xC0FFEE"],
                capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr[-300:])
        files1 = sorted(os.listdir(d1))
        files2 = sorted(os.listdir(d2))
        self.assertEqual(files1, files2)
        for f in files1:
            b1 = open(os.path.join(d1, f), "rb").read()
            b2 = open(os.path.join(d2, f), "rb").read()
            self.assertEqual(b1, b2, f"fixture {f} 不可复现")
        # 六类负例须存在（cutoff 漂移 / 错 scope 是 BF-01 消费对象）
        all_text = "".join(open(os.path.join(d1, f), encoding="utf-8").read()
                           for f in files1 if f.endswith(".json"))
        self.assertIn("cutoff", all_text)
        self.assertIn("scope", all_text)


if __name__ == "__main__":
    unittest.main()
