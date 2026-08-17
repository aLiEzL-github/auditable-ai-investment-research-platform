#!/usr/bin/env python3
"""build_gate5_acceptance.py —— Gate 5 验收包生成器（实时采集，不可手写）。

依据：
  · G5-执行计划.md §1A（基线 B §8 任务表）· §3（E-1…E-9）· §4（规则 ①—㉖）
  · 基线 §9 对 Gate 5：必须证明「UI 无法绕过后端 release_eligible；
    阻断态不可隐藏」；一票否决「前端可改写阻断态」
  · ADR-021 §2：范围判定口径（并集，blocks_development 必含 GN-\\d\\d）
  · ADR-010 §3.1：三条债务清点义务（S3 逐条机检）

前四个 Gate 的教训，本文件逐条落地：
  ㉑ 结论由范围内计数决定（OI-PF-152：Gate 1 生成器曾把该值算出来只用于
     打印，结论行写死 READY_FOR_APPROVAL）；**工程测试失败亦须使结论转红**
  ㉒ 台账路径尊重 PORTFOLIO_ROOT（OI-PF-153：写死路径使「临时副本验证」失效）
  ⑨  两个数不得合并成一个 —— 前端与后端证据**分列报数**
  ⑮ **前端校验不计入 E-1/E-3 的证据**（G5-执行计划 §3.1）——
     本包对二者只采信 backend/tests 的用例，前端测试另列且标明其边界
  EXCLUDE 与台账 audit_session.py 的 _SUB_EXCLUDE 逐字一致（T1 就地重算）
"""
import hashlib
import json
import os
import re
import subprocess
import sys

# ── 写盘前置：目标被 ACTIVE 签署即拒绝（A §10.3）────────────────────
# 验收包内嵌实时读数（CI run / 审计合计 / 开放项计数），**一跑就改字节**。
# 保护此前只在 acceptance_fixpoint 上，直接运行本脚本无任何拦截 ——
# 2026-08-17 实测后果：六份已签包全部漂移。判据只此一份，见该模块。
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from signed_object_guard import refuse_if_signed   # noqa: E402

# 仓库根（backend/tools/x.py → 上溯两级）。**不是 backend/** ——
# 初版写成上溯一级并把它当 cwd，于是 .venv/bin/python 在该目录不存在，
# 命令全部失败；而失败被下一条缺陷掩盖，见 run()。
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

# 与 audit_session.py 的 _SUB_EXCLUDE **逐字一致**（14 条）。
# 注：G3 生成器只有 9 条（缺 g1-08-2026/_mut-/sparseimage/备份目录 = /  g1-08-），
# 目前不影响其哈希（那些模式不会出现在 G3 包里），但属潜在分叉点。
EXCLUDE = ("生成时刻", "实测时刻", "main 最新 CI run", "run = ", "ruleset: ",
           "g1-08-2026", "_mut-", "sparseimage", "备份目录 = ", "  g1-08-",
           "substantive_sha256", "合计", "独立审计:", "v2.0 基线:")


def run(cmd):
    """**pipefail 强制打开**。初版没开，于是 `python … | tail -1` 的退出码
    取自 tail —— 恒为 0。python 根本没跑起来（.venv 路径错）时，本函数
    报告 rc=0，与「跑起来且通过」不可分辨。

    这是 X-7 那次的同款：**「没测」被呈现成了别的东西**。
    """
    r = subprocess.run(["/bin/sh", "-o", "pipefail", "-c", cmd],
                       capture_output=True, text=True, cwd=ROOT)
    return r.returncode, r.stdout.strip(), r.stderr.strip()


def status_of(cmd):
    """只取状态词（OK/FAILED），不含耗时行（X-7 幂等）。

    取不到状态词时**不得只报 rc** —— 「没跑起来」必须自己说出来，
    否则读者会把它读成一种测试结果。
    """
    rc, out, err = run(cmd)
    m = re.findall(r"(OK|FAILED[^\n]*)", out or err)
    if m:
        return m[-1]
    _tail = ((err or out).splitlines() or ["（无输出）"])[-1][:70]
    return f"**未跑起来（rc={rc}）**：{_tail}"


def guard_of(cmd):
    """守卫输出末行；退出码非 0 时显式标注「未通过或未跑起来」——
    二者在读者眼里必须可分辨（Gate 4 的 migration_check 教训：
    守卫报 ModuleNotFoundError 却与其他守卫并列显示）。"""
    rc, out, err = run(cmd)
    tail = (out or err).splitlines()[-1] if (out or err) else "（无输出）"
    return tail if rc == 0 else f"**未通过或未跑起来（rc={rc}）**：{tail[:80]}"


def in_gate5(i):
    """ADR-021 §2 的**逐字段**口径 —— 按该 ADR 原文，四个字段模式各不相同：

        blocks_development     含 GN-\\d\\d
        blocks_data_flow       含 GN / Gate N      ← **裸 GN**
        blocks_decisions       含 Gate N
        deprecated_blocks_gate 含 GN / Gate N      ← **裸 GN**

    既有五个生成器（in_g0 / in_gate1 / in_gate2 / in_gate3 / in_gate4）
    一律把四字段拼成一个串、统一用 `GN-\\d|Gate N` 匹配 —— **要求短横**。
    于是 blocks_data_flow 与 deprecated_blocks_gate 里的裸 GN 一个也匹配不上，
    而裸 GN 恰是这两个字段的主流写法（deprecated_blocks_gate 里
    G1×8 · G2×6 · G0×4 · G7×3；blocks_data_flow 里 G2×13）。
    见 OI-PF-157。
    """
    if re.search(r"G5-\d\d", str(i.get("blocks_development") or "")):
        return True
    for k in ("blocks_data_flow", "deprecated_blocks_gate"):
        if re.search(r"\bG5\b|G5-\d|Gate 5", str(i.get(k) or "")):
            return True
    return bool(re.search(r"Gate 5", str(i.get("blocks_decisions") or "")))


def is_standing_risk(i):
    """blocks_data_flow == "ALL" 的持续性风险项。

    ADR-022（U 裁定 2026-08-12）：**"ALL" 不算命中任何 Gate**（取字面）。
    理由：这类项对每个 Gate 一视同仁，且不可由任一 Gate 解除
    （OI-PF-022 需 VD-20 层面处置，OI-PF-026 需对统计局条款的人工裁定），
    计入即等于宣布该 Gate 不可通过 —— 那不是更严的执行，是永久停摆。

    **该裁定是一次放宽，对价是 ADR-022 §3.1 的单列义务**：
    每个 Gate 验收包须单列一节逐项点名全部 "ALL" 项，不得与
    「② 全部未闭材料性开放项」合并（合并即等于没单列）。
    只落地前半 = 纯放宽，故本函数的产物在 §1.7 被强制成节。

    豁免**仅限 "ALL" 一种取值** —— 其余取值一律走 in_gate5 原路径。
    """
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
    g5_mat = [i for i in mat if in_gate5(i)
              and i.get("category") != "签署前置条件"]
    # ADR-022 §3.1：持续性风险须**单列**，不得与 ② 合并。
    _standing = [i for i in mat if is_standing_risk(i)]

    # ── 证据采集：后端与前端**分列**（⑨/⑮）────────────────────────
    _be_bypass = status_of(
        ".venv/bin/python -m unittest discover -s backend/tests -k g5_bypass 2>&1 | tail -1")
    _be_all = status_of(
        ".venv/bin/python -m unittest discover -s backend/tests 2>&1 | tail -1")
    # 前端用 vitest，输出里没有 unittest 的 OK/FAILED 词 —— 单独解析。
    # 若沿用 status_of，64 个全过会被报成「未跑起来」。
    _rc_fe, _o_fe, _e_fe = run("cd frontend && npm test --silent 2>&1")
    _all_fe = _o_fe or _e_fe
    # vitest 全过时是 `Tests  64 passed (64)`，有失败时是
    # `Tests  2 failed | 62 passed (64)` —— **failed 在前**。
    # 初版正则按「passed 在前」写，于是真失败匹配不上、落进 else 分支，
    # 被标成「未跑起来」。**那正好抹掉了「跑了但失败」与「根本没跑」的区别** ——
    # 这个区别是本文件专门去保住的，却在这里自己丢了一次。
    # 用**分段解析**而非一条整正则：vitest 的这一行有三种形态 ——
    #   Tests  64 passed (64)
    #   Tests  2 failed | 62 passed (64)      ← failed 在前
    #   Tests  64 failed (64)                 ← **没有 passed 段**
    # 一条整正则每照顾一种就漏掉另一种；第三种漏掉时「全部失败」会被
    # 标成「未跑起来」—— 恰好抹掉本文件专门去保住的那个区别。
    _mf = re.search(r"Tests\s+([^\n(]*)\((\d+)\)", _all_fe)
    if _mf:
        _seg = dict((k, int(v)) for v, k in
                    re.findall(r"(\d+)\s+(passed|failed|skipped|todo)", _mf.group(1)))
        _n_fe = int(_mf.group(2))
        _fail_fe = _seg.get("failed", 0)
        _fe = (f"{'OK' if _fail_fe == 0 and _rc_fe == 0 else 'FAILED'}"
               f"（{_n_fe} 个用例，失败 {_fail_fe}）")
    else:
        _tail_fe = (_all_fe.splitlines() or ["（无输出）"])[-1][:70]
        _n_fe, _fail_fe = 0, -1
        _fe = f"**未跑起来（rc={_rc_fe}）**：{_tail_fe}"
    # 用例数：取 unittest 自报的 "Ran N tests"，不用 grep -c ——
    # grep 无匹配时返回 1，在 pipefail 下会被读成「命令失败」，
    # 而「0 个用例」与「命令失败」是两回事（⑨）。
    _n_be_bypass = 0
    _rc, _o, _e = run(".venv/bin/python -m unittest discover -s backend/tests "
                      "-k g5_bypass 2>&1")
    _m = re.search(r"Ran (\d+) tests?", _o or _e)
    _n_be_bypass = int(_m.group(1)) if _m else 0

    _tests_ok = _be_all.startswith("OK") and _be_bypass.startswith("OK")

    # ── §1.7 持续性风险节（ADR-022 §3.1）──────────────────────────
    # **先构造，后自查**：_standing_listed 由「每个 ID 确实出现在产出文本中」
    # 判定，不是一个自己给自己发的证明。自声明式的落地断言，本项目已栽过
    # 一次（OI-PF-150：签署对象自证其未被篡改）。
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
        # 要旨取自 description 原文，**不得改写为更轻的表述**（ADR-022 §3.1）
        _S17.append(f"     要旨: {str(i.get('description') or '')[:120]}")
    _S17.append("")
    _S17.append("上列各项**仍 OPEN 且仍 material** —— 本节不主张其风险已消解，")
    _S17.append("只记载它们不构成本 Gate 的阻断。其处置路径在 VD-20 / VD-12 层面，")
    _S17.append("不在任何 Gate 内（ADR-022 §4.1 已载明这是被放弃的效力）。")
    _S17.append("```\n")
    # **自查点见 §「装配后自查」** —— 不在这里。
    # 初版在此处用 `all(id in "\n".join(_S17))` 判定，**变异注入抓到它是空的**：
    # 删掉 `L.extend(_S17)` 后片段照样构造、自查照样通过，而该节根本没进
    # 最终文件，结论仍报 READY_FOR_APPROVAL。
    # **这是「结构在、功能不在」的又一例，而且出在专门防它的那道检查上** ——
    # _S17 是原料，L 才是产出；核对原料等于没核对。

    # ㉑：结论由范围内计数**与**测试状态共同决定，不得硬编码
    _blockers = []
    if g5_mat:
        # **逐项列出 ID**，不只报个数 —— 只报数时读者无从判断新增的是哪一项，
        # 变异注入也无从断言「我注入的那一项确实被匹到了」。
        _blockers.append(f"Gate 5 范围内材料性开放项 {len(g5_mat)} 项 ≠ 0"
                         f"（ADR-010 §4 不得 PASS）："
                         f"{[i['open_item_id'] for i in g5_mat]}")
    if not _tests_ok:
        _blockers.append(f"工程测试未全过（后端全量={_be_all}；"
                         f"G5 绕过负测={_be_bypass}）")
    if _n_be_bypass == 0:
        _blockers.append("**后端 G5 绕过负测为 0 个** —— 基线 §9 的一票否决"
                         "「前端可改写阻断态」在后端无证据（⑮：前端校验不计入）")
    # 前端证据承载 E-4/E-5/E-6「阻断态不可隐藏」—— 同属基线 §9 必证项。
    # 它跑不起来时结论亦须转红：**没跑 ≠ 通过**。
    if not _fe.startswith("OK"):
        _blockers.append(f"前端测试未全过或未跑起来（{_fe}）—— "
                         f"E-4/E-5/E-6「阻断态不可隐藏」失去证据")
    # 注意：**§0（含结论行）在全部正文装配完毕后才构造并前置** ——
    # 见下方「装配后自查」。结论依赖一个只有在正文成型后才能回答的问题：
    # 「§1.7 真的进到产出里了吗」。

    # ── §1 基线 §9 证明义务 ───────────────────────────────────────
    L.append("## 1. 基线 §9 证明义务（逐条实测）\n")
    L.append("```text")
    L.append("必须证明：UI 无法绕过后端 release_eligible；阻断态不可隐藏")
    L.append("一票否决：前端可改写阻断态")
    L.append("```\n")

    L.append("### 1.1 后端是唯一控制层（E-1/E-2/E-3）—— **只采信后端证据**\n")
    L.append("```text")
    L.append(f"  E-1/E-3 后端绕过负测: {_be_bypass}（用例数 {_n_be_bypass}）")
    L.append("    · 全部直接构造 HTTP 请求，不经任何前端代码（⑮：前端校验不计入证据）")
    L.append("    · 查询串伪造 release_eligible=true → 400 E-G5-002")
    L.append("    · 请求体伪造判定 → 400；POST 写入判定 → 405/400")
    L.append("    · **伪造后结论逐字不变**（规则 ⑩：用原来那个成功绕过的载荷复验）")
    L.append("    · 判定字段清单逐个生效（不得只挡最显眼的那个）")
    L.append("  E-2 release_eligible 单一计算点: backend/app/publish_engine.py")
    L.append("    is_release_eligible + main.py 的 _compute_eligibility 不接受任何入参")
    L.append("  **显式拒绝而非静默忽略** —— 静默忽略会让调用方以为得手，")
    L.append("    也使该尝试在日志中无痕")
    L.append("```\n")

    L.append("### 1.2 阻断态不可隐藏（E-4/E-5/E-6）—— 前端证据，边界如下\n")
    L.append("```text")
    L.append(f"  前端测试: {_fe}")
    L.append("  覆盖 E-4（BLOCKED 恒定渲染、非折叠非仅颜色）· E-5（原因逐条可见）")
    L.append("       · E-6（「已检查无阻断」与「尚未检查」文本互异，data-status 可分辨）")
    L.append("  **边界**：前端测试证明的是「前端按后端返回如实呈现」，")
    L.append("    **不构成** E-1/E-3 的证据 —— 后者见 §1.1（G5-执行计划 §3.1、规则 ⑮）")
    L.append("  机器只能查 DOM 结构与可见性属性，查不了「使用者是否真的注意到」")
    L.append("```\n")

    L.append("### 1.3 证据优先（E-7/E-8/E-9）\n")
    L.append("```text")
    L.append("  E-8 断链须显式报错: 前端 ReleaseStatusBanner 测试含该用例")
    L.append("  E-9 SINGLE_REVIEWER_ATTESTED 首屏可见（U 裁定：前 3 行）")
    L.append("      —— 与 OI-PF-070 对文档的口径一致")
    L.append("```\n")

    # ── §2 G5-01…G5-07 任务验收 ──────────────────────────────────
    # ADR-022 §3.1 的单列节 —— 上面已构造并自查，这里拼入包体。
    L.extend(_S17)

    L.append("## 2. G5-01…G5-07 任务验收（基线 B §8 任务表）\n")
    L.append("```text")
    _tr = os.path.join(PORTFOLIO, "task-records")
    for n in range(1, 8):
        _f = os.path.join(_tr, f"G5-0{n}.json")
        if os.path.exists(_f):
            _d = json.load(open(_f, encoding="utf-8"))
            _ms = (_d.get("merge_status") or {}).get("state", "**无**")
            _ev = json.dumps(_d.get("evidence"), ensure_ascii=False)
            _pr = re.findall(r"PR #(\d+)", _ev)
            L.append(f"  G5-0{n} {_d.get('task_status','?'):6s} merge={_ms:10s} "
                     f"PR={_pr[:1]}")
        else:
            L.append(f"  G5-0{n} **无 task-record**")
    L.append("```\n")

    # ── §3 债务清点（ADR-010 §3.1 三条）─────────────────────────
    L.append("## 3. 债务清点（ADR-010 §3.1，三条）\n")
    L.append("```text")
    L.append(f"① Gate 5 范围内的材料性开放项：{len(g5_mat)} 项（须为零）"
             f"［范围判定：blocks_development 含 G5-xx，ADR-021 §2 并集口径］"
             + (f" —— 非零: {[i['open_item_id'] for i in g5_mat]}" if g5_mat else ""))
    L.append(f"   \"ALL\" 项不计入本条（ADR-022 §2 取字面），"
             f"另见 §1.7 逐项点名的 {len(_standing)} 项持续性风险 ——")
    L.append("   **它们不在本条里，不等于不存在**；ADR-022 §4.1 载明了")
    L.append("   这次放弃的正是「\"ALL\" 项会阻断某个 Gate」这一效力。")
    # ADR-021 §2.4 的自指循环：一项「阻断本 Gate 签署」的材料性 OPEN 项，
    # 自己也落进「本 Gate 范围内」，于是它既是阻断者又是被计数者。
    # §2.4 的处置（category=签署前置条件）在此不适用 —— 守卫 Q2 要求该类别
    # material=False，而这类项确为材料性（OI-PF-151 同款）。据实报出即可，
    # 不为了让数字归零而动台账。
    _self_ref = [i for i in g5_mat
                 if "Gate 5" in str(i.get("blocks_decisions") or "")]
    if _self_ref:
        L.append(f"   **其中 {[i['open_item_id'] for i in _self_ref]} 是自指项** ——")
        L.append("   它本身就是「阻断 Gate 5 签署」的登记项，故既是阻断者又被计数")
        L.append("   （ADR-021 §2.4 记载的循环）。它不代表 Gate 5 有工程欠项，")
        L.append("   U 裁定后即闭合，届时两条阻断一并消失。")
        L.append("   本项待 U 裁定后即闭合。")
    for i in [x for x in items if in_gate5(x)]:
        L.append(f"   {i['open_item_id']}: {i.get('status')} "
                 f"材料性={i.get('material')} 阻断={_blk(i)}")
    L.append(f"② 全部未闭材料性开放项 —— {len(mat)} 项")
    for i in mat:
        L.append(f"   {i['open_item_id']} | {i.get('category','')} | 阻断={_blk(i)}")
    # 窗口须**精确到日**。初版写 "2026-08-1"，把 08-10 也匹进来，关闭数
    # 由 13 虚增到 36 —— 一个前缀少写一位，债务趋势就好看了一倍有余。
    _WINDOW = ("2026-08-11", "2026-08-12")

    def _in_window(*fields):
        _s = " ".join(str(f or "") for f in fields)
        return any(d in _s for d in _WINDOW)

    _added = [i for i in items if _in_window(i.get("source"))]
    _closed = [i for i in items if i["status"] == "CLOSED"
               and _in_window(i.get("closure_evidence"), i.get("source"))]
    _net = len(_added) - len(_closed)
    L.append(f"③ 债务趋势（ADR-010 §3.1 第 3 条）：G5 期间新增 {len(_added)} 项 · "
             f"关闭 {len(_closed)} 项 · 净变化 {_net:+d} 项（负数 = 债务净减少）")
    L.append(f"   窗口 = {_WINDOW[0]}…{_WINDOW[1]}（G5 开工至本包生成）")
    L.append("   **度量基础须知**：登记册没有逐项的登记/关闭时间戳字段")
    L.append("   （detected_at 仅 24 项、closed_at 仅 4 项有值），故本行按")
    L.append("   source / closure_evidence **自由文本中的日期串**匹配得出，")
    L.append("   为近似值。它足以看趋势方向，**不足以作精确计数引用**。")
    L.append("```\n")

    # ── §4 测试命令、退出码与结果 ────────────────────────────────
    L.append("## 4. 测试命令、退出码与结果\n")
    L.append("```text")
    L.append(f"后端全量: {_be_all}")
    L.append(f"G5 绕过负测: {_be_bypass}（{_n_be_bypass} 个用例）")
    L.append(f"前端测试: {_fe}")
    L.append(f"arch_import_check: {guard_of('.venv/bin/python backend/tools/arch_import_check.py .')}")
    L.append(f"research_data_interdict: {guard_of('.venv/bin/python backend/tools/research_data_interdict.py .')}")
    L.append(f"test_integrity_check: {guard_of('.venv/bin/python backend/tools/test_integrity_check.py .')}")
    L.append(f"contract_coverage_check: {guard_of('.venv/bin/python backend/tools/contract_coverage_check.py .')}")
    L.append("```\n")

    # ── §5 风险与已知缺口 ────────────────────────────────────────
    L.append("## 5. 风险与已知缺口（如实载明）\n")
    L.append("```text")
    L.append("· **本 Gate 的一票否决此前在后端毫无强制**：G5-01…G5-07 七次 CI 全绿，")
    L.append("  而前端声明的 17 个 /api/* 端点后端一个都没有 —— 「UI 无法绕过后端」")
    L.append("  在端点不存在时是空话。已由 OI-PF-156 修复（PR #62）")
    L.append("· 后端目前只实现 /api/release/eligibility 一个端点；其余 16 个")
    L.append("  前端声明的端点仍无后端对应物 —— **本包不声称它们已被验证**")
    L.append("· E-4「阻断态不可隐藏」只能查 DOM 结构与可见性属性，")
    L.append("  查不了使用者是否真的注意到。本包不声称后者")
    L.append("· 前端测试不计入 E-1/E-3 证据（⑮）——二者的证据只来自 backend/tests")
    L.append("")
    L.append("· **OI-PF-157：既有五个生成器都没实现 ADR-021 §2 写的模式。**")
    L.append("  ADR-021 §2 原文对 blocks_data_flow 与 deprecated_blocks_gate 写的是")
    L.append("  「含 GN」（裸 GN），而 in_g0 / in_gate1 / in_gate2 / in_gate3 /")
    L.append("  in_gate4 一律用 `GN-\\d|Gate N` —— **要求短横**，裸 GN 一个也匹配不上。")
    L.append("  裸 GN 恰是这两个字段的主流写法（deprecated_blocks_gate 里")
    L.append("  G1×8 · G2×6 · G0×4 · G7×3）。本 Gate 的 in_gate5 按原文逐字段实现。")
    L.append("  **对既有签署的实际影响**：按原文重算，Gate 1 范围内多出 OI-PF-022、")
    L.append("  Gate 2 多出 OI-PF-026 —— 二者各有 reclass_note 载明不构成额外阻断，")
    L.append("  故实质结论未变；**但当时把它们排除掉靠的是正则要求短横，")
    L.append("  与 reclass_note 的理由毫无关系**。结论对，机制不对。")
    L.append("")
    L.append("· **OI-PF-158 已由 ADR-022 裁定（U，2026-08-12）：取字面。**")
    L.append("  \"ALL\" 不算命中任何 Gate —— 这类项对每个 Gate 一视同仁且不可由")
    L.append("  任一 Gate 解除，计入即等于宣布该 Gate 不可通过。")
    L.append("  **该裁定是一次放宽，须照直读**：此后 OI-PF-022（研究产出误公开）")
    L.append("  与 OI-PF-026（统计局转载条款）不会因任何 Gate 的验收而被迫处置，")
    L.append("  其处置只能由 VD-20 / VD-12 层面的决策推动（ADR-022 §4.1）。")
    L.append("  对价是 §1.7 的单列义务 —— 缺该节则本包结论转 NOT_READY。")
    L.append("  曾考虑的第三条路（把 reclass_note 纳入公式作排除依据）**实测否决**：")
    L.append("  未闭材料性 23 项中 16 项带该备注，含 OI-PF-124（其备注明写")
    L.append("  「阻断**未**解除且签署曾越过它」）—— 23 项排掉 16 项，")
    L.append("  债务清点第 ① 条基本失效。见 ADR-022 §1.4。")
    L.append("```\n")

    # ── 装配后自查（ADR-022 §3.1 的强制点）────────────────────────
    # 判据经过两次收窄，两次都被变异注入抓出来：
    #
    #   一版：核对 _S17（已构造的片段）
    #         → 「构造了但没拼进去」照样通过（V-3a 抓到）。
    #           _S17 是原料，L 才是产出 —— 核对原料等于没核对。
    #   二版：核对 "\n".join(L)（整个包体）
    #         → §5 风险节里也写了 OI-PF-022 / OI-PF-026 的名字，
    #           于是 §1.7 整节删掉，ID 仍出现在文档里，检查照过（V-3a 再抓到）。
    #           **判据成了「ID 出现在文档某处」，而要求是「出现在 §1.7 里」**
    #           —— 又一次匹配了代理而非目标。
    #   三版（本版）：**只在 §1.7 这一节的范围内核对**。
    #         节不存在 → 红；节在但漏列 → 红；节在但空 → 红。
    #         别处提到这些 ID 不再能顶替单列义务。
    _body_txt = "\n".join(L)
    _h17 = "### 1.7 持续性风险"
    _i17 = _body_txt.find(_h17)
    if _i17 < 0:
        _sec17 = ""                      # 节不存在
    else:
        _nxt = _body_txt.find("\n## ", _i17)
        _sec17 = _body_txt[_i17:_nxt if _nxt > 0 else len(_body_txt)]
    _standing_listed = all(i["open_item_id"] in _sec17 for i in _standing)
    if _standing and not _standing_listed:
        _missing = [i["open_item_id"] for i in _standing
                    if i["open_item_id"] not in _sec17]
        # 「节根本不存在」与「节在但漏列」是两回事，分开报（规则 ⑨）。
        _why = ("**§1.7 该节不存在**" if _i17 < 0
                else f"§1.7 在，但漏列 {_missing}")
        _blockers.append(
            f"**ADR-022 §3.1 的单列义务未落地**：{_why}。持续性风险共 "
            f"{len(_standing)} 项（{[i['open_item_id'] for i in _standing]}）"
            f"须在该节逐项点名 —— **别处提到不算**。取字面的对价即此节，"
            f"缺此节则本 ADR 沦为纯放宽（§4.1）")
    verdict = "READY_FOR_APPROVAL" if not _blockers else "NOT_READY"

    # ── §0 头部（结论行）—— 装配完毕后才构造并前置 ────────────────
    _H = []
    _H.append("# Gate 5 验收包\n")
    _H.append("```text")
    _H.append(f"生成时刻   = {NOW}")
    _H.append("生成方式   = backend/tools/build_gate5_acceptance.py（全部数据实时采集）")
    _H.append("依据       = G5-执行计划.md §1A/§3/§4 + 基线 §9 + ADR-021 §2"
              " + ADR-022 §2/§3 + ADR-010 §3.1")
    _H.append("范围口径   = ADR-021 §2 并集：material ∧ OPEN ∧ category != 签署前置条件；"
              "blocks_development 含 G5-xx 必含 | blocks_data_flow/blocks_decisions/"
              "deprecated_blocks_gate 含 G5/Gate 5；"
              "blocks_data_flow=\"ALL\" 不算命中（ADR-022 §2），另见 §1.7")
    _H.append(f"结论       = **{verdict}**"
              + (f" —— {'；'.join(_blockers)}" if _blockers
                 else "（范围内材料性开放项为零；后端绕过负测非空且全过；"
                      "工程测试全过；ADR-022 §3.1 单列节已落地）"))
    _H.append("independent_reviewer_present = false（VD-02 = 1 名自然人）")
    _H.append("```\n")
    _H.append("> **本包不是 Gate 5 PASS。** 供批准人审阅的冻结材料；"
              "签署按 ADR-016 S1—S5。\n")
    L = _H + L

    pkg = os.path.join(PORTFOLIO, "Gate5-验收包.md")
    refuse_if_signed(PORTFOLIO, pkg)
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
    # 阻断项**逐条**输出。只报结论词不够：本包存在一个长期阻断项
    # （OI-PF-158 的读法未裁定），于是结论恒为 NOT_READY ——
    # 此时「只看结论词」的变异注入什么也证明不了，每一步都红。
    # 变异须断言各自特有的阻断理由，故这里逐条给出可断言的标识。
    print(f"blockers = {len(_blockers)}", file=sys.stderr)
    for _b in _blockers:
        print(f"  · {_b[:120]}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
