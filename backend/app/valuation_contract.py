"""valuation_contract.py —— G2-15 估值与行业输入的独立来源合同。

基线验收（G2-15）：
  · 每个估值输入有 source_role/rights/as_of/period/basis/locator
  · 同源镜像不算独立证据
  · 主源或权利不足保持 MISSING/PARTIAL，不得静默用聚合器或手工值升格
输入五类：价格、时点股本、净债务、少数股东权益、行业/商品数据。
"""
import json
from datetime import datetime, timezone

INPUTS = ("price", "shares_outstanding", "net_debt",
          "minority_interest", "industry_commodity")


class ValuationContractError(ValueError):
    pass


class ValuationInput:
    def __init__(self, input_key: str):
        self.input_key = input_key
        self.sources = []   # 独立来源登记（source_role/rights/as_of/period/basis/locator）
        self._gaps = []

    # ── 独立来源登记 ────────────────────────────────────────────────
    def add_source(self, source_id: str, source_role: str, rights: str,
                   as_of: str, period: str, basis: str, locator: str) -> None:
        if source_role not in ("PRIMARY", "SECONDARY"):
            raise ValuationContractError(
                f"E-G2-15-001: 非法 source_role: {source_role}")
        # 同源镜像不算独立证据：同一 source_id 已有登记 → 拒绝（镜像）
        if any(s["source_id"] == source_id for s in self.sources):
            raise ValuationContractError(
                f"E-G2-15-002: 同源镜像不算独立证据: {source_id}")
        self.sources.append({
            "source_id": source_id, "source_role": source_role,
            "rights": rights, "as_of": as_of, "period": period,
            "basis": basis, "locator": locator,
            "registered_at": datetime.now(timezone.utc).isoformat()})

    # ── 状态判定：主源或权利不足 → MISSING/PARTIAL（不升格）─────────
    def status(self) -> str:
        if not self.sources:
            return "MISSING"
        primaries = [s for s in self.sources if s["source_role"] == "PRIMARY"]
        if not primaries:
            return "PARTIAL（无主源；副源/手工值不得静默升格）"
        if any(s["rights"] != "ALLOWED" for s in primaries):
            return "PARTIAL（主源权利不足）"
        if any(not s["as_of"] for s in primaries):
            return "PARTIAL（时点缺失）"
        return "READY"

    def to_dict(self) -> dict:
        return {"input_key": self.input_key, "sources": self.sources,
                "status": self.status()}


class ValuationContract:
    def __init__(self):
        self.inputs = {k: ValuationInput(k) for k in INPUTS}

    def register(self, input_key: str, **kw) -> None:
        if input_key not in self.inputs:
            raise ValuationContractError(f"E-G2-15-003: 未知输入: {input_key}")
        self.inputs[input_key].add_source(**kw)

    def summary(self) -> dict:
        return {k: v.status() for k, v in self.inputs.items()}

    def to_dict(self) -> dict:
        return {"inputs": {k: v.to_dict() for k, v in self.inputs.items()},
                "summary": self.summary()}
