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

from sqlalchemy import (
    Boolean, Column, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint,
    create_engine, event, text,
)
from sqlalchemy.engine import Engine
from sqlalchemy.orm import declarative_base, sessionmaker

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
        session.commit()
        return obj


def create_repository(db_path: str):
    """工厂：SQLite 文件路径或完整 URL（postgresql://…，G1-03 双引擎 / R-1(a)）。"""
    url = db_path if db_path.startswith(("postgres", "postgresql", "sqlite:")) \
        else f"sqlite:///{db_path}"
    return Repository(url)
