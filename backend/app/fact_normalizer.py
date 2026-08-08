"""fact_normalizer.py —— G2-07 FactRecord 归一化、冲突与预注册容差。

基线验收（G2-07）：
  1. scope/period/unit/basis/vintage 不可比时 → NOT_COMPARABLE
  2. 缺失不补零
  3. 关键冲突阻断
  4. 同源镜像不算独立来源
"""
from dataclasses import dataclass
from typing import Optional

# 五要素（scope/period/unit/basis/vintage）+ metric 构成归一化键
KEY_FIELDS = ("metric", "scope", "period", "unit", "basis", "vintage")


@dataclass
class Conflict:
    metric: str
    scope: str
    period: str
    left: str
    right: str
    kind: str  # CONFLICT / MIRROR / NOT_COMPARABLE


class FactNormalizer:
    def __init__(self, tolerance: float = 0.0,
                 material_metrics: Optional[set] = None):
        """tolerance：预注册容差（相对差，如 0.01 = 1%）。
        material_metrics：关键指标集合（冲突即阻断）。"""
        self.tolerance = tolerance
        self.material_metrics = material_metrics or set()

    # ── 1. 五要素可比性判定 ─────────────────────────────────────────
    @staticmethod
    def compare_key(fact) -> tuple:
        return tuple(getattr(fact, f, None) for f in KEY_FIELDS)

    def comparability(self, f1, f2) -> str:
        """五要素逐项相等 → COMPARABLE；任一不可比 → NOT_COMPARABLE。"""
        if self.compare_key(f1) == self.compare_key(f2):
            return "COMPARABLE"
        return "NOT_COMPARABLE"

    # ── 2. 缺失不补零 ───────────────────────────────────────────────
    @staticmethod
    def check_missing_value(fact) -> None:
        """value 缺失/空白 → 拒绝；绝不补零（基线：缺失不补零）。"""
        v = getattr(fact, "value", None)
        if v is None or str(v).strip() == "":
            raise ValueError("E-G2-07-001: FactRecord 缺值（缺失不补零，禁止填 0）")

    # ── 3. 同源镜像判定（同源镜像不算独立来源）─────────────────────
    @staticmethod
    def is_mirror(f1, f2) -> bool:
        """同一 source 的重复值 = 镜像，不作为独立来源/冲突证据。"""
        return getattr(f1, "source_id", None) == getattr(f2, "source_id", None)

    # ── 4. 冲突检测 + 关键冲突阻断 + 预注册容差 ────────────────────
    def detect_conflict(self, f1, f2) -> Optional[Conflict]:
        if self.comparability(f1, f2) != "COMPARABLE":
            return Conflict(getattr(f1, "metric", ""), getattr(f1, "scope", ""),
                            getattr(f1, "period", ""), getattr(f1, "value", ""),
                            getattr(f2, "value", ""), "NOT_COMPARABLE")
        if self.is_mirror(f1, f2):
            return Conflict(getattr(f1, "metric", ""), getattr(f1, "scope", ""),
                            getattr(f1, "period", ""), getattr(f1, "value", ""),
                            getattr(f2, "value", ""), "MIRROR")
        v1, v2 = self._num(f1.value), self._num(f2.value)
        if v1 is None or v2 is None:
            return None
        if abs(v1 - v2) <= self.tolerance * max(abs(v1), abs(v2), 1e-9):
            return None  # 预注册容差内：不冲突
        kind = "CONFLICT"
        metric = getattr(f1, "metric", "")
        if metric in self.material_metrics:
            raise ValueError(
                f"E-G2-07-002: 关键指标冲突阻断: {metric} {v1} vs {v2}（容差 {self.tolerance}）")
        return Conflict(getattr(f1, "metric", ""), getattr(f1, "scope", ""),
                        getattr(f1, "period", ""), f1.value, f2.value, kind)

    @staticmethod
    def _num(v: str):
        try:
            return float(v.replace(",", ""))
        except (TypeError, ValueError):
            return None
