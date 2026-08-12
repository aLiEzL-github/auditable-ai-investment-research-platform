"""calibration.py —— G6C-03 预登记基准、Brier/skill、分层校准与充分性门。

基线验收（G6C-03）：
  · base rate、Brier/reference Brier/skill、按 scope/horizon/model/prompt/
    method 分层、cluster-aware effective_n/CI、展示政策和机器可读
    calibration_status
  · 只有 resolved≥30、≥2 个报告期、≥2 个 horizon bucket、clustered
    effective_n≥20、置信区间存在且无材料性选择性未决时为
    CALIBRATION_SUFFICIENT；否则仅 CALIBRATION_PENDING / INSUFFICIENT_SAMPLE，
    不得宣称预测能力

执行计划要点（G6C-执行计划.md §4）：
  H-7  充分性门：样本量不足时阻断「已校准」表述（而非附警告输出）
  H-8  CALIBRATION_PENDING 不得冒充能力（一票否决）：任何声称
       「已校准」「校准通过」「误差已验证」的表述须 FAIL —— 先红后绿
  H-9  「未校准」（VD-26 决策结果）与「校准失败」（测量结果）可分辨
  H-10 阈值有据：逐字取用基线 B §10A G6C-03 验收标准（resolved≥30 ·
       ≥2 报告期 · ≥2 horizon bucket · clustered effective_n≥20 · CI 存在），
       不另设阈值、不引入凭空的数字

VD-26（决策表）：预测取最低门；永久 CALIBRATION_PENDING，不声称已校准。
⇒ declared_status 恒为 CALIBRATION_PENDING；measurement_status 由测量
决定（H-9 的两个维度：决策 vs 测量）。
"""
import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

CALIBRATION_PENDING = "CALIBRATION_PENDING"
INSUFFICIENT_SAMPLE = "INSUFFICIENT_SAMPLE"
CALIBRATION_SUFFICIENT = "CALIBRATION_SUFFICIENT"

DECISION_VD26 = "DECISION_VD26"     # H-9：「未校准」的语义 = 决策结果
MEASUREMENT = "MEASUREMENT"         # H-9：「校准失败」的语义 = 测量结果

# H-8：冒充能力的表述（守卫扫描对象；H-8 的错误消息含 E-G6C-03 前缀，
# 被表述守卫按「拒绝语境」豁免 —— 见 backend/tools/calibration_claim_check.py）
CLAIM_PHRASES = ("已校准", "校准通过", "误差已验证")

# H-10：阈值逐字取用基线 B §10A（不另设）
GATE_THRESHOLDS = {
    "min_resolved": 30,
    "min_reporting_periods": 2,
    "min_horizon_buckets": 2,
    "min_clustered_effective_n": 20,
}


class CalibrationError(ValueError):
    pass


class CalibrationClaimDenied(CalibrationError):
    """H-8 一票否决：PENDING 不得冒充能力 —— 声称被拒。"""


# ════════════════════════════════════════════════════════════════
# Brier / skill / base rate
# ════════════════════════════════════════════════════════════════

def brier(forecast_prob: str, outcome: int) -> float:
    """Brier = (f − o)²，o ∈ {0, 1}。"""
    if outcome not in (0, 1):
        raise CalibrationError(f"E-G6C-03-001: 非法 outcome: {outcome!r}")
    f = float(forecast_prob)
    if not 0.0 <= f <= 1.0:
        raise CalibrationError(f"E-G6C-03-002: 概率越界: {f}")
    return (f - outcome) ** 2


def brier_score(forecast_probs: List[str], outcomes: List[int]) -> float:
    if len(forecast_probs) != len(outcomes):
        raise CalibrationError("E-G6C-03-003: 概率与结果数不一致")
    if not forecast_probs:
        raise CalibrationError("E-G6C-03-004: 空样本不可评 Brier（0 与 N 可分辨）")
    return sum(brier(f, o) for f, o in zip(forecast_probs, outcomes)) / len(
        forecast_probs)


def reference_brier(reference_probs: List[str], outcomes: List[int]) -> float:
    """reference Brier：以参考概率（base rate）为预测。"""
    return brier_score(reference_probs, outcomes)


def skill_score(b: float, rb: float) -> float:
    """skill = 1 − Brier/reference Brier。reference=0（完美基准）→ 未定义。"""
    if rb == 0:
        raise CalibrationError(
            "E-G6C-03-005: reference Brier=0 —— skill 未定义（零除）")
    return 1.0 - b / rb


def base_rate(outcomes: List[int]) -> float:
    if not outcomes:
        raise CalibrationError("E-G6C-03-004: 空样本无 base rate")
    return sum(outcomes) / len(outcomes)


# ════════════════════════════════════════════════════════════════
# 分层 / cluster-aware effective_n / CI
# ════════════════════════════════════════════════════════════════

STRATA_DIMENSIONS = ("scope", "horizon", "model", "prompt", "method")


def horizon_bucket(observation_period_start: str,
                   observation_period_end: str) -> str:
    """horizon bucket：按观察期长度粗分（<1 月 / 1—3 月 / >3 月）。"""
    import datetime
    s = datetime.datetime.fromisoformat(
        observation_period_start.replace("Z", "+00:00"))
    e = datetime.datetime.fromisoformat(observation_period_end.replace(
        "Z", "+00:00"))
    days = (e - s).days
    if days < 31:
        return "LT1M"
    if days <= 93:
        return "1-3M"
    return "GT3M"


def reporting_period(observation_period_end: str) -> str:
    """报告期：观察期末所在月份（≥2 个报告期的判据）。"""
    return observation_period_end[:7]


def stratified_scores(scoring_inputs: Dict[str, Tuple[dict, dict]],
                      dim: str) -> Dict[str, dict]:
    """按维度分层：每层内 Brier / reference Brier / skill / n。"""
    if dim not in STRATA_DIMENSIONS:
        raise CalibrationError(f"E-G6C-03-006: 未知分层维度: {dim}")
    strata: Dict[str, List] = {}
    for pred, adj in scoring_inputs.values():
        if dim == "scope":
            key = pred.get("scope", "?")
        elif dim == "horizon":
            key = horizon_bucket(pred["observation_period_start"],
                                 pred["observation_period_end"])
        else:
            key = pred.get(dim + "_version") or pred.get(dim) or "?"
        strata.setdefault(key, []).append((pred, adj))
    out: Dict[str, dict] = {}
    for key, pairs in strata.items():
        fs = [float(p["forecast_probability"]) for p, _ in pairs]
        rs = [float(p["reference_probability"]) for p, _ in pairs]
        os_ = [int(a["outcome"]) for _, a in pairs]
        b = brier_score([str(f) for f in fs], os_)
        rb = reference_brier([str(r) for r in rs], os_)
        out[key] = {"n": len(pairs), "brier": b, "reference_brier": rb,
                    "skill": skill_score(b, rb) if rb else None}
    return out


def cluster_effective_n(scoring_inputs: Dict[str, Tuple[dict, dict]]) -> float:
    """cluster-aware effective_n：簇 = (scope, horizon bucket)。

    保守调整：effective_n = n / 平均簇规模（同一簇内的预测共享时序与
    范围相关，不按独立样本计）。规则逐字写入本函数注释 —— 这是 H-10
    「阈值有据」的测量侧对应（阈值本身在 GATE_THRESHOLDS）。
    """
    clusters: Dict[Tuple[str, str], int] = {}
    for pred, _ in scoring_inputs.values():
        key = (pred.get("scope", "?"),
               horizon_bucket(pred["observation_period_start"],
                              pred["observation_period_end"]))
        clusters[key] = clusters.get(key, 0) + 1
    n = len(scoring_inputs)
    if n == 0:
        return 0.0
    mean_cluster = sum(clusters.values()) / len(clusters)
    return n / mean_cluster if mean_cluster else 0.0


def wilson_ci(outcomes: List[int], z: float = 1.96) -> Optional[dict]:
    """Wilson 置信区间（结果 0/1 比例）。样本为空 → 无 CI（None 可分辨）。"""
    n = len(outcomes)
    if n == 0:
        return None
    p = sum(outcomes) / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return {"low": centre - half, "high": centre + half, "n": n}


# ════════════════════════════════════════════════════════════════
# 充分性门（H-7）与机器可读状态（H-8 / H-9 / H-10）
# ════════════════════════════════════════════════════════════════

@dataclass
class CalibrationStatus:
    declared_status: str                # VD-26 终态：恒 CALIBRATION_PENDING
    measurement_status: str             # 测量结果：SUFFICIENT / INSUFFICIENT_SAMPLE
    gate: Dict[str, bool] = field(default_factory=dict)
    gate_detail: Dict[str, object] = field(default_factory=dict)
    resolved: int = 0
    reporting_periods: int = 0
    horizon_buckets: int = 0
    effective_n: float = 0.0
    ci: Optional[dict] = None
    selective_unresolved: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"declared_status": self.declared_status,
                "measurement_status": self.measurement_status,
                "gate": self.gate, "gate_detail": self.gate_detail,
                "resolved": self.resolved,
                "reporting_periods": self.reporting_periods,
                "horizon_buckets": self.horizon_buckets,
                "effective_n": self.effective_n, "ci": self.ci,
                "selective_unresolved": self.selective_unresolved}


def check_sufficiency(scoring_inputs: Dict[str, Tuple[dict, dict]],
                      selective_unresolved: List[str]) -> CalibrationStatus:
    """充分性门（H-7 / H-10）：阈值逐字取用基线 B §10A。

    任一判据不满足 → measurement_status = INSUFFICIENT_SAMPLE，
    **阻断**「已校准」表述（不是附警告输出）。样本 0 与样本充足
    可分辨（resolved 计数独立报出，规则 ⑨）。
    """
    resolved = len(scoring_inputs)
    periods = len({reporting_period(p["observation_period_end"])
                   for p, _ in scoring_inputs.values()})
    buckets = len({horizon_bucket(p["observation_period_start"],
                                  p["observation_period_end"])
                   for p, _ in scoring_inputs.values()})
    eff_n = cluster_effective_n(scoring_inputs)
    outcomes = [int(a["outcome"]) for _, a in scoring_inputs.values()]
    ci = wilson_ci(outcomes)
    gate = {
        "resolved>=30": resolved >= GATE_THRESHOLDS["min_resolved"],
        ">=2 报告期": periods >= GATE_THRESHOLDS["min_reporting_periods"],
        ">=2 horizon bucket": buckets >= GATE_THRESHOLDS["min_horizon_buckets"],
        "clustered effective_n>=20":
            eff_n >= GATE_THRESHOLDS["min_clustered_effective_n"],
        "CI 存在": ci is not None,
        "无材料性选择性未决": not selective_unresolved,
    }
    sufficient = all(gate.values())
    return CalibrationStatus(
        declared_status=CALIBRATION_PENDING,          # VD-26：恒为终态
        measurement_status=(CALIBRATION_SUFFICIENT if sufficient
                            else INSUFFICIENT_SAMPLE),
        gate=gate,
        gate_detail={"resolved": resolved, "reporting_periods": periods,
                     "horizon_buckets": buckets, "effective_n": eff_n,
                     "ci": ci,
                     "selective_unresolved": selective_unresolved,
                     "thresholds": dict(GATE_THRESHOLDS)},
        resolved=resolved, reporting_periods=periods,
        horizon_buckets=buckets, effective_n=eff_n, ci=ci,
        selective_unresolved=list(selective_unresolved),
    )


def render_for_display(status: CalibrationStatus) -> str:
    """展示政策（H-8）：declared 非 SUFFICIENT（VD-26 下恒如此）时
    只输出 CALIBRATION_PENDING 文本；任何把 PENDING 渲染成能力的
    表述都被本函数拒绝 —— 声称文案不得含 CLAIM_PHRASES。"""
    if status.declared_status == CALIBRATION_SUFFICIENT:
        raise CalibrationClaimDenied(
            "E-G6C-03-101: VD-26 最低门下 declared_status 恒为 "
            "CALIBRATION_PENDING —— 本状态不可达；不得声称已校准")
    return (
        "校准状态：CALIBRATION_PENDING（VD-26 最低门：永久不作出校准能力声明）。"
        f"测量：resolved={status.resolved}（需≥30）、报告期="
        f"{status.reporting_periods}（需≥2）、horizon bucket="
        f"{status.horizon_buckets}（需≥2）、clustered effective_n="
        f"{status.effective_n:.1f}（需≥20）、CI={'存在' if status.ci else '无'}、"
        f"选择性未决={len(status.selective_unresolved)} 项"
        f"（measurement_status={status.measurement_status}）")


def assert_no_calibration_claim(text: str, where: str = "") -> None:
    """H-8：任何声称「已校准」「校准通过」「误差已验证」的表述 → FAIL。

    供产出路径（导出/UI/README）调用；也供仓库表述守卫作行为断言。
    """
    for ph in CLAIM_PHRASES:
        if ph in text:
            raise CalibrationClaimDenied(
                f"E-G6C-03-102: {where}出现冒充能力表述「{ph}」—— "
                f"CALIBRATION_PENDING 状态不得作出校准能力声明"
                f"（VD-26，一票否决）")
