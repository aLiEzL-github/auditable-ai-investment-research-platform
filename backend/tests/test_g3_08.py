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


# 台账（portfolio）是本仓之外的独立目录，**CI 检出中不存在**。
# 本组用例驱动的工具须读台账，故在台账不可达时**显式跳过并说明原因** ——
# 而不是让 json.loads 崩在非 JSON 的 stdout 上（那会把「测不了」
# 报成「测失败」，两者须可分辨）。
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


_PORTFOLIO = _find_portfolio()
_PORTFOLIO_OK = _PORTFOLIO is not None
_SKIP_WHY = (f"台账不可达（已探测 PORTFOLIO_ROOT / 仓库同级 / 本机既定位置三处）—— 本组用例须读 golden-baselines 与 "
             f"open-items.json，二者不在本仓内。CI 检出无台账属**预期**；"
             f"本机运行或设 PORTFOLIO_ROOT 后即执行。**跳过 ≠ 通过**。")


@unittest.skipUnless(_PORTFOLIO_OK, _SKIP_WHY)
class TestVerticalCandidate(unittest.TestCase):
    def _run(self):
        _env = dict(os.environ, PORTFOLIO_ROOT=_PORTFOLIO)
        r = subprocess.run([sys.executable, TOOL],
                           capture_output=True, text=True, cwd=REPO, env=_env)
        # 断言子进程成功后再解析 —— 否则 json.loads 会掩盖真实错因
        assert r.returncode == 0, (
            f"vertical_candidate_g3_08 退出码 {r.returncode}\n"
            f"stdout[:200]={r.stdout[:200]!r}\nstderr[:400]={r.stderr[:400]!r}")
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
