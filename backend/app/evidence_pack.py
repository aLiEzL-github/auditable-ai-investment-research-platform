"""evidence_pack.py —— G6A-01 冻结 evidence_pack_id 与角色权限 + 注入检测。

基线验收（G6A-01）：
  · 统一证据包、工具和预算白名单、注入语料负向用例
  · 角色不能访问未授权证据或发布服务
  · 证据包内嵌入的诱导指令不能改变工具白名单、证据分级或越过证据门，
    命中即记 SUSPECTED_PROMPT_INJECTION 并转人工

执行计划要点（G6A-执行计划.md §4）：
  F-1  注入检测不依赖 LLM；负测：构造注入载荷须被检出；先红后绿；须报检查对象数
  F-2  首轮哈希冻结：首轮结果在任何对抗轮次之前冻结，实测断言时序
  F-7  共识不等于已验证（一票否决）—— 字段级可分辨（见 consumption_kind）

设计：
  · freeze_evidence_pack：内容寻址冻结（publish_engine.freeze_object），
    evidence_pack_id = sha256(规范字节)。白名单与证据分级是包内容的一部分
    —— 任何嵌入指令都不可能「改变」它们：改一个字节即换一个包 id。
  · 角色权限：ROLE_PERMISSIONS —— 角色 → 可访问的包 id 集 / 工具白名单 /
    预算上限。研究角色一律无发布权（G0-04 写权矩阵；发布仅 L11/L12 语义）。
  · 注入检测：INJECTION_PATTERNS 语料（正则 + 描述）。命中即记
    SUSPECTED_PROMPT_INJECTION 并转人工（consume() 在有人工复核决定前拒绝）。
  · 首轮冻结时序：freeze 事件由调用方注入 (微秒时间戳, 同刻序号)（见
    time_order.py，G6C-执行计划.md 附.6 的时序精度定义同样适用于 F-2）。
"""
import hashlib
import re
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from artifact_store import ArtifactStore
from publish_engine import canonical_bytes

# ════════════════════════════════════════════════════════════════
# 角色与权限（G6A-02 六个角色是消费者；本任务冻结权限模型本身）
# ════════════════════════════════════════════════════════════════

RESEARCH_ROLES = ("financial", "macro", "valuation", "bull", "bear", "risk")

PACK_KIND = "evidence_pack"


class EvidencePackError(ValueError):
    pass


class RoleDenied(EvidencePackError):
    """角色访问未授权证据/工具/预算/发布服务。"""


class InjectionDetected(EvidencePackError):
    """证据包内嵌诱导指令命中 —— 转人工，消费被阻断。"""


class PackFrozen(EvidencePackError):
    """包已冻结后任何改写路径在结构上不可达（内容寻址）。"""


@dataclass
class InjectionHit:
    pattern_id: str
    match_text: str          # 命中片段（有限截断）
    location: str            # 包内位置，如 items/ev-01/content
    payload_hash: str        # 被检对象哈希（就地重算，非自声明）


@dataclass
class InjectionReport:
    inspected: int           # 检查对象数（⑨：N 与 0 可分辨）
    hits: List[InjectionHit] = field(default_factory=list)

    @property
    def suspected(self) -> bool:
        return len(self.hits) > 0

    def summary(self) -> str:
        if not self.hits:
            return f"OK（检查 {self.inspected} 个对象，零命中）"
        return (f"SUSPECTED_PROMPT_INJECTION（检查 {self.inspected} 个对象，"
                f"命中 {len(self.hits)} 处："
                + ", ".join(h.pattern_id for h in self.hits) + "）")


# ════════════════════════════════════════════════════════════════
# 注入语料（F-1：不依赖 LLM —— 纯模式匹配 + 负向用例）
# ════════════════════════════════════════════════════════════════
# 每条：pattern_id / 正则 / 目标。语料可扩展，但缺任一条都不得声称
# 「注入检测已实现」—— 由变异注入测试强制（删一条 → 负测转红）。

INJECTION_PATTERNS = [
    {
        "id": "IGNORE_PREVIOUS",
        "re": re.compile(r"(?i)ignore\s+(all\s+)?(previous|prior)\s+(instructions|rules|context|指令|规则)"),
        "target": "抹除上下文",
    },
    {
        "id": "DISREGARD_ABOVE",
        "re": re.compile(r"(?i)(disregard|forget|discard)\s+(the\s+)?(above|prior|previous)\s+(text|instructions|context|规则|以上)"),
        "target": "抹除上下文",
    },
    {
        "id": "IGNORE_PREVIOUS_ZH",
        "re": re.compile(r"(?i)(忽略|无视)\s*(以上|上述|所有|全部){0,2}\s*(规则|指令|指示|内容)"),
        "target": "抹除上下文（中文）",
    },
    {
        "id": "ROLE_SWITCH",
        "re": re.compile(r"(?i)you\s+are\s+now\s*[:：]?\s*[A-Za-z\u4e00-\u9fff]+(\s+[A-Za-z\u4e00-\u9fff]+){0,4}"),
        "target": "角色冒充（诱导以非研究身份行事）",
    },
    {
        "id": "OVERRIDE_WHITELIST",
        "re": re.compile(r"(?i)(override|bypass|ignore|unlock)\s*.{0,40}(whitelist|white\s?list|tool|工具|白名单)"),
        "target": "改写工具白名单",
    },
    {
        "id": "REGRADE_EVIDENCE",
        "re": re.compile(r"(?i)(change|upgrade|downgrade|regrade|reclassify)\s*.{0,40}(grade|grading|分级|证据)"),
        "target": "改写证据分级",
    },
    {
        "id": "EVIDENCE_GATE_BYPASS",
        "re": re.compile(r"(?i)(bypass|skip|绕过|跳过)\s*.{0,40}(evidence\s*gate|gate|证据门|门控)"),
        "target": "越过证据门",
    },
    {
        "id": "REVEAL_SECRET",
        "re": re.compile(r"(?i)(reveal|print|output|show|give\s+me)\s*.{0,30}(password|secret|token|key|密钥|口令|凭据)"),
        "target": "窃取凭据",
    },
    {
        "id": "EXECUTE_COMMAND",
        "re": re.compile(r"(?i)(execute|run|call)\s+(shell|command|script|python|curl|脚本|命令)"),
        "target": "执行命令",
    },
]


def scan_for_injection(payload: dict) -> InjectionReport:
    """对包内容逐对象扫描。命中不改变任何白名单/分级 ——
    它们已在冻结字节里；本函数只产出报告。

    ⑨：inspected = 实际扫描的对象数（0 与 N 可分辨），
    不依赖调用方自报（就地枚举包内全部文本字段）。
    """
    hits: List[InjectionHit] = []
    inspected = 0

    def walk(obj, location: str) -> None:
        nonlocal inspected
        if isinstance(obj, dict):
            for k, v in obj.items():
                walk(v, f"{location}/{k}")
        elif isinstance(obj, list):
            for i, v in enumerate(obj):
                walk(v, f"{location}[{i}]")
        elif isinstance(obj, str):
            inspected += 1
            for pat in INJECTION_PATTERNS:
                m = pat["re"].search(obj)
                if m:
                    hits.append(InjectionHit(
                        pattern_id=pat["id"],
                        match_text=m.group(0)[:60],
                        location=location,
                        payload_hash=hashlib.sha256(
                            obj.encode("utf-8")).hexdigest()))

    walk(payload, "pack")
    return InjectionReport(inspected=inspected, hits=hits)


# ════════════════════════════════════════════════════════════════
# 冻结与权限
# ════════════════════════════════════════════════════════════════

def validate_pack_shape(pack: dict) -> None:
    """冻结前形状校验（⑱：夹具形状与真实契约一致）。"""
    if not isinstance(pack.get("scope_id"), str) or not pack["scope_id"]:
        raise EvidencePackError("E-G6A-01-001: 证据包缺 scope_id")
    if not isinstance(pack.get("tool_whitelist"), list) or not pack["tool_whitelist"]:
        raise EvidencePackError("E-G6A-01-002: 证据包缺非空工具白名单")
    if not isinstance(pack.get("budget_whitelist"), dict):
        raise EvidencePackError("E-G6A-01-003: 证据包缺预算白名单")
    items = pack.get("items")
    if not isinstance(items, list) or not items:
        raise EvidencePackError("E-G6A-01-004: 证据包缺非空 items")
    for it in items:
        if not isinstance(it.get("item_id"), str):
            raise EvidencePackError("E-G6A-01-005: item 缺 item_id")
        if it.get("grading") not in ("GRADED", "UNGRADED", "ADVERSARIAL"):
            raise EvidencePackError(
                f"E-G6A-01-006: 非法证据分级: {it.get('grading')!r}")


def freeze_evidence_pack(store: ArtifactStore, pack: dict,
                         registered_at: Tuple[str, int]) -> str:
    """内容寻址冻结证据包。返回 evidence_pack_id。

    F-2：registered_at（微秒时间戳, 同刻序号）随冻结事件一并记录在包内；
    对抗轮次（消费/注入扫描的后续轮次）开始时刻须晚于它 —— 时序断言
    用 (ts, seq) 字典序（time_order.cmp_micro），不用显示值比较。
    """
    validate_pack_shape(pack)
    pack = dict(pack)
    pack.setdefault("schema_version", "1.0.0")
    pack["frozen_at_ts"], pack["frozen_at_seq"] = registered_at
    data = canonical_bytes(pack)
    store.store(PACK_KIND, data)
    return __import__("hashlib").sha256(data).hexdigest()


def load_pack(store: ArtifactStore, pack_id: str) -> dict:
    """按 id 读回（读时哈希校验 = 篡改必拒，G2-02）。"""
    import json
    data = store.load(pack_id)
    return json.loads(data.decode("utf-8"))


def role_can_access(role: str, pack_id: str,
                    role_permissions: Dict[str, dict]) -> bool:
    """角色只能访问被授予的证据包 id 集（授权证据）。"""
    if role not in role_permissions:
        return False
    return pack_id in role_permissions[role].get("evidence_pack_ids", ())


def role_can_use_tool(role: str, tool: str,
                      role_permissions: Dict[str, dict]) -> bool:
    """工具白名单按角色授予。"""
    if role not in role_permissions:
        return False
    return tool in role_permissions[role].get("tools", ())


def role_budget_cap(role: str, role_permissions: Dict[str, dict]) -> float:
    if role not in role_permissions:
        return 0.0
    return float(role_permissions[role].get("budget_cap", 0.0))


def assert_role_access(role: str, pack_id: str, tool: str,
                       budget: float,
                       role_permissions: Dict[str, dict]) -> None:
    """F-7 之外的角色门：未授权证据/工具/超预算一律拒绝（默认拒绝）。"""
    if not role_can_access(role, pack_id, role_permissions):
        raise RoleDenied(
            f"E-G6A-01-010: 角色 {role} 无权访问证据包 {pack_id[:12]}…"
            f"（默认拒绝 —— 未授权即拒，不做清单外放行）")
    if not role_can_use_tool(role, tool, role_permissions):
        raise RoleDenied(f"E-G6A-01-011: 角色 {role} 无权使用工具 {tool}")
    if budget > role_budget_cap(role, role_permissions) + 1e-9:
        raise RoleDenied(
            f"E-G6A-01-012: 角色 {role} 预算超上限 {budget}"
            f" > {role_budget_cap(role, role_permissions)}")


def assert_role_cannot_publish(role: str) -> None:
    """角色不能访问发布服务 —— 发布仅 L11/L12 人工语义（G0-04 §2）。"""
    raise RoleDenied(
        f"E-G6A-01-013: 角色 {role} 无发布权 —— 发布服务仅人工批准路径可达")


# ════════════════════════════════════════════════════════════════
# 消费门：注入命中 → 转人工
# ════════════════════════════════════════════════════════════════

@dataclass
class ConsumptionRecord:
    """消费决定。注入命中时只能是 HUMAN_REVIEW ——
    嵌入指令不得越过证据门（一票否决项的行为验证）。"""
    pack_id: str
    consumption_kind: str      # INJECTION_CLEAN / HUMAN_REVIEW
    human_decision: Optional[str] = None   # APPROVE / REJECT（人工复核决定）
    reviewed_at: Optional[str] = None


def consume(pack_id: str, report: InjectionReport,
            human_decision: Optional[str] = None,
            reviewed_at: Optional[str] = None) -> ConsumptionRecord:
    """消费门：命中注入的包，在人工复核批准前不得进入任何工具/分级路径。

    未命中 → INJECTION_CLEAN（可进入既有证据门流程）。
    命中且无人工决定 → 拒绝（fail-closed，不接受「先走流程后补复核」）。
    """
    if not report.suspected:
        return ConsumptionRecord(pack_id=pack_id, consumption_kind="INJECTION_CLEAN")
    if human_decision is None:
        raise InjectionDetected(
            f"E-G6A-01-020: {report.summary()} —— 命中注入，须转人工复核"
            f"（无人工决定不得消费）")
    if human_decision not in ("APPROVE", "REJECT"):
        raise EvidencePackError(f"E-G6A-01-021: 非法人工决定: {human_decision!r}")
    return ConsumptionRecord(pack_id=pack_id, consumption_kind="HUMAN_REVIEW",
                             human_decision=human_decision,
                             reviewed_at=reviewed_at)
