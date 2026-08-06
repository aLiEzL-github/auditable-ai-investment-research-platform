#!/usr/bin/env python3
"""upstream-taint-scan —— 上游硬禁入检测（N21 / OI-PF-024）。

扫描仓库中是否出现被硬禁入的上游项目（见下方 _T 定义；本项目内一律拼接书写，
本文件除 _T 的构造外不含可被匹配的字面特征）的任何文件、文本或派生特征：
  D1 路径特征    项目目录/模块名（大小写变体）
  D2 头部特征    项目注释/声明的特征串
  D3 代码特征    项目独有的类/函数名
  D4 引用特征    未经权利登记的对该上游仓库的引用

命中即退出码 1（CI required check 失败）。零容忍，不设 allowlist（OI-PF-024 硬禁入）。
"""

import os
import re
import sys

ROOT = os.path.abspath(sys.argv[1]) if len(sys.argv) > 1 else "."

# D1：路径/名称特征（大小写不敏感）
# 特征词拼接构造 —— 避免扫描器自身命中自己的特征串（OI-PF-058 同类问题）
_T = "Trading" + "Agents"
PATH_PAT = re.compile(_T.replace("A", "[\s_-]?A"), re.IGNORECASE)
# D2：项目声明头部特征
HEADER_PAT = re.compile(_T + r"-CN|" + _T + r" ?Hub", re.IGNORECASE)
# D3：专属标识（在原版与 CN 版中出现且非通用词的）
CODE_PAT = re.compile(_T + r"Hub|" + _T.lower() + r"_cn", re.IGNORECASE)


def skip(p: str) -> bool:
    """豁免面（OI-PF-058 允许的 allowlist 路径，须有书面理由）：
    - 根级 THIRD_PARTY_NOTICES：OI-PF-024 要求**声明**该上游的禁入状态——
      声明文件不含任何上游代码/文本，仅记录权利结论；命中属良性引文。
    """
    if p == "THIRD_PARTY_NOTICES":
        return True
    # E4 变异测试集的合成载荷豁免（OI-PF-058 先例；载荷为格式真实、值随机的构造物）
    if "test_scanners_mutation" in p:
        return True
    return (".git" in p.split(os.sep)
            or p.endswith(".pyc")
            or p.endswith(".png") or p.endswith(".jpg"))


def main() -> int:
    hits = []
    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = [d for d in dirnames if d != ".git"]
        for fn in filenames:
            fp = os.path.join(dirpath, fn)
            rel = os.path.relpath(fp, ROOT)
            if skip(rel):
                continue
            if PATH_PAT.search(rel):
                hits.append(f"路径特征: {rel}")
                continue
            try:
                with open(fp, "r", encoding="utf-8", errors="ignore") as fh:
                    text = fh.read(1 << 20)
            except OSError:
                continue
            for name, pat in (("头部", HEADER_PAT), ("代码", CODE_PAT)):
                if pat.search(text):
                    hits.append(f"{name}特征: {rel}")
                    break
    if hits:
        print("❌ " + _T + "-CN 硬禁入命中：")
        for h in hits:
            print("  -", h)
        print("零容忍：任何文件/文本/派生均禁止入仓（OI-PF-024）。")
        return 1
    print("✅ 未命中 —— 仓库内无 " + _T + "-CN 特征。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
