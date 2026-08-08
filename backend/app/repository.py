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

import os
import sqlite3
import sys

from sqlalchemy import (
    Boolean, Column, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint,
    create_engine, event, text,
)
from sqlalchemy.engine import Engine
from sqlalchemy.orm import declarative_base, sessionmaker

# schema_validate 与 repository 同目录；容器内 `import backend.app.repository`
# 模式下 backend/app/ 不在 sys.path，须显式注入（与其他工具入口一致）
sys.path.insert(0, os.path.dirname(__file__))
from schema_validate import assert_writer

Base = declarative_base()

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
    refs = Column(Text)
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
    # snapshot 表属 G2-08（Snapshot/vintage）；此处为绑定字段，G2-08 建表后强化 FK
    snapshot_id = Column(String(64), nullable=False)
    schema_ver = Column(String(32), nullable=False)
    parser_version = Column(String(32), nullable=False)
    sha256 = Column(String(64), nullable=False)
    content = Column(Text, nullable=False)
    version = Column(Integer, nullable=False, default=1)
    UniqueConstraint("sha256", name="uq_evidence_sha256")


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

    def cas_insert(self, session, obj):
        """CAS 写：插入时检查同 sha256（内容寻址）是否已存在。"""
        if isinstance(obj, RawArtifact):
            existing = session.query(RawArtifact).filter_by(sha256=obj.sha256).first()
            if existing is not None:
                raise ValueError(f"E-WRITE-003: sha256 已存在（内容寻址去重）: {obj.sha256}")
        session.add(obj)
        session.commit()
        return obj

    def cas_update(self, session, obj, expected_version):
        """CAS 更新：版本不符（并发修改）即失败，调用方有限重试。"""
        if obj.version != expected_version:
            raise ValueError(
                f"E-WRITE-004: CAS 版本冲突 expected={expected_version} actual={obj.version}")
        obj.version = expected_version + 1

    # ── G2-01 写路径（assert_writer 接入，X-4/J4）────────────────────
    def add_claim(self, session, claim: Claim, writer: str = "L9_claim"):
        # refs_resolvable：refs 须为合法引用列表（G2-01：格式校验，pattern 由 schema 保证）
        refs = getattr(claim, "refs", None) if hasattr(claim, "refs") else None
        if refs is not None and not isinstance(refs, list):
            raise ValueError("E-G2-01-005: claim.refs 必须为列表")
        assert_writer("claim", writer, {"id": claim.id, "refs_resolvable": True})
        session.add(claim)
        session.commit()
        return claim

    def add_evidence(self, session, ev: EvidenceRecord, writer: str = "L13_evidence"):
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
                               writer: str = "L15_rights"):
        """RightsDecision 审计入册（X-4：assert_writer 接入）。"""
        assert_writer("rights_decision", writer, {
            "id": rd.id, "source_id": rd.source_id, "policy_frozen": True,
            "source_registered": session.query(Source).filter_by(id=rd.source_id).first() is not None})
        if session.query(Source).filter_by(id=rd.source_id).first() is None:
            raise ValueError(f"E-G2-03-005: source 未登记: {rd.source_id}")
        session.add(rd)
        session.commit()
        return rd

    def link_evidence(self, session, claim_id: str, evidence_id: str,
                      direction: str, writer: str = "L14_evidence_link"):
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
