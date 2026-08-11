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
        # OI-PF-134：原正则不认 PEP 508 extras（psycopg[binary]==3.3.4），
        # 该行被整行跳过，且其 --hash 因 cur 仍指向上一个包而被**错记到上一个包名下**。
        # 实测：psycopg[binary] 的 b6bbc25c… 曾被记在 greenlet 名下。
        m = re.match(r"^([A-Za-z0-9_.\-]+)(\[[^\]]*\])?==([^\s\\]+)", line)
        if m:
            cur = {"name": m.group(1), "extras": m.group(2) or "",
                   "version": m.group(3), "hashes": []}
            pkgs.append(cur)
        elif re.match(r"^[^\s#\-]", line) and "==" in line:
            # 既不是注释、不是哈希续行，又含 ==，却没被上面认出 —— 不得静默跳过
            raise AssertionError(f"E-SBOM-003: 无法解析的依赖行，拒绝静默跳过（OI-PF-134）: {line[:70]}")
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

    # OI-PF-134：原一致性断言用**同一个解析器**重跑，故对解析器自身的缺陷完全免疫
    #（漏认 psycopg[binary] 时，两次解析同样漏认，断言照样通过）。
    # 改为用一个**独立于 parse_requirements 的逐行计数**作交叉核对。
    raw = open(REQ, encoding="utf-8").read().splitlines()
    n_pin = sum(1 for l in raw
                if re.match(r"^[A-Za-z0-9_.\-]+(\[[^\]]*\])?\s*==", l.strip()))
    n_hash = sum(1 for l in raw if "--hash=sha256:" in l)
    assert n_pin == len(sbom["components"]), (
        f"E-SBOM-001: 逐行数出 {n_pin} 个依赖固定行，SBOM 只有 "
        f"{len(sbom['components'])} 个组件 —— 有依赖被静默漏掉（OI-PF-134）")
    n_sbom_hash = sum(len(c["hashes"]) for c in sbom["components"])
    assert n_hash == n_sbom_hash, (
        f"E-SBOM-002: 逐行数出 {n_hash} 条 --hash，SBOM 记录 {n_sbom_hash} 条 —— "
        f"存在漏记或错记归属（OI-PF-134）")
    dup = {p["name"] for p in pkgs if sum(1 for q in pkgs if q["name"] == p["name"]) > 1}
    assert not dup, f"E-SBOM-004: requirements 中包名重复，SBOM 组件将重复: {sorted(dup)}"

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
