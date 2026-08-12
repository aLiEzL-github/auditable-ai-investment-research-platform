"""adjudication.py —— G6C-02 结果裁决、逾期/不可裁决、重述处理与 CalibrationStore。

基线验收（G6C-02）：
  · OPEN / RESOLVED / OVERDUE / UNRESOLVABLE 状态、裁决证据、宽限期、
    重述政策、选择性未决检测和不可变得分输入
  · 未到期、不可裁决或来源不足时不伪造 outcome；到期未裁决进入 OVERDUE；
    UNRESOLVABLE 有证据；重述不回写历史；材料性选择性未决阻断能力声明

执行计划要点（G6C-执行计划.md §4）：
  H-4  后见基准必拒（一票否决）：基准每个输入须带可得时刻，
       晚于预登记时刻的输入即拒 —— (available_at, seq) 字典序比较
  H-5  裁决可回溯至预登记记录与基准数据，无孤儿裁决
  H-6  共识不等于已验证（一票否决）：数据模型上可分辨（字段级），
       outcome_kind=CONSENSUS 不得当作已验证事实使用（consume_as_fact 拒绝）
"""
import datetime
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from prediction import PredictionProposal, PredictionRegistry
from time_order import cmp_micro

OPEN = "OPEN"
RESOLVED = "RESOLVED"
OVERDUE = "OVERDUE"
UNRESOLVABLE = "UNRESOLVABLE"
STATUSES = (OPEN, RESOLVED, OVERDUE, UNRESOLVABLE)

VERIFIED = "VERIFIED"          # 已验证事实（判定来源裁决）
CONSENSUS = "CONSENSUS"        # 共识 —— 不等于已验证事实（H-6）
UNAVAILABLE = "UNAVAILABLE"    # 来源不足


class AdjudicationError(ValueError):
    pass


class HindsightBenchmark(AdjudicationError):
    """H-4 一票否决：基准输入可得时刻晚于预登记时刻。"""


class OrphanAdjudication(AdjudicationError):
    """H-5：裁决不指向任何预登记记录 / 基准哈希缺失。"""


class ConsensusAsFact(AdjudicationError):
    """H-6 一票否决：把共识当已验证事实使用。"""


class SelectiveNonResolution(AdjudicationError):
    """材料性选择性未决：部分已裁决部分未决 —— 阻断能力声明。"""


class ImmutableScoreInput(AdjudicationError):
    """CalibrationStore 不可变：裁决后不得改写评分输入。"""


def _parse_ts(s: str) -> datetime.datetime:
    return datetime.datetime.fromisoformat(s.replace("Z", "+00:00"))


def _add_grace(ts: str, grace: str) -> str:
    """宽限期（P5D → +5 天）。非法格式即拒绝。"""
    if not grace.startswith("P") or not grace.endswith("D"):
        raise AdjudicationError(f"E-G6C-02-001: 非法宽限期: {grace!r}")
    days = int(grace[1:-1])
    return (_parse_ts(ts) + datetime.timedelta(days=days)).strftime(
        "%Y-%m-%dT%H:%M:%S.%fZ")


@dataclass
class BenchmarkInput:
    """基准输入（H-4）：每个输入须带可得时刻。"""
    key: str
    available_at: Tuple[str, int]   # (微秒时间戳, 同刻序号)
    value: str

    def to_dict(self) -> dict:
        return {"key": self.key, "available_at": self.available_at,
                "value": self.value}


@dataclass
class AdjudicationRecord:
    adjudication_id: str
    prediction_id: str
    status: str = OPEN
    outcome: Optional[str] = None
    outcome_kind: Optional[str] = None     # VERIFIED / CONSENSUS / UNAVAILABLE
    adjudicated_at: Optional[str] = None
    evidence: str = ""                     # 裁决证据（来源不足不可裁决）
    benchmark_sha256: str = ""
    restatement_of: Optional[str] = None   # 重述：指向被重述的旧记录

    def to_dict(self) -> dict:
        return {"adjudication_id": self.adjudication_id,
                "prediction_id": self.prediction_id, "status": self.status,
                "outcome": self.outcome, "outcome_kind": self.outcome_kind,
                "adjudicated_at": self.adjudicated_at, "evidence": self.evidence,
                "benchmark_sha256": self.benchmark_sha256,
                "restatement_of": self.restatement_of}


def benchmark_hash(inputs: List[BenchmarkInput]) -> str:
    import hashlib
    import json
    blob = json.dumps([i.to_dict() for i in sorted(
        inputs, key=lambda x: x.key)], ensure_ascii=False,
        sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


class AdjudicationRegistry:
    def __init__(self, predictions: PredictionRegistry,
                 clock=None, now_source=None):
        from time_order import MicroClock
        self.predictions = predictions
        self.clock = clock or MicroClock()
        self.records: Dict[str, AdjudicationRecord] = {}
        self._now = now_source or (lambda: self.clock.tick())

    # ── H-4/H-5：裁决 ──────────────────────────────────────────────
    def adjudicate(self, prediction_id: str, inputs: List[BenchmarkInput],
                   outcome: str, outcome_kind: str, evidence: str,
                   adjudicated_at: str,
                   token: str = "ADJUDICATE") -> AdjudicationRecord:
        """H-5 结构保证：裁决只能指向已预登记且已批准的预测
        （prediction.entry_to_adjudication）—— 孤儿裁决不可达。"""
        p = self.predictions.entry_to_adjudication(prediction_id)
        if not inputs:
            raise AdjudicationError(
                "E-G6C-02-002: 裁决须附基准数据（来源不足不得裁决）")
        if not evidence:
            raise AdjudicationError(
                "E-G6C-02-003: 裁决须附证据（无证据不得裁决）")
        if token != "ADJUDICATE":
            raise AdjudicationError(
                f"E-G6C-02-004: 裁决须显式 ADJUDICATE token，实为 {token!r}")
        if outcome_kind not in (VERIFIED, CONSENSUS, UNAVAILABLE):
            raise AdjudicationError(
                f"E-G6C-02-005: 非法 outcome_kind: {outcome_kind!r}")
        # H-4：后见基准必拒 —— 每个基准输入的可得时刻不得晚于预登记时刻
        for bi in inputs:
            if cmp_micro(*bi.available_at, *p.registered_at) > 0:
                raise HindsightBenchmark(
                    f"E-G6C-02-101: 后见基准被拒 —— 基准输入 {bi.key} 可得时刻 "
                    f"{bi.available_at[0]}#{bi.available_at[1]} 晚于预登记时刻 "
                    f"{p.registered_at[0]}#{p.registered_at[1]}（一票否决）")
        # 未到期不得裁决：observation_period_end + grace 之前无 outcome
        expiry = _add_grace(p.observation_period_end, p.grace_period)
        if _parse_ts(adjudicated_at) < _parse_ts(expiry):
            raise AdjudicationError(
                f"E-G6C-02-006: 未到期不得裁决 —— {adjudicated_at} < 到期 "
                f"{expiry}（观察期末 + 宽限期）。到期未裁决的预测进入 OVERDUE，"
                f"不得伪造 outcome")
        rec = AdjudicationRecord(
            adjudication_id=f"ADJ-{prediction_id}-{len(self.records) + 1}",
            prediction_id=prediction_id, status=RESOLVED, outcome=outcome,
            outcome_kind=outcome_kind, adjudicated_at=adjudicated_at,
            evidence=evidence, benchmark_sha256=benchmark_hash(inputs))
        self.records[rec.adjudication_id] = rec
        return rec

    # ── 逾期 ───────────────────────────────────────────────────────
    def advance_to_overdue(self, prediction_id: str,
                           now: Optional[str] = None) -> Optional[AdjudicationRecord]:
        """到期未裁决 → OVERDUE（now = 观察期末 + 宽限期之后）。"""
        p = self.predictions.predictions.get(prediction_id)
        if p is None:
            raise AdjudicationError(
                f"E-G6C-02-007: 预测未登记: {prediction_id}")
        existing = [r for r in self.records.values()
                    if r.prediction_id == prediction_id and r.status == RESOLVED]
        if existing:
            return None
        expiry = _add_grace(p.observation_period_end, p.grace_period)
        if now is None:
            now_ts, _ = self._now()
            now = now_ts
        if _parse_ts(now) >= _parse_ts(expiry):
            rec = AdjudicationRecord(
                adjudication_id=f"ADJ-{prediction_id}-OVERDUE",
                prediction_id=prediction_id, status=OVERDUE,
                evidence="到期未裁决")
            self.records[rec.adjudication_id] = rec
            return rec
        return None

    # ── 不可裁决（须有证据）────────────────────────────────────────
    def mark_unresolvable(self, prediction_id: str, evidence: str) -> AdjudicationRecord:
        if not evidence:
            raise AdjudicationError(
                "E-G6C-02-008: UNRESOLVABLE 必须有证据（来源不足须写明）")
        rec = AdjudicationRecord(
            adjudication_id=f"ADJ-{prediction_id}-UNRESOLVABLE",
            prediction_id=prediction_id, status=UNRESOLVABLE, evidence=evidence)
        self.records[rec.adjudication_id] = rec
        return rec

    # ── 重述：不回写历史 ───────────────────────────────────────────
    def restate(self, old_adjudication_id: str,
                new_outcome: str, new_kind: str, evidence: str,
                adjudicated_at: str) -> AdjudicationRecord:
        old = self.records.get(old_adjudication_id)
        if old is None:
            raise AdjudicationError(
                f"E-G6C-02-009: 重述目标不存在: {old_adjudication_id}")
        if old.status != RESOLVED:
            raise AdjudicationError(
                f"E-G6C-02-010: 仅 RESOLVED 可重述: {old_adjudication_id}")
        rec = AdjudicationRecord(
            adjudication_id=f"ADJ-{old.prediction_id}-R{old.adjudication_id}",
            prediction_id=old.prediction_id, status=RESOLVED,
            outcome=new_outcome, outcome_kind=new_kind,
            adjudicated_at=adjudicated_at, evidence=evidence,
            benchmark_sha256=old.benchmark_sha256,
            restatement_of=old_adjudication_id)
        self.records[rec.adjudication_id] = rec
        # 旧记录逐字未动（不回写历史）—— 由 CalibrationStore 不可变强制
        return rec

    # ── H-6：共识 ≠ 已验证 ─────────────────────────────────────────
    def consume_as_fact(self, adjudication_id: str) -> str:
        """把裁决结果当作已验证事实使用的唯一入口。

        一票否决：outcome_kind=CONSENSUS 一律拒绝 —— 多 Agent 一致
        不等于事实；UNAVAILABLE 同样拒绝。字段级可分辨，不是文字提醒。
        """
        rec = self.records.get(adjudication_id)
        if rec is None:
            raise OrphanAdjudication(
                f"E-G6C-02-102: 裁决不存在（孤儿引用）: {adjudication_id}")
        if rec.status != RESOLVED or rec.outcome is None:
            raise AdjudicationError(
                f"E-G6C-02-011: 非 RESOLVED 无 outcome 可消费: {adjudication_id}")
        if rec.outcome_kind != VERIFIED:
            raise ConsensusAsFact(
                f"E-G6C-02-103: outcome_kind={rec.outcome_kind} —— "
                f"共识/来源不足不得当作已验证事实使用（一票否决）")
        return rec.outcome

    # ── 选择性未决检测 ─────────────────────────────────────────────
    def selective_non_resolution(self, material_ids: List[str]) -> List[str]:
        """材料性预测中：部分已裁决、部分未裁决 → 名单（阻断能力声明）。"""
        unresolved = []
        resolved = []
        for pid in material_ids:
            recs = [r for r in self.records.values() if r.prediction_id == pid]
            if any(r.status == RESOLVED for r in recs):
                resolved.append(pid)
            elif any(r.status == UNRESOLVABLE for r in recs):
                unresolved.append(pid)   # UNRESOLVABLE 有证据，属「未裁决」
            else:
                unresolved.append(pid)
        if unresolved and resolved:
            return unresolved
        return []


class CalibrationStore:
    """不可变得分输入：裁决记录只追加，不更新不删除（重述另起新记录）。"""

    def __init__(self, registry: AdjudicationRegistry,
                 snapshot_predictions: Dict[str, dict]):
        self.registry = registry
        self.snapshot = snapshot_predictions       # PredictionSnapshot 冻结
        self._inputs: Dict[str, Tuple[dict, dict]] = {}
        self._sealed = False
        self._load()

    def _load(self) -> None:
        for rec in self.registry.records.values():
            pred = self.registry.predictions.predictions.get(rec.prediction_id)
            if pred is None:
                raise OrphanAdjudication(
                    f"E-G6C-02-102: 孤儿裁决 —— {rec.adjudication_id} "
                    f"不指向任何预登记记录（H-5）")
            if rec.status == RESOLVED and rec.outcome is not None:
                self._inputs[rec.adjudication_id] = (pred.to_dict(), rec.to_dict())

    def scoring_inputs(self) -> Dict[str, Tuple[dict, dict]]:
        if not self._sealed:
            raise AdjudicationError(
                "E-G6C-02-012: 评分输入未封存 —— 校准前须先 seal()")
        return dict(self._inputs)

    def seal(self) -> None:
        """封存：此后任何追加/改写 → 拒绝（不可变评分输入）。"""
        self._sealed = True

    def add(self, rec: AdjudicationRecord) -> None:
        if self._sealed:
            raise ImmutableScoreInput(
                "E-G6C-02-013: 评分输入已封存 —— 裁决结果不得事后写入")
        pred = self.registry.predictions.predictions.get(rec.prediction_id)
        if pred is None:
            raise OrphanAdjudication(
                f"E-G6C-02-102: 孤儿裁决（H-5）: {rec.prediction_id}")
        if rec.status == RESOLVED and rec.outcome is not None:
            self._inputs[rec.adjudication_id] = (pred.to_dict(), rec.to_dict())
