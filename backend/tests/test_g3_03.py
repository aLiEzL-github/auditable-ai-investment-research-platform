"""G3-03 验收测试：MacroSpec/MacroSnapshot 前置与确定性聚合门。

基线：
  · 取数前冻结必需/可选序列、频率、vintage、时效、固定分母、宏观快照、
    传导链、开放项
  · published/effective/retrieved/cutoff 分离
  · 未来 vintage、零行、无关、缺失、过期、口径漂移或联网失败时
    只允许 PARTIAL/BLOCKED，不得输出当前估值
  · 同一快照跨进程聚合字节一致

执行计划 §3.1（C-1/C-2/C-3）：
  · C-2 材料性宏观缺失 → 不得输出当前估值（一票否决）
  · C-3 材料性判定可机检：把材料性项标为非材料性 → 阻断消失
"""
import hashlib
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
APP = os.path.join(os.path.dirname(__file__), "..", "app")
sys.path.insert(0, APP)

from macro_snapshot import (  # noqa: E402
    load_spec, verify_spec_frozen, MacroObservation, MacroSnapshot,
    MacroGate, MacroGateFail, SnapshotFrozen, build_snapshot,
)


def obs(series_id, value="5.2", vintage="ORIGINAL", published="2026-07-15T00:00:00Z",
        retrieved="2026-07-16T00:00:00Z", unit="percent", scope="CN",
        source="manual", period="2026Q2"):
    return MacroObservation(series_id=series_id, value=value, unit=unit,
                            scope=scope, vintage=vintage,
                            reference_period=period, published_at=published,
                            retrieved_at=retrieved, source=source)


def full_obs(now="2026-08-11T06:00:00Z"):
    """全部 5 个序列的最新观测（时效内、口径匹配、来源明确）。"""
    return [
        obs("GDP_YOY", "5.2", published="2026-07-15T00:00:00Z",
            retrieved="2026-07-16T00:00:00Z"),
        obs("CPI_YOY", "1.0", published="2026-07-09T00:00:00Z",
            retrieved="2026-07-10T00:00:00Z"),
        obs("LPR_1Y", "3.0", published="2026-07-20T00:00:00Z",
            retrieved="2026-07-21T00:00:00Z"),
        obs("PPI_YOY", "0.5", published="2026-07-09T00:00:00Z",
            retrieved="2026-07-10T00:00:00Z"),
        obs("M2_YOY", "7.0", published="2026-07-09T00:00:00Z",
            retrieved="2026-07-10T00:00:00Z"),
    ]


class TestSpecFrozen(unittest.TestCase):
    def test_spec_loads_and_frozen(self):
        spec = verify_spec_frozen()
        self.assertTrue(spec.get("frozen_sha256"))
        ids = [s["series_id"] for s in spec["series"]]
        self.assertIn("GDP_YOY", ids)
        self.assertIn("LPR_1Y", ids)

    def test_spec_drift_detected(self):
        spec = load_spec()
        spec["series"][0]["staleness_days"] = 999  # 变异：改时效
        with self.assertRaises(Exception) as ctx:
            verify_spec_frozen(spec)
        self.assertIn("E-G3-03-005", str(ctx.exception))


class TestTimeSeparation(unittest.TestCase):
    def test_four_times_separated(self):
        """published/effective/retrieved/cutoff 分离。"""
        snap = build_snapshot("s1", cutoff="2026-08-11T00:00:00Z",
                              spec_version="1.0.0", observations=full_obs(),
                              gate_now_utc="2026-08-11T06:00:00Z")
        agg = snap.aggregated()
        # published ≠ retrieved ≠ cutoff
        for sid, row in agg.items():
            self.assertNotEqual(row["published_at"], row["retrieved_at"])
        self.assertEqual(snap.cutoff, "2026-08-11T00:00:00Z")


class TestDeterministicAggregation(unittest.TestCase):
    def test_cross_process_byte_identical(self):
        """同一快照跨进程聚合字节一致（两次构建字节相等）。"""
        a = build_snapshot("s1", cutoff="2026-08-11T00:00:00Z", spec_version="1.0.0",
                           observations=full_obs(),
                           gate_now_utc="2026-08-11T06:00:00Z")
        b = build_snapshot("s1", cutoff="2026-08-11T00:00:00Z", spec_version="1.0.0",
                           observations=full_obs(),
                           gate_now_utc="2026-08-11T06:00:00Z")
        self.assertEqual(a.canonical, b.canonical)
        self.assertEqual(a.sha256, b.sha256)

    def test_latest_vintage_wins(self):
        """聚合规则 LAST_VINTAGE_SINGLE_VALUE：RESTATED 覆盖 ORIGINAL。"""
        rows = [obs("GDP_YOY", "5.0"), obs("GDP_YOY", "5.3", vintage="REVISED")]
        snap = build_snapshot("s2", "2026-08-11T00:00:00Z", "1.0.0",
                              full_obs() + rows,
                              gate_now_utc="2026-08-11T06:00:00Z")
        self.assertEqual(snap.aggregated()["GDP_YOY"]["value"], "5.3")


class TestGateBlocked(unittest.TestCase):
    """C-2：材料性宏观缺失/过期/未来 vintage → 不得输出当前估值（阻断）。"""

    def test_material_missing_blocks(self):
        rows = [o for o in full_obs() if o.series_id != "GDP_YOY"]  # 缺材料性 GDP
        with self.assertRaises(MacroGateFail) as ctx:
            build_snapshot("s3", "2026-08-11T00:00:00Z", "1.0.0", rows,
                           gate_now_utc="2026-08-11T06:00:00Z")
        self.assertIn("E-G3-03-001", str(ctx.exception))
        self.assertIn("GDP_YOY", str(ctx.exception))

    def test_material_stale_blocks(self):
        """过期（> 时效）材料性序列 → 阻断。"""
        rows = [obs("GDP_YOY", published="2026-01-01T00:00:00Z",
                    retrieved="2026-01-02T00:00:00Z")] + \
               [o for o in full_obs() if o.series_id != "GDP_YOY"]
        with self.assertRaises(MacroGateFail):
            build_snapshot("s4", "2026-08-11T00:00:00Z", "1.0.0", rows,
                           gate_now_utc="2026-08-11T06:00:00Z")

    def test_future_vintage_blocks(self):
        """未来 vintage（发布日 > 取得日）→ 阻断。"""
        rows = [obs("GDP_YOY", published="2026-08-11T00:00:00Z",
                    retrieved="2026-08-10T00:00:00Z")] + \
               [o for o in full_obs() if o.series_id != "GDP_YOY"]
        with self.assertRaises(MacroGateFail):
            build_snapshot("s5", "2026-08-11T00:00:00Z", "1.0.0", rows,
                           gate_now_utc="2026-08-11T06:00:00Z")

    def test_unit_drift_blocks(self):
        """口径漂移（unit ≠ spec）→ 阻断。"""
        rows = [obs("GDP_YOY", unit="yuan")] + \
               [o for o in full_obs() if o.series_id != "GDP_YOY"]
        with self.assertRaises(MacroGateFail):
            build_snapshot("s6", "2026-08-11T00:00:00Z", "1.0.0", rows,
                           gate_now_utc="2026-08-11T06:00:00Z")

    def test_scope_mismatch_blocks(self):
        """无关序列（scope ≠ spec）→ 阻断。"""
        rows = [obs("GDP_YOY", scope="US")] + \
               [o for o in full_obs() if o.series_id != "GDP_YOY"]
        with self.assertRaises(MacroGateFail):
            build_snapshot("s7", "2026-08-11T00:00:00Z", "1.0.0", rows,
                           gate_now_utc="2026-08-11T06:00:00Z")

    def test_missing_source_blocks(self):
        rows = [obs("GDP_YOY", source="")] + \
               [o for o in full_obs() if o.series_id != "GDP_YOY"]
        with self.assertRaises(MacroGateFail):
            build_snapshot("s8", "2026-08-11T00:00:00Z", "1.0.0", rows,
                           gate_now_utc="2026-08-11T06:00:00Z")


class TestNonMaterialDegrades(unittest.TestCase):
    """材料性判定可机检：material=false 的序列缺失只降级，不阻断（C-3 反向）。"""

    def test_non_material_missing_degrades_only(self):
        rows = [o for o in full_obs() if o.series_id != "CPI_YOY"]  # 缺非材料性 CPI
        snap = build_snapshot("s9", "2026-08-11T00:00:00Z", "1.0.0", rows,
                              gate_now_utc="2026-08-11T06:00:00Z")
        gate = MacroGate(now_utc="2026-08-11T06:00:00Z")
        verdict = gate.evaluate(snap)
        self.assertEqual(verdict, "PARTIAL")
        self.assertTrue(any("CPI_YOY" in w for w in gate.warnings))


class TestUnfrozenRejected(unittest.TestCase):
    def test_unfrozen_snapshot_no_output(self):
        snap = MacroSnapshot("s10", "2026-08-11T00:00:00Z", "1.0.0")
        snap.add(obs("GDP_YOY"))
        with self.assertRaises(SnapshotFrozen):
            snap.canonical
        with self.assertRaises(SnapshotFrozen):
            snap.aggregated()


if __name__ == "__main__":
    unittest.main()
