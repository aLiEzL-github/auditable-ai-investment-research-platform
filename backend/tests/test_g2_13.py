"""G2-13 验收测试：材料性手工录入双录复核。

基线：
  · 同一自然人自录自审被系统拒绝
  · 第二复核人缺失时保持 REVIEW_REQUIRED / PARTIAL
交付：双录、差异处理、来源 locator、两次独立签署、不可变录入事件。
"""
import unittest
import tempfile
import shutil
import os
import sys

APP = os.path.join(os.path.dirname(__file__), "..", "app")
sys.path.insert(0, APP)

from dual_entry import DualEntryService, DualEntryError  # noqa: E402
from repository import create_repository, ManualEntry  # noqa: E402


class TestDualEntry(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self.repo = create_repository(os.path.join(self._tmp, "g2_13.sqlite3"))
        self.repo.create_all()
        self.s = self.repo.session()
        # 单人项目（VD-02 = 1 名自然人）
        self.svc = DualEntryService(self.s, reviewer_set={"U"})

    def tearDown(self):
        self.s.close()
        self.repo.engine.dispose()
        shutil.rmtree(self._tmp, ignore_errors=True)

    # ── 同一自然人自录自审被系统拒绝 ────────────────────────────────
    def test_self_review_rejected_single_person(self):
        self.svc.enter("ENT_001", "REVENUE", "100", "LOC/A", "U")
        with self.assertRaises(DualEntryError) as ctx:
            self.svc.verify("ENT_001", "REVENUE", "100", "LOC/A", "U")
        self.assertIn("E-G2-13-002", str(ctx.exception))
        # 保持 REVIEW_REQUIRED / PARTIAL（不产生 VERIFIED 状态）
        self.assertEqual(self.s.query(ManualEntry).count(), 1,
                         "复核失败不得产生第二条录入")

    # ── 第二复核人缺失 → REVIEW_REQUIRED / PARTIAL ──────────────────
    def test_missing_reviewer_keeps_partial(self):
        self.svc.enter("ENT_002", "EPS", "2.5", "LOC/B", "U")
        try:
            self.svc.verify("ENT_002", "EPS", "2.5", "LOC/B", "U")
            self.fail("应拒绝")
        except DualEntryError as e:
            self.assertIn("REVIEW_REQUIRED", str(e))
        # 状态传播：无 VERIFIED → 材料性录入保持 PARTIAL
        self.assertEqual(self.s.query(ManualEntry).filter_by(id="ENT_002").count(), 1)

    # ── 多自然人环境：独立签署通过 ──────────────────────────────────
    def test_two_persons_verified(self):
        svc = DualEntryService(self.s, reviewer_set={"U", "HR"})
        svc.enter("ENT_003", "REVENUE", "100", "LOC/C", "U")
        out = svc.verify("ENT_003", "REVENUE", "100", "LOC/C", "HR")
        self.assertEqual(out["status"], "VERIFIED")
        self.assertEqual(self.s.query(ManualEntry).count(), 2)

    # ── 差异处理 ────────────────────────────────────────────────────
    def test_difference_review_required(self):
        svc = DualEntryService(self.s, reviewer_set={"U", "HR"})
        svc.enter("ENT_004", "REVENUE", "100", "LOC/D", "U")
        out = svc.verify("ENT_004", "REVENUE", "105", "LOC/D", "HR")
        self.assertEqual(out["status"], "DIFF_REVIEW_REQUIRED")

    # ── 不可变录入事件：内容哈希绑定 + 无 update 路径 ───────────────
    def test_immutable_entry(self):
        self.svc.enter("ENT_005", "REVENUE", "100", "LOC/E", "U")
        entry = self.s.query(ManualEntry).filter_by(id="ENT_005").first()
        self.assertTrue(entry.record_hash)
        self.assertEqual(len(entry.record_hash), 64)
        # 无 update API（记录不可改写）
        self.assertFalse(hasattr(self.svc, "update"))

    # ── 来源 locator ────────────────────────────────────────────────
    def test_locator_preserved(self):
        self.svc.enter("ENT_006", "REVENUE", "100", "LOC/600089/2026H1/p25", "U")
        entry = self.s.query(ManualEntry).filter_by(id="ENT_006").first()
        self.assertEqual(entry.locator, "LOC/600089/2026H1/p25")


if __name__ == "__main__":
    unittest.main()
