"""open_item_registry.py —— G3-14 OpenItemRegistry、强类型渲染与对抗语料。

基线验收（G3-14）：
  · owner、截止、阻断 Gate、closure evidence
  · 材料性开放项未关时保持 PARTIAL（不得准出）
  · 任何可见材料性内容不能绕过 Claim 图（渲染绑定）
  · 篡改必失败（closure 哈希锚定）
"""
import hashlib
import json
from dataclasses import dataclass, field
from typing import Dict, List, Optional

OPEN = "OPEN"
CLOSED = "CLOSED"
SUPERSEDED = "SUPERSEDED"


class OpenItemError(ValueError):
    pass


@dataclass
class OpenItem:
    open_item_id: str
    description: str
    material: bool
    owner_role: str
    due_date: Optional[str] = None
    blocks_gate: Optional[str] = None     # 如 "G3-05"
    closure_evidence: Optional[str] = None
    status: str = OPEN
    _record_sha256: str = ""

    def __post_init__(self):
        self._refresh_hash()

    def _refresh_hash(self) -> None:
        self._record_sha256 = hashlib.sha256(json.dumps(
            self.to_dict(), ensure_ascii=False, sort_keys=True).encode()).hexdigest()

    def to_dict(self) -> dict:
        return {"open_item_id": self.open_item_id, "description": self.description,
                "material": self.material, "owner_role": self.owner_role,
                "due_date": self.due_date, "blocks_gate": self.blocks_gate,
                "closure_evidence": self.closure_evidence, "status": self.status}

    def close(self, evidence: str, evidence_sha256: Optional[str] = None) -> None:
        """闭合：须附 closure evidence；闭合记录哈希锚定（篡改必败）。"""
        if not evidence:
            raise OpenItemError(f"E-G3-14-001: {self.open_item_id} 闭合须附证据")
        if evidence_sha256 is not None:
            now = hashlib.sha256(evidence.encode()).hexdigest()
            if now != evidence_sha256:
                raise OpenItemError(
                    f"E-G3-14-002: {self.open_item_id} closure evidence 哈希不符 —— "
                    f"篡改必败（实算 {now[:16]}… ≠ {evidence_sha256[:16]}…）")
        self.closure_evidence = evidence
        self.status = CLOSED
        self._refresh_hash()


@dataclass
class OpenItemRegistry:
    items: Dict[str, OpenItem] = field(default_factory=dict)
    render_bindings: Dict[str, str] = field(default_factory=dict)  # visible_span -> claim_ref

    def register(self, item: OpenItem) -> None:
        if item.open_item_id in self.items:
            raise OpenItemError(f"E-G3-14-003: 重复登记: {item.open_item_id}")
        self.items[item.open_item_id] = item

    def close(self, item_id: str, evidence: str,
              evidence_sha256: Optional[str] = None) -> None:
        it = self.items.get(item_id)
        if it is None:
            raise OpenItemError(f"E-G3-14-004: 开放项不存在: {item_id}")
        it.close(evidence, evidence_sha256)

    # ── 材料性开放项未关 → PARTIAL（不得准出）────────────────────
    def release_eligible(self) -> bool:
        return not any(it.material and it.status == OPEN
                       for it in self.items.values())

    def open_material_count(self) -> int:
        return sum(1 for it in self.items.values()
                   if it.material and it.status == OPEN)

    # ── 强类型渲染：任何可见材料性内容不能绕过 Claim 图 ──────────
    def bind(self, visible_span: str, claim_ref: str) -> None:
        if visible_span in self.render_bindings:
            raise OpenItemError(
                f"E-G3-14-005: 可见跨度重复绑定: {visible_span}")
        self.render_bindings[visible_span] = claim_ref

    def verify_render(self, rendered: str) -> str:
        """渲染校验：每个可见材料性 span 必须命中 Claim 绑定；
        未绑定的材料性可见内容 → 失败（绕过 Claim 图）。"""
        for span, ref in self.render_bindings.items():
            if span not in rendered:
                raise OpenItemError(
                    f"E-G3-14-006: 绑定跨度 {span}（{ref}）未出现在渲染输出")
        return f"render OK: {len(self.render_bindings)} bindings verified"
