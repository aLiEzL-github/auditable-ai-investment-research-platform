import unittest
import subprocess
import sys
import socket
import time
import urllib.request

import os

APP_DIR = os.path.join(os.path.dirname(__file__), "..", "app")


def free_port() -> int:
    """动态空闲端口（A3/N-2：不再固定端口，避免与残留进程冲突）。"""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class TestHealthEndpoints(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.port = free_port()
        env = dict(os.environ, APP_PORT=str(cls.port))
        cls.proc = subprocess.Popen(
            [sys.executable, os.path.join(APP_DIR, "main.py")],
            env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            for _ in range(20):
                try:
                    urllib.request.urlopen(f"http://127.0.0.1:{cls.port}/livez", timeout=0.5)
                    break
                except Exception:
                    time.sleep(0.2)
            else:
                raise RuntimeError("health server did not start")
        except BaseException:
            cls._kill()
            raise

    @classmethod
    def _kill(cls):
        """A3/N-2：任何路径都清理子进程（terminate + wait + 超时 kill）。"""
        if cls.proc.poll() is None:
            cls.proc.terminate()
            try:
                cls.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                cls.proc.kill()
                cls.proc.wait()

    @classmethod
    def tearDownClass(cls):
        cls._kill()

    def _get(self, path):
        return urllib.request.urlopen(
            f"http://127.0.0.1:{self.port}{path}", timeout=3)

    def test_livez(self):
        r = self._get("/livez")
        self.assertEqual(r.status, 200)

    def test_readyz(self):
        r = self._get("/readyz")
        self.assertEqual(r.status, 200)

    def test_unknown_path_404(self):
        with self.assertRaises(urllib.error.HTTPError):
            self._get("/nope")


if __name__ == "__main__":
    unittest.main()
