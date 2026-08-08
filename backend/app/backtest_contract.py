"""backtest_contract.py —— G2-09 backtest_mode 市场数据合同。

基线验收（G2-09）：
  · 三种模式（QUALIFIED / EXPERIMENT_ONLY / REMOVED）均有机器可读状态
  · 只有 QUALIFIED 真实冒烟并允许后续绩效门
G0-09 已裁：六类回测必需数据全无来源/权利 → 当前 backtest_mode = REMOVED
"""
import json
from dataclasses import dataclass, field
from typing import Dict, List

MODES = ("QUALIFIED", "EXPERIMENT_ONLY", "REMOVED")
REQUIRED_DATA = (
    "daily_bars",        # 日线行情
    "adjustment_factor",  # 复权因子
    "corporate_actions",  # 公司行动
    "trading_calendar",   # 交易日历
    "index_benchmark",    # 指数基准
    "pit_universe",       # PIT 标的池
)


@dataclass
class BacktestContract:
    """六类数据逐类来源/权利状态 + 模式判定（机器可读）。"""

    data_status: Dict[str, str] = field(default_factory=dict)
    mode: str = "REMOVED"

    def __post_init__(self):
        for k in REQUIRED_DATA:
            self.data_status.setdefault(k, "UNAVAILABLE")

    # ── 机器可读状态 ────────────────────────────────────────────────
    def to_dict(self) -> dict:
        return {"mode": self.mode,
                "data_status": dict(self.data_status),
                "qualified": self.is_qualified()}

    def is_qualified(self) -> bool:
        """QUALIFIED 判定：六类全部 AVAILABLE 且权利就绪（fail-closed）。"""
        return all(v == "AVAILABLE" for v in self.data_status.values())

    # ── 模式选择校验（来源/权利不足不得选 QUALIFIED）────────────────
    def select_mode(self, requested: str) -> str:
        if requested not in MODES:
            raise ValueError(f"E-G2-09-001: 非法模式 {requested}（须 QUALIFIED/EXPERIMENT_ONLY/REMOVED）")
        if requested == "QUALIFIED" and not self.is_qualified():
            missing = [k for k, v in self.data_status.items() if v != "AVAILABLE"]
            raise ValueError(
                f"E-G2-09-002: 来源/权利不足不得选 QUALIFIED，缺: {missing}")
        self.mode = requested
        return self.mode

    # ── 绩效门：仅 QUALIFIED 允许 ───────────────────────────────────
    def check_performance_gate(self) -> None:
        """后续绩效门：非 QUALIFIED 一律拒绝（基线：只有 QUALIFIED 允许）。"""
        if self.mode != "QUALIFIED":
            raise ValueError(
                f"E-G2-09-003: 绩效门拒绝 —— backtest_mode={self.mode} 非 QUALIFIED")


def current_contract() -> BacktestContract:
    """当前合同（G0-09 已裁：六类全缺 → REMOVED）。"""
    return BacktestContract(mode="REMOVED")
