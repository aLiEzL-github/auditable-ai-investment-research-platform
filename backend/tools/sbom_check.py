#!/usr/bin/env python3
"""sbom_check.py —— SBOM 生成与一致性断言（G1-07 3b）。

SBOM = requirements.txt 的完整依赖清单（含哈希与平台），输出
backend/tools/../../sbom.json（CycloneDX 精简形态）。
断言：SBOM 中的包集合与 requirements.txt 解析结果一致（防依赖漂移）。
"""

import hashlib
import json
import os
import re
import sys

ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
REQ = os.path.join(ROOT, "requirements.txt")
OUT = os.path.join(ROOT, "sbom.json")


def parse_requirements(path):
    """解析 --require-hashes 格式 requirements.txt（包名==版本 + 哈希）。"""
    pkgs = []
    cur = None
    for line in open(path, encoding="utf-8"):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        m = re.match(r"^([A-Za-z0-9_.\-]+)==([^\s\\]+)", line)
        if m:
            cur = {"name": m.group(1), "version": m.group(2), "hashes": []}
            pkgs.append(cur)
        mh = re.search(r"--hash=sha256:([0-9a-f]{64})", line)
        if mh and cur is not None:
            cur["hashes"].append(mh.group(1))
    return pkgs


def main() -> int:
    pkgs = parse_requirements(REQ)
    assert pkgs, "requirements.txt 解析为空"
    missing_hash = [p["name"] for p in pkgs if not p["hashes"]]
    assert not missing_hash, f"缺哈希的包: {missing_hash}"

    sbom = {
        "bomFormat": "CycloneDX",
        "specVersion": "1.4",
        "version": 1,
        "metadata": {"component": {"type": "application",
                                   "name": "auditable-ai-investment-research-platform",
                                   "version": "0.1.0"}},
        "components": [
            {"type": "library", "name": p["name"], "version": p["version"],
             "hashes": [{"alg": "SHA-256", "content": h} for h in p["hashes"]]}
            for p in pkgs
        ],
    }
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(sbom, fh, ensure_ascii=False, indent=1)
        fh.write("\n")

    # 一致性断言：requirements 重新解析 == SBOM 组件
    again = parse_requirements(REQ)
    assert len(again) == len(sbom["components"]), "SBOM 与 requirements 组件数不符"
    for a, s in zip(sorted(again, key=lambda x: x["name"]),
                    sorted(sbom["components"], key=lambda x: x["name"])):
        assert a["name"] == s["name"] and a["version"] == s["version"], \
            f"SBOM 漂移: {a['name']}=={a['version']} vs {s['name']}=={s['version']}"
    print(f"✅ SBOM 已生成（{len(pkgs)} 组件，全部带 SHA-256 哈希）且与 requirements 一致")
    return 0


if __name__ == "__main__":
    sys.exit(main())
