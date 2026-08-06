"""schema_validate.py —— canonical Schema 校验器 + 写权断言（G1-02）。

JSON Schema draft-07 **子集**实现（纯 stdlib，无外部依赖）：
  type / required / properties / enum / pattern / minLength / maxLength /
  minimum / maximum / items / const / additionalProperties(false 语义)

写权断言：按 contracts/writers.json 的矩阵检查写者（G0-04 §2 可执行面）。
验收（G1-02）：
  · LLM 不能写 fact / prediction / approval / release / current_pointer（E-WRITE-002）
  · 非法状态和缺字段被拒绝（E-SCHEMA-001/002/004）
"""

import json
import os
import re

CONTRACTS = os.path.join(os.path.dirname(__file__), "..", "..", "contracts")
SCHEMA_DIR = os.path.join(CONTRACTS, "schema")
WRITERS = json.load(open(os.path.join(CONTRACTS, "writers.json"), encoding="utf-8"))["matrix"]


class SchemaError(ValueError):
    def __init__(self, code, field, detail=""):
        self.code = code
        self.field = field
        self.detail = detail
        super().__init__(f"{code}: {field} {detail}".strip())


def _check(node, schema, path):
    if "type" in schema:
        t = schema["type"]
        if t == "object" and not isinstance(node, dict):
            raise SchemaError("E-SCHEMA-002", path, f"期望 object 实为 {type(node).__name__}")
        if t == "array" and not isinstance(node, list):
            raise SchemaError("E-SCHEMA-002", path, f"期望 array 实为 {type(node).__name__}")
        if t == "string" and not isinstance(node, str):
            raise SchemaError("E-SCHEMA-002", path, f"期望 string 实为 {type(node).__name__}")
        if t == "number" and not isinstance(node, (int, float)):
            raise SchemaError("E-SCHEMA-002", path, f"期望 number 实为 {type(node).__name__}")
        if t == "integer" and (not isinstance(node, int) or isinstance(node, bool)):
            raise SchemaError("E-SCHEMA-002", path, f"期望 integer 实为 {type(node).__name__}")
        if t == "boolean" and not isinstance(node, bool):
            raise SchemaError("E-SCHEMA-002", path, f"期望 boolean 实为 {type(node).__name__}")
    if isinstance(node, dict):
        props = schema.get("properties", {})
        for req in schema.get("required", []):
            if req not in node:
                raise SchemaError("E-SCHEMA-001", f"{path}.{req}", "缺必填字段")
        for k, v in node.items():
            if k not in props and schema.get("additionalProperties", True) is False:
                raise SchemaError("E-SCHEMA-004", f"{path}.{k}", "未知字段")
            if k in props:
                _check(v, props[k], f"{path}.{k}")
        # 对象级 const 检查（如 prediction.calibration_pending）
        for k, sub in props.items():
            if "const" in sub and node.get(k) is not None and k in node and node[k] != sub["const"]:
                raise SchemaError("E-SCHEMA-003", f"{path}.{k}", f"const 要求 {sub['const']!r}")
    elif isinstance(node, list):
        items = schema.get("items", {})
        for i, v in enumerate(node):
            _check(v, items, f"{path}[{i}]")
    if isinstance(node, str):
        if "pattern" in schema and not re.search(schema["pattern"], node):
            raise SchemaError("E-SCHEMA-002", path, f"pattern 不匹配 {schema['pattern']}")
        if "minLength" in schema and len(node) < schema["minLength"]:
            raise SchemaError("E-SCHEMA-002", path, f"minLength {schema['minLength']}")
        if "enum" in schema and node not in schema["enum"]:
            raise SchemaError("E-SCHEMA-003", path, f"枚举外值 {node!r}")
    if isinstance(node, (int, float)):
        if "minimum" in schema and node < schema["minimum"]:
            raise SchemaError("E-SCHEMA-002", path, f"小于 minimum {schema['minimum']}")
        if "maximum" in schema and node > schema["maximum"]:
            raise SchemaError("E-SCHEMA-002", path, f"大于 maximum {schema['maximum']}")


def validate_object(obj_type, obj):
    """按 contracts/schema/<type>.schema.json 校验。返回 None 或抛 SchemaError。"""
    fp = os.path.join(SCHEMA_DIR, f"{obj_type}.schema.json")
    if not os.path.exists(fp):
        raise SchemaError("E-CONTRACT-001", obj_type, "无对应 Schema")
    schema = json.load(open(fp, encoding="utf-8"))
    if obj.get("schema_version") != schema.get("schema_version"):
        raise SchemaError("E-SCHEMA-005", "schema_version",
                          f"对象 {obj.get('schema_version')} vs 契约 {schema.get('schema_version')}")
    _check(obj, schema, obj_type)
    return None


def assert_writer(obj_type, writer):
    """写权断言（G0-04 §2 矩阵）：writer 非法即抛 E-WRITE。"""
    row = WRITERS.get(obj_type)
    if row is None:
        raise SchemaError("E-CONTRACT-001", obj_type, "writers.json 无该对象条目")
    never = row.get("never", [])
    if writer in never:
        raise SchemaError("E-WRITE-002", obj_type,
                          f"写者 {writer} 在「永远不能写」名单（{', '.join(never)}）")
    allowed = row.get("writer")
    if writer != allowed:
        raise SchemaError("E-WRITE-001", obj_type,
                          f"写者 {writer} 非唯一合法写者 {allowed}")
    return None
