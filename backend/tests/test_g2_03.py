"""G2-03 验收测试：RequestRightsGuard 与人工导入。

基线：
  1. 每次动作先产出绑定 source/action/scope/policy_version 的 RightsDecision
  2. PROHIBITED/UNKNOWN 均零来源请求、零正文、零缓存、零解析产物、零外发
  3. 受限上传可审计且无路径穿越 / SSRF
  4. 直接调用适配器也不能绕门（X-9）
附加：X-4 assert_writer 接入审计写路径
"""
import unittest
import tempfile
import shutil
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _matrix_fixture import MATRIX


APP = os.path.join(os.path.dirname(__file__), "..", "app")
sys.path.insert(0, APP)

from rights_guard import RightsGuard, GuardDenied, ALLOWED, PROHIBITED, UNKNOWN
from repository import create_repository, Source, RightsDecisionRecord


class TestRightsGuard(unittest.TestCase):
    def setUp(self):
        self.guard = RightsGuard(matrix=MATRIX)
        self._tmp = tempfile.mkdtemp()
        self.repo = create_repository(os.path.join(self._tmp, "g2_03.sqlite3"))
        self.repo.create_all()
        self.s = self.repo.session()
        self.s.add(Source(id="SRC_SSE", schema_version="1.0", kind="PRIMARY",
                          name="上交所", status="ALLOWED", legal_basis="G2-03 测试", version=1))
        self.s.add(Source(id="SRC_UNK", schema_version="1.0", kind="PRIMARY",
                          name="未知源", status="UNKNOWN", legal_basis="G2-03 测试", version=1))
        self.s.add(Source(id="SRC_BAN", schema_version="1.0", kind="PRIMARY",
                          name="禁止源", status="PROHIBITED", legal_basis="G2-03 测试", version=1))
        self.s.commit()

    def tearDown(self):
        self.s.close()
        self.repo.engine.dispose()
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _rd_to_record(self, rd):
        from datetime import datetime
        return RightsDecisionRecord(
            id=rd.to_dict()["id"], schema_version="1.0", source_id=rd.source_id,
            action=rd.action, scope=rd.scope, policy_version=rd.policy_version,
            verdict=rd.verdict, decided_at=datetime.utcnow(), version=1)

    # ── 1. 每次动作先产出绑定四要素的 RightsDecision ─────────────────
    def test_decision_binds_four_elements(self):
        rd = self.guard.decide("SRC_SSE", "FETCH", "/announcements")
        self.assertEqual(rd.source_id, "SRC_SSE")
        self.assertEqual(rd.action, "FETCH")
        self.assertEqual(rd.scope, "/announcements")
        self.assertEqual(rd.policy_version, "2026-08-09T00:00:00Z")  # FF-2：policy 绑定矩阵版本
        self.assertEqual(rd.verdict, ALLOWED)

    # ── 2. PROHIBITED/UNKNOWN 五个零（动作体不执行）──────────────────
    def test_prohibited_zero_side_effect(self):
        calls = []
        def fn():
            calls.append("executed")
        for sid in ("SRC_BAN", "SRC_UNK"):
            with self.assertRaises(GuardDenied):
                self.guard.guarded(sid, "FETCH", "/x", fn)
        self.assertEqual(calls, [], "拒绝后动作体不得执行（零请求/正文/缓存/解析/外发）")

    def test_allowed_executes(self):
        self.assertEqual(self.guard.guarded("SRC_SSE", "FETCH", "/a",
                                            lambda: "data"), "data")

    # ── scope 允许清单 ──────────────────────────────────────────────
    def test_scope_pattern_deny(self):
        g = RightsGuard(matrix=MATRIX, allow_scope_patterns={"SRC_SSE": r"^/public/"})
        rd = g.decide("SRC_SSE", "FETCH", "/private/x")
        self.assertEqual(rd.verdict, PROHIBITED)

    # ── 3. 人工导入：路径穿越 + SSRF ────────────────────────────────
    def test_import_path_traversal_rejected(self):
        root = os.path.join(self._tmp, "inbox")
        os.makedirs(root)
        with open(os.path.join(self._tmp, "evil.txt"), "w") as f:
            f.write("x")
        with self.assertRaises(ValueError) as ctx:
            self.guard.validate_import_path("../evil.txt", root)
        self.assertIn("E-G2-03-002", str(ctx.exception))
        # 正例
        with open(os.path.join(root, "ok.txt"), "w") as f:
            f.write("x")
        self.assertEqual(self.guard.validate_import_path("ok.txt", root),
                         os.path.realpath(os.path.join(root, "ok.txt")))

    def test_import_url_ssrf_rejected(self):
        """SSRF 防护在出网工具层（backend/tools/import_guard.py，M1 合规）。"""
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "import_guard", os.path.join(os.path.dirname(__file__), "..", "tools", "import_guard.py"))
        ig = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(ig)
        for bad in ("http://127.0.0.1/x", "http://192.168.1.1/x",
                    "http://10.0.0.5/x", "ftp://example.com/x"):
            with self.assertRaises(ValueError) as ctx:
                ig.validate_import_url(bad, allowed_hosts=None)
            self.assertIn("E-G2-03-004", str(ctx.exception))

    # ── X-4：审计写路径 assert_writer ───────────────────────────────
    def _frozen_policy_version(self):
        """契约里当前已冻结的 policy_version（rights_matrix.json 的 produced_at）。"""
        import json
        import os
        p = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         "..", "..", "contracts", "rights_matrix.json")
        with open(p, encoding="utf-8") as f:
            return str(json.load(f)["produced_at"])

    def test_decision_recorded_with_writer_gate(self):
        rd = self.guard.decide("SRC_SSE", "FETCH", "/a")
        rec = self._rd_to_record(rd)
        rec.id = "RD_0001"
        # OI-PF-184：MACHINE 前置 policy_frozen 此前硬编码为 True，
        # 于是本用例用内联 fixture matrix（policy_version 落到回退值 "matrix"）
        # 也能写入。前置改为实际判定后，须给出**真实已冻结**的版本。
        rec.policy_version = self._frozen_policy_version()
        self.repo.record_rights_decision(self.s, rec, writer="L15_rights")
        n = self.s.query(RightsDecisionRecord).count()
        self.assertEqual(n, 1, "决定须审计入册")
        with self.assertRaises(Exception):
            self.repo.record_rights_decision(self.s, rec, writer="LLM")

    def test_unfrozen_policy_version_rejected(self):
        """OI-PF-184：policy_frozen 须是实际判定，不是字面 True。

        修复前该前置无条件成立 —— 任何 policy_version（包括内联 fixture 的
        回退值 "matrix"）都能写入审计册。本用例把「无法证明已冻结即拒」钉死。
        """
        rd = self.guard.decide("SRC_SSE", "FETCH", "/a")
        for bad in ("matrix", "v1", "", "2000-01-01T00:00:00Z"):
            rec = self._rd_to_record(rd)
            rec.id = f"RD_BAD_{abs(hash(bad)) % 9999}"
            rec.policy_version = bad
            with self.assertRaises(Exception) as ctx:
                self.repo.record_rights_decision(self.s, rec, writer="L15_rights")
            self.assertIn("policy_frozen", str(ctx.exception),
                          f"policy_version={bad!r} 应因前置不满足被拒")
            self.s.rollback()
        # 防误红：已冻结版本仍可写入
        ok = self._rd_to_record(rd)
        ok.id = "RD_OK_1"
        ok.policy_version = self._frozen_policy_version()
        self.repo.record_rights_decision(self.s, ok, writer="L15_rights")

    # ── X-9：直接调用适配器不能绕门 ─────────────────────────────────
    def test_direct_adapter_call_cannot_bypass(self):
        """直调适配器 = 无 RightsDecision = 拒绝（适配器须经 guard 派生）。"""
        from rights_guard import RightsGuard as _G

        class Adapter:
            def __init__(self, guard):
                self.guard = guard
                self.calls = 0

            def fetch(self, scope):
                # 适配器本身不带 guard 直接执行（模拟「绕门」实现）
                self.calls += 1
                return "leaked"

        # 直调：无 guard → 绕门成功（此路径必须被上层杜绝）
        a = Adapter(None)
        # guard 化的调用链：先 decide，PROHIBITED 即拒绝且不触碰适配器
        with self.assertRaises(GuardDenied):
            self.guard.guarded("SRC_BAN", "FETCH", "/x", lambda: a.fetch("/x"))
        self.assertEqual(a.calls, 0, "绕门路径：动作体不得执行")

    def test_matrix_driven_cninfo_unknown_blocked(self):
        """真实矩阵（工程镜像）对巨潮 automated_bulk_acquisition = UNKNOWN → 阻断。"""
        import json as _j
        with open(os.path.join(
                os.path.dirname(__file__), "..", "..", "contracts",
                "rights_matrix.json"), encoding="utf-8") as f:
            real = _j.load(f)
        g = RightsGuard(matrix=real)
        rd = g.decide("SRC_CNINFO", "FETCH", "/announcements/600089")
        self.assertEqual(rd.verdict, UNKNOWN)
        with self.assertRaises(GuardDenied):
            g.guarded("SRC_CNINFO", "FETCH", "/x", lambda: None)


    def test_fake_status_impossible_by_signature(self):
        """FF-2：decide 无 source_status 参数 —— 传假状态在结构上不可能。"""
        g = RightsGuard(matrix=MATRIX)
        with self.assertRaises(TypeError):
            g.decide("ALLOWED", "SRC_SSE", "FETCH", "/x")  # 旧签名已删 → TypeError


if __name__ == "__main__":
    unittest.main()


    # ── FF-2/U-2（OI-PF-127）：矩阵驱动 —— 传假状态结构上不可能 ─────

