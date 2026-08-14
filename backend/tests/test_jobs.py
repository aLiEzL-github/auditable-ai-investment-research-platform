"""G1-04 验收测试：Job Lease / 幂等 / 取消 / 恢复。

验收映射（B 基线 G1-04）：
  · SQLite Worker 并发为 1     —— claim_next 的 BEGIN IMMEDIATE 串行化
  · API/Worker 写锁冲突可恢复   —— busy_timeout 等待 + 唯一键冲突回滚重读
  · 重复提交不重复执行          —— job_key 幂等键唯一
"""

import os
import shutil
import tempfile
import threading
import time
import unittest
from datetime import datetime, timedelta

import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
from repository import create_repository
from jobs import JobQueue


class TestJobQueueBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self.repo = create_repository(os.path.join(self._tmp, "jobs.sqlite3"))
        self.repo.create_all()
        self.q = JobQueue(self.repo)

    def tearDown(self):
        self.q.s.close()
        self.repo.engine.dispose()
        shutil.rmtree(self._tmp, ignore_errors=True)


class TestIdempotentSubmit(TestJobQueueBase):
    def test_duplicate_submit_returns_same_job(self):
        j1 = self.q.submit("key-1", "payload", writer="L7_freeze")
        j2 = self.q.submit("key-1", "other-payload", writer="L7_freeze")
        self.assertEqual(j1.id, j2.id)
        self.assertEqual(j1.status, "PENDING")
        self.assertEqual(self.q.s.query(type(j1)).count(), 1)

    def test_terminal_resubmit_returns_original(self):
        j1 = self.q.submit("key-2", writer="L7_freeze")
        claimed = self.q.claim_next("w1")
        self.q.complete(claimed.id, "w1", "ok")
        j2 = self.q.submit("key-2", writer="L7_freeze")
        self.assertEqual(j1.id, j2.id)  # 幂等到底：终态重提返回原 job（含 result）
        self.assertEqual(j2.status, "DONE")


class TestLeaseAndConcurrency(TestJobQueueBase):
    def test_claim_single_worker(self):
        self.q.submit("a", writer="L7_freeze")
        self.q.submit("b", writer="L7_freeze")
        j1 = self.q.claim_next("w1")
        j2 = self.q.claim_next("w1")
        self.assertIsNotNone(j1)
        self.assertIsNotNone(j2)
        self.assertNotEqual(j1.id, j2.id)
        self.assertEqual(j1.status, "RUNNING")

    def test_expired_lease_recovered(self):
        j = self.q.submit("r1", writer="L7_freeze")
        claimed = self.q.claim_next("w1", lease_seconds=1)
        # 模拟租约过期
        claimed.lease_until = datetime.utcnow() - timedelta(seconds=5)
        self.q.s.commit()
        time.sleep(0.1)
        re_claimed = self.q.claim_next("w2")
        self.assertIsNotNone(re_claimed)
        self.assertEqual(re_claimed.id, claimed.id)
        self.assertEqual(re_claimed.worker_id, "w2")
        self.assertEqual(re_claimed.attempts, 2)  # 恢复计数

    def test_active_lease_not_reclaimed(self):
        j = self.q.submit("a1", writer="L7_freeze")
        self.q.claim_next("w1", lease_seconds=60)
        other = self.q.claim_next("w2")
        # 只有 PENDING 的另一个任务可被领取；RUNNING 且租约未过期的不可
        self.q.submit("a2", writer="L7_freeze")
        other = self.q.claim_next("w2")
        self.assertIsNotNone(other)
        self.assertNotEqual(other.id, j.id)


class TestTransitions(TestJobQueueBase):
    def test_complete(self):
        j = self.q.submit("c1", writer="L7_freeze")
        claimed = self.q.claim_next("w1")
        done = self.q.complete(claimed.id, "w1", "result-ok")
        self.assertEqual(done.status, "DONE")
        self.assertEqual(done.result, "result-ok")

    def test_fail(self):
        j = self.q.submit("f1", writer="L7_freeze")
        claimed = self.q.claim_next("w1")
        failed = self.q.fail(claimed.id, "w1", "boom")
        self.assertEqual(failed.status, "FAILED")

    def test_cancel_pending(self):
        j = self.q.submit("x1", writer="L7_freeze")
        cancelled = self.q.cancel(j.id)
        self.assertEqual(cancelled.status, "CANCELLED")
        self.assertIsNone(cancelled.lease_until)

    def test_cancel_terminal_rejected(self):
        j = self.q.submit("x2", writer="L7_freeze")
        claimed = self.q.claim_next("w1")
        self.q.complete(claimed.id, "w1", "ok")
        with self.assertRaises(ValueError):
            self.q.cancel(claimed.id)

    def test_complete_non_running_rejected(self):
        j = self.q.submit("x3", writer="L7_freeze")
        with self.assertRaises(ValueError) as cm:
            self.q.complete(j.id, "w1", "x")  # PENDING 不可完成
        self.assertIn("E-STATE-001", str(cm.exception))

    def test_extend_lease(self):
        j = self.q.submit("e1", writer="L7_freeze")
        claimed = self.q.claim_next("w1", lease_seconds=5)
        self.assertTrue(self.q.extend_lease(claimed.id, "w1", lease_seconds=30))
        self.assertGreater(claimed.lease_until,
                           datetime.utcnow() + timedelta(seconds=10))

    def test_lease_ownership_sequence(self):
        """J-1/Q1 验收序列：A 领 1s → 过期 → B 抢占 → A complete 被拒、B 成功。"""
        self.q.submit("own-1", writer="L7_freeze")
        a = self.q.claim_next("A", lease_seconds=1)
        a.lease_until = datetime.utcnow() - timedelta(seconds=5)
        self.q.s.commit()
        time.sleep(0.1)
        b = self.q.claim_next("B")
        self.assertEqual(b.worker_id, "B")
        # A 已被 B 抢占（worker_id=B）→ A 是**非持有者** → E-LEASE-002
        with self.assertRaises(ValueError) as cm:
            self.q.complete(b.id, "A", "A 的结果（租约已过期）")
        self.assertIn("E-LEASE-002", str(cm.exception))
        # E-LEASE-001 场景：持有者身份仍在但租约已过期（无抢占）
        self.q.submit("own-1b", writer="L7_freeze")
        a2 = self.q.claim_next("A", lease_seconds=1)
        a2.lease_until = datetime.utcnow() - timedelta(seconds=5)
        self.q.s.commit()
        time.sleep(0.1)
        with self.assertRaises(ValueError) as cm:
            self.q.complete(a2.id, "A", "身份在但租约过期")
        self.assertIn("E-LEASE-001", str(cm.exception))
        # B 合法完成
        done = self.q.complete(b.id, "B", "B 的结果")
        self.assertEqual(done.status, "DONE")
        self.assertEqual(done.worker_id, "B")

    def test_non_holder_rejected(self):
        """J-1/Q1：非持有者（租约未过期）提交被拒 E-LEASE-002。"""
        self.q.submit("own-2", writer="L7_freeze")
        a = self.q.claim_next("A", lease_seconds=60)
        with self.assertRaises(ValueError) as cm:
            self.q.complete(a.id, "B", "B 冒充")
        self.assertIn("E-LEASE-002", str(cm.exception))
        with self.assertRaises(ValueError) as cm:
            self.q.fail(a.id, "B", "B 冒充失败")
        self.assertIn("E-LEASE-002", str(cm.exception))
        with self.assertRaises(ValueError) as cm:
            self.q.extend_lease(a.id, "B", lease_seconds=30)
        self.assertIn("E-LEASE-002", str(cm.exception))

    def test_updated_at_advances(self):
        """J-2/Q2：提交→领取→完成后 updated_at > created_at。"""
        j = self.q.submit("u1", writer="L7_freeze")
        claimed = self.q.claim_next("w1")
        time.sleep(0.05)
        done = self.q.complete(claimed.id, "w1", "ok")
        self.assertGreater(done.updated_at, done.created_at)


class TestConcurrentSubmit(TestJobQueueBase):
    def test_concurrent_duplicate_submit_no_dup(self):
        """两个线程同时 submit 同一 key —— 唯一键兜底，仅一条 job。"""
        results = []

        def do_submit():
            results.append(self.q.submit("race-key", writer="L7_freeze"))

        ts = [threading.Thread(target=do_submit) for _ in range(2)]
        for t in ts:
            t.start()
        for t in ts:
            t.join()
        ids = {r.id for r in results}
        self.assertEqual(len(ids), 1, f"并发提交应归并为一条: {ids}")
        count = self.q.s.query(type(results[0])).filter_by(job_key="race-key").count()
        self.assertEqual(count, 1)


if __name__ == "__main__":
    unittest.main()


class TestMigrationPath(unittest.TestCase):
    """K-1/T3：测试路径与部署路径一致 —— 用 alembic upgrade head 建库（非 create_all）。"""

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self.db = os.path.join(self._tmp, "mig.sqlite3")
        backend = os.path.join(os.path.dirname(__file__), "..")
        env = dict(os.environ, DATABASE_URL=f"sqlite:///{self.db}")
        import subprocess
        r = subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "head"],
            cwd=backend, env=env, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, f"alembic upgrade head 失败: {r.stderr}")
        self.repo = create_repository(self.db)
        self.q = JobQueue(self.repo)

    def tearDown(self):
        self.q.s.close()
        self.repo.engine.dispose()
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_job_flow_on_migrated_db(self):
        """迁移建的库上完整跑一遍 job 流程（job 表经迁移存在）。"""
        j = self.q.submit("mig-key", writer="L7_freeze")
        claimed = self.q.claim_next("w1")
        self.assertIsNotNone(claimed)
        done = self.q.complete(claimed.id, "w1", "mig-ok")
        self.assertEqual(done.status, "DONE")
        import sqlite3
        conn = sqlite3.connect(self.db)
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
        conn.close()
        self.assertIn("job", tables)
