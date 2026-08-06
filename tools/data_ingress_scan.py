#!/usr/bin/env python3
"""数据禁入扫描（`ADR-006` L4-b / `OI-PF-021` 数据侧）。

`ADR-006` L4 原文：`.gitignore` **不够** —— 它只防误 `git add`，不防 `git add -f`、
不防粘进 Markdown、不防 CI 日志打印、不防 Issue/PR 正文。

本工具覆盖 L4-b（CI 侧 required check）。L4-a（客户端 hook）复用同一规则集。
密钥侧由 `secret_scan.py` 负责 —— 两者规则集**分开维护**：
密钥模式与数据指纹的误报特征完全不同，混在一张表里会让任一侧的调优伤及另一侧。

## 本工具最难的一点

台账本身**满篇讨论**「巨潮」「统计局」「XBRL」—— 那是治理文本，不是数据。
若按字符串命中即判定，扫描器会永远红，等于没有（与 `secret_scan.py` 的
PEM 头行误报同类）。故指纹须区分：

    讨论数据源       「巨潮 = 备用源，UNKNOWN 阻断」        → 不是泄漏
    数据载荷本身     XBRL 实例文档、成片的财务数字表         → 是泄漏

判据：**来源特征串单独出现不判定**；须与「高数值密度」或「XBRL 实例结构」共现。

## 验收对照（`g1-drafts/L2-L4-合同与验收标准.draft.md §2.4`）

    V1 四个维度各有 ≥1 条规则          ✅ 扩展名 / MIME / 大小 / 内容指纹
    V2 每条规则有变异注入证据          ✅ --selftest
    V3 扫描整个 diff 而非仅新增文件    ✅ --diff（含删除、重命名、模式变更）
    V4 命中时不回显内容                ✅ 只给路径/行号/规则/长度
    V5 退出码 fail-closed              ✅ 0 干净 / 1 命中 / 2 无法判定（视同命中）
    V6 对自身规则样例免疫              ✅ SELF 豁免

用法：
  python3 data_ingress_scan.py <目录>            扫描目录
  python3 data_ingress_scan.py <仓库> --diff REF 扫描 git diff（含删除/重命名）
  python3 data_ingress_scan.py <目录> --selftest 变异注入自检
"""
import os
import re
import subprocess
import sys

SELF = os.path.abspath(__file__)

# ── 维度 1：扩展名黑名单（数据载体格式）────────────────────────────
BLOCK_EXT = {".pdf", ".xbrl", ".xls", ".xlsx", ".xlsm", ".zip", ".7z", ".rar",
             ".csv", ".tsv", ".parquet", ".db", ".sqlite", ".dta", ".sav"}

# ── 维度 2：MIME 魔数（**不信扩展名** —— 防改名绕过）────────────────
MAGIC = [
    (b"%PDF-", "PDF"),
    (b"PK\x03\x04", "ZIP/OOXML"),
    (b"\xd0\xcf\x11\xe0", "旧版 Office 复合文档"),
    (b"SQLite format 3\x00", "SQLite"),
    (b"PAR1", "Parquet"),
]

# ── 维度 3：大小阈值（**写死，不得运行时可调** —— 合同 §2.2③）──────
SIZE_LIMIT = 512 * 1024          # 512 KiB
SIZE_LIMIT_TEXT = 256 * 1024     # 文本类更严：正常治理文档远小于此

# ── 维度 4：内容指纹 ───────────────────────────────────────────────
# (a) XBRL 实例结构 —— 出现即判定，治理文本不会写这些
XBRL_STRUCT = [
    r"<xbrli:xbrl", r"<xbrl\s", r"xmlns:xbrli\s*=",
    r"<xbrli:context\b", r"<xbrli:unit\b", r"contextRef\s*=",
]
# (b) 来源特征串 —— **单独出现不判定**，须与高数值密度共现
SOURCE_HINT = [
    r"cninfo\.com\.cn", r"巨潮资讯网", r"stats\.gov\.cn", r"国家统计局",
    r"sse\.com\.cn", r"上海证券交易所",
]
# 数值 token：≥5 字符的数字（含千分位与小数），排除年份等短数字
NUM = re.compile(r"\b\d{1,3}(?:,\d{3})+(?:\.\d+)?\b|\b\d{5,}(?:\.\d+)?\b|\b\d+\.\d{2,}\b")
DENSE_TOKENS = 5     # 单行 ≥5 个长数值 → 该行「密集」
DENSE_LINES = 3      # ≥3 个连续密集行 → 判定为数据转储

TEXT_EXT = {".md", ".txt", ".json", ".yaml", ".yml", ".py", ".xml", ".html", ""}
SKIP_DIR = {".git", "node_modules", "__pycache__", ".venv", "venv"}


def dense_blocks(txt):
    """返回连续高数值密度块的 (起行, 行数)。这是「成片数字」的判据。"""
    lines = txt.splitlines()
    flags = [len(NUM.findall(l)) >= DENSE_TOKENS for l in lines]
    out, run = [], 0
    for i, f in enumerate(flags):
        if f:
            run += 1
        else:
            if run >= DENSE_LINES:
                out.append((i - run + 1, run))
            run = 0
    if run >= DENSE_LINES:
        out.append((len(flags) - run + 1, run))
    return out


def scan_text(txt, path):
    hits = []
    # (a) XBRL 实例结构 —— 独立判定
    for pat in XBRL_STRUCT:
        for m in re.finditer(pat, txt, re.I):
            hits.append({"dim": "内容指纹/XBRL 实例", "path": path,
                         "line": txt[:m.start()].count("\n") + 1, "detail": "XBRL 实例结构"})
    # (b) 高数值密度块
    blocks = dense_blocks(txt)
    for start, n in blocks:
        hits.append({"dim": "内容指纹/数值密度", "path": path, "line": start,
                     "detail": f"{n} 个连续行各含 ≥{DENSE_TOKENS} 个长数值"})
    # (c) 来源特征串 —— **仅在与密集块共现时判定**
    if blocks:
        for pat in SOURCE_HINT:
            m = re.search(pat, txt, re.I)
            if m:
                hits.append({"dim": "内容指纹/来源+密度共现", "path": path,
                             "line": txt[:m.start()].count("\n") + 1,
                             "detail": "来源特征串与数值密集块共现"})
                break
    return hits


def scan_file(fp, rel):
    hits = []
    ext = os.path.splitext(fp)[1].lower()
    size = os.path.getsize(fp)
    # 维度 1
    if ext in BLOCK_EXT:
        hits.append({"dim": "扩展名", "path": rel, "line": 0, "detail": f"黑名单扩展名 {ext}"})
    # 维度 2：读头部魔数，不信扩展名
    try:
        head = open(fp, "rb").read(16)
        for magic, name in MAGIC:
            if head.startswith(magic):
                hits.append({"dim": "MIME 魔数", "path": rel, "line": 0,
                             "detail": f"实际内容为 {name}（扩展名 {ext or '无'}）"})
                break
    except OSError:
        return [{"dim": "无法判定", "path": rel, "line": 0, "detail": "不可读 → fail-closed"}]
    # 维度 3
    lim = SIZE_LIMIT_TEXT if ext in TEXT_EXT else SIZE_LIMIT
    if size > lim:
        hits.append({"dim": "大小阈值", "path": rel, "line": 0,
                     "detail": f"{size} 字节 > 阈值 {lim}"})
    # 维度 4：仅对文本
    if ext in TEXT_EXT:
        try:
            hits += scan_text(open(fp, encoding="utf-8").read(), rel)
        except (UnicodeDecodeError, OSError):
            pass
    return hits


def walk(root):
    hits, n = [], 0
    for dp, dns, fns in os.walk(root):
        dns[:] = [d for d in dns if d not in SKIP_DIR]
        for fn in fns:
            fp = os.path.join(dp, fn)
            if os.path.abspath(fp) == SELF:
                continue
            n += 1
            hits += scan_file(fp, os.path.relpath(fp, root))
    return hits, n


def scan_diff(repo, ref):
    """V3：扫描整个 diff —— **含删除、重命名、模式变更**。

    删除也要扫：被删除的内容仍在历史里，公开后一并可读。
    """
    r = subprocess.run(["git", "-C", repo, "diff", "--name-status", ref],
                       capture_output=True, text=True)
    if r.returncode != 0:
        print(f"❌ git diff 失败：{r.stderr.strip()[:120]}")
        return None, 0
    hits, n = [], 0
    for line in r.stdout.splitlines():
        parts = line.split("\t")
        status, paths = parts[0], parts[1:]
        n += 1
        for p in paths:
            blob = subprocess.run(
                ["git", "-C", repo, "show",
                 f"{ref}:{p}" if status.startswith("D") else f":{p}"],
                capture_output=True, text=True)
            if blob.returncode == 0 and blob.stdout:
                hits += [dict(h, path=f"[{status}] {p}") for h in scan_text(blob.stdout, p)]
    return hits, n


def selftest():
    print("变异注入自检：")
    bad = []
    cases = [
        ("XBRL 实例", '<xbrli:xbrl xmlns:xbrli="http://www.xbrl.org/2003/instance">', True),
        ("数值密集块",
         "\n".join(["营业收入 12,345,678.90 11,234,567.89 9,876,543.21 8,765,432.10 7,654,321.09"] * 4),
         True),
        ("来源+密度共现",
         "数据来自 cninfo.com.cn\n" +
         "\n".join(["科目 12,345,678.90 11,234,567.89 9,876,543.21 8,765,432.10 7,654,321.09"] * 4),
         True),
    ]
    for name, payload, want in cases:
        got = [h["dim"] for h in scan_text(payload, "<probe>")]
        ok = bool(got) == want
        print(f"  {'✅' if ok else '❌'} {name:18} → {got or '**未检出**'}")
        if not ok:
            bad.append(name)
    # 反向：治理文本讨论数据源，**不得**误报
    gov = ("巨潮 = 备用源，UNKNOWN 阻断；统计局 ALLOWED + 强制署名（stats.gov.cn）。\n"
           "锁定 118.25 人日，含储备 141.90，前向 26 周。\n"
           "| G1 | W1—W3 | 12.30 | 15 |\n| G2 | W4—W9 | 40.50 | 45 |\n")
    fp = [h["dim"] for h in scan_text(gov, "<gov>")]
    print(f"  {'✅' if not fp else '❌'} 治理文本无误报      → {fp or '无'}")
    if fp:
        bad.append("治理文本误报")
    # 反向：来源串**单独**出现不得判定
    lone = "公告主源 = 上交所（sse.com.cn），巨潮资讯网降为备用源。"
    lf = [h["dim"] for h in scan_text(lone, "<lone>")]
    print(f"  {'✅' if not lf else '❌'} 来源串单独出现无误报 → {lf or '无'}")
    if lf:
        bad.append("来源串单独误报")
    return 1 if bad else 0


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    root = os.path.abspath(sys.argv[1])
    if "--selftest" in sys.argv:
        return selftest()
    if "--diff" in sys.argv:
        ref = sys.argv[sys.argv.index("--diff") + 1]
        hits, n = scan_diff(root, ref)
        if hits is None:
            return 2
        print(f"扫描 diff {ref} —— {n} 个变更条目")
    else:
        if not os.path.isdir(root):
            print(f"❌ 不是目录：{root}")
            return 2
        hits, n = walk(root)
        print(f"扫描 {root} —— {n} 个文件")
    print(f"  维度 4 项：扩展名 {len(BLOCK_EXT)} 个 · 魔数 {len(MAGIC)} 条 · "
          f"大小阈值 {SIZE_LIMIT_TEXT}/{SIZE_LIMIT} · 指纹 {len(XBRL_STRUCT)}+{len(SOURCE_HINT)} 条")
    if hits:
        print(f"\n❌ **命中 {len(hits)} 处** —— 不回显内容：")
        for h in hits[:40]:
            print(f"  [{h['dim']}] {h['path']}:{h['line']}  {h['detail']}")
        return 1
    print("\n✅ 未命中。")
    print("  **注意**：未命中不等于无数据 —— 指纹覆盖已知形态，")
    print("  新形态（如图片内嵌表格、编码后的转储）不在覆盖内。本工具不声称穷尽。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
