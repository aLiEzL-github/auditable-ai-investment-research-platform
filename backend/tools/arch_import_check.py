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

B-2a（G4 修复）：可信内核不得 import network_probe —— 断网探针只能
以回调注入（offline_probe 豁免的理由须机器化，不靠人读注释）。
B-2c（G4 修复）：每条 LAYER_EXEMPT 豁免条目须带一条**可执行断言**，
断言不成立即 FAIL —— 豁免即削弱控制，理由须可证伪（OI-PF-119 之后
的第二道豁免纪律：前一道修匹配方式，这一道修理由的可验证性）。

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
    # G4（B-2b (i) 裁定，U，2026-08-11）：发布/冻结层（L7）。写 release/
    # approval/pointer 均经 assert_writer 走 writers.json 写权矩阵
    # （L11_release / L12_approval_endpoint + 前置条件机器强制）；
    # M5「无批准不得提升 current」由该矩阵的 never 名单 + publish_release
    # 的批准校验承担 —— 豁免理由已与实现一致（EXEMPT_ASSERTS 断言）。
    "L7_publish": ["backend/app/publish_engine.py"],
    # G4-08：离线断网断言探针（唯一职责 = 证明网络不可达，探测失败即拒绝，
    # **不是出网能力**；可信内核不 import 本模块，由调用方注入回调）。
    "offline_probe": ["backend/app/network_probe.py"],
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
# G5 审核发现：该键写作 "app.main"，而守卫内部的 rel 形态是
# "backend.app.main.py" —— **从未匹配上**，是一条空转豁免。
# main.py 此前不报错只因它当时仅 import http.server（不在 NETWORK_LIBS 中）。
# 修为实际形态；同时这条豁免的语义须写清：放行的是**服务端监听面**，非出网。
SERVER_ALLOWLIST = {"backend.app.main.py"}

# ── 死豁免检测（G5 审核发现）────────────────────────────────────
# SERVER_ALLOWLIST 的键曾写作 "app.main"，而扫描内部的 rel 形态是
# "backend.app.main.py" —— **从未匹配上**。它不暴露，只因 main.py 当时
# 恰好只 import http.server（不在 NETWORK_LIBS 中）；直到有人引入
# urllib.parse 才现形。
#
# 与 B-2c 的 EXEMPT_ASSERTS 方向相反：那道管「豁免理由是否成立」
# （L7_publish 曾理由不成立而豁免生效）；这道管「豁免是否真的生效」
# （SERVER_ALLOWLIST 曾根本没生效而无人察觉）。**两种都只在有人去戳它时才现形。**
#
# 判据不是「路径存在」而是「本次扫描中被实际命中」—— 前者挡不住格式不符。
_EXEMPT_HITS = {}


def check_dead_exemptions():
    """每条豁免条目须在本次扫描中被实际命中，否则即死豁免。"""
    bad = []
    for layer in LAYER_EXEMPT:
        if ("LAYER_EXEMPT", layer) not in _EXEMPT_HITS:
            bad.append(f"LAYER_EXEMPT[{layer}]: 本次扫描**零命中** —— "
                       f"路径写法与扫描内部形态不符，或文件已不存在。"
                       f"死豁免既不保护什么，也掩盖了它声称保护的东西")
    for key in SERVER_ALLOWLIST:
        if ("SERVER_ALLOWLIST", key) not in _EXEMPT_HITS:
            bad.append(f"SERVER_ALLOWLIST[{key}]: 本次扫描**零命中**。"
                       f"注意：该豁免只在被豁免文件确实 import 了 http/socket 时"
                       f"才会被命中 —— 若该文件已不再持有服务端监听面，"
                       f"应删除本条而非留着")
    return bad

# ── B-2c（G4 修复）：豁免理由须可机检 ────────────────────────────
# 每条 LAYER_EXEMPT 条目须在此带一条可执行断言（路径 → 说明 → 判定）。
# 判定对豁免文件的源码文本执行；不成立即 FAIL —— 豁免理由不再靠人读注释。
# 变异注入：把任一断言改成不成立（如删掉 assert_writer、删掉 socket），
# 本守卫必须转红。
EXEMPT_ASSERTS = {
    "L2_persistence": [
        ("backend/app/repository.py", "persistence 层须引入 sqlalchemy",
         lambda src: "sqlalchemy" in src),
        ("backend/app/jobs.py", "persistence 层须引入 sqlalchemy",
         lambda src: "sqlalchemy" in src),
    ],
    "migrations": [
        ("backend/migrations", "迁移文件须引入 alembic",
         lambda src: "alembic" in src),
    ],
    "L3_fetch": [
        ("backend/tools/import_guard.py", "取数层须持有网络面（NETWORK_LIBS 之一）",
         lambda src: any(lib in src for lib in NETWORK_LIBS)),
        ("backend/tools/sse_adapter.py", "取数层须持有网络面（NETWORK_LIBS 之一）",
         lambda src: any(lib in src for lib in NETWORK_LIBS)),
        ("backend/tools/macro_adapter.py", "取数层须持有网络面（NETWORK_LIBS 之一）",
         lambda src: any(lib in src for lib in NETWORK_LIBS)),
        ("backend/tools/akshare_adapter.py",
         "AKShare 适配器的网络面经 curl_cffi_interdict（受管拦截）",
         lambda src: "curl_cffi_interdict" in src),
        ("backend/tools/cninfo_adapter.py", "取数层须持有网络面（NETWORK_LIBS 之一）",
         lambda src: any(lib in src for lib in NETWORK_LIBS)),
    ],
    "tools_internal": [
        ("backend/tools/migration_check.py", "内部工具须经 subprocess 驱动 alembic",
         lambda src: "subprocess" in src),
        ("backend/tools/vertical_smoke.py", "内部工具须经 subprocess 驱动",
         lambda src: "subprocess" in src),
    ],
    "supply_chain_refresh": [
        ("backend/tools/wheel_manifest_refresh.py",
         "供应链刷新工具须有网络面（出网范围 = pypi 元数据端点）",
         lambda src: any(lib in src for lib in NETWORK_LIBS)),
    ],
    "L7_publish": [
        ("backend/app/publish_engine.py",
         "发布层写 release/approval/pointer 须经 assert_writer（B-2b (i)）",
         lambda src: "assert_writer" in src),
    ],
    "offline_probe": [
        ("backend/app/network_probe.py", "断网探针须使用 socket（真 TCP 断言）",
         lambda src: "import socket" in src),
    ],
}


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


def check_kernel_no_probe_import(ROOT):
    """B-2a：可信内核不得 import network_probe —— 断网探针只经回调注入。

    该不变量独立于 LAYER_EXEMPT（offline_probe 豁免的理由即「内核不
    import 本模块」）：backend/app/ 下除 network_probe.py 自身外的任何
    文件（含 L7_publish 等已豁免层）import network_probe 即 FAIL。
    变异注入：publish_engine.py 顶部加 import network_probe → 必须 FAIL。
    """
    bad = []
    appdir = os.path.join(ROOT, "backend", "app")
    for dp, _, fns in os.walk(appdir):
        for fn in fns:
            if not fn.endswith(".py"):
                continue
            fp = os.path.join(dp, fn)
            rel = os.path.relpath(fp, ROOT).replace(os.sep, ".")
            if rel == "backend.app.network_probe":
                continue
            if ".tests." in rel:
                continue
            try:
                tree = ast.parse(open(fp, encoding="utf-8").read(), filename=fp)
            except (OSError, SyntaxError):
                continue
            for node in ast.walk(tree):
                for mod in module_name_of(node):
                    if mod.split(".")[0] == "network_probe":
                        bad.append(f"{rel}: B-2a 可信内核引入 network_probe —— "
                                   f"探针只得以回调注入（offline_probe 豁免边界）")
    return bad


def check_exemption_asserts(ROOT):
    """B-2c：逐条执行 EXEMPT_ASSERTS —— 豁免理由须可机检，不成立即 FAIL。"""
    bad = []
    for layer, asserts in EXEMPT_ASSERTS.items():
        for pth, desc, pred in asserts:
            if pth.endswith(".py"):
                fp = os.path.join(ROOT, pth)
                if not os.path.exists(fp):
                    bad.append(f"{layer} {pth}: 断言对象文件不存在")
                    continue
                if not pred(open(fp, encoding="utf-8").read()):
                    bad.append(f"{layer} {pth}: 豁免断言不成立 —— {desc}")
            else:
                d = os.path.join(ROOT, pth)
                if not os.path.isdir(d):
                    bad.append(f"{layer} {pth}: 断言对象目录不存在")
                    continue
                n = 0
                for dp, _, fns in os.walk(d):
                    for fn in fns:
                        if not fn.endswith(".py"):
                            continue
                        n += 1
                        if not pred(open(os.path.join(dp, fn), encoding="utf-8").read()):
                            bad.append(f"{layer} {pth}/{fn}: 豁免断言不成立 —— {desc}")
                if n == 0:
                    bad.append(f"{layer} {pth}: 豁免断言无对象可检查（⑨）")
    return bad


def main() -> int:
    bad = check_kernel_no_probe_import(ROOT)
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
                _EXEMPT_HITS.setdefault(("LAYER_EXEMPT", layer), set()).add(rel)
                continue  # 显式层豁免（精确路径，非子串）；其理由由 B-2c 断言承担
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
                        _EXEMPT_HITS.setdefault(("SERVER_ALLOWLIST", rel),
                                                set()).add(rel)
                        continue  # 服务端监听面（非出网）
                    if base in EGRESS_MODULES or base in exempt_modules and base in EGRESS_MODULES:
                        bad.append(f"{rel}: 传递性出网 —— import 已豁免的出网模块 {mod}")
                    if base in NETWORK_LIBS:
                        bad.append(f"{rel}: M1/M4 引入网络库 {mod}")
                    if base in DB_LIBS:
                        bad.append(f"{rel}: M3 引入 persistence {mod}")
                    if base in APPROVAL_WRITERS:
                        bad.append(f"{rel}: M5 直写批准/发布对象 {mod}")
    bad += check_exemption_asserts(ROOT)
    # **须在扫描之后调用** —— _EXEMPT_HITS 由扫描填充，提前调用必然全零
    bad += check_dead_exemptions()
    if bad:
        print("❌ 架构导入边界违规：")
        for b in bad:
            print("  -", b)
        return 1
    exempt_n = len(_all_exempt_files(ROOT))
    print(f"✅ 检查对象 {checked} 个 .py，无违规（M1—M7 骨架级 + B-2a 内核不引探针）"
          f"；豁免文件 {exempt_n} 个（B-2c 断言逐条通过；"
          f"{len(_EXEMPT_HITS)} 条豁免本次均被实际命中，无死豁免）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
