#!/usr/bin/env python3
"""fixture_shape_check.py —— 夹具形状须与真实契约一致（OI-PF-129）。

第十三轮 V-2 的实例：`_matrix_fixture.py` 用**守卫词汇** "FETCH" 作 actions 键，
真实 `contracts/rights_matrix.json` 用**领域键**（automated_acquisition 等）。
夹具恰好匹配了坏掉的那个查询，于是 OI-PF-128（词汇不相交 → 查询永不命中 →
恒 UNKNOWN）在 195 个测试里完全不可见 —— **测试在假状态下全部通过**。

同类先例：OI-PF-101（镜像未装依赖）· OI-PF-106（job 表无迁移，测试用
create_all 绕过）。三次都是「测试路径／夹具形状 ≠ 真实路径／契约形状」。

本守卫断言：**夹具用到的每一个结构键，真实契约里都出现过**。
反向不要求（夹具是子集，不必覆盖全部真实键）。键不认识 = 夹具在验证一个
现实中不存在的形状 —— 无论测试是绿是红，都不构成证据。

用法：python3 backend/tools/fixture_shape_check.py [repo_root]
"""
import json
import os
import sys

ROOT = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else ".")
sys.path.insert(0, os.path.join(ROOT, "backend", "tests"))

# 每条：夹具名 → (取夹具键集, 取真实键集, 说明)
# 清单显式列出，不做自动发现 —— 自动发现会在夹具改名时静默缩小检查范围。
CHECKS = []


def _matrix_keys(doc):
    ks = set()
    for s in doc.get("data_sources", []):
        ks |= set(s.get("actions", {}) or {})
    return ks


def _load_real(rel):
    with open(os.path.join(ROOT, rel), encoding="utf-8") as fh:
        return json.load(fh)


def check_rights_matrix():
    from _matrix_fixture import MATRIX
    real = _load_real("contracts/rights_matrix.json")
    return ("_matrix_fixture.MATRIX.actions",
            _matrix_keys(MATRIX), _matrix_keys(real),
            "contracts/rights_matrix.json 的 actions 键集")


def check_matrix_source_fields():
    """源级字段名（source_key / license 等）也须同形。"""
    from _matrix_fixture import MATRIX
    real = _load_real("contracts/rights_matrix.json")
    fk = set().union(*[set(s) for s in MATRIX.get("data_sources", [])]) \
        if MATRIX.get("data_sources") else set()
    rk = set().union(*[set(s) for s in real.get("data_sources", [])]) \
        if real.get("data_sources") else set()
    return ("_matrix_fixture.MATRIX.data_sources[].字段名", fk, rk,
            "contracts/rights_matrix.json 的源级字段名")


def check_action_map():
    """rights_action_map 的候选键须全部是真实矩阵里出现过的领域键 ——
    这正是 OI-PF-128 的根因所在：两套词汇不相交而无人察觉。"""
    amap = _load_real("contracts/rights_action_map.json")
    real = _matrix_keys(_load_real("contracts/rights_matrix.json"))
    cands = set()
    for v in amap.get("map", {}).values():
        cands |= set(v)
    return ("rights_action_map.json 的候选键", cands, real,
            "contracts/rights_matrix.json 的 actions 键集")


CHECKS = [check_rights_matrix, check_matrix_source_fields, check_action_map]


def main() -> int:
    bad, checked = [], 0
    for fn in CHECKS:
        try:
            name, used, real, src = fn()
        except Exception as e:
            bad.append(f"{fn.__name__}: 执行失败 {type(e).__name__}: {e} —— "
                       f"**判红而非跳过**（检查不了就不算检查过）")
            continue
        checked += 1
        unknown = sorted(used - real)
        if unknown:
            bad.append(f"**{name} 使用了真实契约中不存在的键**: {unknown}；"
                       f"真实键集来自 {src}。夹具在验证一个现实中不存在的形状 —— "
                       f"测试无论绿红都不构成证据（E-FIXTURE-001 / OI-PF-129）")
        if not used:
            bad.append(f"{name}: 键集为空 —— 无对象可比对，不得算作通过"
                       f"（E-FIXTURE-002）")
    for b in bad:
        print(f"  - {b}")
    if bad:
        print(f"❌ 夹具形状与真实契约不一致 {len(bad)} 处")
        return 1
    print(f"✅ 夹具形状与真实契约一致：检查对象 {checked} 组键集")
    return 0


if __name__ == "__main__":
    sys.exit(main())
