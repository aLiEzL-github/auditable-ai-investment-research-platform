"""time_order.py —— 时序精度定义（G6C-执行计划.md 附.6，U 裁定 2026-08-11）。

裁定原文（逐字取用）：
  时间精度取「微秒时间戳 + 同刻序号」。冻结/可得时刻记录为 ISO 8601
  微秒（UTC，6 位小数）+ 同秒内递增事件序号；时序比较用 (timestamp, seq)
  字典序，不用显示值。该精度定义即 H-1/H-4 时序断言的比较基准，
  也适用于 H-4 每个基准输入的可得时刻。

G6A 的 F-2（首轮冻结时序）与 G6C 的 H-1/H-4 一律使用本模块的
MicroClock 与 cmp_micro —— 不得用「字符串比较时间戳」或「秒级比较」，
否则同一秒内的预登记与结果可得会被误判为同时（结构性盲区）。

允许注入时间源（tests 用固定序列），生产用 datetime.now(timezone.utc)。
"""
import datetime
from typing import Callable, Optional, Tuple


def micro_now() -> str:
    """ISO 8601 微秒 UTC：2026-08-12T11:45:00.123456Z（6 位小数）。"""
    return datetime.datetime.now(datetime.timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%S.%fZ")


def cmp_micro(a_ts: str, a_seq: int, b_ts: str, b_seq: int) -> int:
    """(timestamp, seq) 字典序比较。a < b → -1；a > b → +1；相等 → 0。

    注意：ISO 8601 固定宽度（含微秒 6 位小数），字典序 = 时序；
    同一秒内（ts 相等）由 seq 决出先后 —— 这是「同刻序号」的作用。
    """
    if a_ts != b_ts:
        return -1 if a_ts < b_ts else 1
    return (a_seq > b_seq) - (a_seq < b_seq)


class MicroClock:
    """(微秒时间戳, 同刻序号) 事件源。

    seq 在同一秒内单调递增，秒切换后归零 —— 保证 (ts, seq) 全局唯一
    且与真实先后一致。同一事件源的两次 tick 必可比较先后。
    """

    def __init__(self, time_source: Optional[Callable[[], str]] = None):
        self._src = time_source or micro_now
        self._last_ts: Optional[str] = None
        self._seq = 0

    def tick(self) -> Tuple[str, int]:
        ts = self._src()
        if ts != self._last_ts:
            self._last_ts = ts
            self._seq = 0
        else:
            self._seq += 1
        return ts, self._seq
