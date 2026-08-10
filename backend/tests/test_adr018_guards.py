"""ADR-018 §4 三道守卫的负测（守卫自身也须被测）。"""
import os
import subprocess
import sys
import unittest

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
