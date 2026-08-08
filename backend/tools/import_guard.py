#!/usr/bin/env python3
"""import_guard.py —— 人工 URL 导入的 SSRF 防护（G2-03 出网层前置校验）。

位置在工具/适配器层（可出网层，VD-11 §6 Discovery 允许清单内）——
M1/M4 禁止可信内核 backend/app/ 引入网络库，SSRF 校验必须在出网侧。
"""
import ipaddress
import re
import socket
from urllib.parse import urlparse

PRIVATE_HOST_RE = re.compile(
    r"^(127\.|10\.|192\.168\.|172\.(1[6-9]|2[0-9]|3[0-1])\.|169\.254\.)"
)
_LOOPBACK = "::1"


def validate_import_url(url: str, allowed_hosts=None):
    """URL 导入（SSRF 防护）：协议合法、主机允许清单、解析后须为公开地址。"""
    u = urlparse(url)
    if u.scheme not in ("http", "https"):
        raise ValueError("E-G2-03-004: 非法 URL 协议")
    host = u.hostname
    if host is None:
        raise ValueError("E-G2-03-004: 无主机名")
    if allowed_hosts is not None and host not in allowed_hosts:
        raise ValueError(f"E-G2-03-004: 主机不在允许清单: {host}")
    try:
        addrs = socket.getaddrinfo(host, None)
    except socket.gaierror:
        raise ValueError(f"E-G2-03-004: 主机无法解析: {host}")
    for fam, _, _, _, sockaddr in addrs:
        ip = sockaddr[0]
        if fam == socket.AF_INET:
            if PRIVATE_HOST_RE.match(ip):
                raise ValueError(f"E-G2-03-004: 解析到私网地址（SSRF）: {ip}")
        else:
            try:
                if ipaddress.ip_address(ip).is_private or ip == _LOOPBACK:
                    raise ValueError(f"E-G2-03-004: 解析到私网地址（SSRF）: {ip}")
            except ValueError:
                raise ValueError(f"E-G2-03-004: 非法 IP: {ip}")
    return u


if __name__ == "__main__":
    import sys
    validate_import_url(sys.argv[1], None)
    print("OK")
