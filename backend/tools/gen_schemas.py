"""生成 contracts/schema/*.schema.json —— 13 类 canonical 对象（G1-02）。

每个 Schema 含：id/version、必填、枚举、约束、additionalProperties: false。
时间字段一律 ISO-8601 UTC（G0-05 时间六元组）；数值一律 Decimal 字符串（避免浮点）。
写权由 contracts/writers.json 承载（G0-04 §2 写权矩阵的可执行面）。
"""

import json
import os

OUT = os.path.join(os.path.dirname(__file__), "..", "..", "contracts", "schema")

BASE = {
    "$schema": "http://json-schema.org/draft-07/schema#",
    "schema_version": "1.0.0",
}

# 公共片段
TS = {"type": "string", "pattern": r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$"}
DEC = {"type": "string", "pattern": r"^-?\d+(\.\d+)?$"}
SHA = {"type": "string", "pattern": r"^[0-9a-f]{64}$"}
ID = {"type": "string", "pattern": r"^[A-Z0-9][A-Z0-9_\-]{2,63}$"}

SCHEMAS = {
    "source": {
        "description": "数据来源登记（VD-12/13 权利判定结果）",
        "type": "object",
        "required": ["id", "schema_version", "kind", "status", "legal_basis"],
        "properties": {
            "id": ID,
            "kind": {"enum": ["PRIMARY", "SECONDARY"]},
            "name": {"type": "string", "minLength": 1},
            "status": {"enum": ["ALLOWED", "UNKNOWN", "PROHIBITED"]},
            "legal_basis": {"type": "string", "minLength": 1},
            "terms_url": {"type": "string"},
            "attribution_required": {"type": "boolean"},
            "updated_at": TS,
        },
    },
    "raw_artifact": {
        "description": "原始字节对象（L1 三层分离：入本地对象库，仓内只留指针）",
        "type": "object",
        "required": ["id", "schema_version", "source_id", "sha256", "bytes", "acquired_at"],
        "properties": {
            "id": ID,
            "source_id": ID,
            "sha256": SHA,
            "bytes": {"type": "integer", "minimum": 1},
            "content_type": {"type": "string"},
            "acquired_at": TS,
        },
    },
    "acquisition_event": {
        "description": "取得事件（去重保谱系：同字节多次取得须保留全部事件，BF-07）",
        "type": "object",
        "required": ["id", "schema_version", "artifact_id", "source_id", "acquired_at", "ok"],
        "properties": {
            "id": ID,
            "artifact_id": ID,
            "source_id": ID,
            "acquired_at": TS,
            "ok": {"type": "boolean"},
            "error": {"type": "string"},
            "retry_of": ID,
        },
    },
    "fact": {
        "description": "事实记录（LLM 不得写入 —— 写权矩阵）",
        "type": "object",
        "required": ["id", "schema_version", "artifact_id", "metric", "value", "unit",
                     "period", "scope", "basis", "vintage", "locator", "parser_version"],
        "properties": {
            "id": ID,
            "artifact_id": ID,
            "metric": {"type": "string", "minLength": 1},
            "value": DEC,
            "unit": {"type": "string", "minLength": 1},
            "period": {"type": "string", "minLength": 1},
            "scope": {"type": "string", "minLength": 1},
            "basis": {"type": "string"},
            "vintage": {"type": "string"},
            "locator": {"type": "string", "minLength": 1},
            "parser_version": {"type": "string"},
            "comparability": {"enum": ["COMPARABLE", "NOT_COMPARABLE"]},
        },
    },
    "snapshot": {
        "description": "冻结快照（cutoff 后不可改；漂移输入必拒绝，BF-01）",
        "type": "object",
        "required": ["id", "schema_version", "created_at", "cutoff", "frozen", "facts"],
        "properties": {
            "id": ID,
            "created_at": TS,
            "cutoff": TS,
            "frozen": {"type": "boolean"},
            "facts": {"type": "array", "items": ID, "minItems": 1},
            "golden": {"type": "boolean"},
        },
    },
    "assumption": {
        "description": "假设提案（批准事件由 L12 批准端点写入，一切自动化路径无例外）",
        "type": "object",
        "required": ["id", "schema_version", "proposal", "status"],
        "properties": {
            "id": ID,
            "proposal": {"type": "string", "minLength": 1},
            "status": {"enum": ["PENDING", "APPROVED", "REJECTED"]},
            "reviewer": {"type": "string"},
            "decided_at": TS,
        },
    },
    "calc": {
        "description": "计算记录（输入必须是已冻结对象，M2）",
        "type": "object",
        "required": ["id", "schema_version", "snapshot_id", "formula", "inputs",
                     "output", "unit", "calc_engine_version"],
        "properties": {
            "id": ID,
            "snapshot_id": ID,
            "formula": {"type": "string", "minLength": 1},
            "inputs": {"type": "array", "items": ID, "minItems": 1},
            "output": DEC,
            "unit": {"type": "string"},
            "calc_engine_version": {"type": "string"},
            "check": {"type": "object"},
        },
    },
    "claim": {
        "description": "研究主张（引用必须可解析）",
        "type": "object",
        "required": ["id", "schema_version", "statement", "refs"],
        "properties": {
            "id": ID,
            "statement": {"type": "string", "minLength": 1},
            "refs": {"type": "array", "items": ID},
            "status": {"enum": ["DRAFT", "SUPPORTED", "DISPUTED"]},
        },
    },
    "prediction": {
        "description": "预测预登记（永久 CALIBRATION_PENDING，VD-26；LLM 不得写入）",
        "type": "object",
        "required": ["id", "schema_version", "claim_id", "horizon", "probability",
                     "calibration_pending"],
        "properties": {
            "id": ID,
            "claim_id": ID,
            "horizon": TS,
            "probability": {"type": "number", "minimum": 0, "maximum": 1},
            "calibration_pending": {"const": True},
            "registered_at": TS,
        },
    },
    "approval": {
        "description": "批准事件（唯一准出谓词的前提；LLM 不得写入）",
        "type": "object",
        "required": ["id", "schema_version", "object_ref", "approver",
                     "approved_at", "subject_root_hash"],
        "properties": {
            "id": ID,
            "object_ref": ID,
            "approver": {"type": "string", "minLength": 1},
            "approved_at": TS,
            "subject_root_hash": SHA,
        },
    },
    "open_item": {
        "description": "开放项（材料性/阻断分类，ADR-003/010）",
        "type": "object",
        "required": ["id", "schema_version", "title", "status", "material"],
        "properties": {
            "id": ID,
            "title": {"type": "string", "minLength": 1},
            "status": {"enum": ["OPEN", "CLOSED"]},
            "material": {"type": "boolean"},
            "blocks": {"type": "array", "items": {"type": "string"}},
        },
    },
    "candidate": {
        "description": "研究候选（PARTIAL 候选不得提升为 current，VD-27）",
        "type": "object",
        "required": ["id", "schema_version", "kind", "payload"],
        "properties": {
            "id": ID,
            "kind": {"type": "string", "minLength": 1},
            "payload": {"type": "object"},
            "parent": ID,
            "current": {"type": "boolean"},
        },
    },
    "release": {
        "description": "发布（父版本 CAS 成功 + 唯一准出谓词为真；LLM 不得写入 current）",
        "type": "object",
        "required": ["id", "schema_version", "version", "parent_cas", "released_at",
                     "approval_id"],
        "properties": {
            "id": ID,
            "version": {"type": "string", "pattern": r"^\d+\.\d+\.\d+$"},
            "parent_cas": SHA,
            "released_at": TS,
            "approval_id": ID,
            "current_pointer": {"type": "boolean"},
        },
    },
}

def main():
    os.makedirs(OUT, exist_ok=True)
    for name, props in SCHEMAS.items():
        doc = dict(BASE)
        doc.update({
            "title": f"{name} canonical schema",
            "description": props.pop("description"),
            "type": props["type"],
            "required": props["required"],
            "properties": {"schema_version": {"type": "string"}, **props["properties"]},
            "additionalProperties": False,
        })
        fp = os.path.join(OUT, f"{name}.schema.json")
        with open(fp, "w", encoding="utf-8") as fh:
            json.dump(doc, fh, ensure_ascii=False, indent=1)
            fh.write("\n")
    print(f"已生成 {len(SCHEMAS)} 个 Schema -> {OUT}")

if __name__ == "__main__":
    main()
