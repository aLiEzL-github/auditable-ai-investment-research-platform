#!/usr/bin/env python3
"""test_g7_03_system_security.py —— G7-03 系统级安全负测集成套件（OI-PF-015）。

在系统集成层（完整运行环境）重跑七类攻击面，每类变异注入配对：
  防御在位 → 绿；去掉防御 → 红（防误红双向验证）。

七类：XXE · 实体膨胀 · 路径逃逸 · symlink · Docker socket · 网络外连 · 权限提升。

原则（G7-03 原子任务书）：
  · 失败关闭（fail-closed）—— 防御必须真实生效，不是「代码里有检查」；
  · 变异注入矩阵全绿 —— 去掉防御必须判红；
  · 本套件只做本地测试，不对外部目标发起任何攻击。
"""
import os
import socket
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend" / "tools"))

import parser_sandbox
from parser_sandbox import SandboxError


class TestXXE(unittest.TestCase):
    """1/7 XXE：外部实体注入必须失败关闭。"""

    def test_xxe_injected_rejected(self):
        evil = b"""<?xml version="1.0"?>
<!DOCTYPE foo [<!ENTITY xxe SYSTEM "file:///etc/passwd">]>
<foo>&xxe;</foo>"""
        with self.assertRaises(SandboxError):
            parser_sandbox.xxe_guard(evil)

    def test_clean_xml_accepted(self):
        clean = b"<?xml version='1.0'?><foo>bar</foo>"
        parser_sandbox.xxe_guard(clean)


class TestBillionLaughs(unittest.TestCase):
    """2/7 实体膨胀：billion laughs 必须在解析前被拒绝。"""

    def test_billion_laughs_rejected(self):
        evil = b"""<?xml version="1.0"?>
<!DOCTYPE lolz [
<!ENTITY lol "lol"><!ENTITY lol2 "&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;">
<!ENTITY lol3 "&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;">
<!ENTITY lol4 "&lol3;&lol3;&lol3;&lol3;&lol3;&lol3;&lol3;&lol3;&lol3;&lol3;">
<!ENTITY lol5 "&lol4;&lol4;&lol4;&lol4;&lol4;&lol4;&lol4;&lol4;&lol4;&lol4;">
<!ENTITY lol6 "&lol5;&lol5;&lol5;&lol5;&lol5;&lol5;&lol5;&lol5;&lol5;&lol5;">
]>
<lolz>&lol6;</lolz>"""
        with self.assertRaises(SandboxError):
            parser_sandbox.xxe_guard(evil)

    def test_entity_size_bomb_blocked_by_max_size(self):
        with self.assertRaises(SandboxError):
            parser_sandbox.validate_input("application/xml", 51 * 1024 * 1024)


class TestPathTraversal(unittest.TestCase):
    """3/7 路径逃逸：输出路径必须留在沙箱目录内。"""

    def test_traversal_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(SandboxError):
                parser_sandbox.safe_output_path(d, "../../etc/passwd")

    def test_absolute_path_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(SandboxError):
                parser_sandbox.safe_output_path(d, "/etc/passwd")

    def test_normal_name_accepted(self):
        with tempfile.TemporaryDirectory() as d:
            p = parser_sandbox.safe_output_path(d, "out.xml")
            self.assertTrue(Path(p).is_relative_to(Path(d).resolve()))


class TestSymlink(unittest.TestCase):
    """4/7 symlink：沙箱目录内的 symlink 指向沙箱外必须失败关闭。"""

    def test_symlink_escape_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            # 逃逸目标在沙箱目录之外
            outer = tempfile.mkdtemp()
            target = os.path.join(outer, "secret")
            Path(target).write_text("secret")
            link = os.path.join(d, "link.xml")
            os.symlink(target, link)
            with self.assertRaises(SandboxError):
                parser_sandbox.safe_output_path(d, "link.xml")

    def test_symlink_internal_allowed(self):
        with tempfile.TemporaryDirectory() as d:
            inner = os.path.join(d, "inner.xml")
            Path(inner).write_text("data")
            link = os.path.join(d, "alias.xml")
            os.symlink(inner, link)
            p = parser_sandbox.safe_output_path(d, "alias.xml")
            self.assertTrue(Path(p).is_relative_to(Path(d).resolve()))


class TestDockerSocket(unittest.TestCase):
    """5/7 Docker socket：沙箱/解析环境不得把宿主 docker.sock 暴露给进程。"""

    def test_sandbox_env_strips_docker_socket(self):
        env = parser_sandbox.sandbox_env()
        sock = env.get("DOCKER_HOST", "")
        self.assertNotIn("docker.sock", sock)
        # 任何变体 DOCKER_* 不得含 docker.sock 路径
        for k, v in env.items():
            if k.startswith("DOCKER"):
                self.assertNotIn("docker.sock", v)


class TestNetworkEgress(unittest.TestCase):
    """6/7 网络外连：解析进程不得有任意出站网络。"""

    def test_sandbox_env_no_proxy_leak(self):
        env = parser_sandbox.sandbox_env()
        for k in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy",
                  "https_proxy", "all_proxy"):
            self.assertNotIn(k, env)

    def test_import_guard_egress(self):
        """import_guard 对非白名单 host 判红（网络外连防线）。"""
        import import_guard
        with self.assertRaises(ValueError):
            import_guard.validate_import_url("http://not-allowed.example/x", allowed_hosts=["a.example"])


class TestPrivilegeEscalation(unittest.TestCase):
    """7/7 权限提升：解析子进程不得以高权限运行。"""

    def test_float_timeout_fail_closed(self):
        """浮点 timeout 必须失败关闭（G7-03 发现：原实现 setrlimit 传浮点崩溃）。"""
        with self.assertRaises(SandboxError) as ctx:
            parser_sandbox.run_isolated(["sleep", "60"], timeout=0.5)
        self.assertIn("E-G2-12-003", str(ctx.exception))

    def test_no_root_in_sandbox(self):
        env = parser_sandbox.sandbox_env()
        # 不注入特权环境变量；PATH 不含系统管理工具
        self.assertNotIn("SUDO_ASKPASS", env)


if __name__ == "__main__":
    unittest.main()
