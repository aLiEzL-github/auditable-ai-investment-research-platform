import unittest
import subprocess
import sys
import threading
import time
import urllib.request

import os

APP_DIR = os.path.join(os.path.dirname(__file__), "..", "app")


class TestHealthEndpoints(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        env = dict(os.environ, APP_PORT="18081")
        cls.proc = subprocess.Popen(
            [sys.executable, os.path.join(APP_DIR, "main.py")],
            env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(20):
            try:
                urllib.request.urlopen("http://127.0.0.1:18081/livez", timeout=0.5)
                break
            except Exception:
                time.sleep(0.2)
        else:
            raise RuntimeError("health server did not start")

    @classmethod
    def tearDownClass(cls):
        cls.proc.terminate()
        cls.proc.wait()

    def test_livez(self):
        r = urllib.request.urlopen("http://127.0.0.1:18081/livez", timeout=3)
        self.assertEqual(r.status, 200)

    def test_readyz(self):
        r = urllib.request.urlopen("http://127.0.0.1:18081/readyz", timeout=3)
        self.assertEqual(r.status, 200)

    def test_unknown_path_404(self):
        with self.assertRaises(urllib.error.HTTPError):
            urllib.request.urlopen("http://127.0.0.1:18081/nope", timeout=3)


if __name__ == "__main__":
    unittest.main()
