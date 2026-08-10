"""G2-05 验收测试：宏观主源适配器（mock 网络，不出网）。

基线：
  · 发布日、参考期、取得日三者分离
  · 无先行权利决定时零网络/零缓存
BF-04：403/429 失败关闭 · 保守限速 · 幂等 · 超时 · 权利失效零请求
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
from macro_adapter import MacroAdapter, _parse_publication_date  # noqa: E402
from repository import create_repository, Source, AcquisitionEvent, RawArtifact  # noqa: E402


def _resp(status, body="<html>2026年8月数据发布</html>".encode("utf-8")):
    m = mock.Mock()
    m.status = status
    m.read = mock.Mock(return_value=body)
    m.__enter__ = mock.Mock(return_value=m)
    m.__exit__ = mock.Mock(return_value=False)
    return m


class TestMacroAdapter(unittest.TestCase):
    def setUp(self):
        os.environ["MACRO_BASE_URL"] = "https://example.test"
        self.guard = RightsGuard(matrix=MATRIX)
        self.ad = MacroAdapter(self.guard, min_interval=0.0)
        self._tmp = tempfile.mkdtemp()
        self.repo = create_repository(os.path.join(self._tmp, "g2_05.sqlite3"))
        self.repo.create_all()
        self.s = self.repo.session()
        for sid, st in (("SRC_NBS", "ALLOWED"), ("SRC_UNK", "UNKNOWN"),
                        ("SRC_BAN", "PROHIBITED")):
            self.s.add(Source(id=sid, schema_version="1.0", kind="PRIMARY",
                              name=sid, status=st, legal_basis="G2-05 测试", version=1))
        self.s.commit()
        import hashlib
        self.s.add(RawArtifact(id="ART_PLACEHOLDER", schema_version="1.0",
                               source_id="SRC_NBS", sha256=hashlib.sha256(b"ph").hexdigest(),
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
                                    artifact_id="ART_PLACEHOLDER", source_id="SRC_NBS",
                                    acquired_at=datetime.utcnow(), ok=ok, error=error,
                                    version=1))
        self.s.commit()

    # ── 基线：无先行权利决定时零网络 ────────────────────────────────
    def _denied_adapter(self, status_text):
        import copy
        mx = copy.deepcopy(MATRIX)
        for d in mx["data_sources"]:
            if d["source_key"] == "SRC_NBS":
                d["actions"]["automated_acquisition"] = status_text  # OI-PF-128：领域键
        return MacroAdapter(RightsGuard(matrix=mx), min_interval=0.0)

    def test_unknown_zero_network(self):
        ad = self._denied_adapter("UNKNOWN（测试）")
        with mock.patch("macro_adapter.urllib.request.urlopen") as m:
            with self.assertRaises(GuardDenied):
                ad.fetch("/sj/")
            m.assert_not_called()

    def test_prohibited_zero_network(self):
        ad = self._denied_adapter("PROHIBITED（测试）")
        with mock.patch("macro_adapter.urllib.request.urlopen") as m:
            with self.assertRaises(GuardDenied):
                ad.fetch("/sj/")
            m.assert_not_called()

    # ── 基线：发布日/参考期/取得日三者分离 ──────────────────────────
    def test_three_dates_separated(self):
        body = '<html><title>2026年7月主要指标数据</title></html>'.encode("utf-8")
        with mock.patch("macro_adapter.urllib.request.urlopen",
                        return_value=_resp(200, body)):
            p = self.ad.fetch("/sj/ysj/",
                              record_event=self._record_event, event_id="EVT_M1",
                              reference_period="2026-07")
        # 发布日（从正文提取）≠ 参考期（调用方提供）≠ 取得日（当刻）
        self.assertEqual(p.publication_date, "2026-07-01")
        self.assertEqual(p.reference_period, "2026-07")
        self.assertNotEqual(p.publication_date, p.reference_period)
        self.assertNotEqual(p.acquired_at, p.reference_period)
        self.assertNotEqual(p.acquired_at[:10], p.publication_date)

    def test_publication_date_parse(self):
        self.assertEqual(_parse_publication_date("2026年8月2日发布"), "2026-08-02")
        self.assertEqual(_parse_publication_date("发布于2026-06-30"), "2026-06-30")

    # ── BF-04：403/429 失败关闭 ─────────────────────────────────────
    def test_403_fail_closed(self):
        with mock.patch("macro_adapter.urllib.request.urlopen",
                        side_effect=__import__("urllib.error").error.HTTPError(
                            "u", 403, "Forbidden", None, None)):
            with self.assertRaises(RuntimeError) as ctx:
                self.ad.fetch("/sj/",
                              record_event=self._record_event, event_id="EVT_403")
        self.assertIn("E-G2-05-003", str(ctx.exception))
        ev = self.s.query(AcquisitionEvent).filter_by(id="EVT_403").first()
        self.assertEqual(ev.ok, False)
        self.assertIn("403", ev.error)

    def test_429_fail_closed(self):
        with mock.patch("macro_adapter.urllib.request.urlopen",
                        side_effect=__import__("urllib.error").error.HTTPError(
                            "u", 429, "Too Many", None, None)):
            with self.assertRaises(RuntimeError):
                self.ad.fetch("/sj/",
                              record_event=self._record_event, event_id="EVT_429")
        ev = self.s.query(AcquisitionEvent).filter_by(id="EVT_429").first()
        self.assertEqual(ev.ok, False)

    # ── BF-04：超时 / 幂等 / 权利失效 ───────────────────────────────
    def test_timeout_fail_closed(self):
        with mock.patch("macro_adapter.urllib.request.urlopen",
                        side_effect=TimeoutError()):
            with self.assertRaises(RuntimeError):
                self.ad.fetch("/sj/",
                              record_event=self._record_event, event_id="EVT_TO")
        ev = self.s.query(AcquisitionEvent).filter_by(id="EVT_TO").first()
        self.assertEqual(ev.ok, False)

    def test_retry_no_duplicate_event(self):
        with mock.patch("macro_adapter.urllib.request.urlopen",
                        return_value=_resp(200)):
            self.ad.fetch("/sj/",
                          record_event=self._record_event, event_id="EVT_IDEM")
        with mock.patch("macro_adapter.urllib.request.urlopen",
                        return_value=_resp(200)):
            try:
                self.ad.fetch("/sj/",
                              record_event=self._record_event, event_id="EVT_IDEM")
            except Exception:
                self.s.rollback()
        n = self.s.query(AcquisitionEvent).filter_by(id="EVT_IDEM").count()
        self.assertEqual(n, 1, "重试不得产生重复 AcquisitionEvent")

    # ── BF-04：重试 —— 失败后再次取得是独立事件（OI-PF-014）─────────
    def test_retry_after_failure_new_event(self):
        with mock.patch("macro_adapter.urllib.request.urlopen",
                        side_effect=__import__("urllib.error").error.URLError(
                            "refused")):
            with self.assertRaises(RuntimeError):
                self.ad.fetch("/sj/",
                              record_event=self._record_event, event_id="EVT_R1")
        with mock.patch("macro_adapter.urllib.request.urlopen",
                        return_value=_resp(200)):
            self.ad.fetch("/sj/",
                          record_event=self._record_event, event_id="EVT_R2")
        n = self.s.query(AcquisitionEvent).filter(
            AcquisitionEvent.id.in_(["EVT_R1", "EVT_R2"])).count()
        self.assertEqual(n, 2, "失败 1 条 + 成功 1 条，各自唯一，无重复")

    def test_rights_revoked_zero_requests(self):
        ad = self._denied_adapter("PROHIBITED（测试）")
        with mock.patch("macro_adapter.urllib.request.urlopen") as m:
            with self.assertRaises(GuardDenied):
                ad.fetch("/sj/")
            m.assert_not_called()

    def test_rate_limit(self):
        ad = MacroAdapter(self.guard, min_interval=0.05)
        with mock.patch("macro_adapter.urllib.request.urlopen",
                        return_value=_resp(200)):
            ad.fetch("/a")
            t0 = __import__("time").time()
            ad.fetch("/b")
            elapsed = __import__("time").time() - t0
        self.assertGreaterEqual(elapsed, 0.04)


if __name__ == "__main__":
    unittest.main()
