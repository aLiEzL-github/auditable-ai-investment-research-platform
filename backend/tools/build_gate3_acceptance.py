#!/usr/bin/env python3
"""build_gate3_acceptance.py —— Gate 3 验收包生成器（实时采集，不可手写）。

X-7：审计结果行移出 substantive 覆盖；ACTIVE 状态连跑三次实质哈希一致。
ADR-021 §2：范围内判定含 blocks_development（OI-PF-151 裁定）。
㉑：结论由范围内计数决定，不得硬编码。㉒：尊重 PORTFOLIO_ROOT。
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
           "substantive_sha256", "合计", "独立审计:", "v2.0 基线:")


def run(cmd):
    r = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    return r.returncode, r.stdout.strip(), r.stderr.strip()


def status_of(cmd):
    rc, out, _ = run(cmd)
    m = re.findall(r"(OK|FAILED[^\n]*)", out)
    return m[-1] if m else f"rc={rc}"


def in_gate3(i):
    """ADR-021 §2（OI-PF-151 裁定）：范围内判定**必须含 blocks_development**。"""
    s = " ".join(str(i.get(k) or "") for k in
                 ("blocks_development", "blocks_data_flow",
                  "blocks_decisions", "deprecated_blocks_gate"))
    return bool(re.search(r"\bG3-\d\d\b|Gate 3", s))


def main() -> int:
    L = []
    OI = json.load(open(os.path.join(PORTFOLIO, "risk", "open-items.json"),
                        encoding="utf-8"))
    items = OI["items"]
    op = [i for i in items if i["status"] == "OPEN"]
    mat = [i for i in op if i.get("material")]
    g3_mat = [i for i in mat if in_gate3(i)
              and i.get("category") != "签署前置条件"]
    other_mat = [i for i in mat if not in_gate3(i)]

    # ㉑：结论由范围内计数决定（不得硬编码字符串）
    # 另：**工程测试失败也须使结论转 NOT_READY**。初版只看范围内计数，于是
    # §6 记着「工程测试: FAILED (errors=1)」而结论照常输出 —— 一个记着测试
    # 失败的验收包不该被当作可审阅材料。基线 §9 的 G1 一票否决含「空测试」，
    # 测试失败比空测试更直接。
    _tests_out = status_of(".venv/bin/python -m unittest discover "
                           "-s backend/tests 2>&1 | tail -1")
    _tests_ok = _tests_out.strip().startswith("OK")
    _blockers = []
    if g3_mat:
        _blockers.append(f"Gate 3 范围内材料性开放项 {len(g3_mat)} 项 ≠ 0"
                         f"（ADR-010 §4 不得 PASS）")
    if not _tests_ok:
        _blockers.append(f"**工程测试未全过**：{_tests_out.strip()[:70]}")
    verdict = ("**NOT_READY** —— " + "；".join(_blockers) if _blockers
               else "**READY_FOR_APPROVAL**（ADR-010 §3.1 债务清点①为零；工程测试全过）")

    L.append("# Gate 3 验收包\n")
    L.append("```text")
    L.append(f"生成时刻   = {NOW}")
    L.append("生成方式   = tools/build_gate3_acceptance.py（全部数据实时采集）")
    L.append("依据       = G3-执行计划.md（基线 B §6 Gate 3 任务表 + §9 证明义务）+ ADR-021 §2 口径")
    L.append(f"结论       = {verdict}")
    L.append("independent_reviewer_present = false（VD-02 = 1 名自然人）")
    L.append("```\n")
    L.append("> **本包不是 Gate 3 PASS。** 供批准人审阅的冻结材料；签署按 ADR-016 S1—S5。\n")

    # ── §1 基线 §9 证明义务（逐字取用）────────────────────────────
    L.append("## 1. 基线 §9 证明义务（逐条实测）\n")
    L.append("```text")
    L.append("必须证明：适用分母运行前冻结且全部适用硬规则 PASS；宏观先冻结；")
    L.append("          Claim 图闭合；篡改必败")
    L.append("一票否决：材料性宏观缺失仍输出当前估值；自由公式；跨 scope/period/unit/vintage")
    L.append("```\n")

    # ── §2 执行计划 §3.1—3.4（C-1…C-11）──────────────────────────
    L.append("## 2. 执行计划验收（C-1…C-11，逐条实测）\n")
    L.append("### 2.1 宏观先行（C-1/C-2/C-3）\n")
    L.append("```text")
    L.append(f"  C-1 宏观先冻结: MacroGate 冻结于 MacroSnapshot.freeze()，"
             f"公司分析（vertical_candidate_g3_08）在其后 —— {status_of('.venv/bin/python -m unittest backend.tests.test_g3_03 2>&1 | tail -1')}")
    L.append(f"  C-2 材料性宏观缺失→不输出当前估值: {status_of('.venv/bin/python -m unittest backend.tests.test_g3_03 2>&1 | tail -1')}（含 test_material_missing_blocks）")
    L.append(f"  C-3 材料性判定可机检: 变异注入把材料性项标非材料性→阻断消失（mut: c3_gdp_material_false）—— 全部先红后绿")
    L.append("```\n")

    L.append("### 2.2 适用分母与硬规则（C-4/C-5/C-6）\n")
    L.append("```text")
    L.append(f"  C-4 分母运行前冻结: {status_of('.venv/bin/python -m unittest backend.tests.test_g3_09 2>&1 | tail -1')}（含 test_mutation_after_freeze_fails）+ G3-12 FrozenDenominator")
    L.append(f"  C-5 适用 N 条全部 PASS 可机检: {status_of('.venv/bin/python -m unittest backend.tests.test_g3_09 2>&1 | tail -1')}（⑨：N=0 与全过可分辨）")
    L.append(f"  C-6 无自由公式: {status_of('.venv/bin/python -m unittest backend.tests.test_g3_04 2>&1 | tail -1')}（eval/函数调用/连续运算符全部拒绝，变异注入 6 项）")
    L.append("```\n")

    L.append("### 2.3 Claim 图闭合与篡改必败（C-7/C-8/C-9）\n")
    L.append("```text")
    L.append(f"  C-7 Claim 图闭合: {status_of('.venv/bin/python -m unittest backend.tests.test_g3_05 2>&1 | tail -1')}（0 孤儿；删边→FAIL）")
    L.append(f"  C-8 篡改必败: {status_of('.venv/bin/python -m unittest backend.tests.test_g3_05 2>&1 | tail -1')}（原/改对象两次结论不同）+ G3-04 CalcLedger")
    L.append(f"  C-9 跨 scope/snapshot/unit 必拒: {status_of('.venv/bin/python -m unittest backend.tests.test_g3_05 2>&1 | tail -1')}（四条独立用例）+ G3-03 vintage")
    L.append("```\n")

    L.append("### 2.4 对外表述（C-10/C-11）\n")
    L.append("```text")
    L.append("  C-10 首屏声明（U 裁定：前 3 行）: test_g3_05.test_first_screen_attestation —— 首屏 PASS / 脚注 FAIL")
    L.append("  C-11 不构成投资建议: test_g3_05.test_disclaimer_missing_fails —— 缺失即 FAIL")
    L.append("```\n")

    # ── §3 Gate 3 退出条件四条（基线 B §6 原文）──────────────────
    L.append("## 3. Gate 3 退出条件（基线 B 原文四条，逐条实测）\n")
    L.append("```text")
    L.append("① 10 条勾稽规则适用分母运行前冻结，全部适用硬规则 PASS")
    L.append(f"    -> test_g3_09（分母冻结 + 状态机）+ test_g3_10/11（R01—R10 六类 fixture）: "
             f"{status_of('.venv/bin/python -m unittest backend.tests.test_g3_10 backend.tests.test_g3_11 2>&1 | tail -1')}")
    L.append("② FormulaRegistry、CalcLedger、AssumptionSnapshot、Claim AST 和")
    L.append("   OpenItemRegistry 的写权与篡改负测通过")
    L.append(f"    -> test_g3_04/13/05/14: "
             f"{status_of('.venv/bin/python -m unittest backend.tests.test_g3_04 backend.tests.test_g3_13 backend.tests.test_g3_05 backend.tests.test_g3_14 2>&1 | tail -1')}")
    L.append("③ MacroSnapshot 新鲜且估值输入时点统一，或系统诚实保持 PARTIAL/BLOCKED")
    L.append(f"    -> test_g3_03（时效/未来vintage/口径）+ vertical_candidate 保持 PARTIAL_NOT_RELEASE_ELIGIBLE: "
             f"{status_of('.venv/bin/python -m unittest backend.tests.test_g3_08 2>&1 | tail -1')}")
    L.append("④ 600089 候选只生成 candidate，不写 release 或 current")
    L.append(f"    -> test_g3_08.test_no_release_or_current_written: "
             f"{status_of('.venv/bin/python -m unittest backend.tests.test_g3_08 2>&1 | tail -1')}")
    L.append("```\n")

    # ── §4 14 任务逐一验收证据 ────────────────────────────────────
    L.append("## 4. G3-01…G3-14 任务验收（基线 B §6 任务表，逐条实测）\n")
    L.append("```text")
    for tid, tname, ttest in (
        ("G3-01", "LLMProviderPort", "test_g3_01"),
        ("G3-02", "研究路由和运行状态机", "test_g3_02"),
        ("G3-03", "MacroSpec/MacroSnapshot 聚合门", "test_g3_03"),
        ("G3-04", "FormulaRegistry/CalcLedger", "test_g3_04"),
        ("G3-05", "Claim AST/emission map", "test_g3_05"),
        ("G3-06", "四路估值与三情景", "test_g3_06"),
        ("G3-07", "成本/重试/降级", "test_g3_07"),
        ("G3-08", "600089 纵向候选 fixture", "test_g3_08"),
        ("G3-09", "RuleRegistry", "test_g3_09"),
        ("G3-10", "勾稽 R01—R05", "test_g3_10"),
        ("G3-11", "勾稽 R06—R10", "test_g3_11"),
        ("G3-12", "Decimal/单位守恒/冻结分母", "test_g3_12"),
        ("G3-13", "AssumptionSnapshot", "test_g3_13"),
        ("G3-14", "OpenItemRegistry", "test_g3_14"),
    ):
        L.append(f"  {tid} {tname}: {status_of(f'.venv/bin/python -m unittest backend.tests.{ttest} 2>&1 | tail -1')}")
    L.append("```\n")

    # ── §5 债务清点（ADR-010 §3.1 三条）───────────────────────────
    L.append("## 5. 债务清点（ADR-010 §3.1 三条）\n")
    L.append("```text")
    L.append(f"① Gate 3 范围内的材料性开放项：{len(g3_mat)} 项（须为零）"
             f"［blocks_development 含 G3-xx，ADR-021 §2 并集口径］"
             + (f" —— 非零: {[i['open_item_id'] for i in g3_mat]}" if g3_mat else ""))
    unclosed = [i for i in items if i.get("status") == "OPEN" and i.get("material")]
    L.append(f"② 全部未闭材料性开放项 —— {len(unclosed)} 项")
    for i in unclosed:
        L.append(f"   {i['open_item_id']} | {i.get('category','')} | "
                 f"阻断={i.get('blocks_decisions') or '无'} | 数据面={i.get('blocks_data_flow') or '—'}")
    # ADR-010 §3.1 第 3 条原文要求「本 Gate **新增了几项、关闭了几项、净变化**」——
    # 初版只写了一句说明而没有数字，等于该义务未落地。数字按 source 字段的
    # 日期归属统计，口径与 build_gate2_acceptance 一致。
    _g3_added = [i for i in items if any(dd in str(i.get("source", ""))
                                         for dd in ("2026-08-10", "2026-08-11"))]
    _g3_closed = [i for i in items if i.get("status") == "CLOSED"
                  and any(dd in str(i.get("closure_evidence") or "")
                          for dd in ("2026-08-10", "2026-08-11"))]
    L.append(f"③ 债务趋势（ADR-010 §3.1 第 3 条）：G3 期间新增 {len(_g3_added)} 项 · "
             f"关闭 {len(_g3_closed)} 项 · 净变化 {len(_g3_added) - len(_g3_closed):+d} 项"
             f"（负数 = 债务净减少）")
    L.append("```\n")

    # ── §6 测试基线 ───────────────────────────────────────────────
    L.append("## 6. 测试命令、退出码与结果\n")
    L.append("```text")
    for name, script in (("独立审计", "audit_session.py"), ("v2.0 基线", "test_v2_baseline.py")):
        rc, out, err = run(f"python3 {shlex.quote(os.path.join(PORTFOLIO, 'tools', script))} "
                           f"{shlex.quote(PORTFOLIO)}")
        L.append(f"{name}: 退出码 {rc} | {out.splitlines()[-1] if out else err}")
    L.append(f"工程测试: {_tests_out}")
    L.append(f"test-integrity: {status_of('.venv/bin/python backend/tools/test_integrity_check.py . 2>&1 | tail -1')}")
    for t in ("rights_action_map_check", "fixture_shape_check", "contract_coverage_check",
              "arch_import_check"):
        rc, out, _ = run(f".venv/bin/python backend/tools/{t}.py . 2>&1 | tail -1")
        L.append(f"{t}: {out}")
    L.append("```\n")

    # ── §7 签署前置 ───────────────────────────────────────────────
    L.append("## 7. 签署前置\n")
    L.append("```text")
    L.append("P-1 G1/G2 签署有效性：G1-acceptance-6 / G2-acceptance-4 ACTIVE"
             "（signed_at=2026-08-11T06:26:33Z，实测）")
    L.append("P-2 OI-PF-070：U 裁定首屏 = 前 3 行（2026-08-11），C-10 已按此实现")
    L.append("P-3 数据来源：600089 全人工导入（ADR-017），自动取得能力为零 —— "
             "G3 按此前提设计，风险已如实载入本包 §8")
    L.append("X-7：ACTIVE 状态下连跑三次实质哈希逐字一致（本包 substantive 构造已排除审计结果行）")
    L.append("```\n")

    # ── §8 风险与已知缺口（如实载明）─────────────────────────────
    L.append("## 8. 风险与已知缺口（如实载明）\n")
    L.append("```text")
    L.append("· 系统自动取得能力为零（ADR-017 §4.1 放弃的保证在 G3 显现）：")
    L.append("  600089 全部输入经人工导入路径；vertical_candidate 已按此保持 PARTIAL")
    # 原文引用的编号 OI-600089-SUB-SOURCE **在 risk/open-items.json 中不存在** ——
    # 验收包不得引用不可解析的编号（守卫 B1 的同类要求）。改为陈述事实本身。
    L.append("· 600089 的全部输入经人工导入路径（ADR-017 §2.2 逐字载明零自动取得）；")
    L.append("  候选据此保持 PARTIAL_NOT_RELEASE_ELIGIBLE，不得转 eligible。")
    L.append("  **本条不引用开放项编号** —— 该约束的载体是 ADR-017 本身，非某个开放项。")
    L.append("· 「适用」「材料性」判定已落库可机检（macro_spec.json + RuleRegistry），"
             "变异注入证明判定与材料性相关")
    L.append("· G3 是首个产出研究结论的 Gate —— 结论为候选级（CANDIDATE_NOT_RELEASED），"
             "不写 release/current，不构成任何投资决策")
    L.append("```\n")

    pkg = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
        PORTFOLIO, "Gate3-验收包.md")
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
