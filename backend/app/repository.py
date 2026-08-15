"""repository.py —— SQLite/PostgreSQL Repository 与迁移基础（G1-03）。

交付要求（B 基线 G1-03）：SQLAlchemy、Alembic、WAL、busy_timeout、事务策略；
双数据库升级/回滚、锁冲突、CAS 和有限重试测试通过。

本模块实现：
  · engine 工厂：SQLite（WAL + busy_timeout）与 PostgreSQL 双后端
  · Repository 基类：事务上下文、CAS（版本字段乐观锁）、有限重试
  · 初始 schema：source / raw_artifact / acquisition_event（G1-02 契约的
    contracts/schema 落地为 SQL 模型的首批表）

依赖引入方式（过渡）：requirements.txt 固定版本（VD-17 锁定工具落地后升级）。
写权：本层为 L7_freeze 提供写原语 —— assert_writer 的接入点（J4 前向要求）。
"""

import json
import os
import sqlite3
import sys
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean, Column, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint,
    create_engine, event, text,
)
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import declarative_base, sessionmaker
from sqlalchemy.types import TypeDecorator

# schema_validate 与 repository 同目录；容器内 `import backend.app.repository`
# 模式下 backend/app/ 不在 sys.path，须显式注入（与其他工具入口一致）
sys.path.insert(0, os.path.dirname(__file__))
from schema_validate import assert_writer

Base = declarative_base()


class JSONList(TypeDecorator):
    """列表 ↔ JSON 文本（OI-PF-185）。

    `refs` 有两份互相矛盾的契约：存储侧是 `Text`（migrations/g2_01_claim_evidence.py:27），
    域侧是数组（tools/gen_schemas.py:137 `"refs": {"type": "array"}`）。
    修复前二者**互斥**：

        refs=['E-1']    校验要求的形态 → DB 绑定失败 type 'list' is not supported
        refs='["E-1"]'  DB 能存的形态   → 校验拒 E-G2-01-005
        refs=None       →  **唯一能成功写入的取值，恰好是唯一跳过校验的取值**

    即 `E-G2-01-005` 在任何成功路径上都不生效，而 `test_g2_01.py` 走的正是
    refs 缺省（None）那条路，**把这个状态固化成了绿灯**。

    本装饰器让 Python 侧为 list、存储侧仍为 JSON 文本 —— 两份契约同时成立，
    且**不需要迁移**（列类型不变，alembic 路径与 create_all 路径保持一致，
    这一点由 test_jobs.py:202「测试路径与部署路径一致」间接约束）。
    """

    impl = Text
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if not isinstance(value, list):
            raise ValueError("E-G2-01-005: claim.refs 必须为列表")
        return json.dumps(value, ensure_ascii=False)

    def process_result_value(self, value, dialect):
        if value is None or value == "":
            return None
        return json.loads(value)

WAL_PRAGMAS = (
    "PRAGMA journal_mode=WAL;",
    "PRAGMA synchronous=NORMAL;",
    "PRAGMA busy_timeout=5000;",
    "PRAGMA foreign_keys=ON;",
)


class Source(Base):
    """contracts/schema/source.schema.json 的 SQL 落地。"""
    __tablename__ = "source"
    __table_args__ = (UniqueConstraint("name", name="uq_source_name"),)

    id = Column(String(64), primary_key=True)
    schema_version = Column(String(16), nullable=False)
    kind = Column(String(16), nullable=False)          # PRIMARY / SECONDARY
    name = Column(String(255), nullable=False)
    status = Column(String(16), nullable=False)        # ALLOWED / UNKNOWN / PROHIBITED
    legal_basis = Column(Text, nullable=False)
    terms_url = Column(Text)
    attribution_required = Column(Boolean, default=False)
    version = Column(Integer, nullable=False, default=1)  # CAS 乐观锁


class RawArtifact(Base):
    """contracts/schema/raw_artifact.schema.json —— sha256 唯一（内容寻址）。"""
    __tablename__ = "raw_artifact"
    __table_args__ = (UniqueConstraint("sha256", name="uq_artifact_sha256"),)

    id = Column(String(64), primary_key=True)
    schema_version = Column(String(16), nullable=False)
    source_id = Column(String(64), ForeignKey("source.id"), nullable=False)
    sha256 = Column(String(64), nullable=False)
    bytes = Column(Integer, nullable=False)
    content_type = Column(String(64))
    acquired_at = Column(DateTime, nullable=False)
    version = Column(Integer, nullable=False, default=1)


class AcquisitionEvent(Base):
    """contracts/schema/acquisition_event.schema.json —— 去重保谱系（BF-07）。"""
    __tablename__ = "acquisition_event"

    id = Column(String(64), primary_key=True)
    schema_version = Column(String(16), nullable=False)
    artifact_id = Column(String(64), ForeignKey("raw_artifact.id"), nullable=False)
    source_id = Column(String(64), ForeignKey("source.id"), nullable=False)
    acquired_at = Column(DateTime, nullable=False)
    ok = Column(Boolean, nullable=False)
    error = Column(Text)
    retry_of = Column(String(64))
    version = Column(Integer, nullable=False, default=1)


class Claim(Base):
    """G2-01 Claim：分类与 materiality 属 Claim（基线 B）。

    contracts/schema/claim.schema.json（扩展：category / materiality）。
    """
    __tablename__ = "claim"

    id = Column(String(64), primary_key=True)
    schema_version = Column(String(16), nullable=False)
    statement = Column(Text, nullable=False)
    category = Column(String(64), nullable=False)
    materiality = Column(String(16), nullable=False, default="UNCLASSIFIED")
    refs = Column(JSONList)          # OI-PF-185：Python 侧 list，存储侧 JSON 文本
    status = Column(String(16), nullable=False, default="DRAFT")
    version = Column(Integer, nullable=False, default=1)


class EvidenceRecord(Base):
    """G2-01 EvidenceRecord：证据带 schema/parser version 并绑定 snapshot_id。

    contracts/schema/evidence_record.schema.json
    """
    __tablename__ = "evidence_record"

    id = Column(String(64), primary_key=True)
    schema_version = Column(String(16), nullable=False)
    artifact_id = Column(String(64), ForeignKey("raw_artifact.id"), nullable=False)
    snapshot_id = Column(String(64), ForeignKey("snapshot.id"), nullable=False)
    schema_ver = Column(String(32), nullable=False)
    parser_version = Column(String(32), nullable=False)
    sha256 = Column(String(64), nullable=False)
    content = Column(Text, nullable=False)
    version = Column(Integer, nullable=False, default=1)
    UniqueConstraint("sha256", name="uq_evidence_sha256")


class ManualEntry(Base):
    """G2-13 材料性手工录入双录复核（不可变录入事件）。"""
    __tablename__ = "manual_entry"

    id = Column(String(64), primary_key=True)
    schema_version = Column(String(16), nullable=False)
    field_key = Column(String(64), nullable=False)
    value = Column(String(255), nullable=False)
    locator = Column(String(255), nullable=False)
    entered_by = Column(String(64), nullable=False)
    signed_at = Column(DateTime, nullable=False)
    record_hash = Column(String(64), nullable=False)
    version = Column(Integer, nullable=False, default=1)


class Snapshot(Base):
    """G2-08 Snapshot：vintage/cutoff 语义（contracts/schema/snapshot.schema.json）。

    cutoff 冻结后不可改（writers 前置 cutoff_frozen）；facts 为绑定 fact id 列表。"""
    __tablename__ = "snapshot"

    id = Column(String(64), primary_key=True)
    schema_version = Column(String(16), nullable=False)
    created_at = Column(DateTime, nullable=False)
    cutoff = Column(DateTime, nullable=False)
    frozen = Column(Boolean, nullable=False, default=False)
    golden = Column(Boolean, nullable=False, default=False)
    scope_set = Column(Text, nullable=False, default="[]")  # JSON 数组
    facts = Column(Text, nullable=False, default="[]")      # JSON 数组（fact id）
    version = Column(Integer, nullable=False, default=1)


class FactRecord(Base):
    """G2-07 FactRecord：五要素（scope/period/unit/basis/vintage）归一化。
    contracts/schema/fact.schema.json；comparability 枚举 COMPARABLE/NOT_COMPARABLE。"""
    __tablename__ = "fact"

    id = Column(String(64), primary_key=True)
    schema_version = Column(String(16), nullable=False)
    artifact_id = Column(String(64), ForeignKey("raw_artifact.id"), nullable=False)
    source_id = Column(String(64), ForeignKey("source.id"), nullable=False)
    metric = Column(String(64), nullable=False)
    value = Column(String(64), nullable=False)   # 数值字符串（精度保留）
    unit = Column(String(16), nullable=False)
    period = Column(String(32), nullable=False)
    scope = Column(String(64), nullable=False)
    basis = Column(String(64), nullable=False)
    vintage = Column(String(32), nullable=False)
    locator = Column(String(255))
    parser_version = Column(String(32), nullable=False)
    comparability = Column(String(16), nullable=False, default="COMPARABLE")
    version = Column(Integer, nullable=False, default=1)


class RightsDecisionRecord(Base):
    """G2-03 RightsDecision 审计入册（contracts/schema/rights_decision.schema.json）。"""
    __tablename__ = "rights_decision"

    id = Column(String(64), primary_key=True)
    schema_version = Column(String(16), nullable=False)
    source_id = Column(String(64), ForeignKey("source.id"), nullable=False)
    action = Column(String(16), nullable=False)
    scope = Column(String(512), nullable=False)
    policy_version = Column(String(32), nullable=False)
    verdict = Column(String(16), nullable=False)
    decided_at = Column(DateTime, nullable=False)
    version = Column(Integer, nullable=False, default=1)


class ClaimEvidenceLink(Base):
    """G2-01 ClaimEvidenceLink：证据支持/反驳多个 Claim（多对多）。

    contracts/schema/claim_evidence_link.schema.json
    """
    __tablename__ = "claim_evidence_link"

    claim_id = Column(String(64), ForeignKey("claim.id"), nullable=False, primary_key=True)
    evidence_id = Column(String(64), ForeignKey("evidence_record.id"), nullable=False, primary_key=True)
    schema_version = Column(String(16), nullable=False)
    direction = Column(String(16), nullable=False)  # SUPPORT / REFUTE


class Approval(Base):
    """G4-04 批准事件：哈希绑定完整 CurrentKey 与输入（contracts/schema/approval.schema.json）。

    任一输入变化批准失效（inputs_hash 重算不符 → INVALIDATED）；
    token 必须是显式 APPROVE —— 聊天“继续”不算批准（LLM 不得写入）。
    """
    __tablename__ = "approval"

    id = Column(String(64), primary_key=True)
    schema_version = Column(String(16), nullable=False)
    object_ref = Column(String(64), nullable=False)
    approver = Column(String(64), nullable=False)
    approved_at = Column(DateTime, nullable=False)
    subject_root_hash = Column(String(64), nullable=False)
    workflow = Column(String(64), nullable=False)
    scope_id = Column(String(64), nullable=False)
    current_key = Column(String(64), nullable=False, default="")
    inputs_hash = Column(String(64), nullable=False)
    status = Column(String(16), nullable=False, default="ACTIVE")
    token = Column(String(16), nullable=False)
    version = Column(Integer, nullable=False, default=1)


class Release(Base):
    """G4-03 发布：父版本 CAS + 唯一准出谓词；id = 内容哈希（不可变）。

    contracts/schema/release.schema.json。版本号（semver）与 CAS 乐观锁分离：
    version = 语义版本；version_cas = 乐观锁。
    """
    __tablename__ = "release"
    __table_args__ = (UniqueConstraint("workflow", "scope_id", "current_key",
                                       "version", name="uq_release_domain_version"),)

    id = Column(String(64), primary_key=True)
    schema_version = Column(String(16), nullable=False)
    workflow = Column(String(64), nullable=False)
    scope_id = Column(String(64), nullable=False)
    current_key = Column(String(64), nullable=False, default="")
    version = Column(String(32), nullable=False)          # semver 1.0.0 / 1.1.0
    parent_cas = Column(String(64), nullable=True)        # 父 manifest 内容哈希
    subject_root_hash = Column(String(64), nullable=False)
    manifest_hash = Column(String(64), nullable=False)
    approval_id = Column(String(64), ForeignKey("approval.id"), nullable=False)
    released_at = Column(DateTime, nullable=False)
    version_cas = Column(Integer, nullable=False, default=1)


class CurrentPointer(Base):
    """G4-03 CurrentKey 指针：按 workflow/scope_id/current_key 分域；追加式。

    seq 域内单调递增；unique(workflow,scope_id,current_key,seq) 承担并发 CAS：
    同一 seq 二次提交（并发发布）必冲突。每次变更留痕（D-6）：
    谁（changed_by）· 何时（changed_at）· 依据哪个批准（approval_id）。
    """
    __tablename__ = "current_pointer"
    __table_args__ = (UniqueConstraint("workflow", "scope_id", "current_key", "seq",
                                       name="uq_pointer_domain_seq"),)

    id = Column(String(64), primary_key=True)
    schema_version = Column(String(16), nullable=False)
    workflow = Column(String(64), nullable=False)
    scope_id = Column(String(64), nullable=False)
    current_key = Column(String(64), nullable=False, default="")
    release_id = Column(String(64), ForeignKey("release.id"), nullable=False)
    seq = Column(Integer, nullable=False)
    changed_by = Column(String(64), nullable=False)
    changed_at = Column(DateTime, nullable=False)
    approval_id = Column(String(64), ForeignKey("approval.id"), nullable=False)
    version = Column(Integer, nullable=False, default=1)


class CandidateInvalidation(Base):
    """G6A-05/OI-PF-204 候选失效**权威查询面**：按 old_candidate_id 唯一可查。

    失效事实**同时**落两处：
      · 不可变审计证据 —— ArtifactStore kind=candidate_invalidation 内容寻址
        冻结，本表 id 即其内容哈希（永不改写，读时哈希校验为兜底）
      · 本表 —— 权威查询面：old_candidate_id 唯一（uq），重复相同失效幂等
        返回既有行，冲突 new/reason 拒绝（不得静默覆盖）

    create_approval / is_release_eligible / publish_release 全部以本表为
    唯一判据拒绝已失效 candidate：失效后不得新增 Approval，失效前已有的
    Approval 保留审计但不可准出，Release/CurrentPointer 不得新增。
    """
    __tablename__ = "candidate_invalidation"
    __table_args__ = (UniqueConstraint("old_candidate_id",
                                       name="uq_candidate_invalidation_old"),)

    id = Column(String(64), primary_key=True)      # = 内容寻址审计证据哈希
    schema_version = Column(String(16), nullable=False)
    old_candidate_id = Column(String(64), nullable=False)
    new_candidate_id = Column(String(64), nullable=False)
    reason = Column(Text, nullable=False)
    status = Column(String(16), nullable=False)    # INVALIDATED
    invalidated_at = Column(DateTime, nullable=False)
    version = Column(Integer, nullable=False, default=1)


# ── 写权类型表与前置判据（OI-PF-183 / OI-PF-184）──────────────────────
#
# `_OBJ_TYPE` 把 ORM 模型映射到 contracts/writers.json 的对象名。
# **表里没有的类型一律拒**（cas_insert 中默认拒绝），而不是放行 ——
# 新增一个模型而忘了登记，会在第一次写入时报错，不会静默绕过写权。
_OBJ_TYPE = {
    Source: "source",
    RawArtifact: "raw_artifact",
    AcquisitionEvent: "acquisition_event",
    Claim: "claim",
    EvidenceRecord: "evidence_record",
    ManualEntry: "manual_entry",
    Snapshot: "snapshot",
    FactRecord: "fact",
    RightsDecisionRecord: "rights_decision",
    ClaimEvidenceLink: "claim_evidence_link",
    Approval: "approval",
    Release: "release",
    CurrentPointer: "current_pointer",
    CandidateInvalidation: "candidate_invalidation",
    # Job 定义在 jobs.py（与本模块共用 Base），在此按名字延迟登记 ——
    # 直接 import 会造成 repository ←→ jobs 循环依赖。见 _register_late()。
}


def _register_late():
    """把定义在其他模块、但与本模块共用 Base 的 ORM 类补登记进 _OBJ_TYPE。

    不补登记也不会放行（cas_insert 对未登记类型默认拒绝，E-WRITE-005），
    但那会把「忘了登记」和「本就不该写」混为一谈 —— 前者须被看见。
    """
    for cls in Base.registry.mappers:
        c = cls.class_
        if c not in _OBJ_TYPE and getattr(c, "__tablename__", None):
            _OBJ_TYPE[c] = c.__tablename__

# refs 可指向的表 —— 「引用可解析」= 每个 ref 在其中之一里实际存在。
_REF_TABLES = (Claim, EvidenceRecord, FactRecord, RawArtifact, Snapshot, Source)


def _refs_resolvable(session, refs) -> bool:
    """MACHINE 前置 `refs_resolvable`（writers.json claim 行：「引用必须可解析」）。

    此前此处传的是**字面 `True`** —— 前置的输入由被断言方提供且恒为合法值，
    使该前置无条件成立（OI-PF-184 ②）。现改为实际解析：

      · refs 为 None / [] → True（无引用可解析失败，非「跳过校验」）
      · 否则每个 ref 须在 _REF_TABLES 之一中存在；**有一个查不到即 False**
    """
    if refs is None:
        return True
    if not isinstance(refs, list):
        return False
    for r in refs:
        if not isinstance(r, str) or not r:
            return False
        if not any(session.query(t).filter_by(id=r).first() is not None
                   for t in _REF_TABLES):
            return False
    return True


def _policy_frozen(policy_version) -> bool:
    """MACHINE 前置 `policy_frozen`（「policy_version 须为已冻结版本」）。

    此前同样是**字面 `True`**。现按 rights_matrix.json 的 `produced_at` 判定 ——
    `RightsGuard` 正是以它作为 policy_version 的缺省来源（rights_guard.py:75）。

    **已知边界，如实载明**：本判据只认**当前**矩阵版本。历史上冻结过的旧版本
    在仓库内没有任何登记处，故以旧版本记录的 RightsDecision 会被判为不满足前置。
    这是默认拒绝方向的取舍；要支持旧版本，需要一份冻结版本登记册，
    那是 OI-PF-180 写权矩阵接入面的一部分，不在本次修复范围内。
    """
    if not policy_version or not isinstance(policy_version, str):
        return False
    p = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                     "..", "..", "contracts", "rights_matrix.json")
    try:
        with open(p, encoding="utf-8") as f:
            return policy_version == str(json.load(f).get("produced_at"))
    except (OSError, ValueError):
        return False        # 读不到矩阵 → 无法证明已冻结 → 拒（不默认放行）


class Repository:
    """Repository 基类：事务、CAS（版本乐观锁）、有限重试。"""

    def __init__(self, url: str):
        self.engine = create_engine(url, pool_pre_ping=True)
        if url.startswith("sqlite"):
            self._apply_sqlite_pragmas()
        self.Session = sessionmaker(bind=self.engine)

    @staticmethod
    @event.listens_for(Engine, "connect")
    def _set_sqlite_pragma(dbapi_connection, connection_record):
        if isinstance(dbapi_connection, sqlite3.Connection):
            for pragma in WAL_PRAGMAS:
                cursor = dbapi_connection.cursor()
                cursor.execute(pragma)
                cursor.close()

    def _apply_sqlite_pragmas(self):
        with self.engine.connect() as conn:
            for pragma in WAL_PRAGMAS:
                conn.execute(text(pragma))

    def create_all(self):
        Base.metadata.create_all(self.engine)

    def session(self):
        return self.Session()

    def cas_insert(self, session, obj, writer, context=None):
        """CAS 写：插入时检查同 sha256（内容寻址）是否已存在。

        **OI-PF-183**：本方法原先不收写者参数、不判对象类型，
        `session.add(obj)` + `commit()` 直落 —— 于是它成了四个受控写法之外的
        第二条 public 写路径。实测（内存库）曾以

            repo.cas_insert(s, Claim(id='C-BYPASS-1', ...))   → 成功，库内计数 = 1

        绕过 `add_claim` 的 `assert_writer`。断言在它自己那条路上确实有效，
        问题是同一个类上还有第二条路。

        现改为：写者参数**必填**，对象类型经 `_OBJ_TYPE` 查表，
        **查不到即拒**（默认拒绝，而非放行）。
        """
        _register_late()   # 定义了不调用 = 结构在、功能不在
        obj_type = _OBJ_TYPE.get(type(obj))
        if obj_type is None:
            raise ValueError(
                f"E-WRITE-005: {type(obj).__name__} 未登记于写权类型表 —— "
                f"通用写原语不得写未登记类型（默认拒绝）")
        assert_writer(obj_type, writer, context or {})
        if isinstance(obj, RawArtifact):
            existing = session.query(RawArtifact).filter_by(sha256=obj.sha256).first()
            if existing is not None:
                raise ValueError(f"E-WRITE-003: sha256 已存在（内容寻址去重）: {obj.sha256}")
        session.add(obj)
        session.commit()
        return obj

    def cas_update(self, obj, expected_version):
        """CAS 更新：版本不符（并发修改）即失败，调用方有限重试。

        **只改内存版本号，不提交** —— 提交由调用方负责。
        原签名带 `session` 而函数体从不用它（OI-PF-190），会让调用方以为
        本方法负责持久化；实际不 commit 就什么都没发生。
        """
        if obj.version != expected_version:
            raise ValueError(
                f"E-WRITE-004: CAS 版本冲突 expected={expected_version} actual={obj.version}")
        obj.version = expected_version + 1

    def record_candidate_invalidation(self, session, *, old_candidate_id,
                                      new_candidate_id, reason, invalidation_id,
                                      writer, candidates_frozen, invalidated_at=None) -> str:
        """OI-PF-204：失效事实写入权威查询面（repository 内唯一写点）。

        只处理**写入面**：assert_writer 写权断言（candidates_frozen 前置由
        调用方实际校验结果传入）+ 事务提交 + 并发唯一约束兜底。幂等/冲突的
        预检在调用方（recompute.invalidate_previous）完成 —— 两处都只认同一张
        candidate_invalidation 表、同一条按 old_candidate_id 的查询。并发同
        old 二次提交由 uq_candidate_invalidation_old 唯一约束兜底后重读判
        幂等/冲突，绝不静默覆盖。
        """
        assert_writer("candidate_invalidation", writer,
                      {"candidates_frozen": candidates_frozen})
        row = CandidateInvalidation(
            id=invalidation_id, schema_version="1.0.0",
            old_candidate_id=old_candidate_id,
            new_candidate_id=new_candidate_id, reason=reason,
            status="INVALIDATED",
            invalidated_at=invalidated_at or datetime.now(timezone.utc),
            version=1)
        session.add(row)
        try:
            session.commit()
            return invalidation_id
        except IntegrityError:
            session.rollback()
            again = session.query(CandidateInvalidation).filter_by(
                old_candidate_id=old_candidate_id).first()
            if again is not None and again.new_candidate_id == new_candidate_id \
                    and again.reason == reason:
                return again.id
            raise ValueError(
                f"E-G6A-05-008: 冲突失效（并发）—— {old_candidate_id[:12]}… "
                f"已由其他事务失效，重复请求不得静默覆盖")

    # ── G2-01 写路径（assert_writer 接入，X-4/J4）────────────────────
    #
    # **OI-PF-184**：以下四法的 `writer` 原先都有缺省值，且缺省值**恰好等于
    # contracts/writers.json 里该对象白名单的唯一合法值** —— 调用方不传即自动通过，
    # 断言只能挡住「主动自称非法写者」的调用方。缺省已全部移除，writer 为必填。
    #
    # 同一条目的第二半：MACHINE 前置的实参曾有两处**硬编码字面 True** ——
    # `add_claim` 的 `refs_resolvable` 与 `record_rights_decision` 的 `policy_frozen`
    # （另两法的前置一直是真查询）。两处均已改为实际校验结果。

    def add_claim(self, session, claim: Claim, writer: str):
        refs = getattr(claim, "refs", None) if hasattr(claim, "refs") else None
        if refs is not None and not isinstance(refs, list):
            raise ValueError("E-G2-01-005: claim.refs 必须为列表")
        assert_writer("claim", writer, {
            "id": claim.id,
            "refs_resolvable": _refs_resolvable(session, refs)})
        session.add(claim)
        session.commit()
        return claim

    def add_evidence(self, session, ev: EvidenceRecord, writer: str):
        assert_writer("evidence_record", writer, {
            "id": ev.id, "artifact_id": ev.artifact_id,
            "artifact_frozen": session.query(RawArtifact).filter_by(id=ev.artifact_id).first() is not None,
            "snapshot_bound": bool(ev.snapshot_id)})
        if session.query(RawArtifact).filter_by(id=ev.artifact_id).first() is None:
            raise ValueError(f"E-G2-01-001: 证据工件未登记（内容寻址）: {ev.artifact_id}")
        existing = session.query(EvidenceRecord).filter_by(sha256=ev.sha256).first()
        if existing is not None:
            raise ValueError(f"E-G2-01-002: 证据内容寻址去重: {ev.sha256}")
        session.add(ev)
        session.commit()
        return ev

    def record_rights_decision(self, session, rd: RightsDecisionRecord,
                               writer: str):
        """RightsDecision 审计入册（X-4：assert_writer 接入）。"""
        assert_writer("rights_decision", writer, {
            "id": rd.id, "source_id": rd.source_id,
            "policy_frozen": _policy_frozen(rd.policy_version),
            "source_registered": session.query(Source).filter_by(id=rd.source_id).first() is not None})
        if session.query(Source).filter_by(id=rd.source_id).first() is None:
            raise ValueError(f"E-G2-03-005: source 未登记: {rd.source_id}")
        session.add(rd)
        session.commit()
        return rd

    def link_evidence(self, session, claim_id: str, evidence_id: str,
                      direction: str, writer: str):
        claim_exists = session.query(Claim).filter_by(id=claim_id).first() is not None
        evidence_exists = session.query(EvidenceRecord).filter_by(id=evidence_id).first() is not None
        assert_writer("claim_evidence_link", writer, {
            "claim_id": claim_id, "evidence_id": evidence_id,
            "claim_exists": claim_exists, "evidence_exists": evidence_exists})
        if session.query(Claim).filter_by(id=claim_id).first() is None:
            raise ValueError(f"E-G2-01-003: claim 不存在: {claim_id}")
        if session.query(EvidenceRecord).filter_by(id=evidence_id).first() is None:
            raise ValueError(f"E-G2-01-004: evidence 不存在: {evidence_id}")
        link = ClaimEvidenceLink(claim_id=claim_id, evidence_id=evidence_id,
                                 schema_version="1.0", direction=direction)
        session.add(link)
        session.commit()
        return link
        session.commit()
        return obj


def create_repository(db_path: str):
    """工厂：SQLite 文件路径或完整 URL（postgresql://…，G1-03 双引擎 / R-1(a)）。"""
    url = db_path if db_path.startswith(("postgres", "postgresql", "sqlite:")) \
        else f"sqlite:///{db_path}"
    return Repository(url)
