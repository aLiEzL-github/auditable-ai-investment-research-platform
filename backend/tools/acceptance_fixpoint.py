#!/usr/bin/env python3
"""验收包生成的不动点驱动器 —— 消除「生成器 ↔ 台账审计」的循环。

## 循环是什么

  GateN 验收包的结论  ←依赖─  台账审计是否全绿
  台账审计的 S3 检查  ←依赖─  GateN 验收包的 ② 计数

于是**同一份代码、同一份台账，跑一遍与跑两遍会得到不同结论**：
2026-08-12 实测 —— G6A/G6C 首次生成报 NOT_READY，理由是「台账审计未全绿」，
而审计红的原因正是那几个包还没被重新生成；再生成一次即转 READY。

反向也会出错：若先生成包（S3 转绿）、再生成一次拿到 READY，
那个 READY 里「台账审计全绿」这条证据，**是被本次生成动作自己制造出来的**。

## 为什么不靠删检查来消除

两条捷径都能让矛盾消失：
  (a) 生成器不再把「台账审计全绿」当阻断项；
  (b) S3 对未签包不参与 ② 计数比对。
**两者都是靠删掉一条真实的检查来换取自洽。** 本项目已拒绝过同形的做法
（ADR-023 §1.1：不用 EXCLUDE 消解债务趋势义务）。故取第三条：
保留两边检查，把「须跑到不动点」显式化并**可验证**。

## 不动点判据

反复「生成全部包 → 跑审计」，直到某一轮满足：
  · 台账审计全绿，**且**
  · 本轮全部包的 substantive_sha256 与上一轮逐字相同
后者是关键 —— 只看「审计变绿」不够：审计可能因本轮生成而变绿，
而包内容仍在变。两个条件同时成立才说明真的收敛了。

达不到不动点即**判红退出**，不静默接受最后一轮的结果。

## 影响范围

只有**未签且可重生成**的包受循环影响（S3 对已签包比「签署当刻快照」，
不比当前值 —— OI-PF-146 裁定 (ii)）。已签包本工具不重新生成，
重新生成会改字节而触发 A §10.3。

用法：python3 backend/tools/acceptance_fixpoint.py [--max-pass N]
环境：PORTFOLIO_ROOT 指向台账（缺省 /Users/li/Documents/Claudetext/portfolio）
"""
import argparse
import hashlib
import json
import os
import re
import subprocess
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
PORTFOLIO = (os.environ.get("PORTFOLIO_ROOT")
             or "/Users/li/Documents/Claudetext/portfolio")


def _signed_objects():
    """ACTIVE 签署所锚定的对象 —— 这些包**不得**重新生成。"""
    out = set()
    d = os.path.join(PORTFOLIO, "gate-records")
    if not os.path.isdir(d):
        return out
    for f in sorted(os.listdir(d)):
        if not f.endswith(".json"):
            continue
        r = json.load(open(os.path.join(d, f), encoding="utf-8"))
        if r.get("signature_status") == "ACTIVE":
            o = (r.get("subject") or {}).get("object")
            if o:
                out.add(os.path.basename(o))
    return out


def _generators():
    """(标签, 命令, 产物文件名) —— 仓库侧与台账侧各自枚举。"""
    g = []
    for p in sorted(os.listdir(os.path.join(ROOT, "backend", "tools"))):
        m = re.match(r"^build_gate(\w+)_acceptance\.py$", p)
        if m:
            g.append((f"repo:{p}",
                      [sys.executable, os.path.join("backend", "tools", p)],
                      f"Gate{m.group(1).upper()}-验收包.md", ROOT))
    lt = os.path.join(PORTFOLIO, "tools")
    if os.path.isdir(lt):
        for p in sorted(os.listdir(lt)):
            m = re.match(r"^build_gate(\w+)_acceptance\.py$", p)
            if m:
                g.append((f"ledger:{p}",
                          [sys.executable, os.path.join("tools", p)],
                          f"Gate{m.group(1).upper()}-验收包.md", PORTFOLIO))
    return g


# 与台账 audit_session.py 的 _SUB_EXCLUDE、各生成器的 EXCLUDE 同一份清单。
# 剔除的是与实质无关的易变行。
_SUB_EXCLUDE = ("生成时刻", "实测时刻", "main 最新 CI run", "run = ", "ruleset: ",
                "g1-08-2026", "_mut-", "sparseimage", "备份目录 = ", "  g1-08-",
                "substantive_sha256", "合计", "独立审计:", "v2.0 基线:")


def _subs():
    """当前全部验收包的实质哈希。

    优先取包内自声明的 substantive_sha256；**无该行时按 _SUB_EXCLUDE 就地重算**，
    不退回全文哈希 —— 全文含「生成时刻」，每轮必变，会使不动点**永远达不到**。
    初版就是这么写的：Gate0-验收包.md 不写自声明行，于是它每轮都被判为
    「仍在变」，四轮跑完报「未达到不动点」，而真正在变的只有一个时间戳。
    **一个把每次都判为不稳定的稳定性检查，等于没有检查。**
    """
    out = {}
    for f in sorted(os.listdir(PORTFOLIO)):
        if not re.match(r"^Gate\w+-验收包\.md$", f):
            continue
        txt = open(os.path.join(PORTFOLIO, f), encoding="utf-8").read()
        m = re.search(r"^substantive_sha256 = (\w+)", txt, re.M)
        if m:
            out[f] = m.group(1)
            continue
        keep = [x for x in txt.splitlines()
                if not any(e in x for e in _SUB_EXCLUDE)]
        out[f] = hashlib.sha256("\n".join(keep).encode("utf-8")).hexdigest()
    return out


def _audit():
    r = subprocess.run([sys.executable, "tools/audit_session.py"],
                       cwd=PORTFOLIO, capture_output=True, text=True)
    last = (r.stdout.strip().splitlines() or [""])[-1]
    m = re.search(r"PASS (\d+) / FAIL (\d+)", last)
    return (int(m.group(2)) == 0 if m else False), last


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-pass", type=int, default=4)
    a = ap.parse_args()

    signed = _signed_objects()
    gens = [g for g in _generators() if g[2] not in signed]
    skipped = [g[2] for g in _generators() if g[2] in signed]
    print(f"生成器 {len(gens)} 个；跳过已签包 {len(skipped)} 个"
          f"（{', '.join(sorted(skipped)) or '无'}）—— "
          f"已签包重新生成会改字节而触发 A §10.3")

    prev = _subs()
    for n in range(1, a.max_pass + 1):
        for label, cmd, _f, cwd in gens:
            r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
            if r.returncode != 0:
                print(f"❌ 第 {n} 轮：{label} 退出码 {r.returncode}\n"
                      f"{(r.stderr or r.stdout)[-400:]}")
                return 1
        ok, line = _audit()
        cur = _subs()
        stable = (cur == prev)
        print(f"  第 {n} 轮：审计{'全绿' if ok else '**未全绿**'}；"
              f"包哈希{'与上轮一致' if stable else '仍在变'} —— {line[-38:]}")
        if ok and stable:
            print(f"✅ 第 {n} 轮达到不动点：审计全绿且包哈希稳定")
            return 0
        prev = cur

    print(f"❌ {a.max_pass} 轮内未达到不动点 —— **不接受最后一轮的结果**。"
          f"须人工查明是哪个包/哪条检查在持续变动。")
    return 1


if __name__ == "__main__":
    sys.exit(main())
