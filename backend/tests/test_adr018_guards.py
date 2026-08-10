"""ADR-018 §4 三道守卫的负测（守卫自身也须被测）。"""
import os
import subprocess
import sys
import unittest
from unittest import mock

HERE = os.path.dirname(__file__)
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "backend", "app"))
CHECK = os.path.join(ROOT, "backend", "tools", "akshare_use_check.py")
PROBE = os.path.join(ROOT, "backend", "app", "probe_adr018.py")


def _run():
    return subprocess.run([sys.executable, CHECK, ROOT], capture_output=True, text=True)


class TestGuardAB(unittest.TestCase):
    def tearDown(self):
        if os.path.exists(PROBE):
            os.remove(PROBE)

    def test_baseline_green(self):
        self.assertEqual(_run().returncode, 0)

    def test_forbid_curl_cffi_import(self):
        open(PROBE, "w").write("import curl_cffi\n")
        r = _run()
        self.assertEqual(r.returncode, 1)
        self.assertIn("curl_cffi", r.stdout)

    def test_forbid_akshare_submodule(self):
        open(PROBE, "w").write("from akshare.news import news_baidu\n")
        self.assertEqual(_run().returncode, 1)

    def test_whitelist_blocks_unlisted_call(self):
        open(PROBE, "w").write("import akshare as ak\ndef f():\n    return ak.stock_zh_a_hist()\n")
        r = _run()
        self.assertEqual(r.returncode, 1)
        self.assertIn("白名单", r.stdout)


class TestGuardC(unittest.TestCase):
    def test_runtime_interdict(self):
        import curl_cffi_interdict as I
        I.install()
        try:
            with self.assertRaises(I.InterdictError):
                __import__("curl_cffi")
        finally:
            I.uninstall()

    def test_normal_imports_unaffected(self):
        import curl_cffi_interdict as I
        I.install()
        try:
            __import__("json")
            __import__("urllib.request")
        finally:
            I.uninstall()


class TestGuardCLateInstall(unittest.TestCase):
    """OI-PF-135：sys.meta_path 只对**尚未导入**的模块生效。"""

    def test_install_fails_closed_if_already_imported(self):
        import curl_cffi_interdict as I
        with mock.patch.dict("sys.modules", {"curl_cffi": mock.Mock()}):
            with self.assertRaises(I.InterdictError) as ctx:
                I.install()
        self.assertIn("E-ADR018-C-LATE", str(ctx.exception))

    def test_install_ok_when_not_imported(self):
        import curl_cffi_interdict as I
        mods = {k: v for k, v in sys.modules.items() if not k.startswith("curl_cffi.")}
        mods.pop("curl_cffi", None)
        with mock.patch.dict("sys.modules", mods, clear=True):
            I.uninstall()
            self.assertTrue(I.install())
            I.uninstall()
