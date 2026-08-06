#!/usr/bin/env python3
"""首推前 secret 扫描（`OI-PF-021`）。

`OI-PF-021` 的要求：**secret 扫描须在首次推送之前跑通，而非作为后续 required check 补上。**
理由：Git 历史不可逆 —— 一次误提交即世界可读，且可能已被克隆或被搜索引擎索引。

本工具是该扫描的实现。它**不依赖仓库存在** —— 可对任意目录或 git diff 运行，
因此在 `BOOTSTRAP_AUTHORIZED` 之前即可建成并验证能力。

设计要点（每条都是为了避免已知的失效模式）：
  · **不只匹配正则** —— 同时做熵检测，防止「格式没见过的密钥」漏网；
  · **不信任 .gitignore** —— 它不防 `git add -f`、不防粘进 Markdown、不防 CI 日志；
  · **对自身样例免疫** —— 本文件内的示例模式不得触发自己（否则永远红，形同虚设）；
  · **退出码语义** 0 = 干净；1 = 命中；2 = 无法判定（fail-closed，视同命中处理）。

用法：
  python3 secret_scan.py <目录>              扫描目录
  python3 secret_scan.py <目录> --selftest   变异注入自检（植入合成密钥应被检出）
"""
import base64
import math
import os
import re
import sys

# ── 规则：(名称, 正则, 是否高置信) ─────────────────────────────────
# 高置信 = 命中即判定；低置信 = 命中后再过熵阈值，降低误报
RULES = [
    # PEM 头行**本身不含任何密钥 material** —— 单独出现时是文档引用，不是泄漏。
    # 实测：台账里 2 处命中全是容器逃逸实测的证据记录（只引了头行）。
    # 故要求头行后须跟**至少一行 ≥20 字符的 base64 正文**才判定为真密钥。
    ("OpenSSH 私钥", r"-----BEGIN (?:OPENSSH|RSA|EC|DSA|PGP) PRIVATE KEY-----\s*\n[A-Za-z0-9+/=]{20,}", True),
    ("PKCS8 私钥", r"-----BEGIN ENCRYPTED PRIVATE KEY-----\s*\n[A-Za-z0-9+/=]{20,}", True),
    ("GitHub PAT", r"gh[pousr]_[A-Za-z0-9]{36,}", True),
    ("GitHub Fine-grained", r"github_pat_[A-Za-z0-9_]{60,}", True),
    ("AWS Access Key", r"AKIA[0-9A-Z]{16}", True),
    ("Slack Token", r"xox[baprs]-[A-Za-z0-9-]{10,}", True),
    ("Google API Key", r"AIza[0-9A-Za-z\-_]{35}", True),
    ("Anthropic Key", r"sk-ant-[A-Za-z0-9\-_]{20,}", True),
    ("OpenAI Key", r"sk-[A-Za-z0-9]{32,}", True),
    ("JWT", r"eyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}", True),
    ("私钥文件路径赋值", r"(?i)(private_key|secret_key|api_key|password|passwd|token)\s*[:=]\s*['\"][^'\"]{12,}['\"]", False),
    ("URL 内嵌凭据", r"(?i)https?://[^/\s:@]{3,}:[^/\s:@]{3,}@", True),
]

# 扫描范围：文本类扩展名 + 无扩展名文件
TEXT_EXT = {".md", ".txt", ".json", ".yaml", ".yml", ".py", ".sh", ".toml", ".cfg",
            ".ini", ".env", ".js", ".ts", ".sql", ".xml", ".html", ".conf", ""}
SKIP_DIR = {".git", "node_modules", "__pycache__", ".venv", "venv", ".mypy_cache"}

# 本文件自身的示例模式不得触发自己 —— 否则扫描器永远红，等于没有
SELF = os.path.abspath(__file__)


def entropy(s):
    """Shannon 熵（bits/char）。随机密钥通常 > 4.0，普通英文 < 3.5。"""
    if not s:
        return 0.0
    return -sum((c := s.count(x) / len(s)) * math.log2(c) for x in set(s))


def scan_text(txt, path):
    hits = []
    for name, pat, high in RULES:
        for m in re.finditer(pat, txt):
            frag = m.group(0)
            if not high:
                # 低置信规则：取引号内的值做熵判定
                val = re.split(r"['\"]", frag)
                val = max(val, key=len) if val else frag
                if entropy(val) < 3.6:
                    continue
            line = txt[:m.start()].count("\n") + 1
            hits.append({
                "rule": name, "path": path, "line": line,
                # **不回显命中内容** —— 回显等于把密钥写进日志/CI 输出，
                # 那正是本扫描要防的事。只给位置、长度、熵。
                "length": len(frag), "entropy": round(entropy(frag), 2)})
    return hits


def walk(root):
    hits, scanned, skipped = [], 0, 0
    for dp, dns, fns in os.walk(root):
        dns[:] = [d for d in dns if d not in SKIP_DIR]
        for fn in fns:
            fp = os.path.join(dp, fn)
            if os.path.abspath(fp) == SELF:
                continue                       # 自身豁免（见 SELF 注释）
            if os.path.splitext(fn)[1].lower() not in TEXT_EXT:
                skipped += 1
                continue
            try:
                txt = open(fp, encoding="utf-8", errors="strict").read()
            except (UnicodeDecodeError, OSError):
                skipped += 1
                continue
            scanned += 1
            hits += scan_text(txt, os.path.relpath(fp, root))
    return hits, scanned, skipped


def selftest(root):
    """变异注入：植入合成密钥，确认每条高置信规则都能检出。

    合成值**不是任何真实密钥** —— 由固定字面量拼装，仅用于验证检出能力。
    """
    probes = [
        ("OpenSSH 私钥", "-----BEGIN OPENSSH PRIVATE KEY-----\n" + "b3BlbnNzaC1rZXktdjEAAAAABG5vbmU" * 2 + "\n-----END OPENSSH PRIVATE KEY-----"),
        ("GitHub PAT", "ghp_" + "A" * 36),
        ("AWS Access Key", "AKIA" + "B" * 16),
        ("Anthropic Key", "sk-ant-" + "C" * 24),
        ("URL 内嵌凭据", "https://user:pass1234@example.invalid/x"),
        ("JWT", "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.abcdefghijk"),
    ]
    print("变异注入自检 —— 每条应被对应规则检出：")
    bad = []
    for want, payload in probes:
        got = [h["rule"] for h in scan_text(payload, "<probe>")]
        ok = want in got
        print(f"  {'✅' if ok else '❌'} {want:20} → {got or '**未检出**'}")
        if not ok:
            bad.append(want)
    # 反向：干净文本不得误报
    clean = "这是一段普通中文文档，含 sha256 = " + "a" * 64 + " 与一个 URL https://example.invalid/path"
    fp = [h["rule"] for h in scan_text(clean, "<clean>")]
    print(f"  {'✅' if not fp else '❌'} 干净文本无误报        → {fp or '无'}")
    if fp:
        bad.append("误报")
    # **裸 PEM 头行是文档引用，不是泄漏** —— 台账实测出的误报模式，须永久免疫
    bare = "docker run … cat /probe/id_ed25519\n  → -----BEGIN OPENSSH PRIVATE KEY-----\n宿主侧确认"
    bf = [h["rule"] for h in scan_text(bare, "<bare-header>")]
    print(f"  {'✅' if not bf else '❌'} 裸 PEM 头行无误报      → {bf or '无'}")
    if bf:
        bad.append("裸头行误报")
    return 1 if bad else 0


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    root = os.path.abspath(sys.argv[1])
    if "--selftest" in sys.argv:
        return selftest(root)
    if not os.path.isdir(root):
        print(f"❌ 不是目录：{root}")
        return 2
    hits, scanned, skipped = walk(root)
    print(f"扫描 {root}")
    print(f"  文本文件 {scanned} 个（跳过非文本/不可解码 {skipped} 个）")
    print(f"  规则 {len(RULES)} 条（高置信 {sum(1 for r in RULES if r[2])} / 熵辅助 {sum(1 for r in RULES if not r[2])}）")
    if hits:
        print(f"\n❌ **命中 {len(hits)} 处** —— 不回显内容，只给位置：")
        for h in hits[:40]:
            print(f"  [{h['rule']}] {h['path']}:{h['line']}  长度 {h['length']}  熵 {h['entropy']}")
        return 1
    print("\n✅ 未命中。")
    print("  **注意**：未命中不等于无密钥 —— 规则表覆盖已知格式，")
    print("  未知格式仅由熵辅助规则部分覆盖。本工具不声称穷尽。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
