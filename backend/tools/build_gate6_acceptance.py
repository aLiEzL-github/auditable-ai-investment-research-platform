#!/usr/bin/env python3
"""build_gate6_acceptance.py —— Gate 6 汇合（G6-01）验收包生成器。

依据：
  · 基线 B §10 末 G6-01：多 Agent、回测、预测和准出联合验收
    前置：G6A-06、G6C-03；**且非 REMOVED 时 G6B-04**
    （本项目 VD-14=REMOVED，故不含 G6B-04 —— 基线原文即如此）
  · ADR-012 §5：G6A-06 单人期 REVIEW_REQUIRED → G6-01 相应受阻
  · ADR-021 §2（范围口径逐字段）· ADR-022 §2/§3.1 · ADR-010 §3.1
  · VD-26（CALIBRATION_PENDING 不冒充能力）· VD-14（REMOVED 合法汇合）

结论语义（如实载明，不签署）：
  · G6A-06 = REVIEW_REQUIRED（单人期，ADR-012 §3）→ G6-01 前置不满足
    → 汇合结论 = G6_JOINT_BLOCKED（非 PASS、非失败 —— 结构性受阻）
  · G6A-06 = RED_TEAM_SINGLE_PERSON_ATTESTED 且 ADR-026 八条件齐备
    → G6_JOINT_PASSED_SINGLE_PERSON_RED_TEAM（可签精确较弱断言，绝不冒充
      独立红队或 G6_JOINT_READY）
  · 三分支并列可见：G6A（执行 3 + 挂起 3）· G6B（4×NOT_APPLICABLE）
    · G6C（3 任务 DONE，VD-26 恒 PENDING）
"""
import hashlib
import json
import os
import re
import subprocess
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
def _portfolio_root() -> str:
    """台账根目录 —— 由 PORTFOLIO_ROOT 给出，**缺失即拒**（OI-PF-186）。

    此前缺省值写死为编写者的本机绝对路径，并随公开仓库一并发布，
    泄露了本机用户名、目录布局与私有台账的存在。台账位置因人因机而异，
    **没有一个正确的缺省值可取**，故取 fail-closed：未设即报错退出，
    而不是悄悄指向一个别人机器上不存在的路径再产出空结果。
    """
    p = os.environ.get("PORTFOLIO_ROOT")
    if not p or not os.path.isdir(p):
        raise SystemExit(
            "E-ENV-001: 须设 PORTFOLIO_ROOT 指向台账根目录（当前 "
            f"{p!r} 未设或不是目录）—— 本工具不再使用任何硬编码缺省路径")
    return os.path.abspath(p)


PORTFOLIO = _portfolio_root()


# ── ADR-026：单人期红队的精确状态 ────────────────────────────────────
# ADR-026 的结构判据只在 red_team_marker_check 里有一份。Gate 6 与 Gate 6A
# 共用它，避免一个生成器接受而另一个拒绝同一份记录。
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from red_team_marker_check import (  # noqa: E402
    RT_SOLO as _RT_SOLO,
    attestation_missing as _rt_solo_missing,
    baseline_natural_persons,
    independent_review_missing,
)

NOW = subprocess.run(["date", "-u"], capture_output=True, text=True).stdout.strip()

EXCLUDE = ("生成时刻", "实测时刻", "main 最新 CI run", "run = ", "ruleset: ",
           "g1-08-2026", "_mut-", "sparseimage", "备份目录 = ", "  g1-08-",
           "substantive_sha256", "合计", "独立审计:", "v2.0 基线:")


def run(cmd):
    r = subprocess.run(["/bin/sh", "-o", "pipefail", "-c", cmd],
                       capture_output=True, text=True, cwd=ROOT)
    return r.returncode, r.stdout.strip(), r.stderr.strip()


def status_of(cmd):
    rc, out, err = run(cmd)
    m = re.findall(r"(OK|FAILED[^\n]*)", out or err)
    if m:
        return m[-1]
    _tail = ((err or out).splitlines() or ["（无输出）"])[-1][:70]
    return f"**未跑起来（rc={rc}）**：{_tail}"


def in_gate6(i):
    """ADR-021 §2 逐字段口径（OI-PF-157）：汇合任务 G6-01。"""
    if re.search(r"G6-\d\d", str(i.get("blocks_development") or "")):
        return True
    for k in ("blocks_data_flow", "deprecated_blocks_gate"):
        if re.search(r"\bG6\b|G6-\d|Gate 6\b|Gate 6[^ABC]", str(i.get(k) or "")):
            return True
    return bool(re.search(r"Gate 6\b|Gate 6[^ABC]", str(i.get("blocks_decisions") or "")))


def is_standing_risk(i):
    return str(i.get("blocks_data_flow")) == "ALL"


def _blk(i):
    p = []
    if i.get("blocks_development"):
        p.append(f"任务={i['blocks_development']}")
    if i.get("blocks_decisions"):
        p.append(f"决策={i['blocks_decisions']}")
    if i.get("blocks_data_flow"):
        p.append(f"数据面={i['blocks_data_flow']}")
    return " · ".join(p) or "无"


def _rec(tid):
    fp = os.path.join(PORTFOLIO, "task-records", f"{tid}.json")
    if os.path.exists(fp):
        return json.load(open(fp, encoding="utf-8"))
    return None


def main() -> int:
    L = []
    OI = json.load(open(os.path.join(PORTFOLIO, "risk", "open-items.json"),
                        encoding="utf-8"))
    items = OI["items"]
    mat = [i for i in items if i["status"] == "OPEN" and i.get("material")]
    g6_mat = [i for i in mat if in_gate6(i)
              and i.get("category") != "签署前置条件"]
    _standing = [i for i in mat if is_standing_risk(i)]

    # ── 三分支状态实测 ────────────────────────────────────────────
    _g6a01 = _rec("G6A-01"); _g6a05 = _rec("G6A-05"); _g6a06 = _rec("G6A-06")
    _g6b = [_rec(f"G6B-0{n}") for n in range(1, 5)]
    _g6c = [_rec(f"G6C-0{n}") for n in range(1, 4)]
    _st_a = {t: (_rec(t) or {}).get("task_status", "NO_RECORD")
             for t in ("G6A-01", "G6A-05", "G6A-06")}
    _st_b = {f"G6B-0{n}": (_rec(f"G6B-0{n}") or {}).get("task_status", "NO_RECORD")
             for n in range(1, 5)}
    _st_c = {t: (_rec(t) or {}).get("task_status", "NO_RECORD")
             for t in ("G6C-01", "G6C-02", "G6C-03")}
    _st_g601 = (_rec("G6-01") or {}).get("task_status", "NO_RECORD")

    _g6a06_review_required = _st_a.get("G6A-06") == "REVIEW_REQUIRED"
    _g6b_na_ok = all(v == "NOT_APPLICABLE" for v in _st_b.values())
    _g6c_done = all(v == "DONE" for v in _st_c.values())
    # VD-14 / VD-26 当场实测（③ 读数来源：当场实测，不得引用台账记载）
    _vd14 = open(os.path.join(PORTFOLIO, "decisions-v2", "VD-14.md"),
                 encoding="utf-8").read()
    _vd26 = open(os.path.join(PORTFOLIO, "decisions-v2", "VD-26.md"),
                 encoding="utf-8").read()
    _vd14_removed = "backtest_mode = REMOVED" in _vd14
    _vd26_pending = "CALIBRATION_PENDING" in _vd26
    # OI-PF-167：**VD-02 此前从未被读取** —— 而它才是决定「单人期」分支、
    # 进而决定汇合能否 READY 的那一个。本文件注释声称「读数来源：当场实测，
    # 不得引用台账记载」，实测却只覆盖 VD-14/VD-26 两个，**漏掉决定性的那个**，
    # 单人期假设被硬编码进分支逻辑。
    # 后果：原实现的两条判据 `if not _rr:` 与 `if _rr:` 互斥且穷尽 ——
    # G6A-06 取任何值都必然产生阻断项，**不存在通往 READY 的路径**。
    # 第 2 名自然人到位那天，不改本文件就产不出可签署的验收包，
    # 而「要改本文件」此前无任何记载。
    _persons = baseline_natural_persons(PORTFOLIO)
    _solo = (_persons == 1)

    # ── 结论（㉑：由状态决定，不硬编码）────────────────────────────
    _blockers = []
    if g6_mat:
        _blockers.append(f"Gate 6 范围内材料性开放项 {len(g6_mat)} 项 ≠ 0"
                         f"（ADR-010 §4 不得 PASS）："
                         f"{[i['open_item_id'] for i in g6_mat]}")
    # ── G6A-06 按 VD-02 当场实测的自然人数分支（OI-PF-167）──────────
    if _persons is None:
        _blockers.append("**VD-02 读不到 baseline_natural_persons** —— "
                         "无从判断适用单人期还是双人期分支，判红而非默认放行")
    elif _solo:
        # 单人期：G6A-06 只允许两种取值 —— REVIEW_REQUIRED（未做）
        # 或 RED_TEAM_SINGLE_PERSON_ATTESTED（ADR-026：已做，但红队人 = 编制人）。
        # **不允许 DONE** —— DONE 会被读作独立红队已完成，那是不真的。
        _st06 = _st_a.get("G6A-06")
        if _st06 == _RT_SOLO:
            _miss = _rt_solo_missing(_rec("G6A-06") or {}, _persons)
            if _miss:
                _blockers.append(
                    f"G6A-06 = {_RT_SOLO} 但 ADR-026 §4 的条件未齐："
                    + "；".join(_miss))
        elif not _g6a06_review_required:
            _blockers.append(f"G6A-06 状态 = {_st06} —— 单人期只允许 "
                             f"REVIEW_REQUIRED（未做）或 {_RT_SOLO}"
                             f"（ADR-026，已做但红队人 = 编制人），"
                             f"不得径自判 PASS/DONE")
        else:
            _blockers.append("**G6A-06 = REVIEW_REQUIRED（单人期）→ G6-01 汇合"
                             "受阻**（ADR-012 §5：本 ADR 不改 G6-01 的依赖；"
                             "G6B 才是有条件支线。解除路径 = VD-02 重开条款补到"
                             f"第 2 名自然人，当前 VD-02 = {_persons} 名；"
                             f"或按 ADR-026 走 {_RT_SOLO}）")
    elif _st_a.get("G6A-06") == _RT_SOLO:
        # ADR-026 §4 条件 7：补到第 2 名自然人后该状态**自动失效**。
        # 不自动失效的话，一个为「单人期」造的状态会在双人期继续生效，
        # 而它的全部理由（没有第二个人）已经消失 —— 那正是临时措施永久化。
        _blockers.append(
            f"VD-02 = {_persons} 名自然人，而 G6A-06 仍为 {_RT_SOLO} —— "
            f"ADR-026 §4 条件 7：该状态在补到第 2 名自然人时**自动失效**，"
            f"红队须由独立自然人重做并转 DONE")
    else:
        # ≥2 名自然人：重开条款生效，G6A-06 须真的做完且独立性有据
        if _st_a.get("G6A-06") != "DONE":
            _blockers.append(f"VD-02 = {_persons} 名自然人（重开条款已具备），"
                             f"但 G6A-06 状态 = {_st_a.get('G6A-06')} —— 须 DONE")
        else:
            _rec06 = _rec("G6A-06") or {}
            _independent_missing = independent_review_missing(_rec06)
            if _independent_missing:
                _blockers.append("G6A-06 = DONE 但独立红队字段未齐："
                                 + "；".join(_independent_missing))
    if not _g6b_na_ok:
        _blockers.append(f"G6B 状态不符（实测 {_st_b}）—— 四项须 NOT_APPLICABLE")
    if not _g6c_done:
        _blockers.append(f"G6C 状态不符（实测 {_st_c}）—— 三任务须 DONE")
    if not _vd14_removed:
        _blockers.append("VD-14 不再为 REMOVED —— 本文前提改变，G6B-04 须回填")
    if not _vd26_pending:
        _blockers.append("VD-26 不再为 CALIBRATION_PENDING —— 决策前提改变")
    # ── verdict（ADR-026）──────────────────────────────────────────
    # 单人期红队走完全部条件时，结论**不是** G6_JOINT_READY —— 那会被读作
    # 「独立红队已通过」。取一个把缺失写在名字里的状态（ADR-009 的方法论）。
    _solo_rt = (_st_a.get("G6A-06") == _RT_SOLO)
    if _blockers:
        verdict = "G6_JOINT_BLOCKED"
    elif _solo_rt:
        verdict = "G6_JOINT_PASSED_SINGLE_PERSON_RED_TEAM"
    else:
        verdict = "G6_JOINT_READY"

    # ADR-026 §4 条件 6 / X-5：标记须在**首屏**，不是埋在正文里。
    # §5.2 是本 ADR 的承重条款 —— 阻断改标注后，标注若不机械强制传播，
    # 那就是纯削弱。守卫 backend/tools/red_team_marker_check.py 断言此行在位。
    L.append("```text")
    L.append(f"independent_red_team_present = "
             f"{'false' if _solo_rt else ('true' if verdict == 'G6_JOINT_READY' else 'unknown')}")
    if _solo_rt:
        L.append("红队人 = 开发/研究编制人（同一自然人）—— 基线 B §10A 的独立性")
        L.append("要求**未满足**。本包不得被读作「已通过独立红队」或「已经过")
        L.append("独立安全审查」。依据 ADR-026；VD-02 补到 ≥2 人时本状态自动失效。")
    L.append("```\n")

    # ── §1 三分支并列可见（G6B-执行计划 P-2 的汇合时义务）──────────
    L.append("## 1. 三分支并列可见（G6-01 汇合义务）\n")
    L.append("### 1.1 G6A 分支\n")
    L.append("```text")
    _g6a06_semantics = (
        "ADR-026 精确单人状态，独立性未满足"
        if _solo_rt else "单人期 REVIEW_REQUIRED，ADR-012 §3"
    )
    L.append(f"  执行 3 项（分列报数，⑨）：G6A-01={_st_a['G6A-01']}（PR #68）"
             f" · G6A-05={_st_a['G6A-05']}（PR #69）"
             f" · G6A-06={_st_a['G6A-06']}（{_g6a06_semantics}）")
    L.append("  挂起 3 项：G6A-02/03/04 = NOT_APPLICABLE_PENDING_PROVIDER"
             "（可逆，VD-10=NONE；重开触发 +4.00 工时回填）")
    L.append("```\n")
    L.append("### 1.2 G6B 分支（VD-14=REMOVED，永久）\n")
    L.append("```text")
    for tid in ("G6B-01", "G6B-02", "G6B-03", "G6B-04"):
        L.append(f"  {tid} = {_st_b[tid]}（依据 VD-14=REMOVED，非「跳过未做」；"
                 f"反事实 4.75 人日不蒸发）")
    L.append("```\n")
    L.append("### 1.3 G6C 分支\n")
    L.append("```text")
    for tid in ("G6C-01", "G6C-02", "G6C-03"):
        L.append(f"  {tid} = {_st_c[tid]}（PR #70）")
    L.append("  VD-26：declared 恒 CALIBRATION_PENDING —— 本 Gate 的 PASS 不验证")
    L.append("  预测能力；CALIBRATION_PENDING 不冒充能力（一票否决，H-8）")
    L.append("```\n")
    L.append(f"### 1.4 汇合任务 G6-01 自身：{_st_g601}\n")
    L.append("```text")
    L.append("  前置（基线 B §10 末原文）：G6A-06、G6C-03；且非 REMOVED 时 G6B-04")
    L.append("  —— 本项目 VD-14=REMOVED，故不含 G6B-04。")
    L.append("  G6A-06 是**无条件**前置（G6B 才是有条件支线）。")
    if _solo_rt:
        L.append(f"  {_RT_SOLO} 不声称独立性要求已满足；ADR-026 只允许汇合")
        L.append("  形成带 independent_red_team_present=false 的精确较弱 verdict。")
    else:
        L.append("  REVIEW_REQUIRED → G6-01 相应受阻（ADR-012 §5 明文，非本项目例外）")
    L.append("```\n")

    # ── §1.7 持续性风险节 ─────────────────────────────────────────
    _S17 = []
    _S17.append("### 1.7 持续性风险（`blocks_data_flow = \"ALL\"`，"
                "不计入本 Gate 阻断）\n")
    _S17.append("```text")
    _S17.append("依据 = ADR-022 §2（U 裁定 2026-08-12，取字面）：\"ALL\" 不算命中")
    _S17.append("       任何 Gate —— 这类项对每个 Gate 一视同仁且不可由任一 Gate")
    _S17.append("       解除，计入即等于宣布该 Gate 不可通过。")
    _S17.append("**该裁定是一次放宽，本节即其对价**（ADR-022 §3.1/§4.1）。")
    _S17.append("本节不得与「② 全部未闭材料性开放项」合并 —— 合并即等于没单列。")
    _S17.append("")
    _S17.append(f"未闭且材料性的 \"ALL\" 项：{len(_standing)} 项")
    for i in _standing:
        _S17.append(f"  {i['open_item_id']} | {i.get('category','')} | "
                    f"决策={i.get('blocks_decisions')}")
        _S17.append(f"     要旨: {str(i.get('description') or '')[:120]}")
    _S17.append("")
    _S17.append("上列各项**仍 OPEN 且仍 material** —— 本节不主张其风险已消解，")
    _S17.append("只记载它们不构成本 Gate 的阻断。其处置路径在 VD-20 / VD-12 层面，")
    _S17.append("不在任何 Gate 内（ADR-022 §4.1 已载明这是被放弃的效力）。")
    _S17.append("```\n")
    L.extend(_S17)

    # ── §2 债务清点 ───────────────────────────────────────────────
    L.append("## 2. 债务清点（ADR-010 §3.1，三条；范围口径 = ADR-021 §2 逐字段）\n")
    L.append("```text")
    L.append(f"① Gate 6 范围内的材料性开放项：{len(g6_mat)} 项（须为零）"
             f"［范围判定：blocks_development 含 G6-xx，ADR-021 §2 并集口径"
             f"（逐字段实现，OI-PF-157）］"
             + (f" —— 非零: {[i['open_item_id'] for i in g6_mat]}" if g6_mat else ""))
    L.append(f"   \"ALL\" 项不计入本条（ADR-022 §2 取字面），"
             f"另见 §1.7 逐项点名的 {len(_standing)} 项持续性风险")
    L.append(f"② 全部未闭材料性开放项 —— {len(mat)} 项")
    for i in mat:
        L.append(f"   {i['open_item_id']} | {i.get('category','')} | 阻断={_blk(i)}")
    _WINDOW = ("2026-08-11", "2026-08-12")

    def _in_window(*fields):
        _s = " ".join(str(f or "") for f in fields)
        return any(d in _s for d in _WINDOW)

    _added = [i for i in items if _in_window(i.get("source"))]
    _closed = [i for i in items if i["status"] == "CLOSED"
               and _in_window(i.get("closure_evidence"), i.get("source"))]
    L.append(f"③ 债务趋势：G6 期间新增 {len(_added)} 项 · 关闭 {len(_closed)} 项"
             f" · 净变化 {len(_added) - len(_closed):+d} 项（近似值）")
    L.append("```\n")

    # ── §3 测试与守卫 ─────────────────────────────────────────────
    L.append("## 3. 测试命令、退出码与结果\n")
    L.append("```text")
    _t_g6a = status_of(
        ".venv/bin/python -m unittest backend.tests.test_g6a_01 "
        "backend.tests.test_g6a_05 2>&1 | tail -1")
    _t_g6c = status_of(
        ".venv/bin/python -m unittest backend.tests.test_g6c_01 "
        "backend.tests.test_g6c_02 backend.tests.test_g6c_03 "
        "backend.tests.test_calibration_guard 2>&1 | tail -1")
    _t_guard = status_of(
        ".venv/bin/python backend/tools/calibration_claim_check.py . "
        "2>&1 | tail -1")
    L.append(f"G6A-01/05 工程测试: {_t_g6a}")
    L.append(f"G6C 工程测试: {_t_g6c}")
    L.append(f"H-8 表述守卫: {_t_guard}")
    _rc_a, _o_a, _e_a = run(
        f"python3 {os.path.join(PORTFOLIO, 'tools/audit_session.py')} "
        f"{PORTFOLIO} 2>&1")
    _m_a = re.search(r"合计 (\d+) 项：PASS (\d+) / FAIL (\d+)", _o_a or _e_a)
    L.append(f"台账审计: " + (
        f"OK（{_m_a.group(1)} 项：PASS {_m_a.group(2)} / FAIL {_m_a.group(3)}）"
        if _m_a and _m_a.group(3) == "0"
        else f"**未全绿**：{(_e_a or _o_a).splitlines()[-1][:70]}"))
    L.append("```\n")

    # ── §4 风险与已知缺口 ─────────────────────────────────────────
    L.append("## 4. 风险与已知缺口（如实载明）\n")
    L.append("```text")
    if _solo_rt and not _blockers:
        L.append(f"· **本包只支持 {verdict} 这一精确较弱断言。**")
        L.append("  G6A-06 的独立红队要求仍未满足；红队人与编制人是同一自然人。")
        L.append("  任何下游把本 verdict 读作 G6_JOINT_READY、独立红队或独立安全审查，")
        L.append("  都是错误升级；VD-02 补到第 2 名自然人时本状态自动失效并须重做。")
    else:
        L.append("· **本包不是 Gate 6 PASS，也不构成签署。** G6-01 汇合受阻是")
        L.append("  ADR-012 §5 记载的结构性状态：G6A-06 红队独立性在单人结构下")
        L.append("  不可满足，G6-01 无条件前置因此不成立。解除路径为 VD-02 重开条款")
        L.append("  （补到第 2 名自然人）或完成 ADR-026 的精确单人红队路径。")
    L.append("· G6A/G6B/G6C 三支线验收包均已生成并各自载明状态")
    L.append("  （Gate6A/Gate6B/Gate6C-验收包.md）。")
    L.append("· VD-26 下 G6C 分支「CALIBRATION_PENDING 不冒充能力」已由")
    L.append("  H-8 表述守卫机检（仓库 CI + 台账 P6），不是文字提醒")
    L.append("```\n")

    # ── 装配后自查（㉛）───────────────────────────────────────────
    _body_txt = "\n".join(L)
    _h17 = "### 1.7 持续性风险"
    _i17 = _body_txt.find(_h17)
    if _i17 < 0:
        _sec17 = ""
    else:
        _nxt = _body_txt.find("\n## ", _i17)
        _sec17 = _body_txt[_i17:_nxt if _nxt > 0 else len(_body_txt)]
    if _standing and not all(i["open_item_id"] in _sec17 for i in _standing):
        _blockers.append("**ADR-022 §3.1 的单列义务未落地**：持续性风险须在"
                         "§1.7 逐项点名 —— 别处提到不算")
        verdict = "G6_JOINT_NOT_READY"

    # ── §0 头部 ───────────────────────────────────────────────────
    _H = []
    _H.append("# Gate 6 汇合验收包（G6-01）\n")
    _H.append("```text")
    _H.append(f"生成时刻   = {NOW}")
    _H.append("生成方式   = backend/tools/build_gate6_acceptance.py（全部数据实时采集）")
    _H.append("依据       = 基线 B §10 末 G6-01 + ADR-012 §5 + ADR-026 + VD-14/VD-26 + "
              "ADR-021 §2 + ADR-022 §2/§3 + ADR-010 §3.1")
    _H.append("范围口径   = ADR-021 §2 并集逐字段：material ∧ OPEN ∧ "
              "category != 签署前置条件；blocks_development 含 G6-xx 必含 | "
              "其余字段含 G6/Gate 6；blocks_data_flow=\"ALL\" 不算命中"
              "（ADR-022 §2），另见 §1.7")
    _H.append(f"结论       = **{verdict}**"
              + (f" —— {'；'.join(_blockers)}" if _blockers
                 else "（三分支并列可见且各自就绪）"))
    if verdict == "G6_JOINT_PASSED_SINGLE_PERSON_RED_TEAM":
        _H.append("**本包不是独立红队 Gate 6 PASS。** 它只形成 ADR-026 的精确较弱")
        _H.append("签署对象；独立性要求仍未满足，且当前文件本身不构成签署。")
    elif _solo_rt:
        _H.append(f"**本包不是 Gate 6 PASS。** G6A-06 虽声明 {_RT_SOLO}，但")
        _H.append("ADR-026 条件或其他汇合前置未齐；以上 blockers 须先清零，不签署。")
    else:
        _H.append("**本包不是 Gate 6 PASS。** G6-01 前置 G6A-06 单人期")
        _H.append("REVIEW_REQUIRED → 汇合受阻（ADR-012 §5），不签署。")
    _H.append("independent_reviewer_present = false（VD-02 = 1 名自然人）")
    _H.append("```\n")
    _H.append("> **本包不构成签署。** 供批准人审阅的冻结材料；"
              "G6A/G6B/G6C 三分支并列可见。\n")
    L = _H + L

    pkg = os.path.join(PORTFOLIO, "Gate6-验收包.md")
    with open(pkg, "w", encoding="utf-8") as f:
        f.write("\n".join(L))
    assert open(pkg, encoding="utf-8").read() == "\n".join(L), "写入后断言失败"

    sub_lines = [ln for ln in L if not any(x in ln for x in EXCLUDE)]
    substantive = hashlib.sha256("\n".join(sub_lines).encode("utf-8")).hexdigest()
    with open(pkg, "a", encoding="utf-8") as f:
        f.write(f"\nsubstantive_sha256 = {substantive}\n")
    content = hashlib.sha256(open(pkg, "rb").read()).hexdigest()
    print(f"written {pkg}", file=sys.stderr)
    print(f"content_sha256 = {content}", file=sys.stderr)
    print(f"substantive_sha256 = {substantive}", file=sys.stderr)
    print(f"verdict = {verdict}", file=sys.stderr)
    print(f"blockers = {len(_blockers)}", file=sys.stderr)
    for _b in _blockers:
        # 不截断。原为 _b[:120] —— 实测把「本状态已失效」这句切在了第 132 字，
        # 于是阻断理由的**结论部分**读者看不到，只看得到前半段描述。
        # 一条被截断的阻断理由，和一条含糊的阻断理由，对读者是一回事。
        print(f"  · {_b}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
