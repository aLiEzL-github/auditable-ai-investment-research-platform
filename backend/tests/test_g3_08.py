"""G3-08 验收测试：600089 纵向初步候选 fixture。

基线：
  · 全流程可复现（两次运行字节一致）
  · 任一适用规则非 PASS 或材料性开放项未关 → PARTIAL（不得转 eligible）
  · 只生成 candidate，不写 release / current（Gate 3 退出条件第四条）
  · A-2b：台账不可达时回退合成 fixture（SYNTHETIC_FIXTURE），
    **0 skipped** —— 合成数据跑通不等于真实路径被验证，输出须标注
    data_source（合成与真实不得互相冒充）
"""
import json
import os
import subprocess
import sys
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOL = os.path.join(REPO, "tools", "vertical_candidate_g3_08.py")


def _find_portfolio():
    """与 vertical_candidate_g3_08.py 相同的解析顺序，避免两处口径不一致。

    显式环境变量 > 仓库同级 > 本机既定位置。**逐个探测其 golden-baselines
    子目录是否存在**，而不是只看路径字符串 —— 路径存在不等于台账在那儿。
    """
    for _c in (os.environ.get("PORTFOLIO_ROOT"),
               os.path.join(REPO, "..", "..", "portfolio"),
               "/Users/li/Documents/Claudetext/portfolio"):
        if _c and os.path.isdir(os.path.join(_c, "golden-baselines")):
            return os.path.abspath(_c)
    return None


class TestVerticalCandidate(unittest.TestCase):
    def _run(self, portfolio_root=None):
        _env = dict(os.environ)
        if portfolio_root is not None:
            _env["PORTFOLIO_ROOT"] = portfolio_root
        r = subprocess.run([sys.executable, TOOL],
                           capture_output=True, text=True, cwd=REPO, env=_env)
        # 断言子进程成功后再解析 —— 否则 json.loads 会掩盖真实错因
        assert r.returncode == 0, (
            f"vertical_candidate_g3_08 退出码 {r.returncode}\n"
            f"stdout[:200]={r.stdout[:200]!r}\nstderr[:400]={r.stderr[:400]!r}")
        return r

    # ── A-2b：台账不可达时回退合成（0 skipped，产物标注 data_source）──
    def test_synthetic_fallback_when_portfolio_unreachable(self):
        """PORTFOLIO_ROOT=/nonexistent → 回退合成 fixture，0 skipped、0 failed，
        产物含 data_source=SYNTHETIC。"""
        r = self._run(portfolio_root="/nonexistent")
        self.assertEqual(r.returncode, 0, r.stderr)
        d = json.loads(r.stdout)
        self.assertEqual(d["data_source"], "SYNTHETIC")
        self.assertIn("不构成对真实 600089 的任何断言", d["data_source_note"])

    def test_real_path_when_portfolio_available(self):
        """台账可达 → data_source=REAL（本机验证；CI 无台账走合成）。"""
        _pf = _find_portfolio()
        r = self._run(portfolio_root=_pf)
        self.assertEqual(r.returncode, 0, r.stderr)
        d = json.loads(r.stdout)
        if _pf is not None:
            self.assertEqual(d["data_source"], "REAL")

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
