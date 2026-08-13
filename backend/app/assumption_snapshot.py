"""assumption_snapshot.py —— G3-13 AssumptionProposal、ApprovalEvent 与
不可变 AssumptionSnapshot。

基线验收（G3-13）：
  · 写权、payload hash、审批人、时点、拒绝记录和不可变快照
  · LLM 无批准写权（writers.json: assumption.never 含 LLM；批准仅 L12 端点）
  · 拒绝项不进入计算
  · 批准 payload 任一字节变化即失效（approval 绑定 payload_hash，重算不符 → INVALIDATED）

设计：
  · AssumptionProposal：LLM/Agent 可提议（写 assumption 对象，LLM 在 never
    名单外？—— 不：writers.json 中 assumption.writers=[L8,L9]，never=[LLM]，
    即 LLM 不能写 proposal 的持久化对象；本模块提供 proposal 构建 +
    状态流转 PENDING → APPROVED/REJECTED）
  · ApprovalEvent：审批人、时点、payload_hash、token（显式 APPROVE）、
    拒绝记录（REJECTED 时带 reason）
  · AssumptionSnapshot：不可变 —— 仅含已批准项；payload_hash 绑定每个
    批准项；approval 的 payload_hash 重算不符 → 快照失效（INVALIDATED）
"""
import hashlib
import json
from dataclasses import dataclass, field
from typing import Dict, List, Optional

PENDING = "PENDING"
APPROVED = "APPROVED"
REJECTED = "REJECTED"


class AssumptionError(ValueError):
    pass


class NoApprovalWrite(AssumptionError):
    """LLM/自动化无批准写权（结构上不可达）。"""


class PayloadChanged(AssumptionError):
    """批准 payload 任一字节变化即失效。"""


def payload_hash(payload: dict) -> str:
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False,
                                     sort_keys=True).encode("utf-8")).hexdigest()


@dataclass
class AssumptionProposal:
    proposal_id: str
    payload: dict                      # 假设内容（进入计算前须批准）
    proposed_by: str                   # L8/L9（研究层）
    status: str = PENDING
    decided_at: Optional[str] = None
    approved_by: Optional[str] = None  # 审批人（人工）
    rejection_reason: Optional[str] = None
    payload_sha256: str = ""

    def __post_init__(self):
        self.payload_sha256 = payload_hash(self.payload)

    def to_dict(self) -> dict:
        return {"proposal_id": self.proposal_id, "payload": self.payload,
                "proposed_by": self.proposed_by, "status": self.status,
                "decided_at": self.decided_at, "approved_by": self.approved_by,
                "rejection_reason": self.rejection_reason,
                "payload_sha256": self.payload_sha256}


@dataclass
class ApprovalEvent:
    """人工批准/拒绝事件（L12 端点语义）。"""
    proposal_id: str
    decision: str                      # APPROVED / REJECTED
    approver: str                      # 自然人标识
    decided_at: str                    # ISO UTC
    token: str                         # 显式 APPROVE（聊天“继续”不算）
    payload_sha256: str                # 批准锚定的 payload 哈希
    rejection_reason: Optional[str] = None

    def to_dict(self) -> dict:
        return {"proposal_id": self.proposal_id, "decision": self.decision,
                "approver": self.approver, "decided_at": self.decided_at,
                "token": self.token, "payload_sha256": self.payload_sha256,
                "rejection_reason": self.rejection_reason}


# ── 批准者白名单（OI-PF-176：默认拒绝，非黑名单）────────────────────
# 身份形态依 VD-18 ②：化名/代号 + GitHub 账号，**不用真实姓名**
# （VD-05 = 公开仓库，真名会进入公开 task-record 且入 Git 历史不可撤回）。
# 新增批准者须显式加入本清单 —— 加入即等同于加一条豁免，按规则 ㉚ 与守卫同等对待。
APPROVER_ALLOWLIST = ("U",)


def _norm_approver(s: str) -> str:
    """归一后比对：去首尾空白 + 折叠大小写。

    原黑名单是字面比对，故 'llm' 与 'LLM ' 都能绕过。
    """
    return str(s or "").strip().casefold()


_NORM_ALLOWLIST = frozenset(_norm_approver(x) for x in APPROVER_ALLOWLIST)


class AssumptionRegistry:
    """proposal 登记与人工裁决。LLM 无批准写权：
    approve()/reject() 的调用方必须声明身份且非 LLM/自动化。"""

    def __init__(self):
        self.proposals: Dict[str, AssumptionProposal] = {}
        self.events: List[ApprovalEvent] = []

    def propose(self, proposal: AssumptionProposal) -> AssumptionProposal:
        if proposal.proposal_id in self.proposals:
            raise AssumptionError(f"E-G3-13-001: 重复提案: {proposal.proposal_id}")
        if proposal.status != PENDING:
            raise AssumptionError(f"E-G3-13-002: 新提案必须 PENDING")
        self.proposals[proposal.proposal_id] = proposal
        return proposal

    def _assert_approver(self, approver: str) -> None:
        """批准写权：**默认拒绝** —— 不在白名单内的一律无批准权。

        原实现是穷举黑名单 `if approver in ("LLM","AUTOMATION","L8","L9","L10")`。
        实测 2026-08-13（OI-PF-176）：十种身份七种批准成功 ——
        **'llm'（小写）· 'LLM '（尾随空格）· 'AGENT' · 'Codex' · 'system'
        · 'L11' · 'GPT' 全部通过**。其中 'Codex' 尤其致命：VD-02 明写
        「Codex 或任何 AI 辅助**不计入**自然人数（A §6.1）」。

        **这是同一形状的第三例** —— CLIENT_SUPPLIED_VERDICT_KEYS（OI-PF-161）、
        SERVER_ALLOWLIST 死豁免、本处。OI-PF-161 的结论早已写下：
        **穷举清单不可证完备，能做默认拒绝就不要做清单。**

        与前两例相比本处更重：那两处被绕过不改变结论（判定由唯一计算点产出）；
        **本处一旦绕过，即一个未经人工批准的假设进入计算** —— 而 G6A-05 的
        整个确定性回算链条建立在「每条 AssumptionProposal 有独立人工批准」之上。

        判据：归一（去空白 + 折叠大小写）后须落在 APPROVER_ALLOWLIST 内。
        白名单为空时**不得默认放行**（fail-closed）。
        """
        if not APPROVER_ALLOWLIST:
            raise NoApprovalWrite(
                "E-G3-13-003: 批准者白名单为空 —— **不得默认放行**（fail-closed）")
        if _norm_approver(approver) not in _NORM_ALLOWLIST:
            raise NoApprovalWrite(
                f"E-G3-13-003: {approver!r} 不在批准者白名单内，无批准写权"
                f"（默认拒绝；G0-04 §2 / VD-02：AI 辅助不计入自然人数）")

    def decide(self, proposal_id: str, decision: str, approver: str,
               decided_at: str, token: str,
               rejection_reason: Optional[str] = None) -> ApprovalEvent:
        self._assert_approver(approver)
        p = self.proposals.get(proposal_id)
        if p is None:
            raise AssumptionError(f"E-G3-13-004: 提案不存在: {proposal_id}")
        if p.status != PENDING:
            raise AssumptionError(f"E-G3-13-005: 提案已裁决: {proposal_id}")
        if decision not in (APPROVED, REJECTED):
            raise AssumptionError(f"E-G3-13-006: 非法裁决: {decision}")
        if decision == APPROVED and token != "APPROVE":
            raise AssumptionError(
                f"E-G3-13-007: 批准须显式 APPROVE token（聊天“继续”不算）")
        ev = ApprovalEvent(proposal_id, decision, approver, decided_at,
                           token, p.payload_sha256, rejection_reason)
        p.status = decision
        p.decided_at = decided_at
        p.approved_by = approver if decision == APPROVED else None
        p.rejection_reason = rejection_reason if decision == REJECTED else None
        self.events.append(ev)
        return ev


@dataclass
class AssumptionSnapshot:
    """不可变快照：仅含已批准项；approval 的 payload 重算不符即失效。"""
    snapshot_id: str
    version: int = 1
    approved: Dict[str, dict] = field(default_factory=dict)  # proposal_id -> payload
    _frozen: bool = False
    _invalidated: bool = False
    _sha256: Optional[str] = None

    def build(self, registry: AssumptionRegistry) -> "AssumptionSnapshot":
        """从批准事件构建：拒绝项不进入计算。"""
        if self._frozen:
            raise AssumptionError("E-G3-13-008: 快照已冻结")
        for ev in registry.events:
            if ev.decision != APPROVED:
                continue
            p = registry.proposals[ev.proposal_id]
            # 批准 payload 任一字节变化 → 失效
            if payload_hash(p.payload) != ev.payload_sha256:
                self._invalidated = True
                continue
            self.approved[p.proposal_id] = dict(p.payload)
        self._frozen = True
        blob = {"snapshot_id": self.snapshot_id, "version": self.version,
                "approved": self.approved}
        self._sha256 = hashlib.sha256(
            json.dumps(blob, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
        return self

    @property
    def sha256(self) -> str:
        if not self._frozen:
            raise AssumptionError("E-G3-13-009: 未冻结快照无哈希")
        return self._sha256

    @property
    def invalidated(self) -> bool:
        return self._invalidated

    def approved_payloads(self) -> Dict[str, dict]:
        """进入计算的唯一入口：拒绝项不在此；失效快照抛错。"""
        if self._invalidated:
            raise PayloadChanged(
                "E-G3-13-010: 批准 payload 已变化 —— 快照失效，不得进入计算")
        if not self._frozen:
            raise AssumptionError("E-G3-13-009: 未冻结快照")
        return dict(self.approved)
