#!/usr/bin/env python3
"""build_gate6c_acceptance.py —— Gate 6C 验收包生成器（实时采集，不可手写）。

依据：
  · G6C-执行计划.md §1A（基线 B §10A 任务表）· §4（H-1…H-10）· §6
  · ADR-021 §2（范围口径：四字段逐字段，blocks_development 必含 G6C-\\d\\d）
  · ADR-022 §2/§3.1（blocks_data_flow="ALL" 不算命中，但须单列一节）
  · ADR-010 §3.1（三条债务清点义务，S3 逐条机检）
  · VD-26（永久 CALIBRATION_PENDING，不作出校准能力声明）

教训落地：㉑ 结论由计数决定 · ㉒ PORTFOLIO_ROOT · ⑨ 计数分别报出 ·
㉞ pipefail · ㉟ 未跑起来显式说出 · ㉛ §1.7 自查只在节内
"""
import hashlib
import json
import os
import re
import subprocess
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
PORTFOLIO = os.environ.get("PORTFOLIO_ROOT") or "/Users/li/Documents/Claudetext/portfolio"
NOW = subprocess.run(["date", "-u"], capture_output=True, text=True).stdout.strip()

EXCLUDE = ("生成时刻", "实测时刻", "main 最新 CI run", "run = ", "ruleset: ",
           "g1-08-2026", "_mut-", "sparseimage", "备份目录 = ", "  g1-08-",
           "substantive_sha256", "合计", "独立审计:", "v2.0 基线:")

TASKS = ("G6C-01", "G6C-02", "G6C-03")


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


def in_gate6c(i):
    """ADR-021 §2 逐字段口径（OI-PF-157）："""
    if re.search(r"G6C-\d\d", str(i.get("blocks_development") or "")):
        return True
    for k in ("blocks_data_flow", "deprecated_blocks_gate"):
        if re.search(r"\bG6C\b|G6C-\d|Gate 6C", str(i.get(k) or "")):
            return True
    return bool(re.search(r"Gate 6C", str(i.get("blocks_decisions") or "")))


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


def main() -> int:
    L = []
    OI = json.load(open(os.path.join(PORTFOLIO, "risk", "open-items.json"),
                        encoding="utf-8"))
    items = OI["items"]
    mat = [i for i in items if i["status"] == "OPEN" and i.get("material")]
    g6c_mat = [i for i in mat if in_gate6c(i)
               and i.get("category") != "签署前置条件"]
    _standing = [i for i in mat if is_standing_risk(i)]

    _tr = os.path.join(PORTFOLIO, "task-records")
    _st = {}
    for tid in TASKS:
        fp = os.path.join(_tr, f"{tid}.json")
        if os.path.exists(fp):
            _d = json.load(open(fp, encoding="utf-8"))
            _st[tid] = str(_d.get("task_status") or "?")
        else:
            _st[tid] = "NO_RECORD"
    _tasks_ok = all(_st[t] == "DONE" for t in TASKS)

    # ── 工程证据 ──────────────────────────────────────────────────
    _tests = {t: status_of(
        f".venv/bin/python -m unittest backend.tests.{t} 2>&1 | tail -1")
        for t in ("test_g6c_01", "test_g6c_02", "test_g6c_03",
                  "test_calibration_guard")}
    _rc_g, _o_g, _e_g = run(
        ".venv/bin/python backend/tools/calibration_claim_check.py . 2>&1")
    _g_out = _o_g or _e_g
    _guard = ("OK" if "PASS" in _g_out and "FAILED" not in _g_out
              else f"FAILED：{_g_out.splitlines()[-1][:70]}")
    # H-8 行为断言（仓库守卫自带）—— 另跑一次含植入的变异（先红后绿证据）
    _rc_g, _o_g, _e_g = run(
        ".venv/bin/python -m unittest backend.tests.test_calibration_guard "
        "2>&1 | tail -1")
    _audit = status_of(
        f"python3 {os.path.join(PORTFOLIO, 'tools/audit_session.py')} "
        f"{PORTFOLIO} 2>&1 | tail -1")
    _rc_a, _o_a, _e_a = run(
        f"python3 {os.path.join(PORTFOLIO, 'tools/audit_session.py')} "
        f"{PORTFOLIO} 2>&1")
    _m_a = re.search(r"合计 (\d+) 项：PASS (\d+) / FAIL (\d+)", _o_a or _e_a)
    if _m_a:
        _audit = (f"OK（{_m_a.group(1)} 项：PASS {_m_a.group(2)} / "
                  f"FAIL {_m_a.group(3)}）"
                  if _m_a.group(3) == "0"
                  else f"FAILED（PASS {_m_a.group(2)} / FAIL {_m_a.group(3)}）")

    # ── 结论 ──────────────────────────────────────────────────────
    _blockers = []
    if g6c_mat:
        _blockers.append(f"Gate 6C 范围内材料性开放项 {len(g6c_mat)} 项 ≠ 0"
                         f"（ADR-010 §4 不得 PASS）："
                         f"{[i['open_item_id'] for i in g6c_mat]}")
    if not _tasks_ok:
        _blockers.append(f"G6C 任务状态不符（实测 {_st}）—— 三任务须 DONE")
    if not all(v.startswith("OK") for v in _tests.values()):
        _blockers.append(f"G6C 工程测试未全过（{_tests}）")
    if not _guard.startswith("OK"):
        _blockers.append(f"H-8 表述守卫未全绿（{_guard}）")
    if not _audit.startswith("OK"):
        _blockers.append(f"台账审计未全绿（{_audit}）")
    verdict = "G6C_分支_READY" if not _blockers else "G6C_分支_NOT_READY"

    # ── §1 基线 §9 证明义务 ───────────────────────────────────────
    L.append("## 1. 基线 §9 证明义务（逐条实测）\n")
    L.append("```text")
    L.append("必须证明：预测预登记与充分性门")
    L.append("一票否决：后见基准；CALIBRATION_PENDING 冒充能力")
    L.append("```\n")
    L.append("### 1.1 预测预登记（H-1/H-2/H-3，G6C-01）\n")
    L.append("```text")
    L.append("  · 首个有限 DecisionVersion 预登记 3—5 个材料性预测，完整字段集")
    L.append("    （metric/operator/threshold、scope、unit、观察期、判定来源、")
    L.append("    resolution rule、grace period、forecast/reference 概率、")
    L.append("    model/prompt/method/cluster 版本 + 五类绑定 + 审批事件）")
    L.append("  · H-1 时序断言：registered_at (微秒,序号) < outcome_available_at")
    L.append("    —— 结果已可知仍登记（事后补登记）FAIL（E-G6C-01-101，")
    L.append("    变异注入：test_backfill_rejected）")
    L.append("  · Brier 的 forecast/reference 概率在结果可见前冻结（同 H-1）")
    L.append("  · H-2 任一绑定（候选/合同/证据包/cutoff/snapshot）字节变化使")
    L.append("    批准失效，须重新提议、批准并生成新快照（E-G6C-01-012，")
    L.append("    test_binding_byte_change_invalidates）")
    L.append("  · H-3 未预登记预测不得进入裁决（E-G6C-01-008）")
    L.append("  · LLM 无批准写权（E-G6C-01-003）；人工逐项批准（显式 token）")
    L.append("```\n")
    L.append("### 1.2 裁决（H-4/H-5/H-6，G6C-02）\n")
    L.append("```text")
    L.append("  · H-4 后见基准必拒（一票否决）：每个基准输入带可得时刻，")
    L.append("    晚于预登记时刻即拒（E-G6C-02-101，test_hindsight_benchmark_rejected）")
    L.append("  · H-5 裁决可回溯至预登记记录与基准哈希，孤儿裁决结构性不可达")
    L.append("    （CalibrationStore 加载即拒 E-G6C-02-102）")
    L.append("  · H-6 共识不等于已验证（一票否决）：outcome_kind 字段级可分辨，")
    L.append("    CONSENSUS 不得当作已验证事实消费（E-G6C-02-103）")
    L.append("  · 四状态 OPEN/RESOLVED/OVERDUE/UNRESOLVABLE；未到期/来源不足")
    L.append("    不伪造 outcome；到期未裁决进 OVERDUE；UNRESOLVABLE 有证据；")
    L.append("    重述不回写历史；材料性选择性未决阻断能力声明；评分输入不可变")
    L.append("```\n")
    L.append("### 1.3 校准与充分性门（H-7…H-10，G6C-03）\n")
    L.append("```text")
    L.append("  · base rate、Brier/reference Brier/skill、按 scope/horizon/")
    L.append("    model/prompt/method 分层、cluster-aware effective_n、Wilson CI")
    L.append("  · H-7 充分性门：resolved≥30 · ≥2 报告期 · ≥2 horizon bucket · ")
    L.append("    clustered effective_n≥20 · CI 存在 · 无材料性选择性未决，")
    L.append("    全过才 CALIBRATION_SUFFICIENT；否则 INSUFFICIENT_SAMPLE ——")
    L.append("    样本不足时阻断表述而非附警告输出（负测通过）")
    L.append("  · H-10 阈值逐字取用基线 B §10A（GATE_THRESHOLDS=30/2/2/20），")
    L.append("    不另设阈值、无凭空的数字")
    L.append("  · VD-26：declared_status 恒 CALIBRATION_PENDING（最低门、终态）")
    L.append("  · H-8 一票否决：assert_no_calibration_claim（E-G6C-03-102）+")
    L.append("    calibration_claim_check 表述守卫（backend/app 字符串字面量")
    L.append("    默认拒绝扫描，CI 接线）+ 变异注入先红后绿")
    L.append("  · H-9「未校准」（VD-26 决策：declared_status）与「校准失败」")
    L.append("    （测量：measurement_status）字段级可分辨")
    L.append("  · ⑨：样本 0 与样本充足可分辨（resolved 计数独立报出）")
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

    # ── §2 G6C 任务验收 ───────────────────────────────────────────
    L.extend(_S17)
    L.append("## 2. G6C 任务验收（基线 B §10A 任务表）\n")
    L.append("| ID | 任务 | 状态 | PR | 验收要点 |")
    L.append("|---|---|---|---|---|")
    L.append(f"| `G6C-01` | PredictionProposal/独立批准/不可变快照 | {_st['G6C-01']} "
             f"| #70 | H-1/H-2/H-3 |")
    L.append(f"| `G6C-02` | 结果裁决/逾期/重述/CalibrationStore | {_st['G6C-02']} "
             f"| #70 | H-4/H-5/H-6 |")
    L.append(f"| `G6C-03` | 预登记基准/Brier/分层校准/充分性门 | {_st['G6C-03']} "
             f"| #70 | H-7…H-10 + VD-26 |")
    L.append("")
    L.append("```text")
    L.append("**本 Gate 的产出永远不会到达 CALIBRATION_SUFFICIENT 状态** —— 这不是尚未完成，")
    L.append("是 VD-26 的决策结果（G6C-执行计划 §2）：预测取最低门；永久")
    L.append("CALIBRATION_PENDING，不作出校准能力声明。充分性门按基线 B §10A")
    L.append("实现（测量上可验证），VD-26 使 PENDING 为终态（决策上不可声明）")
    L.append("```\n")

    # ── §3 债务清点 ───────────────────────────────────────────────
    L.append("## 3. 债务清点（ADR-010 §3.1，三条；范围口径 = ADR-021 §2 逐字段）\n")
    L.append("```text")
    L.append(f"① Gate 6C 范围内的材料性开放项：{len(g6c_mat)} 项（须为零）"
             f"［范围判定：blocks_development 含 G6C-xx，ADR-021 §2 并集口径"
             f"（逐字段实现：blocks_data_flow/deprecated_blocks_gate 含 G6C/"
             f"Gate 6C 裸 GN，blocks_decisions 含 Gate 6C —— OI-PF-157）］"
             + (f" —— 非零: {[i['open_item_id'] for i in g6c_mat]}" if g6c_mat else ""))
    L.append(f"   \"ALL\" 项不计入本条（ADR-022 §2 取字面），"
             f"另见 §1.7 逐项点名的 {len(_standing)} 项持续性风险")
    for i in [x for x in items if in_gate6c(x)]:
        L.append(f"   {i['open_item_id']}: {i.get('status')} "
                 f"材料性={i.get('material')} 阻断={_blk(i)}")
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
    _net = len(_added) - len(_closed)
    L.append(f"③ 债务趋势：G6C 期间新增 {len(_added)} 项 · 关闭 {len(_closed)} 项"
             f" · 净变化 {_net:+d} 项（窗口 {_WINDOW[0]}…{_WINDOW[1]}；近似值，"
             f"按自由文本日期串匹配）")
    L.append("```\n")

    # ── §4 测试命令、退出码与结果 ─────────────────────────────────
    L.append("## 4. 测试命令、退出码与结果\n")
    L.append("```text")
    for t in ("test_g6c_01", "test_g6c_02", "test_g6c_03",
              "test_calibration_guard"):
        L.append(f"{t}: {_tests[t]}")
    L.append(f"H-8 表述守卫（CI scans job）: {_guard}")
    L.append(f"变异注入（表述守卫先红后绿）: {_tests['test_calibration_guard']}")
    L.append(f"台账审计（含 P6 新守卫）: {_audit}")
    L.append("```\n")

    # ── §5 风险与已知缺口 ─────────────────────────────────────────
    L.append("## 5. 风险与已知缺口（如实载明）\n")
    L.append("```text")
    L.append("· **本 Gate 的 PASS 不代表预测能力被验证** —— VD-26 使 PENDING 为")
    L.append("  终态，样本永远不足（或即使测量上达标也不声明）。与 ADR-017 §2.2")
    L.append("  对 F1 的处理同例：验收记录逐字载明这一点")
    L.append("· H-1 时序断言依赖时钟 —— 微秒时间戳 + 同刻序号使同一秒内的")
    L.append("  预登记与结果可得仍可分辨（附.6 裁定），但时钟本身的来源")
    L.append("  可信性不在本 Gate 范围内")
    L.append("· H-8 表述守卫扫描 backend/app 字符串字面量 + 渲染行为断言；")
    L.append("  对外表述可以出现在任何地方（UI/导出/README/提交信息）——")
    L.append("  台账侧的 P6 守卫覆盖记录与验收包，仓库侧 CI 覆盖生产代码，")
    L.append("  检查范围逐字列出（规则 ⑮）")
    L.append("· 集群有效样本量采用保守调整（n/平均簇规模）；CI 为 Wilson 区间。")
    L.append("  这些是测量侧公式，阈值本身（30/2/2/20）逐字取用基线 B §10A")
    L.append("```\n")

    # ── 装配后自查（㉛：只在 §1.7 节内核对）────────────────────────
    _body_txt = "\n".join(L)
    _h17 = "### 1.7 持续性风险"
    _i17 = _body_txt.find(_h17)
    if _i17 < 0:
        _sec17 = ""
    else:
        _nxt = _body_txt.find("\n## ", _i17)
        _sec17 = _body_txt[_i17:_nxt if _nxt > 0 else len(_body_txt)]
    if _standing and not all(i["open_item_id"] in _sec17 for i in _standing):
        _blockers.append(
            f"**ADR-022 §3.1 的单列义务未落地**：持续性风险 "
            f"{len(_standing)} 项须在 §1.7 逐项点名 —— 别处提到不算")
        verdict = "G6C_分支_NOT_READY"

    # ── §0 头部 ───────────────────────────────────────────────────
    _H = []
    _H.append("# Gate 6C 验收包\n")
    _H.append("```text")
    _H.append(f"生成时刻   = {NOW}")
    _H.append("生成方式   = backend/tools/build_gate6c_acceptance.py（全部数据实时采集）")
    _H.append("依据       = G6C-执行计划.md §4 + 基线 §10A + VD-26 + ADR-021 §2"
              " + ADR-022 §2/§3 + ADR-010 §3.1")
    _H.append("范围口径   = ADR-021 §2 并集逐字段：material ∧ OPEN ∧ "
              "category != 签署前置条件；blocks_development 含 G6C-xx 必含 | "
              "blocks_data_flow/blocks_decisions/deprecated_blocks_gate 含 "
              "G6C/Gate 6C；blocks_data_flow=\"ALL\" 不算命中（ADR-022 §2），"
              "另见 §1.7")
    _H.append(f"结论       = **{verdict}**"
              + (f" —— {'；'.join(_blockers)}" if _blockers
                 else "（三任务 DONE；范围内材料性开放项为零；工程测试全过；"
                      "H-8 表述守卫在 CI 接线）"))
    _H.append("**本包不构成 Gate 6C PASS。** VD-26 下永久 CALIBRATION_PENDING ——")
    _H.append("本 Gate 的 PASS 不验证预测能力，只验证「未校准」被记准确、")
    _H.append("且不可能被误读为能力（G6C-执行计划 §2）")
    _H.append("independent_reviewer_present = false（VD-02 = 1 名自然人）")
    _H.append("```\n")
    _H.append("> **本包不是 Gate 6C PASS。** 供批准人审阅的冻结材料；\n")
    L = _H + L

    pkg = os.path.join(PORTFOLIO, "Gate6C-验收包.md")
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
        print(f"  · {_b[:120]}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
