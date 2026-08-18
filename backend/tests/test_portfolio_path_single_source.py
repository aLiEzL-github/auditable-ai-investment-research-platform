"""台账路径只有一个来源：`PORTFOLIO`。

## 起因

`build_gate2_acceptance.py` 在 `§6 测试基线` 里这样拼台账路径：

```python
os.path.join(ROOT, '..', 'portfolio', 'tools', script)
```

`ROOT` 是**仓库**根，于是拼出 `<仓库>/portfolio/tools/audit_session.py` —— 不存在。
而同一文件开头早已由 `_portfolio_root()` 算好了 `PORTFOLIO`（读 `PORTFOLIO_ROOT`）。

**后果不是崩，是静默记账。** 验收包的 §6 写下

```text
独立审计: 退出码 2 | ... can't open file '.../portfolio/tools/audit_session.py'
v2.0 基线: 退出码 2 | 同上
```

而结论仍取 `READY_FOR_APPROVAL` —— 该包在「自己的证据段显示检查根本没跑起来」
的状态下被签署过一次（2026-08-11），直到 2026-08-17 重签前逐份审阅才发现。

`OI-PF-153` 修过同类缺陷，注释就在同一文件第 171 行。**漏了这一处** ——
「找到一个实例」与「列全实例」是两件事（OI-PF-184 的同一教训）。

## 判据怎么写才不误伤

`vertical_candidate_g3_08.py` 里有一处**正当**的同形代码：

```python
PORTFOLIO = (sys.argv[1] if len(sys.argv) > 1
             else os.environ.get("PORTFOLIO_ROOT")
             or os.path.join(..., "..", "portfolio"))   # 兜底链末端，OI-PF-186 有意为之
```

区别在**位置**，不在写法：那一处在 `PORTFOLIO` 的**定义**里，是路径的来源；
缺陷那一处在**使用**处，是把已经算好的来源又重拼了一遍。

故判据按 AST 定位：文件若定义了 `PORTFOLIO`，则该定义**之外**的任何
`os.path.join(...)` 都不得含字面量 `"portfolio"`。
"""
import ast
import os
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_TOOLS = os.path.join(_HERE, "..", "tools")


def _rebuild_sites(src):
    """返回 `PORTFOLIO` 定义之外、重拼台账路径的位置 [(行号, 源码)]。

    未定义 `PORTFOLIO` 的文件不适用（它没有可用的单一来源），返回空表。
    """
    tree = ast.parse(src)
    defs = [n for n in ast.walk(tree) if isinstance(n, ast.Assign)
            and any(isinstance(t, ast.Name) and t.id == "PORTFOLIO"
                    for t in n.targets)]
    if not defs:
        return []
    # 定义语句所占的行区间 —— 落在其中的 join 是来源本身，不算重拼
    spans = [(d.lineno, d.end_lineno) for d in defs]

    out = []
    for n in ast.walk(tree):
        if not (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                and n.func.attr == "join"):
            continue
        if any(a <= n.lineno <= b for a, b in spans):
            continue
        lits = [x.value for x in n.args
                if isinstance(x, ast.Constant) and isinstance(x.value, str)]
        if any(x == "portfolio" for x in lits):
            seg = ast.get_source_segment(src, n) or ""
            out.append((n.lineno, " ".join(seg.split())[:80]))
    return out


def _sources():
    return sorted(f for f in os.listdir(_TOOLS) if f.endswith(".py"))


class TestPortfolioPathSingleSource(unittest.TestCase):

    def test_no_rebuild_outside_definition(self):
        bad = []
        for f in _sources():
            src = open(os.path.join(_TOOLS, f), encoding="utf-8").read()
            for ln, seg in _rebuild_sites(src):
                bad.append(f"{f}:{ln} {seg}")
        self.assertEqual(
            bad, [],
            f"以下位置绕开 PORTFOLIO 重拼台账路径：{bad} —— "
            f"拼错不会崩，只会让验收包**静默记下「没跑起来」**而结论照旧")

    def test_scan_set_not_empty(self):
        """恒空的集合会让上面那条静默通过。"""
        self.assertGreaterEqual(len(_sources()), 10,
                                f"只扫到 {len(_sources())} 个源文件 —— 少扫即等于少测")

    def test_predicate_goes_red(self):
        """变异注入：**用原缺陷形态** —— 定义之外重拼。"""
        mutant = ("PORTFOLIO = os.environ['PORTFOLIO_ROOT']\n"
                  "def main():\n"
                  "    p = os.path.join(ROOT, '..', 'portfolio', 'tools', s)\n")
        self.assertTrue(_rebuild_sites(mutant), "定义之外重拼时判据未判红")
        good = ("PORTFOLIO = os.environ['PORTFOLIO_ROOT']\n"
                "def main():\n"
                "    p = os.path.join(PORTFOLIO, 'tools', s)\n")
        self.assertEqual(_rebuild_sites(good), [])

    def test_definition_fallback_not_flagged(self):
        """兜底链末端在**定义内**，不得误伤（vertical_candidate 的正当写法）。"""
        legit = ("PORTFOLIO = (sys.argv[1] if len(sys.argv) > 1\n"
                 "             else os.environ.get('PORTFOLIO_ROOT')\n"
                 "             or os.path.join(BASE, '..', 'portfolio'))\n")
        self.assertEqual(_rebuild_sites(legit), [])

    def test_file_without_portfolio_var_skipped(self):
        """没有 PORTFOLIO 变量的文件不适用 —— 它没有可用的单一来源。"""
        self.assertEqual(
            _rebuild_sites("p = os.path.join(A, '..', 'portfolio')\n"), [])


class TestGate2EvidenceActuallyRuns(unittest.TestCase):
    """§6 的检查结果行须由 PORTFOLIO 下的脚本产出。

    只查判据的**输入来源**，不实跑 —— 实跑要真台账，仓库测试里没有。
    真正的实跑证据由签署时的 S1 承载。
    """

    def test_gate2_uses_portfolio_for_checks(self):
        """按 AST 定位那个 for 循环，检其 body 里的路径拼接。

        初版用正则去框「for … 到 L.append(LIVE_END)」之间的文本 —— 结构一变就
        框不到，而框不到时它**照样判红**，看起来像修复没生效。更糟的是文本判据
        会命中注释：本文件那段说明里就写着 `os.path.join(ROOT, '..', 'portfolio')`。
        """
        src = open(os.path.join(_TOOLS, "build_gate2_acceptance.py"),
                   encoding="utf-8").read()
        tree = ast.parse(src)
        loop = next((n for n in ast.walk(tree) if isinstance(n, ast.For)
                     and "独立审计" in (ast.get_source_segment(src, n.iter) or "")),
                    None)
        self.assertIsNotNone(loop, "找不到 §6 的检查循环 —— 判据失效，不得当作通过")

        joins = [n for n in ast.walk(loop)
                 if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                 and n.func.attr == "join"]
        self.assertTrue(joins, "循环体内没有路径拼接 —— 结构已变，判据须重写")
        for j in joins:
            names = {a.id for a in ast.walk(j) if isinstance(a, ast.Name)}
            lits = [a.value for a in j.args
                    if isinstance(a, ast.Constant) and isinstance(a.value, str)]
            self.assertIn("PORTFOLIO", names,
                          f"第 {j.lineno} 行的路径未走 PORTFOLIO —— "
                          f"拼错不崩，只会静默记下 rc=2 而结论照旧")
            self.assertNotIn("portfolio", lits,
                             f"第 {j.lineno} 行仍含字面量 'portfolio'")


if __name__ == "__main__":
    unittest.main()
