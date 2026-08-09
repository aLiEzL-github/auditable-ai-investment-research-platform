"""G2-04 主源切换（VD-15 #2）验收测试：cninfo 适配器。

基线（G2-04 同款）：失败关闭 · 零网络（权利门）· 限速 · PDF 防护页拒绝。
判明实测（2026-08-09）：搜索 API 200（orgId）· 公告查询 API 200（PDF 直链）·
无 JS 挑战 —— 巨潮为主源自动通道，上交所降人工导入（VD-15 #2）。
"""
import unittest
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _matrix_fixture import MATRIX

from unittest import mock

APP = os.path.join(os.path.dirname(__file__), "..", "app")
sys.path.insert(0, APP)
TOOLS = os.path.join(os.path.dirname(__file__), "..", "tools")
sys.path.insert(0, TOOLS)

from rights_guard import RightsGuard, GuardDenied  # noqa: E402
from cninfo_adapter import CninfoAdapter  # noqa: E402


class TestCninfoAdapter(unittest.TestCase):
    def setUp(self):
        os.environ["CNINFO_BASE_URL"] = "https://example.test"
        os.environ["CNINFO_PDF_BASE_URL"] = "https://pdf.example.test"
        self.guard = RightsGuard(matrix=MATRIX)
        self.ad = CninfoAdapter(self.guard, min_interval=0.0)

    # ── 域名运行时注入（L4 来源特征规则保持严格）───────────────────
    def test_missing_domain_rejected(self):
        os.environ.pop("CNINFO_BASE_URL")
        with self.assertRaises(ValueError) as ctx:
            CninfoAdapter(RightsGuard(matrix=MATRIX))
        self.assertIn("E-G2-04-003", str(ctx.exception))

    def _resp_json(self, payload, status=200):
        import json as _j
        body = _j.dumps(payload).encode()
        m = mock.Mock()
        m.status = status
        m.read = mock.Mock(return_value=body)
        m.__enter__ = mock.Mock(return_value=m)
        m.__exit__ = mock.Mock(return_value=False)
        return m

    # ── 权利门：UNKNOWN/PROHIBITED 零请求 ───────────────────────────
    def test_unknown_zero_request(self):
        """FF-2：真实矩阵（CNINFO automated_bulk_acquisition=UNKNOWN）→ 零请求。"""
        import copy, json as _j
        real = _j.load(open(os.path.join(
            os.path.dirname(__file__), "..", "..", "contracts",
            "rights_matrix.json"), encoding="utf-8"))
        ad = CninfoAdapter(RightsGuard(matrix=real), min_interval=0.0)
        with mock.patch("cninfo_adapter.urllib.request.urlopen") as m:
            with self.assertRaises(GuardDenied):
                ad.resolve_org_id("600089")
            m.assert_not_called()
            with self.assertRaises(GuardDenied):
                ad.query_announcements("600089", "g1")
            m.assert_not_called()

    # ── 搜索 → orgId ────────────────────────────────────────────────
    def test_resolve_org_id(self):
        with mock.patch("cninfo_adapter.urllib.request.urlopen",
                        return_value=self._resp_json(
                            [{"code": "600089", "orgId": "gssh0600089"}])):
            org = self.ad.resolve_org_id("600089")
        self.assertEqual(org, "gssh0600089")

    def test_resolve_org_id_missing(self):
        with mock.patch("cninfo_adapter.urllib.request.urlopen",
                        return_value=self._resp_json([{"code": "600888"}])):
            with self.assertRaises(RuntimeError) as ctx:
                self.ad.resolve_org_id("600089")
        self.assertIn("E-G2-04-004", str(ctx.exception))

    # ── 公告查询 → PDF 直链元数据 ──────────────────────────────────
    def test_query_announcements(self):
        j = {"announcements": [
            {"announcementTitle": "年度报告", "adjunctUrl": "finalpage/2026-04-30/x.PDF",
             "announcementTime": 1714400000000}]}
        with mock.patch("cninfo_adapter.urllib.request.urlopen",
                        return_value=self._resp_json(j)):
            anns = self.ad.query_announcements("600089", "g1")
        self.assertEqual(len(anns), 1)
        self.assertEqual(anns[0]["title"], "年度报告")
        self.assertIn("x.PDF", anns[0]["locator"])

    # ── 失败关闭：HTTP 错误 / 超时 ─────────────────────────────────
    def test_http_error_fail_closed(self):
        import urllib.error
        with mock.patch("cninfo_adapter.urllib.request.urlopen",
                        side_effect=urllib.error.HTTPError(
                            "u", 429, "Too Many", None, None)):
            with self.assertRaises(RuntimeError) as ctx:
                self.ad.resolve_org_id("600089")
        self.assertIn("E-G2-04-002", str(ctx.exception))

    # ── PDF 防护页拒绝（返回 HTML 非 PDF → 失败关闭）───────────────
    def test_pdf_html_guard_rejected(self):
        resp = mock.Mock()
        resp.headers = {"Content-Type": "text/html"}
        resp.read = mock.Mock(return_value=b"<html>challenge</html>")
        resp.__enter__ = mock.Mock(return_value=resp)
        resp.__exit__ = mock.Mock(return_value=False)
        with mock.patch("cninfo_adapter.urllib.request.urlopen",
                        return_value=resp):
            with self.assertRaises(RuntimeError) as ctx:
                self.ad.download_pdf("finalpage/x.PDF")
        self.assertIn("E-G2-04-005", str(ctx.exception))

    def test_pdf_ok(self):
        resp = mock.Mock()
        resp.headers = {"Content-Type": "application/pdf"}
        resp.read = mock.Mock(return_value=b"%PDF-1.4")
        resp.__enter__ = mock.Mock(return_value=resp)
        resp.__exit__ = mock.Mock(return_value=False)
        with mock.patch("cninfo_adapter.urllib.request.urlopen",
                        return_value=resp):
            data = self.ad.download_pdf("finalpage/x.PDF")
        self.assertEqual(data, b"%PDF-1.4")

    # ── 限速 ───────────────────────────────────────────────────────
    def test_rate_limit(self):
        ad = CninfoAdapter(self.guard, min_interval=0.05)
        with mock.patch("cninfo_adapter.urllib.request.urlopen",
                        return_value=self._resp_json(
                            [{"code": "600089", "orgId": "g1"}])):
            ad.resolve_org_id("600089")
            t0 = __import__("time").time()
            ad.resolve_org_id("600089")
            el = __import__("time").time() - t0
        self.assertGreaterEqual(el, 0.04)


if __name__ == "__main__":
    unittest.main()
