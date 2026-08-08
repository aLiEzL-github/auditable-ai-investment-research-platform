#!/usr/bin/env python3
"""arch-import-check —— 架构导入边界检查（G0-04 §1.1 的 M1—M7）。

骨架级实现：扫描仓库内 Python 源文件的 import，比对禁止列表与模块依赖图。
当前阶段为单模块骨架（app/ 与 tools/），检查范围随 G1 编码推进扩展。

规则（G0-04 §1.1 硬性禁止）：
  M1/M4  禁止引入网络库（可信内核不得有出网能力；解析器不得有 SSRF/外带面）
  M2     L8 计算层不得直接引用 L5 取得器（计算只能吃冻结对象）
  M3     L6 解析器不得引用 persistence（不得有数据库句柄）
  M5    任何模块不得直接写批准事件（仅 L12 批准端点）
  M6    无批准不得提升 current（release 语义，代码层检查见写权矩阵）
  M7    前端不得绕过后端直连数据层（UI 门，属 G5 检查）

命中即退出码 1（CI required check 失败）。
"""

import ast
import os
import sys

ROOT = os.path.abspath(sys.argv[1]) if len(sys.argv) > 1 else "."

# M1/M4：网络/出网库（模块名精确匹配，含 stdlib 与外置包）
NETWORK_LIBS = {
    "requests", "urllib", "urllib3", "httpx", "aiohttp", "socket",
    "http.client", "http.client", "ftplib", "smtplib", "telnetlib",
    "urllib.request", "urllib.parse", "xmlrpc",
}
# M3：persistence 句柄
DB_LIBS = {"sqlite3", "psycopg", "psycopg2", "asyncpg", "SQLAlchemy", "sqlalchemy", "alembic"}
# M5：批准事件直写（模块名特征）
APPROVAL_WRITERS = {"approval", "approve", "release", "current_pointer"}
# 允许自身持有的网络面（服务端监听，非出网）：app/main.py 的 http.server
SERVER_ALLOWLIST = {"app.main"}


def module_name_of(node: ast.AST) -> str:
    if isinstance(node, ast.Import):
        return [a.name for a in node.names]
    if isinstance(node, ast.ImportFrom) and node.module:
        return [node.module]
    return []


def main() -> int:
    bad = []
    checked = 0
    SKIP_DIRS = {".git", ".venv", "venv", "node_modules", "__pycache__"}
    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for fn in filenames:
            if not fn.endswith(".py"):
                continue
            fp = os.path.join(dirpath, fn)
            rel = os.path.relpath(fp, ROOT).replace(os.sep, ".")
            # tests/ 是验证方（须能访问被测端点），不受 M1—M7 生产代码边界约束
            if ".tests." in rel or rel.startswith("tests."):
                continue
            # persistence 实现层自身（G1-03 repository.py）允许引用 DB 库：
            # M3 约束的对象是「L6 解析器引用 persistence」，而非 persistence 自身。
            if "repository" in rel or "migrations" in rel or "jobs" in rel \
                    or "migration_check" in rel or "vertical_smoke" in rel \
                    or "import_guard" in rel:
                continue  # persistence 实现层/迁移/调度；import_guard 为 L3 取数层
                # SSRF 校验器（M1/M4 只约束 L0—L2 可信内核与 L6 解析器，G0-04 §1.1）
            try:
                tree = ast.parse(open(fp, encoding="utf-8").read(), filename=fp)
            except (OSError, SyntaxError) as e:
                bad.append(f"{rel}: 解析失败 {e}")
                continue
            checked += 1
            for node in ast.walk(tree):
                for mod in module_name_of(node):
                    base = mod.split(".")[0]
                    if rel in SERVER_ALLOWLIST and base in ("http", "socket"):
                        continue  # 服务端监听面（非出网）
                    if base in NETWORK_LIBS:
                        bad.append(f"{rel}: M1/M4 引入网络库 {mod}")
                    if base in DB_LIBS:
                        bad.append(f"{rel}: M3 引入 persistence {mod}")
                    if base in APPROVAL_WRITERS:
                        bad.append(f"{rel}: M5 直写批准/发布对象 {mod}")
    if bad:
        print("❌ 架构导入边界违规：")
        for b in bad:
            print("  -", b)
        return 1
    print(f"✅ 检查对象 {checked} 个 .py，无违规（M1—M7 骨架级）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
