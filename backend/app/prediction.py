"""prediction.py —— G6C-01 PredictionProposal、独立批准与不可变 PredictionSnapshot。

基线验收（G6C-01）：
  · 首个有限 DecisionVersion 预登记 3—5 个材料性预测；每项含
    metric_id/operator/threshold、scope、unit、观察期、判定来源、
    resolution rule、grace period、forecast_probability、reference_probability、
    model/prompt/method/cluster 版本，并绑定 candidate_hash / ResearchContract
    hash / evidence_pack_id / cutoff / snapshot_root / payload_hash 和审批事件
  · Brier 的 forecast 与 reference 概率均在结果可见前冻结（H-1）
  · LLM 无批准写权；人工逐项批准；未批准预测不进入快照
  · 任一候选/合同/证据包/cutoff/snapshot 字节变化使批准失效，必须
    重新提议、批准并生成新 PredictionSnapshot（H-2）

执行计划要点（G6C-执行计划.md §4）：
  H-1  预登记在结果可知之前落库并冻结：registered_at (ts,seq) <
       outcome_available_at —— 事后补登记须 FAIL
  H-2  冻结后字节变更须被拒绝或触发失效：改一字节须 FAIL
  H-3  未预登记的预测不得进入裁决（本模块对裁决入口的断言）

时序精度 = G6C-执行计划.md 附.6（U 裁定）：微秒时间戳 + 同刻序号，
比较用 (timestamp, seq) 字典序（time_order.cmp_micro）。
"""
import hashlib
import json
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from time_order import MicroClock, cmp_micro

PENDING_APPROVAL = "PENDING_APPROVAL"
APPROVED = "APPROVED"
REJECTED = "REJECTED"

OPERATORS = (">=", "<=", "within")


class PredictionError(ValueError):
    pass


class LateRegistration(PredictionError):
    """H-1：结果已可知仍补登记 —— 一票否决（后见伪装预测）。"""


class NoApprovalWrite(PredictionError):
    """LLM/自动化无批准写权（G0-04 §2 / G3-13 同款语义）。"""


class BindingChanged(PredictionError):
    """H-2：任一绑定（候选/合同/证据包/cutoff/snapshot）字节变化 → 批准失效。"""


class UnregisteredPrediction(PredictionError):
    """H-3：未预登记的预测不得进入裁决。"""


class NotApproved(PredictionError):
    """未批准预测不进入快照 / 不得裁决。"""


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def payload_hash(payload: dict) -> str:
    """payload = 除 registered_at/status/审批事件外的全部可冻结字段。"""
    return _sha(json.dumps(payload, ensure_ascii=False, sort_keys=True,
                           separators=(",", ":")).encode("utf-8"))


@dataclass
class PredictionProposal:
    """一份预登记预测。payload 字段冻结即不可事后修改（H-2）。"""
    prediction_id: str
    decision_version_id: str        # 首个有限 DecisionVersion
    metric_id: str
    operator: str                   # >= / <= / within
    threshold: str
    scope: str
    unit: str
    observation_period_start: str
    observation_period_end: str
    adjudication_source: str        # 判定来源（结果可得性的唯一依据）
    resolution_rule: str            # 判定规则
    grace_period: str               # 宽限期（如 P5D）
    forecast_probability: str       # Brier 输入之一 —— 结果可见前冻结（H-1）
    reference_probability: str      # 基准概率 —— 同上
    model_version: str
    prompt_version: str
    method: str
    cluster_version: str
    # 绑定（H-2 的对象）：任一字节变化 → 批准失效
    bindings: Dict[str, str]        # candidate_hash / contract_hash /
                                    # evidence_pack_id / cutoff / snapshot_root
    outcome_available_at: str       # 判定来源最早可得时刻（ISO 微秒 UTC）
    registered_at: Optional[Tuple[str, int]] = None   # H-1 预登记时刻
    status: str = PENDING_APPROVAL
    payload_sha256: str = ""
    approved_by: Optional[str] = None
    decided_at: Optional[str] = None
    rejection_reason: Optional[str] = None

    def __post_init__(self):
        if self.operator not in OPERATORS:
            raise PredictionError(f"E-G6C-01-001: 非法 operator: {self.operator!r}")
        payload = self._payload_dict()
        self.payload_sha256 = payload_hash(payload)

    def _payload_dict(self) -> dict:
        return {
            "prediction_id": self.prediction_id,
            "decision_version_id": self.decision_version_id,
            "metric_id": self.metric_id,
            "operator": self.operator,
            "threshold": self.threshold,
            "scope": self.scope,
            "unit": self.unit,
            "observation_period_start": self.observation_period_start,
            "observation_period_end": self.observation_period_end,
            "adjudication_source": self.adjudication_source,
            "resolution_rule": self.resolution_rule,
            "grace_period": self.grace_period,
            "forecast_probability": self.forecast_probability,
            "reference_probability": self.reference_probability,
            "model_version": self.model_version,
            "prompt_version": self.prompt_version,
            "method": self.method,
            "cluster_version": self.cluster_version,
            "bindings": self.bindings,
            "outcome_available_at": self.outcome_available_at,
        }

    def to_dict(self) -> dict:
        d = self._payload_dict()
        d.update({"status": self.status, "registered_at": self.registered_at,
                  "payload_sha256": self.payload_sha256,
                  "approved_by": self.approved_by,
                  "decided_at": self.decided_at,
                  "rejection_reason": self.rejection_reason})
        return d


class PredictionRegistry:
    """预登记 + 独立批准。LLM 无批准写权；批准逐项人工。"""

    def __init__(self, clock: Optional[MicroClock] = None):
        self.clock = clock or MicroClock()
        self.predictions: Dict[str, PredictionProposal] = {}

    # ── H-1：预登记 ────────────────────────────────────────────────
    def register(self, pred: PredictionProposal) -> PredictionProposal:
        """预登记：registered_at = (微秒时间戳, 同刻序号)。

        时序断言：registered_at < outcome_available_at —— 结果已可知
        仍登记（事后补登记）→ FAIL（一票否决，变异注入抓点）。
        """
        if pred.prediction_id in self.predictions:
            raise PredictionError(
                f"E-G6C-01-002: 预测已登记: {pred.prediction_id}")
        pred.registered_at = self.clock.tick()
        if cmp_micro(pred.registered_at[0], pred.registered_at[1],
                     pred.outcome_available_at, 0) >= 0:
            raise LateRegistration(
                f"E-G6C-01-101: 结果已可知仍登记（事后补登记）—— "
                f"registered_at {pred.registered_at[0]}#{pred.registered_at[1]}"
                f" ≥ outcome_available_at {pred.outcome_available_at}。"
                f"Brier 概率必须在结果可见前冻结（H-1）")
        self.predictions[pred.prediction_id] = pred
        return pred

    def register_with_time(self, pred: PredictionProposal,
                           ts: str, seq: int) -> PredictionProposal:
        """测试用：注入确定时序的登记（不依赖真实时钟）。"""
        if pred.prediction_id in self.predictions:
            raise PredictionError(
                f"E-G6C-01-002: 预测已登记: {pred.prediction_id}")
        pred.registered_at = (ts, seq)
        if cmp_micro(ts, seq, pred.outcome_available_at, 0) >= 0:
            raise LateRegistration(
                f"E-G6C-01-101: 结果已可知仍登记（事后补登记）")
        self.predictions[pred.prediction_id] = pred
        return pred

    # ── 独立批准（人工逐项）────────────────────────────────────────
    def _assert_approver(self, approver: str) -> None:
        if approver in ("LLM", "AUTOMATION", "L8", "L9", "L10"):
            raise NoApprovalWrite(
                f"E-G6C-01-003: {approver} 无批准写权（G0-04 §2）")

    def decide(self, prediction_id: str, decision: str, approver: str,
               decided_at: str, token: str,
               rejection_reason: Optional[str] = None) -> PredictionProposal:
        self._assert_approver(approver)
        p = self.predictions.get(prediction_id)
        if p is None:
            raise PredictionError(
                f"E-G6C-01-004: 预测未登记: {prediction_id}")
        if p.status != PENDING_APPROVAL:
            raise PredictionError(
                f"E-G6C-01-005: 预测已裁决: {prediction_id}")
        if decision not in (APPROVED, REJECTED):
            raise PredictionError(
                f"E-G6C-01-006: 非法裁决: {decision}")
        if decision == APPROVED and token != "APPROVE":
            raise PredictionError(
                f"E-G6C-01-007: 批准须显式 APPROVE token（聊天“继续”不算）")
        p.status = decision
        p.approved_by = approver if decision == APPROVED else None
        p.decided_at = decided_at
        p.rejection_reason = rejection_reason if decision == REJECTED else None
        return p

    # ── H-3：进入裁决的入口断言 ────────────────────────────────────
    def entry_to_adjudication(self, prediction_id: str) -> PredictionProposal:
        """未预登记（H-3）或未批准（未批准预测不进入快照/裁决）→ 拒绝。"""
        p = self.predictions.get(prediction_id)
        if p is None:
            raise UnregisteredPrediction(
                f"E-G6C-01-008: 未预登记的预测不得进入裁决: {prediction_id}")
        if p.status != APPROVED:
            raise NotApproved(
                f"E-G6C-01-009: 未批准预测不得进入裁决: {prediction_id} "
                f"（status={p.status}）")
        return p


@dataclass
class PredictionSnapshot:
    """不可变快照：仅含已批准预测；任一绑定字节变化 → 失效。"""
    snapshot_id: str
    decision_version_id: str
    version: int = 1
    predictions: Dict[str, dict] = field(default_factory=dict)
    _frozen: bool = False
    _invalidated: bool = False
    _invalid_bindings: List[str] = field(default_factory=list)
    _sha256: Optional[str] = None

    def build(self, registry: PredictionRegistry,
              binding_objects: Dict[str, dict]) -> "PredictionSnapshot":
        """从已批准预测构建。binding_objects = 每个绑定键的当前实际对象；
        逐个就地重算哈希比对（H-2：任一字节变化 → 失效）。"""
        if self._frozen:
            raise PredictionError("E-G6C-01-010: 快照已冻结")
        for pid, p in registry.predictions.items():
            if p.status != APPROVED:
                continue                       # 未批准预测不进入快照
            self._verify_bindings(p, binding_objects)
            self.predictions[pid] = p.to_dict()
        self._frozen = True
        blob = {"snapshot_id": self.snapshot_id,
                "decision_version_id": self.decision_version_id,
                "version": self.version, "predictions": self.predictions}
        self._sha256 = _sha(json.dumps(blob, ensure_ascii=False,
                                       sort_keys=True).encode("utf-8"))
        return self

    def _verify_bindings(self, p: PredictionProposal,
                         binding_objects: Dict[str, dict]) -> None:
        """绑定逐键校验：candidate_hash / contract_hash 就地重算比对；
        evidence_pack_id / cutoff / snapshot_root 是 id/时刻 —— 直接比对。
        任一不符 → 批准失效（H-2）。"""
        for key, anchored in p.bindings.items():
            obj = binding_objects.get(key)
            if obj is None:
                self._invalidated = True
                self._invalid_bindings.append(f"{key}(缺失)")
                continue
            if isinstance(obj, str):
                actual = obj                    # id / 时刻：直接比对
            else:
                actual = _sha(json.dumps(obj, ensure_ascii=False,
                                         sort_keys=True,
                                         separators=(",", ":")).encode("utf-8"))
            if actual != anchored:
                self._invalidated = True
                self._invalid_bindings.append(
                    f"{key}(锚定{anchored[:8]}…≠实际{actual[:8]}…)")

    @property
    def sha256(self) -> str:
        if not self._frozen:
            raise PredictionError("E-G6C-01-011: 未冻结快照无哈希")
        return self._sha256

    @property
    def invalidated(self) -> bool:
        return self._invalidated

    @property
    def invalid_bindings(self) -> List[str]:
        return list(self._invalid_bindings)

    def approved_predictions(self) -> Dict[str, dict]:
        """评分/裁决的唯一步入口：失效快照抛错（H-2 的消费侧强制）。"""
        if self._invalidated:
            raise BindingChanged(
                f"E-G6C-01-012: 绑定字节已变化 —— 批准失效（"
                + "; ".join(self._invalid_bindings[:3]) + "）。"
                + "必须重新提议、批准并生成新 PredictionSnapshot")
        if not self._frozen:
            raise PredictionError("E-G6C-01-011: 未冻结快照")
        return dict(self.predictions)
