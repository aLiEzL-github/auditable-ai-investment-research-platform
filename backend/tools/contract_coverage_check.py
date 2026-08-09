#!/usr/bin/env python3
"""contract_coverage_check.py —— GG-1/OI-PF-121 契约覆盖面守卫。

断言：模型 __tablename__ ⊆ writers.json 键集 且 ⊆ contracts/schema 文件名集。
输出检查对象数（⑨）；CI required（X-5：先于其保护的动作生效）。
"""
import ast
import json
import os
import re
import sys

ROOT = os.path.abspath(sys.argv[1]) if len(sys.argv) > 1 else "."


def model_tables(backend_app: str) -> dict:
    tables = {}
    for fn in os.listdir(backend_app):
        if not fn.endswith(".py") or fn.startswith("__"):
            continue
        fp = os.path.join(backend_app, fn)
        try:
            tree = ast.parse(open(fp, encoding="utf-8").read(), filename=fp)
        except (OSError, SyntaxError):
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            for stmt in node.body:
                if isinstance(stmt, ast.Assign):
                    for tgt in stmt.targets:
                        if isinstance(tgt, ast.Name) and tgt.id == "__tablename__":
                            val = stmt.value
                            if isinstance(val, ast.Constant) and isinstance(val.value, str):
                                tables[val.value] = f"{fn}:{node.name}"
    return tables


def main() -> int:
    backend_app = os.path.join(ROOT, "backend", "app")
    writers = json.load(open(os.path.join(ROOT, "contracts", "writers.json"),
                             encoding="utf-8"))["matrix"]
    schema_dir = os.path.join(ROOT, "contracts", "schema")
    schema_files = {f[:-len(".schema.json")] for f in os.listdir(schema_dir)
                    if f.endswith(".schema.json")}

    tables = model_tables(backend_app)
    checked = len(tables)
    bad = []
    for t, where in sorted(tables.items()):
        if t not in writers:
            bad.append(f"{t}（{where}）不在 writers.json 键集")
        if t not in schema_files:
            bad.append(f"{t}（{where}）不在 contracts/schema 文件名集")
    if bad:
        print("❌ 契约覆盖面违规：")
        for b in bad:
            print("  -", b)
        return 1
    print(f"✅ 检查对象 {checked} 个模型表，契约覆盖完整（writers + schema）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
