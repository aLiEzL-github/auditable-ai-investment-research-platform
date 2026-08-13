#!/usr/bin/env python3
"""OI-PF-167：Gate 6 汇合包须**存在通往 READY 的路径**，且三条分支都能报红。

原实现的两条判据 `if not _rr:` 与 `if _rr:` 互斥且穷尽 —— G6A-06 取任何值
都必然产生阻断项，**READY 不可达**。本测试的核心是 ③：证明双人期 + G6A-06
DONE + 独立性有据时 verdict 确实能到 READY。

**「能报红」与「能变绿」都要测。** 只测前者，一个永远红的守卫会全部通过。
"""
import json
import os
import re
import shutil
import subprocess
import sys

SP = "/private/tmp/claude-501/-Users-li-Documents-Claude/24e9b4a4-e9b4-46b4-a888-71fbbc9c7d08/scratchpad"
V = os.path.join(SP, "v3")
SRC = "/Users/li/Documents/Claudetext/portfolio"
PF = os.path.join(SP, "pf167")
GEN = "backend/tools/build_gate6_acceptance.py"


def fresh(drop_self=True):
    """复制台账副本。

    **drop_self**：移除 OI-PF-167 本身 —— 它 blocks_development=G6-01，
    于是每条路径都先撞上「范围内 1 项 ≠ 0」而掩盖真正要测的阻断
    （与 Gate 5 那次 OI-PF-158 自指同形）。本次修复落地后该项即闭合，
    故测试里模拟其已闭合状态。
    """
    shutil.rmtree(PF, ignore_errors=True)
    shutil.copytree(SRC, PF)
    if drop_self:
        q = os.path.join(PF, "risk", "open-items.json")
        d = json.load(open(q, encoding="utf-8"))
        for i in d["items"]:
            if i["open_item_id"] == "OI-PF-167":
                i["status"] = "CLOSED"
        json.dump(d, open(q, "w", encoding="utf-8"),
                  ensure_ascii=False, indent=2)


def gen():
    e = dict(os.environ)
    e["PORTFOLIO_ROOT"] = PF
    r = subprocess.run([os.path.join(V, ".venv/bin/python"), GEN],
                       cwd=V, capture_output=True, text=True, env=e)
    v, bl = "?", []
    for ln in (r.stdout + r.stderr).splitlines():
        if ln.startswith("verdict = "):
            v = ln.split("= ", 1)[1].strip()
        elif ln.startswith("  · "):
            bl.append(ln[4:].strip())
    return v, bl


def set_persons(n):
    p = os.path.join(PF, "decisions-v2", "VD-02.md")
    s = open(p, encoding="utf-8").read()
    s = re.sub(r"baseline_natural_persons\s*=\s*\d+",
               f"baseline_natural_persons  = {n}", s, count=1)
    open(p, "w", encoding="utf-8").write(s)


def set_g6a06(status, extra=None):
    p = os.path.join(PF, "task-records", "G6A-06.json")
    d = json.load(open(p, encoding="utf-8"))
    d["task_status"] = status
    if extra:
        d.update(extra)
    json.dump(d, open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=2)


def step(name, setup, expect_ready, want_in=None):
    fresh()
    setup()
    v, bl = gen()
    ready = (v == "G6_JOINT_READY") or v.endswith("_READY")
    ok = (ready == expect_ready)
    if ok and want_in:
        ok = any(want_in in b for b in bl)
    print(f"  {'✅' if ok else '❌'} {name}\n"
          f"       verdict = {v}"
          + (f"\n       {bl[0][:96]}" if bl else ""))


if __name__ == "__main__":
    step("① 单人期 + G6A-06 = REVIEW_REQUIRED（现状）→ 须受阻",
         lambda: None, False, "汇合受阻")

    step("② 单人期 + G6A-06 被径自判 DONE → 须报「不得径自判 PASS」",
         lambda: set_g6a06("DONE"), False, "不得径自判 PASS")

    step("③ **双人期 + DONE + 独立性有据 + P0/P1 清零 → 须 READY**",
         lambda: (set_persons(2),
                  set_g6a06("DONE", {
                      "red_team_reviewer": "第 2 名自然人（姓名待填）",
                      "independence": "红队人 ≠ 开发/研究编制人",
                      "findings": {"P0": 0, "P1": 0,
                                   "P2": [{"owner": "DEV", "due": "待定",
                                           "materiality": "非材料性"}]}})),
         True)

    step("④ 双人期 + G6A-06 仍 REVIEW_REQUIRED → 须报「须 DONE」",
         lambda: set_persons(2), False, "须 DONE")

    step("⑤ 双人期 + DONE 但**无红队人字段** → 须报红",
         lambda: (set_persons(2), set_g6a06("DONE")), False,
         "缺 red_team_reviewer 字段")

    step("⑥ 双人期 + DONE + 红队人 + **P0 非零** → 须报红",
         lambda: (set_persons(2),
                  set_g6a06("DONE", {"red_team_reviewer": "第 2 名自然人",
                                     "findings": {"P0": 2, "P1": 0}})),
         False, "要求两者均为 0")

    step("⑦ VD-02 读不到人数 → 须判红而非默认放行",
         lambda: (open(os.path.join(PF, "decisions-v2", "VD-02.md"), "w",
                       encoding="utf-8").write("（字段被删）"),),
         False, "读不到 baseline_natural_persons")

    shutil.rmtree(PF, ignore_errors=True)
