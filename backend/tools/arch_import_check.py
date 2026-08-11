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

# BB-1/OI-PF-119：豁免改为**按架构层的显式模块清单**（精确路径匹配），
# 不得用文件名子串 —— 子串豁免可被无意绕过（如 "jobs" 会豁免任何含 jobs 的文件）。
# 各层语义（G0-04 §1.1）：L2 persistence 允许 DB 库（M3 约束对象是 L6 解析器）；
# L3 取数层允许出网（M1/M4 只约束 L0—L2 内核与 L6 解析器，VD-11 §6 Discovery 允许清单）。
LAYER_EXEMPT = {
    "L2_persistence": ["backend/app/repository.py", "backend/app/jobs.py"],
    "migrations": ["backend/migrations"],
    "L3_fetch": ["backend/tools/import_guard.py", "backend/tools/sse_adapter.py",
                 "backend/tools/macro_adapter.py", "backend/tools/akshare_adapter.py",
                 "backend/tools/cninfo_adapter.py"],
    "tools_internal": ["backend/tools/migration_check.py", "backend/tools/vertical_smoke.py"],
    # OI-PF-141：供应链清单刷新工具。出网范围仅 pypi.org 的 JSON 元数据端点，
    # **只读元数据、不下载分发包、不执行任何上游代码**，且**不在 CI 中运行**
    # （CI 只跑离线的 wheel_policy_check）。检查器本身刻意做成离线的，
    # 就是为了不让「守卫自己出网」成为常态 —— 故此处只豁免刷新工具一个文件。
    "supply_chain_refresh": ["backend/tools/wheel_manifest_refresh.py"],
}
# 出网授权模块（L3 取数层）：非豁免模块 import 它们 = 传递性出网，必须抓
EGRESS_MODULES = {"import_guard", "sse_adapter", "macro_adapter", "akshare_adapter",
                  "cninfo_adapter"}


def exempt_layer_of(rel: str):
    """精确路径 → 豁免层（None = 非豁免）。不得用子串。

    rel 以点分隔（os.walk 转换）；清单路径以 / 分隔 —— 统一转点比较。
    """
    rel_dot = rel.replace(os.sep, ".")
    for layer, paths in LAYER_EXEMPT.items():
        for pth in paths:
            p = pth.replace(os.sep, ".")
            if rel_dot == p or rel_dot.startswith(p + "."):
                return layer
    return None

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


def _all_exempt_files(ROOT):
    """全部豁免文件（精确路径 → 模块名），供传递性检查与计数。"""
    out = []
    for layer, paths in LAYER_EXEMPT.items():
        for pth in paths:
            if pth.endswith(".py"):
                out.append(pth)
            else:
                d = os.path.join(ROOT, pth)
                for dp, _, fns in os.walk(d):
                    for fn in fns:
                        if fn.endswith(".py"):
                            out.append(os.path.relpath(os.path.join(dp, fn), ROOT))
    return out


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
            layer = exempt_layer_of(rel)
            if layer is not None:
                continue  # 显式层豁免（精确路径，非子串）
            # FF-3/U-1（OI-PF-126）：禁止硬编码 source_status="ALLOWED" 字面量
            # （测试夹具白名单：_matrix_fixture.py 等显式允许，报数）
            _src_text = open(fp, encoding="utf-8").read()
            if ('source_status="ALLOWED"' in _src_text
                    and "_matrix_fixture" not in rel
                    and "arch_import_check" not in rel):  # 检查器自身豁免（规则定义字面量）
                bad.append(f"{rel}: FF-3 硬编码 source_status=\"ALLOWED\"（须矩阵驱动）")
            try:
                tree = ast.parse(_src_text, filename=fp)
            except (OSError, SyntaxError) as e:
                bad.append(f"{rel}: 解析失败 {e}")
                continue
            checked += 1
            exempt_modules = {m.rsplit("/", 1)[-1][:-3] for m in _all_exempt_files(ROOT)}
            for node in ast.walk(tree):
                for mod in module_name_of(node):
                    base = mod.split(".")[0]
                    if rel in SERVER_ALLOWLIST and base in ("http", "socket"):
                        continue  # 服务端监听面（非出网）
                    if base in EGRESS_MODULES or base in exempt_modules and base in EGRESS_MODULES:
                        bad.append(f"{rel}: 传递性出网 —— import 已豁免的出网模块 {mod}")
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
    exempt_n = len(_all_exempt_files(ROOT))
    print(f"✅ 检查对象 {checked} 个 .py，无违规（M1—M7 骨架级）"
          f"；豁免文件 {exempt_n} 个")
    return 0


if __name__ == "__main__":
    sys.exit(main())
