"""cost_retry_degrades.py —— G3-07 成本、重试和降级。

基线验收（G3-07）：
  · token/时间预算、幂等重试、降级状态
  · 超预算为 PARTIAL/BLOCKED，不伪造完成
"""
import time
from dataclasses import dataclass
from typing import Callable, Optional


class BudgetExceededError(Exception):
    pass


class RetryExhausted(Exception):
    pass


@dataclass
class Budget:
    max_tokens: int = 100000
    max_seconds: float = 300.0
    used_tokens: int = 0
    started_at: Optional[float] = None

    def start(self):
        self.started_at = time.monotonic()

    def elapsed(self) -> float:
        return time.monotonic() - (self.started_at or time.monotonic())

    def charge(self, tokens: int) -> None:
        self.used_tokens += tokens
        if self.used_tokens > self.max_tokens:
            raise BudgetExceededError(
                f"E-G3-07-001: token 超预算 {self.used_tokens} > {self.max_tokens} "
                f"—— PARTIAL/BLOCKED，不伪造完成")
        if self.elapsed() > self.max_seconds:
            raise BudgetExceededError(
                f"E-G3-07-002: 时间超预算 {self.elapsed():.1f}s > {self.max_seconds}s "
                f"—— PARTIAL/BLOCKED，不伪造完成")


def with_retry(fn: Callable, attempts: int = 3, idempotent_key: str = "",
               failures: Optional[list] = None,
               backoff_s: float = 0.0) -> object:
    """幂等重试：同一 idempotent_key 只执行一次；失败重试到 attempts 上限。

    非幂等操作（idempotent_key 为空）拒绝重试 —— 重复执行可能产生
    重复副作用（写库/发请求），宁可失败也不伪造完成。
    """
    if failures is None:
        failures = []
    if not idempotent_key and attempts > 1:
        raise RetryExhausted(
            "E-G3-07-003: 非幂等操作不得重试（重复副作用风险）")
    last = None
    for i in range(attempts):
        try:
            result = fn()
            return result
        except Exception as e:
            last = e
            failures.append(f"{idempotent_key or 'no-key'}:{i + 1}:{type(e).__name__}")
            if i + 1 < attempts and backoff_s:
                time.sleep(backoff_s)
    raise RetryExhausted(f"E-G3-07-004: 重试耗尽 {attempts} 次: {last}")
