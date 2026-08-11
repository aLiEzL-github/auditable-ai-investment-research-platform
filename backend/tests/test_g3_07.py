"""G3-07 验收测试：成本、重试和降级。

基线：
  · token/时间预算（超预算 → PARTIAL/BLOCKED，不伪造完成）
  · 幂等重试（同一 key 只执行一次；失败重试到上限）
  · 非幂等操作拒绝重试（重复副作用风险）
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
APP = os.path.join(os.path.dirname(__file__), "..", "app")
sys.path.insert(0, APP)

from cost_retry_degrades import (  # noqa: E402
    Budget, BudgetExceededError, with_retry, RetryExhausted,
)


class TestBudget(unittest.TestCase):
    def test_token_budget_exceeded(self):
        b = Budget(max_tokens=100)
        b.start()
        b.charge(60)
        with self.assertRaises(BudgetExceededError) as ctx:
            b.charge(50)
        self.assertIn("E-G3-07-001", str(ctx.exception))

    def test_time_budget_exceeded(self):
        b = Budget(max_seconds=0.01)
        b.start()
        import time
        time.sleep(0.02)
        with self.assertRaises(BudgetExceededError) as ctx:
            b.charge(1)
        self.assertIn("E-G3-07-002", str(ctx.exception))

    def test_ok_within_budget(self):
        b = Budget(max_tokens=100)
        b.start()
        b.charge(50)
        b.charge(50)
        self.assertEqual(b.used_tokens, 100)


class TestRetry(unittest.TestCase):
    def test_idempotent_retry_succeeds(self):
        """幂等重试：前两次失败第三次成功。"""
        calls = []
        def fn():
            calls.append(1)
            if len(calls) < 3:
                raise RuntimeError("temporary")
            return "ok"
        result = with_retry(fn, attempts=3, idempotent_key="job-1")
        self.assertEqual(result, "ok")
        self.assertEqual(len(calls), 3)

    def test_retry_exhausted(self):
        def fn():
            raise RuntimeError("always")
        failures = []
        with self.assertRaises(RetryExhausted) as ctx:
            with_retry(fn, attempts=3, idempotent_key="job-2",
                       failures=failures)
        self.assertIn("E-G3-07-004", str(ctx.exception))
        self.assertEqual(len(failures), 3)

    def test_non_idempotent_refuses_retry(self):
        """非幂等操作不得重试。"""
        def fn():
            return "x"
        with self.assertRaises(RetryExhausted) as ctx:
            with_retry(fn, attempts=3, idempotent_key="")
        self.assertIn("E-G3-07-003", str(ctx.exception))

    def test_single_attempt_allowed_no_key(self):
        def fn():
            return "ok"
        self.assertEqual(with_retry(fn, attempts=1, idempotent_key=""), "ok")


if __name__ == "__main__":
    unittest.main()
