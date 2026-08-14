"""jobs.py —— Job Lease / 幂等 / 取消 / 恢复（G1-04）。

B 基线 G1-04 验收：
  · SQLite Worker 并发为 1（锁串行化 + BEGIN IMMEDIATE 语义的原子 UPDATE）
  · API/Worker 写锁冲突可恢复（busy_timeout 等待；唯一键冲突回滚重读）
  · 重复提交不重复执行（job_key 幂等键唯一）

状态机：PENDING → RUNNING（lease 领取）→ DONE / FAILED / CANCELLED
租约：RUNNING 且 lease_until 过期 → 可被其他 worker 重新领取（崩溃恢复）。

SQLAlchemy Session 非线程安全 —— JobQueue 以锁串行化全部操作，
与「SQLite Worker 并发为 1」语义一致：API/Worker 并发提交由
锁 + job_key 唯一约束双重保证（2026-08-07 CI 实测修复）。

写权：**本层自行断言**（OI-PF-180）。原注释写「本层为调度原语，调用方须
自行按 contracts/writers.json 断言」，而 backend/app 内**没有任何调用方
做过该断言** —— 责任下推给了一个不存在的接收方。现由 submit() 直接断言。
（assert_writer 的接入点，J4 前向要求）。
"""

import threading
from datetime import datetime, timedelta

from sqlalchemy import Column, DateTime, Integer, String, Text, UniqueConstraint, text
from sqlalchemy.exc import IntegrityError

from repository import Base, Repository
from schema_validate import assert_writer

JOB_STATUSES = ("PENDING", "RUNNING", "DONE", "FAILED", "CANCELLED")
TERMINAL = ("DONE", "FAILED", "CANCELLED")


class Job(Base):
    __tablename__ = "job"
    __table_args__ = (UniqueConstraint("job_key", name="uq_job_key"),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    job_key = Column(String(255), nullable=False)
    payload = Column(Text)
    status = Column(String(16), nullable=False, default="PENDING")
    worker_id = Column(String(64))
    lease_until = Column(DateTime)
    attempts = Column(Integer, nullable=False, default=0)
    result = Column(Text)
    error = Column(Text)
    created_at = Column(DateTime, nullable=False, default=lambda: datetime.utcnow())
    updated_at = Column(DateTime, nullable=False,
                        default=lambda: datetime.utcnow(),
                        onupdate=lambda: datetime.utcnow())  # J-2/Q2


def _now():
    # SQLAlchemy DateTime 列存 naive（SQLite 丢弃 tzinfo）—— 统一 naive UTC
    return datetime.utcnow()


class JobQueue:
    def __init__(self, repo: Repository):
        self.repo = repo
        self.s = repo.session()
        self._lock = threading.Lock()

    def submit(self, job_key: str, payload: str = None, *, writer: str) -> Job:
        """幂等提交：job_key 存在（含终态）→ 返回既有 job，**不重复执行**。
        幂等键全表唯一；需重跑须用新 job_key。

        `writer` **必填且为关键字参数**（OI-PF-180）：本方法是 job 表的唯一
        写入点，而 contracts/writers.json 的 job 行写者集合为 ['L7_freeze']、
        never 含 LLM。此前本方法不收写者，那一行的契约在执行侧无落点。

        缺省值一律不给 —— OI-PF-184 记的正是「缺省值恰为白名单的唯一合法值时，
        断言只能挡住主动自称非法写者的调用方」。
        """
        assert_writer("job", writer, {"job_key": job_key})
        with self._lock:
            existing = self.s.query(Job).filter_by(job_key=job_key).first()
            if existing is not None:
                return existing
            job = Job(job_key=job_key, payload=payload, status="PENDING")
            self.s.add(job)
            try:
                self.s.commit()
            except IntegrityError:
                self.s.rollback()  # 并发提交撞唯一键：读既有
                return self.s.query(Job).filter_by(job_key=job_key).one()
            return job

    def claim_next(self, worker_id: str, lease_seconds: int = 60) -> Job | None:
        """领取一个可执行任务（PENDING 或租约过期的 RUNNING——崩溃恢复）。
        原子 UPDATE…RETURNING：SQLite 写锁天然串行，同一时刻仅一个 worker 能成功。"""
        with self._lock:
            now = _now()
            lease = now + timedelta(seconds=lease_seconds)
            row = self.s.execute(text(
                "UPDATE job SET status='RUNNING', worker_id=:w, lease_until=:l, "
                "attempts=attempts+1 "
                "WHERE id = (SELECT id FROM job WHERE status='PENDING' "
                "ORDER BY created_at LIMIT 1) RETURNING id"
            ), {"w": worker_id, "l": lease}).fetchone()
            if row is None:
                row = self.s.execute(text(
                    "UPDATE job SET status='RUNNING', worker_id=:w, lease_until=:l, "
                    "attempts=attempts+1 "
                    "WHERE id = (SELECT id FROM job WHERE status='RUNNING' "
                    "AND lease_until < :now ORDER BY created_at LIMIT 1) RETURNING id"
                ), {"w": worker_id, "l": lease, "now": now}).fetchone()
            self.s.commit()
            if row is None:
                return None
            self.s.expire_all()  # 清除 identity map 缓存，确保读取 UPDATE 后的状态
            return self.s.get(Job, row.id)

    @staticmethod
    def _assert_holder(job, worker_id: str, now) -> None:
        """J-1/Q1：持有者校验 —— 非持有者 E-LEASE-002；持有者但租约失效 E-LEASE-001。
        租约机制的全部意义：过期之后原持有者不得再提交。"""
        if job.worker_id != worker_id:
            raise ValueError("E-LEASE-002: 非持有者（worker_id 不符）")
        if job.lease_until is None or job.lease_until <= now:
            raise ValueError("E-LEASE-001: 租约已失效")

    def extend_lease(self, job_id: int, worker_id: str, lease_seconds: int = 60) -> bool:
        """延长租约 —— 必须由当前持有者调用（J-1/Q1 增加 worker_id 参数）。"""
        with self._lock:
            job = self.s.get(Job, job_id)
            if job is None or job.status != "RUNNING":
                return False
            self._assert_holder(job, worker_id, _now())
            job.lease_until = _now() + timedelta(seconds=lease_seconds)
            self.s.commit()
            return True

    def complete(self, job_id: int, worker_id: str, result: str) -> Job:
        with self._lock:
            job = self.s.get(Job, job_id)
            if job is None or job.status != "RUNNING":
                raise ValueError("E-STATE-001: 仅 RUNNING 可完成")
            self._assert_holder(job, worker_id, _now())
            job.status = "DONE"
            job.result = result
            self.s.commit()
            return job

    def fail(self, job_id: int, worker_id: str, error: str) -> Job:
        with self._lock:
            job = self.s.get(Job, job_id)
            if job is None or job.status not in ("RUNNING", "PENDING"):
                raise ValueError("E-STATE-001: 非法状态转换")
            if job.status == "RUNNING":
                self._assert_holder(job, worker_id, _now())
            job.status = "FAILED"
            job.error = error
            self.s.commit()
            return job

    def cancel(self, job_id: int) -> Job:
        """取消：仅 PENDING/RUNNING 可取消；RUNNING 的租约同步释放。"""
        with self._lock:
            job = self.s.get(Job, job_id)
            if job is None or job.status in TERMINAL:
                raise ValueError("E-STATE-001: 终态不可取消")
            job.status = "CANCELLED"
            job.lease_until = None
            self.s.commit()
            return job

    def get(self, job_id: int) -> Job | None:
        with self._lock:
            return self.s.get(Job, job_id)

    def get_by_key(self, job_key: str) -> Job | None:
        with self._lock:
            return self.s.query(Job).filter_by(job_key=job_key).first()
