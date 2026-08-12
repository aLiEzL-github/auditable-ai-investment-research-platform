"""G5 / OI-PF-156：UI 无法绕过后端 release_eligible（E-1 / E-3）。

基线 §9 对 Gate 5 的要求：「UI 无法绕过后端 `release_eligible`；阻断态不可隐藏」，
一票否决「前端可改写阻断态」。

**本组用例存在的理由**：在后端端点存在之前，那句话是空的 —— 没有端点就没有
可绕过的对象，也没有能拒绝的一方。前端测试（frontend/tests/）证明的是
「前端没自己算」，**不是**「后端挡得住」。按 G5-执行计划 §3.1，
E-1 须**在后端而非前端断言**，前端校验不计入证据（通用验收 ⑮）。

用例全部直接构造 HTTP 请求，**不经任何前端代码**。
"""
import json
import os
import sys
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "app"))

from main import HealthHandler  # noqa: E402


class _Silent(HealthHandler):
    def log_message(self, *a):   # 测试期不打访问日志
        pass


class TestReleaseEligibilityBypass(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), _Silent)
        cls.port = cls.srv.server_address[1]
        cls.t = threading.Thread(target=cls.srv.serve_forever, daemon=True)
        cls.t.start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()

    def _url(self, path):
        return f"http://127.0.0.1:{self.port}{path}"

    def _get(self, path, data=None, method=None):
        req = urllib.request.Request(self._url(path), data=data, method=method)
        if data is not None:
            req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    # ── 基线：端点确实存在且由后端计算 ────────────────────────────
    def test_endpoint_exists_and_computed_by_backend(self):
        code, body = self._get("/api/release/eligibility")
        self.assertEqual(code, 200)
        self.assertEqual(body["computed_by"], "backend")
        self.assertIn("source", body)

    # ── E-1：绕过 UI 直接构造请求，后端须拒绝伪造判定 ──────────────
    def test_bypass_query_param_verdict_rejected(self):
        """查询串伪造 release_eligible=true → 400，且不得返回 true。"""
        code, body = self._get("/api/release/eligibility?release_eligible=true")
        self.assertEqual(code, 400, body)
        self.assertEqual(body["error"], "E-G5-002")
        self.assertIn("release_eligible", body["rejected_keys"])
        self.assertNotIn("release_eligible", {k: v for k, v in body.items()
                                              if k == "release_eligible"})

    def test_bypass_body_verdict_rejected(self):
        """请求体伪造判定 → 400。**不是静默忽略** —— 静默忽略会让调用方
        以为得手，也使该尝试在日志中无痕。"""
        payload = json.dumps({"release_eligible": True,
                              "reasons": []}).encode()
        code, body = self._get("/api/release/eligibility", data=payload,
                               method="GET")
        self.assertEqual(code, 400, body)
        self.assertEqual(body["error"], "E-G5-002")

    def test_post_cannot_write_verdict(self):
        """E-3：判定不可由客户端写入 —— POST 须被拒（405 或 400）。"""
        payload = json.dumps({"note": "x"}).encode()
        code, body = self._get("/api/release/eligibility", data=payload,
                               method="POST")
        self.assertIn(code, (400, 405), body)
        self.assertIn(body.get("error"), ("E-G5-002", "E-G5-003"))

    # ── E-3 变异注入：伪造后**后端结论须不变** ────────────────────
    def test_verdict_unchanged_after_forgery_attempt(self):
        """用原来那个成功绕过的载荷复验（规则 ⑩）：
        伪造尝试之后再正常查询，结论必须与伪造前逐字一致。"""
        _, before = self._get("/api/release/eligibility")
        self._get("/api/release/eligibility?release_eligible=true")
        self._get("/api/release/eligibility",
                  data=json.dumps({"release_eligible": True}).encode(),
                  method="GET")
        _, after = self._get("/api/release/eligibility")
        self.assertEqual(before, after,
                         "伪造尝试改变了后端结论 —— E-3 一票否决触发")
        self.assertFalse(after["release_eligible"])

    def test_all_verdict_keys_covered(self):
        """判定字段清单须逐个生效，不得只挡最显眼的那个。"""
        from main import CLIENT_SUPPLIED_VERDICT_KEYS
        self.assertGreaterEqual(len(CLIENT_SUPPLIED_VERDICT_KEYS), 3)
        for k in CLIENT_SUPPLIED_VERDICT_KEYS:
            code, body = self._get(f"/api/release/eligibility?{k}=x")
            self.assertEqual(code, 400, f"{k} 未被拒绝：{body}")
            self.assertIn(k, body["rejected_keys"])


if __name__ == "__main__":
    unittest.main()
