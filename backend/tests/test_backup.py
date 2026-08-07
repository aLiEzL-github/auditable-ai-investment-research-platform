"""G1-08 备份逻辑测试（跨平台：不含 hdiutil 部分，本机实测另记录）。

覆盖：manifest 排除规则 / 保留政策 / 哈希比对核心（4a/4b 的可测部分）。
"""

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))
import backup


class TestManifest(unittest.TestCase):
    def test_manifest_excludes_pycache(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, "app", "__pycache__"))
            os.makedirs(os.path.join(tmp, ".venv", "bin"))
            with open(os.path.join(tmp, "app", "main.py"), "w") as fh:
                fh.write("print(1)")
            with open(os.path.join(tmp, "app", "__pycache__", "main.cpython-311.pyc"), "wb") as fh:
                fh.write(b"x")
            with open(os.path.join(tmp, ".venv", "bin", "python"), "w") as fh:
                fh.write("#!/bin/sh")
            m = backup.manifest_of(tmp)
            self.assertNotIn("app/__pycache__/main.cpython-311.pyc", m)
            self.assertNotIn(".venv/bin/python", m)
            self.assertIn("app/main.py", m)

    def test_manifest_sha256_full(self):
        with tempfile.TemporaryDirectory() as tmp:
            fp = os.path.join(tmp, "f.txt")
            content = b"hello-backup" * 100
            with open(fp, "wb") as fh:
                fh.write(content)
            m = backup.manifest_of(tmp)
            self.assertEqual(m["f.txt"], backup.sha256_file(fp))
            self.assertEqual(len(m["f.txt"]), 64)  # 全长哈希


class TestPrunePolicy(unittest.TestCase):
    def test_keep_only_newest(self):
        backup.BACKUP_ROOT = tempfile.mkdtemp()
        for i in range(10):
            name = f"g1-08-2026080{i}T000000Z.sparseimage"
            open(os.path.join(backup.BACKUP_ROOT, name), "w").close()
            open(os.path.join(backup.BACKUP_ROOT, name + ".manifest.json"), "w").close()
        backup._prune("g1-08")
        remaining = [f for f in os.listdir(backup.BACKUP_ROOT)
                     if f.endswith(".sparseimage")]
        self.assertLessEqual(len(remaining), backup.KEEP_DAILY + backup.KEEP_WEEKLY + backup.KEEP_MONTHLY)


class TestRpoRtoTargets(unittest.TestCase):
    def test_targets_are_vd19_values(self):
        self.assertEqual(backup.RPO_TARGET_S, 24 * 3600)
        self.assertEqual(backup.RTO_TARGET_S, 8 * 3600)


if __name__ == "__main__":
    unittest.main()
