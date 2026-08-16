"""research_data_interdict fixture 半（甲 · A-2a）递归扫描测试。

G7-01 评审发现 6：原工具只扫 fixtures 顶层 .json、只报告一个 fixture。
本测试证明：
  · 嵌套子目录（g7-01/ 等）中的合规合成 fixture 被递归计入
  · 相对路径报告（g7-01/G7-01-*.json 而非裸文件名）
  · 嵌套子目录中的未标记 / 真实 locator fixture 一律判红
  · 符号链接 / 非法 JSON 判红而非跳过（失败关闭）
  · 入仓的三个 G7 fixture 全部被扫且通过

乙半（研究产出禁入）在子进程端到端用例中验证 git 枚举路径不受影响；
子目录 fixture 半是独立于乙半的纯目录扫描。
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest

TOOLS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                     "tools")
sys.path.insert(0, TOOLS)

import research_data_interdict as rdi  # noqa: E402

COMPLIANT = {
    "schema": "g7-01-golden/1.0",
    "SYNTHETIC_FIXTURE": True,
    "case_id": "G7-01-POSITIVE",
    "g7_case": "POSITIVE",
    "source": "synthetic://g7-01/POSITIVE",
    "rules": {"R01": {"locator": "synthetic://g7-01/POSITIVE/r01"}},
    "open_items": [],
}
UNMARKED = {
    "schema": "g7-01-golden/1.0",
    "case_id": "G7-01-NOPE",
    "source": "synthetic://g7-01/NOPE",
    "rules": {},
    "open_items": [],
}
REAL_LOCATOR_FIXTURE = json.loads(json.dumps(
    dict(COMPLIANT, case_id="G7-01-POSITIVE")))
REAL_LOCATOR_FIXTURE["rules"]["R01"]["locator"] = "http://example.com/r.pdf"

# unittest 下 sys.argv 携带测试名，工具模块级 ROOT 计算会失准 ——
# 指向真实仓库 fixtures 目录时显式给出绝对路径。
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
REAL_FIXTURES = os.path.join(REPO_ROOT, "backend", "tests", "fixtures")


def _write_fixtures(root, tree):
    for rel, obj in tree.items():
        fp = os.path.join(root, rel)
        os.makedirs(os.path.dirname(fp), exist_ok=True)
        with open(fp, "w", encoding="utf-8") as fh:
            json.dump(obj, fh, ensure_ascii=False)


class TestFixtureHalfRecursion(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self._old_fixtures = rdi.FIXTURES
        rdi.FIXTURES = os.path.join(self.tmp.name, "backend", "tests",
                                    "fixtures")

    def tearDown(self):
        rdi.FIXTURES = self._old_fixtures

    def test_nested_compliant_fixtures_counted_with_relative_paths(self):
        _write_fixtures(rdi.FIXTURES, {
            "g7-01/G7-01-POSITIVE.json": COMPLIANT,
            "g7-01/G7-01-RESTATEMENT.json": dict(COMPLIANT,
                                                 case_id="G7-01-RESTATEMENT"),
            "g7-01/G7-01-WRONG_BASIS.json": dict(COMPLIANT,
                                                 case_id="G7-01-WRONG_BASIS"),
        })
        bad, checked, exempt = rdi._check_fixtures()
        self.assertEqual(bad, [])
        self.assertEqual(checked, 3)
        self.assertEqual(sorted(exempt), [
            "g7-01/G7-01-POSITIVE.json",
            "g7-01/G7-01-RESTATEMENT.json",
            "g7-01/G7-01-WRONG_BASIS.json",
        ])

    def test_nested_unmarked_fixture_fails_with_relative_path(self):
        _write_fixtures(rdi.FIXTURES, {
            "g7-01/G7-01-POSITIVE.json": COMPLIANT,
            "g7-01/G7-01-BAD.json": UNMARKED,
        })
        bad, checked, exempt = rdi._check_fixtures()
        self.assertEqual(checked, 2)
        self.assertEqual(len(bad), 1)
        self.assertIn("g7-01/G7-01-BAD.json", bad[0])
        self.assertIn("SYNTHETIC_FIXTURE", bad[0])

    def test_nested_real_locator_fixture_fails(self):
        _write_fixtures(rdi.FIXTURES, {
            "g7-01/G7-01-POSITIVE.json": REAL_LOCATOR_FIXTURE,
        })
        bad, checked, exempt = rdi._check_fixtures()
        self.assertEqual(checked, 1)
        self.assertEqual(len(bad), 1)
        self.assertIn("g7-01/G7-01-POSITIVE.json", bad[0])
        self.assertIn("真实形态 locator", bad[0])

    def test_missing_fixtures_dir_is_red(self):
        bad, checked, exempt = rdi._check_fixtures()
        self.assertEqual(checked, 0)
        self.assertEqual(len(bad), 1)
        self.assertIn("无对象可检查", bad[0])

    def test_symlinked_json_fails_closed(self):
        target = os.path.join(self.tmp.name, "real-data.json")
        with open(target, "w", encoding="utf-8") as fh:
            json.dump(COMPLIANT, fh, ensure_ascii=False)
        nested = os.path.join(rdi.FIXTURES, "g7-01")
        os.makedirs(nested, exist_ok=True)
        os.symlink(target, os.path.join(nested, "G7-01-LINK.json"))
        bad, checked, exempt = rdi._check_fixtures()
        self.assertEqual(checked, 0)
        self.assertEqual(len(bad), 1)
        self.assertIn("G7-01-LINK.json", bad[0])
        self.assertIn("符号链接", bad[0])

    def test_symlinked_directory_fails_closed(self):
        # os.walk(followlinks=False) 静默跳过符号链接目录 —— 漏扫仍会
        # 报「检查 0 个」绿灯。必须显式判红：目录符号链接指向真实数据
        # 不得绕过递归扫描。
        real = os.path.join(self.tmp.name, "real-tree")
        os.makedirs(os.path.join(real, "nested"), exist_ok=True)
        with open(os.path.join(real, "nested", "secret.json"), "w",
                  encoding="utf-8") as fh:
            json.dump(UNMARKED, fh, ensure_ascii=False)
        os.makedirs(rdi.FIXTURES, exist_ok=True)
        os.symlink(real, os.path.join(rdi.FIXTURES, "linked-dir"))
        bad, checked, exempt = rdi._check_fixtures()
        self.assertEqual(checked, 0)
        self.assertEqual(len(bad), 1)
        self.assertIn("linked-dir", bad[0])
        self.assertIn("符号链接目录", bad[0])

    def test_empty_fixtures_tree_fails_closed(self):
        # fixtures/ 存在但没有任何 .json —— 检查 0 个不得算通过。
        os.makedirs(os.path.join(rdi.FIXTURES, "empty-subdir"), exist_ok=True)
        bad, checked, exempt = rdi._check_fixtures()
        self.assertEqual(checked, 0)
        self.assertEqual(len(bad), 1)
        self.assertIn("无任何 .json fixture", bad[0])
        self.assertIn("检查 0 个", bad[0])

    def test_symlink_only_tree_fails_once_not_double_counted(self):
        # 只有符号链接目录、无真实 .json：符号链接错误已存在，不得再
        # 重复计一次「检查 0 个」（bad 应恰好 1 条）。
        real = os.path.join(self.tmp.name, "real-tree")
        os.makedirs(real, exist_ok=True)
        os.makedirs(rdi.FIXTURES, exist_ok=True)
        os.symlink(real, os.path.join(rdi.FIXTURES, "linked-dir"))
        bad, checked, exempt = rdi._check_fixtures()
        self.assertEqual(checked, 0)
        self.assertEqual(len(bad), 1)
        self.assertIn("符号链接目录", bad[0])

    def test_invalid_json_fails_closed(self):
        nested = os.path.join(rdi.FIXTURES, "g7-01")
        os.makedirs(nested, exist_ok=True)
        with open(os.path.join(nested, "G7-01-BROKEN.json"), "w",
                  encoding="utf-8") as fh:
            fh.write("{ this is not json ")
        bad, checked, exempt = rdi._check_fixtures()
        self.assertEqual(checked, 0)
        self.assertEqual(len(bad), 1)
        self.assertIn("G7-01-BROKEN.json", bad[0])
        self.assertIn("解析失败", bad[0])

    # ── 三返工：隐藏目录不得剪枝 / 符号链接文件不论扩展名 / 根符号链接 ─
    def test_hidden_directory_fixtures_counted_not_pruned(self):
        # 隐藏目录里的合规 fixture 同样是检查对象 —— 剪枝即漏扫成绿灯。
        _write_fixtures(rdi.FIXTURES, {
            ".hidden/G7-01-HIDDEN.json": COMPLIANT,
        })
        bad, checked, exempt = rdi._check_fixtures()
        self.assertEqual(bad, [])
        self.assertEqual(checked, 1)
        self.assertIn(".hidden/G7-01-HIDDEN.json", exempt)

    def test_hidden_directory_symlink_flagged(self):
        # 隐藏目录里的符号链接同样判红（剪枝会把它漏掉）。
        target = os.path.join(self.tmp.name, "real-data.json")
        with open(target, "w", encoding="utf-8") as fh:
            json.dump(COMPLIANT, fh, ensure_ascii=False)
        hidden = os.path.join(rdi.FIXTURES, ".hidden")
        os.makedirs(hidden, exist_ok=True)
        os.symlink(target, os.path.join(hidden, "link.json"))
        bad, checked, exempt = rdi._check_fixtures()
        self.assertEqual(checked, 0)
        self.assertEqual(len(bad), 1)
        self.assertIn(".hidden/link.json", bad[0])
        self.assertIn("符号链接文件", bad[0])

    def test_non_json_symlink_file_fails_closed(self):
        # 符号链接文件**不论扩展名**一律判红 —— 只挡 .json 会让
        # fixtures/notes.txt 指向真实数据即可绕过。
        target = os.path.join(self.tmp.name, "real-notes.txt")
        with open(target, "w", encoding="utf-8") as fh:
            fh.write("real research data")
        os.makedirs(rdi.FIXTURES, exist_ok=True)
        os.symlink(target, os.path.join(rdi.FIXTURES, "notes.txt"))
        bad, checked, exempt = rdi._check_fixtures()
        self.assertEqual(checked, 0)
        self.assertEqual(len(bad), 1)
        self.assertIn("notes.txt", bad[0])
        self.assertIn("符号链接文件", bad[0])

    def test_symlinked_fixture_root_rejected(self):
        # fixtures 根本身是符号链接 —— os.walk 会跟随根、扫根所指的真实树，
        # 等于入仓一个指向外部真实数据的探针。必须显式判红。
        real = os.path.join(self.tmp.name, "real-fixtures")
        _write_fixtures(real, {"G7-01-OK.json": COMPLIANT})
        os.makedirs(os.path.dirname(rdi.FIXTURES), exist_ok=True)
        os.symlink(real, rdi.FIXTURES)
        bad, checked, exempt = rdi._check_fixtures()
        self.assertEqual(checked, 0)
        self.assertEqual(len(bad), 1)
        self.assertIn("根是符号链接", bad[0])

    def test_checked_in_g7_fixtures_scanned_and_pass(self):
        self._old_fixtures = rdi.FIXTURES
        rdi.FIXTURES = REAL_FIXTURES
        try:
            bad, checked, exempt = rdi._check_fixtures()
        finally:
            rdi.FIXTURES = self._old_fixtures
        rels = {os.path.join("g7-01", f"G7-01-{s}.json")
                for s in ("POSITIVE", "RESTATEMENT", "WRONG_BASIS")}
        self.assertTrue(rels.issubset(set(exempt)), exempt)
        self.assertFalse(any("g7-01/" in b for b in bad), bad)


class TestInterdictEndToEnd(unittest.TestCase):
    """子进程端到端：整具工具在临时 git 仓库上运行（乙半需要 git 枚举）。"""

    def _run(self, root):
        return subprocess.run(
            [sys.executable, os.path.join(TOOLS, "research_data_interdict.py"),
             root],
            capture_output=True, text=True, timeout=60)

    def test_nested_compliant_fixtures_pass(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixtures = os.path.join(tmp, "backend", "tests", "fixtures")
            _write_fixtures(fixtures, {
                "g7-01/G7-01-POSITIVE.json": COMPLIANT,
                "g7-01/G7-01-RESTATEMENT.json": dict(
                    COMPLIANT, case_id="G7-01-RESTATEMENT"),
            })
            subprocess.run(["git", "init", "-q", tmp], check=True)
            subprocess.run(["git", "-C", tmp, "add", "-A"], check=True,
                           capture_output=True)
            r = self._run(tmp)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn("检查 2 个 fixture", r.stdout)
            self.assertIn("g7-01/G7-01-POSITIVE.json", r.stdout)

    def test_nested_unmarked_fixture_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixtures = os.path.join(tmp, "backend", "tests", "fixtures")
            _write_fixtures(fixtures, {
                "g7-01/G7-01-POSITIVE.json": COMPLIANT,
                "g7-01/G7-01-BAD.json": UNMARKED,
            })
            subprocess.run(["git", "init", "-q", tmp], check=True)
            subprocess.run(["git", "-C", tmp, "add", "-A"], check=True,
                           capture_output=True)
            r = self._run(tmp)
            self.assertNotEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn("g7-01/G7-01-BAD.json", r.stdout)
            self.assertIn("违规 1 项", r.stdout)

    def test_nested_real_locator_fixture_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixtures = os.path.join(tmp, "backend", "tests", "fixtures")
            _write_fixtures(fixtures, {
                "g7-01/G7-01-POSITIVE.json": REAL_LOCATOR_FIXTURE,
            })
            subprocess.run(["git", "init", "-q", tmp], check=True)
            subprocess.run(["git", "-C", tmp, "add", "-A"], check=True,
                           capture_output=True)
            r = self._run(tmp)
            self.assertNotEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn("真实形态 locator", r.stdout)

    def test_repo_scan_reports_nested_g7_fixtures(self):
        """入仓的三个 G7 fixture 必须被扫且通过（工具对真实仓库根）。"""
        root = os.path.join(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))), "..")
        r = subprocess.run(
            [sys.executable, os.path.join(TOOLS, "research_data_interdict.py"),
             root],
            capture_output=True, text=True, timeout=120)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("g7-01/G7-01-POSITIVE.json", r.stdout)
        self.assertIn("g7-01/G7-01-RESTATEMENT.json", r.stdout)
        self.assertIn("g7-01/G7-01-WRONG_BASIS.json", r.stdout)


if __name__ == "__main__":
    unittest.main()
