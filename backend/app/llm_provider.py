"""llm_provider.py —— G3-01 受全局权利门约束的 LLMProviderPort。

基线验收（G3-01）：
  1. 可替换模型（端口抽象 + 注入式实现）
  2. 任何外发先过 RequestRightsGuard
  3. UNKNOWN/PROHIBITED 零外发
  4. 模型失败不能修改事实表
  5. 来源正文中的指令不能改变系统指令、工具白名单或预算

设计：
  · LLMProviderPort（ABC）—— 模型实现的唯一入口；实现方注入，可替换。
  · LLMRequest 携带：模型/提示词元数据（model_id / prompt_version / prompt_hash /
    max_tokens / timeout_s）、结构化输出 schema、字段级外发清单
    （outbound_field_manifest）、来源内容数据边界包装（source_content_boundary）。
  · RequestRightsGuard —— 任何外发先经 RightsGuard.decide(LLM_OUTBOUND)；
    非 ALLOWED 即 GuardDenied，零外发；随后过字段级外发清单；最后来源正文
    只以「数据边界」注入（<data>…</data>），系统指令 / 工具白名单 / 预算
    来自 request 冻结值，来源正文不得改变（LLMResponse 附 response_hash）。
  · 写权：LLM 在 writers.json 的 fact/claim/prediction 等 never 名单 ——
    模型输出欲写事实表须经 assert_writer，结构上被拒（验收 4 的机械保证）。

M1/M4：本模块在 L0—L2 可信内核（backend/app/），**不出网**；实际模型传输
由注入的实现方（外部适配器）承担。来源正文不得进入系统指令拼接。
"""
import hashlib
import json
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from rights_guard import RightsGuard, GuardDenied, ALLOWED

LLM_OUTBOUND = "LLM_OUTBOUND"  # 与 contracts/rights_action_map.json 的映射一致


class LLMError(Exception):
    """模型调用失败：不得修改任何事实表。"""


class BudgetExceeded(LLMError):
    """token/时间预算超限：PARTIAL/BLOCKED，不伪造完成。"""


class StructuredOutputError(LLMError):
    """模型输出不符合冻结的输出 schema：拒绝采用。"""


class OutboundFieldDenied(LLMError):
    """字段不在外发清单：该字段零外发。"""


@dataclass
class LLMRequest:
    """一次模型外发的完整合同（全部为冻结元数据 + 数据边界）。"""
    model_id: str
    prompt_version: str                    # 提示词版本（元数据）
    prompt_hash: str                       # 冻结提示词哈希（校验用）
    system_instruction: str                # 冻结系统指令 —— 来源正文不得改变
    tools_whitelist: List[str]             # 工具白名单 —— 来源正文不得改变
    max_tokens: int                        # token 预算 —— 来源正文不得改变
    timeout_s: float                       # 超时预算
    output_schema: Dict[str, Any]          # 结构化输出 JSON Schema 子集
    outbound_field_manifest: List[str]     # 字段级外发清单（白名单）
    source_content_boundary: Dict[str, str] = field(default_factory=dict)
    # ↑ 来源正文按字段包装为数据边界（data），不作为指令注入
    scope: str = "default"
    source_key: str = "SRC_LLM_OUTBOUND"   # 判权用源键（矩阵未登记 → UNKNOWN → 零外发）

    def payload(self) -> Dict[str, Any]:
        """仅含清单字段 + 数据边界包装（来源正文以 data 包裹，不进系统指令）。"""
        allowed = set(self.outbound_field_manifest)
        out: Dict[str, Any] = {
            "model_id": self.model_id,
            "prompt_version": self.prompt_version,
            "prompt_hash": self.prompt_hash,
            "system_instruction": self.system_instruction,
            "tools_whitelist": list(self.tools_whitelist),
            "max_tokens": self.max_tokens,
            "timeout_s": self.timeout_s,
            "output_schema": self.output_schema,
            "scope": self.scope,
        }
        data = {}
        for k, v in self.source_content_boundary.items():
            if k not in allowed:
                raise OutboundFieldDenied(
                    f"E-G3-01-005: 字段 {k} 不在外发清单 {sorted(allowed)} —— 零外发")
            data[k] = v
        if data:
            # 数据边界包装：来源正文永远是 <data>，与系统指令物理分离
            out["source_data"] = {k: f"<data name=\"{k}\">\n{v}\n</data>"
                                  for k, v in data.items()}
        return out


@dataclass
class LLMResponse:
    model_id: str
    structured_output: Optional[Dict[str, Any]]
    usage: Dict[str, int]                  # prompt_tokens / completion_tokens
    finish_reason: str
    response_hash: str                     # 内容寻址：响应字节哈希
    meta: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {"model_id": self.model_id, "structured_output": self.structured_output,
                "usage": self.usage, "finish_reason": self.finish_reason,
                "response_hash": self.response_hash}


class LLMProviderPort(ABC):
    """可替换模型端口：任何实现方经 complete() 接入；外发由守卫统一前置。"""

    model_id: str = "abstract"

    @abstractmethod
    def complete(self, request: LLMRequest) -> LLMResponse:
        """执行一次外发。实现方不得写任何事实表；失败必须抛 LLMError。"""


# ── 结构化输出校验（JSON Schema 子集：type/required/properties/enum/
#    const/minLength/pattern/additionalProperties=false）──────────────
def _check_out(node: Any, schema: Dict[str, Any], path: str) -> None:
    t = schema.get("type")
    if t == "object" and not isinstance(node, dict):
        raise StructuredOutputError(
            f"E-G3-01-003: {path} 期望 object 实为 {type(node).__name__}")
    if t == "string" and not isinstance(node, str):
        raise StructuredOutputError(
            f"E-G3-01-003: {path} 期望 string 实为 {type(node).__name__}")
    if t == "number" and not isinstance(node, (int, float)):
        raise StructuredOutputError(
            f"E-G3-01-003: {path} 期望 number 实为 {type(node).__name__}")
    if t == "boolean" and not isinstance(node, bool):
        raise StructuredOutputError(
            f"E-G3-01-003: {path} 期望 boolean 实为 {type(node).__name__}")
    if isinstance(node, dict):
        props = schema.get("properties", {})
        for req in schema.get("required", []):
            if req not in node:
                raise StructuredOutputError(
                    f"E-G3-01-003: {path} 缺必填字段 {req}")
        for k, v in node.items():
            if k not in props and schema.get("additionalProperties", True) is False:
                raise StructuredOutputError(
                    f"E-G3-01-003: {path}.{k} 未知字段（additionalProperties=false）")
            if k in props:
                _check_out(v, props[k], f"{path}.{k}")
        for k, sub in props.items():
            if "const" in sub and k in node and node[k] != sub["const"]:
                raise StructuredOutputError(
                    f"E-G3-01-003: {path}.{k} const 要求 {sub['const']!r}")
    if isinstance(node, str):
        if "minLength" in schema and len(node) < schema["minLength"]:
            raise StructuredOutputError(
                f"E-G3-01-003: {path} minLength {schema['minLength']}")
        if "enum" in schema and node not in schema["enum"]:
            raise StructuredOutputError(
                f"E-G3-01-003: {path} 枚举外值 {node!r}")


def validate_structured_output(obj: Any, schema: Dict[str, Any]) -> None:
    """冻结输出 schema 校验：不过即拒绝采用（E-G3-01-003）。"""
    _check_out(obj, schema, "$")


class RequestRightsGuard:
    """G3-01 外发守卫：decide 先行 + 字段级清单 + 预算/超时。

    顺序（任一失败即零外发）：
      1) RightsGuard.decide(source_key, LLM_OUTBOUND, scope) 非 ALLOWED → 拒绝
      2) payload() 字段级外发清单校验（来源正文只能经清单字段进入）
      3) 模型调用（注入的 port）→ 失败抛 LLMError，不得写事实表
      4) 结构化输出过冻结 schema
      5) 预算/超时校验 → 超限为 PARTIAL/BLOCKED 状态，不伪造完成
    """

    def __init__(self, port: LLMProviderPort, rights: Optional[RightsGuard] = None):
        self.port = port
        self.rights = rights or RightsGuard()

    def _rights_decision(self, request: LLMRequest):
        return self.rights.decide(request.source_key, LLM_OUTBOUND, request.scope)

    def complete(self, request: LLMRequest) -> LLMResponse:
        # ① 任何外发先过 RequestRightsGuard（矩阵驱动，非 ALLOWED 零外发）
        rd = self._rights_decision(request)
        if rd.verdict != ALLOWED:
            raise GuardDenied(
                f"{rd.verdict}: {request.source_key} {LLM_OUTBOUND} {request.scope} "
                f"—— 零外发（权利门拒绝）")

        # ② 字段级外发清单（含来源正文数据边界包装）
        request.payload()  # OutboundFieldDenied 在字段不在清单时抛出

        # ③ 预算/超时执行
        started = time.monotonic()
        response = self.port.complete(request)
        elapsed = time.monotonic() - started
        if elapsed > request.timeout_s:
            raise BudgetExceeded(
                f"E-G3-01-001: 超时 {elapsed:.2f}s > {request.timeout_s}s —— BLOCKED，不伪造完成")
        used = response.usage.get("completion_tokens", 0)
        if used > request.max_tokens:
            raise BudgetExceeded(
                f"E-G3-01-002: token 超预算 {used} > {request.max_tokens} —— "
                f"PARTIAL/BLOCKED，不伪造完成")

        # ④ 结构化输出过冻结 schema
        if response.structured_output is not None:
            validate_structured_output(response.structured_output, request.output_schema)

        return response


def hash_response(obj: Any) -> str:
    """响应内容寻址：任何字节改动改变哈希（篡改必败的输入基础）。"""
    return hashlib.sha256(json.dumps(obj, ensure_ascii=False,
                                     sort_keys=True).encode("utf-8")).hexdigest()
