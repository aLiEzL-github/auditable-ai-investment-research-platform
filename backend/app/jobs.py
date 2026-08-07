"""jobs.py —— Job Lease / 幂等 / 取消 / 恢复（G1-04）。

B 基线 G1-04 验收：
  · SQLite Worker 并发为 1（BEGIN IMMEDIATE 串行化 + busy_timeout）
  · API/Worker 写锁冲突可恢复（busy_timeout 等待；CAS 版本）
  · 重复提交不重复执行（job_key 幂等键唯一）

状态机：PENDING → RUNNING（lease 领取）→ DONE / FAILED / CANCELLED
租约：RUNNING 且 lease_until 过期 → 可被其他 worker 重新领取（崩溃恢复）。

写权：本层为调度原语，调用方须自行按 contracts/writers.json 断言
（assert_writer 的接入点，J4 前向要求）。
"""

from datetime import datetime, timedelta, timezone

from sqlalchemy import Column, DateTime, Integer, String, Text, UniqueConstraint, text
from sqlalchemy.exc import IntegrityError

from repository import Base, Repository

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
    created_at = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))


def _now():
    # SQLAlchemy DateTime 列存 naive（SQLite 丢弃 tzinfo）—— 统一 naive UTC 避免比较 TypeError
    return datetime.utcnow()


class JobQueue:
    def __init__(self, repo: Repository):
        self.repo = repo
        self.s = repo.session()

    def submit(self, job_key: str, payload: str = None) -> Job:
        """幂等提交：job_key 存在（含终态）→ 返回既有 job，**不重复执行**。
        幂等键全表唯一；需重跑须用新 job_key（调用方自行设计键语义）。"""
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

        原子 UPDATE…RETURNING 领取：SQLite 写锁天然串行，同一时刻仅一个
        worker 能成功（Worker 并发为 1）。不用裸 BEGIN IMMEDIATE ——
        它与 SQLAlchemy 2.0 的自动事务管理冲突（实测死锁）。
        """
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
        return self.s.query(Job).get(row.id)

    def extend_lease(self, job_id: int, lease_seconds: int = 60) -> bool:
        job = self.s.query(Job).get(job_id)
        if job is None or job.status != "RUNNING":
            return False
        job.lease_until = _now() + timedelta(seconds=lease_seconds)
        self.s.commit()
        return True

    def complete(self, job_id: int, result: str) -> Job:
        job = self.s.query(Job).get(job_id)
        if job is None or job.status != "RUNNING":
            raise ValueError("E-STATE-001: 仅 RUNNING 可完成")
        job.status = "DONE"
        job.result = result
        self.s.commit()
        return job

    def fail(self, job_id: int, error: str) -> Job:
        job = self.s.query(Job).get(job_id)
        if job is None or job.status not in ("RUNNING", "PENDING"):
            raise ValueError("E-STATE-001: 非法状态转换")
        job.status = "FAILED"
        job.error = error
        self.s.commit()
        return job

    def cancel(self, job_id: int) -> Job:
        """取消：仅 PENDING/RUNNING 可取消；RUNNING 的租约同步释放。"""
        job = self.s.query(Job).get(job_id)
        if job is None or job.status in TERMINAL:
            raise ValueError("E-STATE-001: 终态不可取消")
        job.status = "CANCELLED"
        job.lease_until = None
        self.s.commit()
        return job

    def get(self, job_id: int) -> Job | None:
        return self.s.query(Job).get(job_id)

    def get_by_key(self, job_key: str) -> Job | None:
        return self.s.query(Job).filter_by(job_key=job_key).first()
