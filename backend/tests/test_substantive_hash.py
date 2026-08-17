"""实质哈希的结构分隔：行为、覆盖与变异注入。

## 起因

原做法是一张 `EXCLUDE` 模式清单，实测两处失败：

```text
① 不完备 —— 「④ 开放项 N/M」与 CI 步骤明细不在清单里，
   六份已签验收包的 substantive 因此**全部漂移**（2026-08-17 实测）
② 已分岔 —— 全仓 9 份 EXCLUDE 定义归并为 2 种：
   build_gate3_acceptance.py 只有 9 条，比其余 8 份少 5 条
```

改为结构分隔：剔除 `<!-- LIVE-BEGIN -->` … `<!-- LIVE-END -->` 之间的内容。
**默认「一切都算数」，只有显式圈出的活块不算** —— 与枚举清单方向相反。

## 覆盖判据为什么要用 AST

迁移 `build_gate0` 时，验证判据检的是「`LIVE_BEGIN` 出现在文件里」——
而它只出现在 **import 行**，没包住任何东西，判据照样报「已迁移」。
实测（连跑两次比对 substantive）才发现活块数为 0。

故本文件的覆盖判据检的是「`LIVE_BEGIN` **被当作实参传给了某个调用**」，
而不是它是否出现在源码里。
"""
import ast
import os
import re
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_TOOLS = os.path.join(_HERE, "..", "tools")
sys.path.insert(0, _TOOLS)

from substantive_hash import (  # noqa: E402
    LIVE_BEGIN, LIVE_END, legacy_leaks, live_lines, strip_live, substantive,
    unbalanced_markers,
)

_GEN_RE = re.compile(r"^build_gate\w+_acceptance\.py$")

_DOC = (f"# 包\n\n正文一\n{LIVE_BEGIN}\n生成时刻 = X\n{LIVE_END}\n正文二\n"
        f"substantive_sha256 = deadbeef\n")


class TestSubstantiveBehaviour(unittest.TestCase):

    def test_live_block_excluded(self):
        a = substantive(_DOC)
        b = substantive(_DOC.replace("生成时刻 = X", "生成时刻 = Y"))
        self.assertEqual(a, b, "活块内改动不应影响 substantive")

    def test_outside_change_included(self):
        """**这条是要害** —— 活块外改动若不影响哈希，这个哈希就等于没有。"""
        a = substantive(_DOC)
        for mutated, why in (
                (_DOC.replace("正文一", "正文壹"), "改正文"),
                (_DOC + "新增一行\n", "增行"),
                (_DOC.replace("正文二\n", ""), "删行")):
            with self.subTest(why):
                self.assertNotEqual(a, substantive(mutated),
                                    f"{why}后 substantive 未变 —— 判据失效")

    def test_self_declaration_excluded(self):
        """哈希的产物不能参与自己的计算。"""
        self.assertEqual(
            substantive(_DOC),
            substantive(_DOC.replace("deadbeef", "0" * 8)))

    def test_multiple_blocks(self):
        """活读数是**散布**的，故须支持多块。"""
        doc = (f"a\n{LIVE_BEGIN}\nX\n{LIVE_END}\nb\n"
               f"{LIVE_BEGIN}\nY\n{LIVE_END}\nc\n")
        self.assertNotIn("X", strip_live(doc))
        self.assertNotIn("Y", strip_live(doc))
        self.assertIn("b", strip_live(doc))
        self.assertEqual(sorted(live_lines(doc)), ["X", "Y"])

    def test_unbalanced_markers_rejected(self):
        for name, doc in (
                ("BEGIN 多", _DOC + LIVE_BEGIN + "\n"),
                ("END 多", _DOC + LIVE_END + "\n"),
                ("交错", _DOC + f"{LIVE_END}\n{LIVE_BEGIN}\n")):
            with self.subTest(name):
                self.assertIsNotNone(unbalanced_markers(doc))

    def test_balanced_markers_ok(self):
        self.assertIsNone(unbalanced_markers(_DOC))


class TestNoDowngrade(unittest.TestCase):
    """MR-2：结构分隔不得比它取代的模式清单覆盖得少。

    起因是**本次迁移自己犯的错**：第一版只圈了「生成时刻」一处，旧清单
    另外 13 条全丢。给台账审计加一条守卫（74→75）就让 Gate3 的 substantive
    漂了 —— 与 OI-PF-118 当年要修的是同一个失效。一次扰动实验才发现，
    通读代码没发现，故固化成判据。
    """

    def test_leak_detected(self):
        doc = f"{LIVE_BEGIN}\n生成时刻 = X\n{LIVE_END}\n独立审计: 退出码 1 | 合计 75 项\n"
        leaks = legacy_leaks(doc)
        self.assertTrue(leaks, "活块外的审计读数未被判为漏")
        self.assertIn(leaks[0][0], ("合计", "独立审计:"))

    def test_no_leak_when_wrapped(self):
        doc = (f"{LIVE_BEGIN}\n生成时刻 = X\n独立审计: 退出码 1 | 合计 75 项\n"
               f"{LIVE_END}\n结论 = **READY**\n")
        self.assertEqual(legacy_leaks(doc), [])

    def test_no_exemption_list(self):
        """**判据不得带豁免名单** —— 起草时曾给「结论 =」开豁免，那正好豁免掉
        了要修的缺陷（结论行嵌着审计读数）。开一条就会开第二条。"""
        src = open(os.path.join(_TOOLS, "substantive_hash.py"), encoding="utf-8").read()
        tree = ast.parse(src)
        names = [t.id for n in tree.body if isinstance(n, ast.Assign)
                 for t in n.targets if isinstance(t, ast.Name)]
        self.assertNotIn("_LEAK_EXEMPT", names,
                         "legacy_leaks 不得有豁免名单")


def _generators():
    return sorted(f for f in os.listdir(_TOOLS) if _GEN_RE.match(f))


def _wraps_with_markers(src):
    """`LIVE_BEGIN` / `LIVE_END` 是否**被当作实参传给了某个调用**。

    不是「出现在源码里」—— import 行里也会出现（build_gate0 实际踩过）。
    """
    tree = ast.parse(src)
    used = set()
    for n in ast.walk(tree):
        if not isinstance(n, ast.Call):
            continue
        for a in list(n.args) + [k.value for k in n.keywords]:
            if isinstance(a, ast.Name) and a.id in ("LIVE_BEGIN", "LIVE_END"):
                used.add(a.id)
            # f-string / 拼接里的用法
            for sub in ast.walk(a):
                if isinstance(sub, ast.Name) and sub.id in ("LIVE_BEGIN", "LIVE_END"):
                    used.add(sub.id)
    return used


class TestGeneratorCoverage(unittest.TestCase):

    def test_every_generator_emits_live_markers(self):
        """X-1：迁移「看起来做了」不算 —— 标记须真的被写出去。"""
        bad = []
        for f in _generators():
            src = open(os.path.join(_TOOLS, f), encoding="utf-8").read()
            used = _wraps_with_markers(src)
            if used != {"LIVE_BEGIN", "LIVE_END"}:
                bad.append(f"{f}（实际用到 {sorted(used) or '无'}）")
        self.assertEqual(
            bad, [],
            f"以下生成器未真正写出活块标记：{bad} —— "
            f"import 了名字不等于包住了东西（build_gate0 实际踩过）")

    def test_every_generator_uses_shared_predicate(self):
        """X-2：不得各自再实现一遍（此前 9 份定义已分岔成 2 种）。

        判据检的是**真的调用了** `substantive_of(...)` 且**没有自带排除清单**。
        初版检的是 `"substantive_hash" in src` —— 子串，import 行就能满足。
        台账侧 build_gate6b 因此蒙混过关：它 import 了共享判据，
        却在 `main()` 里用局部 `_exclude` 清单 + `hashlib.sha256` 自己算，
        与「只 import 不使用」是同一个形态（本轮第三次遇到）。
        """
        bad = []
        for f in _generators():
            src = open(os.path.join(_TOOLS, f), encoding="utf-8").read()
            tree = ast.parse(src)
            calls, own = False, []
            for n in ast.walk(tree):
                if (isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
                        and n.func.id in ("substantive_of", "substantive")):
                    calls = True
                if isinstance(n, ast.Assign):
                    own += [t.id for t in n.targets if isinstance(t, ast.Name)
                            and "exclu" in t.id.lower()]
            if not calls:
                bad.append(f"{f}（未调用共享判据）")
            elif own:
                bad.append(f"{f}（仍自带排除清单 {own}）")
        self.assertEqual(bad, [], f"未使用共享判据：{bad}")

    def test_shared_predicate_check_goes_red(self):
        """变异注入：用 build_gate6b 的**原缺陷形态** —— import 了但自己算。"""
        def probe(src):
            tree = ast.parse(src)
            calls = any(isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
                        and n.func.id in ("substantive_of", "substantive")
                        for n in ast.walk(tree))
            own = [t.id for n in ast.walk(tree) if isinstance(n, ast.Assign)
                   for t in n.targets if isinstance(t, ast.Name)
                   and "exclu" in t.id.lower()]
            return calls and not own

        mutant = ("from substantive_hash import substantive as substantive_of\n"
                  "def main():\n"
                  "    _exclude = ('生成时刻', '合计')\n"
                  "    _sub = [l for l in lines if not any(x in l for x in _exclude)]\n"
                  "    return hashlib.sha256('\\n'.join(_sub).encode()).hexdigest()\n")
        self.assertFalse(probe(mutant), "import 了但自己算，判据不应放行")
        good = ("from substantive_hash import substantive as substantive_of\n"
                "def main():\n"
                "    return substantive_of('\\n'.join(lines))\n")
        self.assertTrue(probe(good))

    def test_generator_set_not_empty(self):
        self.assertGreaterEqual(len(_generators()), 7,
                                "少扫即等于少测")

    def test_coverage_predicate_goes_red(self):
        """X-3 变异注入：**用原缺陷形态** —— 只 import 不使用。"""
        only_import = ("from substantive_hash import LIVE_BEGIN, LIVE_END\n"
                       "def main():\n"
                       "    L.append('生成时刻 = X')\n")
        self.assertEqual(_wraps_with_markers(only_import), set(),
                         "只 import 未使用时，判据不应认为已覆盖")
        wrapped = ("from substantive_hash import LIVE_BEGIN, LIVE_END\n"
                   "def main():\n"
                   "    L.append(LIVE_BEGIN)\n"
                   "    L.append('生成时刻 = X')\n"
                   "    L.append(LIVE_END)\n")
        self.assertEqual(_wraps_with_markers(wrapped),
                         {"LIVE_BEGIN", "LIVE_END"})


if __name__ == "__main__":
    unittest.main()
