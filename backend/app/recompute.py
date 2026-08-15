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
  · OI-PF-195：执行前失败关闭校验 PRODUCT_ORDER 无重复，且
    set(PRODUCT_ORDER) == set(PRODUCT_DEPS) == set(GENERATORS)，逐方向列差集；
    单向检查（order 中每项有生成器）抓不住 GENERATORS 多出未登记项（静默遗漏）
    与 PRODUCT_DEPS/order 漂移。
   · OI-PF-196：candidate 绑定规范冻结输入哈希（frozen_inputs_hash）—— 全部
     顶层冻结输入（contract/facts/macro/formula_specs/valuation_inputs 全部
     dataclass 字段含 statuses/assumption_defaults/approved 不可变身份+sha256/
     open_items_policy 全部字段）任一变化都改变哈希与候选身份，不依赖 run_id
     或产物输出；缺 policy/字段形态不符/JSON 不可规范序列化 → RecomputeError
     E-G6A-05-003 失败关闭，不生成 candidate（不用 str/repr 悄悄吞掉）。
   · OI-PF-199：已冻结 AssumptionSnapshot 正文漂移失败关闭 —— 每次读取
     sha256/approved_payloads 及冻结 candidate 前重算正文哈希并与冻结值比对，
     直接篡改 snap.approved 使快照失效并转 RecomputeError E-G6A-05-003；
     正文深拷贝防浅拷贝/返回值别名（见 assumption_snapshot.py）。
   · 旧候选失效并保留：失效记录（candidate_invalidation）另行落库，
     旧对象不删除（内容寻址不可变），新候选内容寻址冻结。
   · OI-PF-204：失效事实须**同时**保留不可变审计证据（ArtifactStore 内容寻址
     记录）并进入权威查询面（candidate_invalidation 表，按 old_candidate_id
     唯一）—— 写失效前必须 store.load() 并验证 old/new 两端都是完整 candidate
     对象（JSON object、kind=candidate、内容摘要匹配），缺失/内容损坏/其他
     kind/new 不存在均失败关闭 E-G6A-05-002；重复相同失效应幂等，冲突
     new/reason 拒绝 E-G6A-05-008，不得静默覆盖；create_approval /
     is_release_eligible / publish_release 均以权威查询面拒绝已失效 candidate。
   · OI-PF-200：每个 RecomputeResult 绑定产生它的上下文的规范冻结输入哈希
     （frozen_inputs_hash）；recompute_all 在生成产物前后各取一次规范哈希并
     比对，生成期间上下文漂移 → RecomputeError E-G6A-05-004 失败关闭。
     freeze_candidate_from_recompute 冻结前**独立重算**当前 ResearchContext
     的规范结果（canonical），并把调用方传入的回算结果逐项与独立重算结果比对
     —— 调用方提供的绑定字段、products、shas 任一都不作为权威来源（改绑绑定
     字段的陈旧结果、产物+记录哈希同步篡改都被独立重算比对拒绝）。绑定哈希
     不一致 E-G6A-05-005 拒绝、键集漂移或产物/哈希与独立重算不符 E-G6A-05-006
     拒绝；candidate 由独立重算结果组装，其 frozen_inputs_hash/product 数据
     **仅**来自 canonical —— 均不存储 candidate，不加兼容路径。
   · OI-PF-200（返工）：写入边界最终一致性校验 —— canonical 独立重算返回后、
     存储前重算当前上下文规范冻结输入哈希并要求等于 canonical 绑定哈希；任何
     canonical 返回后的上下文漂移都转 RecomputeError E-G6A-05-007 失败关闭，
     零 candidate 写入（不携带更早上下文哈希，不组装混合候选）。
"""
import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Dict, List, Optional, Tuple

from artifact_store import ArtifactStore
from assumption_snapshot import AssumptionSnapshot
from open_item_registry import OPEN, OpenItem, OpenItemRegistry
from publish_engine import canonical_bytes
from valuation_engine import (
    BASE, OPTIMISTIC, PESSIMISTIC, SCENARIOS,
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


# ════════════════════════════════════════════════════════════════
# G6A-06 PARTIAL：估值路由声明（fcff/fcfe/relative/pe_roe_pb）
# ════════════════════════════════════════════════════════════════

VALUATION_ROUTES = ("fcff", "fcfe", "relative", "pe_roe_pb")

ROUTE_READY = "READY"
ROUTE_INPUT_MISSING = "INPUT_MISSING"
ROUTE_NOT_EVALUATED = "NOT_EVALUATED"
ROUTE_STATES = (ROUTE_READY, ROUTE_INPUT_MISSING, ROUTE_NOT_EVALUATED)

# 每路估值/情景产物的 typed method 标签（与 valuation_engine 输出一致）
ROUTE_METHODS = {
    "fcff": "FCFF",
    "fcfe": "FCFE",
    "relative": "RELATIVE_PE",
    "pe_roe_pb": "PE_ROE_PB",
}

# 每路估值「声明 READY 才必须出现」的路由专属事实键
ROUTE_FACT_KEYS = {
    "fcff": "fcff",
    "fcfe": "fcfe",
    "relative": "eps",
    "pe_roe_pb": "book_per_share",
}

# 每路估值路由**实际消费**的假设键（canonical 单点真源：校验 + 产物共用）。
# 只列该路引擎真正读取的假设 —— READY 路由缺失所需假设即 E-G6A-06-020
# 失败关闭；非 READY 路由不消费任何假设，调用方提供的无关默认/提案不得
# 进入账本/声明/映射产物。
ROUTE_ASSUMPTIONS = {
    "fcff": ("wacc",),
    "fcfe": ("growth", "ke"),
    "relative": ("target_pe",),
    "pe_roe_pb": ("roe", "target_pe"),
}

# 五个假设键的**确定性规范顺序**（calc_ledger/claim_map/emission_map 的
# 输出顺序）：全 READY 时恰好 = ROUTE_ASSUMPTIONS 并集（五键）；
# 部分 READY 时只保留被消费键，顺序不变。
ASSUMPTION_KEYS = ("growth", "wacc", "ke", "target_pe", "roe")

# 每个假设键在 claim_map 中的确定性文本（G3-05 可读可见绑定）。
ASSUMPTION_TEXTS = {
    "growth": "营收增速假设",
    "wacc": "WACC 假设",
    "ke": "股权成本假设",
    "target_pe": "目标市盈率假设",
    "roe": "净资产收益率假设",
}


def _consumed_assumptions(ctx: "ResearchContext") -> Tuple[str, ...]:
    """READY 路由实际消费的假设键（ASSUMPTION_KEYS 规范顺序）。

    只取声明 READY 的路由所消费的键 —— 非 READY 路由的假设键即使调用方
    提供了默认/提案也**不得**进入账本/声明/映射产物（不发明数值）。
    """
    routes = _declared_routes(ctx)
    consumed = set()
    for route in VALUATION_ROUTES:
        if routes[route].state == ROUTE_READY:
            consumed.update(ROUTE_ASSUMPTIONS[route])
    return tuple(k for k in ASSUMPTION_KEYS if k in consumed)


def _assumptions_for_statuses(rs: Dict[str, str]) -> Tuple[str, ...]:
    """从 route_statuses 派生同一规范假设键集（quality 校验用，无 ctx）。"""
    consumed = set()
    for route in VALUATION_ROUTES:
        if rs[route] == ROUTE_READY:
            consumed.update(ROUTE_ASSUMPTIONS[route])
    return tuple(k for k in ASSUMPTION_KEYS if k in consumed)


def _validate_ready_route_assumptions(ctx: "ResearchContext",
                                      v: Dict[str, str]) -> None:
    """回算边界：每个 READY 路由所需假设键必须作为**非空字符串**存在。

    在解析路由声明与 approved/default 取值后、产物生成前执行 —— 缺失任一
    必需假设 → RecomputeError E-G6A-06-020 失败关闭（不 KeyError、不静默
    用默认注入）；非 READY 路由不要求任何假设，其键允许不存在。
    """
    routes = _declared_routes(ctx)
    missing = []
    for route in VALUATION_ROUTES:
        decl = routes[route]
        if decl.state != ROUTE_READY:
            continue
        for key in ROUTE_ASSUMPTIONS[route]:
            val = v.get(key)
            if not (isinstance(val, str) and val.strip()):
                missing.append((route, key))
    if missing:
        raise RecomputeError(
            f"E-G6A-06-020: READY 路由所需假设缺失/空值 —— {missing} —— 失败关闭")


@dataclass(frozen=True)
class RouteDeclaration:
    """单路估值声明（冻结输入）。状态专属形状由 __post_init__ 强制：

      · READY          —— 不得带 reason/evidence_refs/missing_inputs；
      · INPUT_MISSING  —— 必须带非空 reason + 非空证据引用 + 非空
                           missing_inputs；
      · NOT_EVALUATED  —— 必须带非空 reason + 非空证据引用，不得带
                           missing_inputs。

    直接构造非法组合（缺 missing_inputs / 带多余字段 / 未知状态 / 类型错误）
    → RecomputeError E-G6A-06-020 失败关闭，不泄漏 KeyError、不静默吞掉矛盾。
    数值事实只能经 facts 且只对 READY 路出现（由 `_declared_routes` 校验）。
    """
    state: str
    reason: str = ""
    evidence_refs: Tuple[str, ...] = ()
    missing_inputs: Tuple[str, ...] = ()

    def __post_init__(self):
        if self.state not in ROUTE_STATES:
            raise RecomputeError(
                f"E-G6A-06-020: 路由状态 {self.state!r} 非法"
                f"（须为 {ROUTE_STATES}）—— 失败关闭")
        if not isinstance(self.reason, str):
            raise RecomputeError(
                f"E-G6A-06-020: 路由声明 reason 须为字符串"
                f"（实得 {type(self.reason).__name__}）—— 失败关闭")
        for label in ("evidence_refs", "missing_inputs"):
            val = getattr(self, label)
            if isinstance(val, list):
                # 冻结 dataclass 内不得保留可变 list 别名 —— 规范化为 tuple
                # （G6A-06 partial-route 硬化：调用方传 list 后改列表会反向
                # 漂移声明）。
                object.__setattr__(self, label, tuple(val))
                val = getattr(self, label)
            elif not isinstance(val, tuple):
                raise RecomputeError(
                    f"E-G6A-06-020: 路由声明 {label} 须为 tuple/list"
                    f"（实得 {type(val).__name__}）—— 失败关闭")
            if not all(isinstance(x, str) and x.strip() for x in val):
                raise RecomputeError(
                    f"E-G6A-06-020: 路由声明 {label} 须全为非空字符串"
                    " —— 失败关闭")
            if len(set(val)) != len(val):
                raise RecomputeError(
                    f"E-G6A-06-020: 路由声明 {label} 含重复项"
                    f"（{sorted(set(val))}）—— 失败关闭")
        if self.state == ROUTE_READY:
            extra = [k for k in ("reason", "evidence_refs", "missing_inputs")
                     if getattr(self, k)]
            if extra:
                raise RecomputeError(
                    f"E-G6A-06-020: READY 路由不得携带 {extra} —— 失败关闭")
            return
        if not self.reason.strip():
            raise RecomputeError(
                "E-G6A-06-020: 非 READY 路由必须带非空 reason —— 失败关闭")
        if not self.evidence_refs:
            raise RecomputeError(
                "E-G6A-06-020: 非 READY 路由必须带非空 evidence_refs"
                " —— 失败关闭")
        if self.state == ROUTE_INPUT_MISSING:
            if not self.missing_inputs:
                raise RecomputeError(
                    f"E-G6A-06-020: {ROUTE_INPUT_MISSING} 路由必须带非空"
                    " missing_inputs —— 失败关闭")
        elif self.missing_inputs:
            raise RecomputeError(
                f"E-G6A-06-020: {ROUTE_NOT_EVALUATED} 路由不得携带"
                " missing_inputs —— 失败关闭")

    def to_dict(self) -> dict:
        d = {"state": self.state}
        if self.state != ROUTE_READY:
            d["reason"] = self.reason
            d["evidence_refs"] = list(self.evidence_refs)
            if self.missing_inputs:
                d["missing_inputs"] = list(self.missing_inputs)
        return d


@dataclass(frozen=True)
class ValuationRoutes:
    """四路估值声明容器（键集必须恰好 = VALUATION_ROUTES）。"""
    routes: Dict[str, RouteDeclaration]


def _declared_routes(ctx: "ResearchContext") -> Dict[str, RouteDeclaration]:
    """解析路由声明并校验事实一致性。legacy 内部上下文缺省 = 全 READY
    （不发明 PARTIAL）；受管请求在 `candidate_service.final_candidate_request`
    已强制四路齐全。任何键集漂移、非 RouteDeclaration、声明状态与 facts 中
    该路数值事实相互矛盾（READY 缺该路事实 / 非 READY 夹带该路数值）→
    RecomputeError E-G6A-06-020 失败关闭。"""
    if ctx.valuation_routes is None:
        routes = {r: RouteDeclaration(ROUTE_READY) for r in VALUATION_ROUTES}
    else:
        vr = ctx.valuation_routes
        if not isinstance(vr, ValuationRoutes):
            raise RecomputeError(
                "E-G6A-06-020: valuation_routes 形态不符（须为 ValuationRoutes）"
                " —— 失败关闭")
        routes = vr.routes
        if set(routes) != set(VALUATION_ROUTES):
            raise RecomputeError(
                f"E-G6A-06-020: 估值路由声明键集 {sorted(routes)} ≠ "
                f"生产注册表 {list(VALUATION_ROUTES)} —— 失败关闭")
        for r in VALUATION_ROUTES:
            decl = routes.get(r)
            if not isinstance(decl, RouteDeclaration):
                raise RecomputeError(
                    f"E-G6A-06-020: valuation_routes.{r} 非 RouteDeclaration"
                    f"（实得 {type(decl).__name__}）—— 失败关闭")
        routes = dict(routes)
    facts = ctx.facts
    if not isinstance(facts, dict):
        raise RecomputeError(
            "E-G6A-06-020: ctx.facts 非对象 —— 失败关闭")
    for r in VALUATION_ROUTES:
        decl = routes[r]
        fk = ROUTE_FACT_KEYS[r]
        has = isinstance(facts.get(fk), str) and facts[fk].strip()
        if decl.state == ROUTE_READY:
            if not has:
                raise RecomputeError(
                    f"E-G6A-06-020: READY 路由 {r} 缺必需事实字段 "
                    f"facts.{fk} —— 声明/事实矛盾，失败关闭")
        elif fk in facts:
            raise RecomputeError(
                f"E-G6A-06-020: 非 READY 路由 {r} 携带数值事实 "
                f"facts.{fk} —— 非 READY 不得夹带该路数值，失败关闭")
    return routes


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
    valuation_routes: Optional[ValuationRoutes] = None

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
    # G6A-06 partial-route 返工：calc_ledger/claim_map/emission_map 只含 READY
    # 路由实际消费的假设。全 READY 时消费集 = ROUTE_ASSUMPTIONS 并集 = 全部
    # 五个键（F-4 all-READY 灵敏度须精确，故声明五键而非按生成时实际消费键
    # 写死 —— 声明的是**可能**消费的并集）；部分 READY 时实际只读被消费键，
    # 该子集在 QUALITY 校验侧与声明并集兼容（不欠报）。
    "calc_ledger": ASSUMPTION_KEYS,
    "valuation_fcff": ("wacc",),            # FCFF 路引擎以终值增速为分母，
    #                                        # 增速参数不影响其结果（如实落库，F-4）
    "valuation_fcfe": ("growth", "ke"),
    "valuation_relative": ("target_pe",),
    "valuation_pe_roe_pb": ("roe", "target_pe"),
    "scenario_pessimistic": ("growth", "ke"),
    "scenario_base": ("growth", "ke"),
    "scenario_optimistic": ("growth", "ke"),
    "claim_map": ASSUMPTION_KEYS,
    # OI-PF-169 修复后：emission_map 由 claim_map 派生，故依赖同一批键。
    # 原值 () 与「恒返回空」互为因果 —— 依赖表说它不读任何假设，
    # 生成器就真的什么也没产出。
    "emission_map": ASSUMPTION_KEYS,
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
    """账本只登记 READY 路由实际消费的假设（G6A-06 partial-route 返工）。

    全 READY → 五键全部登记；全部非 READY → ledger 可为空数组
    （formula_count 保留）。只取 `_consumed_assumptions` 的规范顺序 ——
    非 READY 路由的假设键即使调用方提供了默认/提案也不得进账本（不发明
    数值）。
    """
    approved = ctx.approved_keys()
    return {
        "ledger": [
            {"metric": f"{key}_assumption", "value": v[key],
             "source": ("approved_assumption" if key in approved
                        else "contract_default")}
            for key in _consumed_assumptions(ctx)
        ],
        "formula_count": len(ctx.formula_specs),
    }


def _run_route(route: str, vi: ValuationInputs, f: Dict[str, str],
               v: Dict[str, str]) -> ValuationResult:
    if route == "fcff":
        return fcff_valuation(vi, BASE, f["fcff"], v["wacc"])
    if route == "fcfe":
        return fcfe_valuation(vi, BASE, f["fcfe"], v["growth"], v["ke"])
    if route == "relative":
        return relative_valuation(vi, BASE, v["target_pe"], f["eps"])
    if route == "pe_roe_pb":
        return pe_roe_pb_valuation(
            vi, BASE, v["roe"], f["book_per_share"], v["target_pe"])
    raise RecomputeError(f"E-G6A-06-020: 未知估值路由 {route!r} —— 失败关闭")


@dataclass
class RouteOutcome:
    """单路估值结果：READY 携带 ValuationResult；非 READY 只携带声明元数据
    （含 missing_inputs —— 冻结后仍可追溯，不丢失）。"""
    route: str
    state: str
    result: Optional[ValuationResult] = None
    reason: str = ""
    evidence_refs: Tuple[str, ...] = ()
    missing_inputs: Tuple[str, ...] = ()


def _valuation_outcomes(ctx: ResearchContext,
                        v: Dict[str, str]) -> Dict[str, RouteOutcome]:
    """四路估值（BASE 情景）结果（OI-PF-170 唯一实现路径）。

    非 READY 路由不运行引擎、不产生任何数值 —— typed 状态产物由
    `_gen_valuation`/`_gen_scenario` 按声明输出（含 missing_inputs）；
    交叉验证只取 READY 路。
    """
    vi = ctx.valuation_inputs
    f = ctx.facts
    out: Dict[str, RouteOutcome] = {}
    for route in VALUATION_ROUTES:
        decl = _declared_routes(ctx)[route]
        if decl.state == ROUTE_READY:
            out[route] = RouteOutcome(route, ROUTE_READY,
                                      result=_run_route(route, vi, f, v))
        else:
            out[route] = RouteOutcome(route, decl.state, result=None,
                                      reason=decl.reason,
                                      evidence_refs=decl.evidence_refs,
                                      missing_inputs=decl.missing_inputs)
    return out


def _valuation_results(ctx: ResearchContext,
                       v: Dict[str, str]) -> List[ValuationResult]:
    """仅成功评估（READY）路由的结果集 —— 交叉验证唯一输入。"""
    return [o.result for o in _valuation_outcomes(ctx, v).values()
            if o.state == ROUTE_READY]


def _non_ready_product(route: str, scenario: str, outcome: RouteOutcome) -> dict:
    """非 READY 路由的确定性 typed 状态产物：无任何 per-share 数值；
    INPUT_MISSING 携带 missing_inputs（冻结后仍可追溯），NOT_EVALUATED 不带。"""
    prod = {
        "method": ROUTE_METHODS[route],
        "scenario": scenario,
        "status": outcome.state,
        "reason": outcome.reason,
        "evidence_refs": list(outcome.evidence_refs),
    }
    if outcome.missing_inputs:
        prod["missing_inputs"] = list(outcome.missing_inputs)
    return prod


def _gen_valuation(ctx: ResearchContext, v: Dict[str, str], route: str) -> dict:
    """四路估值（BASE 情景）产物。READY → PASS 数值产物；非 READY →
    typed 状态产物（无 per-share 数值，不夹带声明以外的数字事实）。"""
    outcome = _valuation_outcomes(ctx, v)[route]
    if outcome.state == ROUTE_READY:
        return {"status": "PASS", **outcome.result.to_dict()}
    return _non_ready_product(route, BASE, outcome)


def _gen_scenario(ctx: ResearchContext, v: Dict[str, str], scenario: str) -> dict:
    """三情景：同公式（FCFE 路，增速参数实际参与计算）、不同参数集；
    FCFE 非 READY → 传播声明状态（typed 产物，无数值，含 missing_inputs）。"""
    fcfe_decl = _declared_routes(ctx)["fcfe"]
    if fcfe_decl.state != ROUTE_READY:
        return _non_ready_product("fcfe", scenario.upper(), RouteOutcome(
            "fcfe", fcfe_decl.state, reason=fcfe_decl.reason,
            evidence_refs=fcfe_decl.evidence_refs,
            missing_inputs=fcfe_decl.missing_inputs))
    adj = {"pessimistic": "0.90", "base": "1.00", "optimistic": "1.10"}
    k = adj[scenario]
    from decimal import Decimal
    g = str(Decimal(v["growth"]) * Decimal(k))
    ke = v["ke"]
    r = fcfe_valuation(ctx.valuation_inputs, scenario.upper(),
                       ctx.facts["fcfe"], g, ke)
    return {"scenario": scenario.upper(), "status": "PASS", **r.to_dict()}


def _gen_claim_map(ctx: ResearchContext, v: Dict[str, str]) -> dict:
    """Claim 只声明 READY 路由实际消费的假设（G6A-06 partial-route 返工）。

    只取 `_consumed_assumptions` 的规范顺序；全部非 READY → claims 可为空
    数组。非 READY 路由的假设键即使调用方提供了默认/提案也不得进声明
    （不把无关假设伪装成结论）。
    """
    return {
        "claims": [
            {"id": f"CLM-{idx}", "text": ASSUMPTION_TEXTS[key],
             "assumption": key, "value": v[key]}
            for idx, key in enumerate(_consumed_assumptions(ctx), start=1)
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
    """G3-06 交叉验证 → G3-14 开放项（OI-PF-170 / G6A-06 PARTIAL）。

    差异 > 冻结容差的真实交叉验证不一致 → 强类型 OpenItem（material=true）
    + 原始诊断（scenario/method_a/method_b/diff/tolerance）原样保留；
    一致或容差足够宽 → **允许空集**。禁止无条件塞占位项 —— 项必须由
    `valuation_engine.cross_check` 的真实结果产生。

    G6A-06 PARTIAL：交叉验证只取 READY 路由（非 READY 无数值，不许把
    空/单路集冒充全局 PASS）；每个声明非 READY 的路由登记一个确定性
    material OPEN 开放项 —— owner_role/due_date/blocks_gate 一律取冻结
    OpenItemsPolicy，reason/证据引用原样保留进 description。
    """
    policy = _resolve_open_items_policy(ctx)
    outcomes = _valuation_outcomes(ctx, v)
    mismatches = cross_check(_valuation_results(ctx, v), policy.tolerance)
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
    for route in VALUATION_ROUTES:
        o = outcomes[route]
        if o.state == ROUTE_READY:
            continue
        detail = (f"估值路由 {route.upper()} 未评估（{o.state}）：{o.reason}"
                  f"；证据引用：{', '.join(o.evidence_refs)}")
        if o.missing_inputs:
            detail += f"；缺失输入：{', '.join(o.missing_inputs)}"
        reg.register(OpenItem(
            open_item_id=f"OI-G6A06-RC-{route.upper()}-{o.state}",
            description=detail,
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
        "route_statuses": {route: outcomes[route].state
                           for route in VALUATION_ROUTES},
    }


QUALITY_FULL = "FULL"
QUALITY_PARTIAL = "PARTIAL"
ROUTE_PRODUCT_PASS = "PASS"

# 直接表达路由状态的产物名（四路估值 + 三情景，全部由 FCFE/FCFF 等派生）
VALUATION_PRODUCT_NAMES = (
    "valuation_fcff", "valuation_fcfe", "valuation_relative",
    "valuation_pe_roe_pb", "scenario_pessimistic", "scenario_base",
    "scenario_optimistic",
)

# 每个估值/情景产物对应的估值路由（三情景由 FCFE 路派生）
VALUATION_PRODUCT_ROUTES = {
    "valuation_fcff": "fcff",
    "valuation_fcfe": "fcfe",
    "valuation_relative": "relative",
    "valuation_pe_roe_pb": "pe_roe_pb",
    "scenario_pessimistic": "fcfe",
    "scenario_base": "fcfe",
    "scenario_optimistic": "fcfe",
}

# 每个估值/情景产物按产品名的**精确 method/scenario 标签**（与
# valuation_engine 输出一致，不允许把 valuation_fcff 标成 FCFE 等交叉换名）。
VALUATION_PRODUCT_METHODS = {
    "valuation_fcff": ("FCFF", BASE),
    "valuation_fcfe": ("FCFE", BASE),
    "valuation_relative": ("RELATIVE_PE", BASE),
    "valuation_pe_roe_pb": ("PE_ROE_PB", BASE),
    "scenario_pessimistic": ("FCFE", PESSIMISTIC),
    "scenario_base": ("FCFE", BASE),
    "scenario_optimistic": ("FCFE", OPTIMISTIC),
}

PER_SHARE_FIELDS = ("per_share_low", "per_share_high", "per_share_base")
_NON_READY_META = ("reason", "evidence_refs", "missing_inputs")

# 估值/情景产物的状态专属**精确键集**（多余/缺失字段一律失败关闭）
PASS_PRODUCT_KEYS = {"status", "method", "scenario", *PER_SHARE_FIELDS,
                     "triggers", "notes"}
INPUT_MISSING_PRODUCT_KEYS = {"status", "method", "scenario", "reason",
                              "evidence_refs", "missing_inputs"}
NOT_EVALUATED_PRODUCT_KEYS = {"status", "method", "scenario", "reason",
                              "evidence_refs"}

# 非估值三产物 + open_items 的精确形状（换哈希/换正文即键集不符 → 失败关闭）
CALC_LEDGER_KEYS = {"ledger", "formula_count"}
CALC_LEDGER_ENTRY_KEYS = {"metric", "value", "source"}
CLAIM_MAP_KEYS = {"claims"}
CLAIM_ENTRY_KEYS = {"id", "text", "assumption", "value"}
EMISSION_MAP_KEYS = {"emissions"}
EMISSION_ENTRY_KEYS = {"visible_span", "claim_node", "rendered_value",
                       "assumption"}
OPEN_ITEMS_PRODUCT_KEYS = {"open_items", "cross_check", "route_statuses"}
OPEN_ITEM_KEYS = {"open_item_id", "description", "material", "owner_role",
                  "due_date", "blocks_gate", "closure_evidence", "status"}
CROSS_CHECK_KEYS = {"open_item_id", "scenario", "method_a", "method_b",
                    "diff", "tolerance", "blocking"}


class QualityError(ValueError):
    """G6A-06 严格质量派生失败：畸形/未知状态/根产物不一致。

    调用方归一：candidate bundle 复验 → E-G6A-06-018；发布资格门 →
    E-G6A-06-030。绝不宽松地推导出 FULL。
    """


def _require(cond: bool, message: str) -> None:
    if not cond:
        raise QualityError(f"E-G6A-06-018: {message} —— 失败关闭")


def _exact_keys(obj: dict, allowed, name: str) -> None:
    """精确键集断言：多余或缺失任一字段都失败关闭。"""
    _require(isinstance(obj, dict), f"{name} 非对象")
    _require(set(obj) == set(allowed),
             f"{name} 键集 {sorted(obj)} ≠ 预期 {sorted(allowed)}")


def _nonempty_string_fields(obj: dict, fields, name: str) -> None:
    for key in fields:
        val = obj.get(key)
        _require(isinstance(val, str) and val.strip(),
                 f"{name}.{key} 须为非空字符串")


def _finite_positive_decimal(val, name: str):
    """PASS per-share 值：必须是字符串、可解析为**有限** Decimal 且 > 0。

    NaN/Infinity/负数/非数字 → QualityError 失败关闭，绝不推导出 FULL。
    """
    _require(isinstance(val, str) and val.strip(), f"{name} 非字符串")
    try:
        d = Decimal(val)
    except InvalidOperation:
        raise QualityError(
            f"E-G6A-06-018: {name} 非 Decimal（{val!r}）—— 失败关闭")
    _require(d.is_finite(), f"{name} 非有限数（NaN/Infinity）—— 失败关闭")
    _require(d > 0, f"{name} 必须 > 0（生产不变式）—— 失败关闭")
    return d


def _route_status_item_id(route: str, state: str) -> str:
    return f"OI-G6A06-RC-{route.upper()}-{state}"


def _validate_open_items_product(oi_prod, rs: dict) -> List[dict]:
    """open_items 产物**精确**形状校验（G6A-06 partial-route 硬化）。

      · 顶层键集恰为 {open_items, cross_check, route_statuses}；
      · 每个 OpenItem 对照真实 `OpenItem.to_dict` 合同：精确键集、
        ID/description/owner/due/gate 非空、material 恰为 bool、status 在
        OPEN/CLOSED/SUPERSEDED 支持集、closure evidence 语义（CLOSED 必须
        附证据，非 CLOSED 不得带证据）、重复 ID 拒绝 —— 畸形 material/status
        绝不静默逃离 PARTIAL；
      · 每个声明非 READY 路由必须有其确定性 material OPEN 路由项；READY
        路由不得带路由状态项；
      · cross_check 每条诊断的 open_item_id 必须命中一个 material OPEN 项
        （删/改标签 mismatch 即失败关闭）。
    """
    from open_item_registry import CLOSED as _CLOSED
    from open_item_registry import SUPERSEDED as _SUPERSEDED
    _exact_keys(oi_prod, OPEN_ITEMS_PRODUCT_KEYS, "open_items")
    items = oi_prod.get("open_items")
    _require(isinstance(items, list), "open_items.open_items 非数组")
    item_ids: List[str] = []
    item_by_id: Dict[str, dict] = {}
    for idx, it in enumerate(items):
        _exact_keys(it, OPEN_ITEM_KEYS, f"open_items[{idx}]")
        oid = it["open_item_id"]
        _require(isinstance(oid, str) and oid.strip(),
                 f"open_items[{idx}].open_item_id 非空")
        _require(oid not in item_ids, f"重复 open_item_id {oid!r}")
        item_ids.append(oid)
        item_by_id[oid] = it
        _nonempty_string_fields(
            it, ("description", "owner_role", "due_date", "blocks_gate"),
            f"open_items[{idx}]")
        _require(isinstance(it["material"], bool),
                 f"open_items[{idx}].material 须为 bool"
                 f"（实得 {type(it['material']).__name__}）")
        _require(it["status"] in (OPEN, _CLOSED, _SUPERSEDED),
                 f"open_items[{idx}].status 非法 {it['status']!r}")
        if it["status"] == _CLOSED:
            _require(isinstance(it["closure_evidence"], str)
                     and it["closure_evidence"].strip(),
                     f"CLOSED 项 {oid} 必须附非空 closure_evidence")
        else:
            _require(it["closure_evidence"] is None,
                     f"非 CLOSED 项 {oid} 不得携带 closure_evidence")
    # 路由状态项：非 READY 路由必有确定性项，READY 路由必无。
    for route in VALUATION_ROUTES:
        state = rs[route]
        marker = _route_status_item_id(route, state)
        if state == ROUTE_READY:
            _require(marker not in item_by_id,
                     f"READY 路由 {route} 不得带路由状态项 {marker}")
            _require(not any(oid.startswith(f"OI-G6A06-RC-{route.upper()}-")
                             for oid in item_ids),
                     f"READY 路由 {route} 不得带任何路由状态项")
        else:
            _require(marker in item_by_id,
                     f"非 READY 路由 {route} 缺确定性路由状态项 {marker}")
            it = item_by_id[marker]
            _require(it["material"] is True,
                     f"路由状态项 {marker} 必须 material=true")
            _require(it["status"] == OPEN,
                     f"路由状态项 {marker} 必须 OPEN")
    # cross_check 诊断 ↔ 对应 material OPEN 开放项绑定。
    cc = oi_prod.get("cross_check")
    _require(isinstance(cc, list), "open_items.cross_check 非数组")
    for idx, entry in enumerate(cc):
        _exact_keys(entry, CROSS_CHECK_KEYS, f"cross_check[{idx}]")
        _require(entry["open_item_id"] in item_by_id,
                 f"cross_check[{idx}].open_item_id 无对应开放项")
        it = item_by_id[entry["open_item_id"]]
        _require(it["material"] is True and it["status"] == OPEN,
                 f"cross_check[{idx}] 对应开放项非 material OPEN（删/改标签）")
        _require(entry["scenario"] in SCENARIOS,
                 f"cross_check[{idx}].scenario 非法 {entry['scenario']!r}")
        _nonempty_string_fields(
            entry, ("method_a", "method_b"), f"cross_check[{idx}]")
        _finite_positive_decimal(entry["diff"], f"cross_check[{idx}].diff")
        _finite_positive_decimal(entry["tolerance"], f"cross_check[{idx}].tolerance")
        _require(entry["blocking"] is True,
                 f"cross_check[{idx}].blocking 必须为 true")
    return items


def quality_from_products(products: Dict[str, dict]) -> Tuple[str, bool]:
    """从 canonical 产物**严格**派生候选质量（FULL/PARTIAL）与发布资格。

    只由产物/开放项派生，绝不用调用方输入或请求声明；任一畸形、未知状态、
    错标签、错键集或值域不符都失败关闭（QualityError），**绝不把坏产物当成
    FULL**：

      · 键集必须精确等于生产注册表 PRODUCT_ORDER；
      · 四路估值 + 三情景共 7 个产物必须带 typed status（PASS /
        INPUT_MISSING / NOT_EVALUATED）与**状态专属精确键集**，且 method/
        scenario 必须与产物名一一对应（错方法/错情景换名失败关闭）；
          PASS           → method/scenario + 三个 per-share 数值字段，且每个
                           per-share 值为**有限正 Decimal** 且
                           low ≤ base ≤ high；不得携带非 READY 元数据；
          INPUT_MISSING  → 精确键集、无 per-share、非空 reason + 非空唯一
                           evidence_refs + 非空唯一 missing_inputs；
          NOT_EVALUATED  → 精确键集、无 per-share、非空 reason + 非空唯一
                           evidence_refs、不得带 missing_inputs；
      · calc_ledger / claim_map / emission_map 按**精确生成形状**校验，
        emission_map 与 claim_map 必须一一交叉一致（claim ID/值/假设 ↔
        emission 绑定）—— 换哈希/换正文失败关闭；
      · open_items 按 `_validate_open_items_product` 精确校验；
      · route_statuses 必须与各估值产物及全部三情景产物一致；
      · 任一 material+OPEN 开放项或任一非 READY 估值/情景产物 → PARTIAL。
    """
    if not isinstance(products, dict):
        raise QualityError("E-G6A-06-018: 产物表非 dict —— 失败关闭")
    _require(set(products) == set(PRODUCT_ORDER),
             f"产物键集 {sorted(products)} ≠ 生产注册表 {sorted(PRODUCT_ORDER)}")
    # ── open_items 结构化输出（route_statuses 真源）──
    oi_prod = products.get("open_items")
    _require(isinstance(oi_prod, dict), "open_items 产物非对象")
    rs = oi_prod.get("route_statuses")
    _require(isinstance(rs, dict), "open_items.route_statuses 非对象")
    _require(set(rs) == set(VALUATION_ROUTES),
             f"route_statuses 键集 {sorted(rs)} ≠ 四路 {sorted(VALUATION_ROUTES)}")
    for route, st in rs.items():
        _require(st in ROUTE_STATES,
                 f"route_statuses[{route}] 未知状态 {st!r}")
    items = _validate_open_items_product(oi_prod, rs)
    # ── 四路估值 + 三情景 typed 产物 ──
    non_ready = []
    for name in VALUATION_PRODUCT_NAMES:
        prod = products.get(name)
        _require(isinstance(prod, dict), f"产物 {name} 非对象")
        status = prod.get("status")
        route = VALUATION_PRODUCT_ROUTES[name]
        expected_method, expected_scenario = VALUATION_PRODUCT_METHODS[name]
        _require(prod.get("method") == expected_method,
                 f"产物 {name}.method 须为 {expected_method!r}"
                 f"（实得 {prod.get('method')!r}）—— 失败关闭")
        _require(prod.get("scenario") == expected_scenario,
                 f"产物 {name}.scenario 须为 {expected_scenario!r}"
                 f"（实得 {prod.get('scenario')!r}）—— 失败关闭")
        if status == ROUTE_PRODUCT_PASS:
            _exact_keys(prod, PASS_PRODUCT_KEYS, f"产物 {name}")
            low = _finite_positive_decimal(prod["per_share_low"],
                                           f"{name}.per_share_low")
            base = _finite_positive_decimal(prod["per_share_base"],
                                            f"{name}.per_share_base")
            high = _finite_positive_decimal(prod["per_share_high"],
                                            f"{name}.per_share_high")
            _require(low <= base <= high,
                     f"PASS 产物 {name} per-share 须 low ≤ base ≤ high"
                     f"（{low} ≤ {base} ≤ {high}）—— 失败关闭")
            expected_route_state = ROUTE_READY
        elif status in (ROUTE_INPUT_MISSING, ROUTE_NOT_EVALUATED):
            expected_keys = (INPUT_MISSING_PRODUCT_KEYS if status
                             == ROUTE_INPUT_MISSING
                             else NOT_EVALUATED_PRODUCT_KEYS)
            _exact_keys(prod, expected_keys, f"产物 {name}")
            _nonempty_string_fields(prod, ("reason",), f"产物 {name}")
            refs = prod.get("evidence_refs")
            _require(isinstance(refs, list) and refs
                     and all(isinstance(x, str) and x.strip() for x in refs),
                     f"非 READY 产物 {name} 缺非空 evidence_refs")
            _require(len(refs) == len(set(refs)),
                     f"非 READY 产物 {name} evidence_refs 含重复项")
            if status == ROUTE_INPUT_MISSING:
                mis = prod.get("missing_inputs")
                _require(isinstance(mis, list) and mis
                         and all(isinstance(x, str) and x.strip() for x in mis),
                         f"INPUT_MISSING 产物 {name} 必须带非空 missing_inputs")
                _require(len(mis) == len(set(mis)),
                         f"INPUT_MISSING 产物 {name} missing_inputs 含重复项")
            expected_route_state = status
            non_ready.append(name)
        else:
            raise QualityError(
                f"E-G6A-06-018: 产物 {name}.status 缺失/未知 {status!r}"
                " —— 失败关闭")
        _require(rs[route] == expected_route_state,
                 f"产物 {name}.status={status} 与 route_statuses[{route}]"
                 f"={rs[route]} 不一致 —— 失败关闭")
    # ── 非估值三产物精确形状 + 交叉一致 ──
    calc = products.get("calc_ledger")
    _exact_keys(calc, CALC_LEDGER_KEYS, "calc_ledger")
    formula_count = calc.get("formula_count")
    _require(isinstance(formula_count, int) and not isinstance(formula_count, bool)
             and formula_count >= 0,
             f"calc_ledger.formula_count 须为非负整数（实得 {formula_count!r}）")
    # G6A-06 partial-route 返工：账本只含 READY 路由实际消费的假设。
    #   · 无 READY 路由消费假设 → ledger 必须为空数组；
    #   · 混合/全 READY → metric 序列必须**精确**等于预期假设集（规范顺序），
    #     不允许多/少 —— 调用方提供但未被 READY 路由消费的假设不得出现。
    expected_assumptions = _assumptions_for_statuses(rs)
    expected_metrics = [f"{key}_assumption" for key in expected_assumptions]
    ledger = calc.get("ledger")
    _require(isinstance(ledger, list), "calc_ledger.ledger 非数组")
    if expected_assumptions:
        _require(ledger,
                 "calc_ledger.ledger 非空数组（存在 READY 路由消费假设）")
        _require(
            [e.get("metric") for e in ledger] == expected_metrics,
            f"calc_ledger metric 序列须精确等于预期假设集 "
            f"{expected_metrics}（不允许多/少，含非 READY 路由的无关假设）")
    else:
        _require(not ledger,
                 "无 READY 路由消费假设时 calc_ledger.ledger 必须为空")
    seen_metrics = set()
    for idx, entry in enumerate(ledger):
        _exact_keys(entry, CALC_LEDGER_ENTRY_KEYS, f"calc_ledger.ledger[{idx}]")
        _nonempty_string_fields(
            entry, ("metric", "value", "source"), f"calc_ledger.ledger[{idx}]")
        _require(entry["metric"] not in seen_metrics,
                 f"calc_ledger.ledger[{idx}].metric 重复")
        seen_metrics.add(entry["metric"])
    claims_prod = products.get("claim_map")
    _exact_keys(claims_prod, CLAIM_MAP_KEYS, "claim_map")
    claims = claims_prod.get("claims")
    _require(isinstance(claims, list), "claim_map.claims 非数组")
    if expected_assumptions:
        _require(claims,
                 "claim_map.claims 非空数组（存在 READY 路由消费假设）")
        _require(
            [c.get("assumption") for c in claims] == list(expected_assumptions),
            f"claim_map.claims 假设序列须精确等于预期假设集 "
            f"{list(expected_assumptions)}（不允许多/少，含非 READY 路由的"
            "无关假设）")
    else:
        _require(not claims, "无 READY 路由消费假设时 claim_map.claims 必须为空")
    claim_ids: List[str] = []
    for idx, c in enumerate(claims):
        _exact_keys(c, CLAIM_ENTRY_KEYS, f"claim_map.claims[{idx}]")
        _nonempty_string_fields(c, ("id", "text", "assumption", "value"),
                                f"claim_map.claims[{idx}]")
        _require(c["id"] not in claim_ids, f"claim_map.claims[{idx}].id 重复")
        claim_ids.append(c["id"])
    emissions_prod = products.get("emission_map")
    _exact_keys(emissions_prod, EMISSION_MAP_KEYS, "emission_map")
    emissions = emissions_prod.get("emissions")
    _require(isinstance(emissions, list), "emission_map.emissions 非数组")
    if expected_assumptions:
        _require(emissions,
                 "emission_map.emissions 非空数组（存在 READY 路由消费假设）")
    else:
        _require(not emissions,
                 "无 READY 路由消费假设时 emission_map.emissions 必须为空")
    _require(len(emissions) == len(claims),
             "emission_map 与 claim_map 条目数不一致（交叉换配失败关闭）")
    for idx, e in enumerate(emissions):
        _exact_keys(e, EMISSION_ENTRY_KEYS, f"emission_map.emissions[{idx}]")
        _nonempty_string_fields(
            e, ("visible_span", "claim_node", "rendered_value", "assumption"),
            f"emission_map.emissions[{idx}]")
        c = claims[idx]
        _require(e["claim_node"] == c["id"],
                 f"emission_map.emissions[{idx}].claim_node ≠ claim_map"
                 f"（{e['claim_node']!r} vs {c['id']!r}）—— 交叉不一致")
        _require(e["visible_span"] == f"span:{c['id']}",
                 f"emission_map.emissions[{idx}].visible_span 与 claim id 不符")
        _require(e["rendered_value"] == c["value"],
                 f"emission_map.emissions[{idx}].rendered_value ≠ claim value")
        _require(e["assumption"] == c["assumption"],
                 f"emission_map.emissions[{idx}].assumption ≠ claim assumption")
    material_open = [it for it in items
                     if it.get("material") is True and it.get("status") == OPEN]
    if material_open or non_ready:
        return QUALITY_PARTIAL, False
    return QUALITY_FULL, True


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


# ════════════════════════════════════════════════════════════════
# OI-PF-196：规范冻结输入载荷与哈希（candidate 绑定全部冻结输入）
# ════════════════════════════════════════════════════════════════

def _dict_field(value, name: str) -> None:
    """顶层冻结输入字段形态校验（OI-PF-196 失败关闭）。"""
    if not isinstance(value, dict):
        raise RecomputeError(
            f"E-G6A-05-003: {name} 字段形态不符（须为 dict，实得 "
            f"{type(value).__name__}）—— 失败关闭")


def _statuses_field(vi: ValuationInputs) -> Dict[str, str]:
    """OI-PF-199：ValuationInputs.statuses 是 frozen_inputs_payload **显式展开
    的嵌套结构** —— 形态校验失败关闭。

    非 dict（含 statuses=None，实得 NoneType）→ RecomputeError E-G6A-05-003，
    不得让 `dict(vi.statuses)` 泄漏裸 TypeError；也不得用 str/repr 吞掉非法值。
    """
    s = vi.statuses
    if not isinstance(s, dict):
        raise RecomputeError(
            f"E-G6A-05-003: valuation_inputs.statuses 字段形态不符（须为 dict，"
            f"实得 {type(s).__name__}）—— 失败关闭")
    return dict(s)


def frozen_inputs_payload(ctx: ResearchContext) -> dict:
    """OI-PF-196：规范冻结输入载荷 —— **唯一实现**、固定 key、稳定排序。

    完整覆盖 ResearchContext 全部顶层冻结输入：
      · contract / facts / macro / formula_specs / assumption_defaults
      · valuation_inputs 全部 dataclass 字段（scope/currency/as_of/price/
        shares_outstanding/net_debt/minority_interest/industry_commodity/
        statuses）
      · approved 快照的不可变身份（snapshot_id/version）与 sha256
      · open_items_policy 全部 dataclass 字段
        （tolerance/owner_role/due_date/blocks_gate）
      · valuation_routes 四路估值声明（state/reason/evidence_refs/missing_inputs；
        legacy 上下文缺省 = 全 READY 的确定性展开）

    缺 policy、字段形态不符 → RecomputeError E-G6A-05-003 失败关闭；
    JSON 不可规范序列化的对象在 canonical 序列化时同样失败关闭（见
    `frozen_inputs_hash`），**不用 str/repr 悄悄吞掉**。

    OI-PF-199：显式展开的嵌套结构（如 valuation_inputs.statuses）须校验形态，
    非 dict（含 None）→ RecomputeError E-G6A-05-003（`_statuses_field`），
    不得泄漏裸 TypeError。
    """
    _dict_field(ctx.contract, "contract")
    _dict_field(ctx.facts, "facts")
    _dict_field(ctx.macro, "macro")
    _dict_field(ctx.formula_specs, "formula_specs")
    _dict_field(ctx.assumption_defaults, "assumption_defaults")
    if not isinstance(ctx.valuation_inputs, ValuationInputs):
        raise RecomputeError(
            f"E-G6A-05-003: valuation_inputs 字段形态不符（须为 "
            f"ValuationInputs，实得 {type(ctx.valuation_inputs).__name__}）"
            f"—— 失败关闭")
    if not isinstance(ctx.approved, AssumptionSnapshot):
        raise RecomputeError(
            f"E-G6A-05-003: approved 字段形态不符（须为 AssumptionSnapshot，"
            f"实得 {type(ctx.approved).__name__}）—— 失败关闭")
    p = ctx.open_items_policy
    if p is None:
        raise RecomputeError(
            "E-G6A-05-003: 冻结输入缺 open_items_policy —— 失败关闭，"
            "不默认补值")
    if not isinstance(p, OpenItemsPolicy):
        raise RecomputeError(
            f"E-G6A-05-003: open_items_policy 字段形态不符（须为 "
            f"OpenItemsPolicy，实得 {type(p).__name__}）—— 失败关闭")
    try:
        approved_sha = ctx.approved.sha256
    except Exception as exc:
        raise RecomputeError(
            f"E-G6A-05-003: 批准快照不可用（OI-PF-196 失败关闭）—— {exc!r}")
    vi = ctx.valuation_inputs
    return {
        "contract": ctx.contract,
        "facts": ctx.facts,
        "macro": ctx.macro,
        "formula_specs": ctx.formula_specs,
        "valuation_inputs": {
            "scope": vi.scope,
            "currency": vi.currency,
            "as_of": vi.as_of,
            "price": vi.price,
            "shares_outstanding": vi.shares_outstanding,
            "net_debt": vi.net_debt,
            "minority_interest": vi.minority_interest,
            "industry_commodity": vi.industry_commodity,
            "statuses": _statuses_field(vi),
        },
        "assumption_defaults": ctx.assumption_defaults,
        "approved": {
            "snapshot_id": ctx.approved.snapshot_id,
            "version": ctx.approved.version,
            "sha256": approved_sha,
        },
        "open_items_policy": {
            "tolerance": p.tolerance,
            "owner_role": p.owner_role,
            "due_date": p.due_date,
            "blocks_gate": p.blocks_gate,
        },
        "valuation_routes": {
            r: _declared_routes(ctx)[r].to_dict() for r in VALUATION_ROUTES
        },
    }


def frozen_inputs_hash(ctx: ResearchContext) -> str:
    """OI-PF-196：规范冻结输入载荷的 64 位 sha256 —— **唯一哈希实现**。

    与回算产物输出无关：任何顶层冻结输入变化（含不被任何产物读取的字段：
    macro / valuation_inputs.statuses / currency / policy 等）都改变本哈希，
    进而改变 candidate_id。

    JSON 不可规范序列化（set/Decimal/dataclass 实例等）→ RecomputeError
    E-G6A-05-003 失败关闭，不得生成 candidate；不用 str/repr 吞掉。
    """
    payload = frozen_inputs_payload(ctx)
    try:
        data = canonical_bytes(payload)
    except (TypeError, ValueError) as exc:
        raise RecomputeError(
            f"E-G6A-05-003: 冻结输入不可规范序列化（OI-PF-196 失败关闭）"
            f"—— {type(exc).__name__}: {exc}")
    return hashlib.sha256(data).hexdigest()


def _frozen_approved_sha256(ctx: ResearchContext) -> str:
    """冻结 candidate 前取已批准快照哈希（OI-PF-199 防御纵深）。

    frozen_inputs_hash 已先对 sha256 做完整性重算并失败关闭；此处再读一次
    同样走 `AssumptionSnapshot.sha256` 的正文重算路径 —— 任何在两次读取
    之间的正文漂移都转 RecomputeError E-G6A-05-003，绝不带漂移正文入库。
    """
    try:
        return ctx.approved.sha256
    except Exception as exc:
        raise RecomputeError(
            f"E-G6A-05-003: 批准快照不可用（OI-PF-199 冻结前校验失败关闭）"
            f"—— {exc!r}")


def _validate_registry() -> None:
    """OI-PF-195：全量回算前**失败关闭**校验注册表一致性。

    三个注册表（PRODUCT_ORDER / PRODUCT_DEPS / GENERATORS）必须互为真源：
      · PRODUCT_ORDER 无重复
      · set(PRODUCT_ORDER) == set(PRODUCT_DEPS) == set(GENERATORS)

    只做单向检查（order 中每项都有生成器）抓不住三类漂移：
      · GENERATORS 多出未登记项 —— 生成器在跑但产物不进 order，静默遗漏
        （原失败载荷：GENERATORS["unregistered_probe"] 后 len=12 却只算 11）
      · GENERATORS 缺已登记项 —— order 里的产物没有生成器
      · PRODUCT_DEPS 与 PRODUCT_ORDER 漂移 —— 落库依赖与执行顺序脱节
    报错逐方向列差集/重复，不得只报笼统「不一致」。
    """
    problems: List[str] = []
    order = list(PRODUCT_ORDER)
    seen = set()
    dupes = sorted({n for n in order if n in seen or seen.add(n)})
    if dupes:
        problems.append(f"PRODUCT_ORDER 重复项: {dupes}")
    s_order = set(order)
    s_deps = set(PRODUCT_DEPS)
    s_gen = set(GENERATORS)
    if s_order - s_deps:
        problems.append(f"PRODUCT_ORDER 有而 PRODUCT_DEPS 无: "
                        f"{sorted(s_order - s_deps)}")
    if s_deps - s_order:
        problems.append(f"PRODUCT_DEPS 有而 PRODUCT_ORDER 无: "
                        f"{sorted(s_deps - s_order)}")
    if s_order - s_gen:
        problems.append(f"PRODUCT_ORDER 有而 GENERATORS 无: "
                        f"{sorted(s_order - s_gen)}")
    if s_gen - s_order:
        problems.append(f"GENERATORS 有而 PRODUCT_ORDER 无: "
                        f"{sorted(s_gen - s_order)}")
    if s_deps - s_gen:
        problems.append(f"PRODUCT_DEPS 有而 GENERATORS 无: "
                        f"{sorted(s_deps - s_gen)}")
    if s_gen - s_deps:
        problems.append(f"GENERATORS 有而 PRODUCT_DEPS 无: "
                        f"{sorted(s_gen - s_deps)}")
    if problems:
        raise ProductMissing(
            f"E-G6A-05-001: 全量回算注册表不一致（OI-PF-195 失败关闭）—— "
            f"{'；'.join(problems)}")


@dataclass
class RecomputeResult:
    products: Dict[str, dict] = field(default_factory=dict)
    shas: Dict[str, str] = field(default_factory=dict)
    frozen_inputs_hash: Optional[str] = None

    def product_ids(self) -> Tuple[str, ...]:
        return tuple(sorted(self.products))


def recompute_all(ctx: ResearchContext) -> RecomputeResult:
    """全量回算：注册表内每个产物都重新生成（F-3）。

    OI-PF-195：执行前先失败关闭校验三个注册表一致性（_validate_registry），
    任一方向漂移（order 重复 / deps 漂移 / generators 多一项或少一项）
    都拒绝回算 —— 不允许「静默少算一个产物」或「生成器跑空转」。

    变异注入：把 GENERATORS/PRODUCT_DEPS 里的某一项摘掉，本函数在
    **执行前**失败关闭抛 ProductMissing（E-G6A-05-001）—— 不是产出
    少一个产物，而是根本不产出；测试断言抛错且报错点名差集方向与摘掉的项。

    OI-PF-200：RecomputeResult 绑定**产生它的上下文**的规范冻结输入哈希
    （frozen_inputs_hash）—— 生成产物前先取一次规范哈希（缺失/形态不符的
    冻结输入在此已失败关闭 E-G6A-05-003），全部产物生成完毕后**再取一次**并
    比对：任何生成期间对冻结输入的原地篡改（上下文漂移）都转 RecomputeError
    E-G6A-05-004 失败关闭，绝不把漂移后的结果绑定给候选。
    """
    _validate_registry()
    _resolve_open_items_policy(ctx)
    before = frozen_inputs_hash(ctx)
    v = ctx.values()
    # G6A-06 partial-route 返工：READY 路由所需假设必须在产物生成前以非空
    # 字符串存在（E-G6A-06-020 失败关闭）；非 READY 路由不要求任何假设。
    _validate_ready_route_assumptions(ctx, v)
    res = RecomputeResult()
    for name in PRODUCT_ORDER:
        prod = GENERATORS[name](ctx, v)
        res.products[name] = prod
        res.shas[name] = _prod_sha(prod)
    after = frozen_inputs_hash(ctx)
    if before != after:
        raise RecomputeError(
            f"E-G6A-05-004: 回算期间冻结输入上下文漂移（生成前 {before} "
            f"≠ 生成后 {after}）—— 失败关闭，不产出候选绑定")
    res.frozen_inputs_hash = before
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


def _validate_recompute_binding(recompute: RecomputeResult,
                                canonical: RecomputeResult) -> None:
    """OI-PF-200：冻结前把调用方回算结果与**独立重算**的规范结果逐项比对。

    绑定字段（frozen_inputs_hash）、products、shas 都由调用方提供、皆可被
    改写，单独任何一项都不能证明结果来自当前上下文 —— 唯一权威是**从当前
    ResearchContext 独立重算**得到的 canonical 结果：

    ① 绑定哈希：recompute 的绑定哈希必须逐字等于独立重算的规范哈希 ——
       「未绑定/手工构造」「绑定字段缺省」「改绑为当前上下文哈希但产物来自
       其他上下文（陈旧结果）」一律拒绝 E-G6A-05-005，不存储候选；
    ② 键集：products/shas 键集必须精确等于生产注册表（= canonical 键集）
       —— 多出/缺失/漂移都拒 E-G6A-05-006；
    ③ 产物哈希：每个记录哈希必须等于独立重算的对应产物规范哈希 ——
       「产物+记录哈希同步篡改（自洽但非规范值）」与「陈旧产物」都在此被拒
       E-G6A-05-006；
    ④ 原地篡改：每个记录哈希还必须等于其传入产物自身的规范哈希 ——
       只改产物不改哈希、或只改哈希不改产物都转 E-G6A-05-006。

    不加兼容路径：任一项不符即拒绝，没有可接受的「旧形态」。
    """
    bound = recompute.frozen_inputs_hash
    if bound != canonical.frozen_inputs_hash:
        raise RecomputeError(
            f"E-G6A-05-005: 回算结果绑定哈希 {bound!r} 与当前上下文独立重算"
            f"规范哈希 {canonical.frozen_inputs_hash} 不一致（回算结果来自"
            f"不同上下文或改绑字段）—— 拒绝存储候选")
    keys = set(PRODUCT_ORDER)
    if set(recompute.products) != keys or set(recompute.shas) != keys:
        raise RecomputeError(
            "E-G6A-05-006: 回算结果键集与生产注册表不符（产物/哈希多出或缺失）"
            "—— 拒绝存储候选")
    for name in PRODUCT_ORDER:
        recorded = recompute.shas[name]
        if recorded != canonical.shas[name]:
            raise RecomputeError(
                f"E-G6A-05-006: 记录哈希 {recorded} ≠ 独立重算规范哈希 "
                f"{canonical.shas[name]}（陈旧产物或产物+记录哈希同步篡改："
                f"{name}）—— 拒绝存储候选")
        if recorded != _prod_sha(recompute.products[name]):
            raise RecomputeError(
                f"E-G6A-05-006: 记录哈希 {recorded} ≠ 产物规范哈希 "
                f"{_prod_sha(recompute.products[name])}（产物或哈希被原地"
                f"篡改：{name}）—— 拒绝存储候选")


def _assert_write_boundary(ctx: ResearchContext,
                           canonical: RecomputeResult) -> None:
    """OI-PF-200（返工）：写入边界最终一致性校验。

    canonical 独立重算返回后、ArtifactStore.store 之前，重算**当前上下文**的
    规范冻结输入哈希并要求逐字等于 canonical 绑定哈希 —— 任何在 canonical
    返回后对冻结输入的原地篡改（候选直接读取上下文的字段 contract/scope/
    as_of/approved_snapshot 全部取自顶层冻结输入，由本哈希覆盖）都转
    RecomputeError E-G6A-05-007 失败关闭，**零 candidate 写入**，绝不把
    「canonical 产物 + 漂移后的候选字段」的混合候选落库。
    """
    now = frozen_inputs_hash(ctx)
    if now != canonical.frozen_inputs_hash:
        raise RecomputeError(
            f"E-G6A-05-007: 写入前当前上下文冻结输入哈希 {now} ≠ 独立重算"
            f"规范哈希 {canonical.frozen_inputs_hash}（canonical 返回后上下文"
            f"漂移）—— 失败关闭，不写入候选")


def freeze_candidate_from_recompute(store: ArtifactStore, ctx: ResearchContext,
                                    run_id: str,
                                    recompute: RecomputeResult) -> CandidateFreeze:
    """按回算产物组装候选并内容寻址冻结（candidate hash = 内容哈希）。

    OI-PF-196：候选绑定规范冻结输入哈希（frozen_inputs_hash）—— 全部顶层
    冻结输入任一变化（含不被任何产物读取的字段）都改变哈希与候选身份，
    不依赖 run_id 或产物输出。缺 policy / 字段形态不符 / JSON 不可规范
    序列化 → RecomputeError E-G6A-05-003 失败关闭，**不生成 candidate**。

    OI-PF-199：冻结 candidate 前必须经过正文完整性校验 —— frozen_inputs_hash
    读取 approved.sha256 时重算正文哈希，直接篡改 snap.approved 的载荷在此
    转 RecomputeError E-G6A-05-003，绝不静默接受漂移正文、绝不为已篡改批准
    内容生成 candidate。

    OI-PF-200：冻结前**独立重算**当前 ResearchContext 的规范结果（canonical），
    并把调用方传入的 recompute 逐项与之比对 —— 绑定哈希不一致 E-G6A-05-005、
    键集漂移或产物/哈希与独立重算不符 E-G6A-05-006，一律失败关闭且**不存储
    candidate**；candidate 的 products/product_hashes/frozen_inputs_hash 由
    独立重算结果组装，调用方提供的绑定字段/产物/哈希不作为权威来源。

    OI-PF-200（返工）：写入边界 —— 存储前经 `_assert_write_boundary` 重算当前
    上下文规范冻结输入哈希并要求等于 canonical 绑定哈希；canonical 返回后任何
    上下文漂移转 RecomputeError E-G6A-05-007 失败关闭，零 candidate 写入。

    入口先做形态校验（frozen_inputs_payload，缺 policy/字段形态不符 → 立即
    E-G6A-05-003 失败关闭，不生成 candidate）；该校验结果不携带进候选 ——
    候选的 frozen_inputs_hash 与 products/product_hashes 唯一来源是下方独立
    重算的 canonical。
    """
    frozen_inputs_payload(ctx)
    canonical = recompute_all(ctx)
    _validate_recompute_binding(recompute, canonical)
    quality_status, release_eligible = quality_from_products(canonical.products)
    candidate = {
        "schema_version": "1.0.0",
        "kind": CANDIDATE_KIND,
        "run_id": run_id,
        "contract": ctx.contract.get("contract_id"),
        "scope": ctx.valuation_inputs.scope,
        "as_of": ctx.valuation_inputs.as_of,
        "products": canonical.product_ids(),
        "product_hashes": canonical.shas,
        "approved_snapshot": _frozen_approved_sha256(ctx),
        "frozen_inputs_hash": canonical.frozen_inputs_hash,
        "quality_status": quality_status,
        "release_eligible": release_eligible,
    }
    data = canonical_bytes(candidate)
    _assert_write_boundary(ctx, canonical)
    store.store(CANDIDATE_KIND, data)
    cid = hashlib.sha256(data).hexdigest()
    return CandidateFreeze(candidate_id=cid, candidate=candidate,
                           recompute=canonical)


def _load_candidate_object(store: ArtifactStore, candidate_id: str,
                           label: str) -> dict:
    """OI-PF-204：失效前校验 candidate 完整（JSON object + kind=candidate +
    内容摘要匹配）。`store.load` 本身已强制内容哈希 = 摘要（E-G2-02-005）；
    任何缺失、内容损坏、非 JSON、非 JSON 对象、body.kind ≠ candidate 都转
    RecomputeError E-G6A-05-002 失败关闭 —— 不得只查「旧摘要路径存在」。
    """
    try:
        raw = store.load(candidate_id)
    except (TypeError, ValueError) as exc:
        raise RecomputeError(
            f"E-G6A-05-002: {label} candidate 不可达或内容损坏: "
            f"{str(candidate_id)[:12]}…（{exc}）")
    try:
        obj = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise RecomputeError(
            f"E-G6A-05-002: {label} candidate 非合法 JSON: "
            f"{candidate_id[:12]}…（{type(exc).__name__}）")
    if not isinstance(obj, dict):
        raise RecomputeError(
            f"E-G6A-05-002: {label} candidate 非 JSON 对象: "
            f"{candidate_id[:12]}…（{type(obj).__name__}）")
    if obj.get("kind") != CANDIDATE_KIND:
        raise RecomputeError(
            f"E-G6A-05-002: {label} candidate body.kind ≠ {CANDIDATE_KIND!r}"
            f"（实得 {obj.get('kind')!r}）: {candidate_id[:12]}…")
    return obj


def invalidate_previous(store: ArtifactStore, repo, old_candidate_id: str,
                        new_candidate_id: str, reason: str, *, writer: str,
                        invalidated_at: Optional[datetime] = None) -> str:
    """旧 candidate 失效并保留（OI-PF-204 权威化）。

    失效事实**同时**落两处：
      · 不可变审计证据 —— ArtifactStore kind=candidate_invalidation 内容寻址
        冻结，id = 内容哈希，永不改写（旧对象仍可读，保留）。
      · 权威查询面 —— candidate_invalidation 表按 old_candidate_id 唯一：
        重复相同失效幂等返回既有证据 id；冲突 new/reason 拒绝
        E-G6A-05-008，不得静默覆盖。

    写失效前必须 `store.load()` 并验证 old/new 两端都是完整 candidate 对象
    （JSON object、kind="candidate"、内容摘要匹配）—— 缺失、内容损坏、
    其他 kind、new 不存在均稳定失败关闭 E-G6A-05-002（原实现只检查旧摘要
    路径存在，旧对象可内容损坏或不是 candidate、新 candidate 可不存在）。

    `repo` 为 Repository（事务/写权边界）：幂等/冲突预检经其会话查询，
    权威查询面写入经 `Repository.record_candidate_invalidation`（唯一写点，
    事务 + assert_writer + 并发唯一约束兜底）。`writer` 必填关键字参数
    （OI-PF-184：无合法缺省）。
    """
    from repository import CandidateInvalidation
    old = _load_candidate_object(store, old_candidate_id, "旧")
    new = _load_candidate_object(store, new_candidate_id, "新")
    if old_candidate_id == new_candidate_id:
        raise RecomputeError(
            "E-G6A-05-008: old/new candidate 相同 —— 自失效不能表示后继候选")
    candidates_frozen = isinstance(old, dict) and isinstance(new, dict)
    if not isinstance(reason, str) or not reason.strip():
        raise RecomputeError(
            "E-G6A-05-008: 失效 reason 缺失/非字符串 —— 不得静默覆盖")
    session = repo.session()
    try:
        existing = session.query(CandidateInvalidation).filter_by(
            old_candidate_id=old_candidate_id).first()
        if existing is not None:
            if (existing.new_candidate_id == new_candidate_id
                    and existing.reason == reason):
                # 幂等：审计证据仍在（不可变，读时哈希校验为兜底）
                try:
                    store.load(existing.id)
                except ValueError as exc:
                    raise RecomputeError(
                        f"E-G6A-05-002: 失效审计证据缺失/损坏（幂等路径）: "
                        f"{existing.id[:12]}…（{exc}）")
                return existing.id
            raise RecomputeError(
                f"E-G6A-05-008: 冲突失效 —— {old_candidate_id[:12]}… 已失效指向 "
                f"{existing.new_candidate_id[:12]}…，重复请求指向 "
                f"{new_candidate_id[:12]}…（new/reason 冲突）不得静默覆盖")
        inv = {
            "schema_version": "1.0.0",
            "old_candidate_id": old_candidate_id,
            "new_candidate_id": new_candidate_id,
            "reason": reason,
            "status": "INVALIDATED",
        }
        data = canonical_bytes(inv)
        inv_id = store.store(INVALIDATION_KIND, data)   # 不可变审计证据
        return repo.record_candidate_invalidation(
            session, old_candidate_id=old_candidate_id,
            new_candidate_id=new_candidate_id, reason=reason,
            invalidation_id=inv_id, writer=writer,
            candidates_frozen=candidates_frozen,
            invalidated_at=invalidated_at)
    finally:
        session.close()
