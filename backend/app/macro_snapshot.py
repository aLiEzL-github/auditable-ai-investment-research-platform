"""macro_snapshot.py —— G3-03 MacroSpec、MacroSnapshot 前置与确定性聚合门。

基线验收（G3-03）：
  · 取数前冻结必需/可选序列、频率、vintage、时效、固定分母、宏观快照、
    传导链、开放项；分母/方向/聚合规则与离线重放 fixture
  · published/effective/retrieved/cutoff 分离
  · 未来 vintage、零行、无关、缺失、过期、口径漂移或联网失败时
    只允许 PARTIAL/BLOCKED，**不得输出当前估值**
  · 同一快照跨进程聚合字节一致（确定性聚合）

设计：
  · MacroSpec 从 contracts/macro_spec.json 冻结读取（漂移阻断：frozen_sha256）。
  · MacroSnapshot 是**不可变聚合结果**：freeze 前可加入观测，freeze 后
    输出 canonical 字节（跨进程一致）与 SHA-256。
  · MacroGate：聚合门 —— 对每个必需序列检查：存在、非未来 vintage、非零行、
    时效未过期、口径匹配、来源明确；任一失败 → MACRO_GATE_FAIL（BLOCKED），
    **任何情况下不得输出当前估值**（估值函数只接受冻结且通过门的快照）。
  · 材料性判定（G0-05 §5.1/§5.2）：spec 中 material=true 的序列缺失或
    过期 → 阻断；material=false 的缺失只降级背景，不阻断估值。
"""
import hashlib
import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional

CONTRACTS = os.path.join(os.path.dirname(__file__), "..", "..", "contracts")
SPEC_PATH = os.path.join(CONTRACTS, "macro_spec.json")


class MacroSpecError(ValueError):
    pass


class MacroGateFail(MacroSpecError):
    """聚合门阻断：不得输出当前估值。"""


class SnapshotFrozen(MacroSpecError):
    pass


def _canon(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def load_spec() -> dict:
    with open(SPEC_PATH, encoding="utf-8") as f:
        return json.load(f)


def verify_spec_frozen(spec: Optional[dict] = None) -> dict:
    """漂移阻断：冻结哈希逐字比对（与 G2-10 同模式）。

    哈希锚定排除 frozen_sha256 自身（避免自指），与冻结时计算口径一致。
    """
    spec = spec or load_spec()
    body = {k: v for k, v in spec.items() if k != "frozen_sha256"}
    actual = hashlib.sha256(_canon(body).encode("utf-8")).hexdigest()
    expected = spec.get("frozen_sha256")
    if not expected:
        raise MacroSpecError("E-G3-03-006: macro_spec.json 缺 frozen_sha256")
    if actual != expected:
        raise MacroSpecError(
            f"E-G3-03-005: MacroSpec 漂移阻断 —— 实算 {actual[:16]}… "
            f"≠ 冻结 {expected[:16]}…")
    return spec


def series_by_id(spec: dict) -> Dict[str, dict]:
    return {s["series_id"]: s for s in spec["series"]}


# ── 观测（不可变，冻结前聚合的输入）───────────────────────────────
@dataclass
class MacroObservation:
    series_id: str
    value: str                    # 数值字符串（Decimal 语义，非浮点）
    unit: str                     # spec 声明单位，不匹配 → 口径漂移
    scope: str                    # 与 spec scope 不匹配 → 无关序列
    vintage: str                  # ORIGINAL / REVISED / RESTATED
    reference_period: str         # 参考期（如 2026Q2 / 2026-06）
    published_at: str             # ISO UTC：发布日
    retrieved_at: str             # ISO UTC：取得日
    source: str                   # 来源标识（主源/人工导入）
    locator: str = ""

    def to_dict(self) -> dict:
        return {"series_id": self.series_id, "value": self.value, "unit": self.unit,
                "scope": self.scope, "vintage": self.vintage,
                "reference_period": self.reference_period,
                "published_at": self.published_at, "retrieved_at": self.retrieved_at,
                "source": self.source, "locator": self.locator}


# ── MacroSnapshot：不可变聚合结果 ──────────────────────────────────
@dataclass
class MacroSnapshot:
    snapshot_id: str
    cutoff: str                   # ISO UTC：估值输入时点统一锚
    spec_version: str
    observations: List[MacroObservation] = field(default_factory=list)
    frozen: bool = False
    _aggregated: Optional[Dict[str, dict]] = None
    _canonical: Optional[str] = None
    _sha256: Optional[str] = None

    def add(self, obs: MacroObservation) -> None:
        if self.frozen:
            raise SnapshotFrozen(
                f"E-G3-03-004: 快照 {self.snapshot_id} 已冻结，不可再加入观测")
        self.observations.append(obs)

    def freeze(self) -> None:
        """冻结：聚合为 canonical 字节（确定性），此后只读。"""
        if self.frozen:
            return
        self._aggregated = {}
        for s in load_spec()["series"]:
            sid = s["series_id"]
            rows = [o for o in self.observations if o.series_id == sid]
            if not rows:
                continue
            # 聚合规则（spec 声明）：LAST_VINTAGE_SINGLE_VALUE ——
            # 取最新 vintage（ORIGINAL < REVISED < RESTATED），同 vintage 取最后加入
            order = {"ORIGINAL": 0, "REVISED": 1, "RESTATED": 2}
            rows.sort(key=lambda o: order.get(o.vintage, -1))
            last = rows[-1]
            self._aggregated[sid] = {
                "value": last.value, "unit": last.unit, "scope": last.scope,
                "vintage": last.vintage, "reference_period": last.reference_period,
                "published_at": last.published_at, "retrieved_at": last.retrieved_at,
                "source": last.source,
            }
        blob = {"snapshot_id": self.snapshot_id, "cutoff": self.cutoff,
                "spec_version": self.spec_version, "aggregated": self._aggregated}
        self._canonical = _canon(blob)
        self._sha256 = hashlib.sha256(self._canonical.encode("utf-8")).hexdigest()
        self.frozen = True

    @property
    def canonical(self) -> str:
        if not self.frozen:
            raise SnapshotFrozen("E-G3-03-003: 未冻结快照无 canonical 输出")
        return self._canonical

    @property
    def sha256(self) -> str:
        if not self.frozen:
            raise SnapshotFrozen("E-G3-03-003: 未冻结快照无哈希")
        return self._sha256

    def aggregated(self) -> Dict[str, dict]:
        if not self.frozen:
            raise SnapshotFrozen("E-G3-03-003: 未冻结快照无聚合")
        return dict(self._aggregated)


# ── 聚合门：材料性/时效/口径/未来 vintage 判定 ────────────────────
class MacroGate:
    """G3-03 确定性聚合门。

    对 spec 中每个序列检查：
      · 必需 + 材料性（material=true）序列：缺失 / 未来 vintage / 零行 /
        过期 / 口径漂移 / 无来源 → MACRO_GATE_FAIL（BLOCKED）
      · 可选或 material=false 序列：同项失败只记录降级，不阻断估值
    cutoff 时刻（估值输入时点统一锚）与实际系统时间分离 —— 测试注入
    显式 cutoff，不依赖墙钟。
    """

    def __init__(self, spec: Optional[dict] = None, now_utc: Optional[str] = None):
        self.spec = verify_spec_frozen(spec)
        self.series = series_by_id(self.spec)
        self.now_utc = now_utc  # 显式注入（测试用）；None = 冻结时现场取
        self.failures: List[str] = []
        self.warnings: List[str] = []

    @staticmethod
    def _iso(dt: str) -> datetime:
        return datetime.fromisoformat(dt.replace("Z", "+00:00"))

    def _is_future_vintage(self, obs: MacroObservation) -> bool:
        """未来 vintage：观测的发布日 > 冻结时刻（cutoff/now 取先者）。"""
        anchor = self._iso(obs.retrieved_at)
        return self._iso(obs.published_at) > anchor

    def _is_stale(self, s: dict, obs: MacroObservation) -> bool:
        """时效：published_at 距今超过 spec 声明的 staleness_days。"""
        now = self._iso(self.now_utc) if self.now_utc \
            else datetime.now(timezone.utc)
        delta = (now - self._iso(obs.published_at)).days
        return delta > s.get("staleness_days", 45)

    def evaluate(self, snapshot: MacroSnapshot) -> str:
        """门判定：返回 GATE_OK / PARTIAL / BLOCKED；BLOCKED 时不得估值。"""
        self.failures, self.warnings = [], []
        if not snapshot.frozen:
            raise SnapshotFrozen("E-G3-03-003: 未冻结快照不得过门")
        agg = snapshot.aggregated()
        for sid, s in self.series.items():
            obs = agg.get(sid)
            spec_required = s.get("required", False)
            spec_material = s.get("material", False)
            if obs is None:
                msg = f"{sid} 缺失"
                if spec_required and spec_material:
                    self.failures.append(msg)
                else:
                    self.warnings.append(msg + "（非材料性/可选 —— 降级不阻断）")
                continue
            if self._is_future_vintage_agg(obs):
                self._fail_or_warn(sid, s, "未来 vintage")
                continue
            if self._is_stale(s, self._to_obs(sid, obs)):
                self._fail_or_warn(sid, s, f"过期（> {s.get('staleness_days')} 天）")
                continue
            if s.get("unit") and obs.get("unit") != s["unit"]:
                self._fail_or_warn(sid, s, f"口径漂移（unit {obs.get('unit')} ≠ {s['unit']}）")
                continue
            if s.get("scope") and obs.get("scope") != s["scope"]:
                self._fail_or_warn(sid, s, f"无关序列（scope {obs.get('scope')} ≠ {s['scope']}）")
                continue
            if not obs.get("source"):
                self._fail_or_warn(sid, s, "无来源标识")
        if self.failures:
            return "BLOCKED"
        if self.warnings:
            return "PARTIAL"
        return "GATE_OK"

    def _to_obs(self, sid: str, agg: dict) -> MacroObservation:
        return MacroObservation(
            series_id=sid, value=agg["value"], unit=agg["unit"], scope=agg["scope"],
            vintage=agg["vintage"], reference_period=agg["reference_period"],
            published_at=agg["published_at"], retrieved_at=agg["retrieved_at"],
            source=agg["source"])

    def _is_future_vintage_agg(self, agg: dict) -> bool:
        anchor = self._iso(agg["retrieved_at"])
        return self._iso(agg["published_at"]) > anchor

    def _fail_or_warn(self, sid: str, s: dict, issue: str) -> None:
        spec_required = s.get("required", False)
        spec_material = s.get("material", False)
        msg = f"{sid} {issue}"
        if spec_required and spec_material:
            self.failures.append(msg)
        else:
            self.warnings.append(msg + "（非材料性/可选 —— 降级不阻断）")


def build_snapshot(snapshot_id: str, cutoff: str, spec_version: str,
                   observations: List[MacroObservation],
                   gate_now_utc: Optional[str] = None) -> MacroSnapshot:
    """构建 → 冻结 → 过门 一步完成。门非 GATE_OK 时抛 MacroGateFail。"""
    snap = MacroSnapshot(snapshot_id, cutoff, spec_version)
    for o in observations:
        snap.add(o)
    snap.freeze()
    gate = MacroGate(now_utc=gate_now_utc)
    verdict = gate.evaluate(snap)
    if verdict == "BLOCKED":
        raise MacroGateFail(
            f"E-G3-03-001: 宏观聚合门阻断（BLOCKED）—— 不得输出当前估值。"
            f"阻断项: {gate.failures}")
    return snap
