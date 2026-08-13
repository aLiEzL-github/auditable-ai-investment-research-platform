#!/usr/bin/env python3
"""build_gate6a_acceptance.py —— Gate 6A 验收包生成器（实时采集，不可手写）。

依据：
  · G6A-执行计划.md §1A（基线 B §9 任务表）· §4（F-1…F-12）· §6
  · ADR-012（条件分支：01/05/06 执行；02/03/04 NOT_APPLICABLE_PENDING_PROVIDER）
  · ADR-021 §2（范围口径：四字段逐字段，blocks_development 必含 G6A-\\d\\d）
  · ADR-022 §2/§3.1（blocks_data_flow="ALL" 不算命中，但须单列一节）
  · ADR-010 §3.1（三条债务清点义务，S3 逐条机检）

前五个 Gate 的教训，本文件逐条落地：
  ㉑ 结论由范围内计数与任务状态决定，不得硬编码
  ㉒ 台账路径尊重 PORTFOLIO_ROOT
  ⑨  执行 3 项与挂起 3 项**分别报数**，合并成「6 项已处理」即违规
  ㉞ pipefail 强制打开；㉟「没跑起来」必须自己说出来
  ㉛ §1.7 单列节的自查只在**该节范围内**核对
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
NOW = subprocess.run(["date", "-u"], capture_output=True, text=True).stdout.strip()

# 与 audit_session.py 的 _SUB_EXCLUDE **逐字一致**（14 条）
EXCLUDE = ("生成时刻", "实测时刻", "main 最新 CI run", "run = ", "ruleset: ",
           "g1-08-2026", "_mut-", "sparseimage", "备份目录 = ", "  g1-08-",
           "substantive_sha256", "合计", "独立审计:", "v2.0 基线:")

EXEC_TASKS = ("G6A-01", "G6A-05", "G6A-06")
SUSPENDED_TASKS = ("G6A-02", "G6A-03", "G6A-04")


def run(cmd):
    """pipefail 强制打开（㉞）：`python … | tail -1` 的退出码不得取自 tail。"""
    r = subprocess.run(["/bin/sh", "-o", "pipefail", "-c", cmd],
                       capture_output=True, text=True, cwd=ROOT)
    return r.returncode, r.stdout.strip(), r.stderr.strip()


def status_of(cmd):
    """只取状态词（OK/FAILED）；取不到状态词时**显式说没跑起来**（㉟）。"""
    rc, out, err = run(cmd)
    m = re.findall(r"(OK|FAILED[^\n]*)", out or err)
    if m:
        return m[-1]
    _tail = ((err or out).splitlines() or ["（无输出）"])[-1][:70]
    return f"**未跑起来（rc={rc}）**：{_tail}"


def in_gate6a(i):
    """ADR-021 §2 的**逐字段**口径（与 in_gate5 同构，OI-PF-157）：
        blocks_development     含 G6A-\\d\\d
        blocks_data_flow       含 G6A / Gate 6A（裸 GN）
        blocks_decisions       含 Gate 6A
        deprecated_blocks_gate 含 G6A / Gate 6A（裸 GN）
    """
    if re.search(r"G6A-\d\d", str(i.get("blocks_development") or "")):
        return True
    for k in ("blocks_data_flow", "deprecated_blocks_gate"):
        if re.search(r"\bG6A\b|G6A-\d|Gate 6A", str(i.get(k) or "")):
            return True
    return bool(re.search(r"Gate 6A", str(i.get("blocks_decisions") or "")))


def is_standing_risk(i):
    """blocks_data_flow == "ALL" 的持续性风险项（ADR-022，单列义务）。"""
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
    op = [i for i in items if i["status"] == "OPEN"]
    mat = [i for i in op if i.get("material")]
    g6a_mat = [i for i in mat if in_gate6a(i)
               and i.get("category") != "签署前置条件"]
    _standing = [i for i in mat if is_standing_risk(i)]

    # ── G6A 任务状态实测 ──────────────────────────────────────────
    _tr = os.path.join(PORTFOLIO, "task-records")
    _st = {}
    for tid in EXEC_TASKS + SUSPENDED_TASKS:
        fp = os.path.join(_tr, f"{tid}.json")
        if os.path.exists(fp):
            _d = json.load(open(fp, encoding="utf-8"))
            _st[tid] = str(_d.get("task_status") or "?")
        else:
            _st[tid] = "NO_RECORD"

    _exec_ok = all(_st[t] in ("DONE", "REVIEW_REQUIRED") for t in EXEC_TASKS)
    _susp_ok = all(_st[t] == "NOT_APPLICABLE_PENDING_PROVIDER"
                   for t in SUSPENDED_TASKS)
    # F-6：单人期 G6A-06 必须 REVIEW_REQUIRED，不得径自判 PASS
    _g6a06_ok = _st["G6A-06"] == "REVIEW_REQUIRED"

    # ── 工程证据 ──────────────────────────────────────────────────
    _t_g6a01 = status_of(
        ".venv/bin/python -m unittest backend.tests.test_g6a_01 2>&1 | tail -1")
    _t_g6a05 = status_of(
        ".venv/bin/python -m unittest backend.tests.test_g6a_05 2>&1 | tail -1")
    _audit = status_of(
        f"python3 {os.path.join(PORTFOLIO, 'tools/audit_session.py')} "
        f"{PORTFOLIO} 2>&1 | tail -1")
    # 台账审计输出是「合计 N 项：PASS x / FAIL y」—— status_of 的状态词
    # 解析不适用；当刻复算 PASS/FAIL 计数（⑨：报检查对象数）
    _rc_a, _o_a, _e_a = run(
        f"python3 {os.path.join(PORTFOLIO, 'tools/audit_session.py')} "
        f"{PORTFOLIO} 2>&1")
    _m_a = re.search(r"合计 (\d+) 项：PASS (\d+) / FAIL (\d+)", _o_a or _e_a)
    if _m_a:
        _audit_detail = (f"OK（{_m_a.group(1)} 项：PASS {_m_a.group(2)} / "
                         f"FAIL {_m_a.group(3)}）"
                         if _m_a.group(3) == "0"
                         else f"FAILED（PASS {_m_a.group(2)} / "
                              f"FAIL {_m_a.group(3)}）")
        _audit = _audit_detail
    else:
        _audit = f"**未跑起来（rc={_rc_a}）**：{(_e_a or _o_a).splitlines()[-1][:70]}"

    # ── 结论（㉑：由计数与状态决定）────────────────────────────────
    _blockers = []
    if g6a_mat:
        _blockers.append(f"Gate 6A 范围内材料性开放项 {len(g6a_mat)} 项 ≠ 0"
                         f"（ADR-010 §4 不得 PASS）："
                         f"{[i['open_item_id'] for i in g6a_mat]}")
    if not _exec_ok:
        _blockers.append(f"执行任务状态不符（实测 {_st}）—— 01/05 须 DONE，"
                         f"06 单人期须 REVIEW_REQUIRED")
    if not _g6a06_ok:
        _blockers.append(f"**F-6：G6A-06 状态 = {_st['G6A-06']}，须 "
                         f"REVIEW_REQUIRED** —— 单人期不得径自判 PASS"
                         f"（ADR-012 §3）")
    if not _susp_ok:
        _blockers.append(f"挂起任务标签不符（实测 {_st}）—— 02/03/04 须为 "
                         f"NOT_APPLICABLE_PENDING_PROVIDER（F-9，"
                         f"裸 NOT_APPLICABLE 会把可逆伪装成永久）")
    _tests_ok = _t_g6a01.startswith("OK") and _t_g6a05.startswith("OK")
    if not _tests_ok:
        _blockers.append(f"G6A 工程测试未全过（G6A-01={_t_g6a01}；"
                         f"G6A-05={_t_g6a05}）")
    if not _audit.startswith("OK"):
        _blockers.append(f"台账审计未全绿（{_audit}）")
    verdict = "G6A_分支_READY" if not _blockers else "G6A_分支_NOT_READY"

    # ── §1 基线 §9 证明义务 ───────────────────────────────────────
    L.append("## 1. 基线 §9 证明义务（逐条实测）\n")
    L.append("```text")
    L.append("必须证明：首轮哈希冻结、注入检测、假设批准后全量回算")
    L.append("一票否决：共识冒充已验证事实")
    L.append("（预测预登记与充分性门属 G6C，见 Gate6C-验收包.md）")
    L.append("```\n")
    L.append("### 1.1 注入检测（F-1，G6A-01）\n")
    L.append("```text")
    L.append("  · 不依赖 LLM（纯模式匹配语料 INJECTION_PATTERNS）")
    L.append("  · 负测：8 条注入载荷逐条检出（test_injection_corpus_detected）")
    L.append("  · 命中即记 SUSPECTED_PROMPT_INJECTION 并转人工：consume()")
    L.append("    无人工复核决定即拒绝（E-G6A-01-020，fail-closed）")
    L.append("  · 检查对象数实测报出（⑨）：inspected 随对象增多而增多")
    L.append("  · 变异注入：删语料条目 → 负测转红")
    L.append("```\n")
    L.append("### 1.2 首轮哈希冻结（F-2，G6A-01）\n")
    L.append("```text")
    L.append("  · 证据包内容寻址冻结：evidence_pack_id = sha256(规范字节)")
    L.append("  · 白名单/分级是包内容的一部分 —— 诱导指令无法改变它们，")
    L.append("    改一字节即换包 id（test_any_byte_change_new_id）")
    L.append("  · 首轮冻结在任何对抗轮次之前：frozen_at (微秒,序号) < 对抗开始")
    L.append("    （(timestamp, seq) 字典序，time_order.cmp_micro）")
    L.append("```\n")
    L.append("### 1.3 假设批准后全量回算（F-3/F-4，G6A-05）\n")
    L.append("```text")
    L.append("  · 全量：注册表内每个产物（CalcLedger/四路估值/三情景/")
    L.append("    Claim map/emission map/OpenItem/candidate hash）都重新生成，")
    L.append("    缺一即 E-G6A-05-001（变异注入：摘项须 FAIL）")
    L.append("  · 「受影响」判定落库：PRODUCT_DEPS（产物→假设键，F-4）")
    L.append("  · 批准后从冻结输入全量回算而非局部手改；拒绝项用合同默认值")
    L.append("  · 旧 candidate 失效并保留（invalidate_previous）；差异可审计")
    L.append("    （recompute_diff 逐产物 before/after）")
    L.append("  · Agent/裁决无批准权（G3-13 复用：LLM/AUTOMATION 拒绝）")
    L.append("```\n")
    L.append("### 1.4 共识不等于已验证（F-7，一票否决）\n")
    L.append("```text")
    L.append("  · 消费记录 consumption_kind=INJECTION_CLEAN / HUMAN_REVIEW 字段级")
    L.append("    可分辨；命中注入的包在人工复核（human_decision=APPROVE）前")
    L.append("    不得消费 —— 无人复核的「共识」不可读为可验证")
    L.append("```\n")

    # ── §1.7 持续性风险节（ADR-022 §3.1，先构造后自查）────────────
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

    # ── §2 G6A 任务验收（执行 3 项与挂起 3 项**分列报数**，⑨）──────
    L.extend(_S17)
    L.append("## 2. G6A 任务验收（基线 B §9 任务表；执行 3 项 / 挂起 3 项分列）\n")
    L.append("### 2.1 执行部分（ADR-012：照常执行）\n")
    L.append("| ID | 任务 | 状态 | PR | 验收要点 |")
    L.append("|---|---|---|---|---|")
    L.append(f"| `G6A-01` | 冻结 evidence_pack_id 和角色权限 | {_st['G6A-01']} | "
             f"#68 | F-1/F-2（注入检测、首轮冻结时序） |")
    L.append(f"| `G6A-05` | 批准或拒绝假设并确定性回算 | {_st['G6A-05']} | "
             f"#69 | F-3/F-4（全量回算、受影响落库） |")
    L.append(f"| `G6A-06` | 只找错红队审查 | {_st['G6A-06']} | — | "
             f"F-6：单人期 REVIEW_REQUIRED（ADR-012 §3），"
             f"**不得径自判 PASS** |")
    L.append("")
    L.append("### 2.2 挂起部分（NOT_APPLICABLE_PENDING_PROVIDER，可逆 —— F-8）\n")
    L.append("```text")
    L.append("以下三项**不是「跳过未做」**（ADR-012 §2.1，与 G6B 表述一致）：")
    for t in SUSPENDED_TASKS:
        L.append(f"  · {t}: {_st[t]}（依据 VD-10=provider NONE）")
    L.append("重开触发：VD-10 的 provider 由 NONE 改为任何非 NONE 值 →")
    L.append("  ① 三任务自动恢复 PENDING，工时 +4.00 回填；")
    L.append("  ② 须形成新的决定事件和安全证据；③ 里程碑表重新核定（+4.80 ≈ +1 周）")
    L.append("反事实工时 4.00 人日为**挂起未做**，不计入已完成，也不得从总量中")
    L.append("消失（MR-6 不发明节省，F-12）")
    L.append("```\n")

    # ── §3 债务清点（ADR-010 §3.1 三条）───────────────────────────
    L.append("## 3. 债务清点（ADR-010 §3.1，三条；范围口径 = ADR-021 §2 逐字段）\n")
    L.append("```text")
    L.append(f"① Gate 6A 范围内的材料性开放项：{len(g6a_mat)} 项（须为零）"
             f"［范围判定：blocks_development 含 G6A-xx，ADR-021 §2 并集口径"
             f"（逐字段实现：blocks_data_flow/deprecated_blocks_gate 含 G6A/"
             f"Gate 6A 裸 GN，blocks_decisions 含 Gate 6A —— OI-PF-157）］"
             + (f" —— 非零: {[i['open_item_id'] for i in g6a_mat]}" if g6a_mat else ""))
    L.append(f"   \"ALL\" 项不计入本条（ADR-022 §2 取字面），"
             f"另见 §1.7 逐项点名的 {len(_standing)} 项持续性风险")
    for i in [x for x in items if in_gate6a(x)]:
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
    L.append(f"③ 债务趋势（ADR-010 §3.1 第 3 条）：G6A 期间新增 {len(_added)} 项 · "
             f"关闭 {len(_closed)} 项 · 净变化 {_net:+d} 项（负数 = 债务净减少）")
    L.append(f"   窗口 = {_WINDOW[0]}…{_WINDOW[1]}（G6A 开工至本包生成）")
    L.append("   度量基础：按 source / closure_evidence 自由文本中的日期串匹配，")
    L.append("   为近似值 —— 足以看趋势方向，不足以作精确计数引用。")
    L.append("```\n")

    # ── §4 测试命令、退出码与结果 ─────────────────────────────────
    L.append("## 4. 测试命令、退出码与结果\n")
    L.append("```text")
    L.append(f"G6A-01 工程测试: {_t_g6a01}")
    L.append(f"G6A-05 工程测试: {_t_g6a05}")
    L.append(f"台账审计（含 P5 新守卫）: {_audit}")
    L.append("```\n")

    # ── §5 风险与已知缺口 ─────────────────────────────────────────
    L.append("## 5. 风险与已知缺口（如实载明）\n")
    L.append("```text")
    L.append("· **G6A-06 单人期 REVIEW_REQUIRED → G6-01 汇合受阻**（ADR-012 §5：")
    L.append("  G6A-06 是无条件前置；G6B 才是有条件支线）。解除路径 = VD-02")
    L.append("  重开条款（补到第 2 名自然人）。**本包不声称 G6A-06 已 PASS**")
    L.append("· G6A-05 的基线前置含 G6A-04（挂起）—— ADR-012 未处理该依赖，")
    L.append("  本包按 G6A-执行计划 §7 如实记载：AssumptionProposal 来源为人工")
    L.append("  研究路径（G3-13 语义），不依赖 LLM 角色输出")
    L.append("· VD-02 §(b)：G6A-05 在单人期间保持 REVIEW_REQUIRED —— 无独立")
    L.append("  批准人时假设不得进入计算（与 G2-13 同等待遇，诚实降级）")
    L.append("· 注入语料是有限集合 —— 检出的是语料命中的形态；语料外的新形态")
    L.append("  需要人工审查与语料扩充，本包不声称穷尽所有注入向量")
    L.append("```\n")

    # ── 装配后自查（㉛：只在 §1.7 节范围内核对）────────────────────
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
        verdict = "G6A_分支_NOT_READY"

    # ── §0 头部（结论行）—— 装配完毕后才构造并前置 ────────────────
    _H = []
    _H.append("# Gate 6A 验收包\n")
    _H.append("```text")
    _H.append(f"生成时刻   = {NOW}")
    _H.append("生成方式   = backend/tools/build_gate6a_acceptance.py（全部数据实时采集）")
    _H.append("依据       = G6A-执行计划.md §4 + 基线 §9 + ADR-012 + ADR-021 §2"
              " + ADR-022 §2/§3 + ADR-010 §3.1")
    _H.append("范围口径   = ADR-021 §2 并集逐字段：material ∧ OPEN ∧ "
              "category != 签署前置条件；blocks_development 含 G6A-xx 必含 | "
              "blocks_data_flow/blocks_decisions/deprecated_blocks_gate 含 "
              "G6A/Gate 6A；blocks_data_flow=\"ALL\" 不算命中（ADR-022 §2），"
              "另见 §1.7")
    _H.append(f"结论       = **{verdict}**"
              + (f" —— {'；'.join(_blockers)}" if _blockers
                 else "（执行 3 项已就绪：01/05 DONE、06 REVIEW_REQUIRED；"
                      "挂起 3 项标签与重开条件齐备；范围内材料性开放项为零）"))
    _H.append("**本包不构成 G6-01 汇合 PASS** —— G6A-06 单人期 REVIEW_REQUIRED，")
    _H.append("G6-01 相应受阻（ADR-012 §5）；汇合见 Gate6-验收包.md")
    _H.append("independent_reviewer_present = false（VD-02 = 1 名自然人）")
    _H.append("```\n")
    _H.append("> **本包不是 Gate 6A PASS。** 供批准人审阅的冻结材料；\n")
    L = _H + L

    pkg = os.path.join(PORTFOLIO, "Gate6A-验收包.md")
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
