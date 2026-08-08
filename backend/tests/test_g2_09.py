"""G2-09 验收测试：backtest_mode 市场数据合同。

基线：
  · 三种模式均有机器可读状态
  · 只有 QUALIFIED 真实冒烟并允许后续绩效门
G0-09 已裁：六类全缺 → REMOVED（不得选 QUALIFIED）
"""
import unittest

from backtest_contract import (BacktestContract, current_contract, MODES,
                               REQUIRED_DATA)


class TestBacktestContract(unittest.TestCase):
    # ── 三种模式机器可读 ────────────────────────────────────────────
    def test_three_modes_machine_readable(self):
        self.assertEqual(set(MODES), {"QUALIFIED", "EXPERIMENT_ONLY", "REMOVED"})
        c = current_contract()
        d = c.to_dict()
        self.assertIn("mode", d)
        self.assertIn("data_status", d)
        self.assertIn("qualified", d)
        self.assertEqual(d["mode"], "REMOVED")

    def test_required_data_six_classes(self):
        self.assertEqual(len(REQUIRED_DATA), 6)

    # ── 当前状态：G0-09 已裁 REMOVED（六类全缺）────────────────────
    def test_current_is_removed(self):
        c = current_contract()
        self.assertEqual(c.mode, "REMOVED")
        self.assertFalse(c.is_qualified())
        for v in c.data_status.values():
            self.assertEqual(v, "UNAVAILABLE")

    # ── 来源/权利不足不得选 QUALIFIED ───────────────────────────────
    def test_qualified_rejected_when_unavailable(self):
        c = current_contract()
        with self.assertRaises(ValueError) as ctx:
            c.select_mode("QUALIFIED")
        self.assertIn("E-G2-09-002", str(ctx.exception))
        # 降级选择合法
        self.assertEqual(c.select_mode("EXPERIMENT_ONLY"), "EXPERIMENT_ONLY")
        c2 = current_contract()
        self.assertEqual(c2.select_mode("REMOVED"), "REMOVED")

    def test_illegal_mode_rejected(self):
        with self.assertRaises(ValueError) as ctx:
            current_contract().select_mode("FANCY")
        self.assertIn("E-G2-09-001", str(ctx.exception))

    # ── 只有 QUALIFIED 允许后续绩效门 ───────────────────────────────
    def test_performance_gate_denied_non_qualified(self):
        c = current_contract()  # REMOVED
        with self.assertRaises(ValueError) as ctx:
            c.check_performance_gate()
        self.assertIn("E-G2-09-003", str(ctx.exception))
        c.select_mode("EXPERIMENT_ONLY")
        with self.assertRaises(ValueError):
            c.check_performance_gate()

    def test_performance_gate_allowed_only_qualified(self):
        c = BacktestContract(mode="QUALIFIED",
                             data_status={k: "AVAILABLE" for k in REQUIRED_DATA})
        self.assertTrue(c.is_qualified())
        c.check_performance_gate()  # 不抛错


if __name__ == "__main__":
    unittest.main()
