"""decimal_tools.py —— G3-12 定点十进制、单位守恒、冻结适用分母。

基线验收（G3-12）：
  · 定点十进制（Decimal 字符串，精度保留，跨进程字节一致）
  · 规则级绝对/相对容差、舍入（ROUND_HALF_UP，展示舍入区间）
  · 预运行分母哈希（运行后缩小分母/放宽容差/把缺失改 N/A 必失败并留痕）
  · 单位守恒：不同维度的数值不得相加（重述/错口径/极值/近零 property tests）

设计：
  · FixedDecimal 封装：精度（scale）固定、舍入显式、canonical 序列化一致
  · UnitDim：维度表（货币/比例/倍数/计数），check_compatible() 拒绝跨维加法
  · FrozenDenominator：运行前冻结分母集 + 容差 → 哈希锚定；
    运行中任何修改（缩小分母/放宽容差/缺失改 N/A）→ FrozenViolation
"""
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP, ROUND_DOWN
from typing import Dict, List, Optional


class DecimalToolsError(ValueError):
    pass


class UnitMismatch(DecimalToolsError):
    pass


class FrozenViolation(DecimalToolsError):
    pass


# ── 定点十进制 ─────────────────────────────────────────────────────
@dataclass(frozen=True)
class FixedDecimal:
    """定点十进制：值 + 单位维度 + 展示精度（scale）。

    canonical() 跨进程字节一致（无浮点、无科学计数、定 scale）。
    """
    value: str
    unit: str
    scale: int = 6

    def __post_init__(self):
        try:
            d = Decimal(self.value)
        except InvalidOperation:
            raise DecimalToolsError(f"E-G3-12-001: 非 Decimal 值: {self.value!r}")
        object.__setattr__(self, "value", str(d))

    def dec(self) -> Decimal:
        return Decimal(self.value)

    def rounded(self, scale: int) -> "FixedDecimal":
        d = self.dec().quantize(Decimal(1).scaleb(-scale), rounding=ROUND_HALF_UP)
        return FixedDecimal(str(d), self.unit, scale)

    def canonical(self) -> str:
        """定 scale 输出：整数部分 + '.' + scale 位小数（尾零补齐，字节一致）。

        ip 用 TRUNC（截断）—— 四舍五入只发生在 quantize(scale) 一步，
        不得在取整数部分时再次舍入（否则 1.5 会被 ROUND_HALF_EVEN 变成 2）。
        """
        d = self.dec()
        sign = "-" if d < 0 else ""
        d_abs = abs(d)
        q = d_abs.quantize(Decimal(1).scaleb(-self.scale), rounding=ROUND_HALF_UP)
        ip = str(q.to_integral_value(rounding=ROUND_DOWN))
        s = format(q, "f")
        if "." in s:
            _, fp = s.split(".", 1)
            fp = (fp + "0" * self.scale)[:self.scale]
        else:
            fp = "0" * self.scale
        return f"{sign}{ip}.{fp}"


def add(a: FixedDecimal, b: FixedDecimal) -> FixedDecimal:
    UnitDim.check_compatible(a.unit, b.unit, "+")
    d = a.dec() + b.dec()
    return FixedDecimal(str(d), a.unit, max(a.scale, b.scale))


def sub(a: FixedDecimal, b: FixedDecimal) -> FixedDecimal:
    UnitDim.check_compatible(a.unit, b.unit, "-")
    d = a.dec() - b.dec()
    return FixedDecimal(str(d), a.unit, max(a.scale, b.scale))


def mul(a: FixedDecimal, b: FixedDecimal) -> FixedDecimal:
    """乘法允许：单位可异（数量×单价=金额语义由调用方声明），只做量纲组合。"""
    unit = f"{a.unit}*{b.unit}" if a.unit != b.unit else a.unit
    d = a.dec() * b.dec()
    return FixedDecimal(str(d), unit, a.scale + b.scale)


def div(a: FixedDecimal, b: FixedDecimal) -> FixedDecimal:
    if b.dec() == 0:
        raise DecimalToolsError("E-G3-12-002: 除零（fail-closed）")
    unit = f"{a.unit}/{b.unit}" if a.unit != b.unit else "ratio"
    d = a.dec() / b.dec()
    return FixedDecimal(str(d), unit, a.scale)


# ── 单位维度（守恒检查）───────────────────────────────────────────
DIM_TABLE = {
    # 货币：同币种同维
    "CNY_million": "money", "CNY": "money", "USD_million": "money",
    "percent": "ratio", "%": "ratio", "ratio": "ratio",
    "shares": "count", "times": "ratio", "days": "time",
    "": "unknown",
}


class UnitDim:
    """维度判定：加/减要求同维；空单位视为 unknown（禁止参与加减）。"""

    @staticmethod
    def dim(unit: str) -> str:
        return DIM_TABLE.get(unit or "", "custom")

    @staticmethod
    def check_compatible(a: str, b: str, op: str) -> None:
        if a == b:
            return
        da, db = UnitDim.dim(a), UnitDim.dim(b)
        if da == "unknown" or db == "unknown":
            raise UnitMismatch(
                f"E-G3-12-003: {op} 操作单位缺失（unknown）: {a!r} vs {b!r}")
        if da != db:
            raise UnitMismatch(
                f"E-G3-12-004: 单位维度不守恒: {a!r}({da}) vs {b!r}({db}) —— "
                f"跨维加减必失败（wrong-basis）")


# ── 冻结适用分母（预运行分母哈希）─────────────────────────────────
@dataclass
class FrozenDenominator:
    """运行前冻结分母集与容差；运行中任何修改必失败并留痕。

    freeze() 后：
      · 缩小分母 / 放宽容差 / 把缺失改 N/A → FrozenViolation（E-G3-12-005）
      · verify() 用原分母集哈希重算比对（篡改必败）
    """
    denominators: Dict[str, str] = field(default_factory=dict)  # 分母名->Decimal 字符串
    tolerances: Dict[str, str] = field(default_factory=dict)    # 规则->相对容差
    _frozen_hash: Optional[str] = None

    def freeze(self) -> str:
        import hashlib
        import json
        blob = {"denominators": dict(sorted(self.denominators.items())),
                "tolerances": dict(sorted(self.tolerances.items()))}
        self._frozen_hash = hashlib.sha256(
            json.dumps(blob, ensure_ascii=False, sort_keys=True,
                       separators=(",", ":")).encode()).hexdigest()
        return self._frozen_hash

    def verify(self) -> str:
        """篡改必败：重算比对。任何字节改动 → FrozenViolation。"""
        if self._frozen_hash is None:
            raise FrozenViolation("E-G3-12-005: 分母未冻结 —— 运行前必须冻结")
        import hashlib
        import json
        blob = {"denominators": dict(sorted(self.denominators.items())),
                "tolerances": dict(sorted(self.tolerances.items()))}
        now = hashlib.sha256(
            json.dumps(blob, ensure_ascii=False, sort_keys=True,
                       separators=(",", ":")).encode()).hexdigest()
        if now != self._frozen_hash:
            raise FrozenViolation(
                f"E-G3-12-005: 冻结分母被篡改 —— 实算 {now[:16]}… "
                f"≠ 冻结 {self._frozen_hash[:16]}…（运行后缩小分母/放宽容差/"
                f"缺失改 N/A 必失败）")
        return self._frozen_hash

    def shrink(self, name: str, value: str) -> None:
        """运行中缩小分母 → 立即失败（在 verify 之外也阻断写入）。"""
        if self._frozen_hash is not None:
            raise FrozenViolation(
                f"E-G3-12-005: 冻结后缩小分母 {name} → {value} 必失败")
        self.denominators[name] = value

    def loosen(self, rule: str, tolerance: str) -> None:
        """运行中放宽容差 → 立即失败。"""
        if self._frozen_hash is not None:
            raise FrozenViolation(
                f"E-G3-12-005: 冻结后放宽容差 {rule} → {tolerance} 必失败")
        self.tolerances[rule] = tolerance
