#!/usr/bin/env python3
"""写权矩阵的覆盖守卫（OI-PF-180）。

`contracts/writers.json` 声明了 20 类对象的写权，而执行侧此前只有 7 处
字面量断言 —— 其余各行的契约在代码里**没有落点**。更要紧的是：
`jobs.py` 顶部曾写「本层为调度原语，调用方须自行按 contracts/writers.json
断言」，而 `backend/app` 内**没有任何调用方做过该断言** ——
责任下推给了一个不存在的接收方。

判据（**默认拒绝**：新增一行而未标注即判红）：

  W-1  每行须显式标 `enforcement` ∈ {ENFORCED, NOT_APPLICABLE}
  W-2  标 NOT_APPLICABLE 的须给 `enforcement_reason`（非空）
  W-3  标 NOT_APPLICABLE 的**不得**有 ORM 表 —— 有表即能落库，不能算不适用
  W-4  标 ENFORCED 的须在代码里真有断言点：字面量 `assert_writer("X"`
       或经 `_OBJ_TYPE` 动态查表（有 ORM 表即视为经 cas_insert 覆盖）

W-3 是本守卫的要害：`NOT_APPLICABLE` 的唯一正当理由是**没有落库路径**。
若一个类型有表却标不适用，那是用标注掩盖缺口 —— 与「改判据以适配结论」同形。

用法：python3 backend/tools/writer_coverage_check.py [repo_root]
"""
import json
import os
import re
import sys

VALID = {"ENFORCED", "NOT_APPLICABLE"}


def orm_tables(root):
    """扫 backend/app 下所有 __tablename__ —— 有表即能落库。"""
    out = set()
    app = os.path.join(root, "backend", "app")
    for fn in sorted(os.listdir(app)):
        if not fn.endswith(".py"):
            continue
        src = open(os.path.join(app, fn), encoding="utf-8").read()
        out |= set(re.findall(r'__tablename__\s*=\s*["\']([a-z_]+)["\']', src))
    return out


def literal_asserts(root):
    """字面量断言点 `assert_writer("X"` —— 只能看见静态那部分。"""
    out = set()
    app = os.path.join(root, "backend", "app")
    for fn in sorted(os.listdir(app)):
        if not fn.endswith(".py"):
            continue
        src = open(os.path.join(app, fn), encoding="utf-8").read()
        out |= set(re.findall(r'assert_writer\(\s*["\']([a-z_]+)["\']', src))
    return out


def main() -> int:
    root = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else ".")
    m = json.load(open(os.path.join(root, "contracts", "writers.json"),
                       encoding="utf-8"))["matrix"]
    tables = orm_tables(root)
    lits = literal_asserts(root)
    bad = []

    for k, row in m.items():
        e = row.get("enforcement")
        if e not in VALID:                                    # W-1
            bad.append(f"W-1: {k!r} 的 enforcement = {e!r} —— "
                       f"须显式为 {VALID} 之一，不得留白（默认拒绝）")
            continue
        if e == "NOT_APPLICABLE":
            if not str(row.get("enforcement_reason") or "").strip():   # W-2
                bad.append(f"W-2: {k!r} 标 NOT_APPLICABLE 但无 enforcement_reason")
            if k in tables:                                    # W-3
                bad.append(f"W-3: {k!r} 标 NOT_APPLICABLE，但 backend/app 内**有 "
                           f"__tablename__ = {k!r}** —— 有表即能落库，"
                           f"不能算执行侧不适用。用标注掩盖缺口与「改判据以"
                           f"适配结论」同形")
        else:                                                  # W-4
            if k not in lits and k not in tables:
                bad.append(f"W-4: {k!r} 标 ENFORCED，但既无字面量 assert_writer("
                           f"{k!r} 也无 ORM 表（故不经 cas_insert 的动态查表）"
                           f"—— 断言点不存在")

    if bad:
        print("❌ 写权矩阵覆盖违规（OI-PF-180）：")
        for b in bad:
            print("  - " + b)
        return 1
    n_e = sum(1 for r in m.values() if r["enforcement"] == "ENFORCED")
    n_n = len(m) - n_e
    print(f"✅ 检查对象 {len(m)} 行写权契约："
          f"ENFORCED {n_e}（字面量 {len(lits)} 类 + ORM 表 {len(tables & set(m))} 类，"
          f"经 cas_insert 动态查表）· NOT_APPLICABLE {n_n}（均有理由且无 ORM 表）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
