"""G2-12 验收测试：隔离式 PDF/XML 安全 fallback（恶意/超限样本全失败关闭）。

基线：
  · MIME/大小/超时、XXE/实体膨胀、路径逃逸、symlink 与资源上限控制
  · 恶意与超限样本全部失败关闭
  · 解析器无密钥、无任意网络、无宿主通配写权限（ADR-007 进程级隔离）
"""
import unittest
import os
import sys
import tempfile
import shutil

APP = os.path.join(os.path.dirname(__file__), "..", "app")
sys.path.insert(0, APP)
TOOLS = os.path.join(os.path.dirname(__file__), "..", "tools")
sys.path.insert(0, TOOLS)

from parser_sandbox import (SandboxError, validate_input, xxe_guard,  # noqa: E402
                            sandbox_env, run_isolated, safe_output_path)


class TestParserSandbox(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self._tmp, ignore_errors=True)

    # ── MIME/大小上限 ───────────────────────────────────────────────
    def test_mime_rejected(self):
        for bad in ("application/x-executable", "image/png", ""):
            with self.assertRaises(SandboxError) as ctx:
                validate_input(bad, 10)
            self.assertIn("E-G2-12-001", str(ctx.exception))

    def test_size_limit(self):
        with self.assertRaises(SandboxError) as ctx:
            validate_input("application/pdf", 51 * 1024 * 1024)
        self.assertIn("E-G2-12-002", str(ctx.exception))
        validate_input("application/pdf", 1024)  # 合法不抛

    # ── XXE/实体膨胀 ───────────────────────────────────────────────
    def test_xxe_rejected(self):
        evil = b'<?xml version="1.0"?><!DOCTYPE r [<!ENTITY x SYSTEM "file:///etc/passwd">]><r>&x;</r>'
        with self.assertRaises(SandboxError) as ctx:
            xxe_guard(evil)
        self.assertIn("E-G2-12-005", str(ctx.exception))

    def test_billion_laughs_rejected(self):
        evil = b'<?xml version="1.0"?><!DOCTYPE lolz [<!ENTITY lol "lol">]>'
        with self.assertRaises(SandboxError):
            xxe_guard(evil)

    def test_clean_xml_accepted(self):
        xxe_guard(b'<?xml version="1.0"?><root><a>1</a></root>')  # 不抛

    # ── 进程级隔离（ADR-007）：env 无密钥/无网络代理 ────────────────
    def test_sandbox_env_strips_secrets(self):
        env = sandbox_env()
        self.assertNotIn("HOME", env)
        self.assertNotIn("SSH_AUTH_SOCK", env)
        self.assertNotIn("SSH_AGENT_PID", env)
        self.assertNotIn("http_proxy", env)
        self.assertNotIn("https_proxy", env)
        self.assertNotIn("GITHUB_TOKEN", env)
        self.assertNotIn("BACKUP_PASSPHRASE", env)

    # ── 超时/异常退出失败关闭 ───────────────────────────────────────
    def test_timeout_fail_closed(self):
        with self.assertRaises(SandboxError) as ctx:
            run_isolated([sys.executable, "-c", "import time; time.sleep(60)"],
                         timeout=2, env=sandbox_env())
        self.assertIn("E-G2-12-003", str(ctx.exception))

    def test_crash_fail_closed(self):
        with self.assertRaises(SandboxError) as ctx:
            run_isolated([sys.executable, "-c", "raise SystemExit(1)"],
                         env=sandbox_env())
        self.assertIn("E-G2-12-004", str(ctx.exception))

    def test_normal_process_ok(self):
        r = run_isolated([sys.executable, "-c", "print('ok')"], env=sandbox_env())
        self.assertIn(b"ok", r.stdout)

    # ── 输出路径防逃逸 ──────────────────────────────────────────────
    def test_output_path_traversal_rejected(self):
        with self.assertRaises(SandboxError) as ctx:
            safe_output_path(self._tmp, "../escape.txt")
        self.assertIn("E-G2-12-006", str(ctx.exception))
        ok = safe_output_path(self._tmp, "out.pdf")
        self.assertTrue(ok.startswith(os.path.realpath(self._tmp)))


if __name__ == "__main__":
    unittest.main()
