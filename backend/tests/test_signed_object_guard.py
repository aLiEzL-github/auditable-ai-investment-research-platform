"""已签对象写盘前置的行为与**覆盖**（A §10.3）。

单点修复挡不住下一次：`acceptance_fixpoint` 早就会跳过已签包，而十个
`build_gateN_acceptance.py` **一个真拦的都没有** —— `build_gate1/2/3/4` 里
出现过 `ACTIVE` 字样，逐条读下来全是注释（「本改动不重新生成该验收包」），
是写给人看的行为约束。2026-08-17 实测后果：六份已签包全部漂移。

  X-1  判据本体：已签拒绝 / 未签放行 / 点名例外放行 / 通配不放行 / 读不动即拒
  X-2  **覆盖**：每个 build_gate*_acceptance.py 都须调用 refuse_if_signed
  X-3  **次序**：调用须在任何写盘动作**之前** —— 放在之后等于先破坏再报错
  X-4  变异注入：拿掉任一处调用 → X-2 判红
"""
import ast
import json
import os
import re
import sys
import tempfile
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_TOOLS = os.path.join(_HERE, "..", "tools")
sys.path.insert(0, _TOOLS)

from signed_object_guard import (  # noqa: E402
    ENV_OVERRIDE, active_signed_objects, refuse_if_signed,
)

_GEN_RE = re.compile(r"^build_gate\w+_acceptance\.py$")


def _ledger(tmp, signed=("Gate1-验收包.md",)):
    d = os.path.join(tmp, "gate-records")
    os.makedirs(d, exist_ok=True)
    for i, obj in enumerate(signed):
        json.dump({"signature_status": "ACTIVE",
                   "subject": {"object": f"portfolio/{obj}"}},
                  open(os.path.join(d, f"G{i}-acceptance.json"), "w",
                       encoding="utf-8"), ensure_ascii=False)
    return tmp


class TestGuardBehaviour(unittest.TestCase):

    def test_signed_target_refused(self):
        with tempfile.TemporaryDirectory() as t:
            _ledger(t)
            with self.assertRaises(SystemExit) as ctx:
                refuse_if_signed(t, os.path.join(t, "Gate1-验收包.md"))
            self.assertIn("E-SIGN-001", str(ctx.exception))

    def test_unsigned_target_allowed(self):
        with tempfile.TemporaryDirectory() as t:
            _ledger(t)
            self.assertIsNone(refuse_if_signed(t, os.path.join(t, "Gate9-验收包.md")))

    def test_no_gate_records_allowed(self):
        """尚无任何签署记录 → 放行（首次生成不能被挡）。"""
        with tempfile.TemporaryDirectory() as t:
            self.assertIsNone(refuse_if_signed(t, os.path.join(t, "Gate1-验收包.md")))

    def test_unreadable_records_refused(self):
        """读不动即拒 —— 不能证明未签，就不许写（默认拒绝）。"""
        with tempfile.TemporaryDirectory() as t:
            _ledger(t)
            with open(os.path.join(t, "gate-records", "broken.json"), "w") as f:
                f.write("not json")
            with self.assertRaises(SystemExit) as ctx:
                refuse_if_signed(t, os.path.join(t, "Gate1-验收包.md"))
            self.assertIn("E-SIGN-000", str(ctx.exception))

    def test_named_override_allows(self):
        with tempfile.TemporaryDirectory() as t:
            _ledger(t)
            os.environ[ENV_OVERRIDE] = "Gate1-验收包.md"
            try:
                self.assertIsNone(
                    refuse_if_signed(t, os.path.join(t, "Gate1-验收包.md")))
            finally:
                os.environ.pop(ENV_OVERRIDE, None)

    def test_wildcard_override_still_refused(self):
        """例外必须**点名到具体文件** —— 通配不构成「我知道我在重生成哪一份」。"""
        with tempfile.TemporaryDirectory() as t:
            _ledger(t)
            for val in ("1", "true", "*", "ALL", "Gate2-验收包.md"):
                os.environ[ENV_OVERRIDE] = val
                try:
                    with self.assertRaises(SystemExit, msg=f"{val!r} 不应放行"):
                        refuse_if_signed(t, os.path.join(t, "Gate1-验收包.md"))
                finally:
                    os.environ.pop(ENV_OVERRIDE, None)

    def test_active_only(self):
        """非 ACTIVE 的记录不锚定对象（终态记录可重新生成）。"""
        with tempfile.TemporaryDirectory() as t:
            d = os.path.join(t, "gate-records")
            os.makedirs(d)
            json.dump({"signature_status": "SUPERSEDED_BY_DRIFT",
                       "subject": {"object": "portfolio/Gate1-验收包.md"}},
                      open(os.path.join(d, "old.json"), "w", encoding="utf-8"))
            self.assertEqual(active_signed_objects(t), set())


def _generators():
    return sorted(f for f in os.listdir(_TOOLS) if _GEN_RE.match(f))


def _calls_guard_before_write(src):
    """返回 (是否调用, 调用是否在第一处写盘之前)。

    **按 AST 定位写盘，不按正则** —— 本轮为插入这条前置时，正则
    「行首是 import/from」把代码插进了多行 from-import 的括号中间（两个文件
    语法崩）。判据检的是代理（行首字符），不是目标（语句边界）。
    """
    tree = ast.parse(src)
    call_line = write_line = None
    for n in ast.walk(tree):
        if (isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
                and n.func.id == "refuse_if_signed"):
            call_line = n.lineno if call_line is None else min(call_line, n.lineno)
        if (isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
                and n.func.id == "open" and len(n.args) >= 2
                and isinstance(n.args[1], ast.Constant)
                and isinstance(n.args[1].value, str)
                and "w" in n.args[1].value):
            write_line = n.lineno if write_line is None else min(write_line, n.lineno)
    return call_line is not None, (
        call_line is not None and write_line is not None and call_line < write_line)


class TestGuardCoverage(unittest.TestCase):

    def test_every_generator_calls_guard(self):
        """X-2：新增一个生成器而忘了接前置 → 本用例判红，不靠人记得加。"""
        missing = []
        for f in _generators():
            src = open(os.path.join(_TOOLS, f), encoding="utf-8").read()
            if not _calls_guard_before_write(src)[0]:
                missing.append(f)
        self.assertEqual(
            missing, [],
            f"以下生成器未调用 refuse_if_signed：{missing} —— "
            f"每一个都是一条静默覆盖已签对象的路径")

    def test_guard_called_before_write(self):
        """X-3：放在写盘之后等于先破坏再报错。"""
        bad = []
        for f in _generators():
            src = open(os.path.join(_TOOLS, f), encoding="utf-8").read()
            has, before = _calls_guard_before_write(src)
            if has and not before:
                bad.append(f)
        self.assertEqual(bad, [], f"前置调用晚于写盘：{bad}")

    def test_generator_set_not_empty(self):
        """恒空的集合会让上面两条静默通过 —— 份数须与已知一致。"""
        self.assertGreaterEqual(
            len(_generators()), 7,
            f"只扫到 {len(_generators())} 个生成器（已知仓库侧 7 个）—— "
            f"少扫即等于少测")

    def test_coverage_predicate_goes_red(self):
        """X-4 变异注入：拿掉调用 → 判据须判红（用原缺陷形态：只有注释）。"""
        mutant = ('import os\n\n'
                  '# 本改动不重新生成该验收包 —— 它已 ACTIVE 签署\n'
                  'def main():\n'
                  '    with open(pkg, "w", encoding="utf-8") as f:\n'
                  '        f.write("x")\n')
        self.assertFalse(_calls_guard_before_write(mutant)[0],
                         "只有注释而无调用时，判据不应认为已覆盖")
        good = mutant.replace('    with open(',
                              '    refuse_if_signed(PORTFOLIO, pkg)\n    with open(')
        self.assertTrue(_calls_guard_before_write(good)[0])
        self.assertTrue(_calls_guard_before_write(good)[1])


if __name__ == "__main__":
    unittest.main()
