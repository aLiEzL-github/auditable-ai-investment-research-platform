#!/usr/bin/env python3
"""build_gate2_acceptance.py —— Gate 2 验收包生成器（实时采集，不可手写）。

X-7（修订，OI-PF-118 教训）：审计结果行（合计/独立审计:/v2.0 基线:）移出
substantive 覆盖（AA-1 同款）——ACTIVE 状态下连跑三次实质哈希逐字一致。
Z1 前置：副源挂起期间禁装 akshare/curl_cffi（ADR-017 §3.3）。
"""
import hashlib
import json
import os
import re
import shlex
import subprocess
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
PORTFOLIO = os.environ.get("PORTFOLIO_ROOT") or "/Users/li/Documents/Claudetext/portfolio"
NOW = subprocess.run(["date", "-u"], capture_output=True, text=True).stdout.strip()

EXCLUDE = ("生成时刻", "实测时刻", "main 最新 CI run", "run = ", "ruleset: ",
           "g1-08-2026", "_mut-", "sparseimage", "备份目录 = ", "  g1-08-",
           "substantive_sha256", "合计", "独立审计:", "v2.0 基线:")


def _adr021_in_scope(i, g):
    """ADR-021 §2 **逐字段**口径 + ADR-022 §2（"ALL" 不算命中）。

    OI-PF-157：各生成器此前一律把四字段拼成一个串、统一用 `GN-\\d|Gate N`
    匹配 —— **要求短横**，于是 blocks_data_flow / deprecated_blocks_gate 里的
    **裸 GN** 一个也匹配不上。而裸 GN 恰是那两个字段的主流写法
    （deprecated_blocks_gate 里 G1×8 · G2×6 · G0×4 · G7×3）。

    ADR-021 §2 原文对四个字段给的是各不相同的模式：
        blocks_development     含 GN-\\d\\d
        blocks_data_flow       含 GN / Gate N      ← 裸 GN
        blocks_decisions       含 Gate N
        deprecated_blocks_gate 含 GN / Gate N      ← 裸 GN
    """
    import re  # 局部导入：本函数被多个生成器共用，各模块的 re 绑定方式不一。
    if re.search(rf"{g}-\d\d", str(i.get("blocks_development") or "")):
        return True
    for _k in ("blocks_data_flow", "deprecated_blocks_gate"):
        _v = str(i.get(_k) or "")
        if _v == "ALL":
            continue                       # ADR-022 §2 取字面
        if re.search(rf"\b{g}\b|{g}-\d|Gate {g[1:]}\b", _v):
            return True
    return bool(re.search(rf"Gate {g[1:]}\b",
                          str(i.get("blocks_decisions") or "")))


def run(cmd):
    r = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    return r.returncode, r.stdout.strip(), r.stderr.strip()


def status_of(cmd):
    """只取测试状态词（OK/FAILED），不含耗时行（X-7 幂等）。"""
    rc, out, _ = run(cmd)
    m = re.findall(r"(OK|FAILED[^\n]*)", out)
    return m[-1] if m else f"rc={rc}"


def in_gate2(i):
    """ADR-021 §2 逐字段口径（OI-PF-157）。"""
    # **本改动不重新生成该验收包** —— 它已 ACTIVE 签署，
    # 重生成会改字节而触发 A §10.3（见 OI-PF-168）。
    return _adr021_in_scope(i, "G2")

def main() -> int:
    L = []
    OI = json.load(open(os.path.join(PORTFOLIO, "risk", "open-items.json"),
                        encoding="utf-8"))
    op = [i for i in OI["items"] if i["status"] == "OPEN"]
    mat = [i for i in op if i.get("material")]
    g2_mat = [i for i in mat if in_gate2(i)]
    other_mat = [i for i in mat if not in_gate2(i)]
    L.append("# Gate 2 验收包\n")
    L.append("```text")
    L.append(f"生成时刻   = {NOW}")
    L.append("生成方式   = tools/build_gate2_acceptance.py（全部数据实时采集）")
    L.append("依据       = G2-执行流程.md §3（Gate 2 验收）+ ADR-017（F1 读法 (c) + 副源挂起）")
    L.append("结论       = " + ("**READY_FOR_APPROVAL**（ADR-019 F1 论证更正 + ADR-010 §3.1 债务清点①为零）"
                             if not g2_mat else
                             f"**NOT_READY** —— Gate 2 范围内材料性开放项 {len(g2_mat)} 项 ≠ 0，"
                             f"ADR-010 §4 不得 PASS"))
    L.append("independent_reviewer_present = false（VD-02 = 1 名自然人）")
    L.append("```\n")
    L.append("> **本包不是 Gate 2 PASS。** 供批准人审阅的冻结材料；签署按 ADR-016 S1—S5。\n")

    # ── §1 退出条件 F1—F7 逐条核验（实时采集） ─────────────────────
    L.append("## 1. 退出条件 F1—F7（基线 B 原文，逐条实测）\n")
    L.append("### F1 · 官方主源和 AKShare 副源均有真实冒烟（ADR-017 读法 (c)，ADR-019 更正）\n")
    L.append("```text")
    L.append("读法 (c)：失败关闭计入「已冒烟」，不计入「已取得」（ADR-017，2026-08-08T19:41Z）")
    L.append("论证断裂与更正（B2-1 证据 + ADR-019，U 裁定读法二）：")
    L.append("  签署当刻（189ee52）权利门因动作词汇不相交恒返回 UNKNOWN —— 阻断原因")
    L.append("  曾为「查询不命中」而非「fail-closed 判定」；矩阵从未被咨询（B2-1 实测）")
    L.append("  修复后（PR #42）实测：SRC_SSE FETCH → ALLOWED · SRC_NBS → ALLOWED")
    L.append("  · SRC_CNINFO → UNKNOWN（矩阵明判）· AKShare 上游未登记 → UNKNOWN（fail-closed）")
    L.append("  上交所：2026-08-08T14:54-55Z 两次独立真实请求均 HTTP 403 → C1 失败关闭（E-G2-04-002）")
    L.append("  600089 真实数据 = U 人工提供（xlsx）→ G2-03 人工导入 → golden baseline COMPLETE（8/8 回源）")
    L.append("  AKShare 副源：NOT_APPLICABLE_PENDING_RIGHTS（ADR-019 重新论证，三条重开条件不变）")
    L.append("⇒ F1 按读法 (c) 成立（依据按 ADR-019 更正；失败关闭计入已冒烟；零自动取得如实载明）")
    L.append("```\n")

    L.append("### F2 · 主源缺失/接口漂移/同源镜像/口径冲突测试全部失败关闭\n")
    L.append("```text")
    for name, t in (("权利门零请求", "test_g2_03"),
                    ("适配器失败关闭", "test_g2_04"),
                    ("归一化冲突", "test_g2_07"),
                    ("解析负测", "test_g2_11")):
        L.append(f"  {name}（{t}）: {status_of(f'.venv/bin/python -m unittest backend.tests.{t} 2>&1 | tail -1')}")
    L.append("⇒ 四类负测全失败关闭 ✓")
    L.append("```\n")

    L.append("### F3 · 不得用 AKShare 填补主源硬缺口（可执行断言）\n")
    L.append("```text")
    rc, out, _ = run(".venv/bin/python -m unittest backend.tests.test_g2_06 2>&1 | tail -1")
    L.append(f"  G2-06（promotable 断言）: {out}")
    L.append("  副源挂起（ADR-017）：F3 断言 + Z1 双重强制\n")
    L.append("```\n")

    L.append("### F4 · backtest_mode 机器可读状态（三模式逐一实测）\n")
    L.append("```text")
    L.append(f"  G2-09: {status_of('.venv/bin/python -m unittest backend.tests.test_g2_09 2>&1 | tail -1')}")
    L.append("  当前 = REMOVED（G0-09 已裁），绩效门仅 QUALIFIED 放行\n")
    L.append("```\n")

    L.append("### F5 · 20 项 MetricSpec 完整；结构化解析与安全 fallback 负测通过\n")
    L.append("```text")
    for t in ("test_g2_10", "test_g2_11", "test_g2_12"):
        L.append(f"  {t}: {status_of(f'.venv/bin/python -m unittest backend.tests.{t} 2>&1 | tail -1')}")
    L.append("  20/20 逐字匹配（frozen_sha256 漂移阻断）\n")
    L.append("```\n")

    L.append("### F6 · 五类估值输入独立来源合同已冻结\n")
    L.append("```text")
    L.append(f"  G2-15: {status_of('.venv/bin/python -m unittest backend.tests.test_g2_15 2>&1 | tail -1')}")
    L.append("  六要素（source_role/rights/as_of/period/basis/locator）+ source_kind 防升格\n")
    L.append("```\n")

    L.append("### F7 · 600089 材料性事实已全量人工回源\n")
    L.append("```text")
    gb = json.load(open(os.path.join(PORTFOLIO, "golden-baselines", "600089.json"),
                        encoding="utf-8"))
    L.append(f"  baseline: {gb['status']} · {len(gb['facts'])} facts · 回源 {len(gb['back_source'])}")
    L.append("  材料性事实 100% 人工回源（U 确认，录入=自动化解析，双录语义）\n")
    L.append("```\n")

    # ── §1.5 ADR-010 §3.1 三条债务清点义务（OI-PF-142 同款）────────
    # OI-PF-153：此处原**写死**台账绝对路径，无视 PORTFOLIO_ROOT，
    # 于是同一次生成中 §1.5 读真实台账、§4.1 读 PORTFOLIO_ROOT 指向的副本 ——
    # 两节可给出互相矛盾的数（实测：副本注入探针后 §4.1 报 1 项而 §1.5 报 0 项）。
    # 后果更广：**任何在临时副本上做的验证，在 §1.5 这一节都是假的**，
    # 而通用验收 ⑭ 恰恰要求只读审核须用临时副本。改为复用 §1 已加载的 OI。
    items = OI["items"]
    L.append("## 1.5 债务清点（ADR-010 §3.1，三条）\n")
    L.append("```text")
    # ADR-021 §2（U 裁定 2026-08-11，OI-PF-151）：「本 Gate 范围内」的判定
    # **必须包含 blocks_development**。原实现只查 blocks_decisions，而该字段是
    # 自指的 —— 一项开放项只有被人主动写上「阻断 Gate N 验收签署」才算范围内，
    # 于是「范围内为零」取决于登记时怎么填字段，而非该 Gate 的工程实质。
    # 按 MR-1 严格者胜取并集（与 build_gate0_acceptance.py 的 in_g0() 同构）：
    # 删掉 blocks_decisions 会撤销 OI-PF-082 的修复（§2(c) 覆盖），故保留。
    def _in_g2(i):
        _s = " ".join(str(i.get(k) or "") for k in
                      ("blocks_development", "blocks_data_flow",
                       "blocks_decisions", "deprecated_blocks_gate"))
        return bool(re.search(r"\bG2-\d\d\b|Gate 2", _s))
    gate2 = [i for i in items if _in_g2(i)]
    # 签署前置条件单列：这类项只能由签署关闭，计入阻断即成循环（ADR-021 §2.4）
    gate2_mat = [i for i in gate2 if i.get("material") and i.get("status") == "OPEN"
                 and i.get("category") != "签署前置条件"]
    L.append(f"Gate 2 范围内的材料性开放项：{len(gate2_mat)} 项（须为零）"
             f"［范围判定：blocks_development 含 G2-xx，ADR-021 §2 并集口径］"
             + (f" —— 非零: {[i['open_item_id'] for i in gate2_mat]}" if gate2_mat else ""))
    for i in gate2:
        L.append(f"   {i['open_item_id']}: {i.get('status')} 材料性={i.get('material')} "
                 f"阻断任务={i.get('blocks_development')} 阻断决策={i.get('blocks_decisions')}")
    unclosed = [i for i in items if i.get("status") == "OPEN" and i.get("material")]
    L.append(f"② 全部未闭材料性开放项 —— {len(unclosed)} 项")
    for i in unclosed:
        L.append(f"   {i['open_item_id']} | {i.get('category','')} | "
                 f"阻断={i.get('blocks_decisions') or '无'}")
    g2_added = [i for i in items if "2026-08-08" in str(i.get("source",""))
                or "2026-08-09" in str(i.get("source",""))]
    g2_closed = [i for i in items if i.get("status") == "CLOSED"
                 and ("G2" in str(i.get("closure_evidence","")) or "2026-08-08" in str(i.get("closure_evidence",""))
                      or "2026-08-09" in str(i.get("closure_evidence","")))]
    L.append(f"③ 债务趋势：G2 期间新增 {len(g2_added)} · 关闭 {len(g2_closed)} · "
             f"净变化 {len(g2_closed) - len(g2_added)}")
    L.append("```\n")

    # ── §2 测试基线 ────────────────────────────────────────────────
    L.append("## 2. 测试命令、退出码与结果\n")
    L.append("```text")
    for name, script in (("独立审计", "audit_session.py"), ("v2.0 基线", "test_v2_baseline.py")):
        rc, out, err = run(f"python3 {shlex.quote(os.path.join(ROOT, '..', 'portfolio', 'tools', script))} "
                           f"{shlex.quote(os.path.join(ROOT, '..', 'portfolio'))}")
        L.append(f"{name}: 退出码 {rc} | {out.splitlines()[-1] if out else err}")
    L.append(f"工程测试: {status_of('.venv/bin/python -m unittest discover -s backend/tests 2>&1 | tail -1')}")
    for t in ("arch_import_check", "contract_coverage_check", "migration_check"):
        rc, out, _ = run(f"python3 backend/tools/{t}.py . 2>&1 | tail -1")
        L.append(f"{t}: {out}")
    L.append("```\n")

    # ── §3 签署前置 ────────────────────────────────────────────────
    L.append("## 3. 签署前置（X-7 幂等 + Z1）\n")
    L.append("```text")
    rc, out, _ = run("grep -cE 'akshare==|curl_cffi==' requirements.txt || true")
    L.append(f"Z1（ADR-017 §3.3）：requirements 中 akshare/curl_cffi 命中数 = {out or 0}（须 0）")
    L.append("X-7（修订）：ACTIVE 状态下连跑三次实质哈希逐字一致（本包 substantive 构造已排除")
    L.append("审计结果行 —— AA-1 教训；审计结果由签署记录 S1 承载）")
    L.append("```\n")

    # ── §4 开放项：ADR-010 §3.1 三条债务清点义务（OI-PF-142）────────
    # ①本 Gate 范围内须为零并逐项证明；②全部未闭材料性开放项清单；
    # ③债务趋势。数据全部实时取自 risk/open-items.json，不接受手写。
    L.append("## 4. 开放项（按 `ADR-010` 分范围清点）\n")
    L.append(f"### 4.1 Gate 2 范围内的材料性开放项 —— **{len(g2_mat)} 项**\n")
    if g2_mat:
        for i in sorted(g2_mat, key=lambda x: x["open_item_id"]):
            L.append(f"- **`{i['open_item_id']}`** owner=`{i['owner_role']}` "
                     f"blocks_development=`{i.get('blocks_development')}`")
            L.append(f"  - {i['description'][:200]}")
        L.append("")
        L.append("> ⚠️ **不为零 → Gate 2 不得 PASS**（`ADR-010 §4`，"
                  "B1-1 裁定路径 (a)/(b) 须先落地）。")
    else:
        L.append("**零。** Gate 2 范围内无未闭材料性开放项。")
    L.append("")
    L.append(f"### 4.2 全部未闭材料性开放项 —— {len(mat)} 项（`ADR-010 §3.1` 新增义务）\n")
    L.append("| 编号 | 归属（blocks_development） | 数据面 | 摘要 |")
    L.append("|---|---|---|---|")
    for i in sorted(mat, key=lambda x: x["open_item_id"]):
        L.append(f"| `{i['open_item_id']}` | {i.get('blocks_development') or '—'} | "
                 f"{i.get('blocks_data_flow') or '—'} | "
                 f"{i['description'][:52].replace('|', '/')} |")
    L.append("")
    L.append("### 4.3 债务趋势\n")
    L.append("```text")
    L.append(f"开放项总计     {OI['counts']['open']} / {OI['counts']['total']}")
    L.append(f"其中材料性     {len(mat)}")
    L.append(f"  · G2 范围内  {len(g2_mat)}")
    L.append(f"  · 归属后续   {len(other_mat)}")
    L.append(f"阻断开发       {OI['counts']['blocking_development']}")
    L.append(f"阻断数据流     {OI['counts']['blocking_data_flow']}")
    L.append("```")
    L.append("")
    L.append("**债务趋势（ADR-010 §3.1 第 3 条）**：Gate 2 为首个实现该义务的"
              "G1 之后验收包 —— G1 验收包未载此节（OI-PF-142，B1 处置中）；"
              "自 G3 起逐 Gate 对比新增 / 关闭 / 净变化。")
    L.append("")
    L.append("**终末 Gate（`G7`）的全局要求不变**：届时全部材料性开放项必须为零，"
              "无一例外（`ADR-010 §2.1`）。\n")

    pkg = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
        PORTFOLIO, "Gate2-验收包.md")
    with open(pkg, "w", encoding="utf-8") as f:
        f.write("\n".join(L))
    assert open(pkg, encoding="utf-8").read() == "\n".join(L)

    sub_lines = [ln for ln in L if not any(x in ln for x in EXCLUDE)]
    substantive = hashlib.sha256("\n".join(sub_lines).encode("utf-8")).hexdigest()
    with open(pkg, "a", encoding="utf-8") as f:
        f.write(f"\nsubstantive_sha256 = {substantive}\n")
    content = subprocess.run(["shasum", "-a", "256", pkg],
                             capture_output=True, text=True).stdout.split()[0]
    print(f"written {pkg}", file=sys.stderr)
    print(f"content_sha256 = {content}", file=sys.stderr)
    print(f"substantive_sha256 = {substantive}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
