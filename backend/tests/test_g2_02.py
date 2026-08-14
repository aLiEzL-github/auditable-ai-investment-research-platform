"""G2-02 验收测试：内容寻址工件存储（不可变 + BF-03 防逃逸 + X-8 变异注入）。

基线：已登记原件不可原地改写。
BF-03：写入路径目录逃逸与 symlink 必失败（../ 穿越、库外 symlink、深度嵌套）。
X-8：变异注入 —— 尝试改写已登记原件须被拒。
"""
import unittest
import tempfile
import shutil
import os
import sys

APP = os.path.join(os.path.dirname(__file__), "..", "app")
sys.path.insert(0, APP)

from artifact_store import ArtifactStore


class TestArtifactStore(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self.store = ArtifactStore(os.path.join(self._tmp, "lib"))

    def tearDown(self):
        shutil.rmtree(self._tmp, ignore_errors=True)

    # ── 正例：合法写入 + 内容寻址去重 ────────────────────────────────
    def test_store_and_load(self):
        d = self.store.store("ART_0001", b"hello")
        self.assertEqual(len(d), 64)
        self.assertEqual(self.store.load(d), b"hello")
        # 内容寻址：同内容同摘要；同内容重复写幂等返回（不覆盖）
        d2 = self.store.store("ART_0002", b"hello")
        self.assertEqual(d, d2)

    # ── 基线：已登记原件不可原地改写（X-8 变异注入）──────────────────
    def test_immutable_reject_overwrite(self):
        d = self.store.store("ART_0001", b"v1")
        # 变异注入 1：同摘要重写（幂等）不改变内容
        d2 = self.store.store("ART_0001", b"v1")
        self.assertEqual(d, d2)
        self.assertEqual(self.store.load(d), b"v1")
        # 变异注入 2：绕过 store 直接改写库内文件字节
        rel = f"{d[:2]}/{d[2:4]}/{d[4:]}"
        fp = os.path.join(self.store.root, rel)
        try:
            with open(fp, "wb") as f:
                f.write(b"v1-EVIL")
            # 若权限被绕过（chmod 644 事故）→ load 哈希校验兜底拒绝
            with self.assertRaises(ValueError) as ctx:
                self.store.load(d)
            self.assertIn("E-G2-02-005", str(ctx.exception))
        except PermissionError:
            pass  # BB-2：写入侧只读（0o444）已直接拒绝，无需读时兜底

    # ── BB-2：写入侧只读加固（chmod 0o444）──────────────────────────
    def test_write_side_ro(self):
        import stat
        d = self.store.store("ART_2001", b"ro-data")
        rel = f"{d[:2]}/{d[2:4]}/{d[4:]}"
        fp = os.path.join(self.store.root, rel)
        mode = os.stat(fp).st_mode
        self.assertEqual(mode & 0o444, 0o444, "写入后须为只读")
        self.assertEqual(mode & 0o222, 0, "不得可写")
        # 负测：chmod 前后直接改写，均须被 load 拒绝
        try:
            with open(fp, "wb") as f:
                f.write(b"evil")
            self.fail("只读文件应拒绝改写")
        except PermissionError:
            pass
        os.chmod(fp, 0o644)  # 模拟权限被改（攻击/事故）
        with open(fp, "wb") as f:
            f.write(b"evil-2")
        with self.assertRaises(ValueError) as ctx:
            self.store.load(d)
        self.assertIn("E-G2-02-005", str(ctx.exception))

    # ── BF-03 负例 1：../ 穿越 ───────────────────────────────────────
    def test_traversal_rejected(self):
        for evil in ("../evil", "a/../../evil", "..", "../../.."):
            with self.assertRaises(ValueError) as ctx:
                self.store.store(evil, b"x")
            self.assertIn("E-G2-02-002", str(ctx.exception), evil)

    # ── BF-03 负例 2：库外 symlink（load 路径）───────────────────────
    def test_external_symlink_rejected(self):
        outside = os.path.join(self._tmp, "outside.txt")
        with open(outside, "w") as f:
            f.write("secret")
        # 库内被植入指向库外的 symlink：digest 路径链上的目录是 symlink
        # （root/aa/aa → outside），load("aa…") resolve 后追出库外 → 拒绝
        evil_digest = "aa" * 32
        os.makedirs(os.path.join(self.store.root, "aa"), exist_ok=True)
        os.symlink(outside, os.path.join(self.store.root, "aa", "aa"))
        with self.assertRaises(ValueError) as ctx:
            self.store.load(evil_digest)
        self.assertIn("E-G2-02-001", str(ctx.exception))
        # store 路径同理：不经过任意用户路径，digest 由内容决定（逃逸面封闭）

    # ── BF-03 负例 2b（OI-PF-013/B4-1）：写原语直接遇 symlink 载荷 ──
    def test_store_symlink_payload_fails_closed(self):
        """对写原语直接构造 symlink 载荷：digest 路径链上的目录被替换为
        指向库外的 symlink 时，store() 必须失败关闭（不得写出库外）。"""
        outside_dir = os.path.join(self._tmp, "outside-lib")
        os.makedirs(outside_dir)
        marker = os.path.join(outside_dir, "marker.txt")
        with open(marker, "w") as f:
            f.write("escaped")
        # 内容寻址路径不可预知 → 先占一个 64 位 hex 摘要前缀的目录位置，
        # 植入「目录 → 库外」symlink（模拟攻击者预先布置的写路径劫持）
        d = self.store.store("ART_SYM1", b"probe")
        rel = f"{d[:2]}/{d[2:4]}/{d[4:]}"
        target_dir = os.path.join(self.store.root, d[:2], d[2:4])
        os.chmod(target_dir, 0o755)
        shutil.rmtree(target_dir)
        os.symlink(outside_dir, target_dir)
        # 同内容再次 store：目标链已被 symlink 劫持 → resolve 追出库外 → 拒绝
        with self.assertRaises(ValueError) as ctx:
            self.store.store("ART_SYM2", b"probe")
        self.assertIn("E-G2-02-001", str(ctx.exception))
        # 失败关闭：写原语不得经 symlink 写出 —— 库外既无新文件、也无改写
        with open(marker) as f:
            marker_content = f.read()
        self.assertEqual(
            marker_content, "escaped",
            "symlink 载荷下写原语不得改写到库外（内容不变 = 未写出）")
        self.assertEqual(
            sorted(os.listdir(outside_dir)), ["marker.txt"],
            "库外目录不得出现由写原语产生的任何新文件")

    # ── BF-03 负例 3：超长/深度嵌套名 ────────────────────────────────
    def test_overlong_name_rejected(self):
        with self.assertRaises(ValueError) as ctx:
            self.store.store("A" * 65, b"x")
        self.assertIn("E-G2-02-002", str(ctx.exception))

    # ── 非法摘要拒绝 ─────────────────────────────────────────────────
    def test_bad_digest_rejected(self):
        with self.assertRaises(ValueError):
            self.store.load("nothex" * 8)


def hashlib_sha(data: bytes) -> str:
    import hashlib
    return hashlib.sha256(data).hexdigest()


if __name__ == "__main__":
    unittest.main()
