"""G2-04 验收测试：官方 A 股披露源适配器（mock 网络，不出网）。

基线：
  · 验证码/条款限制时失败关闭，不绕过
  · 无先行权利决定（UNKNOWN/PROHIBITED）时零网络/零缓存
BF-04（取得器级）：
  · 403/429 即停且失败关闭；保守限速
  · 同一取得事件重试不产生重复 AcquisitionEvent（幂等）
  · 超时中止；来源权利失效后新请求/缓存为零
"""
import unittest

import tempfile
import shutil
import os
import sys
from unittest import mock

import os as _os
sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from _matrix_fixture import MATRIX

APP = os.path.join(os.path.dirname(__file__), "..", "app")
sys.path.insert(0, APP)
TOOLS = os.path.join(os.path.dirname(__file__), "..", "tools")
sys.path.insert(0, TOOLS)

from rights_guard import RightsGuard, GuardDenied  # noqa: E402
from sse_adapter import SSEAdapter  # noqa: E402
from repository import create_repository, Source, AcquisitionEvent, RawArtifact  # noqa: E402


def _resp(status, body=b"<html>ok</html>"):
    m = mock.Mock()
    m.status = status
    m.read = mock.Mock(return_value=body)
    m.__enter__ = mock.Mock(return_value=m)
    m.__exit__ = mock.Mock(return_value=False)
    return m


class TestSSEAdapter(unittest.TestCase):
    def setUp(self):
        self.guard = RightsGuard(matrix=MATRIX)
        os.environ["SSE_BASE_URL"] = "https://example.test"
        self.ad = SSEAdapter(self.guard, min_interval=0.0)
        self._tmp = tempfile.mkdtemp()
        self.repo = create_repository(os.path.join(self._tmp, "g2_04.sqlite3"))
        self.repo.create_all()
        self.s = self.repo.session()
        for sid, st in (("SRC_SSE", "ALLOWED"), ("SRC_UNK", "UNKNOWN"),
                        ("SRC_BAN", "PROHIBITED")):
            self.s.add(Source(id=sid, schema_version="1.0", kind="PRIMARY",
                              name=sid, status=st, legal_basis="G2-04 测试", version=1))
        self.s.commit()  # 分步 commit（同批 add 排序问题：raw_artifact 会先插）
        import hashlib
        self.s.add(RawArtifact(id="ART_PLACEHOLDER", schema_version="1.0",
                               source_id="SRC_SSE", sha256=hashlib.sha256(b"ph").hexdigest(),
                               bytes=2, content_type="text/plain",
                               acquired_at=__import__("datetime").datetime.utcnow(), version=1))
        self.s.commit()

    def tearDown(self):
        self.s.close()
        self.repo.engine.dispose()
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _record_event(self, event_id, scope, ok, error, status):
        from datetime import datetime
        self.s.add(AcquisitionEvent(id=event_id, schema_version="1.0",
                                    artifact_id="ART_PLACEHOLDER", source_id="SRC_SSE",
                                    acquired_at=datetime.utcnow(), ok=ok, error=error,
                                    version=1))
        self.s.commit()

    # ── 基线：无先行权利决定时零网络 ────────────────────────────────
    def _denied_adapter(self, status_text):
        """专用矩阵：SRC_SSE 状态可控（矩阵驱动零网络测试）。"""
        import copy
        mx = copy.deepcopy(MATRIX)
        for d in mx["data_sources"]:
            if d["source_key"] == "SRC_SSE":
                d["actions"]["automated_acquisition"] = status_text  # OI-PF-128：领域键
        return SSEAdapter(RightsGuard(matrix=mx), min_interval=0.0)

    def test_unknown_zero_network(self):
        ad = self._denied_adapter("UNKNOWN（测试）")
        with mock.patch("sse_adapter.urllib.request.urlopen") as m:
            with self.assertRaises(GuardDenied):
                ad.fetch("/disclosure/")
            m.assert_not_called()

    def test_prohibited_zero_network(self):
        ad = self._denied_adapter("PROHIBITED（测试）")
        with mock.patch("sse_adapter.urllib.request.urlopen") as m:
            with self.assertRaises(GuardDenied):
                ad.fetch("/disclosure/")
            m.assert_not_called()

    # ── 正例：ALLOWED + 200 ─────────────────────────────────────────
    def test_allowed_success(self):
        with mock.patch("sse_adapter.urllib.request.urlopen",
                        return_value=_resp(200, b"<html>announcement</html>")):
            r = self.ad.fetch("/disclosure/",
                              record_event=self._record_event, event_id="EVT_OK")
        self.assertEqual(r["status"], 200)
        self.assertIn(b"announcement", r["payload"])
        ev = self.s.query(AcquisitionEvent).filter_by(id="EVT_OK").first()
        self.assertEqual(ev.ok, True)

    # ── BF-04：403/429 即停且失败关闭 ───────────────────────────────
    def test_403_fail_closed(self):
        with mock.patch("sse_adapter.urllib.request.urlopen",
                        side_effect=__import__("urllib.error").error.HTTPError(
                            "url", 403, "Forbidden", None, None)):
            with self.assertRaises(RuntimeError) as ctx:
                self.ad.fetch("/disclosure/",
                              record_event=self._record_event, event_id="EVT_403")
        self.assertIn("E-G2-04-002", str(ctx.exception))
        ev = self.s.query(AcquisitionEvent).filter_by(id="EVT_403").first()
        self.assertEqual(ev.ok, False)
        self.assertIn("403", ev.error)

    def test_429_fail_closed(self):
        with mock.patch("sse_adapter.urllib.request.urlopen",
                        side_effect=__import__("urllib.error").error.HTTPError(
                            "url", 429, "Too Many Requests", None, None)):
            with self.assertRaises(RuntimeError):
                self.ad.fetch("/disclosure/",
                              record_event=self._record_event, event_id="EVT_429")
        ev = self.s.query(AcquisitionEvent).filter_by(id="EVT_429").first()
        self.assertEqual(ev.ok, False)
        self.assertIn("429", ev.error)

    # ── BF-04：超时中止 ─────────────────────────────────────────────
    def test_timeout_fail_closed(self):
        with mock.patch("sse_adapter.urllib.request.urlopen",
                        side_effect=TimeoutError("timed out")):
            with self.assertRaises(RuntimeError):
                self.ad.fetch("/disclosure/",
                              record_event=self._record_event, event_id="EVT_TO")
        ev = self.s.query(AcquisitionEvent).filter_by(id="EVT_TO").first()
        self.assertEqual(ev.ok, False)
        self.assertIn("超时", ev.error)

    # ── BF-04：幂等 —— 重试不产生重复 AcquisitionEvent ──────────────
    def test_retry_no_duplicate_event(self):
        """同一取得事件重试：event_id 相同 → 唯一（数据库层面去重防重放）。"""
        with mock.patch("sse_adapter.urllib.request.urlopen",
                        return_value=_resp(200)):
            self.ad.fetch("/disclosure/",
                          record_event=self._record_event, event_id="EVT_IDEMP")
        # 重试同一事件（相同 event_id）：已存在则拒绝（E-WRITE-003 风格）
        from sqlalchemy.exc import IntegrityError
        with mock.patch("sse_adapter.urllib.request.urlopen",
                        return_value=_resp(200)):
            try:
                self.ad.fetch("/disclosure/",
                              record_event=self._record_event, event_id="EVT_IDEMP")
                self.fail("重复 event_id 应被拒")
            except Exception:
                self.s.rollback()  # 失败后回滚（PendingRollback 处置）
        n = self.s.query(AcquisitionEvent).filter_by(id="EVT_IDEMP").count()
        self.assertEqual(n, 1, "重试不得产生重复 AcquisitionEvent")

    # ── BF-04：来源权利失效后新请求为零 ─────────────────────────────
    def test_rights_revoked_zero_requests(self):
        """条款变化（source 转 PROHIBITED）后，新请求/缓存为零。"""
        ad = self._denied_adapter("PROHIBITED（测试）")
        with mock.patch("sse_adapter.urllib.request.urlopen") as m:
            with self.assertRaises(GuardDenied):
                ad.fetch("/disclosure/")
            m.assert_not_called()

    # ── 保守限速：请求间隔 ──────────────────────────────────────────
    def test_rate_limit_min_interval(self):
        ad = SSEAdapter(self.guard, min_interval=0.05)
        with mock.patch("sse_adapter.urllib.request.urlopen",
                        return_value=_resp(200)) as m:
            ad.fetch("/a")
            t0 = __import__("time").time()
            ad.fetch("/b")
            elapsed = __import__("time").time() - t0
        self.assertGreaterEqual(elapsed, 0.04, "两次请求须有最小间隔")
        self.assertEqual(m.call_count, 2)


if __name__ == "__main__":
    unittest.main()
