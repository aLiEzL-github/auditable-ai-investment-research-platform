"""G6C-02 验收测试：结果裁决、逾期/不可裁决、重述处理与 CalibrationStore。

基线（G6C-02）：
  · OPEN/RESOLVED/OVERDUE/UNRESOLVABLE、裁决证据、宽限期、重述政策、
    选择性未决检测、不可变得分输入
  · 未到期/不可裁决/来源不足时不伪造 outcome；到期未裁决进入 OVERDUE；
    UNRESOLVABLE 有证据；重述不回写历史；材料性选择性未决阻断能力声明

执行计划（G6C-执行计划.md §4）：
  H-4  后见基准必拒（一票否决）：基准输入可得时刻晚于预登记时刻即拒
  H-5  裁决可回溯至预登记记录与基准数据，无孤儿裁决
  H-6  共识不等于已验证（一票否决）：字段级可分辨（outcome_kind）
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
APP = os.path.join(os.path.dirname(__file__), "..", "app")
sys.path.insert(0, APP)

from adjudication import (  # noqa: E402
    CalibrationStore, ConsensusAsFact, HindsightBenchmark, ImmutableScoreInput,
    OPEN, OVERDUE, RESOLVED, UNRESOLVABLE, UNRESOLVABLE, VERIFIED, CONSENSUS,
    AdjudicationRegistry, BenchmarkInput, UNAVAILABLE,
)
from prediction import (  # noqa: E402
    APPROVED, PredictionProposal, PredictionRegistry, PredictionSnapshot,
)
from time_order import MicroClock  # noqa: E402


def _mk_prediction(pid="P-1", obs_end="2026-09-30", grace="P5D",
                   outcome_at="2026-10-01T00:00:00.000000Z", **kw):
    import json
    import hashlib
    return PredictionProposal(
        prediction_id=pid, decision_version_id="DV-1",
        metric_id="营收同比增速", operator=">=", threshold="0.05",
        scope="600089.SH", unit="percent",
        observation_period_start="2026-06-30", observation_period_end=obs_end,
        adjudication_source="SSE_ANNUAL_REPORT",
        resolution_rule="以年报披露值判定", grace_period=grace,
        forecast_probability="0.65", reference_probability="0.40",
        model_version="v1.0", prompt_version="v2", method="base_rate",
        cluster_version="c1",
        bindings={
            "candidate_hash": hashlib.sha256(b"cand").hexdigest(),
            "contract_hash": hashlib.sha256(b"contract").hexdigest(),
            "evidence_pack_id": "EVP-1", "cutoff": "2026-08-01",
            "snapshot_root": "ROOT-1"},
        outcome_available_at=outcome_at, **kw)


REG_TS = "2026-08-12T00:00:00.000000Z"


def _adjudication_setup(pred_ids=("P-1",)):
    reg = PredictionRegistry()
    for i, pid in enumerate(pred_ids):
        reg.register_with_time(_mk_prediction(pid), REG_TS, i)
        reg.decide(pid, APPROVED, "U", "2026-08-12T01:00:00Z", "APPROVE")
    adjs = AdjudicationRegistry(reg)
    return reg, adjs


def _benchmark(key, ts=REG_TS, seq=0, value="0.08"):
    return BenchmarkInput(key=key, available_at=(ts, seq), value=value)


class TestAdjudicate(unittest.TestCase):
    def test_resolved_with_evidence_and_benchmark(self):
        reg, adjs = _adjudication_setup()
        rec = adjs.adjudicate("P-1", [_benchmark("revenue")], "0.08",
                              VERIFIED, "年报披露值", "2026-10-06T00:00:00Z")
        self.assertEqual(rec.status, RESOLVED)
        self.assertEqual(rec.outcome_kind, VERIFIED)
        self.assertRegex(rec.benchmark_sha256, r"^[0-9a-f]{64}$",
                         "基准哈希应为 sha256 十六进制（非裁决 id）")

    def test_insufficient_source_rejected(self):
        """来源不足不得裁决：无基准数据 / 无证据都须拒绝。"""
        reg, adjs = _adjudication_setup()
        with self.assertRaises(Exception) as ctx:
            adjs.adjudicate("P-1", [], "0.08", VERIFIED, "证据",
                            "2026-10-06T00:00:00Z")
        self.assertIn("E-G6C-02-002", str(ctx.exception))
        with self.assertRaises(Exception) as ctx:
            adjs.adjudicate("P-1", [_benchmark("revenue")], "0.08", VERIFIED,
                            "", "2026-10-06T00:00:00Z")
        self.assertIn("E-G6C-02-003", str(ctx.exception))

    def test_not_mature_rejected(self):
        """未到期不得裁决（观察期末 + 宽限期之前）。"""
        reg, adjs = _adjudication_setup()
        with self.assertRaises(Exception) as ctx:
            adjs.adjudicate("P-1", [_benchmark("revenue")], "0.08", VERIFIED,
                            "证据", "2026-09-20T00:00:00Z")
        self.assertIn("E-G6C-02-006", str(ctx.exception))

    def test_hindsight_benchmark_rejected(self):
        """H-4 一票否决：基准输入可得时刻晚于预登记时刻 → 拒。"""
        reg, adjs = _adjudication_setup()
        late = _benchmark("revenue", ts="2026-08-13T00:00:00.000000Z", seq=0)
        with self.assertRaises(HindsightBenchmark) as ctx:
            adjs.adjudicate("P-1", [late], "0.08", VERIFIED, "证据",
                            "2026-10-06T00:00:00Z")
        self.assertIn("E-G6C-02-101", str(ctx.exception))

    def test_unregistered_prediction_orphan_impossible(self):
        """H-5：未预登记的预测无法裁决（孤儿裁决结构性不可达）。"""
        reg, adjs = _adjudication_setup()
        with self.assertRaises(Exception) as ctx:
            adjs.adjudicate("P-NO", [_benchmark("revenue")], "0.08", VERIFIED,
                            "证据", "2026-10-06T00:00:00Z")
        self.assertIn("E-G6C-01-008", str(ctx.exception))


class TestOverdueAndUnresolvable(unittest.TestCase):
    def test_overdue_after_expiry(self):
        reg, adjs = _adjudication_setup()
        rec = adjs.advance_to_overdue("P-1", now="2026-10-06T00:00:00.000000Z")
        self.assertIsNotNone(rec)
        self.assertEqual(rec.status, OVERDUE)

    def test_no_outcome_fabrication_for_overdue(self):
        """到期未裁决进入 OVERDUE —— 不伪造 outcome。"""
        reg, adjs = _adjudication_setup()
        rec = adjs.advance_to_overdue("P-1", now="2026-10-06T00:00:00.000000Z")
        self.assertIsNone(rec.outcome)
        self.assertIsNone(rec.outcome_kind)

    def test_not_yet_due_stays_open(self):
        reg, adjs = _adjudication_setup()
        rec = adjs.advance_to_overdue("P-1", now="2026-09-15T00:00:00.000000Z")
        self.assertIsNone(rec, "未到期不得进入 OVERDUE")

    def test_unresolvable_requires_evidence(self):
        reg, adjs = _adjudication_setup()
        with self.assertRaises(Exception) as ctx:
            adjs.mark_unresolvable("P-1", "")
        self.assertIn("E-G6C-02-008", str(ctx.exception))
        rec = adjs.mark_unresolvable("P-1", "公告撤回，无判定来源")
        self.assertEqual(rec.status, UNRESOLVABLE)
        self.assertTrue(rec.evidence)


class TestRestatement(unittest.TestCase):
    def test_restatement_no_history_rewrite(self):
        reg, adjs = _adjudication_setup()
        rec1 = adjs.adjudicate("P-1", [_benchmark("revenue")], "0.08",
                               VERIFIED, "初版披露", "2026-10-06T00:00:00Z")
        before = rec1.to_dict()
        rec2 = adjs.restate(rec1.adjudication_id, "0.09", VERIFIED,
                            "重述后披露", "2026-11-01T00:00:00Z")
        self.assertEqual(rec2.restatement_of, rec1.adjudication_id)
        # 旧记录逐字未动（不回写历史）
        self.assertEqual(rec1.to_dict(), before)
        self.assertEqual(rec1.outcome, "0.08")
        self.assertEqual(rec2.outcome, "0.09")


class TestConsensusVsFact(unittest.TestCase):
    def test_consensus_cannot_be_used_as_fact(self):
        """H-6 一票否决：共识结果不得当作已验证事实使用（字段级拒绝）。"""
        reg, adjs = _adjudication_setup()
        rec = adjs.adjudicate("P-1", [_benchmark("revenue")], "0.08",
                              CONSENSUS, "多 Agent 一致", "2026-10-06T00:00:00Z")
        with self.assertRaises(ConsensusAsFact) as ctx:
            adjs.consume_as_fact(rec.adjudication_id)
        self.assertIn("E-G6C-02-103", str(ctx.exception))

    def test_verified_can_be_used_as_fact(self):
        reg, adjs = _adjudication_setup()
        rec = adjs.adjudicate("P-1", [_benchmark("revenue")], "0.08",
                              VERIFIED, "年报披露值", "2026-10-06T00:00:00Z")
        self.assertEqual(adjs.consume_as_fact(rec.adjudication_id), "0.08")

    def test_kind_field_distinguishable(self):
        """共识与已验证在数据模型上可分辨（字段级，非文字提醒）。"""
        reg, adjs = _adjudication_setup()
        c = adjs.adjudicate("P-1", [_benchmark("revenue")], "0.08", CONSENSUS,
                            "多 Agent 一致", "2026-10-06T00:00:00Z")
        self.assertEqual(c.outcome_kind, CONSENSUS)
        self.assertNotEqual(c.outcome_kind, VERIFIED)


class TestSelectiveNonResolution(unittest.TestCase):
    def test_material_selective_nonresolution_blocks(self):
        """材料性选择性未决：部分已裁决、部分未决 → 阻断能力声明。"""
        reg, adjs = _adjudication_setup(pred_ids=("P-1", "P-2"))
        adjs.adjudicate("P-1", [_benchmark("revenue")], "0.08", VERIFIED,
                        "年报披露值", "2026-10-06T00:00:00Z")
        blocked = adjs.selective_non_resolution(["P-1", "P-2"])
        self.assertEqual(blocked, ["P-2"])
        self.assertTrue(blocked, "选择性未决必须阻断能力声明")

    def test_all_resolved_no_block(self):
        reg, adjs = _adjudication_setup(pred_ids=("P-1", "P-2"))
        for pid in ("P-1", "P-2"):
            adjs.adjudicate(pid, [_benchmark("revenue")], "0.08", VERIFIED,
                            "年报披露值", "2026-10-06T00:00:00Z")
        self.assertEqual(adjs.selective_non_resolution(["P-1", "P-2"]), [])


class TestCalibrationStore(unittest.TestCase):
    def test_immutable_scoring_inputs(self):
        reg, adjs = _adjudication_setup()
        adjs.adjudicate("P-1", [_benchmark("revenue")], "0.08", VERIFIED,
                        "年报披露值", "2026-10-06T00:00:00Z")
        store = CalibrationStore(adjs, {})
        store.seal()
        with self.assertRaises(ImmutableScoreInput):
            adjs.mark_unresolvable("P-1", "证据")
            store.add(adjs.records[
                [k for k in adjs.records if "UNRESOLVABLE" in k][0]])

    def test_score_inputs_require_seal(self):
        reg, adjs = _adjudication_setup()
        adjs.adjudicate("P-1", [_benchmark("revenue")], "0.08", VERIFIED,
                        "年报披露值", "2026-10-06T00:00:00Z")
        store = CalibrationStore(adjs, {})
        with self.assertRaises(Exception):
            store.scoring_inputs()

    def test_orphan_adjudication_detected(self):
        """H-5：不指向预登记记录的裁决 → CalibrationStore 加载即拒。"""
        reg, adjs = _adjudication_setup()
        from adjudication import AdjudicationRecord
        orphan = AdjudicationRecord("ADJ-ORPHAN", "P-NO", status=RESOLVED,
                                    outcome="0.08", outcome_kind=VERIFIED,
                                    adjudicated_at="2026-10-06T00:00:00Z",
                                    evidence="x", benchmark_sha256="h")
        adjs.records[orphan.adjudication_id] = orphan
        with self.assertRaises(Exception) as ctx:
            CalibrationStore(adjs, {})
        self.assertIn("E-G6C-02-102", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
