"""G1-05 验收测试：JSON 日志 / 脱敏 / .env.example 无密钥。

验收映射：
  基线  日志和测试快照无密钥；本地只绑定 loopback（main 默认 127.0.0.1）
  1a    secret_scan 对含真实格式合成密钥的日志样本须命中
  1b    .env.example 无真实值且被 secret_scan 覆盖
"""

import io
import logging
import os
import re
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
from logging_conf import setup_logging
from settings import Settings


class TestJsonLogging(unittest.TestCase):
    def _capture(self):
        buf = io.StringIO()
        root = logging.getLogger("app.test")
        root.handlers.clear()
        handler = logging.StreamHandler(buf)
        from logging_conf import JsonFormatter
        handler.setFormatter(JsonFormatter())
        root.addHandler(handler)
        root.setLevel(logging.INFO)
        return root, buf

    def test_json_format(self):
        root, buf = self._capture()
        root.info("hello %s", "world")
        self.assertIn('"msg": "hello world"', buf.getvalue())
        self.assertIn('"level": "INFO"', buf.getvalue())

    def test_password_masked(self):
        root, buf = self._capture()
        root.info("connecting db password=%s", "S3CR3T123")
        self.assertNotIn("S3CR3T123", buf.getvalue())
        self.assertIn("<REDACTED>", buf.getvalue())

    def test_token_masked(self):
        root, buf = self._capture()
        root.info("token=ghp_%s", "A" * 40)
        out = buf.getvalue()
        self.assertNotIn("ghp_" + "A" * 40, out)
        self.assertIn("<REDACTED>", out)

    def test_url_credentials_masked(self):
        root, buf = self._capture()
        root.info("dsn postgresql://admin:sec%s@db:5432/x", "ret123")
        out = buf.getvalue()
        self.assertNotIn(":sec", out)
        self.assertIn("<REDACTED>", out)

    def test_private_key_header_masked(self):
        root, buf = self._capture()
        root.info("key %s", "-----BEGIN OPENSSH PRIVATE KEY-----")
        self.assertIn("<REDACTED>", buf.getvalue())


class TestSettingsAndEnv(unittest.TestCase):
    def test_loopback_default(self):
        """基线验收：本地默认只绑定 loopback。"""
        self.assertEqual(Settings().bind_host, "127.0.0.1")

    def test_env_example_has_no_real_values(self):
        """1b：.env.example 只含键名与占位，无真实值；被 secret_scan 覆盖（CI scans job）。"""
        p = os.path.join(os.path.dirname(__file__), "..", "..", ".env.example")
        with open(p, encoding="utf-8") as f:
            content = f.read()
        # 无任何 64 位哈希 / 密钥模式 / 占位符之外的真实值
        self.assertNotRegex(content, r"[0-9a-f]{64}")
        self.assertNotRegex(content, r"(?i)(ghp_|sk-[A-Za-z0-9]{10,}|AKIA|BEGIN (?:OPENSSH|RSA))")
        self.assertNotIn("DATABASE_PASSWORD=", content.replace("# DATABASE_PASSWORD=<仅本机环境变量>", ""))

    def test_invalid_settings_rejected(self):
        import os
        os.environ["APP_PORT"] = "99999"
        errors = Settings().validate()
        os.environ["APP_PORT"] = "8080"
        self.assertTrue(any("APP_PORT" in e for e in errors))


class TestSecretScanLogSample(unittest.TestCase):
    def test_synthetic_key_log_hit(self):
        """1a：含真实格式合成密钥的日志样本，secret_scan 须命中。"""
        import subprocess
        import tempfile
        import shutil
        tmp = tempfile.mkdtemp()
        try:
            synthetic_token = "ghp_" + "A" * 40
            with open(os.path.join(tmp, "app.log.txt"), "w", encoding="utf-8") as fh:
                fh.write(f'{{"ts": "x", "msg": "token={synthetic_token}"}}\n')
            scan = os.path.join(os.path.dirname(__file__), "..", "tools", "secret_scan.py")
            r = subprocess.run([sys.executable, scan, tmp], capture_output=True, text=True)
            self.assertEqual(r.returncode, 1, f"secret_scan 未命中合成密钥日志: {r.stdout[-200:]}")
            self.assertIn("GitHub PAT", r.stdout)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
