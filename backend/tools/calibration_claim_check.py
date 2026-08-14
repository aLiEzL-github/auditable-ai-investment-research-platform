#!/usr/bin/env python3
"""calibration_claim_check.py —— G6C H-8 表述守卫（先红后绿，CI 接线）。

基线（G6C-03）+ G6C-执行计划.md H-8：
  「CALIBRATION_PENDING 不得冒充能力」是一票否决项 —— 任何声称
  「已校准」「校准通过」「误差已验证」的表述须 FAIL。

检查范围（规则 ⑮：范围明确列出并可机检）：
  ① 行为断言：用 fixture 数据跑 calibration.check_sufficiency +
     render_for_display，断言
       · declared_status == CALIBRATION_PENDING（VD-26 终态）
       · 渲染文本不含任何 CLAIM_PHRASES 冒充表述
     —— 先红后绿：植入一个冒充表述（如把渲染文本改成含「已校准」）
        守卫必须 FAIL（变异注入证据见 backend/tests/test_calibration_guard.py）
  ② 生产代码静态扫描：backend/app/**/*.py 的字符串字面量（ast，
     不含注释/docstring）中出现 CLAIM_PHRASES 即 FAIL —— 默认拒绝，
     无「清单外漏网」问题（㉝）。
     唯一豁免 = 拒绝语境：字面量含 "E-G6C-03"（本模块自己的拒绝消息）——
     这是语义规则（含错误码的消息是拒绝而非声称），不是穷举清单。
     本守卫自身在 backend/tools/，不在扫描范围（backend/app 之外）。

用法：python3 backend/tools/calibration_claim_check.py [repo_root]
"""
import ast
import os
import sys

ROOT = os.path.abspath(sys.argv[1]) if len(sys.argv) > 1 else "."
APP_DIR = os.path.join(ROOT, "backend", "app")
SELF = os.path.basename(__file__)

CLAIM_PHRASES = ("已校准", "校准通过", "误差已验证")


def _scan_app_literals() -> list:
    """扫描 backend/app 全部 .py 的字符串字面量；报检查对象数。

    排除规则（均为结构规则，非穷举清单 —— ㉝）：
      · 拒绝语境：字面量含 "E-G6C-03"（错误码 = 拒绝消息而非声称）
      · docstring（模块/类/函数的首条语句 —— 文档不是输出）
      · CLAIM_PHRASES 的定义本体（守卫语料自身，须含被禁短语才能工作）
    """
    bad, checked = [], 0
    for dp, _dn, fs in os.walk(APP_DIR):
        for fn in sorted(fs):
            if not fn.endswith(".py"):
                continue
            rel = os.path.relpath(os.path.join(dp, fn), ROOT)
            with open(os.path.join(dp, fn), encoding="utf-8",
                      errors="replace") as f:
                src = f.read()
            try:
                tree = ast.parse(src)
            except SyntaxError as e:
                bad.append(f"{rel}: 解析失败 {e}")
                continue
            docstring_nodes = {ast.get_docstring(n, clean=False)
                               for n in ast.walk(tree)
                               if isinstance(n, (ast.Module, ast.FunctionDef,
                                                 ast.AsyncFunctionDef,
                                                 ast.ClassDef))}
            corpus_literals = set()
            for node in ast.walk(tree):
                if (isinstance(node, ast.Assign)
                        and any(isinstance(t, ast.Name)
                                and t.id == "CLAIM_PHRASES"
                                for t in node.targets)
                        and isinstance(node.value, (ast.Tuple, ast.List))):
                    for elt in node.value.elts:
                        if isinstance(elt, ast.Constant) and isinstance(
                                elt.value, str):
                            corpus_literals.add(elt.value)
            for node in ast.walk(tree):
                if not isinstance(node, ast.Constant) or not isinstance(
                        node.value, str):
                    continue
                checked += 1
                if node.value in docstring_nodes:
                    continue
                if node.value in corpus_literals:
                    continue
                for ph in CLAIM_PHRASES:
                    if ph in node.value:
                        if "E-G6C-03" in node.value:
                            continue
                        bad.append(
                            f"{rel}: 生产代码字符串字面量含冒充能力表述"
                            f"「{ph}」—— {node.value[:40]!r}（H-8，默认拒绝）")
    return bad, checked


def _behavioral_assert() -> list:
    """行为断言：fixture 状态的渲染文本不得含冒充表述。"""
    sys.path.insert(0, APP_DIR)
    from calibration import (  # noqa: E402
        CLAIM_PHRASES as _ph, CALIBRATION_PENDING, check_sufficiency,
        render_for_display,
    )
    bad = []
    status = check_sufficiency({}, selective_unresolved=[])
    if status.declared_status != CALIBRATION_PENDING:
        bad.append(f"declared_status = {status.declared_status} ≠ "
                   f"{CALIBRATION_PENDING}（VD-26 终态必须恒 PENDING）")
    text = render_for_display(status)
    for ph in _ph:
        if ph in text:
            bad.append(f"渲染文本含冒充能力表述「{ph}」：{text!r}（H-8）")
    return bad, 1


def main() -> int:
    bad1, checked = _scan_app_literals()
    bad2, _n = _behavioral_assert()
    bad = bad1 + bad2
    if bad:
        for b in bad:
            print(f"❌ {b}", file=sys.stderr)
        print(f"FAILED —— 检查对象 {checked} 个字符串字面量 + 1 次行为断言",
              file=sys.stderr)
        return 1
    print(f"✅ H-8 表述守卫 PASS —— 检查对象 {checked} 个字符串字面量"
          f"（backend/app）+ 1 次行为断言，零冒充表述")
    return 0


if __name__ == "__main__":
    sys.exit(main())
