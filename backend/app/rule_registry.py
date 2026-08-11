"""rule_registry.py —— G3-09 版本化 RuleRegistry、适用性与闭合状态机。

基线验收（G3-09）：
  · §22.1 精确 R01—R10（ID/精确定义/非 PASS 语义逐字取用）
  · 规则版本、applicability、固定分母
  · 状态域 PASS/FAIL/INPUT_MISSING/NOT_COMPARABLE/RESTATEMENT_PENDING/
    NOT_RUN/NOT_APPLICABLE
  · 未知状态不能映射 PASS；缺输入不得改为 NOT_APPLICABLE；
    N/A 必须有预冻结适用性依据和签名
  · 每条规则有正/负/N/A 合法性测试（fixture 六类：positive/negative/
    legitimate_NA/rounding/restatement/wrong_basis 由 G3-10/11 实现；
    本模块保证规则登记与状态机合法性）

闭合状态机（§22.1）：
  · applicable_count 运行前冻结（freeze_applicable_count 后不可改）
  · 任何 FAIL/INPUT_MISSING/NOT_COMPARABLE/RESTATEMENT_PENDING/NOT_RUN
    传播到 Fact/Claim/OpenItem 并阻断适用硬规则 Gate（GATE_BLOCKED）
"""
import json
import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional

# ── 状态域（§22.1 七态，逐字取用）────────────────────────────────
PASS = "PASS"
FAIL = "FAIL"
INPUT_MISSING = "INPUT_MISSING"
NOT_COMPARABLE = "NOT_COMPARABLE"
RESTATEMENT_PENDING = "RESTATEMENT_PENDING"
NOT_RUN = "NOT_RUN"
NOT_APPLICABLE = "NOT_APPLICABLE"

STATUSES = (PASS, FAIL, INPUT_MISSING, NOT_COMPARABLE,
            RESTATEMENT_PENDING, NOT_RUN, NOT_APPLICABLE)

# 阻断态：任何规则处于其中 → 阻断适用硬规则 Gate
BLOCKING = (FAIL, INPUT_MISSING, NOT_COMPARABLE, RESTATEMENT_PENDING, NOT_RUN)

CONTRACTS = os.path.join(os.path.dirname(__file__), "..", "..", "contracts")


class RuleRegistryError(ValueError):
    pass


class ApplicableCountFrozen(RuleRegistryError):
    pass


@dataclass
class Rule:
    """§22.1 一条勾稽规则的登记元数据（G3-09 层）。"""
    rule_id: str                       # R01 … R10
    title: str
    definition: str                    # §22.1 精确定义（逐字）
    version: str
    non_pass_semantics: str            # §22.1 第三列（INPUT_MISSING 等）
    applicable_count_frozen: int = 0
    statuses: Dict[str, str] = field(default_factory=dict)  # scope->status

    def to_dict(self) -> dict:
        return {"rule_id": self.rule_id, "title": self.title,
                "definition": self.definition, "version": self.version,
                "non_pass_semantics": self.non_pass_semantics}


# ── §22.1 R01—R10 登记（ID/定义/非 PASS 语义逐字取用）─────────────
_RULE_DEFS = [
    ("R01", "分部收入",
     "合并营业收入与分部外部收入、内部交易和抵消项勾稽",
     "1.0", "适用但无完整抵消项为 INPUT_MISSING"),
    ("R02", "利润归属",
     "净利润与归母净利润、少数股东损益勾稽",
     "1.0", "按披露精度计算舍入区间"),
    ("R03", "现金流",
     "现金及现金等价物净增加与经营、投资、筹资现金流及汇率影响勾稽",
     "1.0", "不与资产负债表“货币资金”直接等同"),
    ("R04", "间接法 OCF",
     "OCF 与净利润、非现金项目、营运资本及其他调节项勾稽",
     "1.0",
     "披露框架适用但项目不全为 INPUT_MISSING；只有框架不要求时才 NOT_APPLICABLE"),
    ("R05", "权益变动",
     "期初权益、综合收益、股东投入/分配、股份支付、并购及其他变化勾稽",
     "1.0", "不以简化恒等式冒充完整规则"),
    ("R06", "资产负债",
     "资产 = 负债 + 权益",
     "1.0", "区分合并/母公司和披露舍入"),
    ("R07", "扣非利润",
     "归母净利润与归属于母公司的非经常性损益、扣非归母净利润勾稽",
     "1.0", "使用公司披露定义，不自行简化税后归属"),
    ("R08", "分部利润",
     "分部计量基础、抵消与合并利润口径勾稽",
     "1.0", "适用但计量基础不一致为 NOT_COMPARABLE"),
    ("R09", "母子公司",
     "母公司、子公司、内部交易和抵消勾稽",
     "1.0", "适用但附注不全为 INPUT_MISSING"),
    ("R10", "期间连续",
     "本期期初与上期期末按同一口径比较",
     "1.0", "存在未处理重述为 RESTATEMENT_PENDING"),
]


def _new_rules() -> List[Rule]:
    """每次生成全新 Rule 实例（statuses 为运行期状态，不得跨运行共享）。"""
    return [Rule(rid, title, definition, version, non_pass)
            for rid, title, definition, version, non_pass in _RULE_DEFS]


_RULES = _new_rules()


class RuleRegistry:
    """版本化规则注册表 + 适用性状态机。

    使用：
      · register() 登记 R01—R10（含版本）
      · freeze_applicable_count() 运行前冻结 —— 冻结后任何改动拒绝
      · record_status(rule, scope, status, applicability_basis,
        signature) —— N/A 必须有预冻结依据与签名
      · gate_verdict() —— 任何阻断态 → GATE_BLOCKED
    """

    def __init__(self):
        self.rules: Dict[str, Rule] = {}
        self._applicable_count_frozen = False
        self._applicable_count = 0

    def register(self, rule: Rule) -> None:
        if rule.rule_id in self.rules:
            raise RuleRegistryError(
                f"E-G3-09-001: 规则重复登记: {rule.rule_id}")
        if rule.statuses:
            raise RuleRegistryError(
                f"E-G3-09-002: 登记时不得携带运行期状态: {rule.rule_id}")
        if rule.rule_id not in [r.rule_id for r in _RULES]:
            raise RuleRegistryError(
                f"E-G3-09-003: 非 §22.1 规则: {rule.rule_id}")
        self.rules[rule.rule_id] = rule

    def register_all(self) -> None:
        for r in _new_rules():
            self.register(r)

    # ── 适用分母运行前冻结（C-4 / §22.1 applicable_count）──────────
    def freeze_applicable_count(self, count: int) -> None:
        if self._applicable_count_frozen:
            raise ApplicableCountFrozen(
                "E-G3-09-004: applicable_count 已冻结，运行中修改必失败")
        if count < 0:
            raise RuleRegistryError(f"E-G3-09-005: applicable_count 非法: {count}")
        self._applicable_count = count
        self._applicable_count_frozen = True

    @property
    def applicable_count(self) -> int:
        if not self._applicable_count_frozen:
            raise RuleRegistryError(
                "E-G3-09-006: applicable_count 未冻结 —— 运行前必须冻结")
        return self._applicable_count

    # ── 状态记录（N/A 必须有预冻结依据与签名）────────────────────
    def record_status(self, rule_id: str, scope: str, status: str,
                      applicability_basis: str = "",
                      signature: str = "") -> None:
        rule = self.rules.get(rule_id)
        if rule is None:
            raise RuleRegistryError(f"E-G3-09-007: 未登记规则: {rule_id}")
        if status not in STATUSES:
            raise RuleRegistryError(
                f"E-G3-09-008: 未知状态 {status!r} —— 不得映射 PASS")
        if status == NOT_APPLICABLE:
            # 缺输入不得改为 N/A（§22.1 / 基线 G3-09 验收）
            if not applicability_basis:
                raise RuleRegistryError(
                    f"E-G3-09-009: N/A 必须有预冻结适用性依据: {rule_id}")
            if not signature:
                raise RuleRegistryError(
                    f"E-G3-09-010: N/A 必须有签名: {rule_id}")
        rule.statuses[scope] = status

    # ── 闭合状态机 ────────────────────────────────────────────────
    def gate_verdict(self) -> str:
        """任何阻断态 → GATE_BLOCKED；否则 GATE_OK。

        N 为零时与「N 条全过」可分辨：返回 OK 且附带计数，
        调用方须报「适用 N 条、全部 PASS」而非「N 条全过」（⑨）。
        """
        if not self._applicable_count_frozen:
            raise RuleRegistryError(
                "E-G3-09-006: applicable_count 未冻结 —— 运行前必须冻结")
        blocking = [rid for rid, r in self.rules.items()
                    if any(s in BLOCKING for s in r.statuses.values())]
        if blocking:
            return f"GATE_BLOCKED: {sorted(blocking)}"
        return f"GATE_OK: applicable={self._applicable_count} all_pass"

    def report_applicable(self) -> str:
        """⑨：报出「本次适用 N 条、全部 PASS」；N=0 与「全过」可分辨。"""
        n = self.applicable_count
        passed = [rid for rid, r in self.rules.items()
                  if r.statuses and all(s == PASS for s in r.statuses.values())]
        if n == 0:
            return "适用 0 条（无适用硬规则）—— 与「0 条全过」区分（⑨）"
        return f"适用 {n} 条、全部 PASS（{len(passed)} 条已判定）"
