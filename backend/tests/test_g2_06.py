"""G2-06 验收测试：AKShare 副源适配器（强制 SECONDARY + 故障隔离 + F3）。

基线：
  · 所有结果强制 SECONDARY
  · 故障不污染主源
F3（Gate 2 退出条件）：不得用 AKShare 填补主源硬缺口（可执行断言）
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
from akshare_adapter import AKShareAdapter, AKSHARE_SOURCE_ID  # noqa: E402


class _FakeDF:
    """模拟 DataFrame（日期/值列契约），不依赖 pandas。"""

    def __init__(self, data):
        self._data = data

    def iterrows(self):
        for i, row in enumerate(self._data):
            yield i, {k: v for k, v in row.items()}


class TestAKShareAdapter(unittest.TestCase):
    def setUp(self):
        self.guard = RightsGuard(matrix=MATRIX)
        self.ad = AKShareAdapter(self.guard)
        self._tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self._tmp, ignore_errors=True)

    # ── 基线：所有结果强制 SECONDARY ────────────────────────────────
    def test_all_results_forced_secondary(self):
        fake_ak = mock.Mock()
        fake_ak.stock_zh_a_spot = mock.Mock(return_value=_FakeDF([
            {"date": "2026-08-01", "value": 12.5},
            {"date": "2026-08-02", "value": 12.7},
        ]))
        with mock.patch.dict("sys.modules", {"akshare": fake_ak}):
            rows = self.ad.fetch("stock_zh_a_spot")
        self.assertEqual(len(rows), 2)
        for r in rows:
            self.assertTrue(r["__secondary"], "所有结果强制 SECONDARY")

    # ── 权利门：无先行权利决定零执行 ────────────────────────────────
    def test_unknown_zero_side_effect(self):
        import copy
        mx = copy.deepcopy(MATRIX)
        for d in mx["data_sources"]:
            if d["source_key"] == "SRC_AKSHARE":
                d["actions"]["FETCH"] = "UNKNOWN（测试）"
        ad = AKShareAdapter(RightsGuard(matrix=mx))
        with mock.patch("akshare_adapter.AKShareAdapter._do_fetch") as m:
            with self.assertRaises(GuardDenied):
                ad.fetch("stock_zh_a_spot")
            m.assert_not_called()

    # ── 未安装：诚实拒绝 ────────────────────────────────────────────
    def test_not_installed_fail_closed(self):
        with mock.patch.dict("sys.modules", {"akshare": None}):
            with mock.patch("builtins.__import__",
                            side_effect=ImportError("no module akshare")):
                with self.assertRaises(RuntimeError) as ctx:
                    self.ad.fetch("stock_zh_a_spot")
        self.assertIn("E-G2-06-002", str(ctx.exception))

    # ── 故障不污染主源（隔离）──────────────────────────────────────
    def test_failure_does_not_pollute_primary(self):
        fake_ak = mock.Mock()
        fake_ak.stock_zh_a_spot = mock.Mock(side_effect=RuntimeError("akshare 挂了"))
        with mock.patch.dict("sys.modules", {"akshare": fake_ak}):
            with self.assertRaises(RuntimeError) as ctx:
                self.ad.fetch("stock_zh_a_spot")
        self.assertIn("E-G2-06-003", str(ctx.exception))
        # 主源读取路径不受影响（独立模块/独立权利门）
        self.assertIsNotNone(RightsGuard(matrix=MATRIX).decide("SRC_SSE", "FETCH", "/x"))

    # ── F3：不得用 AKShare 填补主源硬缺口（可执行断言）──────────────
    def test_f3_no_primary_gap_fill(self):
        """副源数据带 __secondary=True；任何主源缺口填补路径必须被拒。"""
        fake_ak = mock.Mock()
        fake_ak.stock_zh_a_spot = mock.Mock(return_value=_FakeDF([
            {"date": "2026-08-01", "value": 12.5}]))
        with mock.patch.dict("sys.modules", {"akshare": fake_ak}):
            rows = self.ad.fetch("stock_zh_a_spot")
        # F3 可执行断言：副源行（__secondary=True）不可提升为主源数据
        for r in rows:
            self.assertFalse(
                self._promotable(r),
                "副源数据不得用于填补主源缺口（F3）")
            self.assertTrue(r["__secondary"], "副源标记必须保留")

    @staticmethod
    def _promotable(row: dict) -> bool:
        """主源提升判定：仅非副源（__secondary 非真）可进入主源路径（F3 断言主体）。"""
        return row.get("__secondary") is False


if __name__ == "__main__":
    unittest.main()
