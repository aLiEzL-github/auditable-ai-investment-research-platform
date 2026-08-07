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
        j1 = self.q.submit("key-1", "payload")
        j2 = self.q.submit("key-1", "other-payload")
        self.assertEqual(j1.id, j2.id)
        self.assertEqual(j1.status, "PENDING")
        self.assertEqual(self.q.s.query(type(j1)).count(), 1)

    def test_terminal_resubmit_returns_original(self):
        j1 = self.q.submit("key-2")
        claimed = self.q.claim_next("w1")
        self.q.complete(claimed.id, "ok")
        j2 = self.q.submit("key-2")
        self.assertEqual(j1.id, j2.id)  # 幂等到底：终态重提返回原 job（含 result）
        self.assertEqual(j2.status, "DONE")


class TestLeaseAndConcurrency(TestJobQueueBase):
    def test_claim_single_worker(self):
        self.q.submit("a")
        self.q.submit("b")
        j1 = self.q.claim_next("w1")
        j2 = self.q.claim_next("w1")
        self.assertIsNotNone(j1)
        self.assertIsNotNone(j2)
        self.assertNotEqual(j1.id, j2.id)
        self.assertEqual(j1.status, "RUNNING")

    def test_expired_lease_recovered(self):
        j = self.q.submit("r1")
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
        j = self.q.submit("a1")
        self.q.claim_next("w1", lease_seconds=60)
        other = self.q.claim_next("w2")
        # 只有 PENDING 的另一个任务可被领取；RUNNING 且租约未过期的不可
        self.q.submit("a2")
        other = self.q.claim_next("w2")
        self.assertIsNotNone(other)
        self.assertNotEqual(other.id, j.id)


class TestTransitions(TestJobQueueBase):
    def test_complete(self):
        j = self.q.submit("c1")
        claimed = self.q.claim_next("w1")
        done = self.q.complete(claimed.id, "result-ok")
        self.assertEqual(done.status, "DONE")
        self.assertEqual(done.result, "result-ok")

    def test_fail(self):
        j = self.q.submit("f1")
        claimed = self.q.claim_next("w1")
        failed = self.q.fail(claimed.id, "boom")
        self.assertEqual(failed.status, "FAILED")

    def test_cancel_pending(self):
        j = self.q.submit("x1")
        cancelled = self.q.cancel(j.id)
        self.assertEqual(cancelled.status, "CANCELLED")
        self.assertIsNone(cancelled.lease_until)

    def test_cancel_terminal_rejected(self):
        j = self.q.submit("x2")
        claimed = self.q.claim_next("w1")
        self.q.complete(claimed.id, "ok")
        with self.assertRaises(ValueError):
            self.q.cancel(claimed.id)

    def test_complete_non_running_rejected(self):
        j = self.q.submit("x3")
        with self.assertRaises(ValueError) as cm:
            self.q.complete(j.id, "x")  # PENDING 不可完成
        self.assertIn("E-STATE-001", str(cm.exception))

    def test_extend_lease(self):
        j = self.q.submit("e1")
        claimed = self.q.claim_next("w1", lease_seconds=5)
        self.assertTrue(self.q.extend_lease(claimed.id, lease_seconds=30))
        self.assertGreater(claimed.lease_until,
                           datetime.utcnow() + timedelta(seconds=10))


class TestConcurrentSubmit(TestJobQueueBase):
    def test_concurrent_duplicate_submit_no_dup(self):
        """两个线程同时 submit 同一 key —— 唯一键兜底，仅一条 job。"""
        results = []

        def do_submit():
            results.append(self.q.submit("race-key"))

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
