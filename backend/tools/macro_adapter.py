#!/usr/bin/env python3
"""macro_adapter.py —— G2-05 宏观主源适配器（官方统计机构）。

基线验收（G2-05）：
  · 发布日、参考期、取得日三者分离
  · 无先行权利决定时零网络/零缓存
BF-04 增补（取得器级）：
  · 403/429 即停且失败关闭；保守限速（可随时停止）
  · 同一取得事件重试不产生重复 AcquisitionEvent（幂等）
  · 超时中止；来源条款变化（权利失效）后新请求/缓存为零
G7-02 增补（首轮审查 + 收口）：
  · 生产默认严格模式（strict_origin 默认 True）只允许官方域名
    https://www.stats.gov.cn，scope 只允许官方数据发布页路径形状（收口收紧为
    实际发布页形状 `^/sj/zxfbhjd/\d{6}/t\d{8}_\d+\.html$`，与 app 层 service
    行为一致）；禁止任意 scheme/host、userinfo、端口、query/fragment、绝对
    URL、路径穿越 —— 非法目标在出网前失败关闭；非 strict 仅供显式测试注入，
    不留默认可用的任意 origin 路径；
  · 无日期不回退当前日期：publication_date 在路径模式下从官方路径
    tYYYYMMDD 片段确定，缺省即失败关闭；日期片段经真实 calendar date 校验，
    不只看 month/day 范围；正文首日期仅保留为 G2-05 兼容模式；
  · 响应体固定上限：Content-Length 声明超限或实际读取超限均失败关闭。

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
from datetime import date, datetime, timezone

APP = os.path.join(os.path.dirname(__file__), "..", "app")
sys.path.insert(0, APP)

from rights_guard import RightsGuard, GuardDenied  # noqa: E402

MACRO_SOURCE_ID = "SRC_NBS"
# 生产 CLI 只允许官方域名（G7-02 首轮审查）：无用户输入可改写。
NBS_PRODUCTION_HOST = "www.stats.gov.cn"
NBS_PRODUCTION_BASE_URL = f"https://{NBS_PRODUCTION_HOST}"
# 官方数据发布页路径形状（G7-02 收口：收紧为本任务实际发布页形状）：
# /sj/zxfbhjd/YYYYMM/tYYYYMMDD_<id>.html。字符集天然排除 `@` `:` `?` `#`
# `\` 空白，结构上禁 userinfo/端口/query/fragment/绝对 URL/路径穿越。
NBS_SCOPE_RE = re.compile(r"^/sj/zxfbhjd/\d{6}/t\d{8}_\d+\.html$")
# 官方路径发布日期片段：/202607/t20260716_xxx.html → 2026-07-16。
NBS_PATH_DATE_RE = re.compile(r"t(20\d{2})(\d{2})(\d{2})")
USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
TIMEOUT_S = 15
MIN_INTERVAL_S = 2.0
MAX_BODY_BYTES = 2 * 1024 * 1024  # 响应体固定上限（G7-02 首轮审查）
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
    source_url: str = ""       # 实际取得 URL（manifest/source_url 绑定）

    def to_dict(self) -> dict:
        return {"scope": self.scope, "publication_date": self.publication_date,
                "reference_period": self.reference_period, "acquired_at": self.acquired_at,
                "bytes": len(self.raw), "source_url": self.source_url}


def _parse_publication_date(text: str) -> str:
    """从页面/元数据提取发布日（YYYY-MM-DD）。

    G7-02 首轮审查：无日期必须失败关闭，**不回退当前日期**（墙钟回退会让
    发布日在缺页时被伪装成「当天」）。G2-05 兼容模式下正文首日期保留为该
    模式的解析来源，但缺日期同样失败关闭。
    """
    m = _DATE_RE.search(text)
    if not m:
        raise ValueError(
            "E-G2-05-004: 无法确定发布日（无日期回退已禁止）—— 失败关闭")
    y, mo, d = m.group(1), int(m.group(2)), int(m.group(3) or "1")
    try:
        date(int(y), mo, d)
    except ValueError:
        raise ValueError(
            "E-G2-05-004: 发布日非法 calendar date —— 失败关闭")
    return f"{y}-{mo:02d}-{d:02d}"


def _publication_date_from_url(url: str) -> str:
    """从官方路径的发布日期片段确定发布日：tYYYYMMDD → YYYY-MM-DD。

    publication_date 与 source_url 绑定：缺路径日期即失败关闭，绝不回退
    正文首日期或墙钟（G7-02 首轮审查）。
    """
    m = NBS_PATH_DATE_RE.search(url)
    if not m:
        raise RuntimeError(
            "E-G7-02-033: 官方路径无发布日期片段（tYYYYMMDD）—— 失败关闭")
    y, mo, d = m.group(1), int(m.group(2)), int(m.group(3))
    try:
        date(int(y), int(mo), int(d))
    except ValueError:
        raise RuntimeError(
            "E-G7-02-033: 路径发布日期非法 calendar date —— 失败关闭")
    return f"{y}-{mo:02d}-{d:02d}"


def validate_nbs_target(base_url: str, scope: str, *, strict_origin: bool) -> str:
    """构造并校验目标 URL（出网前失败关闭）。

    · strict_origin=True（生产 CLI）：base_url 必须恰好是官方域名
      https://www.stats.gov.cn，scope 必须匹配官方数据发布页路径形状；
    · 非严格模式（G2-05/测试注入适配器）：base_url 可由注入方给出，但 scope
      仍执行通用最小防御（禁绝对 URL/userinfo/端口/query/fragment/穿越）；
    · 任何非法形态在出网前抛 ValueError —— 零请求。
    """
    if strict_origin and base_url != NBS_PRODUCTION_BASE_URL:
        raise ValueError(
            f"E-G7-02-033: 生产仅允许 {NBS_PRODUCTION_BASE_URL} —— "
            "任意 scheme/host 拒绝（失败关闭）")
    if scope.startswith("//") or scope.startswith("http://") \
            or scope.startswith("https://"):
        raise ValueError("E-G7-02-033: scope 不得为绝对 URL —— 失败关闭")
    if ".." in scope.split("/"):
        raise ValueError("E-G7-02-033: scope 含路径穿越（..）—— 失败关闭")
    if "//" in scope or "\\" in scope or any(c in scope for c in "?#@ \t\r\n"):
        raise ValueError(
            "E-G7-02-033: scope 含非法字符（userinfo/query/fragment/端口/空白）"
            " —— 失败关闭")
    if strict_origin and not NBS_SCOPE_RE.fullmatch(scope):
        raise ValueError(
            "E-G7-02-033: scope 非 NBS 官方数据发布页路径形状 —— 失败关闭")
    return f"{base_url}{scope}"


class MacroAdapter:
    """宏观主源适配器：guard 化 + 失败关闭 + 三者分离 + BF-04 语义。"""

    def __init__(self, guard: RightsGuard, source_id: str = MACRO_SOURCE_ID,
                 base_url: str = None, min_interval: float = MIN_INTERVAL_S,
                 timeout: float = TIMEOUT_S, *, strict_origin: bool = True,
                 publication_date_mode: str = "body",
                 max_body_bytes: int = MAX_BODY_BYTES):
        if base_url is None:
            base_url = os.environ.get("MACRO_BASE_URL")
            if not base_url:
                raise ValueError("E-G2-05-001: 未注入来源域名（MACRO_BASE_URL）")
        self.guard = guard
        self.source_id = source_id
        self.base_url = base_url
        self.min_interval = min_interval
        self.timeout = timeout
        # G7-02 收口：生产默认安全 —— strict_origin 默认 True，无显式测试
        # 配置时不得以任意 MACRO_BASE_URL 出网；非 strict 仅供显式测试注入。
        self.strict_origin = strict_origin
        if publication_date_mode not in ("path", "body"):
            raise ValueError(f"E-G2-05-005: 非法 publication_date_mode"
                             f"（{publication_date_mode!r}）")
        self.publication_date_mode = publication_date_mode
        self.max_body_bytes = max_body_bytes
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
        # G7-02 首轮审查：目标 URL 出网前校验（非法形态零请求，先于限速）。
        url = validate_nbs_target(self.base_url, scope,
                                  strict_origin=self.strict_origin)
        wait = self._last_request_at + self.min_interval - time.time()
        if wait > 0:
            time.sleep(wait)
        self._last_request_at = time.time()

        req = urllib.request.Request(url, headers={
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/json,text/plain;q=0.9,*/*;q=0.8",
        })
        ok, error, payload, status = False, None, None, None
        try:
            with urllib.request.urlopen(req, timeout=self.timeout,
                                        context=_SSL_CTX) as resp:
                status = resp.status
                # G7-02 首轮审查：Content-Length 声明超限即失败关闭。
                length = None
                try:
                    length = int(resp.headers.get("Content-Length", ""))
                except (TypeError, ValueError):
                    length = None
                if length is not None and length > self.max_body_bytes:
                    raise RuntimeError(
                        f"E-G7-02-034: Content-Length {length} 超响应体上限"
                        f" {self.max_body_bytes} —— 失败关闭")
                chunks, total = [], 0
                while True:
                    requested = min(64 * 1024,
                                    self.max_body_bytes - total + 1)
                    chunk = resp.read(requested)
                    if not chunk:
                        break
                    chunks.append(chunk)
                    total += len(chunk)
                    # G7-02 首轮审查：循环读到 EOF；分块/短读累计超限同样拒绝。
                    if total > self.max_body_bytes:
                        raise RuntimeError(
                            f"E-G7-02-034: 实际读取 {total} 超响应体上限"
                            f" {self.max_body_bytes} —— 失败关闭")
                    # HTTPResponse.read(n) 的短读表示本次响应已到 EOF；只有读满
                    # 请求块时才继续，以兼容受控响应适配器并避免重复消费同一块。
                    if len(chunk) < requested:
                        break
                payload = b"".join(chunks)
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

        # 三者分离：发布日（路径/正文，缺日期失败关闭）≠ 参考期 ≠ 取得日。
        if self.publication_date_mode == "path":
            pub = _publication_date_from_url(url)
        else:
            text = payload.decode("utf-8", "replace")
            pub = _parse_publication_date(text)
        acquired = datetime.now(timezone.utc).isoformat()
        if not reference_period:
            reference_period = pub  # 缺省参考期 = 发布日所在期（由调用方覆盖）
        return MacroDataPoint(scope=scope, publication_date=pub,
                              reference_period=reference_period,
                              acquired_at=acquired, raw=payload,
                              source_url=url)


if __name__ == "__main__":
    scope = sys.argv[1] if len(sys.argv) > 1 else \
        "/sj/zxfbhjd/202607/t20260716_1.html"
    guard = RightsGuard(policy_version="v1")
    # strict_origin 默认 True：任意 MACRO_BASE_URL / 非官方路径均在出网前失败关闭。
    ad = MacroAdapter(guard)
    try:
        p = ad.fetch(scope)
        print(json.dumps({"verdict": "OK"} | p.to_dict()))
    except GuardDenied as e:
        print(json.dumps({"verdict": "DENIED", "reason": str(e)[:80]}))
    except RuntimeError as e:
        print(json.dumps({"verdict": "FAILED_CLOSED", "reason": str(e)[:80]}))
