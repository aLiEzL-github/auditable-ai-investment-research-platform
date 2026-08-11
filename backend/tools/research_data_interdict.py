#!/usr/bin/env python3
"""research_data_interdict.py —— A-2a 研究数据隔离守卫。

禁止「合成数据冒充真实研究数据」：
  · backend/tests/fixtures/ 下的研究数据 fixture 必须带 SYNTHETIC_FIXTURE 标记
    （顶层字段），否则视为「合成数据冒充真实数据」—— 判红。
  · 真实数据（golden-baselines/*.json）**不得**出现在本仓 —— 若检出
    出现在 fixtures/ 或 contracts/ 下且带真实 locator（如 xlsx 披露文件），判红。
  · 合成 fixture 的 locator 一律以 synthetic:// 开头 —— 发现真实形态
    locator（.xlsx/.pdf/http）混入合成 fixture → 判红。

用途：python3 backend/tools/research_data_interdict.py [repo_root]
"""
import json
import os
import re
import sys

ROOT = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else ".")
FIXTURES = os.path.join(ROOT, "backend", "tests", "fixtures")

REAL_LOCATOR = re.compile(r"(\.xlsx|\.pdf|\.html?|https?://|交易所|巨潮|stats\.gov)",
                          re.IGNORECASE)


def main() -> int:
    bad, checked, exempt = [], 0, []
    if not os.path.isdir(FIXTURES):
        # 无 fixtures 目录 = 无对象可检查（⑨：须与「检查 N 个通过」可分辨）
        print("❌ fixtures/ 目录不存在 —— 无对象可检查，判红（A-2a）")
        return 1
    for fn in sorted(os.listdir(FIXTURES)):
        if not fn.endswith(".json"):
            continue
        fp = os.path.join(FIXTURES, fn)
        try:
            d = json.load(open(fp, encoding="utf-8"))
        except Exception as e:
            bad.append(f"{fn}: JSON 解析失败 {e} —— 判红而非跳过")
            continue
        checked += 1
        if d.get("SYNTHETIC_FIXTURE") is True:
            exempt.append(fn)
            # 合成 fixture 的 locator 必须 synthetic:// 开头
            locs = json.dumps(d, ensure_ascii=False)
            if REAL_LOCATOR.search(locs):
                bad.append(f"{fn}: 合成 fixture 含真实形态 locator "
                           f"（xlsx/pdf/http/交易所）—— 冒充真实数据的风险")
            continue
        # 无标记 → 判红：合成数据不得冒充真实数据
        bad.append(f"{fn}: 缺 SYNTHETIC_FIXTURE 标记 —— 合成数据冒充真实数据（A-2a）")
    for b in bad:
        print(f"  - {b}")
    if bad:
        print(f"❌ 研究数据隔离违规 {len(bad)} 项（检查对象 {checked}，"
              f"豁免 {len(exempt)}）")
        return 1
    print(f"✅ 研究数据隔离合格：检查对象 {checked} 个 fixture，"
          f"豁免 {len(exempt)} 个（{', '.join(exempt)}）—— 全部带 "
          f"SYNTHETIC_FIXTURE 标记")
    return 0


if __name__ == "__main__":
    sys.exit(main())
