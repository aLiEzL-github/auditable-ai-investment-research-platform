"""G3-08 验收测试：600089 纵向初步候选 fixture。

基线：
  · 全流程可复现（两次运行字节一致）
  · 任一适用规则非 PASS 或材料性开放项未关 → PARTIAL（不得转 eligible）
  · 只生成 candidate，不写 release / current（Gate 3 退出条件第四条）
"""
import json
import os
import subprocess
import sys
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOL = os.path.join(REPO, "tools", "vertical_candidate_g3_08.py")
PORTFOLIO = os.path.join(REPO, "..", "..", "portfolio")


class TestVerticalCandidate(unittest.TestCase):
    def _run(self):
        r = subprocess.run([sys.executable, TOOL],
                           capture_output=True, text=True, cwd=REPO)
        return r

    def test_runs_and_produces_candidate(self):
        r = self._run()
        self.assertEqual(r.returncode, 0, r.stderr)
        d = json.loads(r.stdout)
        self.assertEqual(d["scope"], "600089")
        self.assertEqual(d["workflow"], "a-share-single-company-research")
        self.assertIn("candidate_id", d)

    def test_reproducible_bytes(self):
        r1 = self._run()
        r2 = self._run()
        self.assertEqual(r1.stdout, r2.stdout, "两次运行字节必须一致（可复现）")

    def test_preserves_partial_with_open_material(self):
        """材料性开放项未关 → PARTIAL_NOT_RELEASE_ELIGIBLE。"""
        d = json.loads(self._run().stdout)
        self.assertEqual(d["status"], "PARTIAL_NOT_RELEASE_ELIGIBLE")
        self.assertFalse(d["open_items"]["eligible"])
        self.assertGreaterEqual(d["open_items"]["open_material"], 1)

    def test_macro_gate_ok_and_bound(self):
        d = json.loads(self._run().stdout)
        self.assertEqual(d["macro"]["verdict"], "GATE_OK")
        self.assertTrue(d["macro"]["snapshot"])  # 快照哈希锚定

    def test_rules_report_applicable(self):
        d = json.loads(self._run().stdout)
        self.assertIn("适用 2 条", d["rules"]["report"])
        self.assertIn("GATE_OK", d["rules"]["verdict"])

    def test_no_release_or_current_written(self):
        """Gate 3 退出条件第四条：只生成 candidate，不写 release/current。"""
        d = json.loads(self._run().stdout)
        self.assertNotIn("release", d)
        self.assertNotIn("current", d)
        self.assertIn("不能发布", d["note"])


if __name__ == "__main__":
    unittest.main()
