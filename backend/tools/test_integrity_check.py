#!/usr/bin/env python3
"""test_integrity_check.py —— G1 一票否决「空测试」机器强制（A2-1/OI-PF-012）。

T-1  发现用例数 == 契约声明（精确匹配，增删须在 PR 显式改基线）
T-2  零跳过 —— skip/expectedFailure 须在契约白名单列明理由
T-3  零空体用例（体内只有 pass / ... / 文档串）
T-4  静态数出的用例 == 被发现的用例 —— 抓「写了但从不运行」
"""
import ast
import json
import os
import sys

ROOT = os.path.abspath(sys.argv[1]) if len(sys.argv) > 1 else "."
TESTS = os.path.join(ROOT, "backend", "tests")
BASELINE = json.load(open(os.path.join(ROOT, "contracts", "test_baseline.json"),
                          encoding="utf-8"))


def find_tests() -> dict:
    """unittest 实际发现的用例：test_*.py 中 Test* 类的 test_* 方法。"""
    found = {}
    for fn in sorted(os.listdir(TESTS)):
        if not fn.startswith("test_") or not fn.endswith(".py"):
            continue
        fp = os.path.join(TESTS, fn)
        try:
            tree = ast.parse(open(fp, encoding="utf-8").read(), filename=fp)
        except (OSError, SyntaxError):
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and node.name.startswith("Test"):
                for item in node.body:
                    if isinstance(item, ast.FunctionDef) and item.name.startswith("test_"):
                        found[f"{fn}:{node.name}.{item.name}"] = item
    return found


def static_count() -> int:
    """静态数出的用例：test_*.py 中 def test_*（含模块级与类级）。"""
    n = 0
    for fn in sorted(os.listdir(TESTS)):
        if not fn.startswith("test_") or not fn.endswith(".py"):
            continue
        src = open(os.path.join(TESTS, fn), encoding="utf-8").read()
        n += len([ln for ln in src.splitlines() if ln.strip().startswith("def test_")])
    return n


def is_empty_body(node: ast.FunctionDef) -> bool:
    stmts = [s for s in node.body if not isinstance(s, ast.Expr) or
             not isinstance(getattr(s, "value", None), ast.Constant)]
    if not stmts:
        return True
    if len(stmts) == 1 and isinstance(stmts[0], ast.Pass):
        return True
    return False


def main() -> int:
    found = find_tests()
    bad = []

    # T-1：发现用例数 == 契约声明（精确匹配）
    declared = BASELINE.get("declared_test_count")
    if declared is None:
        bad.append("contracts/test_baseline.json 缺 declared_test_count")
    elif len(found) != declared:
        bad.append(f"T-1: 发现 {len(found)} 用例 ≠ 契约声明 {declared}（增删须改基线）")

    # T-2：零跳过（白名单外）
    skip_allow = set(BASELINE.get("skip_whitelist", []))
    skipped = []
    for fn in sorted(os.listdir(TESTS)):
        if not fn.endswith(".py"):
            continue
        src = open(os.path.join(TESTS, fn), encoding="utf-8").read()
        for i, ln in enumerate(src.splitlines(), 1):
            if "@skip" in ln or "@expectedFailure" in ln:
                key = f"{fn}:{i}"
                if key not in skip_allow:
                    skipped.append(key)
    if skipped:
        bad.append(f"T-2: 白名单外跳过 {len(skipped)} 处（须列明理由）: {skipped[:3]}…")

    # T-3：零空体用例
    empty = [k for k, n in found.items() if is_empty_body(n)]
    if empty:
        bad.append(f"T-3: 空体用例 {len(empty)} 处: {empty[:3]}…")

    # T-4：静态数出 == 被发现
    stat = static_count()
    if stat != len(found):
        bad.append(f"T-4: 静态 {stat} ≠ 被发现 {len(found)}（写了但从不运行）")

    if bad:
        print("❌ 测试完整性违规：")
        for b in bad:
            print("  -", b)
        return 1
    print(f"✅ 检查对象 {len(found)} 个用例：T-1~T-4 全部通过（静态 {stat} == 发现）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
