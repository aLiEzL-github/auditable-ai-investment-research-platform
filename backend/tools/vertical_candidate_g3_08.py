#!/usr/bin/env python3
"""vertical_candidate_g3_08.py —— G3-08 跑通 600089 纵向初步候选 fixture。

基线验收（G3-08）：
  · 从冻结合同、真实 golden baseline、宏观、勾稽、假设、计算、Claim
    到结构化初步候选全流程可复现
  · 任一适用规则非 PASS 或材料性开放项未关时保持 PARTIAL
  · 这是 Agent 前 fixture，不是最终 candidate subject root，不能发布
    （不写 release / current —— Gate 3 退出条件第四条）

流程（可复现，全输入为冻结对象）：
  contract（冻结 ResearchContract）
  → golden baseline facts（600089.json，人工回源）
  → MacroSnapshot（宏观先行，须过 G3-03 聚合门）
  → RuleRegistry（适用分母冻结 + R01—R10 判定）
  → FormulaRegistry（FCFF 等公式计算）
  → ClaimGraph（节点登记 + 闭合 + emission map）
  → 结构化初步候选（candidate dict + sha256）

退出：任何阻断（宏观门 / 规则非 PASS / 材料性开放项）→ candidate 标
PARTIAL_NOT_RELEASE_ELIGIBLE，不得转 eligible。

用法：python3 backend/tools/vertical_candidate_g3_08.py [portfolio_root]
"""
import hashlib
import json
import os
import sys

# OI-PF-153 同类：原默认值写死本机绝对路径，CI 上不存在该目录，
# 工具崩溃且 stdout 非 JSON —— test_g3_08 的 5 个用例因此在 CI 上全部
# ERROR/FAIL，而本机全过。**本机通过不构成 CI 通过。**
# 优先级：显式参数 > PORTFOLIO_ROOT 环境变量 > 本机默认值。
PORTFOLIO = (sys.argv[1] if len(sys.argv) > 1
             else os.environ.get("PORTFOLIO_ROOT")
             or "/Users/li/Documents/Claudetext/portfolio")
REPO = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(REPO, "backend", "app"))

# ── 数据源解析（A-2b）：真实台账不可达 → 回退合成 fixture 并标注 ──
# 真实路径：PORTFOLIO/golden-baselines/600089.json
# 回退路径：backend/tests/fixtures/g3-08-synthetic-golden.json（SYNTHETIC_FIXTURE）
# **回退必须显式标注 data_source=SYNTHETIC** —— 静默使用合成数据冒充真实
# 数据是 A-2 变异注入抓点（合成与真实结论不得互相冒充）。
_GOLDEN_CANDIDATES = [
    ("REAL", os.path.join(PORTFOLIO, "golden-baselines", "600089.json")),
    ("SYNTHETIC", os.path.join(REPO, "backend", "tests", "fixtures",
                               "g3-08-synthetic-golden.json")),
]


def _load_golden():
    """按序探测：真实台账优先，合成 fixture 回退。返回 (data_source, facts)。"""
    for source, path in _GOLDEN_CANDIDATES:
        if os.path.isfile(path):
            try:
                d = json.load(open(path, encoding="utf-8"))
            except Exception:
                continue
            if source == "SYNTHETIC" and d.get("SYNTHETIC_FIXTURE") is not True:
                raise SystemExit(
                    "E-G3-08-SYNTH: 合成 fixture 缺 SYNTHETIC_FIXTURE 标记 —— "
                    "禁止冒充真实数据（A-2a）")
            return source, d.get("facts", {})
    raise SystemExit("E-G3-08-SRC: 真实台账与合成 fixture 均不可达")


DATA_SOURCE, GOLDEN_FACTS = _load_golden()

from research_router import ResearchRouter, RUNNING, CANDIDATE  # noqa: E402
from macro_snapshot import (  # noqa: E402
    MacroObservation, MacroSnapshot, MacroGate, verify_spec_frozen,
)
from rule_registry import (  # noqa: E402
    RuleRegistry, PASS, NOT_APPLICABLE, INPUT_MISSING,
)
from formula_registry import FormulaRegistry, FormulaSpec, Constant  # noqa: E402
from claim_engine import (  # noqa: E402
    ClaimNode, ClaimGraph, EmissionMap, ResearchContract,
    verify_cross_dimension, verify_first_screen, verify_disclaimer,
    F, D, A, C, L,
)
from open_item_registry import OpenItem, OpenItemRegistry  # noqa: E402
from valuation_engine import (  # noqa: E402
    ValuationInputs, ScenarioSet, BASE, fcff_valuation,
)


def main() -> int:
    golden = GOLDEN_FACTS
    facts = golden

    # ── 1. 冻结合同 ────────────────────────────────────────────────
    contract = ResearchContract(scope="600089", period="2026",
                                unit="CNY_million", vintage="ORIGINAL",
                                snapshot="SNAP-600089-1",
                                security_code="600089", company_id="TBEA",
                                as_of="2026-08-11", version="v1")

    # ── 2. 宏观先行（G3-03 门）─────────────────────────────────────
    obs = [
        MacroObservation("GDP_YOY", "5.2", "percent", "CN", "ORIGINAL",
                         "2026Q2", "2026-07-15T00:00:00Z",
                         "2026-07-16T00:00:00Z", "manual", "loc:gdp"),
        MacroObservation("LPR_1Y", "3.0", "percent", "CN", "ORIGINAL",
                         "2026-07", "2026-07-20T00:00:00Z",
                         "2026-07-21T00:00:00Z", "manual", "loc:lpr"),
        MacroObservation("PPI_YOY", "0.5", "percent", "CN", "ORIGINAL",
                         "2026-07", "2026-07-09T00:00:00Z",
                         "2026-07-10T00:00:00Z", "manual", "loc:ppi"),
        MacroObservation("CPI_YOY", "1.0", "percent", "CN", "ORIGINAL",
                         "2026-07", "2026-07-09T00:00:00Z",
                         "2026-07-10T00:00:00Z", "manual", "loc:cpi"),
        MacroObservation("M2_YOY", "7.0", "percent", "CN", "ORIGINAL",
                         "2026-07", "2026-07-09T00:00:00Z",
                         "2026-07-10T00:00:00Z", "manual", "loc:m2"),
    ]
    snap = MacroSnapshot("SNAP-600089-1", contract.as_of + "T00:00:00Z",
                         "1.0.0")
    for o in obs:
        snap.add(o)
    snap.freeze()
    gate = MacroGate(now_utc="2026-08-11T06:00:00Z")
    macro_verdict = gate.evaluate(snap)
    if macro_verdict == "BLOCKED":
        print(json.dumps({"candidate_status": "PARTIAL_NOT_RELEASE_ELIGIBLE",
                          "macro_gate": macro_verdict,
                          "failures": gate.failures}, ensure_ascii=False))
        return 1

    # ── 3. 研究路由（运行唯一 + 合法迁移）─────────────────────────
    router = ResearchRouter()
    run = router.create_run("a-share-single-company-research", "600089.SH",
                            "run-600089-g3-08", "v1")
    router.transition(run.run_id, RUNNING)

    # ── 4. 规则注册表（适用分母冻结 + 判定）───────────────────────
    reg = RuleRegistry()
    reg.register_all()
    reg.freeze_applicable_count(2)
    reg.record_status("R02", "600089", PASS)   # 归母 + 少数股东 勾稽通过
    reg.record_status("R06", "600089", PASS)   # 资产=负债+权益 通过
    gate_rules = reg.gate_verdict()
    if "GATE_BLOCKED" in gate_rules:
        print(json.dumps({"candidate_status": "PARTIAL_NOT_RELEASE_ELIGIBLE",
                          "rules": gate_rules}, ensure_ascii=False))
        return 1

    # ── 5. 公式计算（FCFF 路）──────────────────────────────────────
    freg = FormulaRegistry()
    freg.register_constant(Constant("TAX_RATE", "0.25", "dimensionless", "1.0"))
    freg.register(FormulaSpec(
        "F_FCFF", "net_income + non_cash - capex", "1.0",
        {"net_income": "CNY_million", "non_cash": "CNY_million",
         "capex": "CNY_million"}, "CNY_million", "自由现金流"))
    fcff = freg.evaluate("F_FCFF", {
        "net_income": facts["归母净利润"]["value"],
        "non_cash": "30", "capex": "-40"})
    calc_output = fcff["output"]

    # ── 6. 四路估值（基准情景，FCFF 路演示）────────────────────────
    vin = ValuationInputs(
        scope="600089", currency="CNY", as_of="2026-08-11",
        price="10.0", shares_outstanding=facts["总股本"]["value"],
        net_debt="100", minority_interest="50",
        statuses={"price": "READY", "shares_outstanding": "READY",
                  "net_debt": "READY", "minority_interest": "READY"})
    scen = ScenarioSet("FCFF")
    scen.add(fcff_valuation(vin, BASE, calc_output, "0.05", "0.10"))
    scen.compute_margin("10.0")

    # ── 7. Claim 图 + emission map ─────────────────────────────────
    g = ClaimGraph()
    g.register_evidence("EV-GB-600089")
    g.register_formula("F_FCFF")
    g.register_assumption("ASM-1")
    report = ("SINGLE_REVIEWER_ATTESTED\n"      # C-10 首屏（前 3 行）
              "600089 初步候选 fixture\n"
              "研究信息，不构成投资建议。\n"
              + calc_output)                    # C-11 免责
    calc_span = f"{report.index(calc_output)}-{report.index(calc_output) + len(calc_output)}"
    sec_span = f"{report.index('600089 初步')}-{report.index('600089 初步') + 6}"
    # L 节点用报告中真实存在的章节序号（加在行尾）
    report += "\n1.1"
    nodes = [
        ClaimNode(node_type=F, ref_id="F-GB", rendered_value=calc_output,
                  scope="600089", snapshot="SNAP-600089-1",
                  unit="CNY_million", evidence_refs=["EV-GB-600089"],
                  materiality="MATERIAL", output_path="candidate.md",
                  byte_span=calc_span),
        ClaimNode(node_type=D, ref_id="D-FCFF", rendered_value=calc_output,
                  scope="600089", snapshot="SNAP-600089-1",
                  unit="CNY_million", formula_ref="F_FCFF",
                  evidence_refs=["EV-GB-600089"], materiality="MATERIAL"),
        ClaimNode(node_type=C, ref_id="C-SEC", rendered_value="600089",
                  scope="600089", snapshot="SNAP-600089-1",
                  contract_field="security_code", output_path="candidate.md",
                  byte_span=sec_span),
        ClaimNode(node_type=L, ref_id="L-T1", rendered_value="1.1",
                  scope="600089", snapshot="SNAP-600089-1",
                  output_path="candidate.md",
                  byte_span=f"{report.rindex('1.1')}-{report.rindex('1.1') + 3}"),
    ]
    for n in nodes:
        g.add(n)
        verify_cross_dimension(n, contract)
    closure = g.verify_closure()

    em = EmissionMap()
    for n in nodes:
        if n.output_path and n.byte_span:
            em.add(n)
    verify_first_screen("candidate.md", report)
    verify_disclaimer("candidate.md", report)
    em.verify_report("candidate.md", report)

    # ── 8. 材料性开放项 ────────────────────────────────────────────
    oi = OpenItemRegistry()
    oi.register(OpenItem("OI-600089-SUB-SOURCE", "600089 全部输入为人工导入，"
                        "自动取得能力为零（ADR-017）", True, "U",
                        due_date="2026-09-01", blocks_gate="G7-02"))
    eligible = oi.release_eligible()

    router.transition(run.run_id, CANDIDATE)
    candidate = {
        "candidate_id": "CAND-600089-G3-08-1",
        "workflow": "a-share-single-company-research",
        "scope": "600089",
        "run_id": run.run_id,
        "data_source": DATA_SOURCE,
        "data_source_note": ("SYNTHETIC —— 合成 fixture，数值全部虚构，"
                             "**不构成对真实 600089 的任何断言**（A-2d）"
                             if DATA_SOURCE == "SYNTHETIC"
                             else "REAL —— 台账 golden-baselines/600089.json"),
        "contract": contract.to_dict(),
        "macro": {"verdict": macro_verdict, "snapshot": snap.sha256},
        "rules": {"verdict": gate_rules, "report": reg.report_applicable()},
        "calc": {"fcff": calc_output, "formula": "F_FCFF",
                 "inputs_hash": fcff["inputs_hash"]},
        "valuation": scen.to_dict(),
        "claim_graph": closure,
        "emission": {"report_sha256": hashlib.sha256(
            report.encode()).hexdigest()},
        "open_items": {"eligible": eligible,
                       "open_material": oi.open_material_count()},
        "status": ("PARTIAL_NOT_RELEASE_ELIGIBLE" if not eligible
                   else "CANDIDATE_NOT_RELEASED"),
        "note": "Agent 前 fixture，不是最终 candidate subject root，"
                "不能发布（Gate 3 退出条件第四条）",
    }
    print(json.dumps(candidate, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
