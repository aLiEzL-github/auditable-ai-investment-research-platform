"""写权断言的**覆盖守卫**（OI-PF-183 / OI-PF-184）。

单点修复挡不住下一次：`cas_insert` 之所以成为洞，不是因为有人决定它不需要
断言，而是因为**没有任何东西要求每条写路径都接断言**。四个方法接了，
第五个没接，测试全绿。

本套件把这条要求变成机检：

  X-1  repository.py 内**每个**写库方法都须调用 assert_writer（默认拒绝：
       新增写方法而未接入即判红）
  X-2  变异注入：造一个不接断言的写方法 → X-1 的判据须判红
       （**用原缺陷形态** —— cas_insert 修复前的函数体，规则 ⑩）
  X-3  _OBJ_TYPE 须覆盖全部 ORM 模型，且每个值都在 contracts/writers.json 内
  X-4  四个受控写法的 writer 参数**不得有缺省值**
       （缺省值恰为合法值 = 断言只能挡住主动自称非法的调用方，OI-PF-184）
  X-5  assert_writer 的 context 实参内不得出现字面 True
       （前置须由实际校验结果填入，不得硬编码）
"""
import ast
import json
import os
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_APP = os.path.join(_HERE, "..", "app")
sys.path.insert(0, _APP)

REPO_PY = os.path.join(_APP, "repository.py")
WRITERS = os.path.join(_HERE, "..", "..", "contracts", "writers.json")

_ORM_WRITES = ("add", "merge", "delete")


def _module_str_consts(tree: ast.Module) -> dict:
    """模块级「名字 → 字符串元组/列表」常量。"""
    out = {}
    for n in tree.body:
        if (isinstance(n, ast.Assign) and len(n.targets) == 1
                and isinstance(n.targets[0], ast.Name)
                and isinstance(n.value, (ast.Tuple, ast.List))
                and n.value.elts
                and all(isinstance(e, ast.Constant) and isinstance(e.value, str)
                        for e in n.value.elts)):
            out[n.targets[0].id] = [e.value for e in n.value.elts]
    return out


def _pragma_names(fn: ast.AST, consts: dict) -> set:
    """函数内由「for x in <全 PRAGMA 的模块常量>」绑定的循环变量名。"""
    names = set()
    for n in ast.walk(fn):
        if (isinstance(n, ast.For) and isinstance(n.target, ast.Name)
                and isinstance(n.iter, ast.Name)):
            vals = consts.get(n.iter.id)
            if vals and all(v.strip().upper().startswith("PRAGMA") for v in vals):
                names.add(n.target.id)
    return names


def _is_pragma_arg(a: ast.AST, pragma_names: set) -> bool:
    if isinstance(a, ast.Call) and isinstance(a.func, ast.Name) and a.func.id == "text":
        return bool(a.args) and _is_pragma_arg(a.args[0], pragma_names)
    if isinstance(a, ast.Name):
        return a.id in pragma_names
    if isinstance(a, ast.Constant) and isinstance(a.value, str):
        return a.value.strip().upper().startswith("PRAGMA")
    return False


def _writes(node: ast.AST, consts: dict) -> bool:
    """函数体内是否出现库写操作。

    **判据盯的是写操作本身，不是接收方的变量名。** 初版写成
    `isinstance(v, ast.Name) and v.id in ("session", "s", "sess")` ——
    那是「匹配代理而非目标」：把 `session` 改名叫 `db` 就绕过了整条守卫，
    而这正是本轮（以及本项目十余次）反复审出的形状。

    `.execute()` 例外按**结构**判定，不按函数名白名单：实参若来自
    「遍历一个全部以 PRAGMA 开头的模块常量」，则是连接配置而非对象写入。
    判不出来的一律当作写（默认拒绝方向）。
    """
    pn = _pragma_names(node, consts)
    for n in ast.walk(node):
        if not (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)):
            continue
        if n.func.attr in _ORM_WRITES:
            return True
        if n.func.attr == "execute":
            if not (n.args and all(_is_pragma_arg(a, pn) for a in n.args)):
                return True
    return False


def _asserts_writer(node: ast.AST) -> bool:
    for n in ast.walk(node):
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) \
                and n.func.id == "assert_writer":
            return True
    return False


def _uncovered(src: str):
    """返回 (方法名, 行号) 列表：写库但未断言写者的方法。"""
    tree = ast.parse(src)
    consts = _module_str_consts(tree)
    out = []
    for n in ast.walk(tree):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if _writes(n, consts) and not _asserts_writer(n):
                out.append((n.name, n.lineno))
    return out


class TestWriteGuardCoverage(unittest.TestCase):

    def setUp(self):
        with open(REPO_PY, encoding="utf-8") as f:
            self.src = f.read()

    # ── X-1 ─────────────────────────────────────────────────────────
    def test_every_write_method_asserts_writer(self):
        bad = _uncovered(self.src)
        self.assertEqual(
            bad, [],
            "以下写库方法未调用 assert_writer —— 每一个都是一条绕过写权矩阵的路："
            + "; ".join(f"{n}():{ln}" for n, ln in bad))

    # ── X-2 变异注入（原缺陷形态）────────────────────────────────────
    def test_guard_goes_red_on_unguarded_write(self):
        """把 cas_insert 修复前的函数体注入回去，X-1 的判据必须判红。

        只测「能变绿」的守卫，等于没有守卫 —— 一个永远绿的判据会让
        X-1 在真的出现新洞时同样保持绿。
        """
        mutant = self.src + (
            "\n\n"
            "class _MutantRepo:\n"
            "    def cas_insert_original(self, session, obj):\n"
            "        '''OI-PF-183 修复前的原形态：无写者参数、无类型判据。'''\n"
            "        session.add(obj)\n"
            "        session.commit()\n"
            "        return obj\n")
        bad = _uncovered(mutant)
        self.assertIn("cas_insert_original", [n for n, _ in bad],
                      "变异体未被判红 —— 判据无效")
        # 防误红：未变异的原文须仍然是干净的
        self.assertEqual(_uncovered(self.src), [], "原文不应被判红")

    def test_guard_not_fooled_by_receiver_rename(self):
        """把 `session` 改名叫 `db` —— 判据仍须判红。

        本判据的初版限定接收方变量名为 session/s/sess，**改个名字就绕过了整条
        守卫**。那是「匹配代理而非目标」，与本轮审出的 OI-PF-183/184 同形，
        也是这个项目里反复出现的那一个形状。此用例把它钉死。
        """
        mutant = self.src + (
            "\n\n"
            "class _RenamedRepo:\n"
            "    def sneaky_write(self, db, obj):\n"
            "        db.add(obj)\n"
            "        db.commit()\n")
        self.assertIn("sneaky_write", [n for n, _ in _uncovered(mutant)],
                      "接收方改名后未被判红 —— 判据仍在匹配变量名而非写操作")

    def test_pragma_helpers_are_not_false_positives(self):
        """连接配置（PRAGMA）不得被判为对象写入 —— 防误红。

        排除按**结构**判定（实参来自遍历全 PRAGMA 的模块常量），
        不是按函数名开白名单：白名单会随下一个新函数失效，且它奖励
        「把写操作塞进已豁免的函数里」。
        """
        clean = [n for n, _ in _uncovered(self.src)]
        for fn in ("_set_sqlite_pragma", "_apply_sqlite_pragmas"):
            self.assertNotIn(fn, clean, f"{fn} 是 PRAGMA 配置，不应被判为写库")
        # 反向：把一个真写操作塞进 PRAGMA 函数里，须判红
        mutant = self.src.replace(
            "            for pragma in WAL_PRAGMAS:\n"
            "                conn.execute(text(pragma))",
            "            for pragma in WAL_PRAGMAS:\n"
            "                conn.execute(text(pragma))\n"
            "            conn.execute('INSERT INTO claim VALUES (1)')", 1)
        self.assertNotEqual(mutant, self.src, "变异未生效 —— 目标代码已变")
        self.assertIn("_apply_sqlite_pragmas", [n for n, _ in _uncovered(mutant)],
                      "PRAGMA 函数内混入真写操作时未判红 —— 豁免过宽")

    # ── X-3 类型表覆盖 ──────────────────────────────────────────────
    def test_obj_type_table_covers_all_models(self):
        import repository as R
        models = {c for c in vars(R).values()
                  if isinstance(c, type) and hasattr(c, "__tablename__")}
        missing = sorted(c.__name__ for c in models if c not in R._OBJ_TYPE)
        self.assertEqual(
            missing, [],
            f"以下 ORM 模型不在 _OBJ_TYPE 内：{missing} —— "
            f"cas_insert 会默认拒绝它们，但登记缺失本身须先被看见")
        with open(WRITERS, encoding="utf-8") as f:
            matrix = json.load(f)["matrix"]
        unknown = sorted(v for v in R._OBJ_TYPE.values() if v not in matrix)
        self.assertEqual(unknown, [],
                         f"_OBJ_TYPE 指向 writers.json 中不存在的对象：{unknown}")

    # ── X-4 writer 不得有缺省值 ─────────────────────────────────────
    def test_writer_param_has_no_default(self):
        tree = ast.parse(self.src)
        offenders = []
        for n in ast.walk(tree):
            if not isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if not _asserts_writer(n):
                continue
            args = n.args.args[len(n.args.args) - len(n.args.defaults):] \
                if n.args.defaults else []
            for a, d in zip(args, n.args.defaults):
                if a.arg == "writer":
                    offenders.append((n.name, ast.unparse(d)))
        self.assertEqual(
            offenders, [],
            "writer 带缺省值的方法："
            + "; ".join(f"{n}(writer={d})" for n, d in offenders)
            + " —— 缺省值恰为契约白名单的合法值时，断言只能挡住"
              "「主动自称非法写者」的调用方（OI-PF-184）")

    # ── X-5 前置实参不得硬编码字面 True ─────────────────────────────
    def test_precondition_context_has_no_literal_true(self):
        tree = ast.parse(self.src)
        offenders = []
        for n in ast.walk(tree):
            if not (isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
                    and n.func.id == "assert_writer"):
                continue
            for arg in list(n.args) + [kw.value for kw in n.keywords]:
                if not isinstance(arg, ast.Dict):
                    continue
                for k, v in zip(arg.keys, arg.values):
                    if isinstance(v, ast.Constant) and v.value is True:
                        key = k.value if isinstance(k, ast.Constant) else "?"
                        offenders.append(f"{key}=True@L{n.lineno}")
        self.assertEqual(
            offenders, [],
            f"assert_writer 的 context 内有硬编码 True：{offenders} —— "
            f"MACHINE 前置须由实际校验结果填入（OI-PF-184 ②）")


if __name__ == "__main__":
    unittest.main()
