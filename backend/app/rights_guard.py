"""rights_guard.py —— G2-03 全局 RequestRightsGuard（FF-2：权利矩阵单一来源）。

FF-2/U-2（OI-PF-127）：decide() 从 rights-matrix.json（工程镜像
contracts/rights_matrix.json）按 source_key 查真实状态 —— **调用方不再
传入 source_status**（结构上杜绝传假状态）；矩阵变更即失效（policy_version
绑定矩阵 produced_at）。

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
import json
import os
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
    """权利门：矩阵驱动（FF-2）—— 状态来自 rights-matrix.json，单一来源。"""

    def __init__(self, matrix: Optional[dict] = None,
                 matrix_path: Optional[str] = None,
                 policy_version: Optional[str] = None,
                 allow_scope_patterns: Optional[dict] = None):
        if matrix is None:
            if matrix_path is None:
                # 默认工程镜像
                _here = os.path.dirname(os.path.abspath(__file__))
                matrix_path = os.path.join(_here, "..", "..", "contracts",
                                           "rights_matrix.json")
            with open(matrix_path, encoding="utf-8") as f:
                matrix = json.load(f)
        self.matrix = matrix
        self.policy_version = policy_version or str(
            matrix.get("produced_at", "matrix"))
        self.allow_scope_patterns = allow_scope_patterns or {}

    # ── 1. 矩阵查询：source_key + action → 状态（归一化）─────────────
    def _status_of(self, source_key: str, action: str) -> str:
        entry = next((d for d in self.matrix.get("data_sources", [])
                      if d.get("source_key") == source_key), None)
        if entry is None:
            return UNKNOWN  # 未登记 → fail-closed
        actions = entry.get("actions", {})
        raw = actions.get(action) or actions.get("__all__")
        if raw is None:
            return UNKNOWN
        txt = str(raw)
        if "PROHIBITED" in txt:
            return PROHIBITED
        if "UNKNOWN" in txt:
            return UNKNOWN
        if "ALLOWED" in txt:
            return ALLOWED
        return UNKNOWN

    # ── 2. 先于任何副作用产出 RightsDecision（无调用方状态参数）─────
    def decide(self, source_key: str, action: str, scope: str) -> RightsDecision:
        if action not in ACTIONS:
            raise ValueError(f"E-G2-03-001: 非法 action: {action}")
        now = datetime.datetime.now(datetime.timezone.utc).isoformat()
        status = self._status_of(source_key, action)
        if status == "ALLOWED":
            pat = self.allow_scope_patterns.get(source_key)
            if pat is not None and not re.search(pat, scope):
                return RightsDecision(source_key, action, scope, self.policy_version,
                                      PROHIBITED, now, reason=f"scope 不在允许清单: {scope}")
            return RightsDecision(source_key, action, scope, self.policy_version,
                                  ALLOWED, now)
        if status == "PROHIBITED":
            return RightsDecision(source_key, action, scope, self.policy_version,
                                  PROHIBITED, now, reason="矩阵判 PROHIBITED")
        return RightsDecision(source_key, action, scope, self.policy_version,
                              UNKNOWN, now, reason="矩阵判 UNKNOWN（未获权利决定）")

    # ── 2. 动作包装：拒绝即不执行（五个零）──────────────────────────
    def guarded(self, source_key: str, action: str,
                scope: str, fn: Callable, record: Optional[Callable] = None):
        """包装动作：decide 先行（矩阵驱动）；PROHIBITED/UNKNOWN 抛 GuardDenied 且不调用 fn。"""
        rd = self.decide(source_key, action, scope)
        if record is not None:
            record(rd)
        if rd.verdict != ALLOWED:
            raise GuardDenied(
                f"{rd.verdict}: {source_key} {action} {scope} —— 零请求/正文/缓存/解析/外发")
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
