#!/usr/bin/env python3
"""akshare_use_check.py —— ADR-018 §4 守卫 A + B：持有但不使用。

A  全仓禁止 import curl_cffi，并禁止导入 akshare 那 5 个引用它的子模块
B  akshare 调用白名单：只允许 contracts/akshare_use_policy.json 列出的函数

清单写在契约里（不写死在代码）；本文件自身与测试夹具显式豁免并**报数**。
用法：python3 backend/tools/akshare_use_check.py [repo_root]
"""
import ast
import json
import os
import re
import sys

ROOT = os.path.abspath(sys.argv[1]) if len(sys.argv) > 1 else "."
POLICY = os.path.join(ROOT, "contracts", "akshare_use_policy.json")
SKIP_DIRS = {".git", ".venv", "venv", "node_modules", "__pycache__"}
SELF = os.path.basename(__file__)


def _modules(node):
    if isinstance(node, ast.Import):
        return [a.name for a in node.names]
    if isinstance(node, ast.ImportFrom):
        base = node.module or ""
        return [base] + [f"{base}.{a.name}" for a in node.names]
    return []


def main() -> int:
    pol = json.load(open(POLICY, encoding="utf-8"))
    forbidden = pol["forbidden_imports"]
    allowed_fns = set(pol.get("allowed_akshare_functions") or [])
    bad, checked, exempt = [], 0, 0
    for dp, dn, fs in os.walk(ROOT):
        dn[:] = [d for d in dn if d not in SKIP_DIRS]
        for fn in fs:
            if not fn.endswith(".py"):
                continue
            rel = os.path.relpath(os.path.join(dp, fn), ROOT)
            # 豁免（**须报数**，不得静默跳过）：
            #  · 本守卫自身携带禁用串
            #  · backend/tests/ 是验证方，须能在字符串字面量中构造红态用例
            #    （沿用 arch_import_check 对 tests/ 的同一先例）
            if fn == SELF or rel.replace(os.sep, "/").startswith("backend/tests/"):
                exempt += 1
                continue
            src = open(os.path.join(dp, fn), encoding="utf-8", errors="replace").read()
            try:
                tree = ast.parse(src)
            except SyntaxError as e:
                bad.append(f"{rel}: 解析失败 {e}")
                continue
            checked += 1
            for node in ast.walk(tree):
                for m in _modules(node):
                    for f in forbidden:
                        if m == f or m.startswith(f + "."):
                            bad.append(f"{rel}: **禁止导入 {m}**（ADR-018 §4 守卫 A）")
            # B：akshare.<fn> 调用须在白名单内
            for m in re.finditer(r"\bak(?:share)?\.([a-z_][a-z0-9_]*)\s*\(", src):
                if m.group(1) not in allowed_fns:
                    bad.append(f"{rel}: akshare 函数 {m.group(1)}() 不在白名单"
                               f"（当前白名单 {len(allowed_fns)} 项，ADR-018 §4 守卫 B）")
    for b in bad:
        print(f"  - {b}")
    if bad:
        print(f"❌ akshare 使用策略违规 {len(bad)} 处")
        return 1
    print(f"✅ akshare 使用策略合规：检查对象 {checked} 个 .py · 豁免 {exempt} 个 · "
          f"禁用导入 {len(forbidden)} 条 · 白名单 {len(allowed_fns)} 项")
    return 0


if __name__ == "__main__":
    sys.exit(main())
