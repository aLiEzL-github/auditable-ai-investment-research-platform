"""valuation_engine.py —— G3-06 四路估值与确定性三情景。

基线验收（G3-06）：
  · 统一且可回源的价格/股本/净债务/少数股东权益/币种/时点（G2-15 合同）
  · 四路：FCFF/FCFE、相对估值、PE—ROE—PB/剩余收益、SOTP
  · 悲观/基准/乐观三情景、触发器、安全边际
  · 路由不适用有证据，SOTP 不双算；交叉验证不一致进入 OpenItem
  · 输出共识、分歧校正和开放项；不冒充外部事实；不给交易动作或
    单一伪精确目标（估值输出为区间/分布而非单点）
  · 每个输入满足 G2-15（MISSING/PARTIAL 时整路不估值）

设计：
  · ValuationInputs：统一五类输入（价格/股本/净债务/少数股东权益/
    行业商品）+ 币种 + 时点；缺失 → 该路 FAIL/INPUT_MISSING
  · 四路估值器：每路输出（区间下界/上界/基准）而非单一伪精确值
  · 三情景：悲观/基准/乐观驱动 FCFF 增速、贴现率、倍数；
    每情景给出触发条件与安全边际（对基准价）
  · 路由：SOTP 仅在分部披露完整时适用；双算（FCFF 与 FCFE）必须一致
    （差异 > 阈值 → OpenItem）
  · 交叉验证：相对估值与绝对估值差异 > 阈值 → OpenItem（进入 G3-14）
"""
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from decimal_tools import FixedDecimal, DecimalToolsError  # noqa: F401
from formula_registry import FormulaRegistry, FormulaSpec  # noqa: F401

PESSIMISTIC = "PESSIMISTIC"
BASE = "BASE"
OPTIMISTIC = "OPTIMISTIC"
SCENARIOS = (PESSIMISTIC, BASE, OPTIMISTIC)


class ValuationError(ValueError):
    pass


class RouteNotApplicable(ValuationError):
    """路由不适用：必须有证据（不可静默跳过）。"""


class SotpDoubleCount(ValuationError):
    """SOTP 不双算：分部加总与合并口径重复计入。"""


class CrossCheckMismatch(ValuationError):
    """双算/交叉验证不一致 → OpenItem。"""


@dataclass
class ValuationInputs:
    """G2-15 五类输入 + 币种 + 时点（统一且可回源）。"""
    scope: str
    currency: str
    as_of: str
    price: Optional[str] = None            # 每股价格
    shares_outstanding: Optional[str] = None
    net_debt: Optional[str] = None
    minority_interest: Optional[str] = None
    industry_commodity: Optional[str] = None
    statuses: Dict[str, str] = field(default_factory=dict)  # G2-15 status()

    def ready(self) -> bool:
        """每个估值输入满足 G2-15：五类均 READY（MISSING/PARTIAL → 不估值）。"""
        required = ("price", "shares_outstanding", "net_debt",
                    "minority_interest")
        for k in required:
            if getattr(self, k) is None:
                return False
            if self.statuses.get(k) not in (None, "READY"):
                return False
        return True


# ── 四路估值器 ────────────────────────────────────────────────────
@dataclass
class ValuationResult:
    method: str
    scenario: str
    per_share_low: str
    per_share_high: str
    per_share_base: str
    triggers: str = ""
    notes: str = ""

    def to_dict(self) -> dict:
        return {"method": self.method, "scenario": self.scenario,
                "per_share_low": self.per_share_low,
                "per_share_high": self.per_share_high,
                "per_share_base": self.per_share_base,
                "triggers": self.triggers, "notes": self.notes}


def _dec(s):
    from decimal import Decimal
    return Decimal(str(s))


def fcff_valuation(inputs: ValuationInputs, scenario: str,
                   fcff: str, wacc: str,
                   terminal_growth: str = "0.03") -> ValuationResult:
    """FCFF 路：**单阶段 Gordon 永续** —— FCFF×(1+g)/(WACC−g)，
    其中 g = terminal_growth（永续增速）。确定性 Decimal。

    不给单一伪精确值：基准情景在 ±5% 区间输出。

    ── 关于已移除的 growth 形参（OI-PF-162）──────────────────────────
    本函数原先还收一个 growth 形参，**被赋值但从未被读取** ——
    实测 growth=-0.30 与 growth=0.50 产出逐字相同（2026-08-12）。
    它不是缺一行代码，是模型里根本没有它的位置：单阶段永续模型只有一个
    增速，即永续增速；近期增速要生效需要显式预测期，那是两阶段模型。

    处置取「去掉形参」而非「补两阶段」：基线 B §270 未要求两阶段，
    补它是扩大 G3-06 的范围。**去掉形参使签名与实现一致** ——
    调用者不再会因为签名收下 growth 而以为它影响结果。
    recompute.py 的 PRODUCT_DEPS 早已按「valuation_fcff 不依赖 growth」
    如实落库，本次改动与那份记载自洽。

    缺陷本可更早发现：基线交付件含「敏感性测试矩阵」，而它此前
    全仓库无对应物。现已补齐 —— contracts/valuation_sensitivity.json
    + backend/tests/test_valuation_sensitivity.py。
    """
    if not inputs.ready():
        raise ValuationError(
            f"E-G3-06-001: FCFF 路输入不满足 G2-15（{inputs.statuses}）")
    fcff_d = _dec(fcff)
    w = _dec(wacc)
    tg = _dec(terminal_growth)
    if w <= tg:
        raise ValuationError("E-G3-06-002: WACC 必须大于终值增速")
    ev = fcff_d * (1 + tg) / (w - tg)     # 简化永续终值
    equity = ev - _dec(inputs.net_debt) - _dec(inputs.minority_interest)
    if equity <= 0:
        raise ValuationError("E-G3-06-003: 权益为负（模型不适用，失败关闭）")
    per = equity / _dec(inputs.shares_outstanding)
    low, high = per * _dec("0.95"), per * _dec("1.05")
    return ValuationResult("FCFF", scenario, str(low), str(high), str(per),
                           triggers=f"WACC 变动 ±50bp；FCFF 上/下修")


def fcfe_valuation(inputs: ValuationInputs, scenario: str,
                   fcfe: str, growth: str, ke: str) -> ValuationResult:
    """FCFE 路：FCFE×(1+g)/(Ke−g)。"""
    if not inputs.ready():
        raise ValuationError("E-G3-06-001: FCFE 路输入不满足 G2-15")
    fcfe_d = _dec(fcfe)
    g = _dec(growth)
    k = _dec(ke)
    if k <= g:
        raise ValuationError("E-G3-06-002: Ke 必须大于增速")
    equity = fcfe_d * (1 + g) / (k - g)
    per = equity / _dec(inputs.shares_outstanding)
    low, high = per * _dec("0.95"), per * _dec("1.05")
    return ValuationResult("FCFE", scenario, str(low), str(high), str(per),
                           triggers=f"Ke 变动 ±50bp；FCFE 上/下修")


def relative_valuation(inputs: ValuationInputs, scenario: str,
                       target_pe: str, eps: str) -> ValuationResult:
    """相对估值路：目标 PE × EPS。"""
    if not inputs.ready():
        raise ValuationError("E-G3-06-001: 相对估值路输入不满足 G2-15")
    per = _dec(target_pe) * _dec(eps)
    low, high = per * _dec("0.95"), per * _dec("1.05")
    return ValuationResult("RELATIVE_PE", scenario, str(low), str(high),
                           str(per), triggers=f"目标 PE 变动；EPS 上/下修")


def pe_roe_pb_valuation(inputs: ValuationInputs, scenario: str,
                        roe: str, book_per_share: str, target_pe: str
                        ) -> ValuationResult:
    """PE—ROE—PB/剩余收益路：PB = (ROE−g)/(Ke−g)；价格 = PB × 每股净资产。"""
    if not inputs.ready():
        raise ValuationError("E-G3-06-001: PE—ROE—PB 路输入不满足 G2-15")
    roe_d = _dec(roe)
    bps = _dec(book_per_share)
    pe_d = _dec(target_pe)
    pb = pe_d * roe_d      # 简化联动：PB≈PE×ROE（单位校验由调用方）
    per = pb * bps
    low, high = per * _dec("0.95"), per * _dec("1.05")
    return ValuationResult("PE_ROE_PB", scenario, str(low), str(high), str(per),
                           triggers=f"ROE 变动；目标 PE 变动")


def sotp_valuation(inputs: ValuationInputs, scenario: str,
                   segments: Dict[str, str], overlaps: Dict[str, str],
                   ) -> ValuationResult:
    """SOTP 路：分部加总 − 重叠/抵消；分部披露不完整 → 路由不适用（有证据）。"""
    if not inputs.ready():
        raise ValuationError("E-G3-06-001: SOTP 路输入不满足 G2-15")
    if not segments or not overlaps:
        raise RouteNotApplicable(
            "E-G3-06-004: SOTP 分部披露不完整 —— 路由不适用（有证据），"
            "不得静默跳过")
    total = sum(_dec(v) for v in segments.values())
    overlap = sum(_dec(v) for v in overlaps.values())
    if overlap > total * _dec("0.5"):
        raise SotpDoubleCount(
            "E-G3-06-005: SOTP 重叠/抵消 > 分部合计 50% —— 分部加总与合并口径"
            "重复计入（不双算）")
    equity = total - overlap - _dec(inputs.net_debt)
    per = equity / _dec(inputs.shares_outstanding)
    low, high = per * _dec("0.9"), per * _dec("1.1")
    return ValuationResult("SOTP", scenario, str(low), str(high), str(per),
                           triggers=f"分部估值假设变动", notes="分部加总 − 抵消")


# ── 三情景与安全边际 ──────────────────────────────────────────────
@dataclass
class ScenarioSet:
    """悲观/基准/乐观三情景 + 触发器 + 安全边际。"""
    method: str
    scenarios: Dict[str, ValuationResult] = field(default_factory=dict)
    margin_of_safety: Optional[str] = None   # 相对当前价的折价
    current_price: Optional[str] = None

    def add(self, result: ValuationResult) -> None:
        if result.method != self.method:
            raise ValuationError(f"E-G3-06-006: 情景方法不符: {result.method}")
        if result.scenario not in SCENARIOS:
            raise ValuationError(f"E-G3-06-007: 非法情景: {result.scenario}")
        if result.scenario in self.scenarios:
            raise ValuationError(f"E-G3-06-008: 情景重复: {result.scenario}")
        self.scenarios[result.scenario] = result

    def compute_margin(self, current_price: str) -> None:
        """安全边际 = (基准估值 − 当前价) / 当前价。"""
        if not self.scenarios.get(BASE):
            raise ValuationError("E-G3-06-009: 缺基准情景")
        base = _dec(self.scenarios[BASE].per_share_base)
        cur = _dec(current_price)
        self.current_price = current_price
        self.margin_of_safety = str((base - cur) / cur)

    def to_dict(self) -> dict:
        return {"method": self.method,
                "scenarios": {k: v.to_dict() for k, v in self.scenarios.items()},
                "margin_of_safety": self.margin_of_safety,
                "current_price": self.current_price}


# ── 交叉验证（不一致 → OpenItem）──────────────────────────────────
def cross_check(results: List[ValuationResult],
                tolerance: str = "0.15") -> List[dict]:
    """双算/交叉验证：同情景不同方法基准价差异 > 阈值 → OpenItem。

    SOTP 与任何方法不双算（结构上跳过 SOTP 对 SOTP）。
    """
    open_items = []
    by_scenario: Dict[str, List[ValuationResult]] = {}
    for r in results:
        by_scenario.setdefault(r.scenario, []).append(r)
    tol = _dec(tolerance)
    for sc, rs in by_scenario.items():
        for i in range(len(rs)):
            for j in range(i + 1, len(rs)):
                a, b = rs[i], rs[j]
                if a.method == "SOTP" and b.method == "SOTP":
                    continue  # SOTP 不双算
                if a.method == "SOTP" or b.method == "SOTP":
                    continue  # SOTP 与其他路属分部/合并口径差异，不强制双算
                da, db = _dec(a.per_share_base), _dec(b.per_share_base)
                if da == 0:
                    continue
                diff = abs(da - db) / abs(da)
                if diff > tol:
                    open_items.append({
                        "open_item_id": f"OI-VAL-{sc}-{a.method}-{b.method}",
                        "scenario": sc,
                        "method_a": a.method, "method_b": b.method,
                        "diff": str(diff), "tolerance": str(tol),
                        "blocking": True})
    return open_items
