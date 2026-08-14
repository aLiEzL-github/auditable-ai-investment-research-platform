#!/usr/bin/env python3
"""未读取形参守卫（`OI-PF-175` / `188` / `189` / `190`）。

**形参未被读取有两种，后果差别很大**：

```text
① 签名多了个不用的参数     —— 读签名会误解，但传什么都不影响结果
   例：update_diff(store, …)（OI-PF-175）· cas_update(session, …)（OI-PF-190）
② **调用方的意图被吞掉**   —— 传了控制值，函数照旧走原路，且不报错不警告
   例：evaluate(constants_override=…)（OI-PF-188）—— 实测传 0.99 与不传，
       输出逐字相同（250，应为 990），**连 inputs_hash 都一样**，
       落进 CalcLedger 后事后无从分辨哪一次带了 override
```

② 比 ① 严重：有人以为在做敏感性分析，实际算的是同一个数，而账本上看不出区别。

判据（**默认拒绝**）：

  U-1  `backend/app` 内任何未读取形参，须在
       `contracts/unused_param_exemptions.json` 里**显式豁免并写明理由**
  U-2  豁免的理由不得为空
  U-3  **死豁免判红** —— 豁免项已不适用（形参改名 / 函数没了 / 参数开始被用）
       时留着，会让下一个真缺陷藏在它后面。本项目已有先例（SERVER_ALLOWLIST 死豁免）

`_` 前缀的形参按 Python 惯例视为有意不用，不计入。

用法：python3 backend/tools/unused_param_check.py [repo_root]
"""
import ast
import json
import os
import sys

SKIP_SELF = ("self", "cls")


def unused_params(fn: ast.AST):
    args = [a.arg for a in fn.args.args] + [a.arg for a in fn.args.kwonlyargs]
    args = [a for a in args if a not in SKIP_SELF and not a.startswith("_")]
    if not args:
        return []
    used = set()
    for x in ast.walk(fn):
        if isinstance(x, ast.Name) and isinstance(x.ctx, ast.Load):
            used.add(x.id)
        elif isinstance(x, ast.Attribute):
            b = x
            while isinstance(b, ast.Attribute):
                b = b.value
            if isinstance(b, ast.Name):
                used.add(b.id)
    return [a for a in args if a not in used]


def scan(app_dir):
    found = set()
    for fn in sorted(os.listdir(app_dir)):
        if not fn.endswith(".py"):
            continue
        try:
            tree = ast.parse(open(os.path.join(app_dir, fn), encoding="utf-8").read())
        except SyntaxError as e:
            raise SystemExit(f"E-UP-000: {fn} 语法错误，无法扫描：{e}")
        for n in ast.walk(tree):
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for a in unused_params(n):
                    found.add(f"{fn}:{n.name}:{a}")
    return found


def main() -> int:
    root = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else ".")
    app = os.path.join(root, "backend", "app")
    cfg = os.path.join(root, "contracts", "unused_param_exemptions.json")
    if not os.path.isdir(app):
        print(f"❌ 找不到 {app} —— 判红而非默认放行")
        return 1
    if not os.path.exists(cfg):
        print(f"❌ 找不到豁免契约 {cfg} —— 无豁免依据即全部判红（默认拒绝）")
        return 1
    ex = json.load(open(cfg, encoding="utf-8"))["exemptions"]
    found = scan(app)

    bad = []
    for k in sorted(found):                                   # U-1
        if k not in ex:
            bad.append(f"U-1: {k} 形参未被读取且**未在契约内豁免** —— "
                       f"若是协议一致性（框架回调签名）请写明理由；"
                       f"若是调用方意图被吞掉（如 OI-PF-188 的 "
                       f"constants_override），请**去掉形参或实现它**，"
                       f"加豁免了事等于把缺陷合法化")
    for k, reason in sorted(ex.items()):                      # U-2
        if not str(reason or "").strip():
            bad.append(f"U-2: 豁免 {k} 无理由")
    for k in sorted(set(ex) - found):                         # U-3
        bad.append(f"U-3: **死豁免** {k} —— 该形参已不再是「未读取」"
                   f"（改名 / 函数已删 / 参数已被使用）。留着会让下一个真缺陷"
                   f"藏在它后面（本项目先例：SERVER_ALLOWLIST 死豁免）")

    if bad:
        print("❌ 未读取形参违规（OI-PF-175/188/189/190）：")
        for b in bad[:20]:
            print("  - " + b)
        return 1
    print(f"✅ 检查对象 {len(found)} 处未读取形参，"
          f"全部在契约内显式豁免且有理由；豁免 {len(ex)} 条无一失效")
    return 0


if __name__ == "__main__":
    sys.exit(main())
