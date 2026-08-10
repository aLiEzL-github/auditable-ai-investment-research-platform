#!/usr/bin/env python3
"""rights_action_map_check.py —— OI-PF-128 守卫：动作映射覆盖面。

断言 RightsGuard.ACTIONS 中每个动作在 rights_action_map.json 中都有映射，
且**每个动作至少有一个源命中** —— 否则该动作的矩阵查询永不生效，
守卫看似接上、实际那条路走不到（OI-PF-128 的原始形态）。

用法：python3 backend/tools/rights_action_map_check.py [repo_root]
"""
import json
import os
import sys

ROOT = os.path.abspath(sys.argv[1]) if len(sys.argv) > 1 else "."
sys.path.insert(0, os.path.join(ROOT, "backend", "app"))
from rights_guard import ACTIONS  # noqa: E402


def main() -> int:
    amp = json.load(open(os.path.join(ROOT, "contracts", "rights_action_map.json"),
                         encoding="utf-8"))["map"]
    mx = json.load(open(os.path.join(ROOT, "contracts", "rights_matrix.json"),
                        encoding="utf-8"))["data_sources"]
    bad, checked = [], 0
    for act in ACTIONS:
        cands = amp.get(act)
        if not cands:
            bad.append(f"动作 {act} 在 rights_action_map.json 中无映射")
            continue
        hits = [s.get("source_key") for s in mx
                if any(c in s.get("actions", {}) for c in cands)]
        checked += 1
        if not hits:
            bad.append(f"动作 {act} 的候选键 {cands} 在矩阵中**零源命中** —— 该动作查询永不生效")
    # 反向：矩阵中每个源，对每个动作至少要么命中要么显式缺席（缺席会被 E-G2-03-005 拦）
    for b in bad:
        print(f"  - {b}")
    if bad:
        print(f"❌ 动作映射覆盖面不合格（{len(bad)} 项）")
        return 1
    print(f"✅ 动作映射覆盖面合格：检查对象 {checked} 个动作 × {len(mx)} 个源")
    return 0


if __name__ == "__main__":
    sys.exit(main())
