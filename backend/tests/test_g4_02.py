"""G4-02 验收测试：结构化证据/权利/安全/报告审计（D-12/D-13 署名义务）。

基线 B §7 G4-02：完整性、来源、materiality、计算、权利、安全、覆盖审计；
任一适用质量门非 PASS 或 materially critical Claim 有缺口 → release_eligible=false。
D-12：含 stats.gov.cn 数据的产出缺署名即 FAIL（先红后绿）。
D-13：「显著位置」= 首屏前 N 行（可机检），与 OI-PF-070 首屏口径一致。
"""
import json
import os
import shutil
import sys
import tempfile
import unittest

APP = os.path.join(os.path.dirname(__file__), "..", "app")
sys.path.insert(0, APP)
sys.path.insert(0, os.path.dirname(__file__))

from artifact_store import ArtifactStore
import _g4_fixtures as fx
from publish_engine import (PROMINENT_FIRST_LINES, attribution_guard,
                            audit_candidate, render_report_text,
                            resolve_subject_root)


class TestAuditGates(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self.store = ArtifactStore(os.path.join(self._tmp, "lib"))
        self.key = fx.sse_source() and __import__("publish_engine").CurrentKey(
            "a-share-single-company-research", "600089.SH")

    def tearDown(self):
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _manifest(self, **kw):
        return fx.minimal_closure(self.store, self.key, **kw)

    # ── 七门全 PASS → eligible ─────────────────────────────────────
    def test_all_gates_pass(self):
        m = self._manifest()
        a = audit_candidate(self.store, m)
        self.assertTrue(a.release_eligible, a.failures)
        self.assertEqual(a.gates["completeness"], "PASS")
        self.assertEqual(a.gates["source"], "PASS")
        self.assertEqual(a.gates["materiality"], "PASS")
        self.assertEqual(a.gates["calculation"], "PASS")
        self.assertEqual(a.gates["rights"], "PASS")
        self.assertEqual(a.gates["security"], "PASS")
        self.assertTrue(a.gates["coverage"].startswith("PASS"),
                        "覆盖门须报适用门数")

    # ── materially critical Claim 缺证据边 → 不 eligible ───────────
    def test_critical_claim_gap_blocks(self):
        m = self._manifest()
        claim_obj = next(oid for oid, meta in m["objects"].items()
                         if meta.get("kind") == "claim")
        m["objects"][claim_obj] = {"kind": "claim", "refs": []}   # 变异：删证据边
        m["id"] = fx.content_id(m)
        a = audit_candidate(self.store, m)
        self.assertFalse(a.release_eligible)
        self.assertEqual(a.gates["materiality"], "FAIL")

    # ── 来源权利 UNKNOWN → fail-closed ─────────────────────────────
    def test_unknown_source_blocks(self):
        m = self._manifest()
        ev = next(oid for oid, meta in m["objects"].items()
                  if meta.get("kind") == "evidence")
        obj = json.loads(self.store.load(ev).decode("utf-8"))
        obj["rights_verdict"] = "UNKNOWN"      # 变异：来源未判定
        from publish_engine import content_id, freeze_object
        new_id = freeze_object(self.store, "evidence", obj)   # 规范字节冻结
        m["objects"][new_id] = {"kind": "evidence", "refs": []}
        del m["objects"][ev]
        m["id"] = content_id(m)
        a = audit_candidate(self.store, m)
        self.assertFalse(a.release_eligible)
        self.assertEqual(a.gates["source"], "FAIL")

    # ── 计算输入未冻结 → 不 eligible ───────────────────────────────
    def test_calc_unfrozen_input_blocks(self):
        m = self._manifest()
        calc = next(oid for oid, meta in m["objects"].items()
                    if meta.get("kind") == "calc")
        m["objects"][calc] = {"kind": "calc", "refs": ["f" * 64]}  # 变异：输入未登记
        m["id"] = fx.content_id(m)
        a = audit_candidate(self.store, m)
        self.assertFalse(a.release_eligible)
        self.assertEqual(a.gates["calculation"], "FAIL")


class TestNbsAttribution(unittest.TestCase):
    """D-12/D-13：国家统计局强制署名（OI-PF-037，义务非可选项）。"""

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self.store = ArtifactStore(os.path.join(self._tmp, "lib"))

    def tearDown(self):
        shutil.rmtree(self._tmp, ignore_errors=True)

    # ── D-12 先红：含 stats.gov.cn 数据、缺署名 → FAIL ─────────────
    def test_missing_attribution_fails(self):
        body = "fixture 报告正文（全部数值合成）"
        head = "\n".join(["# fixture 研究报告", "研究信息不构成投资建议。", body])
        with self.assertRaises(ValueError) as cm:
            attribution_guard(head, ["www.stats.gov.cn"])
        self.assertIn("E-G4-02-009", str(cm.exception))

    # ── D-12 后绿：署名齐备 → PASS ─────────────────────────────────
    def test_with_attribution_passes(self):
        head = "# fixture 研究报告\n转自国家统计局网站，www.stats.gov.cn\n"
        self.assertIsNone(attribution_guard(head, ["www.stats.gov.cn"]))

    # ── D-13：署名位置可机检 —— 首屏前 N 行外 → FAIL ───────────────
    def test_attribution_position_machine_checkable(self):
        lines = ["# fixture 研究报告"]
        lines += [f"fill-{i}" for i in range(PROMINENT_FIRST_LINES + 5)]
        lines.append("转自国家统计局网站，www.stats.gov.cn")  # 出首屏
        text = "\n".join(lines)
        with self.assertRaises(ValueError) as cm:
            attribution_guard(text, ["www.stats.gov.cn"])
        self.assertIn("E-G4-02-009", str(cm.exception))
        # 同文本把署名放到第 2 行 → 通过（首屏前 N 行 = 机检位置）
        fixed = ["# fixture 研究报告",
                 "转自国家统计局网站，www.stats.gov.cn"]
        fixed += [f"fill-{i}" for i in range(PROMINENT_FIRST_LINES + 3)]
        self.assertIsNone(attribution_guard("\n".join(fixed),
                                            ["www.stats.gov.cn"]))

    # ── 不使用统计局数据 → 署名义务不适用 ───────────────────────────
    def test_non_nbs_source_no_obligation(self):
        self.assertIsNone(attribution_guard("# x\nno attribution",
                                            ["www.sse.com.cn"]))

    # ── 行为级：缺署名使审计门 FAIL（不是只查字段存在）─────────────
    def test_missing_attribution_fails_audit_gate(self):
        key = __import__("publish_engine").CurrentKey(
            "a-share-single-company-research", "600089.SH")
        m = fx.minimal_closure(self.store, key, with_nbs=True)
        # 变异：把 report 对象换成无署名文本（内容寻址 → 新 id 重挂）
        report = next(oid for oid, meta in m["objects"].items()
                      if meta.get("kind") == "report")
        from publish_engine import content_id, freeze_object
        no_attr = {"schema_version": "1.0.0", "kind": "report",
                   "text": "# fixture 研究报告\n研究信息不构成投资建议。\n"}
        new_report = freeze_object(self.store, "report", no_attr)
        m["objects"][new_report] = {"kind": "report", "refs": []}
        del m["objects"][report]
        m["id"] = content_id(m)
        a = audit_candidate(self.store, m)
        self.assertFalse(a.release_eligible)
        self.assertEqual(a.gates["rights"], "FAIL")
        # 后绿：补上署名 → 恢复
        m2 = fx.minimal_closure(self.store, key, with_nbs=True)
        a2 = audit_candidate(self.store, m2)
        self.assertTrue(a2.release_eligible, a2.failures)


if __name__ == "__main__":
    unittest.main()
