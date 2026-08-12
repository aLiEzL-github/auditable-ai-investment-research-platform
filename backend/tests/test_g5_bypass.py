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

    # ── 默认拒绝：清单外的变体同样须被拒（S3 ④ 的修复）──────────────
    # 下列七个向量**全部绕过过当刻 main**（2026-08-12 实测，规则 ⑩：
    # 用原来那个成功绕过的载荷复验）。成因是初版用
    # `keys & set(CLIENT_SUPPLIED_VERDICT_KEYS)` 精确匹配 —— 一份穷举清单。
    #
    # **需说清楚**：当时这些绕过并不改变结论，因为 _compute_eligibility()
    # 不接受入参、返回固定 fail-closed 值。问题不在当下的判定被改写，而在
    #   ① 「请求携带判定字段即拒绝」这个断言按字面不成立；
    #   ② 机制是公开的 —— 读代码即知过滤器是精确匹配，也就知道往哪里试；
    #   ③ 日后任何 handler 一旦消费请求数据，这个过滤器是唯一挡在那里的。
    def test_default_deny_rejects_all_variants(self):
        vectors = [
            ("大小写变体",   "?Release_Eligible=true",        None),
            ("百分号编码",   "?release%5Feligible=true",      None),
            ("连字符分隔",   "?release-eligible=true",        None),
            ("前导空格",     "?%20release_eligible=true",     None),
            ("任意未知参数", "?foo=1",                        None),
        ]
        for name, qs, _ in vectors:
            code, body = self._get("/api/release/eligibility" + qs)
            self.assertEqual(code, 400, f"{name} 未被拒绝：{body}")
            self.assertEqual(body["error"], "E-G5-002", name)

    def test_default_deny_rejects_nested_and_array_bodies(self):
        """嵌套 dict 与 list 元素里的键同样算数 —— 初版只看顶层 dict.keys()。"""
        for name, payload in (("嵌套 JSON", {"data": {"release_eligible": True}}),
                              ("JSON 数组体", [{"release_eligible": True}])):
            code, body = self._get("/api/release/eligibility",
                                   data=json.dumps(payload).encode(), method="GET")
            self.assertEqual(code, 400, f"{name} 未被拒绝：{body}")
            self.assertEqual(body["error"], "E-G5-002", name)

    def test_unparseable_body_is_still_input(self):
        """**解析失败不等于没有输入。** 初版在 json.loads 抛异常时静默跳过。"""
        code, body = self._get("/api/release/eligibility",
                               data=b"not-json-at-all", method="GET")
        self.assertEqual(code, 400, body)
        self.assertIn("<body>", body["rejected_keys"])

    def test_verdict_variants_are_labelled_as_verdict_keys(self):
        """归一化只用于**诊断标注**：拒绝与否不取决于它，但标注须准确 ——
        否则日志里看不出「这次是有人在试判定字段」还是「参数打错了」。"""
        code, body = self._get("/api/release/eligibility?Release-Eligible=true")
        self.assertEqual(code, 400, body)
        self.assertTrue(body["verdict_keys"],
                        f"变体未被标注为判定字段：{body}")

    def test_clean_get_still_allowed(self):
        """**唯一的放行路径**：无任何入参。默认拒绝不得把正常读取也挡掉。"""
        code, body = self._get("/api/release/eligibility")
        self.assertEqual(code, 200, body)
        self.assertEqual(body["computed_by"], "backend")


if __name__ == "__main__":
    unittest.main()
