#!/usr/bin/env python3
"""research_data_interdict.py —— 研究数据隔离 + 研究产出禁入（**两半合并**）。

本工具此前存在**两个同名但覆盖面不同的实现**，PR #51 关闭后只有前者在 main 上：

  甲（A-2a，原 main 版）  只扫 backend/tests/fixtures —— 检查对象 1 个 fixture
                          管「合成 fixture 须自证、且不得混入真实定位符」
  乙（OI-PF-022，PR #51） 扫全部已跟踪文件（166 个）
                          管「研究产出不得进入公开仓库」

**实测证明二者不可互相替代**：把真实 golden-baselines/600089.json 提交进仓库，
甲版报「✅ 合格：检查对象 1 个 fixture」**放行**，乙版报「❌ R3 路径特征」拦下。
关闭 PR #51 时按「实质满足」判定 B-3b —— 该判定错误，实质恰恰没满足。

本版合并两半，**各自独立报数**（规则 ⑨：两个数不得合并成一个）。

### 甲 · fixture 合成性（A-2a）
  · backend/tests/fixtures/ 下的 .json 须带顶层 SYNTHETIC_FIXTURE=true
  · 合成 fixture 不得含真实形态 locator（.xlsx/.pdf/http/交易所/巨潮/stats.gov）
  · fixtures/ 目录不存在 = 无对象可检查 → 判红

### 乙 · 研究产出禁入（OI-PF-022 / VD-20 = 仅内部）
  R1  台账制品结构：同时含 baseline_id 与 back_source
  R2  真实财报形态数值（排除标的代码本身、占位值、定位符编号）
  R3  研究产出目录路径：golden-baselines/ · evidence-packs/ · candidates/
  豁免：本文件自身；backend/tests/ 下自证 SYNTHETIC_FIXTURE 的文件

用法：python3 backend/tools/research_data_interdict.py [repo_root]
"""
import json
import os
import re
import subprocess
import sys

ROOT = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else ".")
FIXTURES = os.path.join(ROOT, "backend", "tests", "fixtures")

REAL_LOCATOR = re.compile(r"(\.xlsx|\.pdf|\.html?|https?://|交易所|巨潮|stats\.gov)",
                          re.IGNORECASE)


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


def _check_fixtures():
    """甲 · A-2a：fixture 合成性。返回 (bad, checked, exempt)。"""
    bad, checked, exempt = [], 0, []
    if not os.path.isdir(FIXTURES):
        return (["fixtures/ 目录不存在 —— 无对象可检查，判红（A-2a）"], 0, [])
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
            if REAL_LOCATOR.search(json.dumps(d, ensure_ascii=False)):
                bad.append(f"{fn}: 合成 fixture 含真实形态 locator "
                           f"（xlsx/pdf/http/交易所）—— 冒充真实数据的风险")
            continue
        bad.append(f"{fn}: 缺 SYNTHETIC_FIXTURE 标记 —— 合成数据冒充真实数据（A-2a）")
    return bad, checked, exempt


def _check_interdiction():
    """乙 · OI-PF-022：研究产出禁入。返回 (bad, checked, exempt)。"""
    bad, checked, exempt = [], 0, 0
    try:
        files = tracked_files()
    except RuntimeError as e:
        return ([str(e)], 0, 0)
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
        # R1 须区分**数据实例**与 **schema/类定义**。#51 那轮已把 R2 收窄，
        # 但 R1 漏了同样处理 —— golden_baseline.py 是类定义（baseline_id 是
        # 构造参数名、back_source 是实例属性），不是台账制品。
        # 判据：须是 JSON 数据形态（键带引号且有值），而非 Python 标识符。
        # 仍不够：golden_baseline.py 的 to_dict() 里有 {"baseline_id": self.baseline_id}
        # —— 带引号的键，但值是 **self.xxx**（序列化代码），不是字面量数据。
        # **第三次收窄**：R1 只在文件能被解析为 JSON 且顶层含这两个键时命中。
        # 解析成功 = 它就是数据文件，不是代码。这个判据无法被「代码里恰好有
        # 这两个字符串」触发。
        _is_data = False
        if rel.endswith(".json"):
            try:
                _obj = json.loads(txt)
                _is_data = isinstance(_obj, dict) and "baseline_id" in _obj and "back_source" in _obj
            except Exception:
                _is_data = False
        if _is_data:
            bad.append(f"**{rel}**: R1 台账制品结构（JSON 中同时含 "
                       f'"baseline_id" 与 "back_source" 键）—— golden-baseline 形态')
            continue
        _ev = _looks_like_real_data(txt)
        if _ev:
            bad.append(f"**{rel}**: R2 研究数据形态 —— {_ev} "
                       f"（VD-20 = 仅内部；OI-PF-030：公开仓库无数据对象）")
    return bad, checked, exempt


def main() -> int:
    bad_a, n_a, ex_a = _check_fixtures()
    bad_b, n_b, ex_b = _check_interdiction()
    for b in bad_a + bad_b:
        print(f"  - {b}")
    if bad_a or bad_b:
        print(f"❌ 违规 {len(bad_a)} 项（甲·fixture 合成性）+ "
              f"{len(bad_b)} 项（乙·研究产出禁入）")
        return 1
    print(f"✅ 研究数据隔离与禁入合格："
          f"甲 检查 {n_a} 个 fixture（豁免 {len(ex_a)}：{', '.join(ex_a) or '无'}）· "
          f"乙 检查 {n_b} 个已跟踪文本文件（豁免 {ex_b}）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
