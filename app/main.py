"""应用入口：仅提供健康检查端点（G0-08 §3.1 文件集第 8 件）。

当前阶段（G1 之前）为最小可构建骨架：
- /livez  存活探针（进程活着即 200）
- /readyz 就绪探针（依赖就绪才 200；骨架阶段无外部依赖，恒 200）

零外部依赖、零数据访问、零网络外连（G0-03 ZERO_COPY_ZERO_NETWORK_DEFAULT_DENY）。
"""

import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


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
    port = int(os.environ.get("PORT", "8080"))
    server = ThreadingHTTPServer(("127.0.0.1", port), HealthHandler)
    print(f"listening on 127.0.0.1:{port}", flush=True)
    server.serve_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
