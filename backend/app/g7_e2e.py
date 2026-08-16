"""g7_e2e.py —— G7-01 真实后端 E2E 运行时（三例离线合成 golden fixture）。

范围（严格限定 G7-01）：
  · 三例合成 golden E2E：POSITIVE / RESTATEMENT / WRONG_BASIS
  · 判定使用生产逻辑：`rules_engine.evaluate` + `RuleInput`、
    `research_router.validate_workflow_scope`、`rule_registry` 的 R01—R10
    登记元数据 —— 不是 TypeScript mock 的静态 Python 克隆
  · 确定性 contract→candidate **内存**结果：candidate 身份/闭包来自
    canonical 输入/输出字节（含冻结 source commit/tree），可复现；
    **绝不写** Approval / DecisionVersion / Release / CurrentPointer /
    latest / trade / 任何真实对象库
  · 仅经显式环境旗标 `G7_E2E_MODE=1` 启用；未知/缺失 golden case 或
    mutation 选择子一律失败关闭；普通生产模式不暴露合成端点
  · 合成 fixture 入仓带显式 synthetic 标记；内容不抄任何真实披露/研报

wrong_basis 判定（rules_engine.py 契约：scope/period/unit 与调用方契约
不符由**调用方预先判定**；本模块同时冻结 period_basis）：
  · fixture 顶层 `expected` 声明冻结的 scope/period/unit/period_basis；
    校验要求 expected 与 contract 一致
  · `_evaluate_rule` 在进入生产 `rules_engine.evaluate` **之前**比较
    规则声明的 scope/period/unit/single_quarter_or_cumulative 与冻结值，
    任何错配一律 FAIL —— 与生产引擎的 unit 机械检查一致，且不依赖
    算术是否凑巧一致（WRONG_BASIS R08 的 SINGLE-vs-ANNUAL 错配即此例）

约定错误码：
  E-G7-01-001 G7 端点未启用（普通生产模式 404，不暴露）
  E-G7-01-002 未知/缺失 golden case（含契约未精确匹配冻结 fixture）
  E-G7-01-003 未启动即读取（launch-before-read，失败关闭）
  E-G7-01-004 读端点默认拒绝任何入参
  E-G7-01-005 未知 mutation 选择子 / mutation 请求体含多余字段
   E-G7-01-006 预测状态/claim 错绑定（读取端失败关闭；资格重算并入阻断）
   E-G7-01-007 闭包不完整（阻断，不得冒充完整）
   E-G7-01-008 ResearchContract 非法（缺字段 / workflow/scope 不合法）
   E-G7-01-009 请求体非严格 JSON（含 NaN/Infinity 常量 / 嵌套过深 /
             Content-Length 畸形，失败关闭）
   E-G7-01-010 claim 材料性未分类（post-load 变异防御：审计门与
             release_eligible 共用同一谓词，不得各说各话）
   E-G7-01-011 候选记录完整性（completeness 门不再硬编码 PASS：记录组
             非空 / claim→evidence refs 可解析 / evidence 哈希仍匹配；
             审计门与 release_eligible 共用同一谓词，不得各说各话）
"""
import hashlib
import json
import math
import os
import re
from dataclasses import dataclass
from typing import Dict, List, Optional

from research_router import validate_workflow_scope
from rule_registry import BLOCKING, _RULE_DEFS
from rules_engine import (
    FAIL,
    PASS,
    RuleInput,
    evaluate,
)

# ── G7 E2E 显式启用旗标 ─────────────────────────────────────────────
G7_E2E_MODE_ENV = "G7_E2E_MODE"
G7_E2E_ON = "1"

# ── 合成 fixture 显式标记（与 G3-08 既有合成 golden 一致的标记域）─────
SYNTHETIC_MARKER = "SYNTHETIC_FIXTURE"
SCHEMA = "g7-01-golden/1.0"
CANDIDATE_SCHEMA = "g7-01-candidate/1.0"
SYNTHETIC_LOCATOR_PREFIX = "synthetic://"
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
ID_RE = re.compile(r"^[A-Z0-9][A-Z0-9_\-]{2,63}$")
DECIMAL_RE = re.compile(r"^-?\d+(\.\d+)?$")
NONNEG_DECIMAL_RE = re.compile(r"^\d+(\.\d+)?$")

# 冻结源码版本（G7-01 冻结提交/树）—— 参与候选内容寻址身份。
SOURCE_COMMIT = "0bb76a2c479c1208c76335e6e2ede8fffca878d3"
SOURCE_TREE = "0b743357f3483ccc3eb4b35621007e74df363d60"

G7_CASES = ("POSITIVE", "RESTATEMENT", "WRONG_BASIS")
RULE_IDS = tuple(rid for rid, *_ in _RULE_DEFS)          # R01…R10
RULE_META = {rid: (title, definition, version)
             for rid, title, definition, version, _ in _RULE_DEFS}

# 前端 prediction.schema 四态（逐字）
PREDICTION_STATES = ("REGISTERED", "DUE", "PENDING_DECISION", "UNDECIDABLE")

# 前端 types.ts 枚举（逐字）：Claim 状态/类别/材料性、OpenItem 状态。
CLAIM_STATUSES = ("DRAFT", "SUPPORTED", "DISPUTED")
CLAIM_CATEGORIES = ("F", "D", "A", "P", "C", "L")
CLAIM_MATERIALITIES = ("MATERIAL", "NON_MATERIAL", "UNCLASSIFIED")
OPEN_ITEM_STATUSES = ("OPEN", "CLOSED")

# post-load 变异防御代码：claim 材料性未分类（E-G7-01-010）。
MATERIALITY_UNCLASSIFIED_CODE = "E-G7-01-010"

# 候选记录完整性（E-G7-01-011）：审计 completeness 门与资格重算共用谓词。
RECORD_COMPLETENESS_CODE = "E-G7-01-011"

# ResearchContract 必填字段（与前端 types.ts 逐字）；load 期要求**精确**
# 键集 —— 多余字段不得冒充冻结契约（E-G7-01-002 失败关闭）。
CONTRACT_FIELDS = ("scope", "period", "unit", "vintage", "snapshot",
                   "security_code", "company_id", "as_of", "version",
                   "workflow")

# fixture 顶层冻结 expected（wrong_basis 判定的调用方基准）；三例均为
# 年度报告例（VD-21 显式指定），period_basis 必须恒为 ANNUAL。
EXPECTED_FIELDS = ("scope", "period", "unit", "period_basis")

# 决不允许出现在候选/视图中的字段（本模块绝不写发布/持久化形状）
FORBIDDEN_TOP_LEVEL_FIELDS = (
    "Approval", "DecisionVersion", "Release", "CurrentPointer",
    "latest", "trade",
)

DEFAULT_FIXTURES_DIR = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "tests", "fixtures",
    "g7-01")


class G7E2EError(ValueError):
    """G7 E2E 运行时错误基类 —— 一切失败关闭路径的载体。"""


class UnknownGoldenCase(G7E2EError):
    pass


class NotLaunched(G7E2EError):
    pass


class MutationDenied(G7E2EError):
    pass


class G7BindingError(G7E2EError):
    pass


class GoldenCaseInvalid(G7E2EError):
    pass


# ── strict JSON（拒绝 NaN/Infinity 常量，失败关闭）───────────────────
def _reject_json_constant(token: str) -> None:
    raise ValueError(f"非标准 JSON 常量 {token!r}")


def _strict_json(data: bytes, what: str, code: str) -> dict:
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise GoldenCaseInvalid(
            f"{code}: {what} 非 UTF-8 —— 失败关闭") from exc
    try:
        obj = json.loads(text, parse_constant=_reject_json_constant)
    except RecursionError as exc:
        # 深层嵌套 JSON 会让 json.loads 抛 RecursionError —— 必须归一为
        # 受控 strict-JSON 错误，不得把裸 RecursionError 顶到 HTTP 层。
        raise GoldenCaseInvalid(
            f"{code}: {what} 嵌套过深（RecursionError）—— 失败关闭") from exc
    except ValueError as exc:
        raise GoldenCaseInvalid(
            f"{code}: {what} 非严格 JSON（含 NaN/Infinity 或解析失败："
            f"{exc}）—— 失败关闭") from exc
    if not isinstance(obj, dict):
        raise GoldenCaseInvalid(f"{code}: {what} 非 JSON object —— 失败关闭")
    return obj


def canonical_bytes(obj: dict) -> bytes:
    """跨进程可复现的 canonical 字节（sort_keys + 紧凑 + allow_nan=False）。"""
    return json.dumps(obj, sort_keys=True, ensure_ascii=False,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# ── 预测绑定/状态集中校验（E-G7-01-006 单一判定点）──────────────────
def _prediction_binding_errors(core: dict) -> List[str]:
    """非抛出版：收集预测绑定/状态错误（供资格重算并入阻断）。"""
    known_claims = {c["id"] for c in core["claims"]}
    errs: List[str] = []
    for p in core["predictions"]:
        if p["status"] not in PREDICTION_STATES:
            errs.append(
                f"E-G7-01-006: 预测 {p['id']} 状态 {p['status']!r} 不在"
                f" {PREDICTION_STATES} —— 失败关闭")
        if p["claim_id"] not in known_claims:
            errs.append(
                f"E-G7-01-006: 预测 {p['id']} 绑定到未知 claim "
                f"{p['claim_id']!r} —— 失败关闭")
    return errs


def _validate_predictions(core: dict) -> None:
    """抛出版：预测读取端失败关闭 —— 不返回部分数据。"""
    errs = _prediction_binding_errors(core)
    if errs:
        raise G7BindingError(errs[0])


# ── golden case 加载与校验 ──────────────────────────────────────────
@dataclass(frozen=True)
class GoldenCase:
    case_id: str            # 如 "G7-01-POSITIVE"
    g7_case: str            # POSITIVE / RESTATEMENT / WRONG_BASIS
    contract: dict
    expected: dict          # 冻结 scope/period/unit/period_basis
    rules: Dict[str, dict]  # rule_id -> RuleInput 字段（含 values）
    predictions: List[dict]
    claims: List[dict]
    evidence: List[dict]
    facts: List[dict]
    open_items: List[dict]
    checked_at: str


def _synthetic_locator(value) -> bool:
    return isinstance(value, str) and value.startswith(SYNTHETIC_LOCATOR_PREFIX)


def _validated_contract(obj: dict, case_id: str, code: str) -> dict:
    contract = obj.get("contract")
    if not isinstance(contract, dict):
        raise GoldenCaseInvalid(f"{code}: {case_id} 缺 contract object")
    if set(contract) != set(CONTRACT_FIELDS):
        raise GoldenCaseInvalid(
            f"{code}: {case_id} contract 键集 {sorted(contract)} ≠ "
            f"{sorted(CONTRACT_FIELDS)} —— 失败关闭（多余字段不得冒充冻结契约）")
    for key in CONTRACT_FIELDS:
        if not isinstance(contract.get(key), str) or not contract[key].strip():
            raise GoldenCaseInvalid(
                f"{code}: {case_id} contract.{key} 缺失或为空 —— 失败关闭")
    return dict(contract)


def _validated_expected(obj: dict, case_id: str, contract: dict,
                        code: str) -> dict:
    exp = obj.get("expected")
    if not isinstance(exp, dict):
        raise GoldenCaseInvalid(
            f"{code}: {case_id} 缺 expected object（wrong_basis 冻结基准）")
    if set(exp) != set(EXPECTED_FIELDS):
        raise GoldenCaseInvalid(
            f"{code}: {case_id} expected 键集 {sorted(exp)} ≠ "
            f"{sorted(EXPECTED_FIELDS)} —— 失败关闭")
    for key in EXPECTED_FIELDS:
        if not isinstance(exp.get(key), str) or not exp[key].strip():
            raise GoldenCaseInvalid(
                f"{code}: {case_id} expected.{key} 缺失或为空 —— 失败关闭")
    if exp["period_basis"] != "ANNUAL":
        raise GoldenCaseInvalid(
            f"{code}: {case_id} expected.period_basis {exp['period_basis']!r} "
            "≠ ANNUAL —— 失败关闭（VD-21 只冻结年度报告例）")
    for key in ("scope", "period", "unit"):
        if exp[key] != contract[key]:
            raise GoldenCaseInvalid(
                f"{code}: {case_id} expected.{key} {exp[key]!r} ≠ "
                f"contract.{key} {contract[key]!r} —— 失败关闭")
    return dict(exp)


_COMMON_RULE_FIELDS = (
    "scope", "period", "instant_or_duration",
    "single_quarter_or_cumulative", "original_or_restated",
    "unit", "source_precision", "applicability_predicate",
    "absolute_tolerance", "relative_tolerance", "allowed_residual",
    "failure_impact", "locator")

# 每条规则 values 的键契约：numeric 为有限 Decimal 字符串；text 为显式
# 允许值（rules_engine 唯一消费的两个文本字段）。键集必须**精确匹配**
# —— 多余键/缺失键不得冒充冻结契约（E-G7-01-002 失败关闭）。
_RULE_VALUES = {
    "R01": {"numeric": ("merged_revenue", "segment_external_revenue",
                        "segment_intercompany_revenue", "eliminations"),
            "text": {}},
    "R02": {"numeric": ("net_profit", "parent_net_profit", "minority_profit"),
            "text": {}},
    "R03": {"numeric": ("cash_net_increase", "ocf", "icf", "fcf",
                        "fx_effect"),
            "text": {}},
    "R04": {"numeric": ("ocf", "net_profit", "non_cash_items",
                        "working_capital_changes", "other_adjustments"),
            "text": {}},
    "R05": {"numeric": ("ending_equity", "beginning_equity",
                        "comprehensive_income",
                        "owner_contributions_distributions",
                        "share_based_payment", "m_and_a_effects",
                        "other_changes"),
            "text": {}},
    "R06": {"numeric": ("total_assets", "total_liabilities", "total_equity"),
            "text": {}},
    "R07": {"numeric": ("parent_net_profit", "parent_non_recurring_gain_loss",
                        "non_gang_parent_net_profit"),
            "text": {}},
    "R08": {"numeric": ("merged_profit", "segment_profit_sum",
                        "segment_eliminations"),
            "text": {"segment_measurement_basis": ("", "COMPARABLE")}},
    "R09": {"numeric": ("parent_assets", "subsidiary_assets",
                        "intercompany_eliminations", "consolidated_assets"),
            "text": {}},
    "R10": {"numeric": ("this_period_beginning", "prior_period_ending"),
            "text": {"restatement_pending": ("", "PENDING")}},
}

# 共同字段的常见基准枚举（rules_engine 机械消费；错配即 wrong_basis）。
_RULE_ENUMS = {
    "instant_or_duration": ("INSTANT", "DURATION"),
    "single_quarter_or_cumulative": ("SINGLE", "CUMULATIVE", "ANNUAL"),
    "original_or_restated": ("ORIGINAL", "RESTATED"),
    "failure_impact": ("BLOCKING", "NON_BLOCKING"),
}

_RULE_TOLERANCE_FIELDS = ("absolute_tolerance", "relative_tolerance",
                          "allowed_residual")


def _validated_rule_values(values, rid: str, case_id: str,
                           code: str) -> dict:
    """校验单条规则的 values 键集与取值形状（load 期失败关闭）。

    · 每个键值都须是字符串
    · numeric 键须为有限 Decimal 字符串形状（DECIMAL_RE）
    · 仅已知文本字段（segment_measurement_basis / restatement_pending）
      可非数值，且须取显式允许值 —— 畸形 fixture 在 load 期即
      E-G7-01-002 判红，不会拖到 rules_engine 里才裸抛 RuleEngineError
    """
    if not isinstance(values, dict):
        raise GoldenCaseInvalid(
            f"{code}: {case_id} rules.{rid}.values 须为 object")
    vdef = _RULE_VALUES.get(rid)
    if vdef is None:
        raise GoldenCaseInvalid(
            f"{code}: {case_id} rules.{rid} 无 values 键契约 —— 失败关闭")
    expected = set(vdef["numeric"]) | set(vdef["text"])
    if set(values) != expected:
        raise GoldenCaseInvalid(
            f"{code}: {case_id} rules.{rid}.values 键集 {sorted(values)} ≠ "
            f"{sorted(expected)} —— 失败关闭")
    out: Dict[str, str] = {}
    for key in vdef["numeric"]:
        v = values[key]
        if not isinstance(v, str):
            raise GoldenCaseInvalid(
                f"{code}: {case_id} rules.{rid}.values.{key} 非字符串"
                " —— 失败关闭")
        if not DECIMAL_RE.fullmatch(v):
            raise GoldenCaseInvalid(
                f"{code}: {case_id} rules.{rid}.values.{key} {v!r} 非有限"
                " Decimal 字符串 —— 失败关闭")
        out[key] = v
    for key, allowed in vdef["text"].items():
        v = values[key]
        if not isinstance(v, str):
            raise GoldenCaseInvalid(
                f"{code}: {case_id} rules.{rid}.values.{key} 非字符串"
                " —— 失败关闭")
        if v not in allowed:
            raise GoldenCaseInvalid(
                f"{code}: {case_id} rules.{rid}.values.{key} {v!r} 不在 "
                f"{allowed} —— 失败关闭")
        out[key] = v
    return out


def _validated_rules(obj: dict, case_id: str, code: str) -> Dict[str, dict]:
    raw = obj.get("rules")
    if not isinstance(raw, dict):
        raise GoldenCaseInvalid(f"{code}: {case_id} 缺 rules object")
    if sorted(raw) != list(RULE_IDS):
        raise GoldenCaseInvalid(
            f"{code}: {case_id} rules 键集 ≠ R01—R10 —— 失败关闭")
    rules: Dict[str, dict] = {}
    for rid in RULE_IDS:
        spec = raw[rid]
        if not isinstance(spec, dict):
            raise GoldenCaseInvalid(f"{code}: {case_id} rules.{rid} 非 object")
        for key in _COMMON_RULE_FIELDS:
            if not isinstance(spec.get(key), str):
                raise GoldenCaseInvalid(
                    f"{code}: {case_id} rules.{rid}.{key} 缺失 —— 失败关闭")
        # scope/period/basis 必须显式声明（wrong_basis 判定需要比较基准）
        for key in ("scope", "period", "single_quarter_or_cumulative",
                    "instant_or_duration", "original_or_restated",
                    "source_precision", "applicability_predicate",
                    "failure_impact"):
            if not spec[key].strip():
                raise GoldenCaseInvalid(
                    f"{code}: {case_id} rules.{rid}.{key} 为空 —— 失败关闭")
        # 容差字段须为有限非负 Decimal 字符串（allowed_error 直接 Decimal）
        for key in _RULE_TOLERANCE_FIELDS:
            if not NONNEG_DECIMAL_RE.fullmatch(spec[key]):
                raise GoldenCaseInvalid(
                    f"{code}: {case_id} rules.{rid}.{key} {spec[key]!r} 非"
                    "有限非负 Decimal 字符串 —— 失败关闭")
        # 常见基准枚举（rules_engine 机械消费）
        for key, allowed in _RULE_ENUMS.items():
            if spec[key] not in allowed:
                raise GoldenCaseInvalid(
                    f"{code}: {case_id} rules.{rid}.{key} {spec[key]!r} 不在 "
                    f"{allowed} —— 失败关闭")
        if not (spec["applicability_predicate"].startswith("APPLICABLE")
                or spec["applicability_predicate"].startswith(
                    "NOT_APPLICABLE")):
            raise GoldenCaseInvalid(
                f"{code}: {case_id} rules.{rid}.applicability_predicate "
                f"{spec['applicability_predicate']!r} 非 APPLICABLE/"
                "NOT_APPLICABLE 前缀 —— 失败关闭")
        if not _synthetic_locator(spec["locator"]):
            raise GoldenCaseInvalid(
                f"{code}: {case_id} rules.{rid}.locator 非合成 locator"
                " —— 失败关闭")
        values = _validated_rule_values(spec.get("values"), rid, case_id, code)
        common = {k: spec.get(k) for k in _COMMON_RULE_FIELDS}
        common["values"] = values
        rules[rid] = common
    return rules


def _validated_list(obj: dict, key: str, case_id: str, code: str,
                    required: tuple) -> List[dict]:
    items = obj.get(key)
    if not isinstance(items, list):
        raise GoldenCaseInvalid(f"{code}: {case_id} {key} 须为数组")
    out = []
    for i, item in enumerate(items):
        if not isinstance(item, dict):
            raise GoldenCaseInvalid(
                f"{code}: {case_id} {key}[{i}] 非 object —— 失败关闭")
        missing = sorted(set(required) - set(item))
        if missing:
            raise GoldenCaseInvalid(
                f"{code}: {case_id} {key}[{i}] 缺字段 {missing} —— 失败关闭")
        out.append(dict(item))
    return out


def _validate_integrity(case_id: str, code: str, *, predictions: List[dict],
                        claims: List[dict], evidence: List[dict],
                        facts: List[dict], open_items: List[dict]) -> None:
    """合成 fixture 的完整性校验：结构形状 / 哈希 / 引用可解析 / ID 唯一 /
    预测绑定合法 / locator 合成形态 / 枚举与布尔形状。任何一条失败即
    E-G7-01-002 判红（入仓畸形 fixture 一律在 load 期失败关闭）。"""
    def bad(msg: str) -> None:
        raise GoldenCaseInvalid(f"{code}: {case_id} {msg} —— 失败关闭")

    def nonempty(item: dict, key: str, what: str) -> str:
        v = item.get(key)
        if not isinstance(v, str) or not v.strip():
            bad(f"{what}.{key} 缺失或为空")
        return v

    def ident(item: dict, key: str, what: str) -> str:
        v = nonempty(item, key, what)
        if not ID_RE.fullmatch(v):
            bad(f"{what}.{key} {v!r} 非合法 ID 形状")
        return v

    # 证据：id/版本/字符串字段形状 + sha256==content 哈希（原有检查保留）
    ev_ids = set()
    for ev in evidence:
        label = f"evidence {ev.get('id')}"
        ident(ev, "id", label)
        ident(ev, "artifact_id", label)
        ident(ev, "snapshot_id", label)
        for key in ("schema_version", "schema_ver", "parser_version"):
            nonempty(ev, key, label)
        if not isinstance(ev["sha256"], str) or not SHA256_RE.fullmatch(
                ev["sha256"]):
            bad(f"{label}.sha256 非 64 位 hex")
        if not isinstance(ev["content"], str):
            bad(f"{label}.content 非字符串")
        if ev["sha256"] != _sha256(ev["content"].encode("utf-8")):
            bad(f"{label}.sha256 ≠ content UTF-8 哈希")
        ev_ids.add(ev["id"])

    # claims：refs 非空唯一字符串并可解析 / 枚举 / id / 字符串字段形状
    for c in claims:
        label = f"claim {c.get('id')}"
        ident(c, "id", label)
        nonempty(c, "schema_version", label)
        nonempty(c, "statement", label)
        if c.get("status") not in CLAIM_STATUSES:
            bad(f"{label}.status {c.get('status')!r} 不在 {CLAIM_STATUSES}"
                " —— 失败关闭")
        if c.get("category") not in CLAIM_CATEGORIES:
            bad(f"{label}.category {c.get('category')!r} 不在 "
                f"{CLAIM_CATEGORIES} —— 失败关闭")
        if c.get("materiality") not in CLAIM_MATERIALITIES:
            bad(f"{label}.materiality {c.get('materiality')!r} 不在 "
                f"{CLAIM_MATERIALITIES} —— 失败关闭")
        refs = c.get("refs")
        if not isinstance(refs, list) or not refs:
            bad(f"{label}.refs 须为非空数组")
        # 先逐项判字符串形状，再做重复检查 —— set(refs) 遇不可哈希元素会
        # 裸抛 TypeError，必须先归一为 GoldenCaseInvalid（失败关闭）。
        for ref in refs:
            if not isinstance(ref, str) or not ref.strip():
                bad(f"{label}.refs 含非字符串/空项")
            if ref not in ev_ids:
                bad(f"{label}.refs 悬空 {ref!r}（无对应 evidence）")
        if len(set(refs)) != len(refs):
            bad(f"{label}.refs 含重复项 —— 失败关闭")

    # predictions：probability 有限数值 [0,1]（布尔不接受）、
    # calibration_pending 纯布尔、时间戳/字符串字段非空
    for p in predictions:
        label = f"prediction {p.get('id')}"
        ident(p, "id", label)
        ident(p, "claim_id", label)
        for key in ("horizon", "registered_at"):
            nonempty(p, key, label)
        prob = p.get("probability")
        if isinstance(prob, bool) or not isinstance(prob, (int, float)):
            bad(f"{label}.probability 须为有限数值（不接受布尔）")
        if not math.isfinite(prob) or not 0 <= prob <= 1:
            bad(f"{label}.probability {prob!r} 须为 [0,1] 内有限数值"
                " —— 失败关闭")
        if not isinstance(p.get("calibration_pending"), bool):
            bad(f"{label}.calibration_pending 须为布尔")

    # facts：版本/字符串字段形状 + Decimal 数值 + 合成 locator（原检查保留）
    for f in facts:
        label = f"fact {f.get('id')}"
        ident(f, "id", label)
        ident(f, "artifact_id", label)
        for key in ("schema_version", "parser_version", "metric", "unit",
                    "period", "scope", "basis", "vintage"):
            nonempty(f, key, label)
        if not isinstance(f.get("value"), str) or not DECIMAL_RE.fullmatch(
                f["value"]):
            bad(f"{label}.value 非 Decimal 字符串")
        if not _synthetic_locator(f["locator"]):
            bad(f"{label}.locator 非合成 locator")

    # open_items：status 仅 OPEN/CLOSED、material 纯布尔、
    # blocks 唯一非空字符串列表
    for o in open_items:
        label = f"open_item {o.get('id')}"
        ident(o, "id", label)
        nonempty(o, "title", label)
        if o.get("status") not in OPEN_ITEM_STATUSES:
            bad(f"{label}.status {o.get('status')!r} 不在 {OPEN_ITEM_STATUSES}"
                " —— 失败关闭（材料性开放项不得用未知状态绕过阻断）")
        if not isinstance(o.get("material"), bool):
            bad(f"{label}.material 须为布尔")
        blocks = o.get("blocks")
        if not isinstance(blocks, list):
            bad(f"{label}.blocks 须为数组")
        # 先逐项判字符串形状，再做重复检查（不可哈希元素不得裸抛 TypeError）。
        for b in blocks:
            if not isinstance(b, str) or not b.strip():
                bad(f"{label}.blocks 含非字符串/空项")
        if len(set(blocks)) != len(blocks):
            bad(f"{label}.blocks 含重复项 —— 失败关闭")

    # ID 全局唯一（跨五类对象）
    seen: Dict[str, str] = {}
    for group_key, group in (("predictions", predictions),
                             ("claims", claims),
                             ("evidence", evidence),
                             ("facts", facts),
                             ("open_items", open_items)):
        for item in group:
            ident = item.get("id")
            if ident in seen:
                bad(f"重复 ID {ident!r}（{seen[ident]} 与 {group_key}）")
            seen[ident] = group_key

    # 预测绑定/状态必须在加载期即合法（读取端同样失败关闭）
    pred_errs = _prediction_binding_errors(
        {"claims": claims, "predictions": predictions})
    if pred_errs:
        bad(pred_errs[0])


def load_golden_case(case_id: str, fixtures_dir: Optional[str] = None
                     ) -> GoldenCase:
    """加载并严格校验一个合成 golden case。未知 case / 缺失文件 / 畸形
    结构一律 E-G7-01-002 失败关闭。"""
    code = "E-G7-01-002"
    base = fixtures_dir or os.environ.get("G7_FIXTURES_DIR") \
        or DEFAULT_FIXTURES_DIR
    path = os.path.join(base, f"{case_id}.json")
    if not os.path.isfile(path):
        raise UnknownGoldenCase(
            f"{code}: 未知/缺失 golden case {case_id!r}"
            f"（{path} 不存在）—— 失败关闭")
    try:
        with open(path, "rb") as f:
            data = f.read()
    except OSError as exc:
        raise UnknownGoldenCase(
            f"{code}: golden case {case_id!r} 不可读（{exc}）—— 失败关闭") \
            from exc
    obj = _strict_json(data, f"golden case {case_id}", code)
    if obj.get("schema") != SCHEMA:
        raise GoldenCaseInvalid(
            f"{code}: {case_id}.schema ≠ {SCHEMA!r} —— 失败关闭")
    if obj.get("synthetic") is not True:
        raise GoldenCaseInvalid(
            f"{code}: {case_id} 缺 synthetic=true 标记 —— 失败关闭")
    if obj.get("SYNTHETIC_FIXTURE") is not True:
        raise GoldenCaseInvalid(
            f"{code}: {case_id} 缺 {SYNTHETIC_MARKER} 标记 —— 失败关闭")
    if obj.get("case_id") != case_id:
        raise GoldenCaseInvalid(
            f"{code}: 文件 case_id {obj.get('case_id')!r} ≠ {case_id!r}")
    if not _synthetic_locator(obj.get("source")):
        raise GoldenCaseInvalid(
            f"{code}: {case_id}.source 非合成 locator —— 失败关闭")
    g7_case = obj.get("g7_case")
    if g7_case not in G7_CASES:
        raise GoldenCaseInvalid(
            f"{code}: {case_id} g7_case {g7_case!r} 不在 {G7_CASES}")
    contract = _validated_contract(obj, case_id, code)
    expected = _validated_expected(obj, case_id, contract, code)
    rules = _validated_rules(obj, case_id, code)
    predictions = _validated_list(
        obj, "predictions", case_id, code,
        ("id", "claim_id", "horizon", "probability",
         "calibration_pending", "registered_at", "status"))
    claims = _validated_list(obj, "claims", case_id, code,
                             ("id", "schema_version", "statement", "refs",
                              "status", "category", "materiality"))
    evidence = _validated_list(obj, "evidence", case_id, code,
                               ("id", "schema_version", "artifact_id",
                                "snapshot_id", "schema_ver", "parser_version",
                                "sha256", "content"))
    facts = _validated_list(obj, "facts", case_id, code,
                            ("id", "schema_version", "artifact_id", "metric",
                             "value", "unit", "period", "scope", "basis",
                             "vintage", "locator", "parser_version"))
    open_items = _validated_list(obj, "open_items", case_id, code,
                                 ("id", "title", "status", "material",
                                  "blocks"))
    checked_at = obj.get("checked_at")
    if not isinstance(checked_at, str) or not checked_at:
        raise GoldenCaseInvalid(
            f"{code}: {case_id} 缺确定性 checked_at —— 失败关闭")
    _validate_integrity(case_id, code, predictions=predictions,
                        claims=claims, evidence=evidence, facts=facts,
                        open_items=open_items)
    return GoldenCase(case_id=case_id, g7_case=g7_case, contract=contract,
                      expected=expected, rules=rules, predictions=predictions,
                      claims=claims, evidence=evidence, facts=facts,
                      open_items=open_items, checked_at=checked_at)


def resolve_case(contract: dict, fixtures_dir: Optional[str] = None
                 ) -> GoldenCase:
    """按契约**精确**解析 golden case：契约须与冻结 fixture 的 contract
    逐键一致（含键集，不允许多余字段）。仅同 scope 不算匹配 —— 启动
    契约必须精确对应选定 fixture，否则 E-G7-01-002 失败关闭。"""
    for selector in G7_CASES:
        try:
            case = load_golden_case(f"G7-01-{selector}", fixtures_dir)
        except UnknownGoldenCase:
            continue
        if case.contract == contract:
            return case
    raise UnknownGoldenCase(
        "E-G7-01-002: 未知 golden case（contract 未精确匹配任一冻结"
        f" fixture：{sorted(contract)}） —— 失败关闭，不允许无对应"
        "合成例的启动")


# ── 确定性 contract→candidate ──────────────────────────────────────
def _evaluate_rule(rule_id: str, spec: dict, contract: dict,
                   expected: dict) -> dict:
    """单条规则的**生产判定**：
      · wrong_basis（scope/period/unit/period_basis 与冻结值不符）由调用方
        预先判定（rules_engine.py 契约）→ FAIL —— **不依赖算术凑巧一致**
      · 其余走生产 rules_engine.evaluate（含 unit 机械检查与 R01—R10 逻辑）
    """
    locator = spec["locator"]
    applicable = spec.get("applicability_predicate", "").startswith("APPLICABLE")
    inputs = sorted(spec.get("values") or {})
    mismatches = []
    if spec.get("scope") != contract.get("scope"):
        mismatches.append(f"scope {spec.get('scope')!r} ≠ "
                          f"契约 {contract.get('scope')!r}")
    if spec.get("period") != expected["period"]:
        mismatches.append(f"period {spec.get('period')!r} ≠ "
                          f"冻结 {expected['period']!r}")
    if spec.get("unit") != expected["unit"]:
        mismatches.append(f"unit {spec.get('unit')!r} ≠ "
                          f"冻结 {expected['unit']!r}")
    if spec.get("single_quarter_or_cumulative") != expected["period_basis"]:
        mismatches.append(f"period_basis {spec.get('single_quarter_or_cumulative')!r}"
                          f" ≠ 冻结 {expected['period_basis']!r}")
    if mismatches:
        return {"rule_id": rule_id, "status": FAIL, "residual": "0",
                "detail": "wrong_basis：" + "；".join(mismatches)
                          + " —— 失败关闭",
                "locator": locator, "applicable": applicable, "inputs": inputs}
    try:
        inp = RuleInput(**{k: v for k, v in spec.items()
                           if k in RuleInput.__dataclass_fields__})
    except (TypeError, ValueError) as exc:
        raise G7BindingError(
            f"E-G7-01-006: 规则 {rule_id} RuleInput 构造失败（{exc}）"
            " —— 失败关闭") from exc
    try:
        result = evaluate(rule_id, inp)
    except Exception as exc:
        # 纵深防御：生产判定任何异常（如 RuleEngineError）一律归一为受控
        # G7BindingError —— load 校验已挡住畸形 fixture，但 post-load
        # 变异/引擎内部失败不得断开 HTTP 服务器（受控错误码而非裸 traceback）。
        raise G7BindingError(
            f"E-G7-01-006: 规则 {rule_id} 生产判定失败（{exc}）"
            " —— 失败关闭") from exc
    result["applicable"] = applicable
    result["inputs"] = inputs
    return result


def _closure_objects(candidate_core: dict, contract: dict) -> List[dict]:
    """闭包对象从 canonical 字节确定性派生（内容寻址）。"""
    core_bytes = canonical_bytes(candidate_core)
    contract_bytes = canonical_bytes({"contract": contract})
    rule_bytes = canonical_bytes(candidate_core["rule_results"])
    pred_bytes = canonical_bytes(
        {"predictions": candidate_core["predictions"]})
    ev_bytes = canonical_bytes({"evidence": candidate_core["evidence"]})
    return [
        {"id": _sha256(core_bytes), "kind": "candidate",
         "sha256": _sha256(core_bytes)},
        {"id": _sha256(contract_bytes), "kind": "research_contract",
         "sha256": _sha256(contract_bytes)},
        {"id": _sha256(rule_bytes), "kind": "rule_report",
         "sha256": _sha256(rule_bytes)},
        {"id": _sha256(pred_bytes), "kind": "prediction_bundle",
         "sha256": _sha256(pred_bytes)},
        {"id": _sha256(ev_bytes), "kind": "evidence_artifact",
         "sha256": _sha256(ev_bytes)},
    ]


def _materiality_unclassified_errors(core: dict) -> List[str]:
    """claim 材料性分类谓词（审计门与资格重算**共用单源**）。

    夹具 schema 允许 UNCLASSIFIED 枚举，但审计门与 release_eligible 只认
    MATERIAL/NON_MATERIAL 为已分类。post-load 变异把核心 claim 改成未知/
    未分类材料性时，若只有审计门判 FAIL 而 release_eligible 仍 True，
    两者就各说各话 —— 本谓词保证二者同步阻断（E-G7-01-010）。
    """
    unclassified = [c["id"] for c in core["claims"]
                    if c.get("materiality") not in
                    ("MATERIAL", "NON_MATERIAL")]
    return [f"claim {cid} 材料性未分类 —— 阻断（材料性分类门）"
            for cid in unclassified]


def _record_completeness_errors(core: dict) -> List[str]:
    """候选记录完整性谓词（审计 completeness 门与资格重算**共用单源**）。

    G7-01 终返工：completeness 门不再硬编码 PASS。post-load 变异删除证据 /
    制造悬空 ref / 篡改 evidence 内容后，本谓词抓出漂移：
      · 记录组非空：claims / evidence / facts / predictions 均须非空
      · claim→evidence refs 全部可解析（evidence 的 id 集合）
      · evidence.content 的 sha256 仍匹配（哈希漂移判红）
    返回错误清单（非抛出版）。闭包是独立的 content-addressed 门
    （E-G7-01-007），本谓词不触碰闭包对象。
    """
    errs: List[str] = []
    if not isinstance(core, dict):
        return ["candidate core 非对象 —— 完整性阻断"]
    for group in ("claims", "evidence", "facts", "predictions"):
        items = core.get(group)
        if not isinstance(items, list) or not items:
            errs.append(f"记录组 {group} 为空 —— 完整性阻断")
    ev_ids = {ev["id"] for ev in core.get("evidence") or ()
              if isinstance(ev, dict) and isinstance(ev.get("id"), str)}
    for c in core.get("claims") or ():
        if not isinstance(c, dict):
            continue
        cid = c.get("id")
        for ref in c.get("refs") or ():
            if not isinstance(ref, str) or ref not in ev_ids:
                errs.append(f"claim {cid} ref {ref!r} 悬空 —— 完整性阻断")
    for ev in core.get("evidence") or ():
        if not isinstance(ev, dict):
            continue
        sha, content = ev.get("sha256"), ev.get("content")
        if isinstance(sha, str) and isinstance(content, str) and \
                sha != _sha256(content.encode("utf-8")):
            errs.append(f"evidence {ev.get('id')} sha256 ≠ content 哈希"
                        " —— 完整性阻断")
    return errs


def _recompute_eligibility(candidate: dict) -> None:
    """从当前规则状态/开放项/闭包/预测绑定/材料性分类**重算**候选阻断面
    与发布资格（单源）。

    mutation 改变闭包后必须重算 —— 否则 audit/eligibility 会沿用启动时
    的旧值（变异注入测试将抓出）。规则/开放项/闭包/预测绑定/材料性分类/
    记录完整性六者任一阻断即 release_eligible=False；**材料性开放项仅在
    状态 OPEN 时阻断**，且必须产出可见原因。
    """
    core = candidate["core"]
    blocking = [rid for rid, r in core["rule_results"].items()
                if r["status"] in BLOCKING]
    material_open = [o for o in core["open_items"]
                     if o.get("material") and o.get("status") == "OPEN"]
    complete = candidate["closure"]["complete"]
    pred_errors = _prediction_binding_errors(core)
    mat_errors = _materiality_unclassified_errors(core)
    record_errors = _record_completeness_errors(core)
    failures: List[dict] = []
    for rid in blocking:
        r = core["rule_results"][rid]
        failures.append({"code": f"E-G7-01-{rid}",
                         "detail": f"{rid}={r['status']} —— {r['detail']}"})
    for o in material_open:
        failures.append({
            "code": f"OPEN_ITEM:{o['id']}",
            "detail": f"材料性开放项 {o['id']} 状态 {o.get('status')} —— "
                      "阻断（材料性醒目标识，E-G3-14）"})
    if not complete:
        failures.append({"code": "E-G7-01-007",
                         "detail": "闭包不完整 —— 缺对象不得冒充完整复验（D-10）"})
    if pred_errors:
        failures.append({"code": "E-G7-01-006", "detail": pred_errors[0]})
    if mat_errors:
        failures.append({"code": MATERIALITY_UNCLASSIFIED_CODE,
                         "detail": mat_errors[0]})
    if record_errors:
        failures.append({"code": RECORD_COMPLETENESS_CODE,
                         "detail": f"{RECORD_COMPLETENESS_CODE}: "
                                   f"{record_errors[0]}"})
    candidate["failures"] = failures
    candidate["release_eligible"] = (
        not blocking and not material_open and complete and not pred_errors
        and not mat_errors and not record_errors)


def _rebuild_identity(candidate: dict) -> dict:
    """从当前 core（可已被 mutation 改写）与 mutations 重建身份与闭包。

    候选身份始终 = sha256(当前 canonical 字节)：misbind_prediction 改变
    core 后必须重建 candidate_id，drop_closure_object 只移除闭包对象
    （身份不变、闭包变不完整）。保证「身份/闭包来自 canonical 字节」
    在任何 mutation 之后都成立。
    """
    core = candidate["core"]
    closure = _closure_objects(core, core["contract"])
    candidate_id = _sha256(canonical_bytes(core))
    if "drop_closure_object" in candidate["mutations"]:
        closure = closure[:-1]                       # 缺最后一件
    complete = len(closure) == 5
    candidate["candidate_id"] = candidate_id
    candidate["closure"] = {
        "subject_root": candidate_id,
        "complete": complete,
        "count": len(closure),
        "dangling": 0 if complete else 1,
        "objects": closure,
    }
    _recompute_eligibility(candidate)
    return candidate


def build_candidate(case: GoldenCase, contract: dict,
                    *, mutations: Optional[List[str]] = None) -> dict:
    """确定性 contract→candidate（纯内存，零持久化）。

    candidate 身份 = sha256(canonical 输入/输出字节)，含 source commit/tree
    与契约，可复现；任何 mutation 改变 canonical 字节即改变身份。
    """
    mutations = mutations or []
    rule_results: Dict[str, dict] = {}
    for rid in RULE_IDS:
        rule_results[rid] = _evaluate_rule(
            rid, case.rules[rid], contract, case.expected)

    open_items = [dict(o) for o in case.open_items]

    core = {
        "schema": CANDIDATE_SCHEMA,
        "kind": "g7-01-candidate",
        "case_id": case.case_id,
        "g7_case": case.g7_case,
        "source_commit": SOURCE_COMMIT,
        "source_tree": SOURCE_TREE,
        "contract": dict(contract),
        "rule_results": rule_results,
        "open_items": open_items,
        "claims": [dict(c) for c in case.claims],
        "predictions": [dict(p) for p in case.predictions],
        "evidence": [dict(e) for e in case.evidence],
        "facts": [dict(f) for f in case.facts],
        "checked_at": case.checked_at,
    }
    candidate = {
        "candidate_id": "",
        "core": core,
        "closure": {"subject_root": "", "complete": True, "count": 5,
                    "dangling": 0, "objects": []},
        "release_eligible": False,
        "failures": [],
        "mutations": list(mutations),
    }
    return _rebuild_identity(candidate)


# ── 视图投影（形状与 frontend/src/types.ts 逐字对齐）────────────────
def _status_row(rid: str, result: dict, denominator: str) -> dict:
    title, definition, version = RULE_META[rid]
    return {
        "rule_id": rid,
        "title": title,
        "definition": definition,
        "version": version,
        "status": result["status"],
        "applicability": {"applicable": result.get("applicable", True),
                          "basis": "合成 golden 适用（§22.1）",
                          "signature": SYNTHETIC_MARKER},
        "denominator": denominator,
        "inputs": sorted(result.get("inputs") or ()),
        "result": result["detail"],
        "locator": result["locator"],
    }


def _candidate_record_count(core: dict) -> int:
    """审计 completeness 门的检查对象数：全部候选记录组（与
    _record_completeness_errors 检查的记录组一致：claims/evidence/facts/
    predictions）。open_items 允许为空，不计入记录完整性计数。"""
    return (len(core["claims"]) + len(core["evidence"])
            + len(core["facts"]) + len(core["predictions"]))


def rules_view(candidate: dict) -> dict:
    core = candidate["core"]
    applicable = sum(1 for r in core["rule_results"].values()
                     if r.get("applicable", True))
    denominator = str(applicable)
    return {"rows": [_status_row(rid, core["rule_results"][rid], denominator)
                     for rid in RULE_IDS]}


def audit_view(candidate: dict) -> dict:
    core = candidate["core"]
    blocking = [r for r in core["rule_results"].values()
                if r["status"] in BLOCKING]
    material_open = [o for o in core["open_items"]
                     if o.get("material") and o.get("status") == "OPEN"]
    claims = core["claims"]
    mat_errors = _materiality_unclassified_errors(core)
    record_errors = _record_completeness_errors(core)
    gates = [
        {"gate": "completeness", "verdict":
            "PASS" if not record_errors else "FAIL",
         "checked": _candidate_record_count(core)},
        {"gate": "materiality", "verdict":
            "PASS" if not mat_errors else "FAIL",
         "checked": len(claims)},
        {"gate": "rules", "verdict":
            "FAIL" if blocking else "PASS",
         "checked": len(core["rule_results"])},
        {"gate": "open_items", "verdict":
            "FAIL" if material_open else "PASS",
         "checked": len(core["open_items"])},
        {"gate": "closure", "verdict":
            "PASS" if candidate["closure"]["complete"] else "FAIL",
         "checked": candidate["closure"]["count"]},
    ]
    return {
        "audit": {
            "gates": gates,
            "release_eligible": candidate["release_eligible"],
            "failures": [f["detail"] for f in candidate["failures"]],
            "source": "BACKEND",
        },
        "approvals": {"rows": []},          # G7-01 绝不写 Approval
        "releases": releases_view(candidate),
        "predictions": predictions_view(candidate),
        "closure": candidate["closure"],
        "gate7_reached": False,             # Gate 7 未达 —— 发布控件禁用
    }


def predictions_view(candidate: dict) -> dict:
    """预测视图：状态/claim 错绑定 → E-G7-01-006 失败关闭（不返回部分数据）。"""
    core = candidate["core"]
    _validate_predictions(core)
    return {
        "rows": [dict(p) for p in core["predictions"]],
        "calibration_sufficient": False,
        "calibration_note": "G7-01 合成样本不足 —— 校准充分性未建立（永久 "
                            "CALIBRATION_PENDING，VD-26）",
    }


def closure_view(candidate: dict) -> dict:
    return dict(candidate["closure"])


def releases_view(candidate: dict) -> dict:
    contract = candidate["core"]["contract"]
    key = f"{contract['workflow']}/{contract['security_code']}"
    return {"keys": [{"key": key, "current": None}], "diffs": []}


def approvals_view(_candidate: dict) -> dict:
    """批准视图：G7-01 恒空 —— 本模块**绝不写 Approval**（OI-PF-182 之后
    的同一默认拒绝）。`_candidate` 前缀表明形参有意不读取（签名与其余
    视图保持一致的 `candidate` 形）。"""
    return {"rows": []}


def eligibility_view(candidate: dict) -> dict:
    eligible = candidate["release_eligible"]
    if eligible:
        reasons: List[dict] = []
        status = "CLEAR"
    else:
        status = "BLOCKED"
        reasons = candidate["failures"]
    return {
        "status": status,
        "reasons": reasons,
        "checked_at": candidate["core"]["checked_at"],
        "source": "BACKEND",
    }


# 视图投影字段（frontend/src/types.ts 的 Claim / EvidenceRecord / FactRecord
# 逐字；fixture 校验保证这些字段在源数据中显式存在 —— 不做默认编造）
CLAIM_VIEW_FIELDS = ("schema_version", "id", "statement", "refs", "status",
                     "category", "materiality")
EVIDENCE_VIEW_FIELDS = ("id", "artifact_id", "snapshot_id", "schema_ver",
                        "parser_version", "sha256", "content")
FACT_VIEW_FIELDS = ("id", "artifact_id", "metric", "value", "unit", "period",
                    "scope", "basis", "vintage", "locator", "parser_version")


def evidence_view(candidate: dict) -> dict:
    """证据台账视图：逐字对齐 frontend/src/types.ts 的 Claim /
    EvidenceRecord / FactRecord —— 字段全部来自显式 fixture 校验结果，
    不做静默默认值编造。"""
    core = candidate["core"]
    return {
        "claims": [{k: c[k] for k in CLAIM_VIEW_FIELDS}
                   for c in core["claims"]],
        "evidence": [{k: e[k] for k in EVIDENCE_VIEW_FIELDS}
                     for e in core["evidence"]],
        "facts": [{k: f[k] for k in FACT_VIEW_FIELDS}
                  for f in core["facts"]],
        "openItems": [{"id": o["id"], "title": o["title"],
                       "status": o["status"], "material": o["material"],
                       "blocks": o["blocks"]} for o in core["open_items"]],
    }


# ── mutation 钩子（仅 G7 E2E 模式可达；默认拒绝未知选择子）────────────
MUTATION_SELECTORS = ("drop_closure_object", "misbind_prediction",
                      "corrupt_prediction_status")


def apply_mutation(candidate: dict, selector: str) -> dict:
    """测试专用 mutation 钩子：只允许白名单选择子，未知一律
    E-G7-01-005 默认拒绝。misbind_prediction / corrupt_prediction_status
    是**不可应用**的错绑定：让候选携带错绑数据 —— 读取端
    predictions_view/audit_view 随即 E-G7-01-006 失败关闭（绝不返回部分
    数据），而资格端点按重算结果如实 BLOCKED。"""
    if selector == "drop_closure_object":
        # 把闭包对象降为缺失 —— 读取端 closure/audit 如实报不完整/阻断。
        if not candidate["closure"]["complete"]:
            return candidate
        candidate["mutations"] = sorted(set(candidate["mutations"]) | {selector})
        return _rebuild_identity(candidate)
    if selector == "misbind_prediction":
        # 把一个预测错绑到不存在的 claim —— 该候选从此在读取端失败关闭；
        # 身份随 canonical 字节改变而重建（可复现）。
        core = candidate["core"]
        if core["predictions"]:
            core["predictions"][0] = dict(core["predictions"][0],
                                          claim_id="CLAIM-NO-SUCH-SYN")
        candidate["mutations"] = sorted(set(candidate["mutations"]) | {selector})
        return _rebuild_identity(candidate)
    if selector == "corrupt_prediction_status":
        # 把一个预测状态改为白名单外状态 —— 同上，读取端失败关闭。
        core = candidate["core"]
        if core["predictions"]:
            core["predictions"][0] = dict(core["predictions"][0],
                                          status="RESOLVED")
        candidate["mutations"] = sorted(set(candidate["mutations"]) | {selector})
        return _rebuild_identity(candidate)
    raise MutationDenied(
        f"E-G7-01-005: 未知 mutation 选择子 {selector!r} —— 默认拒绝；"
        f"只允许 {sorted(MUTATION_SELECTORS)}")


# ── 运行期状态（launch-before-read 失败关闭）─────────────────────────
class G7E2ERuntime:
    """单进程共享的运行期状态。绝不接触任何对象库/DB。"""

    def __init__(self, fixtures_dir: Optional[str] = None):
        self._fixtures_dir = fixtures_dir
        self._candidate: Optional[dict] = None

    @property
    def candidate(self) -> Optional[dict]:
        return self._candidate

    def launch(self, payload: dict) -> dict:
        """校验 ResearchContract（生产 validate_workflow_scope + 必填字段）
        并解析 golden case；契约须精确匹配冻结 fixture，未知 case 失败
        关闭。成功即形成候选。"""
        if not isinstance(payload, dict):
            raise G7E2EError(
                "E-G7-01-009: launch 请求体须为 JSON object —— 失败关闭")
        missing = [k for k in CONTRACT_FIELDS
                   if not isinstance(payload.get(k), str)
                   or not payload[k].strip()]
        if missing:
            raise G7E2EError(
                f"E-G7-01-008: ResearchContract 缺必填字段 {missing}"
                " —— 失败关闭")
        try:
            validate_workflow_scope(payload["workflow"], payload["scope"])
        except ValueError as exc:
            raise G7E2EError(
                f"E-G7-01-008: workflow/scope 非法（{exc}）—— 失败关闭") from exc
        case = resolve_case(payload, self._fixtures_dir)
        candidate = build_candidate(case, payload)
        self._candidate = candidate
        run_id = f"run-g7-01-{candidate['candidate_id'][:12]}"
        return {"ok": True, "run_id": run_id, "state": "CANDIDATE",
                "candidate_id": candidate["candidate_id"],
                "source": "backend", "g7_case": case.g7_case}

    def reset(self) -> None:
        self._candidate = None

    def require_launched(self) -> dict:
        """launch-before-read：未启动即读取 → E-G7-01-003 失败关闭。"""
        if self._candidate is None:
            raise NotLaunched(
                "E-G7-01-003: 读取前必须先启动研究（launch-before-read）"
                " —— 失败关闭")
        return self._candidate
