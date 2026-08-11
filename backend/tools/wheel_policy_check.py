#!/usr/bin/env python3
"""wheel_policy_check.py —— 供应链：每个依赖须有 wheel（OI-PF-131）。

`--require-hashes` 保证的是「下载到的字节 == 登记的哈希」，**不保证那份字节
不是 sdist**。sdist 安装时会执行上游的 setup.py / PEP 517 后端 —— 哈希锁定
对这条代码执行路径完全无效。第十三轮的实例：首次生成哈希时选择器只匹配
cp311，curl_cffi 与 mini-racer 双双落到 sdist，恰是风险最高的两个组件。

**本检查离线运行**（OI-PF-141）：初版直接查 PyPI，被 arch_import_check 抓到
「守卫自身引入网络库」—— 那是对的，守卫不该出网。故「哈希 → 文件名」的映射
固化为已提交、经 PR 评审的制品 contracts/wheel_manifest.json；
刷新须显式运行 backend/tools/wheel_manifest_refresh.py。

判定：
  · 每条哈希须在清单中有对应文件名  → 否则 FAIL（清单过期即判红，不放行）
  · 每个包至少一条 .whl             → 否则 FAIL
  · 仅有 sdist 的包须在契约白名单内并写明理由 → 否则 FAIL
用法：python3 backend/tools/wheel_policy_check.py [repo_root]
"""
import json
import os
import re
import sys

ROOT = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else ".")
REQ = os.path.join(ROOT, "requirements.txt")
POLICY = os.path.join(ROOT, "contracts", "supply_chain_policy.json")
MANIFEST = os.path.join(ROOT, "contracts", "wheel_manifest.json")

# PEP 508 extras：psycopg[binary]==3.3.4 —— 不认 extras 会整行漏掉（OI-PF-134）
# OI-PF-147：依赖行可带 PEP 508 环境标记（`pkg==1.0 ; platform_system == "Linux"`）。
# 初版正则不认标记，两条平台条件依赖被整行漏掉 —— 由 E-WHEEL-003 的逐行交叉
# 核对当场抓出（逐行 36 vs 解析 34）。标记捕获为第 4 组，供 §平台闭包核对使用。
PIN = re.compile(
    r"^([A-Za-z0-9_.\-]+)(\[[^\]]*\])?==([^\s\\;]+)"
    r"[ \t]*(;[^\\\n]*)?"
    r"((?:[ \t]*\\\n[ \t]*--hash=sha256:[0-9a-f]{64})+)", re.M)


def parse(path):
    txt = open(path, encoding="utf-8").read()
    out = [(m.group(1), m.group(3),
            re.findall(r"sha256:([0-9a-f]{64})", m.group(5)))
           for m in PIN.finditer(txt)]
    # 交叉核对：逐行数出的固定行数须与解析结果一致（沿用 E-SBOM-001 的做法，
    # 避免「解析器漏认 → 检查范围静默缩小」）
    n_pin = sum(1 for ln in txt.splitlines()
                if re.match(r"^[A-Za-z0-9_.\-]+(\[[^\]]*\])?\s*==", ln.strip()))
    assert n_pin == len(out), (
        f"E-WHEEL-003: 逐行数出 {n_pin} 个固定行，解析器只认出 {len(out)} 个 —— "
        f"有依赖被静默漏掉，检查范围不可信")
    return out


def main() -> int:
    pol = json.load(open(POLICY, encoding="utf-8"))
    allow = {(a["name"].lower().replace("_", "-"), a["version"]): a
             for a in pol.get("sdist_only_allowlist", [])}
    files = json.load(open(MANIFEST, encoding="utf-8"))["files"]

    bad, checked, sdist_only = [], 0, []
    for name, ver, hashes in parse(REQ):
        key = f"{name}=={ver}"
        checked += 1
        unknown = [h for h in hashes if h not in files]
        if unknown:
            bad.append(f"{key}: {len(unknown)}/{len(hashes)} 条哈希不在 "
                       f"contracts/wheel_manifest.json 中 —— 清单过期或依赖被改动，"
                       f"**无法证明其为 wheel**。须运行 wheel_manifest_refresh.py "
                       f"并在 PR 中评审差异（E-WHEEL-004）")
            continue
        picked = [files[h] for h in hashes]
        if any(f.endswith(".whl") for f in picked):
            continue
        k = (name.lower().replace("_", "-"), ver)
        if k in allow:
            sdist_only.append(f"{key}（{allow[k]['status']}）")
        else:
            bad.append(f"**{key} 只登记了 sdist**（{picked}）—— 安装时将在本机与 CI "
                       f"上执行上游构建脚本，哈希锁定对此无效。须改用 wheel，或在 "
                       f"contracts/supply_chain_policy.json 中列明理由（E-WHEEL-001）")
    # 清单不得比 requirements 更宽：多余条目说明清单未随依赖收缩而更新
    used = {h for _, _, hs in parse(REQ) for h in hs}
    stale = [h for h in files if h not in used]
    if stale:
        bad.append(f"清单中有 {len(stale)} 条哈希已不在 requirements.txt 中 —— "
                   f"清单未随依赖变更同步（E-WHEEL-005）")

    for b in bad:
        print(f"  - {b}")
    if bad:
        print(f"❌ 供应链 wheel 政策违规 {len(bad)} 处")
        return 1
    tail = (f"；仅 sdist 且已列明 {len(sdist_only)} 个（{'; '.join(sdist_only)}）"
            if sdist_only else "；全部有 wheel")
    print(f"✅ 供应链 wheel 政策合规：检查对象 {checked} 个包 / {len(used)} 条哈希{tail}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
