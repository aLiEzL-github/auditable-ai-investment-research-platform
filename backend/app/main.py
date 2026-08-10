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

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer  # noqa: E402

from logging_conf import setup_logging  # noqa: E402
from settings import get_settings  # noqa: E402


class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path in ("/livez", "/readyz"):
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"ok\n")
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
