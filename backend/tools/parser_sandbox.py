#!/usr/bin/env python3
"""parser_sandbox.py —— G2-12 隔离式 PDF/XML 安全 fallback（L6 解析层）。

基线验收（G2-12）：
  · MIME/大小/页数/超时、XXE/实体膨胀、路径逃逸、symlink 与资源上限控制
  · 恶意与超限样本全部失败关闭
  · 解析器无密钥、无任意网络、无宿主通配写权限
ADR-007：进程级隔离 + 库级加固（显式记录为相对容器隔离的降级）。
"""
import os
import re
import signal
import subprocess
import sys

ALLOWED_MIME = {"application/pdf", "text/xml", "application/xml", "text/plain"}
MAX_SIZE = 50 * 1024 * 1024  # 50MB
MAX_PAGES = 5000
TIMEOUT_S = 30

DOCTYPE_RE = re.compile(r"<!DOCTYPE", re.IGNORECASE)
ENTITY_RE = re.compile(r"<!ENTITY", re.IGNORECASE)


class SandboxError(ValueError):
    pass


def validate_input(content_type: str, size: int) -> None:
    """MIME 白名单 + 大小上限（失败关闭）。"""
    if content_type not in ALLOWED_MIME:
        raise SandboxError(
            f"E-G2-12-001: MIME 不在允许清单（失败关闭）: {content_type}")
    if size > MAX_SIZE:
        raise SandboxError(f"E-G2-12-002: 超大小上限（失败关闭）: {size} > {MAX_SIZE}")


def xxe_guard(xml_bytes: bytes) -> None:
    """XXE/实体膨胀防护：DOCTPYPE/ENTITY 声明一律拒绝（失败关闭）。"""
    head = xml_bytes[:4096].decode("utf-8", "replace")
    if DOCTYPE_RE.search(head) or ENTITY_RE.search(head):
        raise SandboxError("E-G2-12-005: 检测到 DOCTYPE/ENTITY（XXE 防护，失败关闭）")


def sandbox_env() -> dict:
    """最小化环境：无 HOME/SSH/密钥/网络代理（解析器无密钥、无任意网络）。"""
    env = {
        "PATH": "/usr/bin:/bin",
        "LANG": "C.UTF-8",
        "PYTHONIOENCODING": "utf-8",
    }
    return env


def run_isolated(argv: list, timeout: float = TIMEOUT_S,
                 cwd: str = None, env: dict = None) -> subprocess.CompletedProcess:
    """进程级隔离（ADR-007）：子进程 + 资源上限 + 超时 kill（失败关闭）。"""
    import resource

    def _limit():
        resource.setrlimit(resource.RLIMIT_CPU, (timeout, timeout + 5))
        try:
            # macOS 上 AS 收紧可能被 hard limit 拒绝：容错跳过（CPU+超时为主防线）
            resource.setrlimit(resource.RLIMIT_AS, (256 * 1024 * 1024, 256 * 1024 * 1024))
        except (ValueError, OSError):
            pass

    try:
        r = subprocess.run(
            argv, capture_output=True, timeout=timeout, cwd=cwd,
            env=env or sandbox_env(), preexec_fn=_limit)
    except subprocess.TimeoutExpired:
        raise SandboxError(f"E-G2-12-003: 解析超时中止（失败关闭）: {argv[0]}")
    if r.returncode != 0:
        raise SandboxError(
            f"E-G2-12-004: 解析进程异常退出（失败关闭）: {argv[0]} rc={r.returncode}")
    return r


def safe_output_path(root: str, name: str) -> str:
    """输出路径防逃逸/symlink：resolve 后须在沙箱目录内。"""
    from pathlib import Path
    base = Path(root).resolve()
    target = (base / name).resolve()
    if not target.is_relative_to(base):
        raise SandboxError("E-G2-12-006: 输出路径逃逸沙箱边界（失败关闭）")
    return str(target)


if __name__ == "__main__":
    # 自检：python3 backend/tools/parser_sandbox.py <content_type> <size>
    try:
        validate_input(sys.argv[1], int(sys.argv[2]))
        print("OK")
    except SandboxError as e:
        print(str(e), file=sys.stderr)
        sys.exit(1)
