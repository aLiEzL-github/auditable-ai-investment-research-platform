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


def _norm_person(s) -> str:
    """自然人标识归一：去首尾空白 + 折叠大小写。

    OI-PF-179：原实现的自录自审判定是**字面比较** `a.entered_by == entered_by`，
    故 'U' 录入、'u' 复核**不会被识别为同一人** —— 而 G2-13 的全部价值
    就在于「同一自然人不能自录自审」。
    """
    return str(s or "").strip().casefold()


class DualEntryService:
    def __init__(self, session, reviewer_set=None):
        """reviewer_set：可参与复核的自然人集合（单人项目 = {U}）。"""
        self.s = session
        # **`or` 会把空集合替换成默认值** —— `set() or {"U"}` → `{"U"}`，
        # 于是「reviewer_set 为空」这一 fail-closed 分支**永远走不到**。
        # 须用 `is None` 区分「未传」与「传了空集合」：前者取默认，后者原样保留。
        # （2026-08-13 实测：本函数的 fail-closed 用例因此失败一次。）
        self.reviewer_set = {"U"} if reviewer_set is None else set(reviewer_set)
        self._norm_reviewers = frozenset(
            _norm_person(x) for x in self.reviewer_set if _norm_person(x))

    def _assert_known_person(self, who: str, role: str) -> str:
        """**默认拒绝**：不在 reviewer_set 内的标识一律不得参与双录。

        OI-PF-179：原实现**从不校验成员资格** —— `reviewer_set` 在源码中
        出现 5 次，除定义与文档串外只用于 `if len(self.reviewer_set) <= 1`，
        即它只判断「有没有第二人」，从不判断「这个人是不是第二人」。
        实测：'U' 录入后以 'U2' / 'u' / 'U ' / 'AGENT' / 'Codex' / ''（空串）
        六种身份复核，**全部返回 VERIFIED**。

        其中 'Codex' 通过尤其直接违反 VD-02「AI 辅助不计入自然人数（A §6.1）」。

        **同一形状的第四例**（前三：CLIENT_SUPPLIED_VERDICT_KEYS · SERVER_ALLOWLIST
        死豁免 · _assert_approver），但本处最彻底 —— 前三例至少有一份清单
        参与判定，本处的清单只被用来数个数。
        """
        n = _norm_person(who)
        if not n:
            raise DualEntryError(
                f"E-G2-13-004: {role} 标识为空 —— 双录须具名自然人")
        if not self._norm_reviewers:
            raise DualEntryError(
                "E-G2-13-005: reviewer_set 为空 —— **不得默认放行**（fail-closed）")
        if n not in self._norm_reviewers:
            raise DualEntryError(
                f"E-G2-13-006: {role} {who!r} 不在 reviewer_set 内 —— "
                f"默认拒绝（VD-02：AI 辅助不计入自然人数）")
        return n

    # ── 录入：双录流程 ──────────────────────────────────────────────
    def enter(self, entry_id: str, field_key: str, value: str,
              locator: str, entered_by: str) -> ManualEntry:
        """第一次录入（entry A）。

        **录入侧同样校验** —— 否则以 'Codex' 录入、'U' 复核，
        字面不等即判 VERIFIED，双录形式齐备而实质是单人加一个不存在的人。
        """
        self._assert_known_person(entered_by, "录入人")
        return self._write(entry_id, field_key, value, locator, entered_by)

    def verify(self, entry_id: str, field_key: str, value: str,
               locator: str, entered_by: str) -> dict:
        """第二次录入（entry B，复核签署）。

        同一自然人自录自审 → 拒绝（E-G2-13-001）。
        第二复核人缺失（单人环境）→ 保持 REVIEW_REQUIRED / PARTIAL（E-G2-13-002）。
        """
        n_b = self._assert_known_person(entered_by, "复核人")
        a = self.s.query(ManualEntry).filter_by(id=entry_id).first()
        if a is None:
            raise DualEntryError(f"E-G2-13-003: 待复核录入不存在: {entry_id}")
        # **归一后比较** —— 否则 'U' 与 'u' 会被当成两个人（OI-PF-179）
        if _norm_person(a.entered_by) == n_b:
            if len(self._norm_reviewers) <= 1:
                raise DualEntryError(
                    "E-G2-13-002: 无第二复核人 —— 保持 REVIEW_REQUIRED / PARTIAL"
                    f"（自录自审拒绝: {entered_by} == {a.entered_by}）")
            raise DualEntryError(
                f"E-G2-13-001: 同一自然人自录自审被拒绝: {entered_by}")
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
