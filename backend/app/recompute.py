"""recompute.py —— G6A-05 假设批准后确定性全量回算。

基线验收（G6A-05）：
  · 质询/裁决产生的每条 AssumptionProposal 独立批准或拒绝（复用 G3-13）
  · 新 AssumptionSnapshot
  · 重新生成 CalcLedger、四路估值、三情景、Claim/emission map、
    OpenItem 和 candidate hash
  · Agent/裁决无批准权（assumption_snapshot._assert_approver，LLM 一律拒绝）
  · 拒绝项不进入计算（AssumptionSnapshot.approved_payloads 为唯一入口）
  · 批准后必须从冻结输入全量回算而非局部手改
  · 旧 candidate/subject root 失效并保留；回算前后差异可审计

执行计划要点（G6A-执行计划.md §4）：
  F-3  批准一个假设后，断言**所有**受影响产物都被重算（全量，不接受抽样）；
       变异注入：让一个受影响产物不参与回算，须 FAIL
  F-4  「受影响」的判定须落库而非临时推断 —— PRODUCT_DEPS 即落库形态

设计：
  · ResearchContext = 全部冻结输入（合同/事实/宏观/估值输入/公式表）+ 已批准
    假设快照。recompute_all() 是纯函数：同一输入 → 同一产物字节（确定性）。
  · PRODUCT_DEPS：产物 → 其读取的假设键集合（F-4）。产物生成器只从
    （冻结输入 ∪ 已批准假设）取值；假设未批准时用冻结合同默认值 ——
    拒绝项由此不进入计算。
  · 全量回算 = 注册表内每个产物**都**被重新生成（变异：注册表缺一项即被抓）。
  · 旧候选失效并保留：失效记录（candidate_invalidation）另行落库，
    旧对象不删除（内容寻址不可变），新候选内容寻址冻结。
"""
import hashlib
import json
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Dict, List, Optional, Tuple

from artifact_store import ArtifactStore
from assumption_snapshot import AssumptionSnapshot
from open_item_registry import OPEN, OpenItem, OpenItemRegistry
from publish_engine import canonical_bytes
from valuation_engine import (
    BASE, OPTIMISTIC, PESSIMISTIC,
    ValuationInputs, ValuationResult,
    fcff_valuation, fcfe_valuation,
    pe_roe_pb_valuation, relative_valuation, cross_check,
)

CANDIDATE_KIND = "candidate"
INVALIDATION_KIND = "candidate_invalidation"


class RecomputeError(ValueError):
    pass


class ProductMissing(RecomputeError):
    """全量回算缺产物 —— 抽样/漏算（F-3 变异抓点）。"""


class OldCandidateMissing(RecomputeError):
    """失效记录引用的旧候选不可达 —— 不得「失效并保留」的假象。"""


@dataclass(frozen=True)
class OpenItemsPolicy:
    """G3-14 开放项生成策略 —— **冻结输入**（OI-PF-170）。

    owner_role / due_date / blocks_gate / tolerance 必须来自本策略的冻结值，
    不得读墙钟、环境变量或硬编码当前日期；缺失、空值、非法容差由
    `_resolve_open_items_policy` 失败关闭（RecomputeError），不默认补值。

    tolerance 为交叉验证相对容差（Decimal 字符串，>0）：越宽越少项，
    可宽到一致结果不产生任何项（**允许空集**；禁止无条件塞占位项）。
    """
    tolerance: str
    owner_role: str
    due_date: str
    blocks_gate: str


@dataclass
class ResearchContext:
    """全部冻结输入 + 已批准假设快照。

    字段均为冻结对象（可哈希字典）；approved 为不可变 AssumptionSnapshot。
    """
    contract: Dict[str, object]
    facts: Dict[str, str]
    macro: Dict[str, str]
    formula_specs: Dict[str, object]
    valuation_inputs: ValuationInputs
    assumption_defaults: Dict[str, str]   # 假设键 → 冻结合同默认值
    approved: AssumptionSnapshot
    open_items_policy: Optional[OpenItemsPolicy] = None

    def approved_keys(self) -> set:
        """已批准假设的键集合（payload 以 proposal_id 为键 —— 展平取键）。"""
        keys = set()
        for payload in self.approved.approved_payloads().values():
            keys.update(payload)
        return keys

    def values(self) -> Dict[str, str]:
        """进入计算的唯一取值路径：已批准假设覆盖合同默认；
        拒绝项不在 approved_payloads 中 → 用冻结默认（不进入计算）。"""
        v = dict(self.assumption_defaults)
        for payload in self.approved.approved_payloads().values():
            v.update(payload)
        return v


# ════════════════════════════════════════════════════════════════
# F-4：受影响判定落库 —— PRODUCT_DEPS（产物 → 假设键）
# ════════════════════════════════════════════════════════════════

PRODUCT_DEPS: Dict[str, Tuple[str, ...]] = {
    # OI-PF-171：声明须与生成器**真实读取**逐一对应，既不欠报也不少报。
    # calc_ledger 的生成器只从 v 读 growth（其余键仅用于账本无关的元数据），
    # claim_map 只读 growth/wacc —— 旧声明把五个键全列上属偏保守多报，
    # 使 F-4 受影响判定不准确（批准 ke/target_pe/roe 会虚报这两个产物受影响）。
    # 最小修复：声明收敛到当前真实读取，不扩张产品输出语义。
    "calc_ledger": ("growth",),
    "valuation_fcff": ("wacc",),            # FCFF 路引擎以终值增速为分母，
    #                                        # 增速参数不影响其结果（如实落库，F-4）
    "valuation_fcfe": ("growth", "ke"),
    "valuation_relative": ("target_pe",),
    "valuation_pe_roe_pb": ("roe", "target_pe"),
    "scenario_pessimistic": ("growth", "ke"),
    "scenario_base": ("growth", "ke"),
    "scenario_optimistic": ("growth", "ke"),
    "claim_map": ("growth", "wacc"),
    # OI-PF-169 修复后：emission_map 由 claim_map 派生，故依赖同一批键。
    # 原值 () 与「恒返回空」互为因果 —— 依赖表说它不读任何假设，
    # 生成器就真的什么也没产出。
    "emission_map": ("growth", "wacc"),
    # OI-PF-170：open_items 由四路估值交叉验证派生（_valuation_results
    # 唯一实现路径），四路合计读取**全部五个**假设键 —— fcff 读 wacc、
    # fcfe 读 growth/ke、relative 读 target_pe、pe_roe_pb 读 roe/target_pe。
    # 任一键批准都可能改变某路基准价 → 改变交叉验证差异与开放项集，
    # 故五键全部如实落库（F-4 判定不欠报）。
    "open_items": ("growth", "wacc", "ke", "target_pe", "roe"),
}

PRODUCT_ORDER = tuple(PRODUCT_DEPS)


# ════════════════════════════════════════════════════════════════
# 产物生成器（纯函数：frozen inputs + approved values）
# ════════════════════════════════════════════════════════════════

def _gen_calc_ledger(ctx: ResearchContext, v: Dict[str, str]) -> dict:
    growth = v["growth"]
    approved = ctx.approved_keys()
    return {
        "ledger": [
            {"metric": "growth_assumption", "value": growth,
             "source": ("approved_assumption" if "growth" in approved
                        else "contract_default")},
        ],
        "formula_count": len(ctx.formula_specs),
    }


def _valuation_results(ctx: ResearchContext,
                       v: Dict[str, str]) -> Dict[str, ValuationResult]:
    """四路估值（BASE 情景）**唯一实现路径**（OI-PF-170）。

    valuation 各产物与 open_items 的交叉验证**共用同一批 ValuationResult**，
    避免两套公式漂移 —— open_items 若自算一遍口径会与估值产物分叉。
    """
    vi = ctx.valuation_inputs
    f = ctx.facts
    return {
        "fcff": fcff_valuation(vi, BASE, f["fcff"], v["wacc"]),
        "fcfe": fcfe_valuation(vi, BASE, f["fcfe"], v["growth"], v["ke"]),
        "relative": relative_valuation(vi, BASE, v["target_pe"], f["eps"]),
        "pe_roe_pb": pe_roe_pb_valuation(
            vi, BASE, v["roe"], f["book_per_share"], v["target_pe"]),
    }


def _gen_valuation(ctx: ResearchContext, v: Dict[str, str], route: str) -> dict:
    """四路估值（BASE 情景）产物 —— 取 `_valuation_results` 的对应路。"""
    return _valuation_results(ctx, v)[route].to_dict()


def _gen_scenario(ctx: ResearchContext, v: Dict[str, str], scenario: str) -> dict:
    """三情景：同公式（FCFE 路，增速参数实际参与计算）、不同参数集。"""
    adj = {"pessimistic": "0.90", "base": "1.00", "optimistic": "1.10"}
    k = adj[scenario]
    from decimal import Decimal
    g = str(Decimal(v["growth"]) * Decimal(k))
    ke = v["ke"]
    r = fcfe_valuation(ctx.valuation_inputs, scenario.upper(),
                       ctx.facts["fcfe"], g, ke)
    return {"scenario": scenario.upper(), **r.to_dict()}


def _gen_claim_map(ctx: ResearchContext, v: Dict[str, str]) -> dict:
    return {
        "claims": [
            {"id": "CLM-1", "text": "营收增速假设", "assumption": "growth",
             "value": v["growth"]},
            {"id": "CLM-2", "text": "WACC 假设", "assumption": "wacc",
             "value": v["wacc"]},
        ],
    }


def _gen_emission_map(ctx: ResearchContext, v: Dict[str, str]) -> dict:
    """`visible_span ↔ claim_node` 映射（G3-05 明列交付件）。

    **OI-PF-169**：原实现是 `return {"emissions": sorted(PRODUCT_DEPS["emission_map"])}`
    —— 它把**本产物读取哪些假设键**当作 emissions 输出，而那两件事不同；
    且 `PRODUCT_DEPS["emission_map"] = ()`，故**恒返回空**。

    基线 B §10 G3-05 交付件明列「`visible_span↔claim_node` emission map」，
    验收明写「除白名单 C/L 外**每段可见内容一一绑定 Claim**」。
    恒空 ⇒ 该绑定在回算产物中不存在 —— 一件明列交付件的缺失。

    本实现由 claim_map 的产出派生：每条 Claim 生成一个可见片段绑定，
    使「可见内容 → Claim」可查、且随假设值变化而变化。
    """
    claims = _gen_claim_map(ctx, v).get("claims", [])
    return {"emissions": [
        {"visible_span": f"span:{c['id']}",
         "claim_node": c["id"],
         "rendered_value": c["value"],
         "assumption": c["assumption"]}
        for c in claims]}


def _resolve_open_items_policy(ctx: ResearchContext) -> OpenItemsPolicy:
    """冻结 policy 校验（失败关闭）：缺失 / 空值 / 非法容差 → RecomputeError。

    不读墙钟、环境变量或硬编码当前日期；**不默认补值** —— 补一个默认
    owner/due_date/blocks_gate/tolerance 等于把「谁来负责、何时截止、
    多宽才算一致」写死进实现，那是另一处硬编码。
    """
    p = ctx.open_items_policy
    if p is None:
        raise RecomputeError(
            "E-OI-PF-170-001: 冻结 OpenItemsPolicy 缺失 —— 失败关闭，"
            "不默认补值")
    tol_text = str(p.tolerance or "").strip()
    if not tol_text:
        raise RecomputeError(
            "E-OI-PF-170-002: OpenItemsPolicy.tolerance 为空 —— 失败关闭")
    try:
        tol = Decimal(tol_text)
    except InvalidOperation:
        raise RecomputeError(
            f"E-OI-PF-170-002: OpenItemsPolicy.tolerance 非法 "
            f"({tol_text!r}) —— 失败关闭")
    if tol <= 0:
        raise RecomputeError(
            f"E-OI-PF-170-002: OpenItemsPolicy.tolerance 必须 > 0"
            f"（实得 {tol_text}）—— 失败关闭")
    for fld in ("owner_role", "due_date", "blocks_gate"):
        if not str(getattr(p, fld) or "").strip():
            raise RecomputeError(
                f"E-OI-PF-170-003: OpenItemsPolicy.{fld} 为空 —— 失败关闭")
    return p


def _gen_open_items(ctx: ResearchContext, v: Dict[str, str]) -> dict:
    """G3-06 交叉验证 → G3-14 开放项（OI-PF-170）。

    差异 > 冻结容差的真实交叉验证不一致 → 强类型 OpenItem（material=true）
    + 原始诊断（scenario/method_a/method_b/diff/tolerance）原样保留；
    一致或容差足够宽 → **允许空集**。禁止无条件塞占位项 —— 项必须由
    `valuation_engine.cross_check` 的真实结果产生。
    """
    policy = _resolve_open_items_policy(ctx)
    mismatches = cross_check(list(_valuation_results(ctx, v).values()),
                             policy.tolerance)
    reg = OpenItemRegistry()
    for m in mismatches:
        reg.register(OpenItem(
            open_item_id=m["open_item_id"],
            description=(
                f"G3-06 交叉验证不一致：{m['scenario']} 下 {m['method_a']} "
                f"与 {m['method_b']} 基准价相对差 {m['diff']} "
                f"> 冻结容差 {m['tolerance']}"),
            material=True,
            owner_role=policy.owner_role,
            due_date=policy.due_date,
            blocks_gate=policy.blocks_gate,
            closure_evidence=None,
            status=OPEN,
        ))
    return {
        "open_items": [it.to_dict() for it in reg.items.values()],
        # G3-06 交叉验证诊断原样保留（scenario/method_a/method_b/diff/tolerance）
        "cross_check": mismatches,
    }


GENERATORS = {
    "calc_ledger": lambda ctx, v: _gen_calc_ledger(ctx, v),
    "valuation_fcff": lambda ctx, v: _gen_valuation(ctx, v, "fcff"),
    "valuation_fcfe": lambda ctx, v: _gen_valuation(ctx, v, "fcfe"),
    "valuation_relative": lambda ctx, v: _gen_valuation(ctx, v, "relative"),
    "valuation_pe_roe_pb": lambda ctx, v: _gen_valuation(ctx, v, "pe_roe_pb"),
    "scenario_pessimistic": lambda ctx, v: _gen_scenario(ctx, v, "pessimistic"),
    "scenario_base": lambda ctx, v: _gen_scenario(ctx, v, "base"),
    "scenario_optimistic": lambda ctx, v: _gen_scenario(ctx, v, "optimistic"),
    "claim_map": lambda ctx, v: _gen_claim_map(ctx, v),
    "emission_map": lambda ctx, v: _gen_emission_map(ctx, v),
    "open_items": lambda ctx, v: _gen_open_items(ctx, v),
}


def _prod_sha(product: dict) -> str:
    return hashlib.sha256(canonical_bytes(product)).hexdigest()


@dataclass
class RecomputeResult:
    products: Dict[str, dict] = field(default_factory=dict)
    shas: Dict[str, str] = field(default_factory=dict)

    def product_ids(self) -> Tuple[str, ...]:
        return tuple(sorted(self.products))


def recompute_all(ctx: ResearchContext) -> RecomputeResult:
    """全量回算：注册表内每个产物都重新生成（F-3）。

    变异注入：把 GENERATORS/PRODUCT_DEPS 里的某一项摘掉，本函数的
    产出就少一个产物 —— 测试对 product_ids() 与 PRODUCT_ORDER 逐项
    断言，缺任一即 FAIL（全量，不接受抽样）。
    """
    v = ctx.values()
    res = RecomputeResult()
    for name in PRODUCT_ORDER:
        if name not in GENERATORS:
            raise ProductMissing(f"E-G6A-05-001: 产物 {name} 无生成器 —— "
                                 f"注册表与生成器不一致（全量回算不可抽样）")
        prod = GENERATORS[name](ctx, v)
        res.products[name] = prod
        res.shas[name] = _prod_sha(prod)
    return res


def recompute_diff(old: RecomputeResult, new: RecomputeResult) -> dict:
    """回算前后差异可审计：逐产物 sha 对照 + 变化清单。"""
    changed = sorted(n for n in PRODUCT_ORDER
                     if old.shas.get(n) != new.shas.get(n))
    return {
        "changed_products": changed,
        "per_product": {n: {"before": old.shas.get(n), "after": new.shas.get(n)}
                        for n in PRODUCT_ORDER},
    }


@dataclass
class CandidateFreeze:
    candidate_id: str
    candidate: dict
    recompute: RecomputeResult


def freeze_candidate_from_recompute(store: ArtifactStore, ctx: ResearchContext,
                                    run_id: str,
                                    recompute: RecomputeResult) -> CandidateFreeze:
    """按回算产物组装候选并内容寻址冻结（candidate hash = 内容哈希）。"""
    candidate = {
        "schema_version": "1.0.0",
        "run_id": run_id,
        "contract": ctx.contract.get("contract_id"),
        "scope": ctx.valuation_inputs.scope,
        "as_of": ctx.valuation_inputs.as_of,
        "products": recompute.product_ids(),
        "product_hashes": recompute.shas,
        "approved_snapshot": ctx.approved.sha256,
    }
    data = canonical_bytes(candidate)
    store.store(CANDIDATE_KIND, data)
    cid = hashlib.sha256(data).hexdigest()
    return CandidateFreeze(candidate_id=cid, candidate=candidate,
                           recompute=recompute)


def invalidate_previous(store: ArtifactStore, old_candidate_id: str,
                        new_candidate_id: str, reason: str) -> str:
    """旧 candidate 失效并保留：
    · 保留 —— 旧对象内容寻址不可变，仍在对象库中可读（校验即证）
    · 失效 —— 失效记录落库（kind=candidate_invalidation），引用旧 id
    """
    if not store.exists(old_candidate_id):
        raise OldCandidateMissing(
            f"E-G6A-05-002: 旧候选不可达 {old_candidate_id[:12]}… —— "
            f"「失效并保留」无从谈起")
    inv = {
        "schema_version": "1.0.0",
        "old_candidate_id": old_candidate_id,
        "new_candidate_id": new_candidate_id,
        "reason": reason,
        "status": "INVALIDATED",
    }
    data = canonical_bytes(inv)
    store.store(INVALIDATION_KIND, data)
    return hashlib.sha256(data).hexdigest()
