#!/usr/bin/env python3
"""build_rights_matrix_mirror.py —— 台账 rights-matrix.json → 工程精简镜像。

工程镜像只含**机器可读字段**（source_key → actions 状态判定）：
  · 不含台账的治理叙事文本（被禁来源的登记文本会被 upstream_taint_scan
    零容忍拦截，OI-PF-024 —— 镜像只承载机器可读判定字段）
  · 镜像的权威性由台账 audit 校验（镜像哈希 vs 台账派生值）
用法：python3 backend/tools/build_rights_matrix_mirror.py <台账矩阵路径> <输出路径>
"""
import json
import os
import sys


def build(source: str, out: str) -> dict:
    m = json.load(open(source, encoding="utf-8"))
    mirror = {
        "schema": "rights-matrix-mirror/1.0",
        "mirror_of": os.path.basename(source),
        "produced_at": m.get("produced_at", ""),
        "policy": {"default": m.get("policy", {}).get("default", "")},
        "data_sources": [],
    }
    for d in m.get("data_sources", []):
        sk = d.get("source_key")
        if not sk:
            continue
        mirror["data_sources"].append({
            "id": d.get("id", ""),
            "source_key": sk,
            "actions": d.get("actions", {}),
        })
    with open(out, "w", encoding="utf-8") as f:
        json.dump(mirror, f, ensure_ascii=False, indent=1)
        f.write("\n")
    return mirror


if __name__ == "__main__":
    src = sys.argv[1]
    out = sys.argv[2]
    build(src, out)
    print(f"镜像已写: {out}")
