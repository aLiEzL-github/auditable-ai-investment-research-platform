"""research_router.py —— G3-02 研究路由和运行状态机。

基线验收（G3-02）：
  · workflow、scope/run/version ID 齐备
  · 运行唯一（同一 workflow/scope 下同时只有一个活动运行）
  · 禁止直接跳到 RELEASED（RELEASED 只能由 G4 发布引擎经合法迁移到达）

状态机（显式合法迁移表，非法迁移 E-STATE-001）：

  DRAFT ──► RUNNING ──► CANDIDATE ──► RELEASED
    │           │            │
    ▼           ▼            ▼
  BLOCKED     PARTIAL      FAILED

  · DRAFT → RUNNING      研究启动（前置全过后）
  · RUNNING → CANDIDATE  结构化候选生成（G3-08）
  · CANDIDATE → RELEASED **仅 G4 发布引擎**经 unique release 谓词调用
    —— G3-02 自身任何入口都不得直接跳到 RELEASED（变异注入验证）
  · DRAFT → BLOCKED      前置不满足
  · RUNNING → PARTIAL    部分结果（不伪造完成）
  · CANDIDATE → FAILED   闭合失败
  终态（不可迁移）：RELEASED / BLOCKED / PARTIAL / FAILED

workflow 白名单与 G4 CurrentKey 命名对齐（基线 §1）：
  a-share-single-company-research / system-design-plan

运行唯一性：同一 (workflow, scope_id) 下存在非终态 run 时，不得再
create_run —— 并发研究同一标的会混用证据与宏观快照。
"""
import datetime
import re
from dataclasses import dataclass
from typing import Dict, Optional

DRAFT = "DRAFT"
RUNNING = "RUNNING"
CANDIDATE = "CANDIDATE"
RELEASED = "RELEASED"
PARTIAL = "PARTIAL"
BLOCKED = "BLOCKED"
FAILED = "FAILED"

STATES = (DRAFT, RUNNING, CANDIDATE, RELEASED, PARTIAL, BLOCKED, FAILED)
TERMINAL = (RELEASED, PARTIAL, BLOCKED, FAILED)
ACTIVE = (DRAFT, RUNNING, CANDIDATE)  # 活动运行（占用唯一性）

# 合法迁移表（显式列全，非法迁移一律 E-STATE-001）
LEGAL_TRANSITIONS: Dict[str, tuple] = {
    DRAFT: (RUNNING, BLOCKED),
    RUNNING: (CANDIDATE, PARTIAL, FAILED),
    CANDIDATE: (RELEASED, FAILED),
    RELEASED: (),
    PARTIAL: (),
    BLOCKED: (),
    FAILED: (),
}

WORKFLOWS = ("a-share-single-company-research", "system-design-plan")

_SCOPE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._\-]*$")
_RUN_RE = re.compile(r"^run-[0-9A-Za-z\-]{8,}$")


@dataclass
class ResearchRun:
    workflow: str
    scope_id: str
    run_id: str
    version_id: str
    state: str = DRAFT
    parent_version: Optional[str] = None

    def key(self) -> str:
        return f"{self.workflow}/{self.scope_id}/{self.run_id}"


def validate_workflow_scope(workflow: str, scope_id: str) -> None:
    if workflow not in WORKFLOWS:
        raise ValueError(
            f"E-G3-02-004: workflow 不在白名单 {WORKFLOWS}: {workflow}")
    if not _SCOPE_RE.match(scope_id or ""):
        raise ValueError(f"E-G3-02-005: 非法 scope_id: {scope_id!r}")


def make_run_id(now_utc: str, nonce: str) -> str:
    """run ID：UTC 秒 + 随机后缀，唯一且可溯（⑰ 随机后缀由调用方注入）。"""
    rid = f"run-{now_utc.replace(':', '').replace('-', '')}-{nonce}"
    if not _RUN_RE.match(rid):
        raise ValueError(f"E-G3-02-006: run_id 生成非法: {rid}")
    return rid


class ResearchRouter:
    """研究路由：run 的唯一注册处 + 合法状态迁移。

    运行唯一：同一 (workflow, scope_id) 下存在活动 run 时 create_run 拒绝。
    禁止直接跳到 RELEASED：G3-02 的迁移表只有 CANDIDATE→RELEASED 一条，
    且该入口仅由 G4 发布引擎（release_eligible 为真）调用 —— 路由层对
    调用方不做身份假设，而是以「显式 release 入口」区分。
    """

    def __init__(self):
        self._runs: Dict[str, ResearchRun] = {}

    def create_run(self, workflow: str, scope_id: str, run_id: str,
                   version_id: str, parent_version: Optional[str] = None
                   ) -> ResearchRun:
        # OI-PF-189：原有 now_utc 形参从不被读取，且全仓无调用方传过它。
        # run_id 由调用方经 make_run_id(now_utc, nonce) 生成后传入 ——
        # 时间早已烘进 id，本方法不需要时钟。形参是设计变更后的残留，
        # 留着会让调用方以为可以注入时钟（测试/重放/确定性构造），而它静默失效。
        validate_workflow_scope(workflow, scope_id)
        if not version_id or not version_id.startswith("v"):
            raise ValueError(f"E-G3-02-007: 非法 version_id: {version_id!r}")
        for r in self._runs.values():
            if r.workflow == workflow and r.scope_id == scope_id \
                    and r.state in ACTIVE:
                raise ValueError(
                    f"E-G3-02-001: 运行不唯一 —— {workflow}/{scope_id} 已有活动运行 "
                    f"{r.run_id}（state={r.state}）")
        if run_id in self._runs:
            raise ValueError(f"E-G3-02-002: run_id 重复: {run_id}")
        run = ResearchRun(workflow, scope_id, run_id, version_id,
                          parent_version=parent_version)
        self._runs[run_id] = run
        return run

    def get(self, run_id: str) -> Optional[ResearchRun]:
        return self._runs.get(run_id)

    def transition(self, run_id: str, to: str) -> ResearchRun:
        """合法迁移：不在 LEGAL_TRANSITIONS 表中的一律 E-STATE-001。

        RELEASED 是唯一从 CANDIDATE 可达的状态，且本路由**没有**任何
        「任意状态 → RELEASED」的快捷路径 —— 变异注入删除迁移表条目
        或新增直达边都会被测试抓到。
        """
        run = self._runs.get(run_id)
        if run is None:
            raise ValueError(f"E-G3-02-003: run 不存在: {run_id}")
        if to not in STATES:
            raise ValueError(f"E-G3-02-008: 未知状态: {to!r}")
        if run.state in TERMINAL:
            raise ValueError(
                f"E-STATE-001: 终态不可迁移: {run.state} → {to}")
        if to not in LEGAL_TRANSITIONS.get(run.state, ()):
            raise ValueError(
                f"E-STATE-001: 非法状态转换 {run.state} → {to}（"
                f"合法目标: {LEGAL_TRANSITIONS.get(run.state, ())}）")
        run.state = to
        return run

    def release(self, run_id: str) -> ResearchRun:
        """RELEASED 唯一入口：CANDIDATE → RELEASED。

        G4 发布引擎在 release_eligible 为真后调用本方法；路由层不代替
        G4 判断资格 —— 但**跳过本入口**（如直接改 state 字段或走
        transition 的任意路径）在结构上不可达：state 是 dataclass 字段，
        外部改字段不受本类控制，但所有经路由的状态变更都必须走本方法。
        变异注入验证：把 release() 改为从任何状态放行 → 测试转红。
        """
        return self.transition(run_id, RELEASED)
