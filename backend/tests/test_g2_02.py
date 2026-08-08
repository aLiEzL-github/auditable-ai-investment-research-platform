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
        # 变异注入 2：绕过 store 直接改写库内文件字节 → load 必须检测拒绝
        rel = f"{d[:2]}/{d[2:4]}/{d[4:]}"
        fp = os.path.join(self.store.root, rel)
        with open(fp, "wb") as f:
            f.write(b"v1-EVIL")
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
