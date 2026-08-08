"""snapshot_service.py —— G2-08 Snapshot/vintage 与黄金 fixture 校验。

基线验收（G2-08）：
  · 同一运行不混用事后数据（cutoff 语义）
  · 离线 CI 可复跑（黄金 fixture 合成可复现）
BF-01 回填：cutoff 后对象、错 scope 或漂移输入必拒绝。
"""
import json
from datetime import datetime, timezone
from typing import List

from repository import Snapshot, FactRecord, RawArtifact  # noqa: F401


class SnapshotService:
    def __init__(self, session):
        self.s = session

    # ── 创建 snapshot（cutoff 冻结）─────────────────────────────────
    def create_snapshot(self, snapshot_id: str, cutoff: datetime,
                        golden: bool = False, scope_set: List[str] = None) -> Snapshot:
        snap = Snapshot(id=snapshot_id, schema_version="1.0",
                        created_at=datetime.now(timezone.utc),
                        cutoff=cutoff, frozen=False, golden=golden,
                        scope_set=json.dumps(scope_set or []),
                        facts=json.dumps([]), version=1)
        self.s.add(snap)
        self.s.commit()
        return snap

    def freeze(self, snapshot_id: str) -> Snapshot:
        snap = self.s.query(Snapshot).filter_by(id=snapshot_id).first()
        if snap is None:
            raise ValueError(f"E-G2-08-004: snapshot 不存在: {snapshot_id}")
        snap.frozen = True
        snap.version += 1
        self.s.commit()
        return snap

    # ── 绑定事实：cutoff / scope / 漂移 三重校验 ────────────────────
    def bind_fact(self, snapshot_id: str, fact: FactRecord) -> Snapshot:
        snap = self.s.query(Snapshot).filter_by(id=snapshot_id).first()
        if snap is None:
            raise ValueError(f"E-G2-08-004: snapshot 不存在: {snapshot_id}")
        if snap.frozen:
            raise ValueError("E-G2-08-005: snapshot 已冻结，不可再绑定")

        # ① cutoff 后对象必拒绝（同一运行不混用事后数据 / BF-01）
        artifact = self.s.query(RawArtifact).filter_by(id=fact.artifact_id).first()
        if artifact is None:
            raise ValueError(f"E-G2-08-006: 工件不存在: {fact.artifact_id}")
        if artifact.acquired_at > snap.cutoff:
            raise ValueError(
                f"E-G2-08-001: cutoff 后对象拒绝: {fact.id} acquired_at "
                f"{artifact.acquired_at} > cutoff {snap.cutoff}")

        # ② 错 scope 必拒绝（BF-01）
        scopes = set(json.loads(snap.scope_set))
        if fact.scope not in scopes:
            raise ValueError(f"E-G2-08-002: 错 scope 拒绝: {fact.scope} ∉ {scopes}")

        # ③ 漂移输入必拒绝（BF-01 / 黄金 snapshot）
        if snap.golden:
            expect = _golden_hash(snap.id, fact.id)
            if fact.value != expect.get("value") or fact.period != expect.get("period"):
                raise ValueError(
                    f"E-G2-08-003: 漂移输入拒绝: {fact.id} 与黄金 fixture 声明不符")

        facts = json.loads(snap.facts)
        facts.append(fact.id)
        snap.facts = json.dumps(facts)
        snap.version += 1
        self.s.commit()
        return snap


def _golden_hash(snapshot_id: str, fact_id: str) -> dict:
    """黄金 fixture 声明（合成确定性）：由 snapshot/fact id 派生的固定期望。"""
    import hashlib
    seed = hashlib.sha256(f"{snapshot_id}:{fact_id}".encode()).hexdigest()
    value = str(int(seed[:8], 16) % 100000)
    period = f"2026-{(int(seed[8:10], 16) % 12) + 1:02d}"
    return {"value": value, "period": period}
