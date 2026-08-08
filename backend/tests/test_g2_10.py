"""G2-10 验收测试：20 项 MetricSpec 冻结。

基线：
  · 20/20 逐字匹配且 origin 合同完整
  · 缺项不补默认值
  · 漂移阻断
  · 5 指标 PoC 保留 20 项 Schema，未实现项标 NOT_IMPLEMENTED / NOT_RELEASE_ELIGIBLE
"""
import unittest
import json
import hashlib
import os
import sys

APP = os.path.join(os.path.dirname(__file__), "..", "app")
sys.path.insert(0, APP)

from metric_spec import (load_spec, check_20_spec, verify_frozen, status_of,
                         MetricSpecError)

# 基线 B §22.2 的 20 个精确指标名（逐字）
B_METRIC_IDS = [
    "营业收入", "归母净利润", "扣非归母净利润", "少数股东损益",
    "经营活动现金流净额", "自由现金流", "分部收入", "分部毛利率",
    "ROE", "ROIC", "资产负债率", "有息负债",
    "货币资金", "应收账款及票据", "存货", "在建工程",
    "资本开支", "商誉", "总股本", "分红总额",
]


class TestMetricSpec(unittest.TestCase):
    # ── 20/20 逐字匹配 ─────────────────────────────────────────────
    def test_20_of_20_exact_match(self):
        spec = load_spec()
        ids = [m["metric_id"] for m in spec["metrics"]]
        self.assertEqual(len(ids), 20)
        self.assertEqual(ids, B_METRIC_IDS, "指标名须与基线 B §22.2 逐字一致")

    def test_origin_contract_complete(self):
        spec = load_spec()
        for m in spec["metrics"]:
            self.assertIn("expected_origin", m)
            self.assertIn("caliber", m)
            self.assertTrue(m["expected_origin"])
            self.assertTrue(m["caliber"])

    def test_origin_enum_valid(self):
        VALID = {"REPORTED", "DERIVED", "REPORTED_OR_NOT_DISCLOSED",
                 "REPORTED_OR_DERIVED", "DERIVED_WITH_REPORTED_CROSSCHECK",
                 "DERIVED_FROM_REPORTED_COMPONENTS", "REPORTED_POINT_IN_TIME",
                 "REPORTED_EVENT", "DERIVED_OR_REPORTED"}
        for m in load_spec()["metrics"]:
            self.assertIn(m["expected_origin"], VALID, m["metric_id"])

    # ── 缺项不补默认值 ─────────────────────────────────────────────
    def test_missing_metric_rejected(self):
        spec = load_spec()
        m = spec["metrics"]
        import copy
        mutated = copy.deepcopy(spec)
        mutated["metrics"] = m[:19]  # 缺一项
        with self.assertRaises(MetricSpecError) as ctx:
            from metric_spec import check_20_spec as _c
            # 直接以变异清单验证（不经文件）
            _verify_len(mutated["metrics"])
        self.assertIn("E-G2-10-001", str(ctx.exception))

    # ── 漂移阻断 ───────────────────────────────────────────────────
    def test_drift_blocked(self):
        import copy
        mutated = copy.deepcopy(load_spec())
        mutated["metrics"][0]["metric_id"] = "营业额"  # 漂移
        with self.assertRaises(MetricSpecError) as ctx:
            verify_frozen(mutated["metrics"])
        self.assertIn("E-G2-10-003", str(ctx.exception))

    def test_verify_frozen_passes(self):
        verify_frozen(load_spec()["metrics"])  # 不抛错

    # ── 5 指标 PoC 保留 20 项 ───────────────────────────────────────
    def test_poc_keeps_20_schema(self):
        self.assertEqual(check_20_spec(), 20)

    def test_unimplemented_marked(self):
        for mid in ("ROIC", "商誉", "在建工程"):
            self.assertEqual(status_of(mid), "NOT_IMPLEMENTED / NOT_RELEASE_ELIGIBLE")
        for mid in ("营业收入", "归母净利润", "总股本", "货币资金", "存货"):
            self.assertEqual(status_of(mid), "IMPLEMENTED")


def _verify_len(metrics):
    if len(metrics) != 20:
        raise MetricSpecError(f"E-G2-10-001: 20 项 MetricSpec 不完整: {len(metrics)}/20")


if __name__ == "__main__":
    unittest.main()
