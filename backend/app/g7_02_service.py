"""g7_02_service.py —— G7-02 全链真实候选 + 另一真实来源冒烟（app 层服务，无网络）。

范围与边界（G7-02 原子任务书）：
  · 只消费仓外真实输入（600089 人工回源登记 + NBS 宏观取得 manifest），
    绝不扩展 G7-01 合成 fixture loader；
  · 网络只在 tools 层且必须先经 `RightsGuard`；本模块只做矩阵决定与内容
    寻址对象写入，不引入任何网络库（M1/M4）；
  · 600089 只走 IMPORT 权利判定，绝不访问 CNINFO/SSE 自动通道；
  · 复用 `freeze_final_candidate_from_payload` / `verify_candidate_bundle`，
    不复制候选计算；
  · 四路估值诚实 NOT_EVALUATED，不创建 assumption approval，最终 G6A
    candidate 为真 PARTIAL / release_eligible=false；
  · 输出内容寻址 G7-02 candidate pack，只含定位/哈希/状态/计数，不含原始
    正文或真实批量数值；任一依赖缺失或改字节，verify 必须失败关闭。

G7-02 首轮审查收口（本文件承担）：
  · NBS 身份与网络边界：manifest 绑定官方 source_url（https://www.stats.gov.cn
    官方路径形状，禁任意 scheme/host/userinfo/端口/query/fragment/绝对 URL/
    路径穿越）；publication_date 从 source_url 路径 tYYYYMMDD 片段确定并与其
    绑定，不再信正文首日期或墙钟回退；取得阶段即检查 cutoff；
  · strict JSON / 最小披露：json.loads 经 parse_constant 拒绝 NaN/Infinity，
    canonical dumps allow_nan=False；异常不含材料事实原值或原始时间字面量；
    输入正文永不进入 stdout/stderr/pack；
  · macro manifest / rights 绑定：manifest 严格验证 source_url、source
    revision，embedded RightsDecision 的 source_id/action/scope/policy_version/
    verdict 与本次 RightsGuard 重判一致，decided_at ≤ acquired_at 顺序合理；
    publication_date ≤ cutoff；不错误要求 acquired_at ≤ cutoff；
  · raw 完整性：freeze 前用 store.load() 校验 raw 内容哈希并比对 raw_bytes
    （不只 exists()）；verify 默认直接从 object store 加载 raw；
  · 600089 来源与覆盖：pack/受管 request 绑定 issuer source_id=SRC_CNINFO、
    source_family=600089-issuer-legal-filings、action=IMPORT 与本次
    RightsDecision；缺原始 artifact hash/source 明细兼容但降级 PARTIAL；
    显式声明 AKShare/aggregator/synthetic/NBS/非官方 family 一律 BLOCKED；
    back_source 键集须与材料事实精确一致、时间为 ISO；
  · 期间覆盖防假绿：移除任意 /YYYY/ 回退，只认明确年末日期或受约束 #YYYY；
    FULL 覆盖按归一 metric×period 矩阵完整，单个 2024 事实不能使全体 FULL；
  · 真实事实进入 frozen context：context.facts 以 g7_02_ 前缀键冻结全部已
    验证材料事实值（不与 fcff/fcfe/eps/book_per_share 路由键冲突）；
  · 整体状态分轴：顶层 candidate_status 恒 PARTIAL（macro PARTIAL+CONTEXT_ONLY、
    G6A 四路 NOT_EVALUATED），公司数据覆盖另设 company.data_status；
    reviewer_independence = SINGLE_REVIEWER_ATTESTED；blocks_gate=G7；
  · verify 交叉绑定：加载 stored request 逐字比对 request context.contract 的
    公司 canonical/raw 哈希、macro manifest/raw 哈希、issuer 绑定、scope/cutoff/
    snapshot 与 pack；错绑 E-G7-02-022 失败。

错误码约定（G7-02 命名空间）：
  E-G7-02-001..009  公司输入形状/材料性事实/回源/双源冒充/来源绑定
  E-G7-02-010..014  宏观 manifest 形状/权利/双源/空正文/cutoff 越界
  E-G7-02-015       权利拒绝（零请求/正文/写入）
  E-G7-02-016       对象落库 digest 与计算哈希不符 / raw 对象缺失·篡改·字节漂移
  E-G7-02-017       pack 非严格 JSON / 根非 object
  E-G7-02-020..024  verify 哈希漂移 / 覆盖漂移 / 依赖缺失 / 轴提升 / revision 漂移
"""
import datetime
import hashlib
import json
import re
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from artifact_store import ArtifactStore
from candidate_service import (
    CandidateFreezeService,
    CandidateVerificationError,
    freeze_final_candidate_from_payload,
    validate_source_revision,
)
from rights_guard import ALLOWED, RightsGuard
from schema_validate import SchemaError, validate_object

G7_02_TASK_ID = "G7-02"
PACK_KIND = "g7_real_candidate_pack"
PACK_SCHEMA_VERSION = "1.0.0"

SCOPE_600089 = "600089.SH"
TICKER_600089 = "600089"
MARKET_SCOPE = "CN_A_SHARE"
CURRENCY = "CNY"

# Golden 范围 = VD-13/VD-21 最少年报 + 上一年报（cutoff 下 2025 与 2024）。
REQUIRED_PERIODS = ("2025", "2024")

# 600089 人工导入的官方披露源 —— 只允许 IMPORT 权利判定，禁止 FETCH。
IMPORT_SOURCE_KEY = "SRC_CNINFO"
IMPORT_SOURCE_FAMILY = "600089-issuer-legal-filings"
IMPORT_ACTION = "IMPORT"
# 另一真实来源（官方统计机构）冒烟 —— 生产只允许官方域名。
NBS_SOURCE_ID = "SRC_NBS"
NBS_SOURCE_FAMILY = "nbs-official"
NBS_PRODUCTION_HOST = "www.stats.gov.cn"
NBS_PRODUCTION_BASE_URL = f"https://{NBS_PRODUCTION_HOST}"
MACRO_SCOPE = MARKET_SCOPE

SINGLE_SOURCE_DISCLOSED = "SINGLE_SOURCE_DISCLOSED"
SINGLE_REVIEWER_ATTESTED = "SINGLE_REVIEWER_ATTESTED"
SNAPSHOT_ID = "MACRO-CN-G7-02-20260816"

# 发布/决策/当前指针写入轴 —— 本任务全部为 0。
WRITE_AXES = ("Approval", "DecisionVersion", "Release", "CurrentPointer",
              "latest", "trade")

KIND_COMPANY_INPUT = "g7_02_company_input"
KIND_MACRO_MANIFEST = "g7_02_macro_manifest"
KIND_MACRO_RAW = "g7_02_macro_raw"

# 材料性事实若声明第二独立来源或 NBS 来源族 → 同源冒充 / NBS 冒充财务双源。
FORBIDDEN_DUAL_SOURCE_KEYS = ("second_source", "dual_source",
                              "independent_second_source")
# 事实/回源显式声明的非官方来源标记 → BLOCKED。
FORBIDDEN_SOURCE_MARKERS = ("akshare", "aggregator", "synthetic", "nbs")

DECIMAL_RE = re.compile(r"^-?\d+(\.\d+)?$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
SOURCE_REVISION_RE = re.compile(r"^[0-9a-f]{40}$")

# 期间覆盖防假绿：只认明确年末日期（YYYY-12-31）或受约束 #YYYY。
FACT_COVERAGE_PERIOD_RE = re.compile(r"(20\d{2})-12-31")
FACT_ANCHOR_PERIOD_RE = re.compile(r"(?:^|[^0-9])#(20\d{2})(?![0-9])")
# 官方数据发布页路径形状（G7-02 收口：收紧为本任务实际发布页形状，与
# tools/macro_adapter.NBS_SCOPE_RE 行为一致；app 层不引网络库，故此处按需
# 重复该稳定形状定义，并有测试断言两处 pattern 逐字一致）。
NBS_SCOPE_RE = re.compile(r"^/sj/zxfbhjd/\d{6}/t\d{8}_\d+\.html$")
NBS_PATH_DATE_RE = re.compile(r"t(20\d{2})(\d{2})(\d{2})")


class G7_02Error(ValueError):
    """G7-02 失败关闭错误（BLOCKED / 零写入）。"""


class G7_02Blocked(G7_02Error):
    """须停在 BLOCKED / 零写入的失败路径。"""


def _nonempty(v) -> bool:
    return isinstance(v, str) and bool(v.strip())


def _canonical(obj) -> bytes:
    """规范 JSON 字节（确定性排序、紧凑分隔符）—— 内容寻址身份。

    G7-02 首轮审查：allow_nan=False —— 任何 NaN/Infinity 在写入前失败关闭，
    绝不进入对象库或 pack。
    """
    return json.dumps(obj, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


def _sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _reject_json_constant(token: str):
    raise ValueError(f"非标准 JSON 常量 {token!r}")


def _strict_json_obj(data: bytes, what: str) -> dict:
    try:
        obj = json.loads(data.decode("utf-8"),
                         parse_constant=_reject_json_constant)
    except (UnicodeDecodeError, ValueError) as exc:
        raise G7_02Error(
            f"E-G7-02-017: {what} 非严格 JSON —— 失败关闭"
            f"（{type(exc).__name__}）") from exc
    if not isinstance(obj, dict):
        raise G7_02Error(f"E-G7-02-017: {what} 根须为 JSON object")
    return obj


def _iso_datetime(value, label: str) -> datetime.datetime:
    """ISO 时间解析（异常不含原始时间字面量 —— 最小披露）。"""
    if not _nonempty(value):
        raise G7_02Error(f"E-G7-02-000: {label} 须为非空字符串")
    try:
        dt = datetime.datetime.fromisoformat(
            str(value).replace("Z", "+00:00"))
    except ValueError as exc:
        raise G7_02Error(f"E-G7-02-000: {label} 非 ISO 时间 —— 失败关闭") from exc
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.timezone.utc)
    return dt


def _iso_date(value, label: str) -> datetime.date:
    dt = _iso_datetime(value, label)
    return dt.date()


def _require_sha256(value, label: str, code: str = "E-G7-02-001") -> None:
    """严格 sha256 格式校验（任何对象写入前的入口门）。"""
    if not isinstance(value, str) or not SHA256_RE.fullmatch(value):
        raise G7_02Error(
            f"{code}: {label} 非严格 sha256 —— 失败关闭")


def _publication_date_from_path(path: str) -> str:
    """从 source_url 路径 tYYYYMMDD 片段确定发布日（与 source_url 绑定）。

    G7-02 收口：日期片段须为真实 calendar date（不只 month/day 范围）。
    """
    m = NBS_PATH_DATE_RE.search(path)
    if not m:
        raise G7_02Error(
            "E-G7-02-010: source_url 路径无发布日期片段（tYYYYMMDD）"
            " —— 失败关闭")
    y, mo, d = m.group(1), int(m.group(2)), int(m.group(3))
    try:
        datetime.date(int(y), mo, d)
    except ValueError:
        raise G7_02Error(
            "E-G7-02-010: 路径发布日期非法 calendar date —— 失败关闭")
    return f"{y}-{mo:02d}-{d:02d}"


def _validate_source_url(url: str) -> str:
    """source_url 严格校验：官方域名 + 官方路径形状（身份不靠 manifest 自证）。

    拒绝任意 scheme/host、userinfo、端口、query/fragment、绝对 URL、路径穿越。
    """
    if not isinstance(url, str) or not url.startswith(NBS_PRODUCTION_BASE_URL + "/"):
        raise G7_02Error(
            f"E-G7-02-010: source_url 非官方 NBS 域名（{NBS_PRODUCTION_BASE_URL}）"
            " —— 失败关闭")
    rest = url[len(NBS_PRODUCTION_BASE_URL):]
    if not rest or not rest.startswith("/"):
        raise G7_02Error("E-G7-02-010: source_url 路径非法 —— 失败关闭")
    if "//" in rest or "\\" in rest \
            or any(c in rest for c in "?#@ \t\r\n"):
        raise G7_02Error(
            "E-G7-02-010: source_url 含 userinfo/端口/query/fragment/空白"
            " —— 失败关闭")
    if ".." in rest.split("/"):
        raise G7_02Error("E-G7-02-010: source_url 含路径穿越 —— 失败关闭")
    if not NBS_SCOPE_RE.fullmatch(rest):
        raise G7_02Error(
            "E-G7-02-010: source_url 路径非 NBS 官方数据发布页形状 —— 失败关闭")
    return rest


def _forbidden_source(value) -> bool:
    low = str(value or "").lower()
    return any(m in low for m in FORBIDDEN_SOURCE_MARKERS)


def _check_declared_source(owner: dict, metric: str, where: str):
    """事实/回源显式声明的来源必须为官方发行人绑定，否则 BLOCKED。"""
    sid = owner.get("source_id")
    fam = owner.get("source_family")
    if sid is not None and sid != IMPORT_SOURCE_KEY:
        raise G7_02Blocked(
            f"E-G7-02-008: {where} {metric} source_id 非 {IMPORT_SOURCE_KEY}"
            " —— 非官方来源 BLOCKED")
    if fam is not None and fam != IMPORT_SOURCE_FAMILY:
        raise G7_02Blocked(
            f"E-G7-02-008: {where} {metric} source_family 非"
            f" {IMPORT_SOURCE_FAMILY}"
            " —— 非官方 family BLOCKED")
    if sid is not None and _forbidden_source(sid):
        raise G7_02Blocked(
            f"E-G7-02-008: {where} {metric} source_id 显式声明聚合/合成/AKShare"
            " —— BLOCKED")
    if fam is not None and _forbidden_source(fam):
        raise G7_02Blocked(
            f"E-G7-02-008: {where} {metric} source_family 显式声明聚合/合成/"
            "NBS/AKShare —— BLOCKED")


def _normalize_metric(metric: str) -> str:
    """归一 metric：剥离 `_YYYY` 期间后缀（营业收入_2024 → 营业收入）。"""
    m = re.match(r"^(.*)_(20\d{2})$", metric)
    return m.group(1) if m else metric


def _locator_period(locator: str) -> Optional[str]:
    """从 locator 提取参考期年。

    G7-02 首轮审查（防假绿）：移除任意 `/YYYY/` 回退 —— 只认明确年末日期
    （YYYY-12-31）或受约束的 #YYYY 锚点；两者皆无即无法判定期间（诚实 PARTIAL）。
    """
    for m in FACT_COVERAGE_PERIOD_RE.finditer(locator):
        return m.group(1)
    m = FACT_ANCHOR_PERIOD_RE.search(locator)
    if m:
        return m.group(1)
    return None


# ── 公司输入（600089 人工回源登记）─────────────────────────────────
@dataclass(frozen=True)
class CompanyValidation:
    input_sha256: str
    ticker: str
    source_doc_count: int
    source_complete: bool
    material_fact_count: int
    material_verified_count: int
    period_status: Dict[str, bool]
    missing_periods: List[str]
    missing_bindings: List[str]
    data_status: str            # FULL / PARTIAL（公司数据覆盖轴，与顶层分离）
    fact_coverage: Dict[str, dict]   # 归一 metric -> {period: {locator,state,unit}}
    material_facts: Dict[str, dict]  # metric -> fact（值仅供 frozen context，不入 pack）


def validate_company_input(data, required_periods=REQUIRED_PERIODS) -> CompanyValidation:
    """验证 600089 外部登记输入（兼容 golden-baselines/600089.json 形状）。

    失败关闭（BLOCKED，零写入）：缺核心键 / 材料性事实缺 value·unit·locator /
    非有限十进制值 / 材料性回源缺失·错绑·非 VERIFIED / 同源冒充 / NBS 冒充 /
    事实·回源显式声明非官方 family / back_source 键集与材料事实不一致。
    诚实降级（PARTIAL）：材料性事实未覆盖 2024（或 locator 无法解析参考期）；
    缺原始 artifact hash/source 明细同样只降 PARTIAL、绝不升 FULL。
    顶层 `status=COMPLETE` 不参与升格 —— 期间完整性以本函数重验为准。
    """
    if not isinstance(data, dict):
        raise G7_02Error("E-G7-02-001: company input 根须为 JSON object")
    if data.get("ticker") != TICKER_600089:
        raise G7_02Error(
            f"E-G7-02-002: ticker 非 {TICKER_600089!r}")
    # 发行人来源绑定：声明必须匹配官方绑定（未声明视为兼容，不得自证）。
    declared_sid = data.get("source_id")
    declared_fam = data.get("source_family")
    if declared_sid is not None and declared_sid != IMPORT_SOURCE_KEY:
        raise G7_02Blocked(
            f"E-G7-02-008: company source_id 非 {IMPORT_SOURCE_KEY}"
            " —— BLOCKED")
    if declared_fam is not None and declared_fam != IMPORT_SOURCE_FAMILY:
        raise G7_02Blocked(
            f"E-G7-02-008: company source_family 非 {IMPORT_SOURCE_FAMILY}"
            " —— BLOCKED")
    if declared_sid is not None and _forbidden_source(declared_sid):
        raise G7_02Blocked(
            "E-G7-02-008: company source_id 显式声明聚合/合成/AKShare"
            " —— BLOCKED")
    if declared_fam is not None and _forbidden_source(declared_fam):
        raise G7_02Blocked(
            "E-G7-02-008: company source_family 显式声明聚合/合成/NBS/AKShare"
            " —— BLOCKED")
    source_docs = data.get("source_docs")
    if not isinstance(source_docs, list) or not source_docs:
        raise G7_02Error("E-G7-02-003: source_docs 缺失或为空")
    # 收口：source_complete 要求根 source_id/source_family 显式正确 + 每个
    # source doc 的 artifact_sha256/source_id/source_family 及 action=IMPORT
    # 完整，registered_at 须为 ISO 时间。现有外部 baseline 缺这些字段 → 只降
    # PARTIAL，绝不升 FULL。
    source_complete = (declared_sid == IMPORT_SOURCE_KEY
                       and declared_fam == IMPORT_SOURCE_FAMILY)
    for i, d in enumerate(source_docs):
        if not isinstance(d, dict) or not _nonempty(d.get("locator")) \
                or not _nonempty(d.get("registered_at")):
            raise G7_02Error(
                f"E-G7-02-003: source_docs[{i}] 缺 locator/registered_at")
        _iso_datetime(d.get("registered_at"),
                      f"source_docs[{i}].registered_at")
        art = d.get("artifact_sha256")
        if not (isinstance(art, str) and SHA256_RE.fullmatch(art)) \
                or d.get("source_id") != IMPORT_SOURCE_KEY \
                or d.get("source_family") != IMPORT_SOURCE_FAMILY \
                or d.get("action") != IMPORT_ACTION:
            source_complete = False
    facts = data.get("facts")
    if not isinstance(facts, dict) or not facts:
        raise G7_02Error("E-G7-02-004: facts 缺失或为空")
    back_source = data.get("back_source")
    if not isinstance(back_source, dict):
        raise G7_02Error("E-G7-02-004: back_source 缺失或非 object")

    material = {}
    for metric, fact in facts.items():
        if not isinstance(fact, dict):
            raise G7_02Error(f"E-G7-02-004: facts.{metric} 非 object")
        if not isinstance(fact.get("material"), bool):
            raise G7_02Error(f"E-G7-02-004: facts.{metric}.material 须为布尔")
        if fact["material"] is True:
            material[metric] = fact
    if not material:
        raise G7_02Error("E-G7-02-004: 无任何材料性事实")

    for metric, fact in facts.items():
        value = fact.get("value")
        if not isinstance(value, str) or not DECIMAL_RE.fullmatch(value):
            raise G7_02Error(
                f"E-G7-02-005: facts.{metric}.value 非有限十进制字符串"
                " —— 失败关闭")
        for key in ("unit", "locator"):
            if not _nonempty(fact.get(key)):
                raise G7_02Error(f"E-G7-02-005: facts.{metric}.{key} 缺失或为空")

    # back_source 键集须与材料事实精确一致（G7-02 首轮审查）。
    material_keys = set(material)
    if set(back_source) != material_keys:
        raise G7_02Error(
            "E-G7-02-006: back_source 键集与材料事实不一致 —— 失败关闭")

    for metric in material:
        fact = material[metric]
        _check_declared_source(fact, metric, "facts")
        bs = back_source[metric]
        if not isinstance(bs, dict):
            raise G7_02Error(
                f"E-G7-02-006: 材料性事实 {metric} 缺回源记录 —— 失败关闭")
        if bs.get("locator") != fact.get("locator"):
            raise G7_02Error(
                f"E-G7-02-007: 材料性事实 {metric} 回源 locator 与事实 locator "
                "不一致 —— 失败关闭")
        if bs.get("state") != "VERIFIED":
            raise G7_02Error(
                f"E-G7-02-006: 材料性事实 {metric} 回源 state ≠ VERIFIED"
                f"（{bs.get('state')!r}）—— 失败关闭")
        for key in ("reviewed_by", "at"):
            if not _nonempty(bs.get(key)):
                raise G7_02Error(
                    f"E-G7-02-006: 材料性事实 {metric} 回源 {key} 缺失或为空")
        _iso_datetime(bs["at"], f"back_source.{metric}.at")
        _check_declared_source(bs, metric, "back_source")
        # 同源冒充 / NBS 冒充财务双源：材料性事实不得声明第二独立来源。
        for key in FORBIDDEN_DUAL_SOURCE_KEYS:
            if key in fact and fact.get(key) is not False:
                raise G7_02Error(
                    f"E-G7-02-008: facts.{metric}.{key} 声明第二独立来源 —— "
                    "同源冒充")
            if key in bs and bs.get(key) is not False:
                raise G7_02Error(
                    f"E-G7-02-008: 材料性事实 {metric} 回源 {key} 声明第二"
                    "独立来源 —— 同源冒充")
        if fact.get("source_id") == NBS_SOURCE_ID \
                or fact.get("source_family") == NBS_SOURCE_FAMILY:
            raise G7_02Error(
                f"E-G7-02-008: facts.{metric} 以 NBS 冒充 600089 财务来源")

    # 期间覆盖矩阵：归一 metric × required period 全部覆盖才 FULL。
    period_status = {p: False for p in required_periods}
    matrix: Dict[str, dict] = {}
    for metric in material:
        fact = material[metric]
        norm = _normalize_metric(metric)
        period = _locator_period(fact["locator"])
        cell = matrix.setdefault(norm, {})
        if period:
            if period in cell:
                raise G7_02Error(
                    f"E-G7-02-009: 归一 metric×period 重复（{norm}/{period}）"
                    " —— 后写不得覆盖前写（失败关闭）")
            cell[period] = {
                "locator": fact["locator"],
                "state": "VERIFIED",
                "unit": fact["unit"],
            }
    metrics = sorted(matrix)
    missing_bindings = []
    for p in required_periods:
        period_status[p] = bool(metrics) and all(
            p in matrix[m] for m in metrics)
        for m in metrics:
            if p not in matrix[m]:
                missing_bindings.append(f"{m}/{p}")
    missing_bindings.sort()
    missing_periods = [p for p in required_periods if not period_status[p]]
    coverage_complete = not missing_bindings
    data_status = "FULL" if coverage_complete and source_complete else "PARTIAL"
    input_sha256 = _sha256_hex(_canonical(data))
    return CompanyValidation(
        input_sha256=input_sha256,
        ticker=TICKER_600089,
        source_doc_count=len(source_docs),
        source_complete=source_complete,
        material_fact_count=len(material),
        material_verified_count=len(material),
        period_status=period_status,
        missing_periods=missing_periods,
        missing_bindings=missing_bindings,
        data_status=data_status,
        fact_coverage=matrix,
        material_facts=material,
    )


# ── 宏观 manifest（NBS 第二独立真实来源冒烟）───────────────────────
def validate_macro_manifest(manifest, *, cutoff_at, source_commit, source_tree,
                            guard: Optional[RightsGuard] = None) -> Tuple[dict, str]:
    """验证 NBS 宏观取得 manifest 并返回 (manifest, canonical_sha256)。

    失败关闭（零写入）：
      · 非 SRC_NBS / nbs-official；source_url 非官方域名/官方路径形状（身份
        不靠 manifest 自证）；publication_date 非由 source_url 路径确定（绑定）；
      · manifest.scope 与 embedded rights_decision.scope 必须逐字等于
        source_url 的 path（禁止用抽象 CN_A_SHARE 冒充实际取得范围）；
      · source revision 非严格 40 位十六进制或与当前代码版本漂移；
      · embedded RightsDecision 与本次 RightsGuard 重判不一致（source_id/action/
        scope/policy_version/verdict）；decided_at ≤ acquired_at 顺序不合理；
      · is_financial_dual_source_for_600089=True；缺核心字段；
        raw_sha256 非法 / raw_bytes < 1（空正文）；
        publication_date 晚于 cutoff（越界阻断）或晚于 manifest.cutoff_at；
      · manifest.cutoff_at 与本次 cutoff 规范化后逐时刻不等（双 cutoff 漂移）；
      · gate_status 非 PARTIAL + CONTEXT_ONLY。
    取得时刻（acquired_at）可晚于 cutoff —— 检索晚于 cutoff 不构成越界。
    """
    if not isinstance(manifest, dict):
        raise G7_02Error("E-G7-02-010: macro manifest 根须为 JSON object")
    if manifest.get("source_id") != NBS_SOURCE_ID:
        raise G7_02Error(
            f"E-G7-02-010: macro manifest source_id ≠ {NBS_SOURCE_ID}")
    if manifest.get("source_family") != NBS_SOURCE_FAMILY:
        raise G7_02Error(
            f"E-G7-02-010: macro manifest source_family ≠ {NBS_SOURCE_FAMILY}")
    scope_path = _validate_source_url(manifest.get("source_url", ""))
    # 收口：scope 必须逐字等于 source_url 的 path（取得范围真实化，禁止
    # 抽象 CN_A_SHARE 冒充）；embedded rights_decision.scope 同该 path。
    if manifest.get("scope") != scope_path:
        raise G7_02Error(
            "E-G7-02-010: macro manifest.scope 非 source_url 的 path —— "
            "抽象范围冒充实际取得范围（失败关闭）")
    # 发布日必须由 source_url 路径确定并与 manifest 声明一致（绑定）。
    derived_pub = _publication_date_from_path(manifest.get("source_url", ""))
    if manifest.get("publication_date") != derived_pub:
        raise G7_02Error(
            "E-G7-02-010: publication_date 与 source_url 路径发布日期不一致"
            " —— 失败关闭")
    # source revision 严格校验 + 与当前代码版本漂移检查。
    mc = manifest.get("source_commit")
    mt = manifest.get("source_tree")
    if not (isinstance(mc, str) and SOURCE_REVISION_RE.fullmatch(mc)) \
            or not (isinstance(mt, str) and SOURCE_REVISION_RE.fullmatch(mt)):
        raise G7_02Error(
            "E-G7-02-010: macro manifest source revision 非严格 40 位十六进制"
            " —— 失败关闭")
    if mc != source_commit or mt != source_tree:
        raise G7_02Error(
            "E-G7-02-010: macro manifest source revision 与当前代码版本漂移"
            " —— 失败关闭")
    rd = manifest.get("rights_decision")
    if not isinstance(rd, dict) or rd.get("verdict") != ALLOWED:
        raise G7_02Error(
            "E-G7-02-011: macro manifest rights_decision 缺失或非 ALLOWED")
    if rd.get("source_id") != NBS_SOURCE_ID or rd.get("action") != "FETCH":
        raise G7_02Error(
            "E-G7-02-011: rights_decision 须为 SRC_NBS FETCH ALLOWED")
    if rd.get("scope") != manifest.get("scope"):
        raise G7_02Error(
            "E-G7-02-011: rights_decision.scope 与 manifest.scope 不一致")
    if rd.get("scope") != scope_path:
        raise G7_02Error(
            "E-G7-02-011: rights_decision.scope 非 source_url 的 path —— "
            "实际取得范围错绑（失败关闭）")
    if manifest.get("is_financial_dual_source_for_600089") is not False:
        raise G7_02Error(
            "E-G7-02-012: NBS 不得作为 600089 财务事实的双源复核")
    for key in ("scope", "reference_period", "acquired_at", "attribution",
                "cutoff_at"):
        if not _nonempty(manifest.get(key)):
            raise G7_02Error(f"E-G7-02-010: macro manifest.{key} 缺失或为空")
    raw_sha = manifest.get("raw_sha256")
    raw_bytes = manifest.get("raw_bytes")
    if not isinstance(raw_sha, str) or not SHA256_RE.fullmatch(raw_sha):
        raise G7_02Error(
            "E-G7-02-010: macro manifest raw_sha256 非严格 sha256")
    if not isinstance(raw_bytes, int) or isinstance(raw_bytes, bool) \
            or raw_bytes < 1:
        raise G7_02Error(
            "E-G7-02-013: macro manifest raw_bytes 须 ≥1（空正文失败关闭）")
    pub = _iso_datetime(manifest.get("publication_date"),
                        "macro manifest.publication_date")
    cutoff = _iso_datetime(cutoff_at, "cutoff_at")
    if pub > cutoff:
        raise G7_02Error(
            "E-G7-02-014: publication_date 晚于 cutoff —— 阻断")
    manifest_cutoff = _iso_datetime(manifest.get("cutoff_at"),
                                    "macro manifest.cutoff_at")
    if pub > manifest_cutoff:
        raise G7_02Error(
            "E-G7-02-014: publication_date 晚于 manifest.cutoff_at —— 阻断")
    # 收口：manifest.cutoff_at 必须与本次传入 cutoff 规范化后逐时刻相等
    # （不只分别晚于 publication_date —— 双 cutoff 漂移即失败关闭）。
    if manifest_cutoff != cutoff:
        raise G7_02Error(
            "E-G7-02-014: macro manifest.cutoff_at 与本次 cutoff 规范化后"
            "不一致 —— 失败关闭")
    gate = manifest.get("gate_status")
    if not isinstance(gate, dict) \
            or gate.get("quality_status") != "PARTIAL" \
            or gate.get("decision_use_status") != "CONTEXT_ONLY":
        raise G7_02Error(
            "E-G7-02-010: macro manifest gate_status 须为 PARTIAL + CONTEXT_ONLY")
    # 权利绑定：与本次 RightsGuard 重判逐字一致（来源身份不能靠 manifest 自证）。
    if guard is not None:
        fresh = guard.decide(NBS_SOURCE_ID, "FETCH", manifest["scope"])
        if fresh.verdict != ALLOWED:
            raise G7_02Error(
                f"E-G7-02-011: NBS FETCH 当前矩阵判 {fresh.verdict}"
                " —— 失败关闭")
        if rd.get("source_id") != fresh.source_id \
                or rd.get("action") != fresh.action \
                or rd.get("scope") != fresh.scope \
                or rd.get("policy_version") != fresh.policy_version \
                or rd.get("verdict") != fresh.verdict:
            raise G7_02Error(
                "E-G7-02-011: embedded rights_decision 与本次 RightsGuard 重判"
                "不一致 —— 失败关闭")
    # decided_at ≤ acquired_at 顺序合理；acquired_at 可晚于 cutoff（检索晚于
    # cutoff 不构成越界）。
    decided = _iso_datetime(rd.get("decided_at"),
                            "rights_decision.decided_at")
    acquired = _iso_datetime(manifest.get("acquired_at"),
                             "macro manifest.acquired_at")
    if decided > acquired:
        raise G7_02Error(
            "E-G7-02-011: decided_at 晚于 acquired_at —— 顺序不合理")
    manifest_sha = _sha256_hex(_canonical(manifest))
    return manifest, manifest_sha


def require_rights(guard: RightsGuard, source_key: str, action: str,
                   scope: str, label: str):
    """权利决定先行：非 ALLOWED 一律失败关闭（零请求/正文/写入）。"""
    rd = guard.decide(source_key, action, scope)
    if rd.verdict != ALLOWED:
        raise G7_02Error(
            f"E-G7-02-015: {label} 权利拒绝（{rd.verdict}）—— 零请求/正文/写入")
    return rd


def _decision_snapshot(rd) -> dict:
    """确定性 RightsDecision 快照（剔除墙钟 decided_at —— 保证 pack 确定性）。"""
    return {
        "source_id": rd.source_id,
        "action": rd.action,
        "scope": rd.scope,
        "policy_version": rd.policy_version,
        "verdict": rd.verdict,
    }


# ── 受管 final candidate request 构造 ──────────────────────────────
VALUATION_ROUTES = ("fcff", "fcfe", "relative", "pe_roe_pb")
# 路由专属事实键 —— context.facts 的真实材料事实须用 g7_02_ 前缀，避免冲突。
ROUTE_FACT_KEYS = ("fcff", "fcfe", "eps", "book_per_share")


def _macro_descriptor(manifest: dict, manifest_sha: str) -> dict:
    """MacroSnapshot 描述符（PARTIAL + CONTEXT_ONLY）—— 绑定 manifest/raw 哈希。"""
    return {
        "snapshot_id": SNAPSHOT_ID,
        "local_profile_status": "LOCAL_PROFILE_AVAILABLE",
        "vintage_mode": "MIXED",
        "quality_status": "PARTIAL",
        "decision_use_status": "CONTEXT_ONLY",
        "history_permission": "CONTEXT_ONLY",
        "price_as_of": "",
        "investment_stance": "NOT_PRODUCED",
        "source_id": manifest["source_id"],
        "source_family": manifest["source_family"],
        "source_url": manifest["source_url"],
        "manifest_sha256": manifest_sha,
        "raw_sha256": manifest["raw_sha256"],
        "reference_period": manifest["reference_period"],
        "publication_date": manifest["publication_date"],
        "scope": manifest["scope"],
    }


def build_candidate_request(*, company: CompanyValidation,
                            macro_manifest: dict, manifest_sha: str,
                            company_raw_sha256: str,
                            macro_manifest_raw_sha256: str,
                            import_rights_snapshot: dict,
                            contract_id: str, run_id: str,
                            source_commit: str, source_tree: str,
                            cutoff_at: str, as_of_date: str) -> dict:
    """由 G7-02 service 构造受管 final candidate request。

    全部已验证材料事实值以 `g7_02_` 前缀键进入 context.facts（真实冻结值，
    不与 fcff/fcfe/eps/book_per_share 路由键冲突）；company canonical/raw
    哈希、macro manifest/raw 哈希、issuer 绑定与本次 IMPORT RightsDecision、
    scope/cutoff/snapshot 全部绑入 frozen context；四路估值诚实
    NOT_EVALUATED；approved_snapshot 为空（不创建 assumption approval）。
    """
    routes = {}
    for route in VALUATION_ROUTES:
        routes[route] = {
            "state": "NOT_EVALUATED",
            "reason": "G7-02：四路估值诚实未评估（无批准假设、无发布授权、"
                      "PIPELINE_ACCEPTANCE_ONLY）",
            "evidence_refs": [f"object:{company.input_sha256}",
                              f"object:{manifest_sha}"],
        }
    facts = {f"g7_02_{metric}": fact["value"]
             for metric, fact in company.material_facts.items()}
    return {
        "schema_version": "1.1.0",
        "run_id": run_id,
        "source_revision": {"source_commit": source_commit,
                            "source_tree": source_tree},
        "context": {
            "contract": {
                "contract_id": contract_id,
                "scope": SCOPE_600089,
                "workflow": "G7-02_PIPELINE_ACCEPTANCE_ONLY",
                "market_scope": MARKET_SCOPE,
                "currency": CURRENCY,
                "as_of_date": as_of_date,
                "cutoff_at": cutoff_at,
                "g7_02_company_input_sha256": company.input_sha256,
                "g7_02_company_raw_sha256": company_raw_sha256,
                "g7_02_macro_manifest_sha256": manifest_sha,
                "g7_02_macro_manifest_raw_sha256": macro_manifest_raw_sha256,
                "g7_02_macro_raw_sha256": macro_manifest["raw_sha256"],
                "g7_02_macro_snapshot_id": SNAPSHOT_ID,
                "issuer_source_id": IMPORT_SOURCE_KEY,
                "issuer_source_family": IMPORT_SOURCE_FAMILY,
                "issuer_action": IMPORT_ACTION,
                "issuer_rights_decision": import_rights_snapshot,
            },
            "facts": facts,
            "macro": _macro_descriptor(macro_manifest, manifest_sha),
            "formula_specs": {},
            "valuation_inputs": {"scope": SCOPE_600089, "currency": CURRENCY,
                                 "as_of": as_of_date},
            "assumption_defaults": {},
            "approved_snapshot": {"snapshot_id": "SNAP-G7-02", "version": 1,
                                  "proposals": [], "decisions": []},
            "open_items_policy": {
                "tolerance": "0.15", "owner_role": "U",
                "due_date": "2026-08-31", "blocks_gate": "G7"},
            "valuation_routes": routes,
        },
    }


# ── G7-02 candidate pack 构造 ──────────────────────────────────────
def build_pack(*, company: CompanyValidation, macro_manifest: dict,
               manifest_sha: str, manifest_raw_sha256: str,
               company_raw_sha256: str, import_rights_snapshot: dict,
               candidate, request_hash: str,
               source_commit: str, source_tree: str, cutoff_at: str,
               as_of_date: str, contract_id: str) -> dict:
    """Pack 只含定位/哈希/状态/计数 —— 不含原始正文或真实批量数值。

    顶层 candidate_status 与 company.data_status 分轴（G7-02 首轮审查）：
    macro 固定 PARTIAL+CONTEXT_ONLY、G6A 四路 NOT_EVALUATED 时顶层恒 PARTIAL，
    即使 company data coverage 自身 FULL。
    """
    # 本任务的 macro 与 G6A 路由均按合同固定为 PARTIAL；顶层不得暗示存在
    # 当前不可达的 FULL 路径。
    top_status = "PARTIAL"
    return {
        "schema_version": PACK_SCHEMA_VERSION,
        "kind": PACK_KIND,
        "task_id": G7_02_TASK_ID,
        "source_commit": source_commit,
        "source_tree": source_tree,
        "scope_id": SCOPE_600089,
        "as_of_date": as_of_date,
        "cutoff_at": cutoff_at,
        "research_contract": {
            "contract_id": contract_id,
            "scope": SCOPE_600089,
            "workflow": "G7-02_PIPELINE_ACCEPTANCE_ONLY",
            "market_scope": MARKET_SCOPE,
            "currency": CURRENCY,
            "as_of_date": as_of_date,
            "cutoff_at": cutoff_at,
        },
        "company": {
            "ticker": company.ticker,
            "data_status": company.data_status,
            "input_sha256": company.input_sha256,
            "input_raw_sha256": company_raw_sha256,
            "source_doc_count": company.source_doc_count,
            "material_fact_count": company.material_fact_count,
            "material_verified_count": company.material_verified_count,
            "period_status": company.period_status,
            "single_source_disclosed": SINGLE_SOURCE_DISCLOSED,
            "source_id": IMPORT_SOURCE_KEY,
            "source_family": IMPORT_SOURCE_FAMILY,
            "import_action": IMPORT_ACTION,
            "import_rights_decision": import_rights_snapshot,
            "source_complete": company.source_complete,
        },
        "material_fact_coverage": company.fact_coverage,
        "missing_periods": company.missing_periods,
        "missing_bindings": company.missing_bindings,
        "candidate_status": top_status,
        "single_source_disclosed": SINGLE_SOURCE_DISCLOSED,
        "reviewer_independence": SINGLE_REVIEWER_ATTESTED,
        "macro": {
            "source_id": macro_manifest["source_id"],
            "source_family": macro_manifest["source_family"],
            "source_url": macro_manifest["source_url"],
            "source_commit": macro_manifest["source_commit"],
            "source_tree": macro_manifest["source_tree"],
            "manifest_sha256": manifest_sha,
            "manifest_raw_sha256": manifest_raw_sha256,
            "raw_sha256": macro_manifest["raw_sha256"],
            "raw_bytes": macro_manifest["raw_bytes"],
            "scope": macro_manifest["scope"],
            "publication_date": macro_manifest["publication_date"],
            "reference_period": macro_manifest["reference_period"],
            "acquired_at": macro_manifest["acquired_at"],
            "cutoff_at": macro_manifest["cutoff_at"],
            "policy_version": macro_manifest["rights_decision"]["policy_version"],
            "rights_decision": macro_manifest["rights_decision"],
            "gate_status": macro_manifest["gate_status"],
            "is_financial_dual_source_for_600089": False,
        },
        "g6a_candidate": {
            "candidate_id": candidate.candidate_id,
            "request_hash": request_hash,
            "product_count": len(candidate.candidate["product_hashes"]),
            "quality_status": candidate.candidate["quality_status"],
            "release_eligible": candidate.candidate["release_eligible"],
        },
        "gate_status": {
            "gate7_reached": False,
            "gate_release_eligible": False,
        },
        "write_counts": {axis: 0 for axis in WRITE_AXES},
    }


# ── 主入口：freeze / verify ────────────────────────────────────────
@dataclass(frozen=True)
class PackFreeze:
    pack_id: str
    pack: dict
    candidate_id: str
    request_hash: str
    candidate_status: str
    company_data_status: str
    missing_periods: List[str]
    missing_bindings: List[str]


def freeze_pack(store: ArtifactStore, *, company_input, macro_manifest,
                source_commit: str, source_tree: str, cutoff_at: str,
                as_of_date: str, contract_id: str, run_id: str,
                company_raw_sha256: str, macro_manifest_raw_sha256: str,
                guard: Optional[RightsGuard] = None,
                import_source_key: str = IMPORT_SOURCE_KEY) -> PackFreeze:
    """G7-02 全链冻结：权利门 → 输入验证 → 哈希绑定 → G6A candidate →
    candidate pack。任何失败都零写入（或只留孤儿对象，不构成 pack）。"""
    guard = guard or RightsGuard()
    # 收口：入口门在任何对象写入前 —— raw 哈希必须为严格 sha256，且发行人
    # 权利源必须精确等于 SRC_CNINFO（禁止调用方用别的权利源但 pack 仍声称
    # CNINFO）。
    if import_source_key != IMPORT_SOURCE_KEY:
        raise G7_02Error(
            "E-G7-02-001: import_source_key 必须精确等于 SRC_CNINFO —— "
            "禁止以别的权利源冒充发行人来源")
    _require_sha256(company_raw_sha256, "company_raw_sha256")
    _require_sha256(macro_manifest_raw_sha256, "macro_manifest_raw_sha256")
    validate_source_revision(source_commit, source_tree)
    # 1) 600089 只走 IMPORT 权利判定（绝不 FETCH CNINFO/SSE）。
    rd_import = require_rights(guard, import_source_key, "IMPORT", SCOPE_600089,
                               "600089 人工导入")
    # 2) 公司输入验证 + 哈希。
    company = validate_company_input(company_input)
    # 3) 宏观 manifest 验证 + NBS FETCH 权利复验（embedded decision 比对）。
    macro, manifest_sha = validate_macro_manifest(
        macro_manifest, cutoff_at=cutoff_at, source_commit=source_commit,
        source_tree=source_tree, guard=guard)
    # 4) raw 完整性：store.load() 读时哈希校验（篡改即拒）并比对 raw_bytes。
    try:
        raw_data = store.load(macro["raw_sha256"])
    except (TypeError, ValueError, OSError) as exc:
        raise G7_02Error(
            f"E-G7-02-016: macro raw 对象缺失或被篡改（{macro['raw_sha256'][:12]}…）"
            " —— 原始绑定缺失") from exc
    if len(raw_data) != macro["raw_bytes"]:
        raise G7_02Error(
            "E-G7-02-016: macro raw 对象字节数与 manifest 不符 —— 失败关闭")
    # 5) 仓外对象库内容寻址写入（输入正文可含真实值，只落仓外库）。
    stored_company = store.store(KIND_COMPANY_INPUT, _canonical(company_input))
    if stored_company != company.input_sha256:
        raise G7_02Error(
            "E-G7-02-016: company input 落库 digest 与计算哈希不符")
    stored_manifest = store.store(KIND_MACRO_MANIFEST, _canonical(macro_manifest))
    if stored_manifest != manifest_sha:
        raise G7_02Error(
            "E-G7-02-016: macro manifest 落库 digest 与计算哈希不符")
    # 6) 复用权威最终候选冻结（不复制重算）；真实材料事实值进入 frozen context。
    import_snapshot = _decision_snapshot(rd_import)
    payload = build_candidate_request(
        company=company, macro_manifest=macro, manifest_sha=manifest_sha,
        company_raw_sha256=company_raw_sha256,
        macro_manifest_raw_sha256=macro_manifest_raw_sha256,
        import_rights_snapshot=import_snapshot,
        contract_id=contract_id, run_id=run_id, source_commit=source_commit,
        source_tree=source_tree, cutoff_at=cutoff_at, as_of_date=as_of_date)
    candidate = freeze_final_candidate_from_payload(
        store, payload, source_commit=source_commit, source_tree=source_tree)
    # 7) 复用可复验 bundle API 确认完整。
    CandidateFreezeService(store).verify_candidate_bundle(
        candidate.candidate_id, expected_source_commit=source_commit,
        expected_source_tree=source_tree)
    # 8) pack 内容寻址（不含真实值）。
    pack = build_pack(
        company=company, macro_manifest=macro, manifest_sha=manifest_sha,
        manifest_raw_sha256=macro_manifest_raw_sha256,
        company_raw_sha256=company_raw_sha256,
        import_rights_snapshot=import_snapshot,
        candidate=candidate,
        request_hash=candidate.candidate["request_hash"],
        source_commit=source_commit, source_tree=source_tree,
        cutoff_at=cutoff_at, as_of_date=as_of_date, contract_id=contract_id)
    try:
        validate_object(PACK_KIND, pack)
    except SchemaError as exc:
        raise G7_02Error(
            f"E-G7-02-017: pack 不符合 canonical schema（{exc}）") from exc
    pack_bytes = _canonical(pack)
    pack_id = _sha256_hex(pack_bytes)
    stored_pack = store.store(PACK_KIND, pack_bytes)
    if stored_pack != pack_id:
        raise G7_02Error("E-G7-02-016: pack 落库 digest 与内容哈希不符")
    return PackFreeze(pack_id=pack_id, pack=pack,
                      candidate_id=candidate.candidate_id,
                      request_hash=candidate.candidate["request_hash"],
                      candidate_status=pack["candidate_status"],
                      company_data_status=company.data_status,
                      missing_periods=list(company.missing_periods),
                      missing_bindings=list(company.missing_bindings))


def verify_pack(store: ArtifactStore, pack_id: str, *, company_input,
                macro_manifest, source_commit: str, source_tree: str,
                macro_raw: Optional[bytes] = None,
                company_raw_sha256: Optional[str] = None,
                macro_manifest_raw_sha256: Optional[str] = None,
                guard: Optional[RightsGuard] = None,
                import_source_key: str = IMPORT_SOURCE_KEY) -> dict:
    """离线复验 G7-02 pack 与其全部依赖。

    任一依赖缺失、哈希漂移、覆盖漂移、request↔pack 交叉绑定错绑、candidate
    篡改、source revision 漂移或发布轴提升都稳定抛 G7_02Error（失败关闭），
    绝不返回部分成功。macro raw 默认直接从 object store 加载；提供 --macro-raw
    时做额外交叉比对。
    """
    guard = guard or RightsGuard()
    # 收口：verify 同样在入口校验 —— import_source_key 精确等于 SRC_CNINFO，
    # raw 哈希为严格 sha256（与 freeze 一致，防验证路径绕过）。
    if import_source_key != IMPORT_SOURCE_KEY:
        raise G7_02Error(
            "E-G7-02-020: import_source_key 必须精确等于 SRC_CNINFO —— "
            "禁止以别的权利源冒充发行人来源")
    _require_sha256(company_raw_sha256, "company_raw_sha256",
                    code="E-G7-02-020")
    _require_sha256(macro_manifest_raw_sha256, "macro_manifest_raw_sha256",
                    code="E-G7-02-020")
    validate_source_revision(source_commit, source_tree)
    try:
        data = store.load(pack_id)      # 读时哈希校验：pack 自身篡改即拒
    except (TypeError, ValueError, OSError) as exc:
        raise G7_02Error(
            f"E-G7-02-020: pack 缺失或被篡改（{str(pack_id)[:12]}…）"
            f"—— {exc}") from exc
    pack = _strict_json_obj(data, "pack")
    try:
        validate_object(PACK_KIND, pack)
    except SchemaError as exc:
        raise G7_02Error(
            f"E-G7-02-017: pack 不符合 canonical schema（{exc}）") from exc

    # 1) 外部输入哈希重算（改字节即失败）。
    company_sha = _sha256_hex(_canonical(company_input))
    manifest_sha = _sha256_hex(_canonical(macro_manifest))
    if company_sha != pack["company"]["input_sha256"]:
        raise G7_02Error("E-G7-02-020: company input 哈希漂移 —— 输入改字节")
    if manifest_sha != pack["macro"]["manifest_sha256"]:
        raise G7_02Error("E-G7-02-020: macro manifest 哈希漂移 —— 输入改字节")
    if company_raw_sha256 is None \
            or company_raw_sha256 != pack["company"]["input_raw_sha256"]:
        raise G7_02Error("E-G7-02-020: company raw 哈希漂移 —— 失败关闭")
    if macro_manifest_raw_sha256 is None \
            or macro_manifest_raw_sha256 != pack["macro"]["manifest_raw_sha256"]:
        raise G7_02Error("E-G7-02-020: macro manifest raw 哈希漂移 —— 失败关闭")

    # 2) macro raw 完整性：默认从对象库加载（读时哈希校验）；提供 raw 交叉比对。
    if macro_raw is None:
        try:
            stored_raw = store.load(pack["macro"]["raw_sha256"])
        except (TypeError, ValueError, OSError) as exc:
            raise G7_02Error(
                f"E-G7-02-022: macro raw 对象缺失或被篡改（{exc}）"
                " —— 失败关闭") from exc
        if len(stored_raw) != pack["macro"]["raw_bytes"]:
            raise G7_02Error(
                "E-G7-02-020: macro raw 对象字节数与 pack 不符 —— 失败关闭")
    else:
        raw_sha = _sha256_hex(macro_raw)
        if raw_sha != pack["macro"]["raw_sha256"] \
                or len(macro_raw) != pack["macro"]["raw_bytes"]:
            raise G7_02Error("E-G7-02-020: macro raw 哈希/字节漂移")

    # 3) 公司输入完整性重验 + 覆盖/期间/状态逐项比对。
    company = validate_company_input(company_input)
    if company.data_status != pack["company"]["data_status"] \
            or company.missing_periods != pack["missing_periods"] \
            or company.missing_bindings != pack["missing_bindings"] \
            or company.period_status != pack["company"]["period_status"] \
            or company.material_fact_count != pack["company"]["material_fact_count"] \
            or company.material_verified_count != pack["company"]["material_verified_count"] \
            or company.fact_coverage != pack["material_fact_coverage"] \
            or company.source_complete != pack["company"]["source_complete"]:
        raise G7_02Error(
            "E-G7-02-021: 材料性事实覆盖/期间/状态漂移 —— 失败关闭")

    # 4) 宏观 manifest 重验（含 cutoff 越界复判 + revision 漂移复判）。
    macro, recomputed_manifest_sha = validate_macro_manifest(
        macro_manifest, cutoff_at=pack["cutoff_at"],
        source_commit=source_commit, source_tree=source_tree, guard=guard)

    # 5) 当前矩阵权利复验：600089 IMPORT 必须仍 ALLOWED。
    rd_import = require_rights(guard, import_source_key, "IMPORT", SCOPE_600089,
                               "600089 人工导入")
    import_snapshot = _decision_snapshot(rd_import)
    if import_snapshot != pack["company"]["import_rights_decision"]:
        raise G7_02Error(
            "E-G7-02-022: IMPORT rights decision 与 pack 绑定不符 —— 失败关闭")

    # 6) 交叉绑定：加载 stored request，逐字比对 request context.contract 的
    #    company canonical/raw 哈希、macro manifest/raw 哈希、issuer 绑定、
    #    scope/cutoff/snapshot 与 pack；错绑 E-G7-02-022 失败。
    try:
        req_bytes = store.load(pack["g6a_candidate"]["request_hash"])
    except (TypeError, ValueError, OSError) as exc:
        raise G7_02Error(
            f"E-G7-02-022: 受管 request 对象缺失或被篡改（{exc}）"
            " —— 失败关闭") from exc
    req = _strict_json_obj(req_bytes, "request")
    try:
        contract = req["context"]["contract"]
        rev = req["source_revision"]
    except (KeyError, TypeError) as exc:
        raise G7_02Error(
            "E-G7-02-022: 受管 request 形状非法 —— 失败关闭") from exc
    bindings = [
        ("g7_02_company_input_sha256", pack["company"]["input_sha256"]),
        ("g7_02_company_raw_sha256", pack["company"]["input_raw_sha256"]),
        ("g7_02_macro_manifest_sha256", pack["macro"]["manifest_sha256"]),
        ("g7_02_macro_manifest_raw_sha256", pack["macro"]["manifest_raw_sha256"]),
        ("g7_02_macro_raw_sha256", pack["macro"]["raw_sha256"]),
        ("g7_02_macro_snapshot_id", SNAPSHOT_ID),
    ]
    for rkey, expected in bindings:
        actual = contract.get(rkey)
        if actual != expected:
            raise G7_02Error(
                "E-G7-02-022: candidate request 与 pack 交叉绑定不符"
                f"（{rkey}）—— 失败关闭")
    if contract.get("issuer_source_id") != IMPORT_SOURCE_KEY \
            or contract.get("issuer_source_family") != IMPORT_SOURCE_FAMILY \
            or contract.get("issuer_action") != IMPORT_ACTION:
        raise G7_02Error(
            "E-G7-02-022: candidate request 缺 issuer 绑定 —— 失败关闭")
    if contract.get("issuer_rights_decision") != pack["company"]["import_rights_decision"]:
        raise G7_02Error(
            "E-G7-02-022: candidate request issuer rights decision 与 pack 不符"
            " —— 失败关闭")
    if contract.get("scope") != SCOPE_600089 \
            or contract.get("cutoff_at") != pack["cutoff_at"] \
            or contract.get("g7_02_macro_snapshot_id") != SNAPSHOT_ID:
        raise G7_02Error(
            "E-G7-02-022: candidate request scope/cutoff/snapshot 与 pack 不符"
            " —— 失败关闭")
    if rev.get("source_commit") != source_commit \
            or rev.get("source_tree") != source_tree:
        raise G7_02Error(
            "E-G7-02-022: 受管 request source revision 与当前代码版本漂移"
            " —— 失败关闭")

    # 收口：不只比哈希 —— 按已验证 company input 重建预期 context.facts 并
    # 逐字比对；重建并比对完整 macro descriptor；比对 contract_id/workflow/
    # market_scope/currency/as_of_date/cutoff、valuation_inputs 与 pack。
    # 候选请求事实值错绑但 hash 字段自洽也必须失败。
    expected_facts = {f"g7_02_{metric}": fact["value"]
                      for metric, fact in company.material_facts.items()}
    if req["context"].get("facts") != expected_facts:
        raise G7_02Error(
            "E-G7-02-022: candidate request context.facts 与已验证公司输入"
            "重建不符（事实值错绑）—— 失败关闭")
    expected_macro = _macro_descriptor(macro, recomputed_manifest_sha)
    if req["context"].get("macro") != expected_macro:
        raise G7_02Error(
            "E-G7-02-022: candidate request macro descriptor 与重建不符"
            " —— 失败关闭")
    for fld in ("contract_id", "scope", "workflow", "market_scope",
                "currency", "as_of_date", "cutoff_at"):
        if contract.get(fld) != pack["research_contract"].get(fld):
            raise G7_02Error(
                f"E-G7-02-022: candidate request {fld} 与 pack 不符"
                " —— 失败关闭")
    expected_vi = {"scope": SCOPE_600089, "currency": CURRENCY,
                   "as_of": pack["as_of_date"]}
    if req["context"].get("valuation_inputs") != expected_vi:
        raise G7_02Error(
            "E-G7-02-022: candidate request valuation_inputs 与 pack 不符"
            " —— 失败关闭")

    # 7) G6A candidate 完整 bundle 复验（candidate/request/11 产品正文/哈希）。
    candidate_id = pack["g6a_candidate"]["candidate_id"]
    try:
        verified = CandidateFreezeService(store).verify_candidate_bundle(
            candidate_id, expected_source_commit=source_commit,
            expected_source_tree=source_tree)
    except CandidateVerificationError as exc:
        raise G7_02Error(
            f"E-G7-02-022: G6A candidate bundle 复验失败（{exc}）"
            " —— 失败关闭") from exc
    if verified["request_hash"] != pack["g6a_candidate"]["request_hash"]:
        raise G7_02Error(
            "E-G7-02-022: candidate request_hash 与 pack 记录不符")
    if verified["product_count"] != pack["g6a_candidate"]["product_count"]:
        raise G7_02Error("E-G7-02-022: candidate 产品数与 pack 记录不符")

    # 8) 依赖对象逐一可读（内容寻址读时哈希校验：改字节/缺失即拒）。
    for dep in (pack["company"]["input_sha256"],
                pack["macro"]["manifest_sha256"],
                pack["macro"]["raw_sha256"],
                pack["g6a_candidate"]["request_hash"]):
        try:
            store.load(dep)
        except (TypeError, ValueError, OSError) as exc:
            raise G7_02Error(
                f"E-G7-02-022: 依赖对象缺失或被篡改（{dep[:12]}…）—— {exc}") \
                from exc

    # 9) 分轴：candidate 质量 / Gate 状态 / 发布资格互不提升。
    if pack["gate_status"].get("gate7_reached") is not False \
            or pack["gate_status"].get("gate_release_eligible") is not False:
        raise G7_02Error("E-G7-02-023: gate 轴被提升 —— 失败关闭")
    if any(pack["write_counts"].get(axis, 1) != 0 for axis in WRITE_AXES):
        raise G7_02Error("E-G7-02-023: 发布/决策写入计数非零 —— 失败关闭")
    if pack.get("single_source_disclosed") != SINGLE_SOURCE_DISCLOSED:
        raise G7_02Error("E-G7-02-023: 缺 SINGLE_SOURCE_DISCLOSED")
    if pack.get("reviewer_independence") != SINGLE_REVIEWER_ATTESTED:
        raise G7_02Error("E-G7-02-023: 缺 SINGLE_REVIEWER_ATTESTED")
    if pack["g6a_candidate"].get("release_eligible") is not False:
        raise G7_02Error("E-G7-02-023: G6A candidate 不得发布资格")

    # 10) source revision 漂移。
    if pack.get("source_commit") != source_commit \
            or pack.get("source_tree") != source_tree:
        raise G7_02Error(
            "E-G7-02-024: pack source revision 与当前期望代码版本漂移"
            " —— 失败关闭")

    return {
        "pack_id": pack_id,
        "source_commit": source_commit,
        "source_tree": source_tree,
        "candidate_status": pack["candidate_status"],
        "company_data_status": pack["company"]["data_status"],
        "missing_periods": list(pack["missing_periods"]),
        "missing_bindings": list(pack["missing_bindings"]),
        "candidate_id": candidate_id,
        "product_count": verified["product_count"],
        "quality_status": pack["g6a_candidate"]["quality_status"],
        "release_eligible": pack["g6a_candidate"]["release_eligible"],
        "gate7_reached": False,
        "gate_release_eligible": False,
        "reviewer_independence": SINGLE_REVIEWER_ATTESTED,
        "write_counts": {axis: 0 for axis in WRITE_AXES},
        "single_source_disclosed": SINGLE_SOURCE_DISCLOSED,
    }
