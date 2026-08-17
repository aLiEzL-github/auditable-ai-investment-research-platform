#!/usr/bin/env python3
"""已签对象的写入前置：**目标被 ACTIVE 签署即拒绝写入**。

## 为什么需要它

`A §10.3`：批准后字节变化即失效。而验收包内嵌**实时读数**（`main 最新 CI run`
经 API 实采、审计合计、开放项计数），因此**任何一次重新生成都必然改字节** ——
不是「可能漂移」，是「一跑就漂」。

保护此前只在 `acceptance_fixpoint._signed_objects()` 上：驱动器会跳过已签包，
**而直接运行 `build_gateN_acceptance.py` 没有任何拦截**。十个生成器里
`build_gate1`/`build_gate2/3/4` 有过 `ACTIVE` 字样，但逐条读下来**全是注释**
（「本改动不重新生成该验收包 —— 它已 ACTIVE 签署」），是写给人看的行为约束。

2026-08-17 实测后果：**六份已签验收包全部漂移**，`content_sha256` 无一相符。

## 同一形状的第三次

```text
OI-PF-168  误运行生成器覆盖已签的 Gate1-验收包.md   → 闭合时给的是**行为**教训
OI-PF-187  import 生成器当场重生成 Gate0-验收包.md  → 加了导入屏障（机械）
本次       六份已签包全部被重生成                    → 屏障挡 import，挡不住直接运行
```

`OI-PF-187` 的屏障解决的是「import 等于运行」，**没解决「运行就覆盖已签对象」**。
本模块补的正是后者。

## 判据（默认拒绝）

```text
· 目标文件名出现在任一 ACTIVE gate-record 的 subject.object → 拒绝
· gate-records 目录存在但读不动 → **拒绝**（不能证明未签，就不许写）
· gate-records 目录不存在 → 放行（尚无任何签署）
```

## 例外

重签是合法路径（`ADR-021` 曾因口径变化重签 `G1`/`G2`）。故留一个例外，
但**必须点名到具体文件**：

```bash
ALLOW_REGENERATE_SIGNED=Gate1-验收包.md python3 tools/build_gate1_acceptance.py
```

不接受 `ALLOW_REGENERATE_SIGNED=1` 这类通配 —— 点名才构成「我知道我在重生成
哪一份已签对象」。放行时**向 stderr 打印醒目告警**，使它不会悄悄发生。
"""
import json
import os
import sys

ENV_OVERRIDE = "ALLOW_REGENERATE_SIGNED"


def active_signed_objects(portfolio_root):
    """返回被 ACTIVE 签署锚定的对象文件名集合。

    读不动即抛 —— 由调用方转为拒绝。**不返回空集**：
    空集会被读成「没有已签对象」，那是把「查不了」当成「没有」。
    """
    d = os.path.join(portfolio_root, "gate-records")
    if not os.path.isdir(d):
        return set()                      # 尚无任何签署记录
    out = set()
    for fn in sorted(os.listdir(d)):      # listdir 失败即抛，调用方接住
        if not fn.endswith(".json"):
            continue
        with open(os.path.join(d, fn), encoding="utf-8") as f:
            r = json.load(f)
        if r.get("signature_status") != "ACTIVE":
            continue
        obj = (r.get("subject") or {}).get("object")
        if obj:
            out.add(os.path.basename(obj))
    return out


def refuse_if_signed(portfolio_root, output_path):
    """写盘前置。目标已被 ACTIVE 签署 → SystemExit；否则返回 None。

    调用点须放在**任何写盘动作之前** —— 放在之后等于先破坏再报错。
    """
    target = os.path.basename(output_path)
    try:
        signed = active_signed_objects(portfolio_root)
    except (OSError, ValueError) as e:     # 读不动 / JSON 坏
        raise SystemExit(
            f"E-SIGN-000: 无法读取 {portfolio_root}/gate-records（{type(e).__name__}: {e}）"
            f" —— 不能证明 {target!r} 未被签署，**默认拒绝写入**")

    if target not in signed:
        return None

    allowed = os.environ.get(ENV_OVERRIDE, "")
    if allowed == target:
        print(f"⚠️  {ENV_OVERRIDE}={target} —— 正在**重新生成一个已 ACTIVE 签署的对象**。\n"
              f"    按 A §10.3，字节一变该签署即失效，须重新审核并重签。\n"
              f"    若非有意重签，立刻中止并复原。",
              file=sys.stderr)
        return None

    raise SystemExit(
        f"E-SIGN-001: {target!r} 已被 ACTIVE 签署锚定，拒绝写入。\n"
        f"  验收包内嵌实时读数（CI run / 审计合计 / 开放项计数），"
        f"**任何一次重新生成都必然改字节**；\n"
        f"  而 A §10.3 规定批准后字节变化即失效。\n"
        f"  · 只想看内容 → 读现有文件，不要跑生成器\n"
        f"  · 确需重签   → {ENV_OVERRIDE}={target} 显式点名后重跑，"
        f"并按 ADR-016 走 S1—S5 重新签署")


if __name__ == "__main__":                # 便于人工查询
    root = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("PORTFOLIO_ROOT", "")
    if not root:
        raise SystemExit("用法: signed_object_guard.py <portfolio_root>")
    objs = sorted(active_signed_objects(root))
    print(f"ACTIVE 签署锚定的对象 {len(objs)} 个：")
    for o in objs:
        print(f"  {o}")
