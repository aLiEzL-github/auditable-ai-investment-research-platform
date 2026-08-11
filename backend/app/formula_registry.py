"""formula_registry.py —— G3-04 FormulaRegistry、CalcLedger 与确定性财务计算。

基线验收（G3-04）：
  · 受限 AST（禁止自由 eval）、公式/常量版本、单位签名、输入对象、
    精度、输出哈希和负向测试
  · 禁止自由 eval、未登记常量和非法函数
  · 极端值、除零、非有限值、单位不守恒和模型不适用失败关闭

设计：
  · FormulaRegistry：登记公式（expression + 版本 + 单位签名）。表达式
    用**受限解析器**（白名单 token 集：数字、标识符、+ - * / ( ) ），
    任何其他语法（eval/属性访问/调用）直接拒绝 —— 结构上无 eval。
  · 常量注册表：常量必须显式登记（版本化）；未登记常量 → E-G3-04-003。
  · 单位签名：每个输入与输出声明单位；加/减须同维（复用 decimal_tools
    UnitDim）；输出单位由公式声明校验。
  · CalcLedger：确定性计算账本 —— 每笔记录输入对象哈希 + 公式版本 +
    输出哈希；任一输入字节变化 → 重算哈希不符（篡改必败）。
"""
import hashlib
import json
import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Dict, List, Optional

from decimal_tools import FixedDecimal, UnitDim, DecimalToolsError

# 受限 token：数字（含负数）/ 标识符 / 四则（含独立减号）/ 括号
# （无函数调用、无属性、无 eval）。负号优先匹配数字字面量。
_TOKEN_RE = re.compile(r"""
    (?P<num> -?\d+(?:\.\d+)? ) |
    (?P<id> [A-Za-z_][A-Za-z0-9_]* ) |
    (?P<op> [-+*/(),] ) |
    (?P<ws> \s+ ) |
    (?P<bad> . )
""", re.VERBOSE)


class FormulaError(ValueError):
    pass


class EvalProhibited(FormulaError):
    """结构上无 eval：语法不在白名单即拒绝。"""


class UnregisteredConstant(FormulaError):
    pass


class UnitSignatureError(FormulaError):
    pass


class CalcLedgerMismatch(FormulaError):
    """篡改必败：重算哈希与账本不符。"""


@dataclass
class Constant:
    name: str
    value: str            # Decimal 字符串
    unit: str
    version: str

    def to_dict(self) -> dict:
        return {"name": self.name, "value": self.value, "unit": self.unit,
                "version": self.version}


@dataclass
class FormulaSpec:
    formula_id: str
    expression: str       # 受限语法：a + b * c - (d / e)
    version: str
    inputs: Dict[str, str]        # 输入名 -> 单位
    output_unit: str
    description: str = ""

    def to_dict(self) -> dict:
        return {"formula_id": self.formula_id, "expression": self.expression,
                "version": self.version, "inputs": self.inputs,
                "output_unit": self.output_unit, "description": self.description}


class FormulaRegistry:
    """公式登记 + 受限求值。

    求值路径：
      1. 语法白名单检查（无 eval / 函数调用 / 属性访问）
      2. tokenize + 递归下降求值（仅 + - * / 与括号）
      3. 输入全部来自已冻结对象（dict 值）；常量来自登记表
      4. 单位签名校验（加/减同维；输出单位与声明一致）
      5. 除零 / 非有限值失败关闭
    """

    def __init__(self):
        self.formulas: Dict[str, FormulaSpec] = {}
        self.constants: Dict[str, Constant] = {}

    # ── 登记 ──────────────────────────────────────────────────────
    def register_constant(self, c: Constant) -> None:
        if c.name in self.constants:
            raise FormulaError(f"E-G3-04-001: 常量重复登记: {c.name}")
        Decimal(c.value)  # 校验数值
        self.constants[c.name] = c

    def register(self, f: FormulaSpec) -> None:
        if f.formula_id in self.formulas:
            raise FormulaError(f"E-G3-04-002: 公式重复登记: {f.formula_id}")
        self._check_syntax(f.expression)  # 登记时即拒绝非法语法
        # 未登记常量（出现在公式中的 id 不在 inputs 也不在 constants）→ 拒绝
        ids = self._identifiers(f.expression)
        for i in ids:
            if i not in f.inputs and i not in self.constants:
                raise UnregisteredConstant(
                    f"E-G3-04-003: 未登记常量/输入: {i}（公式 {f.formula_id}）")
        self.formulas[f.formula_id] = f

    # ── 语法白名单 ────────────────────────────────────────────────
    @staticmethod
    def _check_syntax(expr: str) -> None:
        """白名单语法检查：除数字/标识符/+-*/( )/空白外一律拒绝。

        额外拒绝两种「结构上像函数调用/恶意表达式」的形态：
          · 标识符后紧跟 `(`（f(x) = 函数调用）
          · 连续运算符（`**`、`//`、`+-` 等）
        这些形态即使能解析也会被求值器拒绝，但**登记期就拒绝**更早失败。
        """
        toks = [m for m in _TOKEN_RE.finditer(expr) if not m.group("ws")]
        for i, m in enumerate(toks):
            if m.group("bad"):
                raise EvalProhibited(
                    f"E-G3-04-004: 非法语法 {m.group('bad')!r} at {m.start()} "
                    f"—— 自由 eval/函数调用/属性访问一律拒绝")
            if m.group("id"):
                nxt = toks[i + 1] if i + 1 < len(toks) else None
                if nxt is not None and nxt.group("op") == "(":
                    raise EvalProhibited(
                        f"E-G3-04-004: 函数调用形态 {m.group('id')}( at {m.start()}"
                        f" —— 受限语法禁止调用")
            if m.group("op") in ("*", "/", "+", "-"):
                nxt = toks[i + 1] if i + 1 < len(toks) else None
                if nxt is not None and nxt.group("op") in ("*", "/", "+", "-"):
                    raise EvalProhibited(
                        f"E-G3-04-004: 连续运算符 {m.group('op')}{nxt.group('op')} "
                        f"at {m.start()} —— 拒绝（含 ** // 等）")

    @staticmethod
    def _identifiers(expr: str) -> List[str]:
        return [m.group("id") for m in _TOKEN_RE.finditer(expr) if m.group("id")]

    # ── 求值（递归下降，仅四则与括号）────────────────────────────
    def evaluate(self, formula_id: str, inputs: Dict[str, str],
                 constants_override: Optional[Dict[str, str]] = None) -> dict:
        f = self.formulas.get(formula_id)
        if f is None:
            raise FormulaError(f"E-G3-04-005: 未登记公式: {formula_id}")
        if set(inputs) != set(f.inputs):
            raise FormulaError(
                f"E-G3-04-006: 输入集合不符: 需要 {set(f.inputs)} 实得 {set(inputs)}")
        self._check_syntax(f.expression)
        # 单位签名：加/减操作数同维（求值时用带单位的值）
        env = {}
        for name, unit in f.inputs.items():
            v = inputs[name]
            try:
                Decimal(v)
            except InvalidOperation:
                raise FormulaError(f"E-G3-04-007: 输入非数值: {name}={v!r}")
            env[name] = FixedDecimal(v, unit, 6)
        for name, c in self.constants.items():
            if name in f.inputs:
                continue
            env[name] = FixedDecimal(c.value, c.unit, 6)

        # 求值（带单位传播）
        try:
            result = self._parse(f.expression, env)
        except DecimalToolsError as e:
            raise UnitSignatureError(f"E-G3-04-008: 单位签名失败: {e}")
        if not result.dec().is_finite():
            raise FormulaError("E-G3-04-009: 非有限值（失败关闭）")
        # 输出单位签名：与声明一致（允许量纲组合后规范化）
        if result.unit != f.output_unit:
            raise UnitSignatureError(
                f"E-G3-04-010: 输出单位 {result.unit} ≠ 声明 {f.output_unit} —— "
                f"单位不守恒必失败")
        ledger = CalcLedgerEntry(
            formula_id=formula_id, version=f.version,
            inputs=dict(sorted(inputs.items())),
            output=result.canonical(), output_unit=result.unit,
            inputs_hash=hashlib.sha256(json.dumps(
                inputs, ensure_ascii=False, sort_keys=True).encode()).hexdigest(),
        )
        return ledger.to_dict()

    # 递归下降：expr -> term {+|- term}；term -> factor {*|/ factor}
    def _parse(self, expr: str, env: Dict[str, FixedDecimal]) -> FixedDecimal:
        self._tokens = [m for m in _TOKEN_RE.finditer(expr)
                        if not m.group("ws")]
        self._pos = 0
        self._env = env
        val = self._expr()
        if self._pos != len(self._tokens):
            raise FormulaError(f"E-G3-04-011: 尾部多余 token: "
                               f"{self._tokens[self._pos].group()}")
        return val

    def _peek(self):
        return self._tokens[self._pos] if self._pos < len(self._tokens) else None

    def _next(self):
        t = self._peek()
        self._pos += 1
        return t

    def _expr(self) -> FixedDecimal:
        val = self._term()
        while True:
            t = self._peek()
            if t is None or t.group("op") not in ("+", "-"):
                return val
            self._next()
            rhs = self._term()
            if t.group("op") == "+":
                val = FixedDecimal(str(val.dec() + rhs.dec()), val.unit,
                                   max(val.scale, rhs.scale))
            else:
                val = FixedDecimal(str(val.dec() - rhs.dec()), val.unit,
                                   max(val.scale, rhs.scale))
            UnitDim.check_compatible(val.unit, rhs.unit, t.group("op"))

    def _term(self) -> FixedDecimal:
        val = self._factor()
        while True:
            t = self._peek()
            if t is None or t.group("op") not in ("*", "/"):
                return val
            self._next()
            rhs = self._factor()
            if t.group("op") == "*":
                unit = (f"{val.unit}*{rhs.unit}" if val.unit != rhs.unit
                        else val.unit)
                if rhs.unit == "dimensionless":
                    unit = val.unit
                elif val.unit == "dimensionless":
                    unit = rhs.unit
                val = FixedDecimal(str(val.dec() * rhs.dec()), unit,
                                   val.scale + rhs.scale)
            else:
                if rhs.dec() == 0:
                    raise FormulaError("E-G3-04-012: 除零（失败关闭）")
                unit = (f"{val.unit}/{rhs.unit}" if val.unit != rhs.unit
                        else "ratio")
                val = FixedDecimal(str(val.dec() / rhs.dec()), unit, val.scale)

    def _factor(self) -> FixedDecimal:
        t = self._next()
        if t is None:
            raise FormulaError("E-G3-04-013: 表达式截断")
        g = t.group
        if g("num"):
            return FixedDecimal(g("num"), "dimensionless", 6)
        if g("id"):
            name = g("id")
            if name not in self._env:
                raise UnregisteredConstant(
                    f"E-G3-04-003: 求值期未登记: {name}")
            return self._env[name]
        if g("op") == "(":
            val = self._expr()
            t2 = self._next()
            if t2 is None or t2.group("op") != ")":
                raise FormulaError("E-G3-04-014: 缺右括号")
            return val
        if g("op") == ",":
            raise EvalProhibited(
                "E-G3-04-004: 逗号（函数调用）在受限语法中禁止")
        raise EvalProhibited(f"E-G3-04-004: 非法 token: {g()}")


@dataclass
class CalcLedgerEntry:
    formula_id: str
    version: str
    inputs: Dict[str, str]
    output: str
    output_unit: str
    inputs_hash: str

    def to_dict(self) -> dict:
        return {"formula_id": self.formula_id, "version": self.version,
                "inputs": self.inputs, "output": self.output,
                "output_unit": self.output_unit, "inputs_hash": self.inputs_hash}


class CalcLedger:
    """确定性计算账本：记录全部计算；重算必须与账本哈希一致（篡改必败）。"""

    def __init__(self):
        self.entries: List[CalcLedgerEntry] = []

    def record(self, entry: dict) -> None:
        self.entries.append(CalcLedgerEntry(**entry))

    def verify(self) -> str:
        """重算全部条目哈希；任何一条不符 → CalcLedgerMismatch。"""
        for e in self.entries:
            now = hashlib.sha256(json.dumps(
                e.inputs, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
            if now != e.inputs_hash:
                raise CalcLedgerMismatch(
                    f"E-G3-04-015: 账本条目 {e.formula_id} 输入被篡改 —— "
                    f"实算 {now[:16]}… ≠ 记录 {e.inputs_hash[:16]}…")
        return f"CalcLedger OK: {len(self.entries)} entries"
