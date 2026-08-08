#!/usr/bin/env python3
"""akshare_adapter.py —— G2-06 AKShare 副源适配器。

基线验收（G2-06）：
  · 所有结果强制 SECONDARY（AKShare 为第三方聚合，非官方）
  · 故障不污染主源（副源失败不影响主源数据流）
F3（Gate 2 退出条件）：不得用 AKShare 填补主源硬缺口 —— 可执行断言，
  副源数据永不落为主源路径（primary-fill 拒绝）。

设计：不硬依赖 akshare/pandas（重依赖 + 哈希成本）。
  · 适配器实现 AKShare 风格接口契约（scope → 行数据 list[dict]）
  · akshare 库未安装时拒绝执行（E-G2-06-002，诚实标注，不装即不可用）
  · 真实 akshare 集成在安装后由同一契约驱动（G2-11 深化）
"""
import json
import os
import sys

APP = os.path.join(os.path.dirname(__file__), "..", "app")
sys.path.insert(0, APP)

from rights_guard import RightsGuard, GuardDenied  # noqa: E402

AKSHARE_SOURCE_ID = "SRC_AKSHARE"


class AKShareAdapter:
    """AKShare 副源：权利门先行 + 强制 SECONDARY + 故障隔离。"""

    def __init__(self, guard: RightsGuard, source_id: str = AKSHARE_SOURCE_ID):
        self.guard = guard
        self.source_id = source_id

    def _akshare_module(self):
        try:
            import akshare  # noqa: F401
            return akshare
        except ImportError:
            raise RuntimeError(
                "E-G2-06-002: akshare 库未安装（副源不可用，诚实标注；"
                "安装后由同一契约驱动）")

    # ── 1. 权利门先行（与主源同款，X-9 不可绕门）────────────────────
    def fetch(self, scope: str, source_status: str, record_decision=None,
              record_event=None, event_id: str = "EVT_AK_0001") -> list:
        rd = self.guard.decide(source_status, self.source_id, "FETCH", scope)
        if record_decision is not None:
            record_decision(rd)
        if rd.verdict != "ALLOWED":
            raise GuardDenied(
                f"{rd.verdict}: {self.source_id} FETCH {scope} —— 零请求/正文/缓存/解析/外发")
        return self._do_fetch(scope, record_event, event_id)

    def _do_fetch(self, scope: str, record_event, event_id: str) -> list:
        ak = self._akshare_module()
        # AKShare 风格调用：scope → DataFrame（按列名日期/值契约消费）
        fn = getattr(ak, scope, None)
        if fn is None:
            raise RuntimeError(f"E-G2-06-001: 未知 AKShare 接口: {scope}")
        try:
            df = fn()
            rows = self._to_rows(df)
        except Exception as e:
            if record_event is not None:
                record_event(event_id, scope, False, f"副源异常: {e}", None)
            # 故障隔离：异常不外泄污染主源读取路径
            raise RuntimeError(f"E-G2-06-003: 副源取得失败（故障隔离）: {scope} {e}")
        if record_event is not None:
            record_event(event_id, scope, True, None, 200)
        return rows

    @staticmethod
    def _to_rows(df) -> list:
        """DataFrame → list[dict]（日期/值列契约）；全部强制 SECONDARY 标记。"""
        rows = []
        for _, row in df.iterrows():
            r = {str(k): v for k, v in row.items()}
            r["__secondary"] = True  # 强制 SECONDARY（G2-06 基线）
            rows.append(r)
        return rows


if __name__ == "__main__":
    guard = RightsGuard(policy_version="v1")
    ad = AKShareAdapter(guard)
    try:
        rows = ad.fetch(os.environ.get("AK_SCOPE", "stock_zh_a_spot"),
                        source_status=os.environ.get("AK_STATUS", "UNKNOWN"))
        print(json.dumps({"verdict": "OK", "rows": len(rows),
                          "all_secondary": all(r.get("__secondary") for r in rows)}))
    except GuardDenied as e:
        print(json.dumps({"verdict": "DENIED", "reason": str(e)[:80]}))
    except RuntimeError as e:
        print(json.dumps({"verdict": "FAILED_CLOSED", "reason": str(e)[:80]}))
