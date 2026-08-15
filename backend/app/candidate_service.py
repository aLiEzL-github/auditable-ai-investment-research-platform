"""candidate_service.py —— G6A-06 权威最终候选冻结服务（OI-PF-203）。

缺陷（OI-PF-203）：
  · G6A-06 没有权威最终 candidate hash —— `recompute.freeze_candidate_from_recompute`
    仅被测试调用，冻结时只存 candidate 摘要；11 项 product hash 的**正文不落库**，
    从 ArtifactStore + candidate id 出发无法加载正文、无法独立复验。
  · candidate 不绑定代码 commit/tree，无法回答「这份候选由哪一段代码生成」。
  · 临时测试 candidate 不得冒充最终候选。

本模块提供**非测试专用**的最终候选冻结入口（app 层服务）：
  1. `CandidateFreezeService.freeze_final_candidate` —— 权威冻结。保留 OI-PF-200
     的独立 canonical 重算 + 写入前漂移边界：调用方传入的回算结果**不是权威**，
     逐项与当前上下文独立重算比对后才允许冻结；冻结前重算当前上下文规范冻结
     输入哈希并要求等于 canonical 绑定哈希（E-G6A-05-007）。冻结时把 canonical
     的 11 项产品正文**逐项内容寻址写入 ArtifactStore**，实际 digest 必须逐字
     等于 `RecomputeResult.shas`；失败绝不写 candidate（产品可作孤儿，但不得
     被当成完整 bundle）。
  2. G6A-06 request 绑定/partial-route 硬化：`request_payload` 为必填关键字
     参数 —— 不允许无受管请求依赖的 canonical 最终候选。冻结前把请求**严格规范
     为单一 immutable 字节映像**（allow_nan=False，NaN/Infinity 一律拒绝），把
     该映像解析回普通对象验证并重放 `recompute_all`，证明 run_id /
     frozen_inputs_hash / 批准快照哈希 / contract / scope / as_of / 逐项产物
     正文与哈希与待冻结上下文精确一致，再把这些字节原样落库为独立不可变对象，
     digest 写入 candidate.request_hash —— 调用方随后改动原载荷无法改变已存
     请求字节（TOCTOU 关闭）；请求必须声明期望 source_commit/source_tree 并与
     显式干净 checkout 逐字一致。
  3. candidate 明确含 `kind="candidate"` + 冻结 source commit / source tree；
     revision 由调用方**显式提供**并做严格 40 位小写十六进制校验，领域函数
     不隐式猜本地 git HEAD；source commit/tree 参与候选内容寻址身份。
  4. `load_candidate_bundle` / `verify_candidate_bundle` —— 从 ArtifactStore +
     candidate id 加载 candidate 与全部产品正文，并要求 candidate 的代码版本
     与调用方期望版本逐字一致；缺失、篡改、错键集或错代码版本稳定失败关闭；
     复验还重载 request_hash 请求、重放 final_candidate_request→recompute_all
     并核对与候选逐字一致（E-G6A-06-018）。
  5. `freeze_final_candidate_from_payload` —— 受管 JSON 输入到 ResearchContext
     的生产服务边界；批准快照只可由 proposal + decision 重建，不接受调用方
     直接塞入 approved 正文。本入口只写 ArtifactStore，不写任何发布表。

真实外部触发点位于 `backend/tools/final_candidate.py`。该本地 CLI 从当前干净
Git checkout 读取真实 commit/tree 后调用本模块；不会用任意 40 位字符串冒充
实际执行代码版本，也不把通用队列的 `claim_next()` 错当成定向作业领取。
"""
import copy
import hashlib
import json
import re
from dataclasses import dataclass
from typing import Dict, Optional, Tuple

from artifact_store import ArtifactStore
from assumption_snapshot import (
    AssumptionError,
    AssumptionProposal,
    AssumptionRegistry,
    AssumptionSnapshot,
)
from publish_engine import canonical_bytes
from recompute import (
    CANDIDATE_KIND,
    PRODUCT_ORDER,
    QUALITY_PARTIAL,
    ROUTE_FACT_KEYS,
    ROUTE_INPUT_MISSING,
    ROUTE_NOT_EVALUATED,
    ROUTE_READY,
    ROUTE_STATES,
    VALUATION_ROUTES,
    CandidateFreeze,
    OpenItemsPolicy,
    QualityError,
    RecomputeError,
    RecomputeResult,
    ResearchContext,
    RouteDeclaration,
    ValuationRoutes,
    _assert_write_boundary,
    _frozen_approved_sha256,
    _prod_sha,
    _validate_recompute_binding,
    frozen_inputs_payload,
    quality_from_products,
    recompute_all,
)
from schema_validate import SchemaError, validate_object
from valuation_engine import ValuationInputs

# 产品正文在对象库中的写入名（受 ArtifactStore NAME_RE 约束；digest 由正文决定）
PRODUCT_KIND = "recompute_product"

# 受管最终候选请求在对象库中的写入名 —— 冻结时把**校验后的 canonical 请求
# 字节**作为独立不可变对象落库，candidate.request_hash 锚定它（G6A-06 请求
# 绑定/partial-route 硬化：最终候选必须绑定产生它的受管请求，发布侧可重载
# 重放，不允许无依赖候选）。
FINAL_CANDIDATE_REQUEST_KIND = "final_candidate_request"

# 严格 40 位小写十六进制（git commit/tree SHA-1 形态）
SOURCE_REVISION_RE = re.compile(r"^[0-9a-f]{40}$")


class CandidateVerificationError(ValueError):
    """候选 bundle 复验失败 —— 任何缺失、篡改、错键集或错 revision 稳定失败关闭。"""


class CandidateRequestError(RecomputeError):
    """最终候选受管输入非法；不得以宽松默认值拼出 ResearchContext。"""


def _reject_json_constant(token: str) -> None:
    """json.loads 的 parse_constant —— 非标准 JSON 常量（NaN/Infinity/-Infinity）
    一律失败关闭，不得静默转成 float 进入后续验证/落库。"""
    raise ValueError(f"非标准 JSON 常量 {token!r}")


def _strict_json_object(data: bytes, what: str, code: str) -> dict:
    """把存储字节解析为普通 JSON 对象，**严格拒绝非标准 JSON 常量**。

    任意非 UTF-8 / JSON 解析失败 / NaN/Infinity 常量 / 非 object 根 → 用
    `code` 归一失败关闭（复验路径 E-G6A-06-018 / 发布门 E-G6A-06-030）。
    """
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise CandidateVerificationError(
            f"{code}: {what} 非 UTF-8 —— 失败关闭") from exc
    try:
        obj = json.loads(text, parse_constant=_reject_json_constant)
    except ValueError as exc:
        raise CandidateVerificationError(
            f"{code}: {what} 非标准 JSON（含 NaN/Infinity 常量或解析失败："
            f"{exc}）—— 失败关闭") from exc
    if not isinstance(obj, dict):
        raise CandidateVerificationError(
            f"{code}: {what} 非 JSON object —— 失败关闭")
    return obj


def _canonicalize_request(payload: Optional[dict]) -> Tuple[bytes, dict]:
    """把调用方可变 `request_payload` 规范化为**单一 immutable 字节映像**。

    · 严格 JSON：`allow_nan=False` —— 任意 NaN/Infinity 都在写入前以
      E-G6A-06-002 失败关闭，绝不落库非标准 JSON 字节；
    · 把该字节映像解析回普通对象返回 —— 冻结只信任这份映像并重放它，**不
      二次读取调用方可变对象**：调用方在冻结期间/之后改动原载荷都改变不了
      已存请求字节（TOCTOU 关闭）；也不会出现「校验一个状态、稍后序列化另
      一个状态」的第二份读取。
    """
    if not isinstance(payload, dict):
        raise RecomputeError(
            "E-G6A-06-002: 最终候选冻结缺受管请求绑定（request_payload 须为 "
            "JSON object）—— 不允许无依赖的 canonical 最终候选（失败关闭）")
    try:
        data = json.dumps(payload, sort_keys=True, ensure_ascii=False,
                          separators=(",", ":"), allow_nan=False).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise RecomputeError(
            "E-G6A-06-002: 受管请求含非有限数（NaN/Infinity）或非 JSON 值"
            f"（{type(exc).__name__}）—— 失败关闭，零写入") from exc
    try:
        obj = json.loads(data.decode("utf-8"),
                         parse_constant=_reject_json_constant)
    except (UnicodeDecodeError, ValueError) as exc:
        raise RecomputeError(
            "E-G6A-06-002: 受管请求无法解析（非标准 JSON 常量）—— 失败关闭，"
            "零写入") from exc
    if not isinstance(obj, dict):
        raise RecomputeError(
            "E-G6A-06-002: 受管请求根须为 JSON object —— 失败关闭，零写入")
    return data, obj


def validate_source_revision(commit: str, tree: str) -> None:
    """source commit/tree 严格校验：必须为 40 位小写十六进制。

    由调用方显式提供，领域函数不得隐式猜本地 git HEAD（OI-PF-203）。
    """
    for label, val in (("source_commit", commit), ("source_tree", tree)):
        if not isinstance(val, str) or not SOURCE_REVISION_RE.fullmatch(val):
            raise RecomputeError(
                f"E-G6A-06-001: {label} 须为严格 40 位小写十六进制"
                f"（实得 {val!r}）—— 失败关闭")


@dataclass(frozen=True)
class CandidateBundle:
    """可复验的最终候选 bundle：candidate + 全部产品正文（内容寻址加载）。

    request_hash 为冻结时绑定的受管请求对象 digest（可重载重放）。
    """
    candidate_id: str
    candidate: dict
    products: Dict[str, dict]
    product_hashes: Dict[str, str]
    request_hash: Optional[str] = None


@dataclass(frozen=True)
class FinalCandidateRequest:
    """已验证的最终候选请求。

    source_commit/source_tree 为请求声明的**期望代码版本**（严格 40 位小写十六
    进制）—— 冻结时须与显式干净 checkout 提供的版本逐字一致，候选根取同一
    值；复验时绑定请求的 source revision 必须与候选 source revision 一致。
    """
    run_id: str
    context: ResearchContext
    source_commit: str
    source_tree: str


def _object(value, label: str, required, optional=()) -> dict:
    """默认拒绝的 JSON object 形态校验。"""
    if not isinstance(value, dict):
        raise CandidateRequestError(
            f"E-G6A-06-020: {label} 须为 JSON object，实得 "
            f"{type(value).__name__}")
    allowed = set(required) | set(optional)
    missing = sorted(set(required) - set(value))
    unknown = sorted(set(value) - allowed)
    if missing or unknown:
        raise CandidateRequestError(
            f"E-G6A-06-020: {label} 字段不符（missing={missing}, "
            f"unknown={unknown}）—— 默认拒绝")
    return value


def _mapping(value, label: str) -> dict:
    """校验自由键映射；字段集合由其上层领域对象定义。"""
    if not isinstance(value, dict):
        raise CandidateRequestError(
            f"E-G6A-06-020: {label} 须为 JSON object，实得 "
            f"{type(value).__name__}")
    return value


def _nonempty_string(value, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise CandidateRequestError(
            f"E-G6A-06-020: {label} 须为非空字符串")
    return value


def final_candidate_request(payload: dict) -> FinalCandidateRequest:
    """把受管 JSON 请求重建为 ResearchContext。

    approved snapshot 不接受已展平正文；调用方必须给 proposal 与人工 decision，
    再复用 AssumptionRegistry/AssumptionSnapshot 的批准权、payload 绑定和
    OI-PF-206 冲突键失败关闭逻辑构建。
    """
    try:
        validate_object("final_candidate_request", payload)
    except (AttributeError, SchemaError) as exc:
        raise CandidateRequestError(
            f"E-G6A-06-020: 最终候选请求不符合 canonical schema（{exc}）") \
            from exc
    root = _object(payload, "request",
                   ("schema_version", "run_id", "source_revision", "context"))
    if root["schema_version"] != "1.1.0":
        raise CandidateRequestError(
            "E-G6A-06-020: request.schema_version 须为 '1.1.0'")
    run_id = _nonempty_string(root["run_id"], "request.run_id")
    # G6A-06 请求绑定/partial-route 硬化：请求必须声明期望 source commit/tree
    # （严格 40 位小写十六进制），冻结服务据此要求与显式干净 checkout 逐字一致。
    src = _object(root["source_revision"], "request.source_revision",
                  ("source_commit", "source_tree"))
    source_commit = _nonempty_string(
        src["source_commit"], "source_revision.source_commit")
    source_tree = _nonempty_string(
        src["source_tree"], "source_revision.source_tree")
    try:
        validate_source_revision(source_commit, source_tree)
    except RecomputeError as exc:
        raise CandidateRequestError(
            f"E-G6A-06-020: request.source_revision 非法（{exc}）—— 失败关闭") \
            from exc
    body = _object(
        root["context"], "request.context",
        ("contract", "facts", "macro", "formula_specs", "valuation_inputs",
         "assumption_defaults", "approved_snapshot", "open_items_policy",
         "valuation_routes"))

    mappings = {}
    for key in ("contract", "facts", "macro", "formula_specs",
                "assumption_defaults"):
        mappings[key] = copy.deepcopy(_mapping(body[key], f"context.{key}"))

    # G6A-06 PARTIAL：四路估值声明 —— 键集必须恰好四路；状态专属形状：
    # READY 不得带 reason/evidence_refs/missing_inputs；INPUT_MISSING 必须带
    # 非空 missing_inputs；NOT_EVALUATED 不得带 missing_inputs；非 READY 必须
    # 带非空 reason + 非空证据引用列表；声明状态与 facts 中该路数值事实相互
    # 矛盾 → 失败关闭。契约 oneOf 已强制形状，此处再逐路显式校验给出精确错误。
    vr_raw = _object(body["valuation_routes"], "context.valuation_routes",
                     VALUATION_ROUTES)
    route_decls = {}
    for route in VALUATION_ROUTES:
        decl = _object(
            vr_raw[route], f"valuation_routes.{route}", ("state",),
            ("reason", "evidence_refs", "missing_inputs"))
        state = decl["state"]
        if state not in ROUTE_STATES:
            raise CandidateRequestError(
                f"E-G6A-06-020: valuation_routes.{route}.state 非法"
                f"（{state!r}，须为 {ROUTE_STATES}）")
        if state == ROUTE_READY:
            extra = [k for k in ("reason", "evidence_refs", "missing_inputs")
                     if k in decl]
            if extra:
                raise CandidateRequestError(
                    f"E-G6A-06-020: READY 路由 {route} 不得携带 {extra} —— "
                    "声明/事实矛盾，失败关闭")
            route_decls[route] = RouteDeclaration(ROUTE_READY)
            continue
        reason = _nonempty_string(decl.get("reason", ""),
                                  f"valuation_routes.{route}.reason")
        refs = decl.get("evidence_refs")
        if (not isinstance(refs, list) or not refs
                or not all(isinstance(r, str) and r.strip() for r in refs)):
            raise CandidateRequestError(
                f"E-G6A-06-020: valuation_routes.{route}.evidence_refs "
                "须为非空证据引用列表")
        refs = tuple(r.strip() for r in refs)
        missing = ()
        if "missing_inputs" in decl:
            mis = decl["missing_inputs"]
            if (not isinstance(mis, list)
                    or not all(isinstance(x, str) and x.strip() for x in mis)):
                raise CandidateRequestError(
                    f"E-G6A-06-020: valuation_routes.{route}.missing_inputs "
                    "须为字符串列表")
            missing = tuple(x.strip() for x in mis)
        if state == ROUTE_INPUT_MISSING and not missing:
            raise CandidateRequestError(
                f"E-G6A-06-020: INPUT_MISSING 路由 {route} 必须带非空 "
                "missing_inputs —— 失败关闭")
        if state == ROUTE_NOT_EVALUATED and missing:
            raise CandidateRequestError(
                f"E-G6A-06-020: NOT_EVALUATED 路由 {route} 不得携带 "
                "missing_inputs —— 失败关闭")
        try:
            route_decls[route] = RouteDeclaration(state, reason, refs, missing)
        except RecomputeError as exc:
            raise CandidateRequestError(
                f"E-G6A-06-020: valuation_routes.{route} 声明非法（{exc}）"
                " —— 失败关闭") from exc
    facts_raw = mappings["facts"]
    for route in VALUATION_ROUTES:
        fk = ROUTE_FACT_KEYS[route]
        has = fk in facts_raw
        if route_decls[route].state == ROUTE_READY:
            if not (has and isinstance(facts_raw[fk], str)
                    and facts_raw[fk].strip()):
                raise CandidateRequestError(
                    f"E-G6A-06-020: READY 路由 {route} 缺必需事实字段 "
                    f"facts.{fk} —— 声明/事实矛盾，失败关闭")
        elif has:
            raise CandidateRequestError(
                f"E-G6A-06-020: 非 READY 路由 {route} 携带数值事实 "
                f"facts.{fk} —— 非 READY 不得夹带该路数值，失败关闭")

    vi_raw = _object(
        body["valuation_inputs"], "context.valuation_inputs",
        ("scope", "currency", "as_of"),
        ("price", "shares_outstanding", "net_debt", "minority_interest",
         "industry_commodity", "statuses"))
    _nonempty_string(vi_raw["scope"], "valuation_inputs.scope")
    _nonempty_string(vi_raw["currency"], "valuation_inputs.currency")
    _nonempty_string(vi_raw["as_of"], "valuation_inputs.as_of")
    _nonempty_string(
        mappings["contract"].get("contract_id"), "contract.contract_id")
    policy_raw = _object(
        body["open_items_policy"], "context.open_items_policy",
        ("tolerance", "owner_role", "due_date", "blocks_gate"))
    snap_raw = _object(
        body["approved_snapshot"], "context.approved_snapshot",
        ("snapshot_id", "version", "proposals", "decisions"))
    snapshot_id = _nonempty_string(
        snap_raw["snapshot_id"], "approved_snapshot.snapshot_id")
    version = snap_raw["version"]
    if type(version) is not int or version < 1:
        raise CandidateRequestError(
            "E-G6A-06-020: approved_snapshot.version 须为正整数")
    if not isinstance(snap_raw["proposals"], list) \
            or not isinstance(snap_raw["decisions"], list):
        raise CandidateRequestError(
            "E-G6A-06-020: approved_snapshot.proposals/decisions 须为数组")

    registry = AssumptionRegistry()
    try:
        for index, item in enumerate(snap_raw["proposals"]):
            p = _object(item, f"approved_snapshot.proposals[{index}]",
                        ("proposal_id", "payload", "proposed_by"))
            registry.propose(AssumptionProposal(
                _nonempty_string(p["proposal_id"], "proposal_id"),
                copy.deepcopy(_mapping(p["payload"], "proposal.payload")),
                _nonempty_string(p["proposed_by"], "proposal.proposed_by")))
        for index, item in enumerate(snap_raw["decisions"]):
            d = _object(
                item, f"approved_snapshot.decisions[{index}]",
                ("proposal_id", "decision", "approver", "decided_at", "token"),
                ("rejection_reason",))
            registry.decide(
                _nonempty_string(d["proposal_id"], "decision.proposal_id"),
                _nonempty_string(d["decision"], "decision.decision"),
                _nonempty_string(d["approver"], "decision.approver"),
                _nonempty_string(d["decided_at"], "decision.decided_at"),
                _nonempty_string(d["token"], "decision.token"),
                d.get("rejection_reason"))
        snapshot = AssumptionSnapshot(snapshot_id, version).build(registry)
        valuation_inputs = ValuationInputs(**copy.deepcopy(vi_raw))
        policy = OpenItemsPolicy(**copy.deepcopy(policy_raw))
    except (AssumptionError, TypeError, ValueError) as exc:
        raise CandidateRequestError(
            f"E-G6A-06-020: 最终候选输入无法构造冻结上下文（{exc}）") from exc

    return FinalCandidateRequest(
        run_id=run_id,
        context=ResearchContext(
            contract=mappings["contract"], facts=mappings["facts"],
            macro=mappings["macro"], formula_specs=mappings["formula_specs"],
            valuation_inputs=valuation_inputs,
            assumption_defaults=mappings["assumption_defaults"],
            approved=snapshot, open_items_policy=policy,
            valuation_routes=ValuationRoutes(route_decls)),
        source_commit=source_commit, source_tree=source_tree)


class CandidateFreezeService:
    """最终候选冻结服务（app 层，只写 ArtifactStore，不写任何 DB 对象）。"""

    def __init__(self, store: ArtifactStore):
        self.store = store

    # ── 权威冻结入口 ──────────────────────────────────────────────
    def freeze_final_candidate(self, ctx: ResearchContext, run_id: str,
                               source_commit: str, source_tree: str,
                               recompute: RecomputeResult, *,
                               request_payload: dict) -> CandidateFreeze:
        """权威最终候选冻结。

        · source commit/tree 显式提供并严格校验（E-G6A-06-001），不猜 git HEAD。
        · OI-PF-200 保留：入口形态校验（E-G6A-05-003）→ 独立重算 canonical →
          逐项比对调用方回算结果（绑定哈希 E-G6A-05-005 / 键集·产物·哈希
          E-G6A-05-006）→ 冻结前写入边界漂移检查（E-G6A-05-007）。
          调用方结果**不是权威**，candidate 的 products/product_hashes/
          frozen_inputs_hash 仅来自 canonical。
        · G6A-06 request 绑定/partial-route 硬化：`request_payload` 为**必填
          关键字参数** —— 不允许无受管请求依赖的 canonical 最终候选。冻结先
          把请求**严格规范为单一 immutable 字节映像**（allow_nan=False，任意
          NaN/Infinity 一律 E-G6A-06-002 失败关闭），把该映像解析回普通对象经
          `final_candidate_request` 验证、经 `_bind_managed_request` 证明其
          run_id / 重放 frozen_inputs_hash / 批准快照哈希 / contract / scope /
          as_of / 逐项产物正文与哈希与待冻结 ctx/canonical **精确一致**，并
          要求请求声明的 source revision 与显式提供的干净 checkout 逐字一致；
          随后把**同一份字节**原样落库为独立不可变对象，digest 写入
          candidate.request_hash —— 调用方随后改动原载荷改变不了已存请求字节。
        · 11 项产品正文逐项内容寻址写入 ArtifactStore；实际 digest 必须逐字等于
          `RecomputeResult.shas`（不相等即 E-G6A-06-002 失败关闭）。
        · candidate 含 `kind="candidate"` + source_commit/source_tree；
          source 字段参与候选内容寻址身份。
        · 失败（含写入边界漂移）绝不写 candidate；已写入的产品正文/请求对象
          是孤儿，不构成完整 bundle（verify 会拒）。
        """
        if not isinstance(run_id, str) or not run_id.strip():
            raise RecomputeError(
                "E-G6A-06-001: run_id 须为非空字符串 —— 失败关闭")
        validate_source_revision(source_commit, source_tree)
        # 先把请求严格规范为单一 immutable 字节映像并解析回普通对象 —— 之后
        # 只信任这份映像，不再二次读取调用方可变对象（TOCTOU / NaN 关闭）。
        request_bytes, request_obj = _canonicalize_request(request_payload)
        frozen_inputs_payload(ctx)                 # E-G6A-05-003 形态校验
        canonical = recompute_all(ctx)             # 独立 canonical 重算
        _validate_recompute_binding(recompute, canonical)   # E-G6A-05-005/006
        req = self._bind_managed_request(request_obj, ctx, canonical, run_id)
        if (req.source_commit != source_commit
                or req.source_tree != source_tree):
            raise RecomputeError(
                "E-G6A-06-002: 受管请求 source revision "
                f"（{req.source_commit[:12]}…/{req.source_tree[:12]}…）≠ 显式 "
                f"干净 checkout（{source_commit[:12]}…/{source_tree[:12]}…）"
                " —— 失败关闭，零对象写入")
        for name in PRODUCT_ORDER:                 # 产品正文逐项落库（内容寻址）
            data = canonical_bytes(canonical.products[name])
            digest = self.store.store(PRODUCT_KIND, data)
            if digest != canonical.shas[name]:
                raise RecomputeError(
                    f"E-G6A-06-002: 产品正文落库 digest {digest} ≠ 规范哈希 "
                    f"{canonical.shas[name]}（{name}）—— 失败关闭")
        # 受管请求**同一份** immutable 字节落库（独立不可变对象），digest 锚定
        # 进候选 —— 绝不重新序列化调用方可变对象（TOCTOU 关闭）。
        request_hash = self.store.store(
            FINAL_CANDIDATE_REQUEST_KIND, request_bytes)
        quality_status, release_eligible = quality_from_products(
            canonical.products)
        candidate = {
            "schema_version": "1.1.0",
            "kind": CANDIDATE_KIND,
            "run_id": run_id,
            "source_commit": source_commit,
            "source_tree": source_tree,
            "contract": ctx.contract.get("contract_id"),
            "scope": ctx.valuation_inputs.scope,
            "as_of": ctx.valuation_inputs.as_of,
            "products": list(canonical.product_ids()),
            "product_hashes": dict(canonical.shas),
            "approved_snapshot": _frozen_approved_sha256(ctx),
            "frozen_inputs_hash": canonical.frozen_inputs_hash,
            "request_hash": request_hash,
            # G6A-06 PARTIAL：质量/发布资格只由 canonical 产物派生
            # （open_items + 估值/情景 typed status），绝不来自调用方输入。
            "quality_status": quality_status,
            "release_eligible": release_eligible,
        }
        try:
            validate_object("final_candidate", candidate)
        except SchemaError as exc:
            raise RecomputeError(
                f"E-G6A-06-002: candidate 不符合 canonical schema（{exc}）") from exc
        data = canonical_bytes(candidate)
        _assert_write_boundary(ctx, canonical)     # E-G6A-05-007：存储前漂移检查
        cid = hashlib.sha256(data).hexdigest()
        stored = self.store.store(CANDIDATE_KIND, data)
        if stored != cid:
            raise RecomputeError(
                "E-G6A-06-002: candidate 落库 digest 与规范内容哈希不符")
        return CandidateFreeze(candidate_id=cid, candidate=candidate,
                               recompute=canonical)

    def _bind_managed_request(self, request_obj: dict,
                              ctx: ResearchContext, canonical: RecomputeResult,
                              run_id: str) -> FinalCandidateRequest:
        """验证受管请求（**已解析的 immutable 字节映像**）与待冻结 ctx/canonical
        精确等价（G6A-06 request 绑定/partial-route 硬化）。

        入参必须是 `_canonicalize_request` 从 strict 字节映像解析出的普通对象，
        冻结只重放这份映像，不读取调用方原始可变对象。合法请求必须：
          · 经 `final_candidate_request` 重建（proposal/decision 重放批准快照）；
          · run_id 与冻结 run_id 逐字一致；
          · 从请求重放 `recompute_all` 的 frozen_inputs_hash 与批准快照哈希、
            contract/scope/as_of、逐项产物正文与哈希 == 待冻结 canonical。
        任一不符 → E-G6A-06-002 失败关闭，零对象写入。返回已验证请求（含
        source revision，供调用方核对显式干净 checkout）。
        """
        try:
            req = final_candidate_request(request_obj)
        except (CandidateRequestError, RecomputeError) as exc:
            raise RecomputeError(
                f"E-G6A-06-002: 受管请求不合法（{exc}）—— 失败关闭") from exc
        if req.run_id != run_id:
            raise RecomputeError(
                f"E-G6A-06-002: 受管请求 run_id {req.run_id!r} ≠ 冻结 run_id "
                f"{run_id!r} —— 失败关闭")
        try:
            req_canonical = recompute_all(req.context)
        except Exception as exc:
            raise RecomputeError(
                f"E-G6A-06-002: 受管请求无法确定性重算"
                f"（{type(exc).__name__}: {exc}）—— 失败关闭") from exc
        if req_canonical.frozen_inputs_hash != canonical.frozen_inputs_hash:
            raise RecomputeError(
                "E-G6A-06-002: 受管请求重放冻结输入哈希 ≠ 待冻结 canonical"
                " —— 请求与上下文不一致，失败关闭")
        try:
            req_approved = _frozen_approved_sha256(req.context)
            ctx_approved = _frozen_approved_sha256(ctx)
        except Exception as exc:
            raise RecomputeError(
                f"E-G6A-06-002: 批准快照不可用（{exc}）—— 失败关闭") from exc
        if req_approved != ctx_approved:
            raise RecomputeError(
                "E-G6A-06-002: 受管请求批准快照哈希 ≠ 待冻结上下文 —— 失败关闭")
        if (ctx.contract.get("contract_id")
                != req.context.contract.get("contract_id")
                or ctx.valuation_inputs.scope != req.context.valuation_inputs.scope
                or ctx.valuation_inputs.as_of != req.context.valuation_inputs.as_of):
            raise RecomputeError(
                "E-G6A-06-002: 受管请求 contract/scope/as_of 与待冻结上下文"
                " 不一致 —— 失败关闭")
        if (req_canonical.shas != canonical.shas
                or req_canonical.products != canonical.products):
            raise RecomputeError(
                "E-G6A-06-002: 受管请求重放产物正文/哈希与待冻结 canonical "
                "不一致 —— 失败关闭")
        return req

    # ── 可重载/可验证 bundle API ──────────────────────────────────
    @staticmethod
    def _parse_object(data: bytes, what: str) -> dict:
        # 严格 JSON（parse_constant 拒绝 NaN/Infinity）—— 非标准常量/非 JSON
        # /非 object 根都归一 E-G6A-06-010 失败关闭。
        try:
            return _strict_json_object(data, what, "E-G6A-06-010")
        except CandidateVerificationError:
            raise
        except Exception as exc:
            raise CandidateVerificationError(
                f"E-G6A-06-010: {what} 非 UTF-8/JSON —— 失败关闭"
                f"（{type(exc).__name__}）") from exc

    def _verify_candidate_core(self, candidate: dict) -> dict:
        """对**反序列化后的候选字典**做 bundle 语义校验（不含期望 revision 比对）。

        供 load_candidate_bundle 与发布资格门 `verify_stored_final_candidate`
        复用；也作为独立可测的校验核心 —— 任何缺失（kind/source/
        product_hashes/products）、篡改、错键集或错 revision 都稳定失败关闭
        （E-G6A-06-011~015）；G6A-06 PARTIAL 下根 quality_status/
        release_eligible 必须与实际加载产物严格派生一致，畸形/未知状态/不一致
        归一为 E-G6A-06-018（质量派生单源 `quality_from_products`）。
        """
        if candidate.get("schema_version") != "1.1.0":
            raise CandidateVerificationError(
                "E-G6A-06-011: candidate.schema_version ≠ '1.1.0' —— 失败关闭")
        if candidate.get("kind") != CANDIDATE_KIND:   # ②
            raise CandidateVerificationError(
                f"E-G6A-06-011: candidate.kind = {candidate.get('kind')!r}"
                f" ≠ {CANDIDATE_KIND!r} —— 失败关闭")
        for label in ("source_commit", "source_tree"):   # ③
            val = candidate.get(label)
            if not isinstance(val, str) or not SOURCE_REVISION_RE.fullmatch(val):
                raise CandidateVerificationError(
                    f"E-G6A-06-012: candidate.{label} 非严格 40 位小写十六进制"
                    f"（实得 {val!r}）—— 失败关闭")
        for label in ("frozen_inputs_hash", "approved_snapshot"):
            digest = candidate.get(label)
            if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
                raise CandidateVerificationError(
                    f"E-G6A-06-013: candidate.{label} 非严格 sha256")
        if not isinstance(candidate.get("run_id"), str) \
                or not candidate["run_id"].strip():
            raise CandidateVerificationError(
                "E-G6A-06-013: candidate.run_id 缺失或为空")
        try:
            validate_object("final_candidate", candidate)
        except (AttributeError, SchemaError) as exc:
            raise CandidateVerificationError(
                f"E-G6A-06-013: candidate 不符合 canonical schema（{exc}）") \
                from exc
        product_hashes = candidate.get("product_hashes")
        products_list = candidate.get("products")
        if not isinstance(product_hashes, dict):       # ④
            raise CandidateVerificationError(
                "E-G6A-06-013: candidate.product_hashes 缺失或非 dict —— 失败关闭")
        if set(product_hashes) != set(PRODUCT_ORDER):
            raise CandidateVerificationError(
                f"E-G6A-06-013: 产品哈希键集 {sorted(product_hashes)} ≠ 生产注册表"
                f" {sorted(PRODUCT_ORDER)} —— 失败关闭")
        if not isinstance(products_list, list) \
                or tuple(products_list) != tuple(sorted(PRODUCT_ORDER)):
            raise CandidateVerificationError(
                "E-G6A-06-013: 产品列表须无重复且逐字等于排序后的生产注册表"
                " —— 失败关闭")
        products: Dict[str, dict] = {}
        for name in PRODUCT_ORDER:                     # ⑤
            digest = product_hashes[name]
            if not isinstance(digest, str) \
                    or not re.fullmatch(r"[0-9a-f]{64}", digest):
                raise CandidateVerificationError(
                    f"E-G6A-06-013: product_hashes[{name!r}] 非严格 sha256")
            try:
                body = self.store.load(digest)         # 缺失/篡改在此拒
            except (TypeError, ValueError, OSError) as exc:
                raise CandidateVerificationError(
                    f"E-G6A-06-014: 产品正文不可读（{name} = {digest[:12]}…）"
                    f"—— {exc}") from exc
            prod = self._parse_object(body, f"product {name}")
            try:
                actual = _prod_sha(prod)
            except (TypeError, ValueError) as exc:
                raise CandidateVerificationError(
                    f"E-G6A-06-015: 产品正文无法规范哈希（{name}）") from exc
            if actual != digest:
                raise CandidateVerificationError(
                    f"E-G6A-06-015: 产品正文重算哈希 ≠ 记录值（{name}）"
                    f"—— 正文被篡改或与候选不一致，失败关闭")
            products[name] = prod
        # ⑥ G6A-06 PARTIAL：根质量/发布元数据必须与**实际加载产物**严格派生
        # 一致（quality_from_products 单源）—— 畸形/未知状态/route 不一致归一
        # E-G6A-06-018；根/产物不一致同样 E-G6A-06-018 失败关闭。
        try:
            quality, eligible = quality_from_products(products)
        except QualityError as exc:
            raise CandidateVerificationError(
                f"E-G6A-06-018: 严格质量派生失败（{exc}）—— 失败关闭") from exc
        if candidate.get("quality_status") != quality:
            raise CandidateVerificationError(
                f"E-G6A-06-018: candidate.quality_status "
                f"{candidate.get('quality_status')!r} ≠ 产物派生 {quality!r}"
                " —— 根/产物不一致，失败关闭")
        if candidate.get("release_eligible") != eligible:
            raise CandidateVerificationError(
                f"E-G6A-06-018: candidate.release_eligible "
                f"{candidate.get('release_eligible')!r} ≠ 产物派生 {eligible}"
                " —— 根/产物不一致，失败关闭")
        # ⑦ G6A-06 request 绑定：candidate 必须锚定受管请求，重载该请求 →
        # final_candidate_request 重放批准 → recompute_all 重放产物，并要求
        # run_id / source revision / frozen_inputs_hash / 批准快照哈希 /
        # contract / scope / as_of / 逐项产物正文与哈希与候选声称的**精确
        # 一致**。任何畸形 / 错配 / 自洽但不对应绑定请求的 bundle →
        # E-G6A-06-018 失败关闭 —— 任意全 PASS 产物 + 空 open_items 自洽
        # 伪造无法再被批准。
        self._verify_request_binding(candidate, products, product_hashes)
        return {"candidate": candidate, "products": products,
                "product_hashes": dict(product_hashes)}

    def _verify_request_binding(self, candidate: dict, products: Dict[str, dict],
                                product_hashes: Dict[str, str]) -> None:
        """重载绑定请求并重放，验证与候选精确一致（E-G6A-06-018 归一）。

        请求对象加载走严格 JSON（parse_constant 拒绝 NaN/Infinity 常量）——
        库内被写入非标准 JSON 常量字节的请求对象在此失败关闭。
        """
        req_hash = candidate.get("request_hash")
        if not isinstance(req_hash, str) \
                or not re.fullmatch(r"[0-9a-f]{64}", req_hash):
            raise CandidateVerificationError(
                "E-G6A-06-018: candidate.request_hash 非严格 sha256 —— 失败关闭")
        try:
            data = self.store.load(req_hash)
        except (TypeError, ValueError, OSError) as exc:
            raise CandidateVerificationError(
                f"E-G6A-06-018: 受管请求不可读或内容损坏"
                f"（{req_hash[:12]}…）—— {exc}") from exc
        try:
            obj = _strict_json_object(data, "受管请求", "E-G6A-06-018")
        except CandidateVerificationError:
            raise
        except (UnicodeDecodeError, ValueError) as exc:
            raise CandidateVerificationError(
                "E-G6A-06-018: 受管请求非 UTF-8/JSON —— 失败关闭") from exc
        try:
            request = final_candidate_request(obj)
        except (CandidateRequestError, RecomputeError,
                AttributeError, SchemaError) as exc:
            raise CandidateVerificationError(
                f"E-G6A-06-018: 受管请求重建失败（{exc}）—— 失败关闭") from exc
        try:
            req_canonical = recompute_all(request.context)
        except Exception as exc:
            raise CandidateVerificationError(
                f"E-G6A-06-018: 受管请求无法确定性重算"
                f"（{type(exc).__name__}: {exc}）—— 失败关闭") from exc
        if request.run_id != candidate.get("run_id"):
            raise CandidateVerificationError(
                "E-G6A-06-018: 绑定请求 run_id ≠ candidate.run_id —— 失败关闭")
        if (request.source_commit != candidate.get("source_commit")
                or request.source_tree != candidate.get("source_tree")):
            raise CandidateVerificationError(
                "E-G6A-06-018: 绑定请求 source revision ≠ candidate source "
                "revision —— 失败关闭")
        try:
            req_approved = _frozen_approved_sha256(request.context)
        except Exception as exc:
            raise CandidateVerificationError(
                f"E-G6A-06-018: 绑定请求批准快照不可用（{exc}）"
                " —— 失败关闭") from exc
        if req_approved != candidate.get("approved_snapshot"):
            raise CandidateVerificationError(
                "E-G6A-06-018: 绑定请求批准快照哈希 ≠ candidate.approved_snapshot"
                " —— 失败关闭")
        if req_canonical.frozen_inputs_hash != candidate.get("frozen_inputs_hash"):
            raise CandidateVerificationError(
                "E-G6A-06-018: 绑定请求重放 frozen_inputs_hash ≠ 候选记录"
                " —— 失败关闭")
        if (candidate.get("contract")
                != request.context.contract.get("contract_id")
                or candidate.get("scope")
                != request.context.valuation_inputs.scope
                or candidate.get("as_of")
                != request.context.valuation_inputs.as_of):
            raise CandidateVerificationError(
                "E-G6A-06-018: 绑定请求 contract/scope/as_of 与候选不一致"
                " —— 失败关闭")
        for name in PRODUCT_ORDER:
            if req_canonical.shas.get(name) != product_hashes.get(name) \
                    or req_canonical.products.get(name) != products.get(name):
                raise CandidateVerificationError(
                    f"E-G6A-06-018: 绑定请求重放产物 {name} 正文/哈希与候选"
                    " 不符 —— 失败关闭")
        try:
            req_quality, req_eligible = quality_from_products(
                req_canonical.products)
        except QualityError as exc:
            raise CandidateVerificationError(
                f"E-G6A-06-018: 绑定请求严格质量派生失败（{exc}）"
                " —— 失败关闭") from exc
        if (req_quality != candidate.get("quality_status")
                or req_eligible != candidate.get("release_eligible")):
            raise CandidateVerificationError(
                "E-G6A-06-018: 绑定请求派生质量 ≠ 候选根元数据 —— 失败关闭")

    def _verify_dict(self, candidate: dict, *, expected_source_commit: str,
                     expected_source_tree: str) -> dict:
        """反序列化后的候选字典 → 完整 bundle 语义校验 + 期望代码版本比对。

        `_verify_candidate_core` 全过后再要求 source revision 与调用方期望
        版本逐字一致（E-G6A-06-017）。
        """
        verified = self._verify_candidate_core(candidate)
        for label, expected in (("source_commit", expected_source_commit),
                                ("source_tree", expected_source_tree)):
            if not isinstance(expected, str) \
                    or not SOURCE_REVISION_RE.fullmatch(expected):
                raise CandidateVerificationError(
                    f"E-G6A-06-017: 期望 {label} 非严格 40 位小写十六进制")
            if candidate[label] != expected:
                raise CandidateVerificationError(
                    f"E-G6A-06-017: candidate.{label} 与当前期望代码版本不符"
                    "—— 错代码版本失败关闭")
        return verified

    def load_candidate_bundle(self, candidate_id: str, *,
                              expected_source_commit: str,
                              expected_source_tree: str) -> CandidateBundle:
        """从仅有 ArtifactStore + candidate id 加载 candidate 与全部产品正文。

        逐项校验（任一失败稳定失败关闭）：
          ① candidate 自身内容哈希 —— store.load 读时哈希校验（E-G2-02-005）
          ② kind == "candidate"
          ③ source_commit / source_tree 严格 40 位小写十六进制
          ④ 产品键集精确等于生产注册表 PRODUCT_ORDER
          ⑤ 每个产品正文 store.load 可读（缺失/篡改即拒）且重算哈希 == 记录值
        """
        try:
            data = self.store.load(candidate_id)    # ① 候选自身内容哈希
        except (TypeError, ValueError, OSError) as exc:
            raise CandidateVerificationError(
                f"E-G6A-06-016: candidate 缺失或被篡改"
                f"（{str(candidate_id)[:12]}…）"
                f"—— {exc}") from exc
        candidate = self._parse_object(data, "candidate")
        verified = self._verify_dict(
            candidate, expected_source_commit=expected_source_commit,
            expected_source_tree=expected_source_tree)
        return CandidateBundle(candidate_id=candidate_id,
                               candidate=verified["candidate"],
                               products=verified["products"],
                               product_hashes=verified["product_hashes"],
                               request_hash=verified["candidate"].get(
                                   "request_hash"))

    def verify_candidate_bundle(self, candidate_id: str, *,
                                expected_source_commit: str,
                                expected_source_tree: str) -> dict:
        """完整复验入口：load_candidate_bundle 全通过即返回验证结论。

        任何缺失、篡改、错键集或错 revision 都稳定抛
        CandidateVerificationError（失败关闭），绝不返回部分成功。
        """
        b = self.load_candidate_bundle(
            candidate_id, expected_source_commit=expected_source_commit,
            expected_source_tree=expected_source_tree)
        return {
            "candidate_id": candidate_id,
            "kind": b.candidate.get("kind"),
            "source_commit": b.candidate.get("source_commit"),
            "source_tree": b.candidate.get("source_tree"),
            "frozen_inputs_hash": b.candidate.get("frozen_inputs_hash"),
            "request_hash": b.candidate.get("request_hash"),
            "product_count": len(b.products),
            "products": sorted(b.products),
        }


# 最终候选**强专属**字段：凡含其中任一字段的对象都视为最终候选形状，不得借
# 「删掉 product_hashes 降级成 legacy」绕过发布资格门。
FINAL_CANDIDATE_ONLY_FIELDS = (
    "request_hash", "product_hashes", "source_commit", "source_tree",
    "frozen_inputs_hash", "quality_status", "release_eligible",
)

# 对 **非 candidate** 对象（report/manifest 等）只有**明确的最终依赖标记**
# 才把它们判为 malformed-final —— `source_commit`/`quality_status` 等通用溯源/
# 状态字段可能合法出现在 report/manifest 上，单独出现不构成最终候选（防过度
# 拦截）；`request_hash`/`product_hashes` 则是无可争议的最终候选专属依赖标记。
FINAL_DEPENDENCY_MARKERS = ("request_hash", "product_hashes")

# 真 legacy G4 candidate 的**精确**根键集：schema_version 1.0.0 + kind +
# payload（object）—— 只这一种形状跳过发布资格门（G4 治理）。任何其他
# candidate 对象（含剥离了全部强标记、保留 run_id/contract/scope/as_of/
# products/approved_snapshot 的 1.1 PARTIAL 根）都是 strict-final，必须按
# canonical 1.1 校验或 E-G6A-06-030 失败关闭，不得降级。
LEGACY_G4_CANDIDATE_KEYS = frozenset(("schema_version", "kind", "payload"))


def _is_exact_legacy_g4(obj) -> bool:
    """真 legacy G4 candidate 的**精确**形状：schema_version 1.0.0 + kind +
    payload(object)，根键集恰好三项。只此一种形状可在「调用点声明候选」
    （expected_candidate=True）下跳过发布资格门 —— kind 被删或剥离全部强标记
    的正文一律 strict-final/malformed（E-G6A-06-030），不得借形状检查跳过。
    """
    return (isinstance(obj, dict)
            and obj.get("schema_version") == "1.0.0"
            and obj.get("kind") == CANDIDATE_KIND
            and set(obj) == LEGACY_G4_CANDIDATE_KEYS
            and isinstance(obj.get("payload"), dict))


def _final_candidate_shape(obj: dict) -> bool:
    """最终候选形状判定。

      · kind == candidate 且根键集/版本**精确**等于 legacy G4（1.0.0 + kind +
        payload object）→ False，交 G4 治理；
      · 其余任何 candidate 对象（schema_version 1.1、或剥离强标记但保留
        最终字段的 1.0 形态、或含任一强专属字段）→ True（strict-final）；
      · 非 candidate 对象：仅含 `request_hash`/`product_hashes` 这类**明确
        最终依赖标记**才判 True；仅带 source_commit/quality_status 等通用
        溯源/状态字段的 report/manifest → False（防过度拦截）。

    契约边界（非本门可修属性）：本门**不声称**能区分「删光所有最终字段并把
    自己重写成另一份合法 legacy 1.0 对象」的改写 —— 那种对象已与真 legacy
    对象形态不可分，属既有 G4 契约（payload/闭包/审计）的治理边界；本门只
    保证凡**保留**任一强标记、声明 1.1，或仍保留 run_id/contract/scope/as_of/
    products/approved_snapshot 的 candidate 对象必须 strict-final。清单/调用点
    声明候选（expected_candidate=True）时，连 kind 被删的正文也不得跳过。
    """
    if not isinstance(obj, dict):
        return True
    if obj.get("kind") == CANDIDATE_KIND:
        return not _is_exact_legacy_g4(obj)
    return any(f in obj for f in FINAL_DEPENDENCY_MARKERS)


def bundle_manifest_objects(bundle: CandidateBundle) -> dict:
    """把**已验证** CandidateBundle 展开为精确的 manifest.objects 登记表。

    最终候选在闭包内**内部引用恰好 12 个依赖 digest**：request_hash + 全部
    11 项 product_hashes。本助手直接由 bundle 生成与那 12 个 digest 精确一致
    的条目（candidate refs + request/各 recompute_product 叶子条目），避免
    调用方手抄哈希 —— 发布清单应把返回的登记表并入 `manifest["objects"]`。
    """
    cand = bundle.candidate
    refs = [cand["request_hash"]] + sorted(cand["product_hashes"].values())
    objects = {
        bundle.candidate_id: {"kind": CANDIDATE_KIND, "refs": refs},
        cand["request_hash"]: {"kind": FINAL_CANDIDATE_REQUEST_KIND,
                               "refs": []},
    }
    for digest in cand["product_hashes"].values():
        objects[digest] = {"kind": PRODUCT_KIND, "refs": []}
    return objects


def verify_stored_final_candidate(store: ArtifactStore,
                                  candidate_id: Optional[str], *,
                                  expected_candidate: bool = False) -> Optional[str]:
    """发布资格门调用的**不可钉版** stored-bundle 校验（G6A-06 PARTIAL 收口）。

    仅凭候选根自证 quality_status/release_eligible 不再可信（A）：凡最终候选
    形状（强专属字段或 1.1 candidate）的对象，从 ArtifactStore 重载 candidate
    与精确 11 项产品正文，逐项校验 digest/body/对象形态，再经
    `quality_from_products` **严格重派生**质量并比对根元数据，且重载受管请求
    （request_hash）重放 final_candidate_request→recompute_all 并要求与候选
    精确一致 —— 只有**真** FULL 可发布 bundle 返回 None：

      · 缺失 source 字段、畸形产品注册表、产品不可读/篡改、未知/缺失产品
        状态、open_items/route-status 不一致、质量不一致、请求绑定不符 →
        稳定 E-G6A-06-030；
      · 合法但 PARTIAL/不可发布 → E-G6A-06-031；
      · 非最终候选形状（真 legacy G4 candidate + payload）→ None，由 G4
        其他门把关。

    `expected_candidate`（调用点对每个被检 id 的候选声明）：candidate_digest
    恒为 True；subject_root 在 manifest.objects[subject_root].kind ==
    "candidate" 时为 True。声明候选时只有**精确** legacy G4 形状可跳过 ——
    kind 被删 + 剥离全部强标记的正文不得借形状检查跳过，一律 strict-final/
    malformed → E-G6A-06-030。

    publish_engine.final_candidate_release_gate 以**惰性导入**调用本函数，
    避免 publish_engine↔recompute 的模块级循环导入；质量算法只存在于
    `quality_from_products` 单源，发布侧不复制第二份。
    """
    if not candidate_id:
        return None
    try:
        data = store.load(candidate_id)   # 读时哈希校验 = 候选自身被篡改必拒
    except (TypeError, ValueError, OSError) as exc:
        return (f"E-G6A-06-030: 候选不可读或内容损坏: "
                f"{str(candidate_id)[:12]}…（{exc}）")
    try:
        obj = _strict_json_object(data, "候选", "E-G6A-06-030")
    except CandidateVerificationError as exc:
        return str(exc)
    if expected_candidate:
        # 调用点声明这是候选：只有精确 legacy G4 形状跳过（G4 治理）；任何
        # 其他正文（含 kind 被删 + 剥离全部强标记）都是 strict-final/malformed。
        if _is_exact_legacy_g4(obj):
            return None
    elif not _final_candidate_shape(obj):
        return None    # 非最终候选形状（真 legacy G4 candidate 或非 candidate）
    service = CandidateFreezeService(store)
    try:
        verified = service._verify_candidate_core(obj)
    except CandidateVerificationError as exc:
        return (f"E-G6A-06-030: 最终候选 bundle 复验失败（{exc}）"
                "—— 失败关闭")
    cand = verified["candidate"]
    if cand.get("quality_status") == QUALITY_PARTIAL \
            or not cand.get("release_eligible"):
        return (f"E-G6A-06-031: 最终候选 PARTIAL 或不可发布"
                f"（quality_status={cand.get('quality_status')}, "
                f"release_eligible={cand.get('release_eligible')}）"
                "—— 拒绝批准/准出/发布")
    return None


def freeze_final_candidate_from_payload(store: ArtifactStore, payload: dict,
                                        *, source_commit: str,
                                        source_tree: str) -> CandidateFreeze:
    """受管 JSON 输入的生产服务入口，只生成可复验 candidate bundle。

    调用链由 `backend/tools/final_candidate.py` 暴露；该 CLI 负责从当前干净
    checkout 取得真实 source revision。本函数负责默认拒绝地重建批准快照与
    ResearchContext，并保留 OI-PF-200 的“调用方回算结果非权威”双重重算边界。
    """
    try:
        request = final_candidate_request(payload)
        recompute = recompute_all(request.context)
        return CandidateFreezeService(store).freeze_final_candidate(
            request.context, request.run_id, source_commit, source_tree,
            recompute, request_payload=payload)
    except (CandidateRequestError, RecomputeError):
        raise
    except (ArithmeticError, KeyError, TypeError) as exc:
        raise CandidateRequestError(
            f"E-G6A-06-020: 最终候选输入无法完成确定性回算"
            f"（{type(exc).__name__}）") from exc
