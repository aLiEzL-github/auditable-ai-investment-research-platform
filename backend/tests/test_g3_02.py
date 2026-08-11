"""G3-02 验收测试：研究路由和运行状态机。

基线：
  1. workflow、scope/run/version ID 齐备且合法
  2. 运行唯一（同一 workflow/scope 下同时只有一个活动运行）
  3. 禁止直接跳到 RELEASED（唯一入口 CANDIDATE→RELEASED 且须走 release()）

变异注入（先红后绿）：
  · 删除迁移表条目 → transition 应被测试抓到
  · release() 从任何状态放行 → 应被测试抓到
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
APP = os.path.join(os.path.dirname(__file__), "..", "app")
sys.path.insert(0, APP)

from research_router import (  # noqa: E402
    ResearchRouter, ResearchRun, DRAFT, RUNNING, CANDIDATE, RELEASED,
    PARTIAL, BLOCKED, FAILED, LEGAL_TRANSITIONS, WORKFLOWS,
    make_run_id, validate_workflow_scope,
)


class TestIds(unittest.TestCase):
    def test_ids_generated(self):
        rid = make_run_id("2026-08-11T06:00:00Z", "abc123")
        self.assertRegex(rid, r"^run-[0-9A-Za-z\-]+-[0-9A-Za-z\-]+$")
        self.assertTrue(rid.startswith("run-20260811"))

    def test_workflow_whitelist(self):
        for w in WORKFLOWS:
            validate_workflow_scope(w, "600089.SH")
        with self.assertRaises(ValueError) as ctx:
            validate_workflow_scope("other-workflow", "x")
        self.assertIn("E-G3-02-004", str(ctx.exception))
        with self.assertRaises(ValueError) as ctx:
            validate_workflow_scope("a-share-single-company-research", "../evil")
        self.assertIn("E-G3-02-005", str(ctx.exception))


class TestRunUnique(unittest.TestCase):
    def setUp(self):
        self.r = ResearchRouter()

    def test_two_active_runs_rejected(self):
        self.r.create_run("a-share-single-company-research", "600089.SH",
                          "run-1", "v1")
        with self.assertRaises(ValueError) as ctx:
            self.r.create_run("a-share-single-company-research", "600089.SH",
                              "run-2", "v1")
        self.assertIn("E-G3-02-001", str(ctx.exception))

    def test_duplicate_run_id_rejected(self):
        self.r.create_run("a-share-single-company-research", "600089.SH",
                          "run-1", "v1")
        with self.assertRaises(ValueError) as ctx:
            self.r.create_run("system-design-plan", "p", "run-1", "v1")
        self.assertIn("E-G3-02-002", str(ctx.exception))

    def test_same_scope_different_workflow_allowed(self):
        """分域（D-4）：不同 workflow 的相同 scope 互不占用唯一性。"""
        self.r.create_run("a-share-single-company-research", "600089.SH",
                          "run-1", "v1")
        self.r.create_run("system-design-plan", "600089.SH", "run-2", "v1")
        self.assertEqual(len(self.r._runs), 2)

    def test_terminal_run_releases_uniqueness(self):
        r = self.r.create_run("a-share-single-company-research", "600089.SH",
                              "run-1", "v1")
        self.r.transition(r.run_id, BLOCKED)
        self.r.create_run("a-share-single-company-research", "600089.SH",
                          "run-2", "v1")  # 终态不再占用唯一性


class TestTransitions(unittest.TestCase):
    def setUp(self):
        self.r = ResearchRouter()

    def test_illegal_transition_rejected(self):
        run = self.r.create_run("a-share-single-company-research", "600089.SH",
                                "run-1", "v1")
        for bad in ("RELEASED", "FAILED", "CANDIDATE"):
            with self.assertRaises(ValueError) as ctx:
                self.r.transition(run.run_id, bad)
            self.assertIn("E-STATE-001", str(ctx.exception))
        self.assertEqual(run.state, DRAFT)

    def test_legal_path(self):
        run = self.r.create_run("a-share-single-company-research", "600089.SH",
                                "run-1", "v1")
        self.r.transition(run.run_id, RUNNING)
        self.r.transition(run.run_id, CANDIDATE)
        self.assertEqual(run.state, CANDIDATE)
        # 从 CANDIDATE 可 FAILED（闭合失败）或 RELEASED（走 release()）
        self.r.transition(run.run_id, FAILED)
        self.assertEqual(run.state, FAILED)

    def test_terminal_no_transition(self):
        run = self.r.create_run("a-share-single-company-research", "600089.SH",
                                "run-1", "v1")
        self.r.transition(run.run_id, BLOCKED)
        for to in (RUNNING, CANDIDATE, FAILED):
            with self.assertRaises(ValueError) as ctx:
                self.r.transition(run.run_id, to)
            self.assertIn("E-STATE-001", str(ctx.exception))

    def test_unknown_state_rejected(self):
        run = self.r.create_run("a-share-single-company-research", "600089.SH",
                                "run-1", "v1")
        with self.assertRaises(ValueError) as ctx:
            self.r.transition(run.run_id, "BOGUS")
        self.assertIn("E-G3-02-008", str(ctx.exception))


class TestNoDirectJumpToReleased(unittest.TestCase):
    """禁止直接跳到 RELEASED —— 一票否决语义。"""

    def test_no_quick_path_in_transition_table(self):
        """迁移表中 RELEASED 只能从 CANDIDATE 到达，且只经 release()。"""
        for src, targets in LEGAL_TRANSITIONS.items():
            if RELEASED in targets:
                self.assertEqual(src, CANDIDATE,
                                 f"RELEASED 只能由 CANDIDATE 到达，但 {src} 可达")

    def test_direct_transition_to_released_rejected(self):
        run = self.r_create()
        with self.assertRaises(ValueError) as ctx:
            self._router.transition(run.run_id, RELEASED)
        self.assertIn("E-STATE-001", str(ctx.exception))

    def test_release_only_after_candidate(self):
        router = ResearchRouter()
        run = router.create_run("a-share-single-company-research", "600089.SH",
                                "run-1", "v1")
        with self.assertRaises(ValueError):
            router.release(run.run_id)  # DRAFT 不可 release
        router.transition(run.run_id, RUNNING)
        with self.assertRaises(ValueError):
            router.release(run.run_id)  # RUNNING 不可 release
        router.transition(run.run_id, CANDIDATE)
        router.release(run.run_id)      # 唯一合法路径
        self.assertEqual(run.state, RELEASED)

    def r_create(self):
        if not hasattr(self, "_router"):
            self._router = ResearchRouter()
        return self._router.create_run("a-share-single-company-research",
                                       "600089.SH", "run-1", "v1")


if __name__ == "__main__":
    unittest.main()
