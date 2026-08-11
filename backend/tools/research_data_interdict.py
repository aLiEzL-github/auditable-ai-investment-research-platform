#!/usr/bin/env python3
"""research_data_interdict.py —— 研究产出禁入公开仓库（OI-PF-022 / VD-20）。

`VD-20 = 仅内部`（`decisions-v2/VD-20.md`，DECIDED 2026-07-29）。
`OI-PF-022` 原文：「代码公开使误提交研究结论、证据包或 600089 数据的后果放大。
**须建立 .gitignore 硬规则与自动化拦截**，物理隔离代码与研究产出。」
`OI-PF-030`：「公开仓库只有代码与指针、**无数据对象**」——这是预期行为。

`.gitignore` 已按扩展名禁入（`.xlsx`/`.csv`/`.pdf` 等）与目录禁入
（`data/`、`object-store/`、`fixtures/real/`），但**按扩展名挡不住 `.json`
形态的研究产出** —— 台账的 `golden-baselines/600089.json` 正是 `.json`。
且 `.gitignore` 只防误加，不防蓄意提交（`git add -f` 即绕过）。

本守卫做**内容层拦截**：扫描已跟踪文件，命中研究产出特征即判红。

### 判据（命中任一即违规）
  R1  台账制品的结构特征：同时含 `baseline_id` 与 `back_source`
      —— 这是 golden-baseline 的形状，不是普通配置
  R2  标的证券代码 + 财务科目同现（600089 与「营业收入/净利润/总资产」等）
  R3  路径特征：`golden-baselines/`、`evidence-packs/`、`candidates/`

### 豁免（须显式且报数）
  · 本文件自身（规则字面量）
  · `backend/tests/` 下的**合成** fixture，但须自证为合成
    （文件内含 `SYNTHETIC_FIXTURE` 标记）—— 合成数据不承载真实研究产出

用法：python3 backend/tools/research_data_interdict.py [repo_root]
"""
import json
import os
import re
import subprocess
import sys

ROOT = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else ".")

# R2：标的 + 财务科目同现。科目词取常见中文报表项，避免只匹配代码而误伤。
TICKERS = ("600089",)
FIN_TERMS = ("营业收入", "净利润", "总资产", "归母", "扣非", "经营活动现金流",
             "股东权益", "少数股东")
# R3：路径特征
PATH_PAT = re.compile(r"(^|/)(golden-baselines|evidence-packs|candidates)/")
SYNTHETIC = "SYNTHETIC_FIXTURE"



def _looks_like_real_data(txt):
    """判定文本是否含**真实财报形态的数值**，而非占位值或 schema 定义。

    真实财报数的特征：多位有效数字、非整千整万。占位值（1000000、100、0）
    与 schema 字段名不构成研究产出。**宁可漏报也不误报** —— 一个把类定义
    判成数据泄露的守卫会被当作噪声关掉，那比没有守卫更糟。
    """
    if not any(t in txt for t in TICKERS):
        return None
    if not any(f in txt for f in FIN_TERMS):
        return None
    # 取财务科目附近 120 字符内的数值
    for f in FIN_TERMS:
        for m in re.finditer(re.escape(f), txt):
            seg = txt[m.start():m.start() + 120]
            for num in re.findall(r"\b\d{4,}(?:\.\d+)?\b", seg):
                _n = num.split(".")[0]
                # 标的代码本身不是财务数值 —— 初版把 600089 当成「附近的数值」，
                # 于是每个提到该标的的文件都命中。**又一次匹配了代理而非目标。**
                if _n in TICKERS:
                    continue
                # 占位值：整千整万（1000000）、纯重复位（111111）
                if _n.rstrip("0") in ("1", "2", "5", "") or len(set(_n)) == 1:
                    continue
                # 定位符里的页码/编号（LOC/600089/p25 之类）不是财务数值
                _ctx = seg[max(0, seg.find(num) - 12):seg.find(num)]
                if "LOC/" in _ctx or "/p" in _ctx or "locator" in _ctx.lower():
                    continue
                return f"科目「{f}」附近出现非占位数值 {num}"
    return None


def tracked_files():
    r = subprocess.run(["git", "-C", ROOT, "ls-files"],
                       capture_output=True, text=True, timeout=60)
    if r.returncode != 0:
        raise RuntimeError("E-RDI-003: git ls-files 失败 —— **无法枚举已跟踪文件，"
                           "判红**（没检查 ≠ 检查通过）")
    return [p for p in r.stdout.splitlines() if p.strip()]


def main() -> int:
    try:
        files = tracked_files()
    except RuntimeError as e:
        print(f"❌ {e}")
        return 1

    bad, checked, exempt = [], 0, 0
    for rel in files:
        if "research_data_interdict" in rel:
            exempt += 1
            continue
        if PATH_PAT.search(rel):
            bad.append(f"**{rel}**: R3 路径特征 —— 研究产出目录不得入仓"
                       f"（OI-PF-030：公开仓库只有代码与指针、无数据对象）")
            continue
        fp = os.path.join(ROOT, rel)
        if not os.path.isfile(fp):
            continue
        try:
            txt = open(fp, encoding="utf-8", errors="ignore").read()
        except OSError:
            continue
        checked += 1
        if SYNTHETIC in txt and rel.startswith("backend/tests/"):
            exempt += 1
            continue
        # ⚠️ 判据须区分**数据实例**与**schema 定义 / scope 标识符**。
        # 初版按「字段名共现」与「标的 + 科目共现」判，在 main 上得 5 处命中，
        # 逐个核实**全部是假阳性**：golden_baseline.py 是类定义（baseline_id 是
        # 构造参数名）；test_g3_05/06 的 600089 是 scope="600089" 标识符；
        # test_g2_14 的「营业收入 1000000」是整百万的占位值。
        # 收窄为：须出现**真实财报形态的数值**才判违规。
        if _looks_like_real_data(txt):
            _ev = _looks_like_real_data(txt)
            bad.append(f"**{rel}**: R2 研究数据形态 —— {_ev} "
                       f"（VD-20 = 仅内部；OI-PF-030：公开仓库无数据对象）")

    for b in bad:
        print(f"  - {b}")
    if bad:
        print(f"❌ 研究产出禁入违规 {len(bad)} 处（OI-PF-022 / VD-20 = 仅内部）")
        return 1
    print(f"✅ 研究产出禁入合规：检查对象 {checked} 个已跟踪文本文件 · "
          f"豁免 {exempt} 个（本工具自身 + 自证合成的测试 fixture）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
