#!/usr/bin/env python3
"""验收包的实质哈希：**结构分隔**，不用模式清单。

## 为什么换掉模式清单

原做法是一张 `EXCLUDE` 模式清单（14 条），凡行内含其中任一子串即不计入
`substantive`。实测两处失败：

```text
① 不完备 —— 「④ 开放项 37/214」与 CI 步骤明细（docker-build: success …）
   不在清单里，于是六份已签包的 substantive 全部漂移。
   枚举清单不可证完备（规则 ㉝）。

② 已分岔 —— 全仓 9 份 EXCLUDE 定义归并为 **2 种**：
   build_gate3_acceptance.py 只有 9 条，比其余 8 份少
   `_mut-` · `sparseimage` · `备份目录 = ` · `g1-08-2026` · `  g1-08-`。
   即 Gate3 的自声明 substantive 与审计的重算口径**不同**。
```

## 新做法

```text
实时读数圈进  <!-- LIVE-BEGIN --> … <!-- LIVE-END -->
substantive 剔除**每一对**标记之间的内容（可有多块，因为活读数是散布的）

⇒ 默认「一切都算数」，只有显式圈出的活块不算 —— 与枚举清单方向相反。
  漏圈一处活读数 → 包不可复现 → 立刻暴露；
  而漏写一条模式 → 静默漂移，要到签署失效才发现。
```

`substantive_sha256 = …` 那一行本身也剔除 —— 它是哈希的产物，不能参与
自己的计算。

## 判据只此一份

生成器（10）· 台账审计 `T1`/`A6` · `acceptance_fixpoint._subs()` 全部 import 本模块。
此前它们各算一遍，已经分岔（见上）。
"""
import hashlib
import re

LIVE_BEGIN = "<!-- LIVE-BEGIN -->"
LIVE_END = "<!-- LIVE-END -->"

# substantive 自声明行：哈希的产物不能参与自己的计算。
#
# **连同行尾换行一并剔除**，且 strip_live 末尾 rstrip("\n")。这两处不是洁癖：
# 生成器算的是 `substantive("\n".join(L))`（写盘**前**，此时还没有自声明行），
# 而审计 T1 是对**整个文件**就地重算。文件 = join(L) + "\nsubstantive_sha256 = X\n"。
# 若只删行文本、留下两侧换行，文件侧就比生成器侧多出 '\n\n' ——
# **自声明值与就地重算值永远对不上，T1 恒红**，签署锚定的哈希无从校验。
#
# 旧的模式清单方案没有这个问题：它是逐行过滤，file.splitlines() 后滤掉自声明行
# 得到的正是 L，两侧天然相等。改成正则替换时把这条性质弄丢了。
# 2026-08-17 在 S-4 取第一份签署哈希时才发现 —— S-1/S-2 验的是「两次生成是否
# 一致」，两次都用生成器的口径，**永远自洽**，测不出跨口径的这条。
_SELF_DECL = re.compile(r"^substantive_sha256\s*=.*$\n?", re.M)

_BLOCK = re.compile(
    re.escape(LIVE_BEGIN) + r".*?" + re.escape(LIVE_END),
    re.S)


def strip_live(text: str) -> str:
    """剔除全部活块与 substantive 自声明行，返回参与哈希的正文。

    末尾 `rstrip("\\n")` 使**生成器输入**（写盘前的 `"\\n".join(L)`）与
    **文件内容**（其后追加了自声明行）归一到同一个串 —— 见 `_SELF_DECL` 的说明。
    代价：仅在文件末尾增删空行不改变 substantive。那是无语义的改动，接受。
    """
    return _SELF_DECL.sub("", _BLOCK.sub("", text)).rstrip("\n")


def declared(text: str):
    """取文件里自声明的 substantive_sha256；无该行返回 None。"""
    m = re.search(r"^substantive_sha256\s*=\s*([0-9a-f]{64})\s*$", text, re.M)
    return m.group(1) if m else None


def selfdecl_mismatch(text: str):
    """自声明值 ≠ 就地重算值即返回说明，None = 相符（或本就无自声明行）。

    **这是本轮漏掉的那个测量点。** 签署记录绑定的是自声明值，而 `T1` 校验的是
    就地重算值；两者若不同口径，签了也永远校验不过。
    S-1/S-2 只比过「两次生成是否一致」—— 同一口径自比，测不出这条。
    """
    d = declared(text)
    if d is None:
        return None
    got = substantive(text)
    if d != got:
        return (f"自声明 substantive {d[:12]}… ≠ 就地重算 {got[:12]}… —— "
                f"签署锚定的哈希与 T1 的校验口径不同，签了也校验不过")
    return None


def substantive(text: str) -> str:
    """实质哈希 —— 对剔除活块后的正文取 sha256。"""
    return hashlib.sha256(strip_live(text).encode("utf-8")).hexdigest()


def unbalanced_markers(text: str):
    """标记是否配对。返回说明字符串，None = 配对正确。

    **不配对即拒**：`BEGIN` 多于 `END` 会让 `_BLOCK` 少剔一块（漏剔 → 假漂移），
    `END` 多于 `BEGIN` 说明有裸标记。二者都须暴露而非静默。
    """
    nb, ne = text.count(LIVE_BEGIN), text.count(LIVE_END)
    if nb != ne:
        return f"活块标记不配对：BEGIN {nb} 个 / END {ne} 个"
    # 交错检查：BEGIN 与 END 必须严格交替出现
    seq = [m.group(0) for m in re.finditer(
        re.escape(LIVE_BEGIN) + "|" + re.escape(LIVE_END), text)]
    for i, tok in enumerate(seq):
        if tok != (LIVE_BEGIN if i % 2 == 0 else LIVE_END):
            return f"活块标记交错错误：第 {i + 1} 个标记为 {tok}"
    return None


# ── 不得低于旧覆盖（MR-2）────────────────────────────────────────────
# 旧 EXCLUDE 的 14 条模式。**不是判据本体** —— 它已被证明不完备（见上）。
# 它在这里只作**下界**：结构迁移至少要覆盖住旧方案覆盖过的东西。
#
# 这条下界是有来历的：本次迁移第一版只圈了「生成时刻」一处，其余 13 条全丢，
# 结果给台账审计加一条守卫（74→75）就让 Gate3 的 substantive 漂了 ——
# 与 OI-PF-118 当年要修的**是同一个失效**。当时靠一次扰动实验才发现，
# 靠读代码没发现。故把它固化成判据。
_LEGACY_VOLATILE = (
    "生成时刻", "实测时刻", "main 最新 CI run", "run = ", "ruleset: ",
    "g1-08-2026", "_mut-", "sparseimage", "备份目录 = ", "  g1-08-",
    "合计", "独立审计:", "v2.0 基线:",
)
# `substantive_sha256` 不在此列 —— 它由 _SELF_DECL 单独剔除。

# 旧清单**没**覆盖、但实测证明会漂的读数。加进来是收紧（MR-1 严格者胜）。
#
# 「开放项总计 N / M」是全局计数 —— 登记册任何一处变动都会改它，与本 Gate 无关。
# 它正是当初漏网的那条：漂移分析里「④ 开放项 N/M 与 CI 步骤明细不在清单里」
# 就是六份包 substantive 全漂的直接原因之一。
# 2026-08-17 闭合 OI-PF-022/026 时再次撞到：闭掉两项就让 Gate0 的这一行由
# 37/214 变成 35/214。
#
# **范围内**计数不在此列 —— 那是断言（「本 Gate 范围内材料性开放项 0 项」），
# 变了就该让签名失效。区分读数与断言，正是结构分隔比模式清单强的地方。
_MEASURED_VOLATILE = (
    "开放项总计",
)

# **没有豁免名单。**
# 起草时曾给「结论       = 」开一条豁免，理由是「判定本体须签」——
# 那是把正要修的缺陷豁免掉了：Gate6 的结论行里嵌着 `合计 75 项：PASS 71 / FAIL 4`，
# 加一条审计守卫就会改写它。判定该签是对的，**做法是把明细拆出去**，
# 不是给整行发豁免。豁免名单一旦开口，下一处漏也会走同一个口子。


def legacy_leaks(text: str):
    """活块**外**仍命中旧 13 条模式的行 —— 返回 [(模式, 行)]，空表 = 未降级。

    用途是防「结构迁移看起来做了、实际只做了一处」。
    """
    out = []
    for ln in strip_live(text).splitlines():
        for pat in _LEGACY_VOLATILE + _MEASURED_VOLATILE:
            if pat in ln:
                out.append((pat, ln.strip()))
                break
    return out


def live_lines(text: str):
    """返回活块内的行 —— 供守卫核对「该圈的都圈了」。"""
    out = []
    for m in _BLOCK.finditer(text):
        out.extend(m.group(0).splitlines()[1:-1])
    return out
