#!/usr/bin/env python3
"""cninfo_adapter.py —— G2-04 官方 A 股披露源适配器（证监会指定披露平台，VD-15 #2 主源）。

主源切换（2026-08-08 裁定）：上交所自动化三层拦截（403 + WAF JS 挑战，不绕过）→
巨潮（证监会指定法定披露平台）为主源自动通道；上交所降人工导入通道。

API（判明实测 2026-08-09）：搜索/公告查询 POST 接口直接返回 JSON（无 JS 挑战）；
guard/失败关闭/限速/幂等框架复用 sse_adapter。域名运行时注入（CNINFO_BASE_URL）。
"""
import json
import os
import ssl
import sys
import time
import urllib.request
import urllib.parse
import urllib.error
from datetime import datetime, timezone

APP = os.path.join(os.path.dirname(__file__), "..", "app")
sys.path.insert(0, APP)

from rights_guard import RightsGuard, GuardDenied  # noqa: E402

CNINFO_SOURCE_ID = "SRC_CNINFO"
# 域名运行时注入（环境变量 CNINFO_BASE_URL）——代码不含来源特征串（L4 规则保持严格）
USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
TIMEOUT_S = 15
MIN_INTERVAL_S = 2.0
_CA = "/etc/ssl/cert.pem"
_SSL_CTX = ssl.create_default_context()
if os.path.exists(_CA):
    _SSL_CTX.load_verify_locations(_CA)


class CninfoAdapter:
    """巨潮披露源：guard 化 + 失败关闭 + 限速 + 幂等。"""

    def __init__(self, guard: RightsGuard, source_id: str = CNINFO_SOURCE_ID,
                 base_url: str = None, min_interval: float = MIN_INTERVAL_S,
                 timeout: float = TIMEOUT_S):
        if base_url is None:
            base_url = os.environ.get("CNINFO_BASE_URL")
            if not base_url:
                raise ValueError("E-G2-04-003: 未注入来源域名（CNINFO_BASE_URL）")
        self.guard = guard
        self.source_id = source_id
        self.base_url = base_url
        self.min_interval = min_interval
        self.timeout = timeout
        self._last_request_at = 0.0
        self._stopped = False

    def stop(self):
        self._stopped = True

    def _pace(self):
        if self._stopped:
            raise RuntimeError("E-G2-04-001: 适配器已停止（保守限速）")
        wait = self._last_request_at + self.min_interval - time.time()
        if wait > 0:
            time.sleep(wait)
        self._last_request_at = time.time()

    def _post(self, path: str, data: dict) -> dict:
        self._pace()
        req = urllib.request.Request(
            f"{self.base_url}{path}",
            data=urllib.parse.urlencode(data).encode(),
            headers={"User-Agent": USER_AGENT,
                     "Content-Type": "application/x-www-form-urlencoded",
                     "Referer": f"{self.base_url}/new/index",
                     "Origin": self.base_url})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout,
                                        context=_SSL_CTX) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            raise RuntimeError(f"E-G2-04-002: 巨潮取得失败（失败关闭）: HTTP {e.code}")
        except TimeoutError:
            raise RuntimeError("E-G2-04-002: 巨潮取得失败（失败关闭）: 超时中止")

    # ── 1. 权利门先行（UNKNOWN/PROHIBITED 零请求）──────────────────
    def resolve_org_id(self, code: str, source_status: str) -> str:
        rd = self.guard.decide(source_status, self.source_id, "FETCH", f"/search/{code}")
        if rd.verdict != "ALLOWED":
            raise GuardDenied(f"{rd.verdict}: {self.source_id} —— 零请求/正文/缓存/解析/外发")
        j = self._post("/new/information/topSearch/query",
                       {"keyWord": code, "maxNum": 5})
        for x in j:
            if x.get("code") == code:
                return x["orgId"]
        raise RuntimeError(f"E-G2-04-004: 未找到 {code} 的 orgId")

    def query_announcements(self, code: str, org_id: str, source_status: str,
                            date_from: str = "", date_to: str = "",
                            page: int = 1, page_size: int = 30) -> list:
        rd = self.guard.decide(source_status, self.source_id, "FETCH",
                               f"/announcements/{code}")
        if rd.verdict != "ALLOWED":
            raise GuardDenied(f"{rd.verdict}: {self.source_id} —— 零请求/正文/缓存/解析/外发")
        data = {"pageNum": page, "pageSize": page_size, "column": "sse",
                "tabName": "fulltext", "stock": f"{code},{org_id}",
                "searchkey": "", "secid": "", "plate": "", "category": "",
                "trade": "", "seDate": f"{date_from}~{date_to}"}
        j = self._post("/new/hisAnnouncement/query", data)
        out = []
        for a in j.get("announcements") or []:
            out.append({
                "title": a.get("announcementTitle", ""),
                "pdf_path": a.get("adjunctUrl", ""),
                "announcement_time": a.get("announcementTime"),
                "code": code,
                "locator": f"{self.base_url}/{a.get('adjunctUrl', '')}",
            })
        return out

    # ── 2. PDF 直链下载（guard 化 + 失败关闭）──────────────────────
    def download_pdf(self, pdf_path: str, source_status: str) -> bytes:
        rd = self.guard.decide(source_status, self.source_id, "FETCH", pdf_path)
        if rd.verdict != "ALLOWED":
            raise GuardDenied(f"{rd.verdict}: {self.source_id} —— 零请求")
        self._pace()
        pdf_base = os.environ.get("CNINFO_PDF_BASE_URL")
        if not pdf_base:
            raise RuntimeError("E-G2-04-003: 未注入 PDF 域名（CNINFO_PDF_BASE_URL）")
        url = f"{pdf_base}/{pdf_path}"
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout,
                                        context=_SSL_CTX) as r:
                if r.headers.get("Content-Type", "").startswith("text/html"):
                    raise RuntimeError("E-G2-04-005: 返回非 PDF（防护页，失败关闭）")
                return r.read()
        except urllib.error.HTTPError as e:
            raise RuntimeError(f"E-G2-04-002: PDF 取得失败（失败关闭）: HTTP {e.code}")
        except TimeoutError:
            raise RuntimeError("E-G2-04-002: PDF 取得失败（失败关闭）: 超时")


if __name__ == "__main__":
    code = sys.argv[1] if len(sys.argv) > 1 else "600089"
    guard = RightsGuard(policy_version="v1")
    ad = CninfoAdapter(guard)
    try:
        org = ad.resolve_org_id(code, source_status="ALLOWED")
        anns = ad.query_announcements(code, org, source_status="ALLOWED")
        print(json.dumps({"verdict": "OK", "org_id": org,
                          "announcements": len(anns),
                          "first": anns[0] if anns else None}, ensure_ascii=False))
    except GuardDenied as e:
        print(json.dumps({"verdict": "DENIED", "reason": str(e)[:80]}))
    except RuntimeError as e:
        print(json.dumps({"verdict": "FAILED_CLOSED", "reason": str(e)[:80]}))
