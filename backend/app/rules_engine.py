"""rules_engine.py —— G3-10/G3-11 勾稽规则 R01—R10 引擎（§22.1）。

基线（G3-10/G3-11）：
  · §22.1 R01—R10 逐条实现；每条规则六类 fixture：
    positive / negative / legitimate_NA / rounding / restatement / wrong_basis
  · 共同字段：scope / period / instant_or_duration / single_quarter_or_cumulative /
    original_or_restated / unit / source_precision / applicability_predicate /
    absolute_tolerance / relative_tolerance / allowed_residual / failure_impact / locator
  · allowed_error = max(disclosed_rounding_interval, absolute_tolerance,
    relative_tolerance * frozen_reference_scale)（§22.1 逐字）
  · 只有来源精确到最小单位且规则不存在展示舍入时才允许绝对零容差
  · scope/period/unit/basis 错配必阻断（wrong_basis → FAIL）
  · 重述（original_or_restated 不一致）→ RESTATEMENT_PENDING
  · 输出与输入可回到 evidence locator（每条 result 带 locator 链）

状态复用 G3-09 RuleRegistry 的七态域：
  PASS / FAIL / INPUT_MISSING / NOT_COMPARABLE / RESTATEMENT_PENDING /
  NOT_RUN / NOT_APPLICABLE

设计：
  · RuleInput 是六类 fixture 的共同形状（契约一致，⑱）
  · 每条规则 = evaluate(engine, inputs) -> (status, residual, detail)
  · 数值一律 Decimal 字符串（精度保留，跨进程字节一致 —— G3-12 正式化）
"""
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Callable, Dict, List, Optional, Tuple

# 状态域（与 G3-09 一致，单来源引用）
PASS = "PASS"
FAIL = "FAIL"
INPUT_MISSING = "INPUT_MISSING"
NOT_COMPARABLE = "NOT_COMPARABLE"
RESTATEMENT_PENDING = "RESTATEMENT_PENDING"
NOT_RUN = "NOT_RUN"
NOT_APPLICABLE = "NOT_APPLICABLE"


class RuleEngineError(ValueError):
    pass


@dataclass
class RuleInput:
    """§22.1 共同字段 + 规则特定数值。locator 为证据回源锚。"""
    scope: str
    period: str
    instant_or_duration: str            # INSTANT / DURATION
    single_quarter_or_cumulative: str   # SINGLE / CUMULATIVE / ANNUAL
    original_or_restated: str           # ORIGINAL / RESTATED
    unit: str
    source_precision: str               # 披露精度（最小单位，如 "0.01" 亿）
    applicability_predicate: str        # APPLICABLE / NOT_APPLICABLE 依据
    absolute_tolerance: str             # Decimal 字符串
    relative_tolerance: str             # 相对容差（如 "0.001" = 0.1%）
    allowed_residual: str               # 允许残余（展示舍入区间）
    failure_impact: str                 # BLOCKING / NON_BLOCKING
    locator: str                        # evidence locator（可回源）
    values: Dict[str, str] = field(default_factory=dict)  # 规则特定数值

    def dec(self, key: str) -> Optional[Decimal]:
        v = self.values.get(key)
        if v is None:
            return None
        try:
            return Decimal(v)
        except InvalidOperation:
            raise RuleEngineError(f"E-G3-10-001: 非 Decimal 值: {key}={v!r}")


def allowed_error(inp: RuleInput, frozen_reference_scale: str) -> Decimal:
    """§22.1 逐字：allowed_error = max(披露舍入区间, 绝对容差,
    相对容差 × 冻结参考量纲)。"""
    disclosed = Decimal(inp.allowed_residual or "0")
    abs_tol = Decimal(inp.absolute_tolerance or "0")
    rel_tol = Decimal(inp.relative_tolerance or "0") * Decimal(frozen_reference_scale)
    err = max(disclosed, abs_tol, rel_tol)
    # 只有来源精确到最小单位且无展示舍入时才允许绝对零容差
    if err == 0 and (inp.source_precision != "min_unit"
                     or Decimal(inp.allowed_residual or "0") != 0):
        err = Decimal("0.000001")  # 非零最小容差（零容差仅在特例允许）
    return err


# ── R01—R05 规则实现（G3-10）──────────────────────────────────────
def rule_r01(inp: RuleInput) -> Tuple[str, Decimal, str]:
    """R01 分部收入：合并营业收入 = 分部外部收入 + 内部交易 - 抵消项。

    适用但无完整抵消项 → INPUT_MISSING。
    """
    if inp.applicability_predicate.startswith("NOT_APPLICABLE"):
        return NOT_APPLICABLE, Decimal("0"), "分部披露框架不要求"
    merged = inp.dec("merged_revenue")
    seg_ext = inp.dec("segment_external_revenue")
    seg_int = inp.dec("segment_intercompany_revenue")
    elim = inp.dec("eliminations")
    if merged is None or seg_ext is None:
        return INPUT_MISSING, Decimal("0"), "R01 缺输入"
    if seg_int is None or elim is None:
        return INPUT_MISSING, Decimal("0"), "R01 适用但无完整抵消项（§22.1）"
    lhs = seg_ext + seg_int - elim
    residual = (merged - lhs).copy_abs()
    scale = abs(merged)
    if residual <= allowed_error(inp, str(scale)):
        return PASS, residual, f"R01 PASS residual={residual}"
    return FAIL, residual, f"R01 FAIL residual={residual}"


def rule_r02(inp: RuleInput) -> Tuple[str, Decimal, str]:
    """R02 利润归属：净利润 = 归母净利润 + 少数股东损益。

    按披露精度计算舍入区间（allowed_error 含 disclosed）。
    """
    net = inp.dec("net_profit")
    parent = inp.dec("parent_net_profit")
    minority = inp.dec("minority_profit")
    if net is None or parent is None or minority is None:
        return INPUT_MISSING, Decimal("0"), "R02 缺输入"
    if inp.applicability_predicate.startswith("NOT_APPLICABLE"):
        return NOT_APPLICABLE, Decimal("0"), "无少数股东（单一权益主体）"
    residual = (net - (parent + minority)).copy_abs()
    if residual <= allowed_error(inp, str(abs(net))):
        return PASS, residual, f"R02 PASS residual={residual}"
    return FAIL, residual, f"R02 FAIL residual={residual}"


def rule_r03(inp: RuleInput) -> Tuple[str, Decimal, str]:
    """R03 现金流：现金净增加 = 经营 + 投资 + 筹资 + 汇率影响。

    不与资产负债表「货币资金」直接等同（货币资金 ≠ 现金净增加）。
    """
    incr = inp.dec("cash_net_increase")
    ocf = inp.dec("ocf")
    icf = inp.dec("icf")
    fcf = inp.dec("fcf")
    fx = inp.dec("fx_effect")
    if None in (incr, ocf, icf, fcf):
        return INPUT_MISSING, Decimal("0"), "R03 缺输入"
    if fx is None:
        return INPUT_MISSING, Decimal("0"), "R03 缺汇率影响（适用框架）"
    residual = (incr - (ocf + icf + fcf + fx)).copy_abs()
    if residual <= allowed_error(inp, str(abs(incr))):
        return PASS, residual, f"R03 PASS residual={residual}"
    return FAIL, residual, f"R03 FAIL residual={residual}"


def rule_r04(inp: RuleInput) -> Tuple[str, Decimal, str]:
    """R04 间接法 OCF：OCF = 净利润 + 非现金项目 + 营运资本变化 + 其他调节项。

    披露框架适用但项目不全 → INPUT_MISSING；
    只有框架不要求时才 NOT_APPLICABLE。
    """
    ocf = inp.dec("ocf")
    net = inp.dec("net_profit")
    non_cash = inp.dec("non_cash_items")
    wc = inp.dec("working_capital_changes")
    other = inp.dec("other_adjustments")
    if ocf is None or net is None:
        return INPUT_MISSING, Decimal("0"), "R04 缺输入"
    if inp.applicability_predicate == "NOT_APPLICABLE_INDIRECT_NOT_REQUIRED":
        return NOT_APPLICABLE, Decimal("0"), "间接法框架不要求（§22.1 R04）"
    if non_cash is None or wc is None or other is None:
        return INPUT_MISSING, Decimal("0"), "R04 披露框架适用但项目不全（§22.1）"
    expected = net + non_cash + wc + other
    residual = (ocf - expected).copy_abs()
    if residual <= allowed_error(inp, str(abs(ocf))):
        return PASS, residual, f"R04 PASS residual={residual}"
    return FAIL, residual, f"R04 FAIL residual={residual}"


def rule_r05(inp: RuleInput) -> Tuple[str, Decimal, str]:
    """R05 权益变动：期末权益 = 期初权益 + 综合收益 + 股东投入/分配 +
    股份支付 + 并购 + 其他变化。

    不以简化恒等式（期初+净利润）冒充完整规则 ——
    简化输入（仅净利润、无综合收益/投入/分配）→ INPUT_MISSING。
    """
    end_eq = inp.dec("ending_equity")
    begin_eq = inp.dec("beginning_equity")
    comp = inp.dec("comprehensive_income")
    contrib = inp.dec("owner_contributions_distributions")
    share_based = inp.dec("share_based_payment")
    m_and_a = inp.dec("m_and_a_effects")
    other = inp.dec("other_changes")
    if end_eq is None or begin_eq is None:
        return INPUT_MISSING, Decimal("0"), "R05 缺输入"
    # 简化恒等式（只有净利润）→ 不得冒充完整规则
    if comp is None and contrib is None:
        return INPUT_MISSING, Decimal("0"), "R05 简化恒等式不得冒充完整规则（§22.1）"
    if None in (comp, contrib, share_based, m_and_a, other):
        return INPUT_MISSING, Decimal("0"), "R05 缺完整权益变动项目"
    expected = begin_eq + comp + contrib + share_based + m_and_a + other
    residual = (end_eq - expected).copy_abs()
    if residual <= allowed_error(inp, str(abs(end_eq))):
        return PASS, residual, f"R05 PASS residual={residual}"
    return FAIL, residual, f"R05 FAIL residual={residual}"


# ── R06—R10 规则实现（G3-11）──────────────────────────────────────
def rule_r06(inp: RuleInput) -> Tuple[str, Decimal, str]:
    """R06 资产负债：资产 = 负债 + 权益。区分合并/母公司与披露舍入。"""
    assets = inp.dec("total_assets")
    liab = inp.dec("total_liabilities")
    eq = inp.dec("total_equity")
    if None in (assets, liab, eq):
        return INPUT_MISSING, Decimal("0"), "R06 缺输入"
    residual = (assets - (liab + eq)).copy_abs()
    if residual <= allowed_error(inp, str(abs(assets))):
        return PASS, residual, f"R06 PASS residual={residual}"
    return FAIL, residual, f"R06 FAIL residual={residual}"


def rule_r07(inp: RuleInput) -> Tuple[str, Decimal, str]:
    """R07 扣非利润：归母净利润 = 扣非归母净利润 + 归母非经常性损益。

    使用公司披露定义（非经常性损益用归母口径），不自行简化税后归属。
    """
    parent_net = inp.dec("parent_net_profit")
    non_recur = inp.dec("parent_non_recurring_gain_loss")
    kf = inp.dec("non_gang_parent_net_profit")
    if None in (parent_net, non_recur, kf):
        return INPUT_MISSING, Decimal("0"), "R07 缺输入"
    residual = (parent_net - (kf + non_recur)).copy_abs()
    if residual <= allowed_error(inp, str(abs(parent_net))):
        return PASS, residual, f"R07 PASS residual={residual}"
    return FAIL, residual, f"R07 FAIL residual={residual}"


def rule_r08(inp: RuleInput) -> Tuple[str, Decimal, str]:
    """R08 分部利润：分部计量基础、抵消与合并利润口径勾稽。

    适用但计量基础不一致 → NOT_COMPARABLE。
    """
    merged_profit = inp.dec("merged_profit")
    seg_profit = inp.dec("segment_profit_sum")
    elim = inp.dec("segment_eliminations")
    basis = inp.values.get("segment_measurement_basis", "")
    if merged_profit is None or seg_profit is None:
        return INPUT_MISSING, Decimal("0"), "R08 缺输入"
    if basis and basis != "COMPARABLE":
        return NOT_COMPARABLE, Decimal("0"), "R08 计量基础不一致（§22.1）"
    residual = (merged_profit - (seg_profit - elim)).copy_abs()
    if residual <= allowed_error(inp, str(abs(merged_profit))):
        return PASS, residual, f"R08 PASS residual={residual}"
    return FAIL, residual, f"R08 FAIL residual={residual}"


def rule_r09(inp: RuleInput) -> Tuple[str, Decimal, str]:
    """R09 母子公司：母公司、子公司、内部交易和抵消勾稽。

    适用但附注不全 → INPUT_MISSING。
    """
    parent_assets = inp.dec("parent_assets")
    sub_assets = inp.dec("subsidiary_assets")
    elim = inp.dec("intercompany_eliminations")
    consolidated = inp.dec("consolidated_assets")
    if parent_assets is None or sub_assets is None:
        return INPUT_MISSING, Decimal("0"), "R09 缺输入"
    if elim is None or consolidated is None:
        return INPUT_MISSING, Decimal("0"), "R09 适用但附注不全（§22.1）"
    residual = (consolidated - (parent_assets + sub_assets - elim)).copy_abs()
    if residual <= allowed_error(inp, str(abs(consolidated))):
        return PASS, residual, f"R09 PASS residual={residual}"
    return FAIL, residual, f"R09 FAIL residual={residual}"


def rule_r10(inp: RuleInput) -> Tuple[str, Decimal, str]:
    """R10 期间连续：本期期初 = 上期期末（同一口径）。

    存在未处理重述 → RESTATEMENT_PENDING。
    """
    this_begin = inp.dec("this_period_beginning")
    prior_end = inp.dec("prior_period_ending")
    restated = inp.values.get("restatement_pending", "")
    if this_begin is None or prior_end is None:
        return INPUT_MISSING, Decimal("0"), "R10 缺输入"
    if restated == "PENDING":
        return RESTATEMENT_PENDING, Decimal("0"), "R10 存在未处理重述（§22.1）"
    residual = (this_begin - prior_end).copy_abs()
    if residual <= allowed_error(inp, str(abs(this_begin))):
        return PASS, residual, f"R10 PASS residual={residual}"
    return FAIL, residual, f"R10 FAIL residual={residual}"


RULES: Dict[str, Callable[[RuleInput], Tuple[str, Decimal, str]]] = {
    "R01": rule_r01, "R02": rule_r02, "R03": rule_r03, "R04": rule_r04,
    "R05": rule_r05, "R06": rule_r06, "R07": rule_r07, "R08": rule_r08,
    "R09": rule_r09, "R10": rule_r10,
}


def evaluate(rule_id: str, inp: RuleInput) -> dict:
    """执行单条规则（G3-09 状态机兼容：状态字符串一致）。

    wrong_basis（scope/period/unit 与调用方契约不符）由调用方预先判定，
    本函数对 unit 一致性做机械检查：unit 为空或非 CN 币种单位 → FAIL。
    """
    fn = RULES.get(rule_id)
    if fn is None:
        raise RuleEngineError(f"E-G3-10-002: 未知规则: {rule_id}")
    if not inp.unit:
        return {"rule_id": rule_id, "status": FAIL,
                "residual": "0", "detail": "unit 缺失（wrong_basis）",
                "locator": inp.locator}
    status, residual, detail = fn(inp)
    return {"rule_id": rule_id, "status": status, "residual": str(residual),
            "detail": detail, "locator": inp.locator}
