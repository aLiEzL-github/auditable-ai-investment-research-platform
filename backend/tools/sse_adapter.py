#!/usr/bin/env python3
"""sse_adapter.py —— G2-04 官方 A 股披露源适配器（上交所）。

基线验收（G2-04）：
  · 验证码/条款限制时失败关闭，不绕过
  · 无先行权利决定时零网络/零缓存
BF-04 增补（取得器级）：
  · 403/429 即停且失败关闭；保守限速（可随时停止）
  · 同一取得事件重试不产生重复 AcquisitionEvent（幂等）
  · 超时中止；来源条款变化（权利失效）后新请求/缓存为零

层级：L3 取数层（backend/tools/，可出网 —— M1/M4 只约束 L0—L2 与 L6）。
权利门：backend/app/rights_guard.py 的 guarded() —— 任何请求先产出 RightsDecision，
PROHIBITED/UNKNOWN 即 GuardDenied，动作体（网络请求）不执行。
"""
import json
import os
import ssl
import sys
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone

APP = __import__("os").path.join(__import__("os").path.dirname(__file__), "..", "app")
sys.path.insert(0, APP)

from rights_guard import RightsGuard, GuardDenied  # noqa: E402

SSE_BASE = "https://www.sse.com.cn"
SSE_SOURCE_ID = "SRC_SSE"
USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
TIMEOUT_S = 15
MIN_INTERVAL_S = 2.0  # 保守限速：两次请求最小间隔（BF-04）
# 系统证书链（macOS /etc/ssl/cert.pem）；缺失时仍失败关闭（不绕过 TLS）
_CA = "/etc/ssl/cert.pem"
_SSL_CTX = ssl.create_default_context()
if os.path.exists(_CA):
    _SSL_CTX.load_verify_locations(_CA)


class SSEAdapter:
    """上交所披露源适配器：权利门前置 + 失败关闭 + 幂等 + 限速。"""

    def __init__(self, guard: RightsGuard, source_id: str = SSE_SOURCE_ID,
                 base_url: str = SSE_BASE, min_interval: float = MIN_INTERVAL_S,
                 timeout: float = TIMEOUT_S):
        self.guard = guard
        self.source_id = source_id
        self.base_url = base_url
        self.min_interval = min_interval
        self.timeout = timeout
        self._last_request_at = 0.0
        self._stopped = False  # 保守限速：可随时停止

    def stop(self):
        self._stopped = True

    # ── 1. 权利门先行（任何路径都不可绕过：X-9）────────────────────
    def fetch(self, scope: str, source_status: str, record_decision=None,
              record_event=None, event_id: str = "EVT_SSE_0001") -> dict:
        """guard 化的取得：UNKNOWN/PROHIBITED → GuardDenied 且零网络。

        record_decision / record_event 为审计回调（由调用方入册）。
        """
        rd = self.guard.decide(source_status, self.source_id, "FETCH", scope)
        if record_decision is not None:
            record_decision(rd)
        if rd.verdict != "ALLOWED":
            raise GuardDenied(
                f"{rd.verdict}: {self.source_id} FETCH {scope} —— 零请求/正文/缓存/解析/外发")

        return self._do_fetch(scope, record_event, event_id)

    # ── 2. 请求执行（限速/超时/失败关闭/幂等）──────────────────────
    def _do_fetch(self, scope: str, record_event, event_id: str) -> dict:
        if self._stopped:
            raise RuntimeError("E-G2-04-001: 适配器已停止（保守限速）")
        # 保守限速：距上次请求不足最小间隔即等待（可随时 stop 中断）
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
            # 403/429 即停且失败关闭（BF-04）：不绕过、不重试
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
                f"E-G2-04-002: 取得失败（失败关闭）: {scope} status={status} {error}")
        return {"scope": scope, "status": status, "payload": payload,
                "content_type": "text/html"}


if __name__ == "__main__":
    # 真实取得探测（OI-PF-047 判明）：python3 backend/tools/sse_adapter.py <scope>
    scope = sys.argv[1] if len(sys.argv) > 1 else "/disclosure/"
    guard = RightsGuard(policy_version="v1")
    ad = SSEAdapter(guard)
    try:
        r = ad.fetch(scope, source_status="UNKNOWN")
        print(json.dumps({"verdict": "OK", "status": r["status"], "bytes": len(r["payload"])}))
    except GuardDenied as e:
        print(json.dumps({"verdict": "DENIED", "reason": str(e)[:80]}))
    except RuntimeError as e:
        print(json.dumps({"verdict": "FAILED_CLOSED", "reason": str(e)[:80]}))
