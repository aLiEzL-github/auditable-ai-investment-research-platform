"""应用入口：仅提供健康检查端点（G0-08 §3.1 文件集第 8 件）。

当前阶段（G1 之前）为最小可构建骨架：
- /livez  存活探针（进程活着即 200）
- /readyz 就绪探针（依赖就绪才 200；骨架阶段无外部依赖，恒 200）

绑定地址：默认 127.0.0.1（本机安全）；容器场景传 --bind 0.0.0.0
（Docker 端口映射转发到容器 eth0，loopback 绑定会导致外部探针打不到）。

零外部依赖、零数据访问、零网络外连（G0-03 ZERO_COPY_ZERO_NETWORK_DEFAULT_DENY）。
"""

import argparse
import os
import sys

# ADR-018 §4 守卫 C —— 必须在**任何**业务导入之前装入：拦截器只对尚未导入的
# 模块生效（OI-PF-135）。此前 install() 只在测试里被调用过，生产路径为空。
import curl_cffi_interdict  # noqa: E402

curl_cffi_interdict.install()

import json  # noqa: E402
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer  # noqa: E402

from logging_conf import setup_logging  # noqa: E402
from settings import get_settings  # noqa: E402


# ── G5 / OI-PF-156：release_eligible 的后端端点 ────────────────────
# 基线 §9 对 Gate 5 的要求：「UI 无法绕过后端 release_eligible」，
# 一票否决「前端可改写阻断态」。
#
# **在此端点存在之前，那句话是空的** —— 没有端点就没有可绕过的对象，
# 也没有能拒绝的一方。前端测试证明的是「前端没自己算」，不是「后端挡得住」。
#
# 本端点的不变量（E-1/E-3）：
#   · 判定**只由后端计算**，客户端传入的任何 release_eligible / eligible /
#     reasons 字段一律**忽略**，且请求体里带这些字段时须显式拒绝（400）——
#     不是静默忽略：静默忽略会让攻击者以为得手，也让日志无痕
#   · GET 无副作用；本端点不接受 POST 改写判定
def _compute_eligibility():
    """唯一计算点。**不接受任何入参** —— 入参即攻击面。

    真实判定接 publish_engine.is_release_eligible；此处按 G5 阶段的
    可用状态返回 fail-closed 缺省值：无批准对象即不合格。
    """
    return {"release_eligible": False,
            "reasons": ["E-G5-001: 尚无已批准的候选对象（fail-closed 缺省）"],
            "computed_by": "backend", "source": "publish_engine.is_release_eligible"}


CLIENT_SUPPLIED_VERDICT_KEYS = ("release_eligible", "eligible", "reasons",
                                "verdict", "gates")


class HealthHandler(BaseHTTPRequestHandler):
    def _json(self, code, payload):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    @staticmethod
    def _split_path(raw):
        """拆 path 与 query。**不用 urllib.parse** —— M1/M4 禁止可信内核
        引入网络库，arch_import_check 抓到了这一点（它是对的）。
        这里只需按 '?' 与 '&'/'=' 切分，无需 URL 语义。"""
        path, _, qs = raw.partition("?")
        keys = set()
        for pair in qs.split("&"):
            if pair:
                keys.add(pair.split("=", 1)[0])
        return path, keys

    def _reject_client_verdict(self):
        """E-1/E-3：请求携带判定字段即拒绝。返回 True 表示已拒绝。"""
        _, keys = self._split_path(self.path)
        n = int(self.headers.get("Content-Length") or 0)
        if n > 0:
            raw = self.rfile.read(n)
            try:
                obj = json.loads(raw)
                if isinstance(obj, dict):
                    keys |= set(obj.keys())
            except Exception:
                pass
        hit = sorted(keys & set(CLIENT_SUPPLIED_VERDICT_KEYS))
        if hit:
            self._json(400, {
                "error": "E-G5-002",
                "detail": f"请求携带判定字段 {hit} —— release_eligible 只由后端计算，"
                          f"客户端不得传入。**显式拒绝而非静默忽略**：静默忽略会让"
                          f"调用方以为得手，也使该尝试在日志中无痕。",
                "rejected_keys": hit})
            return True
        return False

    def do_GET(self):
        path, _ = self._split_path(self.path)
        if path in ("/livez", "/readyz"):
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"ok\n")
            return
        if path == "/api/release/eligibility":
            if self._reject_client_verdict():
                return
            self._json(200, _compute_eligibility())
            return
        self.send_response(404)
        self.end_headers()

    def do_POST(self):
        path, _ = self._split_path(self.path)
        if path == "/api/release/eligibility":
            # E-3：本端点不接受写入 —— 判定不可由客户端改写。
            # 带判定字段 → 400（更具体）；否则 → 405（方法不允许）。
            # _reject_client_verdict 已发响应时不得再发第二次。
            if not self._reject_client_verdict():
                self._json(405, {"error": "E-G5-003",
                                 "detail": "release_eligible 不可由客户端写入；"
                                           "本端点只读，判定由后端唯一计算点产出"})
            return
        self.send_response(404)
        self.end_headers()


def main() -> int:
    log = setup_logging(get_settings().log_level)
    ap = argparse.ArgumentParser()
    ap.add_argument("--bind", default=None,
                    help="绑定地址（缺省取 BIND_HOST；容器场景须为 0.0.0.0）")
    args, _ = ap.parse_known_args()
    settings = get_settings()
    errors = settings.validate()
    if errors:
        for e in errors:
            log.error("配置校验失败: %s", e)
        return 2  # fail-closed：配置非法即退出
    bind = args.bind or settings.bind_host
    port = settings.app_port
    server = ThreadingHTTPServer((bind, port), HealthHandler)
    log.info("listening on %s:%s", bind, port)
    server.serve_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
