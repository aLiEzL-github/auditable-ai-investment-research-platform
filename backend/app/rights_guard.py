"""rights_guard.py —— G2-03 全局 RequestRightsGuard 与人工文件/URL 导入。

基线验收（G2-03）：
  1. 每次动作先产出绑定 source/action/scope/policy_version 的 RightsDecision
  2. PROHIBITED / UNKNOWN 均零来源请求、零正文、零缓存、零解析产物、零外发
  3. 受限上传可审计且无路径穿越 / SSRF
  4. 直接调用适配器也不能绕门（X-9）

设计：
  · RightsGuard.decide() —— 先于任何副作用产出 RightsDecision（审计入册）
  · guarded() —— 动作包装：拒绝即不执行动作体（五个零由「不执行」保证）
  · fetch 适配器必须经 guard 派生（X-9：直调适配器 = 无 RightsDecision = 拒绝）
"""
import datetime
import re
from dataclasses import dataclass
from typing import Callable, Optional

ALLOWED = "ALLOWED"
PROHIBITED = "PROHIBITED"
UNKNOWN = "UNKNOWN"
ACTIONS = ("FETCH", "IMPORT", "PARSE", "EXPORT")

@dataclass
class RightsDecision:
    source_id: str
    action: str
    scope: str
    policy_version: str
    verdict: str
    decided_at: str
    reason: str = ""
    id: str = ""

    def to_dict(self) -> dict:
        return {
            "schema_version": "1.0",
            "id": self.id or f"RD_{self.source_id}_{abs(hash(self.scope))}",
            "source_id": self.source_id,
            "action": self.action,
            "scope": self.scope,
            "policy_version": self.policy_version,
            "verdict": self.verdict,
            "decided_at": self.decided_at,
        }


class GuardDenied(Exception):
    """权利门拒绝：动作体不得执行。"""


class RightsGuard:
    """权利门：source 状态（ALLOWED/UNKNOWN/PROHIBITED）+ scope 允许清单。"""

    def __init__(self, policy_version: str = "v1",
                 allow_scope_patterns: Optional[dict] = None):
        self.policy_version = policy_version
        # 每 source 的 scope 允许正则（未列 = 全部放行仅当 source ALLOWED）
        self.allow_scope_patterns = allow_scope_patterns or {}

    # ── 1. 先于任何副作用产出 RightsDecision ────────────────────────
    def decide(self, source_status: str, source_id: str,
               action: str, scope: str) -> RightsDecision:
        if action not in ACTIONS:
            raise ValueError(f"E-G2-03-001: 非法 action: {action}")
        now = datetime.datetime.now(datetime.timezone.utc).isoformat()
        if source_status == "ALLOWED":
            pat = self.allow_scope_patterns.get(source_id)
            if pat is not None and not re.search(pat, scope):
                return RightsDecision(source_id, action, scope, self.policy_version,
                                      PROHIBITED, now, reason=f"scope 不在允许清单: {scope}")
            return RightsDecision(source_id, action, scope, self.policy_version,
                                  ALLOWED, now)
        if source_status == "PROHIBITED":
            return RightsDecision(source_id, action, scope, self.policy_version,
                                  PROHIBITED, now, reason="source 状态 PROHIBITED")
        return RightsDecision(source_id, action, scope, self.policy_version,
                              UNKNOWN, now, reason="source 状态 UNKNOWN（未获权利决定）")

    # ── 2. 动作包装：拒绝即不执行（五个零）──────────────────────────
    def guarded(self, source_status: str, source_id: str, action: str,
                scope: str, fn: Callable, record: Optional[Callable] = None):
        """包装动作：decide 先行；PROHIBITED/UNKNOWN 抛 GuardDenied 且不调用 fn。"""
        rd = self.decide(source_status, source_id, action, scope)
        if record is not None:
            record(rd)
        if rd.verdict != ALLOWED:
            raise GuardDenied(
                f"{rd.verdict}: {source_id} {action} {scope} —— 零请求/正文/缓存/解析/外发")
        return fn()

    # ── 3. 人工文件/URL 导入的安全边界（路径穿越 + SSRF）────────────
    def validate_import_path(self, path: str, allowed_root: str) -> str:
        """文件导入：解析后必须留在 allowed_root 内（复用内容寻址防逃逸思路）。"""
        import os
        root = os.path.realpath(allowed_root)
        target = os.path.realpath(os.path.join(root, path))
        if not target.startswith(root + os.sep) and target != root:
            raise ValueError("E-G2-03-002: 导入路径穿越边界")
        if not os.path.isfile(target):
            raise ValueError(f"E-G2-03-003: 导入文件不存在: {path}")
        return target

    # URL 导入的 SSRF 校验在工具层（backend/tools/import_guard.py）——
    # M1/M4 禁止可信内核（backend/app/）引入网络库（G0-04 §1.1）；
    # SSRF 校验属出网适配器层（VD-11 §6 Discovery 允许清单）。
