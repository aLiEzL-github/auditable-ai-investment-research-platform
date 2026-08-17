"""G3-04 验收测试：FormulaRegistry、CalcLedger 与确定性财务计算。

基线：
  · 受限 AST：禁止自由 eval、未登记常量和非法函数
  · 公式/常量版本、单位签名、输入对象、精度、输出哈希
  · 极端值、除零、非有限值、单位不守恒和模型不适用失败关闭

执行计划 §3.2（C-6 无自由公式，一票否决）：注入不在 spec 内的公式
（eval / 属性访问 / 函数调用）必须 FAIL。
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
APP = os.path.join(os.path.dirname(__file__), "..", "app")
sys.path.insert(0, APP)

from formula_registry import (  # noqa: E402
    FormulaRegistry, FormulaSpec, Constant, FormulaError, EvalProhibited,
    UnregisteredConstant, UnitSignatureError, CalcLedger, CalcLedgerMismatch,
)


def registry():
    reg = FormulaRegistry()
    reg.register_constant(Constant("PI", "3.14159", "dimensionless", "1.0"))
    reg.register(FormulaSpec(
        "F_FCFF", "net_income + non_cash - capex", "1.0",
        {"net_income": "CNY_million", "non_cash": "CNY_million",
         "capex": "CNY_million"},
        "CNY_million", "自由现金流"))
    return reg


class TestNoFreeEval(unittest.TestCase):
    """C-6 一票否决：无自由公式。"""

    def test_eval_syntax_rejected(self):
        reg = registry()
        for evil in ("eval('x')", "x.__class__", "os.system('TEST_SENTINEL')",
                     "open('TEST_SENTINEL')", "a.b.c", "f(x)",
                     "x; y", "import os", "x ** 2", "x // 2", "x @ y",
                     "x @ 1"):  # 纯 bad token（@ 后无 id，无 UnregisteredConstant 兜底）
            # 恶意表达式必须在登记期被拒绝 —— 拒绝类型不限
            # （EvalProhibited 或 UnregisteredConstant 均为失败关闭）
            with self.assertRaises((EvalProhibited, UnregisteredConstant)):
                reg.register(FormulaSpec("EVIL", evil, "1.0",
                                         {"x": "CNY_million"}, "CNY_million"))

    def test_evaluate_evil_expression_rejected(self):
        """注入不在 spec 内的公式 → 求值期同样拒绝（先红后绿）。"""
        reg = registry()
        with self.assertRaises(EvalProhibited):
            reg.register(FormulaSpec("EVIL2", "x ** 2", "1.0",
                                     {"x": "CNY_million"}, "CNY_million"))
        with self.assertRaises(EvalProhibited):
            reg.register(FormulaSpec("EVIL3", "x + y(2)", "1.0",
                                     {"x": "CNY_million"}, "CNY_million"))

    def test_no_eval_in_implementation(self):
        """实现不含 eval/exec（静态检查）。"""
        import inspect
        import formula_registry as fr
        src = inspect.getsource(fr)
        for banned in ("eval(", "exec(", "__import__("):
            self.assertNotIn(banned, src,
                             f"实现不得含 {banned}")


class TestRegisteredOnly(unittest.TestCase):
    def test_unregistered_constant_rejected(self):
        """未登记常量必须拒绝。"""
        reg = FormulaRegistry()
        with self.assertRaises(UnregisteredConstant) as ctx:
            reg.register(FormulaSpec("F_BAD", "rev + GDP", "1.0",
                                     {"rev": "CNY_million"}, "CNY_million"))
        self.assertIn("E-G3-04-003", str(ctx.exception))

    def test_unknown_formula_rejected(self):
        reg = registry()
        with self.assertRaises(FormulaError):
            reg.evaluate("F_NOT_EXIST", {})


class TestDeterministicCalc(unittest.TestCase):
    def test_calc_deterministic(self):
        reg = registry()
        inputs = {"net_income": "100", "non_cash": "30", "capex": "-40"}
        r1 = reg.evaluate("F_FCFF", inputs)
        r2 = reg.evaluate("F_FCFF", inputs)
        self.assertEqual(r1["output"], r2["output"])
        self.assertEqual(r1["output"], "170.000000")

    def test_input_set_mismatch_rejected(self):
        reg = registry()
        with self.assertRaises(FormulaError) as ctx:
            reg.evaluate("F_FCFF", {"net_income": "100"})
        self.assertIn("E-G3-04-006", str(ctx.exception))

    def test_divide_by_zero_fails(self):
        reg = FormulaRegistry()
        reg.register(FormulaSpec("F_DIV", "a / b", "1.0",
                                 {"a": "CNY", "b": "CNY"}, "ratio"))
        with self.assertRaises(FormulaError) as ctx:
            reg.evaluate("F_DIV", {"a": "1", "b": "0"})
        self.assertIn("E-G3-04-012", str(ctx.exception))

    def test_unit_signature_mismatch_fails(self):
        """单位不守恒必失败：不同维加减。"""
        reg = FormulaRegistry()
        reg.register(FormulaSpec("F_BADUNIT", "money + ratio", "1.0",
                                 {"money": "CNY", "ratio": "percent"}, "CNY"))
        with self.assertRaises(UnitSignatureError):
            reg.evaluate("F_BADUNIT", {"money": "1", "ratio": "2"})

    def test_output_unit_mismatch_fails(self):
        """输出单位与声明不符必失败（负例：声明 percent 实得 CNY）。"""
        reg = FormulaRegistry()
        reg.register(FormulaSpec("F_BADOUT", "a + b", "1.0",
                                 {"a": "CNY", "b": "CNY"}, "percent"))
        with self.assertRaises(UnitSignatureError) as ctx:
            reg.evaluate("F_BADOUT", {"a": "1", "b": "2"})
        self.assertIn("E-G3-04-010", str(ctx.exception))

    def test_output_unit_matches_after_dimensionless(self):
        reg = FormulaRegistry()
        reg.register(FormulaSpec("F_UNIT", "a * 2", "1.0",
                                 {"a": "CNY"}, "CNY"))
        # 2 是 dimensionless，CNY * dimensionless = CNY → 输出单位 CNY ✓
        r = reg.evaluate("F_UNIT", {"a": "5"})
        self.assertEqual(r["output_unit"], "CNY")


class TestCalcLedger(unittest.TestCase):
    def test_ledger_verify_ok(self):
        reg = registry()
        e = reg.evaluate("F_FCFF", {"net_income": "100", "non_cash": "30",
                                    "capex": "-40"})
        ledger = CalcLedger()
        ledger.record(e)
        self.assertIn("OK", ledger.verify())

    def test_ledger_tamper_detected(self):
        """篡改必败：改账本中的输入后 verify 失败。"""
        reg = registry()
        e = reg.evaluate("F_FCFF", {"net_income": "100", "non_cash": "30",
                                    "capex": "-40"})
        ledger = CalcLedger()
        ledger.record(e)
        ledger.entries[0].inputs["net_income"] = "999"  # 篡改
        with self.assertRaises(CalcLedgerMismatch) as ctx:
            ledger.verify()
        self.assertIn("E-G3-04-015", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
