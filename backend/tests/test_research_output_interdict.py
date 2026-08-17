"""研究产出禁入的**两层**都须在位（OI-PF-022 / OI-PF-026 闭合条件）。

`OI-PF-022` 的闭合条件原文：

    ADR-002 三层分离 + **.gitignore 硬规则**使数据与研究产出结构性无法入库；
    仍须 **G1-01 落实规则**、G1-07 落实自动拦截。

2026-08-17 闭合时实测发现：**G1-01 那一层从未落地** —— `golden-baselines/`
`evidence-packs/` `candidates/` `research/` `portfolio/` 一个也没被 `.gitignore`
忽略，全靠 CI 单层拦。`ADR-006` L4 的原话是「`.gitignore` **不够**」——
不够不等于不要，两层各防一半：

```text
.gitignore（G1-01）   防误 git add
CI 侧扫描（G1-07）    防 git add -f、防粘进 Markdown、防 CI 日志打印
```

## 另一件当场发现的事

该工具当时**没有** `--selftest`，而未知开关被无声吞掉：

```text
research_data_interdict.py . --selftest              ┐ 输出逐字相同
research_data_interdict.py . --this-flag-does-not-exist ┘ md5 一致
```

于是 `rc=0` 被读成「自检通过」，实际什么都没跑。**「没这个功能」被当成
「自检通过」** —— 与规则 ㉟（「跑了但失败」与「根本没跑」须可分辨）同形态。
本文件把「未知开关须判红」也固化成用例。

## 本文件为何须自证 SYNTHETIC_FIXTURE

R2 的正向样本必须**长得像真数据**，否则测不出规则会不会命中 —— 于是本文件
自己就带着研究数据形态的字符串，`research_data_interdict` 扫到它会判红
（实测撞到）。这不是误报，是扫描器在正确工作。

走它既有的自证机制：`backend/tests/` 下声明 `SYNTHETIC_FIXTURE` 即豁免。
**该机制是自我声明** —— 任何测试文件都能这样给自己开豁免；这是既有设计的
已知边界，不是本文件引入的。写在这里以免下一个人以为它是强保证。

SYNTHETIC_FIXTURE = true —— 本文件内的数值全部为构造样本，非真实财报数据。
"""
import os
import subprocess
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.abspath(os.path.join(_HERE, "..", ".."))
_TOOL = os.path.join(_ROOT, "backend", "tools", "research_data_interdict.py")

sys.path.insert(0, os.path.join(_ROOT, "backend", "tools"))

from research_data_interdict import (  # noqa: E402
    PATH_PAT, _looks_like_real_data, _r1_hit,
)

# 研究产出与原始序列 —— 两者都不得入公开仓（VD-20 = 仅内部；ADR-006 L5 指针化）
_MUST_IGNORE = ("golden-baselines", "evidence-packs", "candidates",
                "research", "portfolio", "raw-series")


class TestGitignoreLayer(unittest.TestCase):
    """G1-01 那一层 —— 靠 `git check-ignore` 实测，不读 .gitignore 的字面。"""

    def _ignored(self, path):
        return subprocess.run(["git", "-C", _ROOT, "check-ignore", "-q", path],
                              capture_output=True).returncode == 0

    def test_research_dirs_ignored(self):
        missing = [d for d in _MUST_IGNORE if not self._ignored(f"{d}/x.json")]
        self.assertEqual(
            missing, [],
            f"以下研究产出目录未被 .gitignore 忽略：{missing} —— "
            f"G1-01 那一层缺位，只剩 CI 单层拦（2026-08-17 实测正是此状态）")

    def test_xbrl_ignored(self):
        """ADR-006 L5：统计局原始序列不入库（OI-PF-026）。"""
        self.assertTrue(self._ignored("x.xbrl"),
                        "XBRL 实例文档未被忽略 —— L5 指针化的一半缺位")

    def test_predicate_is_check_ignore_not_substring(self):
        """判据须是 git 的判定，不是「.gitignore 里出现了这个词」。

        写下一行 `# golden-baselines/` 注释也能让子串检查通过。
        """
        self.assertFalse(self._ignored("backend/app/main.py"),
                         "正常源码被忽略了 —— 规则过宽")


class TestRulesFire(unittest.TestCase):
    """R1/R2/R3 各自能红、且良性输入不误报 —— 调用**真判据**。"""

    def test_r1_json_only(self):
        self.assertTrue(_r1_hit(
            "x.json", '{"baseline_id": "B-600089-1", "back_source": "cninfo"}'))
        # 治理文本与序列化代码都曾误命中过（三次收窄的由来）
        self.assertFalse(_r1_hit(
            "adr/x.md", "本文讨论 baseline_id 字段，以及 back_source 的取值范围。"))
        self.assertFalse(_r1_hit(
            "app/golden_baseline.py",
            'return {"baseline_id": self.baseline_id, "back_source": self.back_source}'))
        self.assertFalse(_r1_hit("y.json", '{"baseline_id": "B-1"}'))

    def test_r2_real_vs_placeholder(self):
        self.assertTrue(_looks_like_real_data(
            "600089 营业收入 12345678.90 元；净利润 2345678.12 元"))
        self.assertFalse(_looks_like_real_data("600089 营业收入 1000000 元（占位）"))
        self.assertFalse(_looks_like_real_data("标的 600089 的权利判定见 VD-12"))

    def test_r3_paths(self):
        for p in ("golden-baselines/600089.json", "evidence-packs/x.json",
                  "candidates/y.json"):
            self.assertTrue(PATH_PAT.search(p), p)
        self.assertIsNone(PATH_PAT.search("backend/app/models.py"))


class TestCliContract(unittest.TestCase):

    def _run(self, *args):
        return subprocess.run([sys.executable, _TOOL, _ROOT, *args],
                              capture_output=True, text=True)

    def test_selftest_actually_runs(self):
        """自检须**真的跑** —— 与未知开关的输出必须不同。

        这正是它此前的缺陷形态：两者输出逐字相同，rc 都是 0。
        """
        a = self._run("--selftest")
        b = self._run("--this-flag-does-not-exist")
        self.assertEqual(a.returncode, 0, a.stdout + a.stderr)
        self.assertNotEqual(
            a.stdout, b.stdout,
            "--selftest 与未知开关输出相同 —— 自检未实现却报通过")

    def test_unknown_flag_refused(self):
        r = self._run("--nope")
        self.assertEqual(r.returncode, 2,
                         "未知开关未判红 —— 静默忽略会让「没跑」与「跑过了」不可分辨")

    def test_plain_scan_passes(self):
        r = self._run()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)


class TestCiWiring(unittest.TestCase):
    """工具存在不等于在跑 —— CI 里须真的调用它。"""

    def test_tool_invoked_in_workflow(self):
        wf = os.path.join(_ROOT, ".github", "workflows")
        hit = [f for f in os.listdir(wf)
               if "research_data_interdict.py" in
               open(os.path.join(wf, f), encoding="utf-8").read()]
        self.assertTrue(hit, "没有任何 workflow 调用 research_data_interdict.py")


if __name__ == "__main__":
    unittest.main()
