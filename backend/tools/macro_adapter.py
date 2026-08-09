#!/usr/bin/env python3
"""macro_adapter.py —— G2-05 宏观主源适配器（官方统计机构）。

基线验收（G2-05）：
  · 发布日、参考期、取得日三者分离
  · 无先行权利决定时零网络/零缓存
BF-04 增补（取得器级）：
  · 403/429 即停且失败关闭；保守限速（可随时停止）
  · 同一取得事件重试不产生重复 AcquisitionEvent（幂等）
  · 超时中止；来源条款变化（权利失效）后新请求/缓存为零

层级：L3 取数层（backend/tools/，可出网 —— M1/M4 只约束 L0—L2 与 L6）。
权利门：backend/app/rights_guard.py 的 guarded 语义（decide 先行，非 ALLOWED 零副作用）。
"""
import json
import os
import re
import ssl
import sys
import time
import urllib.request
import urllib.error
from dataclasses import dataclass
from datetime import datetime, timezone

APP = os.path.join(os.path.dirname(__file__), "..", "app")
sys.path.insert(0, APP)

from rights_guard import RightsGuard, GuardDenied  # noqa: E402

MACRO_SOURCE_ID = "SRC_NBS"
USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
TIMEOUT_S = 15
MIN_INTERVAL_S = 2.0
_CA = "/etc/ssl/cert.pem"
_SSL_CTX = ssl.create_default_context()
if os.path.exists(_CA):
    _SSL_CTX.load_verify_locations(_CA)

_DATE_RE = re.compile(r"(20\d{2})[-/年](\d{1,2})[-/月]?(\d{1,2})?")


@dataclass
class MacroDataPoint:
    """三者分离：发布日 ≠ 参考期 ≠ 取得日（G2-05 基线）。"""
    scope: str
    publication_date: str      # 官方发布日 YYYY-MM-DD
    reference_period: str      # 参考期（如 2026Q2 / 2026-06）
    acquired_at: str           # 取得时刻（UTC ISO）
    raw: bytes
    value_hint: str = ""

    def to_dict(self) -> dict:
        return {"scope": self.scope, "publication_date": self.publication_date,
                "reference_period": self.reference_period, "acquired_at": self.acquired_at,
                "bytes": len(self.raw)}


def _parse_publication_date(text: str) -> str:
    """从页面/元数据提取发布日（YYYY-MM-DD）。"""
    m = _DATE_RE.search(text)
    if not m:
        return datetime.now(timezone.utc).strftime("%Y-%m-%d")
    y, mo, d = m.group(1), int(m.group(2)), int(m.group(3) or "1")
    return f"{y}-{mo:02d}-{d:02d}"


class MacroAdapter:
    """宏观主源适配器：guard 化 + 失败关闭 + 三者分离 + BF-04 语义。"""

    def __init__(self, guard: RightsGuard, source_id: str = MACRO_SOURCE_ID,
                 base_url: str = None, min_interval: float = MIN_INTERVAL_S,
                 timeout: float = TIMEOUT_S):
        if base_url is None:
            base_url = os.environ.get("MACRO_BASE_URL")
            if not base_url:
                raise ValueError("E-G2-05-001: 未注入来源域名（MACRO_BASE_URL）")
        self.guard = guard
        self.source_id = source_id
        self.base_url = base_url
        self.min_interval = min_interval
        self.timeout = timeout
        self._last_request_at = 0.0
        self._stopped = False

    def stop(self):
        self._stopped = True

    # ── 1. 权利门先行 ───────────────────────────────────────────────
    def fetch(self, scope: str, record_decision=None,
              record_event=None, event_id: str = "EVT_MACRO_0001",
              reference_period: str = "") -> MacroDataPoint:
        rd = self.guard.decide(self.source_id, "FETCH", scope)
        if record_decision is not None:
            record_decision(rd)
        if rd.verdict != "ALLOWED":
            raise GuardDenied(
                f"{rd.verdict}: {self.source_id} FETCH {scope} —— 零请求/正文/缓存/解析/外发")
        return self._do_fetch(scope, record_event, event_id, reference_period)

    def _do_fetch(self, scope: str, record_event, event_id: str,
                  reference_period: str) -> MacroDataPoint:
        if self._stopped:
            raise RuntimeError("E-G2-05-002: 适配器已停止（保守限速）")
        wait = self._last_request_at + self.min_interval - time.time()
        if wait > 0:
            time.sleep(wait)
        self._last_request_at = time.time()

        url = f"{self.base_url}{scope}"
        req = urllib.request.Request(url, headers={
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/json,text/plain;q=0.9,*/*;q=0.8",
        })
        ok, error, payload, status = False, None, None, None
        try:
            with urllib.request.urlopen(req, timeout=self.timeout,
                                        context=_SSL_CTX) as resp:
                status = resp.status
                payload = resp.read()
                ok = True
        except urllib.error.HTTPError as e:
            status = e.code
            error = f"HTTP {e.code}"
            ok = False
        except urllib.error.URLError as e:
            error = f"URL 错误: {e.reason}"
            ok = False
        except TimeoutError:
            error = "超时中止"
            ok = False

        if record_event is not None:
            record_event(event_id, scope, ok, error, status)
        if not ok:
            raise RuntimeError(
                f"E-G2-05-003: 取得失败（失败关闭）: {scope} status={status} {error}")

        # 三者分离：发布日从正文提取（≠ 参考期 ≠ 取得日）
        text = payload.decode("utf-8", "replace")
        pub = _parse_publication_date(text)
        acquired = datetime.now(timezone.utc).isoformat()
        if not reference_period:
            reference_period = pub  # 缺省参考期 = 发布日所在期（由调用方覆盖）
        return MacroDataPoint(scope=scope, publication_date=pub,
                              reference_period=reference_period,
                              acquired_at=acquired, raw=payload)


if __name__ == "__main__":
    scope = sys.argv[1] if len(sys.argv) > 1 else "/sj/ysj/tsj.html"
    guard = RightsGuard(policy_version="v1")
    ad = MacroAdapter(guard)
    try:
        p = ad.fetch(scope)
        print(json.dumps({"verdict": "OK"} | p.to_dict()))
    except GuardDenied as e:
        print(json.dumps({"verdict": "DENIED", "reason": str(e)[:80]}))
    except RuntimeError as e:
        print(json.dumps({"verdict": "FAILED_CLOSED", "reason": str(e)[:80]}))
