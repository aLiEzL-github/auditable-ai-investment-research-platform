#!/usr/bin/env python3
"""单人期红队标记的机械传播守卫（`ADR-026 §5.2` 的**承重条款**）。

`ADR-026` 把 `Gate 6` 从「阻断」改为「可签 + 标注」。这在一条轴上是**严格削弱**：

    阻断对任何忽略标签的消费者都成立；标注只对读标签的消费者成立。

该削弱的对价，就是本守卫 —— **标记不得依赖任何人记得看**。
`ADR-026 §8 X-3` 把它定为该 ADR 的**生效前置**，不是配套改进：
本守卫不存在时，`ADR-026` 的 `MR-2` 论证不成立。

判据（默认拒绝：判不出来即判红，不放行）：

  M-1  `G6A-06` 记录为 `RED_TEAM_SINGLE_PERSON_ATTESTED` 时，
       `independent_red_team_present` 须**显式为 false**
  M-2  此时 `Gate6-验收包.md` 须在**首屏**（前 40 行）含该标记行
  M-3  此时由 Gate 6 派生的产物（`gate-records/G6*.json`）须逐份携带该标记
  M-4  `G6A-06` 不处于该状态时，`Gate 6` 验收包**不得**出现该标记
       —— 防「标记贴错地方」与「标记留在原地而状态已变」
  M-6  该状态须满足 ADR-026 §4 的完整结构条件；Gate 6 与 Gate 6A 生成器
       复用同一判据，不允许「一个接受、另一个拒绝」

用法：python3 backend/tools/red_team_marker_check.py [portfolio_root]
      （不传则取 PORTFOLIO_ROOT；两者皆无即拒 —— OI-PF-186 的 fail-closed）
"""
import glob
import json
import os
import re
import sys

RT_SOLO = "RED_TEAM_SINGLE_PERSON_ATTESTED"
MARKER = "independent_red_team_present"
HEAD_LINES = 40

# ADR-026 §4 条件 8（U 于接受本 ADR 时补入）：该状态自审查执行之日起失效。
# 起草稿只有条件 7（补到第 2 名自然人则失效），**没有处理「一直没补到人」**
# —— 而那恰恰是最可能发生的情形。缺了它，签一次即永久有效。
VALID_DAYS = 180


def expiry_problem(rec):
    """条件 8 的判据。返回说明字符串，None = 未过期且字段齐备。

    **判据只此一份** —— 生成器 build_gate6_acceptance.py 直接 import 本函数，
    不另写一遍。OI-PF-173 记的正是「同一判据四份实现，行为一致但无守卫保证
    其一致；任一份日后被改，其余不会跟着变」。
    """
    import datetime as _dt
    raw = str(rec.get("red_team_performed_at") or "").strip()
    if not raw:
        return ("缺 red_team_performed_at（ADR-026 §4 条件 8）—— "
                "无从判断该状态是否已过期，判红而非默认放行")
    try:
        t = _dt.datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return f"red_team_performed_at 不是合法 ISO8601：{raw!r}"
    if t.tzinfo is None:
        t = t.replace(tzinfo=_dt.timezone.utc)
    age = (_dt.datetime.now(_dt.timezone.utc) - t).days
    if age > VALID_DAYS:
        return (f"红队审查执行于 {raw}，已过 {age} 天 > {VALID_DAYS} 天 —— "
                f"ADR-026 §4 条件 8：本状态已失效，须重做红队"
                f"（代码、开放项与九轮审查结论都可能已变）")
    if age < 0:
        return f"red_team_performed_at 在未来：{raw}"
    return None


def baseline_natural_persons(root):
    """Read the VD-02 branch selector; unreadable input stays fail-closed."""
    path = os.path.join(root, "decisions-v2", "VD-02.md")
    try:
        with open(path, encoding="utf-8") as stream:
            text = stream.read()
    except OSError:
        return None
    match = re.search(r"baseline_natural_persons\s*=\s*(\d+)", text)
    return int(match.group(1)) if match else None


def attestation_missing(rec, natural_persons):
    """Return unmet ADR-026 single-person attestation conditions.

    This is the single structural predicate shared by the Gate 6 and Gate 6A
    generators.  It cannot establish that the human judgment is substantively
    correct; it only rejects an incomplete or expired attestation record.
    """
    miss = []

    evidence = rec.get("red_team_evidence")
    if not isinstance(evidence, list) or not evidence:
        miss.append("缺 red_team_evidence（须为非空列表，每项含 check/command/output）")
    else:
        for i, item in enumerate(evidence):
            if not isinstance(item, dict) or not all(
                    str(item.get(k) or "").strip()
                    for k in ("check", "command", "output")):
                miss.append(f"red_team_evidence[{i}] 的 check/command/output 不齐")
                break

    if not isinstance(rec.get("reviewed_products"), list) or not rec.get(
            "reviewed_products"):
        miss.append("缺 reviewed_products（审查靶面须穷举）")
    if "scope_not_covered" not in rec:
        miss.append("**缺 scope_not_covered 字段** —— 未覆盖维度不写出来等于假装没有"
                    "（OI-PF-186：九轮范围内的审查，最大遗漏落在全部范围之外）；"
                    "空列表表示『已查无遗漏』，与字段缺失不同")

    dispositions = rec.get("agent_findings_disposition")
    if not isinstance(dispositions, list) or not dispositions:
        miss.append("缺非空 agent_findings_disposition（AGENT_ADVERSARIAL_REVIEW 的发现"
                    "须逐条记采信/否决及理由；手册 §4：不得直接当作红队结论）")
    else:
        for i, item in enumerate(dispositions):
            if not isinstance(item, dict) or not all(
                    str(item.get(k) or "").strip()
                    for k in ("open_item_id", "disposition", "basis")):
                miss.append(f"agent_findings_disposition[{i}] 的 "
                            "open_item_id/disposition/basis 不齐")
                break

    findings = rec.get("findings")
    if not isinstance(findings, dict) or "P0" not in findings or "P1" not in findings:
        miss.append("缺 findings.P0 / findings.P1（须可机检的数值字段）")
    elif findings.get("P0") or findings.get("P1"):
        miss.append(f"findings P0={findings.get('P0')} · P1={findings.get('P1')} —— "
                    "基线 B §10A 要求两者均为 0")
    else:
        for i, item in enumerate(findings.get("P2") or []):
            if not isinstance(item, dict) or not all(
                    str(item.get(k) or "").strip()
                    for k in ("owner", "due", "materiality", "basis")):
                miss.append(f"findings.P2[{i}] 缺 owner/due/materiality/basis 之一 —— "
                            "『非材料性判定』是实质判断，判错即等于绕过 P0/P1")
                break

    if not str(rec.get("red_team_reviewer") or "").strip():
        miss.append("缺 red_team_reviewer（须落到具体的人）")
    if rec.get("independent_red_team_present") is not False:
        miss.append("independent_red_team_present 须**显式为 false** —— "
                    "单人期红队人即编制人，该事实须落库而非省略"
                    "（ADR-026 §4 条件 5/6）")
    if not str(rec.get("independence") or "").strip():
        miss.append("缺 independence（须写明红队人与编制人的实际关系，"
                    "单人期即『同一自然人』）")

    if natural_persons is None:
        miss.append("VD-02 读不到 baseline_natural_persons，无法判断 ADR-026 条件 7")
    elif natural_persons != 1:
        miss.append(f"VD-02 = {natural_persons} 名自然人；ADR-026 条件 7："
                    f"{RT_SOLO} 已自动失效，须由独立自然人重做并转 DONE")

    expiry = expiry_problem(rec)
    if expiry:
        miss.append(expiry)
    return miss


def independent_review_missing(rec):
    """Return unmet baseline B section 10A fields for the >=2-person branch."""
    miss = []
    if not str(rec.get("red_team_reviewer") or "").strip():
        miss.append("缺 red_team_reviewer（独立红队须落到具体的人）")
    findings = rec.get("findings")
    if not isinstance(findings, dict) or "P0" not in findings or "P1" not in findings:
        miss.append("缺 findings.P0 / findings.P1（须可机检的数值字段）")
    elif findings.get("P0") or findings.get("P1"):
        miss.append(f"findings P0={findings.get('P0')} · P1={findings.get('P1')} —— "
                    "基线 B §10A 要求两者均为 0")
    return miss


def _root(argv) -> str:
    p = (argv[1] if len(argv) > 1 else None) or os.environ.get("PORTFOLIO_ROOT")
    if not p or not os.path.isdir(p):
        raise SystemExit(
            f"E-ENV-001: 须给出台账根目录（参数或 PORTFOLIO_ROOT），当前 {p!r} "
            f"未设或不是目录 —— 本工具不使用硬编码缺省路径（OI-PF-186）")
    return os.path.abspath(p)


def main() -> int:
    root = _root(sys.argv)
    bad = []

    rec_p = os.path.join(root, "task-records", "G6A-06.json")
    if not os.path.exists(rec_p):
        print(f"❌ 找不到 {rec_p} —— 无从判断，判红而非默认放行")
        return 1
    rec = json.load(open(rec_p, encoding="utf-8"))
    solo = rec.get("task_status") == RT_SOLO

    # ── M-1 ──
    if solo and rec.get(MARKER) is not False:
        bad.append(f"M-1: G6A-06 = {RT_SOLO} 但 {MARKER} 不是显式 false"
                   f"（实测 {rec.get(MARKER)!r}）—— 该事实须落库，不得省略")

    # ── M-6：ADR-026 完整结构 ──
    if solo:
        persons = baseline_natural_persons(root)
        for problem in attestation_missing(rec, persons):
            bad.append("M-6: " + problem)

    pkg_p = os.path.join(root, "Gate6-验收包.md")
    pkg = ""
    if os.path.exists(pkg_p):
        pkg = open(pkg_p, encoding="utf-8").read()
    head = "\n".join(pkg.splitlines()[:HEAD_LINES])

    # ── M-2 ──
    if solo:
        if not pkg:
            bad.append(f"M-2: G6A-06 = {RT_SOLO} 但找不到 {pkg_p}")
        elif f"{MARKER} = false" not in head:
            where = "正文内" if f"{MARKER} = false" in pkg else "全文无"
            bad.append(f"M-2: Gate6 验收包**首屏 {HEAD_LINES} 行内**无 "
                       f"`{MARKER} = false`（{where}）—— ADR-026 §4 条件 6 要求"
                       f"在首屏；埋在正文里等于指望读者翻到那一页")

    # ── M-3 ──
    # **派生产物计数须报出来。** 签署前 gate-records/G6*.json 尚不存在，
    # 此时循环体一次都不执行 —— 一个恒空的集合会让本判据静默通过，
    # 那正是「结构在、功能不在」。故把计数写进输出：0 要看得见，
    # 而不是藏在一个 ✅ 后面。
    n_derived = 0
    if solo:
        derived = sorted(glob.glob(os.path.join(root, "gate-records", "G6*.json")))
        n_derived = len(derived)
        for f in derived:
            d = json.load(open(f, encoding="utf-8"))
            if d.get(MARKER) is not False:
                bad.append(f"M-3: 派生产物 {os.path.basename(f)} 未携带 "
                           f"{MARKER} = false —— 标记须逐份传播，"
                           f"不得只贴在源头")

    # ── M-5：有效期（ADR-026 §4 条件 8）──
    if solo:
        _exp = expiry_problem(rec)
        if _exp:
            bad.append("M-5: " + _exp)

    # ── M-4：反向 ──
    if not solo and f"{MARKER} = false" in pkg:
        bad.append(f"M-4: G6A-06 = {rec.get('task_status')!r}（非 {RT_SOLO}）"
                   f"，而 Gate6 验收包仍带 `{MARKER} = false` —— "
                   f"状态已变而标记留在原地，会把一个不再成立的声明当成现状")

    if bad:
        print("❌ 单人期红队标记传播违规（ADR-026 §5.2 承重条款）：")
        for b in bad:
            print("  - " + b)
        return 1
    if not solo:
        print(f"✅ G6A-06 = {rec.get('task_status')}（非 {RT_SOLO}）："
              f"M-1—M-3 不适用，M-4 已验（包内无残留标记）")
    else:
        print(f"✅ G6A-06 = {RT_SOLO}：M-1 记录标记为 false · "
              f"M-2 验收包首屏含标记 · "
              f"M-6 ADR-026 结构条件齐备 · "
              f"M-3 **检查派生产物 {n_derived} 份**"
              + ("（**为 0** —— 签署前 gate-records/G6*.json 尚不存在，"
                 "本判据此刻空转，签署后须复跑）" if n_derived == 0 else "，全部携带标记"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
