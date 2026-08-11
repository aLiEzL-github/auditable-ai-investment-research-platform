"""G3-09 验收测试：版本化 RuleRegistry、适用性与闭合状态机。

基线（G3-09）：
  · §22.1 精确 R01—R10（10 条，定义逐字）
  · 规则版本 / applicability / 固定分母（applicable_count 运行前冻结）
  · 未知状态不能映射 PASS；缺输入不得改为 NOT_APPLICABLE；
    N/A 必须有预冻结适用性依据和签名
  · 每条规则有正/负/N/A 合法性测试（登记与状态机层）

执行计划 §3.2（C-4/C-5）：
  · C-4 适用分母运行前冻结：冻结后修改必失败（变异注入）
  · C-5 全部适用硬规则 PASS 可机检；N 为零与「N 条全过」可分辨（⑨）
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
APP = os.path.join(os.path.dirname(__file__), "..", "app")
sys.path.insert(0, APP)

from rule_registry import (  # noqa: E402
    RuleRegistry, Rule, PASS, FAIL, INPUT_MISSING, NOT_COMPARABLE,
    RESTATEMENT_PENDING, NOT_RUN, NOT_APPLICABLE, BLOCKING, STATUSES,
    RuleRegistryError, ApplicableCountFrozen, _RULES,
)


class TestRegisterAll(unittest.TestCase):
    def test_ten_rules_registered(self):
        reg = RuleRegistry()
        reg.register_all()
        self.assertEqual(len(reg.rules), 10)
        self.assertEqual(sorted(reg.rules), [f"R{i:02d}" for i in range(1, 11)])

    def test_rule_ids_definition_matches_baseline(self):
        """§22.1 精确定义逐字登记（抽样核对非 PASS 语义）。"""
        reg = RuleRegistry()
        reg.register_all()
        self.assertIn("INPUT_MISSING", reg.rules["R01"].non_pass_semantics)
        self.assertIn("NOT_COMPARABLE", reg.rules["R08"].non_pass_semantics)
        self.assertIn("RESTATEMENT_PENDING", reg.rules["R10"].non_pass_semantics)
        self.assertEqual(reg.rules["R06"].definition, "资产 = 负债 + 权益")

    def test_duplicate_and_non_canonical_rejected(self):
        reg = RuleRegistry()
        reg.register_all()
        with self.assertRaises(RuleRegistryError) as ctx:
            reg.register(Rule("R01", "x", "y", "1.0", "z"))
        self.assertIn("E-G3-09-001", str(ctx.exception))
        with self.assertRaises(RuleRegistryError) as ctx:
            reg.register(Rule("R99", "x", "y", "1.0", "z"))
        self.assertIn("E-G3-09-003", str(ctx.exception))

    def test_register_with_status_rejected(self):
        reg = RuleRegistry()
        with self.assertRaises(RuleRegistryError) as ctx:
            reg.register(Rule("R01", "x", "y", "1.0", "z",
                              statuses={"600089": PASS}))
        self.assertIn("E-G3-09-002", str(ctx.exception))


class TestApplicableCountFrozen(unittest.TestCase):
    """C-4：适用分母运行前冻结。"""

    def test_frozen_before_use(self):
        reg = RuleRegistry()
        reg.register_all()
        with self.assertRaises(RuleRegistryError) as ctx:
            _ = reg.applicable_count  # 未冻结即读 → 拒绝
        self.assertIn("E-G3-09-006", str(ctx.exception))
        reg.freeze_applicable_count(7)
        self.assertEqual(reg.applicable_count, 7)

    def test_mutation_after_freeze_fails(self):
        """变异注入：冻结后再次冻结必须失败（运行中修改分母 FAIL）。"""
        reg = RuleRegistry()
        reg.freeze_applicable_count(5)
        with self.assertRaises(ApplicableCountFrozen) as ctx:
            reg.freeze_applicable_count(4)  # 缩小分母
        self.assertIn("E-G3-09-004", str(ctx.exception))

    def test_negative_rejected(self):
        reg = RuleRegistry()
        with self.assertRaises(RuleRegistryError):
            reg.freeze_applicable_count(-1)


class TestStatusMachine(unittest.TestCase):
    def setUp(self):
        self.reg = RuleRegistry()
        self.reg.register_all()
        self.reg.freeze_applicable_count(2)

    def test_unknown_status_rejected(self):
        """未知状态不能映射 PASS。"""
        with self.assertRaises(RuleRegistryError) as ctx:
            self.reg.record_status("R01", "600089", "SUPER_PASS")
        self.assertIn("E-G3-09-008", str(ctx.exception))

    def test_na_requires_basis_and_signature(self):
        """N/A 必须有预冻结适用性依据和签名。"""
        with self.assertRaises(RuleRegistryError) as ctx:
            self.reg.record_status("R01", "600089", NOT_APPLICABLE,
                                   applicability_basis="x")
        self.assertIn("E-G3-09-010", str(ctx.exception))
        with self.assertRaises(RuleRegistryError) as ctx:
            self.reg.record_status("R01", "600089", NOT_APPLICABLE,
                                   signature="s")
        self.assertIn("E-G3-09-009", str(ctx.exception))
        # 合法 N/A：依据 + 签名
        self.reg.record_status("R01", "600089", NOT_APPLICABLE,
                               applicability_basis="披露框架不要求（§22.1 R01）",
                               signature="frozen-applicability-2026-08-11")

    def test_input_missing_not_mapped_to_na(self):
        """缺输入不得改为 NOT_APPLICABLE：缺输入应记 INPUT_MISSING。"""
        self.reg.record_status("R04", "600089", INPUT_MISSING)
        self.assertIn("GATE_BLOCKED", self.reg.gate_verdict())

    def test_all_pass_gate_ok(self):
        self.reg.record_status("R02", "600089", PASS)
        self.reg.record_status("R07", "600089", PASS)
        self.assertIn("GATE_OK", self.reg.gate_verdict())

    def test_blocking_states_propagate(self):
        """任一 FAIL/INPUT_MISSING/NOT_COMPARABLE/RESTATEMENT_PENDING/
        NOT_RUN → GATE_BLOCKED（传播到 Fact/Claim/OpenItem 的机器保证）。"""
        for st in (FAIL, INPUT_MISSING, NOT_COMPARABLE,
                   RESTATEMENT_PENDING, NOT_RUN):
            reg = RuleRegistry()
            reg.register_all()
            reg.freeze_applicable_count(1)
            reg.record_status("R03", "600089", st)
            self.assertIn("GATE_BLOCKED", reg.gate_verdict(),
                          f"{st} 必须阻断硬规则 Gate")

    def test_unregistered_rule_rejected(self):
        with self.assertRaises(RuleRegistryError) as ctx:
            self.reg.record_status("R99", "600089", PASS)
        self.assertIn("E-G3-09-007", str(ctx.exception))


class TestReportApplicable(unittest.TestCase):
    """C-5 / ⑨：适用 N 条可机检；N=0 与「全过」可分辨。"""

    def test_zero_distinct_from_all_pass(self):
        reg = RuleRegistry()
        reg.register_all()
        reg.freeze_applicable_count(0)
        msg = reg.report_applicable()
        self.assertIn("适用 0 条", msg)
        self.assertIn("区分", msg)

    def test_n_report(self):
        reg = RuleRegistry()
        reg.register_all()
        reg.freeze_applicable_count(2)
        reg.record_status("R02", "600089", PASS)
        reg.record_status("R07", "600089", PASS)
        self.assertIn("适用 2 条、全部 PASS", reg.report_applicable())


if __name__ == "__main__":
    unittest.main()
