"""publish_engine.py —— G4 通用发布引擎（审计·批准·不可变发布·更新）。

基线 B §7（Gate 4）：只使用脱敏、冻结 fixture 验证通用发布引擎。
计划发布的固定 current key：
  · system-design-plan/auditable-ai-investment-research-platform
  · a-share-single-company-research/600089.SH
两者不得共享指针、不得互为 parent（D-4 分域）。
真实来源候选可生成但不得形成真实 DecisionVersion —— 须等 G7-00
最终对象闭合与 Gate 7 终审（基线 B §7）。

§9 证明义务（本 Gate）：
  · 对象闭包（D-1）、CurrentKey 分域（D-4）、subject root（D-2）、
    CAS（D-3）、幂等（D-7）、离线复建（D-8/D-9）
一票否决：
  · PROVENANCE_ONLY 冒充完整复验（D-10/D-11）
  · 孤儿成为 current（D-5）

本模块仅依赖 stdlib 与既有 artifact_store / repository ——
离线复建路径（rebuild_from_store）连 repository 都不依赖，
可在干净环境（python -S -I / 新 venv）中执行（D-9）。
断网探针（socket）不在此模块 —— 见 network_probe.py，由调用方注入。
"""
import hashlib
import json
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple

from artifact_store import ArtifactStore

# ════════════════════════════════════════════════════════════════
# 内容寻址（G4-01，D-3 CAS）
# ════════════════════════════════════════════════════════════════

def canonical_bytes(obj) -> bytes:
    """规范序列化：key 排序、无空白 —— 内容寻址的唯一字节形态。"""
    return json.dumps(obj, sort_keys=True, ensure_ascii=False,
                      separators=(",", ":")).encode("utf-8")


def content_id(obj) -> str:
    """对象 id = sha256(规范字节)。同内容必同 id（D-3）。"""
    return hashlib.sha256(canonical_bytes(obj)).hexdigest()


# ════════════════════════════════════════════════════════════════
# CurrentKey（G4-03，D-4 分域）
# ════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class CurrentKey:
    workflow: str
    scope_id: str
    current_key: str = ""

    def __str__(self) -> str:
        base = f"{self.workflow}/{self.scope_id}"
        return f"{base}/{self.current_key}" if self.current_key else base

    @classmethod
    def parse(cls, s: str) -> "CurrentKey":
        parts = s.split("/")
        if len(parts) == 2:
            return cls(parts[0], parts[1], "")
        if len(parts) == 3:
            return cls(parts[0], parts[1], parts[2])
        raise ValueError(f"E-G4-03-001: CurrentKey 须为 workflow/scope_id[/current_key]: {s!r}")


SYS_DESIGN_KEY = CurrentKey("system-design-plan", "auditable-ai-investment-research-platform")
RESEARCH_600089_KEY = CurrentKey("a-share-single-company-research", "600089.SH")


def assert_domains_disjoint(keys: Sequence[CurrentKey]) -> None:
    """固定 current key 完全分域：不得共享指针（同一 key 重复出现即冲突）。"""
    seen = set()
    for k in keys:
        if str(k) in seen:
            raise ValueError(f"E-G4-03-002: 固定 current key 重复（共享指针）: {k}")
        seen.add(str(k))


# ════════════════════════════════════════════════════════════════
# G4-01 冻结：候选 / 全目录哈希 / 清单
# ════════════════════════════════════════════════════════════════

def freeze_object(store: ArtifactStore, kind: str, obj: dict) -> str:
    """内容寻址冻结：id = sha256(规范字节)；同内容必同 id（D-3）。

    同内容不同路径（不同 kind 名）入库 —— kind 名受 store 写入名约束，
    内容寻址由 digest 决定，路径只影响对象「名」，不影响 id（D-3 验收 1）。
    """
    data = canonical_bytes(obj)
    store.store(kind, data)
    return hashlib.sha256(data).hexdigest()


def freeze_candidate(store: ArtifactStore, candidate: dict) -> str:
    """G4-01 候选冻结：候选身份 = 内容哈希。改动后哈希与候选身份变化。"""
    candidate = dict(candidate)
    candidate.setdefault("schema_version", "1.0.0")
    return freeze_object(store, "candidate", candidate)


def directory_hash(root_dir: str) -> str:
    """G4-01 全目录哈希：按相对路径排序，逐文件 sha256 汇入。

    任一字节能改动目录哈希（验收：改动后哈希变化）。
    """
    entries: List[str] = []
    for dirpath, dirnames, filenames in os.walk(root_dir):
        dirnames.sort()
        filenames.sort()
        for fn in filenames:
            fp = os.path.join(dirpath, fn)
            rel = os.path.relpath(fp, root_dir)
            entries.append(f"{rel}:{hashlib.sha256(open(fp, 'rb').read()).hexdigest()}")
    return hashlib.sha256("\n".join(entries).encode("utf-8")).hexdigest()


def freeze_manifest(store: ArtifactStore, manifest: dict) -> str:
    """G4-01 清单冻结：对象闭包登记表 + 单一 subject root + 全目录哈希。

    幂等（D-7）：同一输入连跑三次，产物（id）哈希一致。
    """
    manifest = dict(manifest)
    manifest.setdefault("schema_version", "1.0.0")
    return freeze_object(store, "manifest", manifest)


# ════════════════════════════════════════════════════════════════
# G4-07 对象闭包与 subject root（D-1 / D-2）
# ════════════════════════════════════════════════════════════════

OBJECT_KINDS = ("candidate", "manifest", "evidence", "macro", "assumption",
                "calc", "claim", "worksheet", "test", "code_config",
                "open_item", "approval", "report")


@dataclass
class ClosureResult:
    complete: bool                    # 闭包完整 = 登记表全可达 ∧ 无外引用
    count: int                        # 闭包内对象数（⑨：0 与完整可分辨）
    reachable: Set[str]
    registered: Set[str]
    dangling: Set[str]                # 被引用但未登记（漏登记 → D-1 变异）
    dead: Set[str]                    # 已登记但不可达（闭包外非空）
    mismatch: Set[str]                # 版本漂移 / 内容与摘要不符


def resolve_subject_root(manifest: dict) -> str:
    """D-2：subject root 单一且明确。两个候选 root 必须 FAIL，不得任选其一。"""
    cands = manifest.get("subject_root_candidates")
    if cands is not None:
        if len(cands) != 1:
            raise ValueError(
                f"E-G4-07-003: subject root 候选 {len(cands)} 个 —— 必须恰好 1 个，"
                f"不得任选（{cands}）")
        root = cands[0]
    else:
        root = manifest.get("subject_root")
        if not root:
            raise ValueError("E-G4-07-003: manifest 缺 subject_root")
    return root


def compute_closure(store: ArtifactStore, manifest: dict) -> ClosureResult:
    """D-1：从 subject root BFS 全可达，且闭包外为空（漏登记/死对象必 FAIL）。

    ⑨：「闭包内 0 个对象」与「闭包完整」必须可分辨 —— complete 与 count
    是两个独立字段；发布侧要求 complete 且 count ≥ 1。
    """
    root = resolve_subject_root(manifest)
    registered: Set[str] = set(manifest.get("objects", {}))
    if root not in registered:
        raise ValueError(f"E-G4-07-004: subject root 未登记: {root[:12]}…")

    reachable: Set[str] = set()
    stack = [root]
    dangling: Set[str] = set()
    while stack:
        oid = stack.pop()
        if oid in reachable:
            continue
        reachable.add(oid)
        meta = manifest["objects"].get(oid)
        if meta is None:
            dangling.add(oid)
            continue
        for ref in meta.get("refs", []):
            if ref not in registered:
                dangling.add(ref)
            elif ref not in reachable:
                stack.append(ref)

    dead = registered - reachable
    mismatch: Set[str] = set()
    for oid in reachable:
        try:
            store.load(oid)                     # 读时哈希校验 = 篡改必拒
        except ValueError:
            mismatch.add(oid)

    complete = not dangling and not dead and not mismatch
    return ClosureResult(
        complete=complete,
        count=len(reachable),
        reachable=reachable,
        registered=registered,
        dangling=dangling,
        dead=dead,
        mismatch=mismatch,
    )


def audit_open_items(store: ArtifactStore, manifest: dict) -> None:
    """G4-07：未关材料性开放项使 release_eligible=false。

    闭包内 kind=open_item 的对象：status=OPEN ∧ material=true → FAIL。
    """
    for oid, meta in manifest.get("objects", {}).items():
        if meta.get("kind") != "open_item":
            continue
        try:
            obj = json.loads(store.load(oid).decode("utf-8"))
        except ValueError:
            continue
        if obj.get("status") == "OPEN" and obj.get("material"):
            raise ValueError(f"E-G4-07-005: 未关材料性开放项在闭包内: "
                             f"{obj.get('title', oid[:12])}")


def assert_cross_domain_clean(manifest: dict) -> None:
    """G4-07：闭包对象不得跨 workflow/scope —— 与清单不一致即 FAIL。"""
    for oid, meta in manifest.get("objects", {}).items():
        w = meta.get("workflow")
        s = meta.get("scope_id")
        if w is not None and w != manifest.get("workflow"):
            raise ValueError(f"E-G4-07-006: 对象跨 workflow: {oid[:12]}… {w} "
                             f"≠ {manifest.get('workflow')}")
        if s is not None and s != manifest.get("scope_id"):
            raise ValueError(f"E-G4-07-006: 对象跨 scope: {oid[:12]}… {s} "
                             f"≠ {manifest.get('scope_id')}")


# ════════════════════════════════════════════════════════════════
# G4-02 审计：完整性 / 来源 / materiality / 计算 / 权利 / 安全 / 覆盖
# ════════════════════════════════════════════════════════════════

NBS_DOMAIN = "stats.gov.cn"
NBS_ATTRIBUTION_LINE = "转自国家统计局网站，www.stats.gov.cn"
PROMINENT_FIRST_LINES = 10      # D-13：「显著位置」= 首屏前 N 行（可机检）


def attribution_guard(report_text: str, source_domains: Iterable[str]) -> None:
    """D-12/D-13：含 stats.gov.cn 数据的产出，首屏前 N 行须含署名。

    缺署名即 FAIL（先红后绿 —— 由测试与守卫双向证明）；「显著位置」
    定义为可机检的首屏前 N 行，与 OI-PF-070 首屏口径一致。
    """
    uses_nbs = any(NBS_DOMAIN in (d or "") for d in source_domains)
    if not uses_nbs:
        return
    head = "\n".join(report_text.splitlines()[:PROMINENT_FIRST_LINES])
    if "转自国家统计局网站" not in head or NBS_DOMAIN not in head:
        raise ValueError(
            f"E-G4-02-009: 缺国家统计局署名 —— 须在首屏前 {PROMINENT_FIRST_LINES} 行内"
            f"注明「转自国家统计局网站」并标明 www.{NBS_DOMAIN}")


@dataclass
class AuditResult:
    gates: Dict[str, str]           # gate 名 → PASS / FAIL（覆盖门报告 N）
    release_eligible: bool
    failures: List[str]

    def report(self) -> str:
        lines = [f"gate {k} = {v}" for k, v in self.gates.items()]
        lines.append(f"release_eligible = {self.release_eligible}")
        return "\n".join(lines)


def audit_candidate(store: ArtifactStore, manifest: dict,
                    candidate_digest: Optional[str] = None) -> AuditResult:
    """G4-02：任一适用质量门非 PASS 或 materially critical Claim 有缺口
    → release_eligible=false。七门逐一断言，覆盖门报适用门数（⑨）。"""
    failures: List[str] = []
    gates: Dict[str, str] = {}

    # ① 完整性
    try:
        closure = compute_closure(store, manifest)
        if not closure.complete or closure.count == 0:
            raise ValueError(
                f"E-G4-02-001: 闭包不完整: dangling={len(closure.dangling)} "
                f"dead={len(closure.dead)} mismatch={len(closure.mismatch)} "
                f"count={closure.count}")
        gates["completeness"] = "PASS"
    except ValueError as e:
        failures.append(str(e))
        gates["completeness"] = "FAIL"

    # ② 来源：每份 evidence/macro 的来源须已登记且可判定
    sources = set()
    for oid, meta in manifest.get("objects", {}).items():
        if meta.get("kind") not in ("evidence", "macro"):
            continue
        try:
            obj = json.loads(store.load(oid).decode("utf-8"))
        except ValueError:
            failures.append(f"E-G4-02-002: 来源对象不可读: {oid[:12]}…")
            gates["source"] = "FAIL"
            continue
        src = obj.get("source_key") or obj.get("source_domain") or ""
        sources.add(obj.get("source_domain", ""))
        verdict = obj.get("rights_verdict")
        if not verdict or verdict == "UNKNOWN":
            failures.append(f"E-G4-02-003: 来源权利未判定（fail-closed）: {src}")
            gates["source"] = "FAIL"
        elif verdict not in ("ALLOWED", "ALLOWED_WITH_ATTRIBUTION"):
            failures.append(f"E-G4-02-004: 来源权利非 ALLOWED: {src} = {verdict}")
            gates["source"] = "FAIL"
    gates.setdefault("source", "PASS")

    # ③ materiality：materially critical Claim 须有 ≥1 条证据边
    critical_gaps = []
    for oid, meta in manifest.get("objects", {}).items():
        if meta.get("kind") != "claim":
            continue
        try:
            obj = json.loads(store.load(oid).decode("utf-8"))
        except ValueError:
            critical_gaps.append(oid)
            continue
        if obj.get("materiality") == "CRITICAL" and not meta.get("refs"):
            critical_gaps.append(obj.get("id", oid[:12]))
    if critical_gaps:
        failures.append(f"E-G4-02-005: materially critical Claim 缺证据边: {critical_gaps}")
        gates["materiality"] = "FAIL"
    else:
        gates["materiality"] = "PASS"

    # ④ 计算：calc 对象的输入须全部在闭包内（冻结输入）
    calc_gaps = []
    for oid, meta in manifest.get("objects", {}).items():
        if meta.get("kind") != "calc":
            continue
        for ref in meta.get("refs", []):
            if ref not in manifest.get("objects", {}):
                calc_gaps.append(ref[:12])
    if calc_gaps:
        failures.append(f"E-G4-02-006: 计算输入未冻结: {calc_gaps}")
        gates["calculation"] = "FAIL"
    else:
        gates["calculation"] = "PASS"

    # ⑤ 权利：含国家统计局的产出须满足强制署名（D-12/D-13）
    try:
        report = render_report_text(store, manifest)
        attribution_guard(report, sources)
        gates["rights"] = "PASS"
    except ValueError as e:
        failures.append(str(e))
        gates["rights"] = "FAIL"

    # ⑥ 安全：闭包内全部对象哈希校验通过（篡改必拒），闭包外引用为零
    try:
        closure = compute_closure(store, manifest)
        if closure.mismatch:
            raise ValueError(f"E-G4-02-007: 对象被篡改（哈希不符）: "
                             f"{sorted(closure.mismatch)[:3]}")
        if closure.dangling or closure.dead:
            raise ValueError("E-G4-02-007: 闭包外引用非空")
        gates["security"] = "PASS"
    except ValueError as e:
        failures.append(str(e))
        gates["security"] = "FAIL"

    # ⑦ 覆盖：适用门数 N 逐门断言（⑨：N=0 与 N 门全过可分辨）
    applicable = [k for k in gates if k != "coverage"]
    if not applicable:
        failures.append("E-G4-02-008: 无适用质量门 —— 空跑不可 PASS")
        gates["coverage"] = "FAIL"
    elif all(v == "PASS" for v in gates.values()):
        gates["coverage"] = f"PASS({len(applicable)} 门适用)"
    else:
        gates["coverage"] = "FAIL"

    eligible = all(v.startswith("PASS") for v in gates.values())
    return AuditResult(gates=gates, release_eligible=eligible,
                       failures=failures)


def render_report_text(store: ArtifactStore, manifest: dict) -> str:
    """报告渲染（fixture 形态）：首屏前 N 行是「显著位置」的机检对象。

    产出以 manifest 闭包中的 kind=report 对象内容为准；
    含 stats.gov.cn 来源时须由生成方把署名放进首屏（D-12）。
    """
    report_objs = [oid for oid, meta in manifest.get("objects", {}).items()
                   if meta.get("kind") == "report"]
    if not report_objs:
        raise ValueError("E-G4-02-010: 闭包内无 report 对象")
    data = store.load(sorted(report_objs)[0])
    return data.decode("utf-8")


# ════════════════════════════════════════════════════════════════
# G4-04 批准：唯一准出谓词 + 哈希绑定人工批准
# ════════════════════════════════════════════════════════════════

APPROVE_TOKEN = "APPROVE"


def approval_subject_root(store: ArtifactStore, manifest: dict,
                          exclude_kinds: Sequence[str] = ("approval",)) -> str:
    """subject root 排除批准事件本身；final manifest 再纳入批准事件。

    根哈希 = 闭包内全部对象（排除批准类）的 (id,digest) 排序后汇入。
    """
    closure = compute_closure(store, manifest)
    if not closure.complete:
        raise ValueError("E-G4-04-001: 闭包不完整，不得计算批准根")
    pairs = []
    for oid in sorted(closure.reachable):
        meta = manifest["objects"][oid]
        if meta.get("kind") in exclude_kinds:
            continue
        pairs.append(f"{oid}:{meta.get('kind', '')}")
    return hashlib.sha256("\n".join(pairs).encode("utf-8")).hexdigest()


def inputs_hash(manifest: dict, candidate_digest: Optional[str] = None) -> str:
    """输入哈希：清单 + 候选 —— 任一输入变化批准失效（G4-04 验收）。"""
    h = hashlib.sha256(canonical_bytes(manifest))
    if candidate_digest:
        h.update(b"|" + candidate_digest.encode())
    return h.hexdigest()


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def create_approval(store: ArtifactStore, session, manifest: dict,
                    approver: str, key: CurrentKey,
                    candidate_digest: Optional[str] = None,
                    approved_at: Optional[str] = None,
                    token: str = APPROVE_TOKEN) -> "ApprovalRow":
    """G4-04：批准绑定完整 CurrentKey + subject root + 输入哈希。

    聊天“继续”不算批准 —— token 必须是显式 APPROVE（L12 端点人工发起）。
    """
    from repository import Approval
    if token != APPROVE_TOKEN:
        raise ValueError(f"E-G4-04-002: 聊天“继续”不算批准 —— token 须为 {APPROVE_TOKEN!r}")
    root = approval_subject_root(store, manifest)
    ih = inputs_hash(manifest, candidate_digest)
    appr = Approval(
        id=f"APR_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%f')}",
        schema_version="1.0.0",
        object_ref=resolve_subject_root(manifest),
        approver=approver,
        approved_at=datetime.fromisoformat((approved_at or _now_iso()).replace("Z", "+00:00")),
        subject_root_hash=root,
        workflow=key.workflow,
        scope_id=key.scope_id,
        current_key=key.current_key,
        inputs_hash=ih,
        status="ACTIVE",
        token=token,
        version=1,
    )
    session.add(appr)
    session.commit()
    return appr


def is_release_eligible(session, approval, manifest: dict,
                        candidate_digest: Optional[str] = None,
                        audit: Optional[AuditResult] = None) -> Tuple[bool, str]:
    """唯一准出谓词：批准 ACTIVE ∧ 输入哈希一致 ∧ 审计 eligible。

    人工风险接受不能绕门：仅凭 risk_acceptance 标记不改变结论。
    """
    from repository import Approval
    appr = session.get(Approval, approval.id) if hasattr(approval, "id") else approval
    if appr is None or appr.status != "ACTIVE":
        return False, "E-G4-04-003: 批准非 ACTIVE（未批准 / 已失效）"
    if appr.token != APPROVE_TOKEN:
        return False, "E-G4-04-002: 批准 token 非 APPROVE"
    if appr.inputs_hash != inputs_hash(manifest, candidate_digest):
        return False, "E-G4-04-004: 输入变化批准失效（inputs_hash 不符）"
    if audit is not None and not audit.release_eligible:
        return False, "E-G4-04-005: 审计门未全 PASS —— 准出谓词为假"
    return True, ""


# ════════════════════════════════════════════════════════════════
# G4-03 发布：CurrentKey 提交协议 + DB 事务 + 父版本 CAS
# ════════════════════════════════════════════════════════════════

def _version_str(seq: int) -> str:
    return f"1.{seq}.0"


def publish_release(store: ArtifactStore, session, manifest: dict,
                    key: CurrentKey, approval, candidate_digest: Optional[str] = None,
                    changed_by: str = "L11_release",
                    released_at: Optional[str] = None,
                    writer: str = "L11_release") -> "ReleaseRow":
    """G4-03：先写内容寻址工件并验哈希，再以 DB 事务更新 release/pointer。

    规则：
      · 首次研究发布要求 parent=null 且该 CurrentKey 不存在（E-G4-03-005）
      · 同 subject root 幂等（返回既有 release，不改指针）
      · 不同 root 冲突硬失败（E-G4-03-006）
      · 陈旧父拒绝（parent 必须 = 当前 release 的 manifest 哈希）（E-G4-03-007）
      · 跨 workflow/scope/key 拒绝（E-G4-03-008）
      · 并发（同域同 seq 二次提交）唯一约束拒绝（E-G4-03-009）
      · 工件失败（闭包对象缺失/篡改）拒绝（E-G4-03-004）
      · 孤儿永不成为 current（D-5）
    """
    from sqlalchemy.exc import IntegrityError
    from repository import Approval, CurrentPointer, Release

    # 唯一准出谓词的一部分：批准须 ACTIVE 且绑定本清单输入（E-G4-04-003/004）
    appr = session.get(Approval, approval.id) if hasattr(approval, "id") else approval
    if appr is None or appr.status != "ACTIVE":
        raise ValueError("E-G4-04-003: 批准非 ACTIVE —— 未批准不得发布")
    if appr.inputs_hash != inputs_hash(manifest, candidate_digest):
        raise ValueError("E-G4-04-004: 输入变化批准失效 —— 不得发布")

    # 阶段 1：工件预写并验哈希（内容寻址；任何失败即拒绝，不触碰 DB）
    try:
        closure = compute_closure(store, manifest)
    except ValueError as e:
        raise ValueError(f"E-G4-03-004: 闭包校验失败: {e}")
    if not closure.complete or closure.count == 0:
        raise ValueError(
            f"E-G4-03-004: 工件失败 —— 闭包不完整（count={closure.count} "
            f"dangling={len(closure.dangling)} dead={len(closure.dead)})")
    # D-5：孤儿不得成为 current —— 发布路径只接受闭包内对象（subject root 在闭包内）
    if resolve_subject_root(manifest) not in closure.reachable:
        raise ValueError("E-G4-03-010: 孤儿不得成为 current —— subject root 不在闭包内")
    # 跨 workflow/scope/key：清单 CurrentKey 与发布目标必须一致（不得跨域发布）
    if (manifest.get("workflow") != key.workflow
            or manifest.get("scope_id") != key.scope_id
            or manifest.get("current_key", "") != key.current_key):
        raise ValueError(
            f"E-G4-03-008: 清单 CurrentKey 与发布目标不符（跨域发布）")

    # 阶段 2：DB 事务（单事务提交 release + pointer）
    try:
        session.rollback()
        # 同 subject root 幂等：该域该清单已发布 → 返回既有 release，不动指针
        existing = session.query(Release).filter_by(
            workflow=key.workflow, scope_id=key.scope_id,
            current_key=key.current_key).all()
        same_root = [r for r in existing if r.subject_root_hash == manifest["subject_root"]]
        if same_root and same_root[0].manifest_hash == manifest["id"]:
            return same_root[0]

        pointer = session.query(CurrentPointer).filter_by(
            workflow=key.workflow, scope_id=key.scope_id,
            current_key=key.current_key).order_by(
            CurrentPointer.seq.desc()).first()
        cur_seq = pointer.seq if pointer else 0
        cur_manifest = None
        if pointer is not None:
            cur_rel = session.get(Release, pointer.release_id)
            cur_manifest = cur_rel.manifest_hash if cur_rel else None

        if existing and not same_root:
            # 不同 root 冲突硬失败（不得任选其一）
            raise ValueError(
                f"E-G4-03-006: 不同 subject root 冲突硬失败 —— "
                f"{key} 已有 release {existing[0].version}")
        if cur_seq == 0:
            # 首次发布：parent 必须为 null
            if manifest.get("parent"):
                raise ValueError(
                    f"E-G4-03-005: 首次发布要求 parent=null，实为 "
                    f"{manifest['parent'][:12]}…（{key}）")
        else:
            # 陈旧父拒绝：parent 必须 = 当前 release 的 manifest 哈希
            if manifest.get("parent") != cur_manifest:
                raise ValueError(
                    f"E-G4-03-007: 陈旧父拒绝 —— manifest.parent="
                    f"{str(manifest.get('parent'))[:12]}… ≠ 当前 {str(cur_manifest)[:12]}…")

        rel = Release(
            id=manifest["id"],
            schema_version="1.0.0",
            workflow=key.workflow,
            scope_id=key.scope_id,
            current_key=key.current_key,
            version=_version_str(cur_seq + 1),
            parent_cas=manifest.get("parent"),
            subject_root_hash=manifest["subject_root"],
            manifest_hash=manifest["id"],
            approval_id=approval.id,
            released_at=datetime.fromisoformat(
                (released_at or _now_iso()).replace("Z", "+00:00")),
            version_cas=1,
        )
        session.add(rel)
        # 显式 flush 保证 current_pointer 的 release_id FK 先于指针插入存在
        # （同一事务内：commit 仍在最后，原子性不变；顺序由依赖决定）
        session.flush()
        ptr = CurrentPointer(
            id=f"PTR_{key.workflow[:4].upper()}_{cur_seq + 1}_"
               f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%f')}",
            schema_version="1.0.0",
            workflow=key.workflow,
            scope_id=key.scope_id,
            current_key=key.current_key,
            release_id=rel.id,
            seq=cur_seq + 1,
            changed_by=changed_by,
            changed_at=datetime.fromisoformat(
                (released_at or _now_iso()).replace("Z", "+00:00")),
            approval_id=approval.id,
            version=1,
        )
        session.add(ptr)
        session.commit()
        return rel
    except IntegrityError:
        session.rollback()
        raise ValueError(
            f"E-G4-03-009: 并发冲突 —— {key} 同 seq 二次提交（唯一约束拒绝）")
    except ValueError:
        session.rollback()
        raise


def gc_orphans(store: ArtifactStore, manifests: Sequence[dict]) -> List[str]:
    """孤儿回收：不在任何 manifest 闭包内的对象。回收后仍不得成为 current（D-5）。"""
    reachable: Set[str] = set()
    for m in manifests:
        closure = compute_closure(store, m)
        reachable |= closure.reachable
    orphans: List[str] = []
    for p in store.root.rglob("*"):
        if not p.is_file():
            continue
        rel = p.relative_to(store.root).as_posix()
        if not re.fullmatch(r"[0-9a-f]{2}/[0-9a-f]{2}/[0-9a-f]{60}", rel):
            continue
        digest = rel.replace("/", "")
        if digest not in reachable:
            orphans.append(digest)
    for oid in orphans:
        rel = f"{oid[:2]}/{oid[2:4]}/{oid[4:]}"
        try:
            os.remove(store.root / rel)
        except OSError:
            pass
    return orphans


def current_release(session, key: CurrentKey) -> Optional[dict]:
    """读取某域的 current（分域读，D-4 断言用）。"""
    from repository import CurrentPointer, Release
    ptr = session.query(CurrentPointer).filter_by(
        workflow=key.workflow, scope_id=key.scope_id,
        current_key=key.current_key).order_by(CurrentPointer.seq.desc()).first()
    if ptr is None:
        return None
    rel = session.get(Release, ptr.release_id)
    if rel is None:
        return None
    return {"release_id": rel.id, "version": rel.version,
            "manifest_hash": rel.manifest_hash,
            "subject_root_hash": rel.subject_root_hash,
            "seq": ptr.seq, "changed_by": ptr.changed_by,
            "changed_at": ptr.changed_at.isoformat(),
            "approval_id": ptr.approval_id}


# ════════════════════════════════════════════════════════════════
# G4-05 UpdateDiff：差异 / 受影响结论 / 父子链
# ════════════════════════════════════════════════════════════════

def update_diff(store: ArtifactStore, old_manifest: dict,
                new_manifest: dict) -> dict:
    """新证据生成新版本，不回写历史：只比较、不写回。"""
    old_objs = old_manifest.get("objects", {})
    new_objs = new_manifest.get("objects", {})
    changed = sorted(set(old_objs) ^ set(new_objs))
    changed_content = sorted(oid for oid in set(old_objs) & set(new_objs)
                             if old_objs[oid] != new_objs[oid])
    changed = sorted(set(changed) | set(changed_content))

    affected_claims = sorted(
        oid for oid in new_objs
        if new_objs[oid].get("kind") == "claim"
        and any(ref in changed for ref in new_objs[oid].get("refs", [])))

    chain = [old_manifest.get("id")]
    for p in (new_manifest.get("parent"), old_manifest.get("parent")):
        if p and p not in chain:
            chain.append(p)
    return {
        "changed_objects": changed,
        "affected_conclusions": affected_claims,
        "parent_chain": chain,
        "old_version": old_manifest.get("id"),
        "new_version": new_manifest.get("id"),
    }


# ════════════════════════════════════════════════════════════════
# G4-08 离线复建（D-8 / D-9 / D-10 / D-11）
# ════════════════════════════════════════════════════════════════

VERIFICATION_FULL = "FULL"
VERIFICATION_PROVENANCE_ONLY = "PROVENANCE_ONLY"


@dataclass
class RebuildResult:
    verification_level: str          # FULL / PROVENANCE_ONLY（D-10 显式可分辨）
    missing: List[str]
    rebuilt: List[str]               # 已复建对象 id
    out_dir: str


def rebuild_from_store(store: ArtifactStore, manifest: dict, out_dir: str,
                       probe=None) -> RebuildResult:
    """G4-08：断网、空缓存、从对象库按 manifest 逐字节复建。

    对象完整 → 复建同一对象根（FULL）；缺对象 → 只能 PROVENANCE_ONLY，
    不得冒充完整复验（D-10）。本函数不 import repository —— 可在
    干净环境（python -S -I / 新 venv）执行（D-9）。

    probe：断网断言回调（如 network_probe.assert_network_unreachable）。
    内核不持 socket —— **必须由调用方显式注入探针**，缺省拒绝执行
    （fail-closed：不接受「理论上可以」）。
    """
    if probe is None:
        raise ValueError(
            "E-G4-08-003: 须显式提供断网探针（network_probe）—— "
            "离线复建不接受无断网断言")
    probe()                              # 先证真断网（D-8）
    os.makedirs(out_dir, exist_ok=True)
    missing: List[str] = []
    rebuilt: List[str] = []
    for oid in sorted(manifest.get("objects", {})):
        try:
            data = store.load(oid)       # 读时哈希校验 = 篡改必拒
        except ValueError:
            missing.append(oid)
            continue
        with open(os.path.join(out_dir, oid), "wb") as f:
            f.write(data)
        rebuilt.append(oid)
    level = VERIFICATION_FULL if not missing else VERIFICATION_PROVENANCE_ONLY
    return RebuildResult(verification_level=level, missing=missing,
                         rebuilt=rebuilt, out_dir=out_dir)


def consume_rebuild(result: RebuildResult, require: str = VERIFICATION_FULL) -> None:
    """D-11：把 PROVENANCE_ONLY 当完整复验使用必须 FAIL（一票否决）。

    下游（发布 / 报告渲染 / 复验声明）一律经本函数消费复建产物：
    等级不符即拒绝；声称 FULL 但存在缺失对象（冒充）同样拒绝 ——
    只查字段存在不算（行为验证）。
    """
    if result.verification_level != require:
        raise ValueError(
            f"E-G4-08-002: 复验等级 {result.verification_level} ≠ 要求的 {require}"
            f" —— PROVENANCE_ONLY 不得冒充完整复验")
    if require == VERIFICATION_FULL and result.missing:
        raise ValueError(
            f"E-G4-08-002: 冒充完整复验 —— 声称 FULL 但仍有缺失对象 "
            f"（{result.missing[:3]}）")
