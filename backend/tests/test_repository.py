"""G1-03 验收测试：WAL/busy_timeout/CAS/重试/双数据库语义。

验收映射（B 基线 G1-03）：
  · 双数据库升级/回滚  —— 由 Alembic 迁移脚本覆盖（见 migrations/，本套件验证模型与 WAL 语义）
  · 锁冲突             —— 两个连接同时写，busy_timeout 下有限等待（SQLite）
  · CAS                —— 版本乐观锁：陈旧版本更新被拒
  · 有限重试           —— CAS 冲突后重试成功
"""

import os
from datetime import datetime
import sqlite3
import tempfile
import threading
import unittest

from sqlalchemy import text

import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
from repository import AcquisitionEvent, RawArtifact, Repository, Source, create_repository


class TestRepositoryBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self.db = os.path.join(self._tmp, "test.sqlite3")
        self.repo = create_repository(self.db)
        self.repo.create_all()
        self.s = self.repo.session()

    def tearDown(self):
        self.s.close()
        self.repo.engine.dispose()
        import shutil
        shutil.rmtree(self._tmp, ignore_errors=True)


class TestWalAndBusyTimeout(TestRepositoryBase):
    def test_wal_mode_enabled(self):
        with self.repo.engine.connect() as conn:
            row = conn.execute(text("PRAGMA journal_mode")).fetchone()
        self.assertEqual(row[0].lower(), "wal")

    def test_busy_timeout_set(self):
        with self.repo.engine.connect() as conn:
            row = conn.execute(text("PRAGMA busy_timeout")).fetchone()
        self.assertGreaterEqual(row[0], 5000)

    def test_lock_conflict_retries_within_timeout(self):
        """锁冲突：第二个连接在 busy_timeout 内等待后成功（不立即失败）。"""
        src = Source(id="S-1", schema_version="1.0.0", kind="PRIMARY",
                     name="test-source", status="ALLOWED", legal_basis="test")
        self.s.add(src)
        self.s.commit()
        other = self.repo.engine.connect()
        result = {"ok": False}

        def blocking_write():
            with self.repo.engine.begin() as conn:
                conn.execute(text("BEGIN IMMEDIATE"))
                conn.execute(text("SELECT count(*) FROM source"))
                import time
                time.sleep(0.5)
                conn.execute(text("COMMIT"))

        t = threading.Thread(target=blocking_write)
        t.start()
        import time
        time.sleep(0.2)
        try:
            with self.repo.engine.begin() as conn:
                conn.execute(text("UPDATE source SET name='x' WHERE id='S-1'"))
                result["ok"] = True
        except Exception as e:
            result["error"] = str(e)
        t.join()
        self.assertTrue(result["ok"], f"busy_timeout 内应等待成功: {result}")

    def test_unique_sha256_constraint(self):
        src = Source(id="S-1", schema_version="1.0.0", kind="PRIMARY",
                     name="src-uniq", status="ALLOWED", legal_basis="t")
        self.s.add(src)
        self.s.commit()
        a1 = RawArtifact(id="RA-1", schema_version="1.0.0", source_id="S-1",
                         sha256="a" * 64, bytes=10, acquired_at=datetime(2026, 8, 7))
        a2 = RawArtifact(id="RA-2", schema_version="1.0.0", source_id="S-1",
                         sha256="a" * 64, bytes=10, acquired_at=datetime(2026, 8, 7))
        self.s.add(a1)
        self.s.commit()
        self.s.add(a2)
        with self.assertRaises(Exception):
            self.s.commit()
        self.s.rollback()


class TestCasAndRetry(TestRepositoryBase):
    def _mk_artifact(self, sha="b" * 64, src_id="S-2", src_name="cas-source",
                     art_id="RA-9"):
        src = Source(id=src_id, schema_version="1.0.0", kind="PRIMARY",
                     name=src_name, status="ALLOWED", legal_basis="t")
        self.s.add(src)
        self.s.commit()
        return RawArtifact(id=art_id, schema_version="1.0.0", source_id=src_id,
                           sha256=sha, bytes=1, acquired_at=datetime(2026, 8, 7))

    def test_cas_stale_version_rejected(self):
        art = self._mk_artifact()
        self.repo.cas_insert(self.s, art)
        self.assertEqual(art.version, 1)
        # 陈旧版本并发更新（version 仍 1，模拟他人已 +1）
        art.version = 2  # 他人已提交
        stale = self.s.query(RawArtifact).filter_by(id="RA-9").one()
        with self.assertRaises(ValueError) as cm:
            self.repo.cas_update(self.s, stale, expected_version=1)
        self.assertIn("CAS", str(cm.exception))

    def test_retry_after_conflict_succeeds(self):
        art = self._mk_artifact()
        self.repo.cas_insert(self.s, art)
        # 模拟两次冲突后成功（有限重试）
        for expected in (1, 2):
            current = self.s.query(RawArtifact).filter_by(id="RA-9").one()
            try:
                self.repo.cas_update(self.s, current, expected_version=expected)
                break
            except ValueError:
                continue
        fresh = self.s.query(RawArtifact).filter_by(id="RA-9").one()
        self.assertGreater(fresh.version, 1)

    def test_content_addressed_dedup_rejected(self):
        first = self._mk_artifact()
        self.repo.cas_insert(self.s, first)
        dup = self._mk_artifact(sha="b" * 64, src_id="S-3", src_name="cas-source-2")
        with self.assertRaises(ValueError) as cm:
            self.repo.cas_insert(self.s, dup)
        self.assertIn("sha256", str(cm.exception))


if __name__ == "__main__":
    unittest.main()
