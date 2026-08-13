#!/usr/bin/env python3
"""公开仓库卫生守卫（OI-PF-186）。

**这一维度此前零覆盖。** AGENT_ADVERSARIAL_REVIEW 跑了九轮，审的全是代码行为
与契约一致性，**没有一轮问过「公开仓库里实际躺着什么」**。`mut_g6path.py`
（台账侧变异脚本，含本机路径与私有台账目录布局）由 PR #78 被 `git add -A`
扫进仓库根，在公开 main 上待了一整天，是看合并输出时偶然撞见的。
**「偶然发现」本身就是该维度无覆盖的证据。**

  H-1  受跟踪文件内不得出现本机绝对路径（/Users/… 或 /home/…）
  H-2  仓库根的受跟踪条目须与声明集合**完全一致**（多一个即判红）

H-2 是**默认拒绝**，不是豁免清单：不在声明集合内的一律判红。
方向与「白名单内放行、单外也放行」的穷举清单相反 —— 后者不可证完备，
前者只要声明集合本身被 review 过，新增物就无处藏身。

用法：python3 backend/tools/repo_hygiene_check.py <repo_root>
"""
import os
import re
import subprocess
import sys

# ── H-2：仓库根允许出现的受跟踪条目（默认拒绝：不在此列即判红）──
ROOT_ALLOWED = {
    ".env.example", ".github", ".gitignore", "CODEOWNERS", "Dockerfile",
    "LICENSE", "SECURITY.md", "THIRD_PARTY_NOTICES", "backend", "contracts",
    "docs", "frontend", "infra", "requirements.txt", "sbom.json",
}

# ── H-1：本机绝对路径。/home/runner 是 CI 工作目录，由 Actions 注入到
#        锁文件与配置里，非编写者的个人路径，故按前缀排除（写明理由，
#        不是「看着眼熟就放过」）。
_ABS = re.compile(r"(?<![\w.-])(/Users/[A-Za-z0-9._-]+|/home/(?!runner\b)[A-Za-z0-9._-]+)/")

_TEXT_EXT = {".py", ".md", ".yml", ".yaml", ".json", ".toml", ".cfg", ".ini",
             ".txt", ".sh", ".ts", ".tsx", ".js", ".jsx", ".html", ".css",
             ".sql", ".mako", ".example", ""}

# 本文件自身含示例正则与说明文字，逐条排除会退化成豁免清单；
# 改为：本文件只在 H-1 中跳过**自身路径**，且该事实在此写明。
_SELF = "backend/tools/repo_hygiene_check.py"


def abs_path_hit(line: str):
    """H-1 判据：一行里是否含本机绝对路径。返回命中串或 None。

    与 test_scanners_mutation.py 的约定一致 —— **载荷表独立于本规则**维护，
    规则被改动或误删时对应正例必须变红，不「自己出题自己答」。
    """
    m = _ABS.search(line)
    return m.group(0) if m else None


def root_violations(roots):
    """H-2 判据：仓库根条目集合 vs 声明集合。返回违规说明列表。

    **默认拒绝**：不在 ROOT_ALLOWED 内的一律判红。这与「白名单内放行、
    单外也放行」的穷举清单方向相反 —— 后者不可证完备（规则 ㉝），
    前者只要声明集合本身被 review 过，新增物就无处藏身。
    """
    out = []
    for e in sorted(set(roots) - ROOT_ALLOWED):
        out.append(f"H-2: 仓库根多出受跟踪条目 {e!r} —— 未在 ROOT_ALLOWED 声明")
    for m in sorted(ROOT_ALLOWED - set(roots)):
        out.append(f"H-2: 声明的根条目 {m!r} 已不存在 —— 声明集合须随之更新")
    return out


def tracked(root):
    r = subprocess.run(["git", "-C", root, "ls-files", "-z"],
                       capture_output=True, text=True)
    if r.returncode != 0:
        raise SystemExit(f"E-HYG-000: git ls-files 失败（rc={r.returncode}）"
                         f"—— 无法枚举受跟踪文件，不默认放行\n{r.stderr[:200]}")
    return [p for p in r.stdout.split("\0") if p]


def main() -> int:
    root = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else ".")
    files = tracked(root)
    bad = []

    # ── H-1 ──
    for rel in files:
        if rel == _SELF:
            continue
        if os.path.splitext(rel)[1].lower() not in _TEXT_EXT:
            continue
        p = os.path.join(root, rel)
        try:
            with open(p, encoding="utf-8", errors="strict") as f:
                src = f.read()
        except (OSError, UnicodeDecodeError):
            continue
        for i, line in enumerate(src.splitlines(), 1):
            hit = abs_path_hit(line)
            if hit:
                bad.append(f"H-1: {rel}:{i} 含本机绝对路径 {hit!r}")

    # ── H-2 ──
    roots = {rel.split("/", 1)[0] for rel in files}
    bad.extend(root_violations(roots))

    if bad:
        print("❌ 仓库卫生违规（OI-PF-186）：")
        for b in bad[:40]:
            print("  - " + b)
        if len(bad) > 40:
            print(f"  …… 另 {len(bad) - 40} 条")
        return 1
    print(f"✅ 检查对象 {len(files)} 个受跟踪文件："
          f"H-1 无本机绝对路径 · H-2 根条目 {len(roots)} 项与声明完全一致")
    return 0


if __name__ == "__main__":
    sys.exit(main())
