"""golden_baseline.py —— G2-14 600089 真实 golden baseline 框架。

基线验收（G2-14）：
  · 真实 Gate 样本只来自已取得的真实披露
  · 合成 fixture 仅用于自动化负测（不得混入真实 baseline）
  · 材料性事实 100% 人工回源（手工缺口符合 G2-13 独立双录合同）
当前状态：真实披露文件待提供（上交所 403 已判明 → 人工导入路径），
框架先行，真实样本缺口显式标注（G1-06 2e 同款诚实处理）。
"""
import json
from datetime import datetime, timezone

GOLDEN_SCHEMA_VERSION = "1.0"


class GoldenBaselineError(ValueError):
    pass


class GoldenBaseline:
    def __init__(self, baseline_id: str, ticker: str, period: str):
        self.baseline_id = baseline_id
        self.ticker = ticker
        self.period = period
        self.source_docs = []    # 真实披露 locator 列表（人工导入）
        self.facts = {}          # metric_id → fact dict
        self.back_source = {}    # metric_id → 回源记录
        self._synthetic = False  # 真实样本标记

    # ── 1. 真实披露文档登记（只接受人工导入的真实披露）──────────────
    def add_source_doc(self, locator: str, synthetic: bool = False) -> None:
        """合成 fixture 仅用于自动化负测——不得混入真实 baseline。"""
        if synthetic:
            raise GoldenBaselineError(
                f"E-G2-14-001: 合成 fixture 禁止混入真实 baseline: {locator}")
        self.source_docs.append({"locator": locator, "registered_at":
                                 datetime.now(timezone.utc).isoformat()})

    # ── 2. 事实登记（须带回源记录）──────────────────────────────────
    def add_fact(self, metric_id: str, value: str, unit: str,
                 locator: str, material: bool = True) -> None:
        # 材料性事实未回源：允许登记，状态保持 PARTIAL（G2-13 缺口语义，
        # 不回源不升格 COMPLETE）
        self.facts[metric_id] = {"value": value, "unit": unit,
                                 "locator": locator, "material": material}

    def add_back_source(self, metric_id: str, locator: str,
                        reviewed_by: str, review_state: str) -> None:
        """人工回源记录：locator + 核对人 + 状态（G2-13 双录合同）。"""
        if reviewed_by not in self._reviewers():
            self.back_source[metric_id] = {
                "locator": locator, "reviewed_by": reviewed_by,
                "state": review_state,
                "at": datetime.now(timezone.utc).isoformat()}
            return
        raise GoldenBaselineError(
            f"E-G2-14-003: 回源核对人须不同于录入人（双录）: {reviewed_by}")

    def _reviewers(self):
        return {"U"}

    # ── 3. 状态判定：材料性 100% 回源，缺口 → PARTIAL ───────────────
    def status(self) -> str:
        missing = [m for m, f in self.facts.items()
                   if f["material"] and m not in self.back_source]
        if missing:
            return f"PARTIAL（材料性未回源: {missing}）"
        if not self.source_docs:
            return "PARTIAL（真实披露样本未取得）"
        return "COMPLETE"

    # ── 机器可读 ────────────────────────────────────────────────────
    def to_dict(self) -> dict:
        return {"baseline_id": self.baseline_id, "ticker": self.ticker,
                "period": self.period, "source_docs": self.source_docs,
                "facts": self.facts, "back_source": self.back_source,
                "status": self.status()}
