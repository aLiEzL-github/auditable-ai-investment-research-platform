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
  2. candidate 明确含 `kind="candidate"` + 冻结 source commit / source tree；
     revision 由调用方**显式提供**并做严格 40 位小写十六进制校验，领域函数
     不隐式猜本地 git HEAD；source commit/tree 参与候选内容寻址身份。
  3. `load_candidate_bundle` / `verify_candidate_bundle` —— 从 ArtifactStore +
     candidate id 加载 candidate 与全部产品正文，并要求 candidate 的代码版本
     与调用方期望版本逐字一致；缺失、篡改、错键集或错代码版本稳定失败关闭。
  4. `freeze_final_candidate_from_payload` —— 受管 JSON 输入到 ResearchContext
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
from typing import Dict

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
    CandidateFreeze,
    OpenItemsPolicy,
    RecomputeError,
    RecomputeResult,
    ResearchContext,
    _assert_write_boundary,
    _frozen_approved_sha256,
    _prod_sha,
    _validate_recompute_binding,
    frozen_inputs_payload,
    recompute_all,
)
from schema_validate import SchemaError, validate_object
from valuation_engine import ValuationInputs

# 产品正文在对象库中的写入名（受 ArtifactStore NAME_RE 约束；digest 由正文决定）
PRODUCT_KIND = "recompute_product"

# 严格 40 位小写十六进制（git commit/tree SHA-1 形态）
SOURCE_REVISION_RE = re.compile(r"^[0-9a-f]{40}$")


class CandidateVerificationError(ValueError):
    """候选 bundle 复验失败 —— 任何缺失、篡改、错键集或错 revision 稳定失败关闭。"""


class CandidateRequestError(RecomputeError):
    """最终候选受管输入非法；不得以宽松默认值拼出 ResearchContext。"""


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
    """可复验的最终候选 bundle：candidate + 全部产品正文（内容寻址加载）。"""
    candidate_id: str
    candidate: dict
    products: Dict[str, dict]
    product_hashes: Dict[str, str]


@dataclass(frozen=True)
class FinalCandidateRequest:
    """已验证的最终候选请求；source revision 由外部 Git 边界另行提供。"""
    run_id: str
    context: ResearchContext


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
    root = _object(payload, "request", ("schema_version", "run_id", "context"))
    if root["schema_version"] != "1.0.0":
        raise CandidateRequestError(
            "E-G6A-06-020: request.schema_version 须为 '1.0.0'")
    run_id = _nonempty_string(root["run_id"], "request.run_id")
    body = _object(
        root["context"], "request.context",
        ("contract", "facts", "macro", "formula_specs", "valuation_inputs",
         "assumption_defaults", "approved_snapshot", "open_items_policy"))

    mappings = {}
    for key in ("contract", "facts", "macro", "formula_specs",
                "assumption_defaults"):
        mappings[key] = copy.deepcopy(_mapping(body[key], f"context.{key}"))

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
            approved=snapshot, open_items_policy=policy))


class CandidateFreezeService:
    """最终候选冻结服务（app 层，只写 ArtifactStore，不写任何 DB 对象）。"""

    def __init__(self, store: ArtifactStore):
        self.store = store

    # ── 权威冻结入口 ──────────────────────────────────────────────
    def freeze_final_candidate(self, ctx: ResearchContext, run_id: str,
                               source_commit: str, source_tree: str,
                               recompute: RecomputeResult) -> CandidateFreeze:
        """权威最终候选冻结。

        · source commit/tree 显式提供并严格校验（E-G6A-06-001），不猜 git HEAD。
        · OI-PF-200 保留：入口形态校验（E-G6A-05-003）→ 独立重算 canonical →
          逐项比对调用方回算结果（绑定哈希 E-G6A-05-005 / 键集·产物·哈希
          E-G6A-05-006）→ 冻结前写入边界漂移检查（E-G6A-05-007）。
          调用方结果**不是权威**，candidate 的 products/product_hashes/
          frozen_inputs_hash 仅来自 canonical。
        · 11 项产品正文逐项内容寻址写入 ArtifactStore；实际 digest 必须逐字等于
          `RecomputeResult.shas`（不相等即 E-G6A-06-002 失败关闭）。
        · candidate 含 `kind="candidate"` + source_commit/source_tree；
          source 字段参与候选内容寻址身份。
        · 失败（含写入边界漂移）绝不写 candidate；已写入的产品正文是孤儿，
          不构成完整 bundle（verify 会拒）。
        """
        if not isinstance(run_id, str) or not run_id.strip():
            raise RecomputeError(
                "E-G6A-06-001: run_id 须为非空字符串 —— 失败关闭")
        validate_source_revision(source_commit, source_tree)
        frozen_inputs_payload(ctx)                 # E-G6A-05-003 形态校验
        canonical = recompute_all(ctx)             # 独立 canonical 重算
        _validate_recompute_binding(recompute, canonical)   # E-G6A-05-005/006
        for name in PRODUCT_ORDER:                 # 产品正文逐项落库（内容寻址）
            data = canonical_bytes(canonical.products[name])
            digest = self.store.store(PRODUCT_KIND, data)
            if digest != canonical.shas[name]:
                raise RecomputeError(
                    f"E-G6A-06-002: 产品正文落库 digest {digest} ≠ 规范哈希 "
                    f"{canonical.shas[name]}（{name}）—— 失败关闭")
        candidate = {
            "schema_version": "1.0.0",
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

    # ── 可重载/可验证 bundle API ──────────────────────────────────
    @staticmethod
    def _parse_object(data: bytes, what: str) -> dict:
        try:
            obj = json.loads(data.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as exc:
            raise CandidateVerificationError(
                f"E-G6A-06-010: {what} 非 UTF-8/JSON —— 失败关闭") from exc
        if not isinstance(obj, dict):
            raise CandidateVerificationError(
                f"E-G6A-06-010: {what} 非 JSON 对象 —— 失败关闭")
        return obj

    def _verify_dict(self, candidate: dict, *, expected_source_commit: str,
                     expected_source_tree: str) -> dict:
        """对**反序列化后的候选字典**做 bundle 语义校验（无自身哈希）。

        供 load_candidate_bundle 复用；也作为独立可测的校验核心 —— 任何
        缺失（kind/source/product_hashes/products）、篡改、错键集或错
        revision 都稳定失败关闭（E-G6A-06-011~015）。
        """
        if candidate.get("schema_version") != "1.0.0":
            raise CandidateVerificationError(
                "E-G6A-06-011: candidate.schema_version ≠ '1.0.0' —— 失败关闭")
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
            except ValueError as exc:
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
        return {"candidate": candidate, "products": products,
                "product_hashes": dict(product_hashes)}

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
        except (TypeError, ValueError) as exc:
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
                               product_hashes=verified["product_hashes"])

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
            "product_count": len(b.products),
            "products": sorted(b.products),
        }


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
            request.context, request.run_id, source_commit, source_tree, recompute)
    except (CandidateRequestError, RecomputeError):
        raise
    except (ArithmeticError, KeyError, TypeError) as exc:
        raise CandidateRequestError(
            f"E-G6A-06-020: 最终候选输入无法完成确定性回算"
            f"（{type(exc).__name__}）") from exc
