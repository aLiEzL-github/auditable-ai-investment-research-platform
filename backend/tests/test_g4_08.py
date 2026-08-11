"""G4-08 验收测试：断网、空缓存离线复建（D-8/D-9/D-10/D-11）。

基线 B §7 G4-08：对象完整时复建同一对象根；缺对象只能 PROVENANCE_ONLY，
不得冒充完整复验。

D-8  「断网」必须真断网测 —— 在 OS 级/容器级断网环境中执行：
       · 首选 docker run --network none（容器内无网络栈，且天然是干净环境 D-9）
       · darwin 回退 sandbox-exec (deny network*) + env -i + python -S -I
       两种机制都不可用时判红（fail-closed：无法证真断网 = 不通过）。
D-9  复建环境独立：容器 / 隔离进程，不复用开发机既有状态。
D-10 FULL 与 PROVENANCE_ONLY 在输出中显式可分辨，下游行为不同。
D-11 把 PROVENANCE_ONLY 当完整复验使用必须 FAIL（一票否决，单独用例）。
"""
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

APP = os.path.join(os.path.dirname(__file__), "..", "app")
sys.path.insert(0, APP)
sys.path.insert(0, os.path.dirname(__file__))

from artifact_store import ArtifactStore
import _g4_fixtures as fx
from publish_engine import (VERIFICATION_FULL, VERIFICATION_PROVENANCE_ONLY,
                            RebuildResult, consume_rebuild, rebuild_from_store)

REBUILD_SCRIPT = r'''
import hashlib, json, os, sys
sys.path.insert(0, "/app")                      # backend/app 只读挂载
from artifact_store import ArtifactStore
from network_probe import assert_network_unreachable
from publish_engine import (VERIFICATION_FULL, VERIFICATION_PROVENANCE_ONLY,
                            consume_rebuild, rebuild_from_store)

manifest = json.load(open("/work/manifest.json", encoding="utf-8"))
store = ArtifactStore("/objs")                  # 对象库只读挂载
try:
    assert_network_unreachable()                # 先证真断网（D-8）
except ValueError as e:
    print("PROBE_REACHABLE: " + str(e)); sys.exit(1)

result = rebuild_from_store(store, manifest, "/out",
                            probe=assert_network_unreachable)
missing = result.missing
if not missing:
    consume_rebuild(result, VERIFICATION_FULL)   # FULL 可用
else:
    try:
        consume_rebuild(result, VERIFICATION_FULL)   # D-11：冒充完整复验必败
        print("MISUSE_ACCEPTED"); sys.exit(2)
    except ValueError:
        pass                                      # 正确拒绝
files = sorted(os.listdir("/out"))
digest = hashlib.sha256(b"".join(
    open("/out/" + f, "rb").read() for f in files)).hexdigest()
print(json.dumps({"verification_level": result.verification_level,
                  "rebuilt": len(result.rebuilt),
                  "missing": missing,
                  "output_digest": digest}))
'''


def _probe_stub_ok():
    """单测用探针替身：不真实断网（仅测等级语义；真断网由容器测试承担）。"""
    return None


class TestOfflineRebuild(unittest.TestCase):
    """D-8/D-9：真断网 + 干净环境复建（docker --network none 或 sandbox-exec）。"""

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self.store = ArtifactStore(os.path.join(self._tmp, "lib"))
        self.key = __import__("publish_engine").CurrentKey(
            "a-share-single-company-research", "600089.SH")
        self.manifest = fx.minimal_closure(self.store, self.key)
        self.manifest_digest = fx.freeze_manifest(self.store, self.manifest)
        self.expected = {oid: self.store.load(oid)
                         for oid in self.manifest["objects"]}

    def tearDown(self):
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _docker(self, store_dir: str, out_dir: str,
                delete_objects: tuple = ()) -> subprocess.CompletedProcess:
        """docker --network none 断网复建（不经 bind mount —— docker cp 注入
        文件，避免 virtiofs/CI 挂载差异；容器内无网络栈 = 真断网）。"""
        docker = shutil.which("docker") or os.path.expanduser(
            "~/.local/bin/docker")
        if not docker or not os.path.exists(docker):
            return None
        os.makedirs(out_dir)
        manifest_fp = os.path.join(self._tmp, "manifest.json")
        with open(manifest_fp, "w", encoding="utf-8") as f:
            json.dump(self.manifest, f)
        script_fp = os.path.join(self._tmp, "rebuild.py")
        with open(script_fp, "w", encoding="utf-8") as f:
            f.write(REBUILD_SCRIPT)
        for oid in delete_objects:
            rel = f"{oid[:2]}/{oid[2:4]}/{oid[4:]}"
            p = os.path.join(store_dir, rel)
            if os.path.exists(p):
                os.chmod(p, 0o644)
                os.remove(p)
        app_dir = os.path.normpath(APP)          # backend/app（artifact_store 所在）
        cid = subprocess.run(
            [docker, "create", "--network", "none",
             "python:3.11-alpine", "sleep", "300"],
            capture_output=True, text=True).stdout.strip()
        if not cid:
            return None
        try:
            subprocess.run([docker, "start", cid], capture_output=True,
                           check=True)
            subprocess.run([docker, "exec", cid, "mkdir", "-p",
                            "/work", "/objs", "/out"],
                           capture_output=True, check=True)
            for src, dst in ((app_dir, "/app"),
                             (os.path.join(self._tmp, "rebuild.py"),
                              "/work/rebuild.py"),
                             (manifest_fp, "/work/manifest.json")):
                subprocess.run([docker, "cp", src, f"{cid}:{dst}"],
                               capture_output=True, check=True)
            rels = []
            for dp, _dn, fns in os.walk(store_dir):
                for fn in fns:
                    rels.append(os.path.relpath(os.path.join(dp, fn),
                                                store_dir))
            if rels:
                dirs = sorted({os.path.dirname(r) for r in rels})
                subprocess.run([docker, "exec", cid, "mkdir", "-p",
                                *[f"/objs/{d}" for d in dirs]],
                               capture_output=True, check=True)
                for rel in rels:
                    subprocess.run(
                        [docker, "cp", os.path.join(store_dir, rel),
                         f"{cid}:/objs/{rel}"],
                        capture_output=True, check=True)
            run = subprocess.run(
                [docker, "exec", cid, "python", "/work/rebuild.py"],
                capture_output=True, text=True)
            code = run.returncode
            out = subprocess.run([docker, "cp", f"{cid}:/out/.", out_dir],
                                 capture_output=True)
            return subprocess.CompletedProcess(
                [docker, "exec", "--network", "none", "rebuild"],
                code, run.stdout, run.stderr)
        finally:
            subprocess.run([docker, "rm", "-f", cid], capture_output=True)

    def _sandbox(self, store_dir: str, out_dir: str,
                 delete_objects: tuple = ()) -> subprocess.CompletedProcess:
        """darwin 回退：sandbox-exec deny network* + env -i + python -S。"""
        if not shutil.which("sandbox-exec"):
            return None
        os.makedirs(out_dir)
        manifest_fp = os.path.join(self._tmp, "manifest.json")
        with open(manifest_fp, "w", encoding="utf-8") as f:
            json.dump(self.manifest, f)
        # 脚本用绝对路径版（与容器版等价：仅 stdlib 依赖）
        script = REBUILD_SCRIPT.replace('sys.path.insert(0, "/app")',
                                        f'sys.path.insert(0, "{APP}")')
        script = script.replace('"/work/manifest.json"',
                                f'"{manifest_fp}"')
        script = script.replace('ArtifactStore("/objs")',
                                f'ArtifactStore("{store_dir}")')
        script = script.replace('"/out/"', f'"{out_dir}/"')
        script = script.replace('"/out"', f'"{out_dir}"')
        script_fp = os.path.join(self._tmp, "rebuild_sb.py")
        with open(script_fp, "w", encoding="utf-8") as f:
            f.write(script)
        for oid in delete_objects:
            rel = f"{oid[:2]}/{oid[2:4]}/{oid[4:]}"
            p = os.path.join(store_dir, rel)
            if os.path.exists(p):
                os.chmod(p, 0o644)
                os.remove(p)
        cmd = ["sandbox-exec", "-p",
               "(version 1) (allow default) (deny network*)",
               "env", "-i", f"PATH={os.environ['PATH']}",
               "HOME=" + os.path.join(self._tmp, "home"),
               sys.executable, "-S", "-I", script_fp]
        return subprocess.run(cmd, capture_output=True, text=True, timeout=180)

    def _run_offline(self, delete_objects=()):
        """任一机制可用即用；全部不可用 → fail-closed。"""
        store_dir = str(self.store.root)
        out_dir = os.path.join(self._tmp, "out")
        r = self._docker(store_dir, out_dir, delete_objects)
        mechanism = "docker --network none"
        if r is None:
            r = self._sandbox(store_dir, out_dir, delete_objects)
            mechanism = "sandbox-exec(deny network)"
        if r is None:
            self.fail("本环境既无 docker 也无 sandbox-exec —— "
                      "无法建立 OS 级断网，离线复建验收不通过（fail-closed）")
        return r, mechanism, out_dir

    # ── D-8/D-9 正例：断网、干净环境、逐字节一致复建 ───────────────
    def test_offline_full_rebuild_byte_identical(self):
        r, mech, out_dir = self._run_offline()
        self.assertEqual(r.returncode, 0,
                         f"[{mech}] 复建失败: {r.stderr[-800:]}")
        self.assertNotIn("PROBE_REACHABLE", r.stdout)
        report = json.loads(r.stdout.strip().splitlines()[-1])
        self.assertEqual(report["verification_level"], VERIFICATION_FULL,
                         f"[{mech}] 对象完整须 FULL")
        self.assertEqual(report["missing"], [])
        self.assertEqual(report["rebuilt"], len(self.expected))
        # 逐字节一致：复建产物哈希 == 原对象库对象哈希
        rebuilt = {}
        for fn in os.listdir(out_dir):
            with open(os.path.join(out_dir, fn), "rb") as f:
                rebuilt[fn] = f.read()
        self.assertEqual(set(rebuilt), set(self.expected),
                         f"[{mech}] 复建对象集合与 manifest 不一致")
        for oid, data in self.expected.items():
            self.assertEqual(rebuilt[oid], data,
                             f"[{mech}] 对象 {oid[:8]}… 未逐字节一致")

    # ── D-8/D-10/D-11：缺对象 → PROVENANCE_ONLY，冒充完整复验必败 ──
    def test_offline_missing_object_provenance_only(self):
        victim = next(oid for oid, meta in self.manifest["objects"].items()
                      if meta.get("kind") == "report")
        r, mech, out_dir = self._run_offline(delete_objects=(victim,))
        self.assertEqual(r.returncode, 0,
                         f"[{mech}] PROVENANCE_ONLY 流程须正常结束: {r.stderr[-800:]}")
        report = json.loads(r.stdout.strip().splitlines()[-1])
        self.assertEqual(report["verification_level"],
                         VERIFICATION_PROVENANCE_ONLY,
                         f"[{mech}] 缺对象只能 PROVENANCE_ONLY（D-10 显式可分辨）")
        self.assertIn(victim, report["missing"])
        self.assertNotIn("MISUSE_ACCEPTED", r.stdout,
                         f"[{mech}] D-11：冒充完整复验必须被拒绝")

    # ── 幂等：同一对象库连跑三次，复建产物哈希一致（D-7 沿用）──────
    def test_offline_rebuild_three_runs_same_digest(self):
        digests = set()
        for _ in range(3):
            store_dir = str(self.store.root)
            out_dir = os.path.join(self._tmp, "out3", str(_))
            r = self._docker(store_dir, out_dir)
            mech = "docker --network none"
            if r is None:
                r = self._sandbox(store_dir, out_dir)
                mech = "sandbox-exec(deny network)"
            if r is None:
                self.fail("无可用断网机制（fail-closed）")
            self.assertEqual(r.returncode, 0, r.stderr[-500:])
            report = json.loads(r.stdout.strip().splitlines()[-1])
            digests.add(report["output_digest"])
        self.assertEqual(len(digests), 1, "连跑三次复建产物哈希须一致")


class TestVerificationLevels(unittest.TestCase):
    """D-10/D-11 行为语义（探针替身；真断网由上方容器测试承担）。"""

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self.store = ArtifactStore(os.path.join(self._tmp, "lib"))
        self.key = __import__("publish_engine").CurrentKey(
            "a-share-single-company-research", "600089.SH")

    def tearDown(self):
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_full_distinguishable_and_consumable(self):
        m = fx.minimal_closure(self.store, self.key)
        out = os.path.join(self._tmp, "out")
        r = rebuild_from_store(self.store, m, out, probe=_probe_stub_ok)
        self.assertEqual(r.verification_level, VERIFICATION_FULL)
        self.assertIsNone(consume_rebuild(r, VERIFICATION_FULL))

    def test_provenance_only_explicit_and_rejected_for_full(self):
        m = fx.minimal_closure(self.store, self.key)
        victim = next(iter(m["objects"]))
        rel = f"{victim[:2]}/{victim[2:4]}/{victim[4:]}"
        p = os.path.join(self.store.root, rel)
        try:
            os.remove(p)
        except PermissionError:
            os.chmod(p, 0o644)
            os.remove(p)
        out = os.path.join(self._tmp, "out2")
        r = rebuild_from_store(self.store, m, out, probe=_probe_stub_ok)
        self.assertEqual(r.verification_level, VERIFICATION_PROVENANCE_ONLY,
                         "缺对象只能 PROVENANCE_ONLY")
        self.assertEqual(r.missing, [victim])
        with self.assertRaises(ValueError) as cm:
            consume_rebuild(r, VERIFICATION_FULL)      # D-11：冒充必败
        self.assertIn("E-G4-08-002", str(cm.exception))

    def test_rebuild_without_probe_fails_closed(self):
        """D-8 补充：内核不持探针 —— 缺探针拒绝执行（不接受「理论上可以」）。"""
        m = fx.minimal_closure(self.store, self.key)
        out = os.path.join(self._tmp, "outX")
        with self.assertRaises(ValueError) as cm:
            rebuild_from_store(self.store, m, out, probe=None)
        self.assertIn("E-G4-08-003", str(cm.exception))

    def test_direct_claim_fails(self):
        """D-11 独立用例：把 PROVENANCE_ONLY 输出标成 FULL 交给下游 → 拒绝。"""
        forged = RebuildResult(verification_level=VERIFICATION_FULL,
                               missing=["a" * 64], rebuilt=[], out_dir="")
        with self.assertRaises(ValueError) as cm:
            consume_rebuild(forged, VERIFICATION_FULL)
        self.assertIn("E-G4-08-002", str(cm.exception))
        # 行为验证：不仅仅查字段存在 —— 伪造的 FULL 含 missing 必须被拒
        honest = RebuildResult(verification_level=VERIFICATION_PROVENANCE_ONLY,
                               missing=["b" * 64], rebuilt=[], out_dir="")
        with self.assertRaises(ValueError):
            consume_rebuild(honest, VERIFICATION_FULL)


if __name__ == "__main__":
    unittest.main()
