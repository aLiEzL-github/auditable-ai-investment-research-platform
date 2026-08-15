"""schema_validate.py —— canonical Schema 校验器 + 写权断言（G1-02）。

JSON Schema draft-07 **子集**实现（纯 stdlib，无外部依赖）：
  type / required / properties / enum / pattern / minLength / maxLength /
  minimum / maximum / items / minItems / uniqueItems / const /
  additionalProperties(false 语义) / $ref（#/definitions/*） / oneOf

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
with open(os.path.join(CONTRACTS, "writers.json"), encoding="utf-8") as f:
    WRITERS = json.load(f)["matrix"]


class SchemaError(ValueError):
    def __init__(self, code, field, detail=""):
        self.code = code
        self.field = field
        self.detail = detail
        super().__init__(f"{code}: {field} {detail}".strip())


def _check(node, schema, path, defs=None):
    # $ref 解析：#/definitions/<name>（Draft-07 定义片段；本仓库契约已声明
    # draft-07，definitions/oneOf 为合法子集能力 —— G6A-06 PARTIAL 用它们把
    # 状态专属的 required/forbidden 字段条件写进契约，而不只依赖 app 代码）。
    ref = schema.get("$ref")
    if ref:
        if ref.startswith("#/definitions/"):
            name = ref[len("#/definitions/"):]
            resolved = (defs or {}).get(name)
            if resolved is None:
                raise SchemaError("E-SCHEMA-001", path, f"未定义 $ref {ref}")
            return _check(node, resolved, path, defs)
        raise SchemaError("E-SCHEMA-001", path, f"不支持的 $ref {ref}")
    # oneOf：恰一个分支满足（READY/INPUT_MISSING/NOT_EVALUATED 等状态专属
    # 形状由各分支 required/properties/additionalProperties 共同表达 ——
    # 未知状态/矛盾字段组合在此失败关闭，不给 app 代码留下宽放）。
    if "oneOf" in schema:
        matches = []
        for sub in schema["oneOf"]:
            try:
                _check(node, sub, path, defs)
                matches.append(sub)
            except SchemaError:
                continue
        if len(matches) != 1:
            raise SchemaError(
                "E-SCHEMA-006", path,
                f"oneOf 须恰好匹配 1 个分支，实得 {len(matches)}")
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
    # 通用 const / enum 检查（全部 JSON 标量；null 不得绕过 const）。
    # 旧实现只在对象属性上下文用 `node.get(k) is not None` 查 const，null 值即
    # 被静默跳过；此处改为节点级通用检查，任何节点类型与取值（含 null）都必须
    # 逐字满足 const / 落在 enum 内。
    if "const" in schema and node != schema["const"]:
        raise SchemaError("E-SCHEMA-003", path, f"const 要求 {schema['const']!r}")
    if "enum" in schema and node not in schema["enum"]:
        raise SchemaError("E-SCHEMA-003", path, f"枚举外值 {node!r}")
    if isinstance(node, dict):
        props = schema.get("properties", {})
        for req in schema.get("required", []):
            if req not in node:
                raise SchemaError("E-SCHEMA-001", f"{path}.{req}", "缺必填字段")
        for k, v in node.items():
            if k not in props and schema.get("additionalProperties", True) is False:
                raise SchemaError("E-SCHEMA-004", f"{path}.{k}", "未知字段")
            if k in props:
                _check(v, props[k], f"{path}.{k}", defs)
    elif isinstance(node, list):
        if "minItems" in schema and len(node) < schema["minItems"]:
            raise SchemaError("E-SCHEMA-002", path,
                              f"minItems {schema['minItems']}（实得 {len(node)}）")
        if "maxItems" in schema and len(node) > schema["maxItems"]:
            raise SchemaError("E-SCHEMA-002", path,
                              f"maxItems {schema['maxItems']}（实得 {len(node)}）")
        if schema.get("uniqueItems"):
            # uniqueItems 用**规范 JSON 身份**比对（同一内容不同实例亦算重复），
            # 不得对不可哈希元素调 set()（dict/list 会泄漏裸 TypeError）；不可
            # 规范序列化的非 JSON 元素归一为 SchemaError。
            try:
                keys = [json.dumps(v, sort_keys=True) for v in node]
            except (TypeError, ValueError) as exc:
                raise SchemaError(
                    "E-SCHEMA-004", path,
                    f"uniqueItems 无法取得规范身份（非 JSON 元素: {exc!r}）"
                    "—— 失败关闭") from exc
            if len(keys) != len(set(keys)):
                raise SchemaError("E-SCHEMA-004", path, "uniqueItems 含重复项")
        items = schema.get("items", {})
        for i, v in enumerate(node):
            _check(v, items, f"{path}[{i}]", defs)
    if isinstance(node, str):
        if "pattern" in schema and not re.search(schema["pattern"], node):
            raise SchemaError("E-SCHEMA-002", path, f"pattern 不匹配 {schema['pattern']}")
        if "minLength" in schema and len(node) < schema["minLength"]:
            raise SchemaError("E-SCHEMA-002", path, f"minLength {schema['minLength']}")
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
    with open(fp, encoding="utf-8") as f:
        schema = json.load(f)
    if obj.get("schema_version") != schema.get("schema_version"):
        raise SchemaError("E-SCHEMA-005", "schema_version",
                          f"对象 {obj.get('schema_version')} vs 契约 {schema.get('schema_version')}")
    _check(obj, schema, obj_type, schema.get("definitions"))
    return None


def _norm_writer(s) -> str:
    """写者标识的归一化 —— 去空白 + 折叠大小写 + Unicode NFKC（OI-PF-181）。

    NFKC 把全角 ＬＬＭ 折成 LLM；casefold 处理大小写；strip 处理首尾空白与
    制表符；零宽字符单独剔除（NFKC 不处理 U+200B/U+200C/U+200D/U+FEFF）。
    """
    import unicodedata
    t = unicodedata.normalize("NFKC", str(s or ""))
    for z in ("\u200b", "\u200c", "\u200d", "\ufeff"):
        t = t.replace(z, "")
    return "".join(t.split()).casefold()


def assert_writer(obj_type, writer, context=None):
    """写权断言（G0-04 §2 矩阵）：writer 非法或前置不满足即抛错。

    H-2/J2：支持两种可判定语义 ——
      writers: [w1, w2]   精确集合（writer 必须在其中）
      any_of: true        任意受管写者（仅排除 never 名单）
    P0-2/M-1：校验 preconditions ——
      MACHINE 前置须 context 提供对应 key 且为真，否则 E-PRECOND-001；
      MANUAL 前置须 context 提供 acknowledged 确认，否则 E-PRECOND-002；
      NONE / PENDING_RULING 不校验。
    """
    row = WRITERS.get(obj_type)
    if row is None:
        raise SchemaError("E-CONTRACT-001", obj_type, "writers.json 无该对象条目")
    # **两侧同一归一化**（OI-PF-181）。原为字面 `writer in never`，于是
    # llm · Llm · "LLM " · " LLM" · "LLM\t" · 零宽空格 · 全角 ＬＬＭ
    # 七种变体全部绕过 never 名单。有 writers 白名单的行不受影响（变体撞
    # 白名单被拒），但 any_of 的行**没有第二道门** —— 默认拒绝只在有白名单时
    # 成立，any_of 把它翻转成了默认允许。
    #
    # 同一形状本项目已修四次（OI-PF-161 · SERVER_ALLOWLIST 死豁免 ·
    # OI-PF-176 · OI-PF-179），此处是第五次，也是唯一发生在**全局写权契约
    # 执行函数**上的一次。
    w = _norm_writer(writer)
    never = row.get("never", [])
    if w in {_norm_writer(x) for x in never}:
        raise SchemaError("E-WRITE-002", obj_type,
                          f"写者 {writer!r} 在「永远不能写」名单"
                          f"（{', '.join(never)}；归一后比对）")
    allowed = row.get("writers")
    if not (row.get("any_of") or (allowed and w in {_norm_writer(x) for x in allowed})):
        raise SchemaError("E-WRITE-001", obj_type,
                          f"写者 {writer!r} 非合法写者集合 {allowed}")
    if not w:
        raise SchemaError("E-WRITE-001", obj_type,
                          "写者为空 —— 空串/空白一律拒（默认拒绝）")
    ctx = context or {}
    for pre in row.get("preconditions", []):
        kind = pre.get("check")
        if kind == "MACHINE":
            key = pre.get("key")
            # **取布尔，不取真值**（OI-PF-182）。原为 `if not ctx.get(key)`，
            # 于是字符串 "false" / "False" / "0" / "no" 与 [0] / {"ok": False}
            # 全部判为「前置满足」—— 而这些正是从 JSON / 环境变量 / CLI 取值时
            # 天然会出现的形态。以 raw_artifact 的 rights_gate_and_parse_ok
            # （= 经 L3 权利门 + L6 解析成功）为例，那等于**权利门被静默放行**。
            #
            # 该缺陷此前不可达（那些行无 production 调用点）；OI-PF-180 的修复
            # 使 cas_insert 覆盖全部 ORM 类型后，**15 个 ENFORCED 对象的
            # MACHINE 前置全部变成活的**，故此处必须同批处置。
            v = ctx.get(key)
            if v is not True:
                raise SchemaError(
                    "E-PRECOND-001", obj_type,
                    f"前置不满足: {pre.get('description')}（key={key}，"
                    f"实测 {v!r}）—— MACHINE 前置须**显式为布尔 True**，"
                    f"非布尔一律拒（字符串 \"false\" 之类不得当作满足）")
        elif kind == "MANUAL":
            if ctx.get("acknowledged") is not True:
                raise SchemaError("E-PRECOND-002", obj_type,
                                  f"MANUAL 前置未经确认: {pre.get('description')} "
                                  f"（由 {pre.get('guaranteed_by')} 保证）")
        # NONE / PENDING_RULING 不校验
    return None
