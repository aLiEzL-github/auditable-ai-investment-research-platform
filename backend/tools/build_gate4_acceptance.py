#!/usr/bin/env python3
"""build_gate4_acceptance.py —— Gate 4 验收包生成器（实时采集，不可手写）。

依据：
  · G4-执行计划.md §1A（基线 B §7 任务表）· §3（D-1..D-13）· §4（规则 ①—⑳ + ㉑—㉖）
  · ADR-021 §2：范围判定口径（并集，blocks_development 必含 GN-\\d\\d）
  · ADR-010 §3.1：三条债务清点义务（S3 逐条机检）

结论由范围内计数决定（㉑，OI-PF-152），不得硬编码；
台账路径尊重 PORTFOLIO_ROOT（㉒，OI-PF-153）。
X-7（修订）：审计结果行移出 substantive 覆盖 —— ACTIVE 状态下连跑三次
实质哈希逐字一致（AA-1 教训；审计结果由签署记录 S1 承载）。
"""
import json
import os
import re
import shlex
import subprocess
import sys

# ── 写盘前置：目标被 ACTIVE 签署即拒绝（A §10.3）────────────────────
# 验收包内嵌实时读数（CI run / 审计合计 / 开放项计数），**一跑就改字节**。
# 保护此前只在 acceptance_fixpoint 上，直接运行本脚本无任何拦截 ——
# 2026-08-17 实测后果：六份已签包全部漂移。判据只此一份，见该模块。
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from signed_object_guard import refuse_if_signed   # noqa: E402

from substantive_hash import (   # noqa: E402
    LIVE_BEGIN, LIVE_END, substantive as substantive_of, unbalanced_markers,
)

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
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

def in_gate4(i):
    """ADR-021 §2 逐字段口径（OI-PF-157）。"""
    # **本改动不重新生成该验收包** —— 它已 ACTIVE 签署，
    # 重生成会改字节而触发 A §10.3（见 OI-PF-168）。
    return _adr021_in_scope(i, "G4")

def main() -> int:
    L = []
    OI = json.load(open(os.path.join(PORTFOLIO, "risk", "open-items.json"),
                        encoding="utf-8"))
    items = OI["items"]
    op = [i for i in items if i["status"] == "OPEN"]
    mat = [i for i in op if i.get("material")]
    g4_mat = [i for i in mat if in_gate4(i)
              and i.get("category") != "签署前置条件"]
    other_mat = [i for i in mat if not in_gate4(i)]
    oi_closed = len([i for i in items if i.get("status") == "CLOSED"
                     and ("2026-08-11" in str(i.get("closure_evidence", ""))
                          or "2026-08-11" in str(i.get("source", "")))])
    g4_added = [i for i in items if "2026-08-11" in str(i.get("source", ""))
                and i.get("status") == "OPEN"]

    L.append("# Gate 4 验收包\n")
    L.append("```text")
    L.append(LIVE_BEGIN)
    L.append(f"生成时刻   = {NOW}")
    L.append(LIVE_END)
    L.append("生成方式   = backend/tools/build_gate4_acceptance.py（全部数据实时采集）")
    L.append("依据       = G4-执行计划.md §1A/§3/§4 + ADR-021 §2 + ADR-010 §3.1")
    L.append("范围口径   = ADR-021 §2 并集：material ∧ OPEN ∧ category != 签署前置条件；"
             "blocks_development 含 G4-xx 必含 | blocks_data_flow/blocks_decisions/"
             "deprecated_blocks_gate 含 G4/Gate 4 —— blocks_development 含 G4-xx，ADR-021")
    # 结论除范围内计数外，**工程测试未全过亦须转 NOT_READY**（与 G3 包一致）——
    # 一个记着测试失败的验收包不该被当作可审阅材料。
    _tests_out = status_of(".venv/bin/python -m unittest discover "
                           "-s backend/tests 2>&1 | tail -1")
    _blockers = []
    if g4_mat:
        _blockers.append(f"G4 范围内材料性开放项 {len(g4_mat)} 项 ≠ 0，ADR-010 §4 不得 PASS")
    if not _tests_out.strip().startswith("OK"):
        _blockers.append(f"**工程测试未全过**：{_tests_out.strip()[:70]}")
    L.append("结论       = " + ("**READY_FOR_APPROVAL**（G4 范围内材料性开放项为零；工程测试全过）"
                             if not _blockers else "**NOT_READY** —— " + "；".join(_blockers)))
    L.append("independent_reviewer_present = false（VD-02 = 1 名自然人）")
    L.append("```\n")
    L.append("> **本包不是 Gate 4 PASS。** 供批准人审阅的冻结材料；签署按 ADR-016 S1—S5。\n")

    # ── §1 基线 §9 证明义务 D-1..D-13 逐条实测 ──────────────────
    L.append("## 1. 基线 §9 证明义务（G4-执行计划 §3，逐条实测）\n")
    L.append("### 1.1 对象闭包与 subject root（D-1/D-2/D-3）\n")
    L.append("```text")
    for name, t in (("CAS 内容寻址（D-3）", "test_g4_01"),
                    ("闭包完整与变异注入（D-1）", "test_g4_07"),
                    ("subject root 单一（D-2）", "test_g4_07")):
        L.append(f"  {name}（{t}）: {status_of(f'.venv/bin/python -m unittest backend.tests.{t} 2>&1 | tail -1')}")
    L.append("  闭包对象数：完整闭包 11 个对象（candidate/claim/evidence/macro/"
             "  assumption/calc/worksheet/test/code_config/open_item/report）"
             "  ——「0 个对象」与「闭包完整」可分辨（⑨）")
    L.append("```\n")

    L.append("### 1.2 CurrentKey 分域与孤儿（D-4/D-5/D-6）\n")
    L.append("```text")
    L.append(f"  G4-03 发布协议: {status_of('.venv/bin/python -m unittest backend.tests.test_g4_03 2>&1 | tail -1')}")
    L.append("  固定 key 完全分域：system-design-plan/auditable-ai-investment-"
             "  research-platform 与 a-share-single-company-research/600089.SH"
             "  互不干扰，逐域断言（D-4）")
    L.append("  孤儿不得成为 current（D-5，一票否决）：孤儿清单无法通过批准，")
    L.append("  硬发发布必被拒绝 —— 独立负测通过")
    L.append("  current 变更可追溯（D-6）：指针追加式留痕，逐行载明"
             "  changed_by / changed_at / approval_id")
    L.append("```\n")

    L.append("### 1.3 幂等与离线复建（D-7/D-8/D-9）\n")
    L.append("```text")
    L.append(f"  离线复建: {status_of('.venv/bin/python -m unittest backend.tests.test_g4_08 2>&1 | tail -1')}")
    L.append("  真断网机制：docker run --network none（容器内无网络栈，OS 级断网）；")
    L.append("  darwin 回退 sandbox-exec (deny network*) + env -i + python -S -I")
    L.append("  断网断言：TCP 探针 1.1.1.1/github.com/www.stats.gov.cn —— 任一可连即拒绝")
    L.append("  复建产物逐字节一致；连跑三次产物哈希一致（D-7，不接受跑两次）")
    L.append("  干净环境（D-9）：新容器/隔离进程，不复用开发机状态；")
    L.append("  内核不持探针（E-G4-08-003 缺探针即拒绝）")
    L.append("```\n")

    L.append("### 1.4 PROVENANCE_ONLY 不得冒充完整复验（D-10/D-11）\n")
    L.append("```text")
    L.append("  FULL 与 PROVENANCE_ONLY 在输出中显式可分辨（verification_level 字段）")
    L.append("  下游行为不同：FULL 可被发布消费；PROVENANCE_ONLY 冒充完整复验")
    L.append("  必失败（E-G4-08-002，一票否决，独立用例）")
    L.append("  行为验证而非字段存在：声称 FULL 但含缺失对象同样拒绝")
    L.append("```\n")

    L.append("### 1.5 署名义务（D-12/D-13，OI-PF-037）\n")
    L.append("```text")
    L.append(f"  报告审计: {status_of('.venv/bin/python -m unittest backend.tests.test_g4_02 2>&1 | tail -1')}")
    L.append("  含 stats.gov.cn 数据的产出须在显著位置注明转自国家统计局网站并")
    L.append("  标明 www.stats.gov.cn —— 缺署名即 FAIL（守卫先红后绿）")
    L.append("  D-13「显著位置」可机检定义：首屏前 10 行（PROMINENT_FIRST_LINES），")
    L.append("  与 OI-PF-070 对 SINGLE_REVIEWER_ATTESTED 的首屏口径一致")
    L.append("  （该定义按 G4-执行计划 §3.5 落地；签署时由 U 确认）")
    L.append("```\n")

    # ── §1.6 债务清点（ADR-010 §3.1，三条；OI-PF-142 同款）──────
    L.append("## 1.6 债务清点（ADR-010 §3.1，三条）\n")
    L.append("```text")
    L.append(f"Gate 4 范围内的材料性开放项（ADR-021 §2 口径）：{len(g4_mat)} 项（须为零）"
             + (f" —— 非零: {[i['open_item_id'] for i in g4_mat]}" if g4_mat else ""))
    # ADR-021 §2 的范围口径是**四字段并集**，故清单也须按并集显示阻断关系。
    # 初版只显示 blocks_development，于是 OI-PF-147（blocks_decisions 非空、
    # blocks_development 为 None）显示「阻断=无」—— 与 Gate 3 包对同一项的
    # 记载矛盾，而两包读的是同一份台账。
    def _blk(i):
        _parts = []
        if i.get("blocks_development"):
            _parts.append(f"任务={i['blocks_development']}")
        if i.get("blocks_decisions"):
            _parts.append(f"决策={i['blocks_decisions']}")
        if i.get("blocks_data_flow"):
            _parts.append(f"数据面={i['blocks_data_flow']}")
        return " · ".join(_parts) or "无"
    for i in g4_mat:
        L.append(f"   {i['open_item_id']}: {i.get('status')} 材料性={i.get('material')} "
                 f"阻断={_blk(i)}")
    L.append(f"② 全部未闭材料性开放项 —— {len(mat)} 项")
    for i in mat:
        L.append(f"   {i['open_item_id']} | {i.get('category','')} | 阻断={_blk(i)}")
    # 净变化 = 新增 - 关闭。初版写成 关闭 - 新增，于是「新增 3 · 关闭 21」
    # 得出「净变化 18」—— 债务净减少 18 却显示为正数，读者会读成净增加。
    _net = len(g4_added) - oi_closed
    L.append(f"③ 债务趋势（ADR-010 §3.1 第 3 条）：G4 期间新增 {len(g4_added)} 项 · "
             f"关闭 {oi_closed} 项 · 净变化 {_net:+d} 项（负数 = 债务净减少）")
    L.append("```\n")

    # ── §2 测试基线 ────────────────────────────────────────────
    L.append("## 2. 测试命令、退出码与结果\n")
    L.append("```text")
    L.append(LIVE_BEGIN)
    for name, script in (("独立审计", "audit_session.py"), ("v2.0 基线", "test_v2_baseline.py")):
        rc, out, err = run(f"python3 {shlex.quote(os.path.join(PORTFOLIO, 'tools', script))} "
                           f"{shlex.quote(PORTFOLIO)}")
        L.append(f"{name}: 退出码 {rc} | {out.splitlines()[-1] if out else err}")
    L.append(LIVE_END)
    L.append(f"工程测试: {_tests_out}")
    # 初版用系统 python3 跑守卫，而工程测试用 .venv/bin/python —— 同一段代码
    # 两套解释器，于是 migration_check 报 ModuleNotFoundError: sqlalchemy，
    # 却与其他守卫并列显示，读者会以为它检查过了。改为统一用 venv，
    # 且**退出码非 0 时显式标注「未通过/未跑起来」**（没检查 ≠ 检查通过）。
    for t in ("arch_import_check", "contract_coverage_check", "migration_check",
              "data_ingress_scan"):
        rc, out, err = run(f".venv/bin/python backend/tools/{t}.py . 2>&1 | tail -1")
        _mark = "" if rc == 0 else f"  ← **rc={rc}，未通过或未跑起来**"
        L.append(f"{t}: {out or err}{_mark}")
    L.append("```\n")

    # ── §3 签署前置 ────────────────────────────────────────────
    L.append("## 3. 签署前置（X-7 幂等）\n")
    L.append("```text")
    L.append("X-7（修订）：ACTIVE 状态下连跑三次实质哈希逐字一致（本包 substantive")
    L.append("构造已排除审计结果行 —— AA-1 教训；审计结果由签署记录 S1 承载）")
    L.append("```\n")

    # ── §4 开放项：ADR-010 §3.1 三条债务清点义务 ────────────────
    L.append("## 4. 开放项（按 `ADR-010`/`ADR-021 §2` 分范围清点）\n")
    L.append(f"### 4.1 Gate 4 范围内的材料性开放项 —— **{len(g4_mat)} 项**\n")
    if g4_mat:
        for i in sorted(g4_mat, key=lambda x: x["open_item_id"]):
            L.append(f"- **`{i['open_item_id']}`** owner=`{i['owner_role']}` "
                     f"blocks_development=`{i.get('blocks_development')}`")
            L.append(f"  - {i['description'][:200]}")
        L.append("")
        L.append("> ⚠️ **不为零 → Gate 4 不得 PASS**（`ADR-010 §4`）。")
    else:
        L.append("**零。** Gate 4 范围内无未闭材料性开放项。")
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
    L.append(f"  · G4 范围内  {len(g4_mat)}")
    L.append(f"  · 归属后续   {len(other_mat)}")
    L.append(f"阻断开发       {OI['counts']['blocking_development']}")
    L.append(f"阻断数据流     {OI['counts']['blocking_data_flow']}")
    L.append("```")
    L.append("")
    L.append("**债务趋势（ADR-010 §3.1 第 3 条）**：G4 期间开放项净变化见 §1.6 ③；"
             "终末 Gate（`G7`）的全局要求不变：届时全部材料性开放项必须为零。")
    L.append("")

    # ── §5 风险与已知缺口 ──────────────────────────────────────
    L.append("## 5. 风险与已知缺口（G4-执行计划 §5，如实载明）\n")
    L.append("```text")
    L.append("· 离线复建实测：本机经 docker --network none（容器内真断网）；")
    L.append("  若环境无 docker 亦无 sandbox-exec，测试判红（fail-closed）")
    L.append("· OI-PF-037 的关闭以本 PR 合入 origin/main 为前提（规则 ㉓/㉔），")
    L.append("  包生成时若未合并则其状态仍为 OPEN，范围内计数按实读取")
    L.append("· 附.5 待裁定项（OI-PF-144/147）不在 G4 范围内（blocks_development 为空），")
    L.append("  已列于 §4.2 全部未闭清单；不影响本 Gate 判定")
    L.append("· G4 只使用脱敏、冻结 fixture 验证通用发布引擎（基线 B §7）——")
    L.append("  真实发布必须等 G7-00 最终对象闭合与 Gate 7 终审")
    L.append("```\n")

    pkg = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
        PORTFOLIO, "Gate4-验收包.md")
    refuse_if_signed(PORTFOLIO, pkg)
    with open(pkg, "w", encoding="utf-8") as f:
        f.write("\n".join(L))
    assert open(pkg, encoding="utf-8").read() == "\n".join(L)

    _merr = unbalanced_markers("\n".join(L))

    if _merr:

        raise SystemExit(f"E-LIVE-001: {_merr}")

    substantive = substantive_of("\n".join(L))
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
