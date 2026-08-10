#!/usr/bin/env python3
"""test_integrity_check.py —— 空测试／被跳过的测试必须使 CI 失败（OI-PF-012）。

基线 A §9.1 与附录 A.1 的变异测试原则：**缺此门则 CI 全绿不构成任何证据。**
一个绿色的 required check，可能意味着「200 个断言都过了」，也可能意味着
「测试被删空了」「测试被 skip 了」「测试文件存在但从不被发现」。
这三种情况在流水线输出里长得一模一样 —— 本守卫让它们可分辨。

四条断言：
  T-1  发现到的用例数 == contracts/test_baseline.json 的 expected_tests
       （**精确匹配**：增删测试都须在 PR 中显式改基线，收缩因此不可能悄悄发生）
  T-2  零跳过 —— skip / expectedFailure 须在契约白名单中列明理由
  T-3  零空体用例（体内只有 pass / ... / 文档串）
  T-4  静态数出的用例数 == 被发现的用例数
       —— 抓「写了但从不运行」：模块级 test_ 函数、非 TestCase 类里的 test_ 方法

**本守卫不运行测试断言本身**，只审查测试集合的完整性；断言由测试作业负责。
用法：python3 backend/tools/test_integrity_check.py [repo_root]
"""
import ast
import json
import os
import sys
import unittest

ROOT = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else ".")
TESTS = os.path.join(ROOT, "backend", "tests")
BASELINE = os.path.join(ROOT, "contracts", "test_baseline.json")


def _body_is_empty(node):
    body = [x for x in node.body
            if not (isinstance(x, ast.Expr) and isinstance(x.value, ast.Constant)
                    and isinstance(x.value.value, str))]
    return all(isinstance(x, ast.Pass)
               or (isinstance(x, ast.Expr) and isinstance(x.value, ast.Constant)
                   and x.value.value is Ellipsis)
               for x in body)


def _static_cases():
    """静态数出的用例：类内 test* 方法 + **模块级 test* 函数**。
    后者写了也不会被 unittest 发现 —— 正是 T-4 要抓的那一类。"""
    cases, empty, orphan = set(), [], []
    for f in sorted(os.listdir(TESTS)):
        if not (f.startswith("test_") and f.endswith(".py")):
            continue
        tree = ast.parse(open(os.path.join(TESTS, f), encoding="utf-8").read())
        for cls in [x for x in tree.body if isinstance(x, ast.ClassDef)]:
            for n in cls.body:
                if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) \
                        and n.name.startswith("test"):
                    cases.add(f"{cls.name}::{n.name}")
                    if _body_is_empty(n):
                        empty.append(f"{f}::{cls.name}::{n.name}")
        for n in tree.body:
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) \
                    and n.name.startswith("test"):
                orphan.append(f"{f}::{n.name}")
                if _body_is_empty(n):
                    empty.append(f"{f}::{n.name}")
    return cases, empty, orphan


def _discovered():
    found = set()

    def walk(suite):
        for t in suite:
            if isinstance(t, unittest.TestSuite):
                walk(t)
            else:
                parts = t.id().split(".")
                found.add(f"{parts[-2]}::{parts[-1]}")
    sys.path.insert(0, TESTS)
    walk(unittest.defaultTestLoader.discover(TESTS))
    return found


def main() -> int:
    base = json.load(open(BASELINE, encoding="utf-8"))
    allow_skip = set(base.get("allowed_skips", []))
    bad = []

    static, empty, orphan = _static_cases()
    found = _discovered()

    # T-1 精确匹配
    if len(found) != base["expected_tests"]:
        bad.append(f"**发现 {len(found)} 个用例，契约声明 {base['expected_tests']} 个** —— "
                   f"增删测试须在 PR 中显式改 contracts/test_baseline.json，"
                   f"否则测试集合收缩可以悄悄发生（E-TEST-001 / OI-PF-012）")

    # T-2 零跳过
    import io
    res = unittest.TextTestRunner(stream=io.StringIO(), verbosity=0).run(
        unittest.defaultTestLoader.discover(TESTS))
    skipped = [t.id() for t, _ in res.skipped] + \
              [t.id() for t, _ in res.expectedFailures]
    rogue = [s for s in skipped if s not in allow_skip]
    if rogue:
        bad.append(f"**{len(rogue)} 个用例被跳过且未在契约中列明理由**: {rogue[:4]} "
                   f"—— 跳过的测试与通过的测试在 CI 输出里长得一样（E-TEST-002）")

    # T-3 零空体
    if empty:
        bad.append(f"**{len(empty)} 个空体用例**（体内只有 pass/.../文档串）: {empty[:4]} "
                   f"—— 空测试恒绿，不构成任何证据（E-TEST-003）")

    # T-4 静态 == 发现
    if orphan:
        bad.append(f"**{len(orphan)} 个模块级 test_ 函数**: {orphan[:4]} —— "
                   f"unittest 只发现 TestCase 子类里的方法，这些写了也不会运行"
                   f"（E-TEST-004）")
    miss = sorted(static - found)
    if miss:
        bad.append(f"**{len(miss)} 个用例静态存在却未被发现**: {miss[:4]} —— "
                   f"可能所在类不是 TestCase 子类（E-TEST-004）")

    for b in bad:
        print(f"  - {b}")
    if bad:
        print(f"❌ 测试集合完整性不合格 {len(bad)} 处")
        return 1
    print(f"✅ 测试集合完整性合格：发现 {len(found)} 个用例 == 契约声明 · "
          f"跳过 0 · 空体 0 · 静态与发现一致（检查对象 {len(static)} 个静态用例）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
