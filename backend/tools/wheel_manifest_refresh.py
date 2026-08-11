#!/usr/bin/env python3
"""wheel_manifest_refresh.py —— 刷新 contracts/wheel_manifest.json（**出网**）。

本工具是 wheel_policy_check 的**离线化**代价所在（OI-PF-141）：检查器不出网，
代价是「哈希 → 文件名」的映射须由本工具显式刷新，其差异在 PR 中评审。

出网范围：仅 https://pypi.org/pypi/{name}/{version}/json，只读元数据，
不下载分发包、不执行任何上游代码。故在 arch_import_check 中单列豁免。

**不在 CI 中运行** —— CI 只跑离线的 wheel_policy_check。手动执行：
    python3 backend/tools/wheel_manifest_refresh.py [repo_root]
"""
import json
import os
import re
import ssl
import subprocess
import sys
import urllib.request

ROOT = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else ".")
REQ = os.path.join(ROOT, "requirements.txt")
MANIFEST = os.path.join(ROOT, "contracts", "wheel_manifest.json")

# OI-PF-147：依赖行可带 PEP 508 环境标记（`pkg==1.0 ; platform_system == "Linux"`）。
# 初版正则不认标记，两条平台条件依赖被整行漏掉 —— 由 E-WHEEL-003 的逐行交叉
# 核对当场抓出（逐行 36 vs 解析 34）。标记捕获为第 4 组，供 §平台闭包核对使用。
PIN = re.compile(
    r"^([A-Za-z0-9_.\-]+)(\[[^\]]*\])?==([^\s\\;]+)"
    r"[ \t]*(;[^\\\n]*)?"
    r"((?:[ \t]*\\\n[ \t]*--hash=sha256:[0-9a-f]{64})+)", re.M)


def main() -> int:
    try:
        import certifi
        ctx = ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        ctx = ssl.create_default_context()
    txt = open(REQ, encoding="utf-8").read()
    now = subprocess.run(["date", "-u", "+%Y-%m-%dT%H:%M:%SZ"],
                         capture_output=True, text=True).stdout.strip()
    files, missing, n = {}, [], 0
    for m in PIN.finditer(txt):
        name, ver = m.group(1), m.group(3)
        hashes = re.findall(r"sha256:([0-9a-f]{64})", m.group(5))
        url = f"https://pypi.org/pypi/{name}/{ver}/json"
        d = json.load(urllib.request.urlopen(url, timeout=30, context=ctx))
        fmap = {u["digests"]["sha256"]: u["filename"] for u in d["urls"]}
        for h in hashes:
            if h in fmap:
                files[h] = fmap[h]
            else:
                missing.append(f"{name}=={ver} 的哈希 {h[:12]}… 在 PyPI 上无对应文件")
        n += 1
    if missing:
        for x in missing:
            print(f"  - {x}")
        print(f"❌ {len(missing)} 条哈希无法回源 —— 拒绝写出清单")
        return 1
    old = {}
    if os.path.exists(MANIFEST):
        old = json.load(open(MANIFEST, encoding="utf-8")).get("files", {})
    json.dump({"schema": "wheel-manifest/1.0",
               "rationale": ("OI-PF-131/OI-PF-141：wheel_policy_check 须离线运行 —— "
                             "守卫自身不得出网（M1/M4）。本清单把「哈希 → PyPI 上的"
                             "文件名」固化为已提交、经 PR 评审的制品，检查器只读它。"),
               "refreshed_at": now,
               "source": "https://pypi.org/pypi/{name}/{version}/json",
               "files": files},
              open(MANIFEST, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    added = sorted(set(files) - set(old))
    removed = sorted(set(old) - set(files))
    changed = [h for h in set(files) & set(old) if files[h] != old[h]]
    whl = sum(1 for f in files.values() if f.endswith(".whl"))
    print(f"✅ 清单已刷新：{n} 个包 · {len(files)} 条映射（wheel {whl} · "
          f"sdist {len(files) - whl}）")
    print(f"   相对上一版：新增 {len(added)} · 移除 {len(removed)} · 变更 {len(changed)}")
    for h in changed:
        print(f"   **变更** {h[:12]}…: {old[h]} → {files[h]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
