"""dual_entry.py —— G2-13 材料性手工录入双录复核。

基线验收（G2-13）：
  · 同一自然人自录自审被系统拒绝
  · 第二复核人缺失时保持 REVIEW_REQUIRED / PARTIAL
交付：双录、差异处理、来源 locator、两次独立签署和不可变录入事件。
"""
import hashlib
from datetime import datetime, timezone

from repository import ManualEntry  # noqa: F401


class DualEntryError(ValueError):
    pass


class DualEntryService:
    def __init__(self, session, reviewer_set=None):
        """reviewer_set：可参与复核的自然人集合（单人项目 = {U}）。"""
        self.s = session
        self.reviewer_set = reviewer_set or {"U"}

    # ── 录入：双录流程 ──────────────────────────────────────────────
    def enter(self, entry_id: str, field_key: str, value: str,
              locator: str, entered_by: str) -> ManualEntry:
        """第一次录入（entry A）。"""
        return self._write(entry_id, field_key, value, locator, entered_by)

    def verify(self, entry_id: str, field_key: str, value: str,
               locator: str, entered_by: str) -> dict:
        """第二次录入（entry B，复核签署）。

        同一自然人自录自审 → 拒绝（E-G2-13-001）。
        第二复核人缺失（单人环境）→ 保持 REVIEW_REQUIRED / PARTIAL（E-G2-13-002）。
        """
        a = self.s.query(ManualEntry).filter_by(id=entry_id).first()
        if a is None:
            raise DualEntryError(f"E-G2-13-003: 待复核录入不存在: {entry_id}")
        if a.entered_by == entered_by:
            # 同一自然人自录自审被系统拒绝
            if len(self.reviewer_set) <= 1:
                # 无第二复核人：保持 REVIEW_REQUIRED / PARTIAL（诚实，不假装双人）
                raise DualEntryError(
                    "E-G2-13-002: 无第二复核人 —— 保持 REVIEW_REQUIRED / PARTIAL"
                    f"（自录自审拒绝: {entered_by} == {a.entered_by}）")
            raise DualEntryError(
                f"E-G2-13-001: 同一自然人自录自审被拒绝: {entered_by}")
        # 第二人存在（多自然环境）：独立签署
        b = self._write(f"{entry_id}:B", field_key, value, locator, entered_by)
        if value != a.value:
            return {"status": "DIFF_REVIEW_REQUIRED", "entry_a": a.value,
                    "entry_b": b.value}
        return {"status": "VERIFIED", "entry_a": a, "entry_b": b}

    def _write(self, entry_id: str, field_key: str, value: str,
               locator: str, entered_by: str) -> ManualEntry:
        # 不可变录入事件：record_hash 绑定内容（无 update 路径）
        rec = f"{entry_id}|{field_key}|{value}|{locator}|{entered_by}"
        rec_hash = hashlib.sha256(rec.encode("utf-8")).hexdigest()
        entry = ManualEntry(id=entry_id, schema_version="1.0",
                            field_key=field_key, value=value, locator=locator,
                            entered_by=entered_by,
                            signed_at=datetime.now(timezone.utc),
                            record_hash=rec_hash, version=1)
        self.s.add(entry)
        self.s.commit()
        return entry
