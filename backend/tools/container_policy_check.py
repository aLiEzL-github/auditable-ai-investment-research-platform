#!/usr/bin/env python3
"""container_policy_check.py —— 容器策略断言（G1-07 3b / OI-PF-100 / G0-06 §6 N17/N18）。

断言 infra/compose.yaml 与 Dockerfile 满足 container-policy：
  · compose：read_only · cap_drop ALL · no-new-privileges · 资源限制
  · Dockerfile：非 root（USER appuser）· digest 固定 FROM
"""

import os
import re
import sys

ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
COMPOSE = os.path.join(ROOT, "infra", "compose.yaml")
DOCKERFILE = os.path.join(ROOT, "Dockerfile")


def main() -> int:
    bad = []
    c = open(COMPOSE, encoding="utf-8").read()
    d = open(DOCKERFILE, encoding="utf-8").read()

    for label, text, pat in (
        ("compose read_only", c, r"read_only:\s*true"),
        ("compose cap_drop ALL", c, r"cap_drop:\s*\n\s*-\s*ALL"),
        ("compose no-new-privileges", c, r"no-new-privileges:\s*true"),
        ("compose mem_limit", c, r"mem_limit:"),
        ("compose cpus", c, r"cpus:"),
        ("compose 端口 127.0.0.1", c, r"127\.0\.0\.1:8080:8080"),
        ("Dockerfile 非 root", d, r"USER appuser"),
        ("Dockerfile digest 固定 FROM", d, r"FROM python:3\.11-slim@sha256:[0-9a-f]{64}"),
    ):
        if not re.search(pat, text):
            bad.append(label)
    if bad:
        print("❌ container-policy 违规:", bad)
        return 1
    print("✅ container-policy 全部满足（read_only/cap_drop/no-new-privileges/资源限制/非 root/digest）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
