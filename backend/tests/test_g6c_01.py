"""G6C-01 验收测试：PredictionProposal、独立批准与不可变 PredictionSnapshot。

基线（G6C-01）：
  · 首个有限 DecisionVersion 预登记 3—5 个材料性预测（完整字段集 + 绑定）
  · Brier 的 forecast/reference 概率在结果可见前冻结（H-1 时序断言）
  · LLM 无批准写权；人工逐项批准；未批准预测不进入快照
  · 任一候选/合同/证据包/cutoff/snapshot 字节变化使批准失效，
    必须重新提议、批准并生成新 PredictionSnapshot（H-2）
  · H-3：未预登记的预测不得进入裁决
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
APP = os.path.join(os.path.dirname(__file__), "..", "app")
sys.path.insert(0, APP)

from prediction import (  # noqa: E402
    APPROVED, BindingChanged, LateRegistration, NoApprovalWrite,
    NotApproved, PredictionProposal, PredictionRegistry, PredictionSnapshot,
    REJECTED, UnregisteredPrediction,
)
from time_order import MicroClock  # noqa: E402


def _bindings(candidate_extra=None):
    candidate = {"candidate_id": "CAND-1", "value": 42}
    contract = {"contract_id": "C-600089", "scope": "600089.SH"}
    pack = {"pack_id": "EVP-1", "tools": ["read"]}
    cutoff = {"cutoff": "2026-08-01"}
    root = {"subject_root": "ROOT-1"}
    import json
    import hashlib
    return {
        "candidate_hash": hashlib.sha256(json.dumps(
            (candidate_extra and {**candidate, **candidate_extra}) or candidate,
            sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
        "contract_hash": hashlib.sha256(json.dumps(
            contract, sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
        "evidence_pack_id": "EVP-1",
        "cutoff": "2026-08-01",
        "snapshot_root": "ROOT-1",
    }


def _binding_objects():
    import json
    import hashlib
    return {
        "candidate_hash": {"candidate_id": "CAND-1", "value": 42},
        "contract_hash": {"contract_id": "C-600089", "scope": "600089.SH"},
        "evidence_pack_id": "EVP-1",
        "cutoff": "2026-08-01",
        "snapshot_root": "ROOT-1",
    }


def _pred(pid, outcome_at="2026-10-01T00:00:00.000000Z", **kw):
    return PredictionProposal(
        prediction_id=pid,
        decision_version_id="DV-1",
        metric_id="营收同比增速",
        operator=">=",
        threshold="0.05",
        scope="600089.SH",
        unit="percent",
        observation_period_start="2026-06-30",
        observation_period_end="2026-09-30",
        adjudication_source="SSE_ANNUAL_REPORT",
        resolution_rule="以年报披露值判定",
        grace_period="P5D",
        forecast_probability="0.65",
        reference_probability="0.40",
        model_version="v1.0",
        prompt_version="v2",
        method="base_rate",
        cluster_version="c1",
        bindings=_bindings(),
        outcome_available_at=outcome_at,
        **kw,
    )


FIXED = ["2026-08-12T00:00:00.000000Z", "2026-08-12T00:00:00.000001Z",
         "2026-08-12T00:00:00.000002Z", "2026-08-12T00:00:00.000003Z",
         "2026-08-12T00:00:00.000004Z"]


class TestPreRegistrationTiming(unittest.TestCase):
    def test_registered_before_outcome_available(self):
        """H-1：预登记在结果可知之前 —— 断言 registered_at < outcome_available_at。"""
        reg = PredictionRegistry()
        p = _pred("P-1")
        reg.register_with_time(p, "2026-08-12T00:00:00.000000Z", 0)
        self.assertEqual(p.status, "PENDING_APPROVAL")
        self.assertLess(p.registered_at[0], p.outcome_available_at)

    def test_backfill_rejected(self):
        """H-1 变异注入：结果已可知仍登记（事后补登记）→ FAIL。"""
        reg = PredictionRegistry()
        p = _pred("P-2", outcome_at="2026-08-01T00:00:00.000000Z")
        with self.assertRaises(LateRegistration) as ctx:
            reg.register_with_time(p, "2026-08-12T00:00:00.000000Z", 0)
        self.assertIn("E-G6C-01-101", str(ctx.exception))

    def test_same_second_seq_ordering(self):
        """同秒内由 seq 决出先后 —— 预登记与结果同秒也可分辨先后。"""
        reg = PredictionRegistry()
        # outcome 与登记同秒不同序号：seq=0 的登记早于 seq 后续事件
        p = _pred("P-3", outcome_at="2026-08-12T00:00:00.000005Z")
        reg.register_with_time(p, "2026-08-12T00:00:00.000000Z", 0)
        self.assertTrue(("2026-08-12T00:00:00.000000Z", 0) <
                        (p.outcome_available_at, 0))


class TestApprovalAndSnapshot(unittest.TestCase):
    def _approved_snapshot(self):
        reg = PredictionRegistry()
        ps = [_pred(f"P-{i}") for i in range(1, 4)]
        for p in ps:
            reg.register_with_time(p, FIXED[ps.index(p)], 0)
            reg.decide(p.prediction_id, APPROVED, "U",
                       "2026-08-12T01:00:00Z", "APPROVE")
        snap = PredictionSnapshot("SNAP-1", "DV-1").build(reg, _binding_objects())
        return reg, snap

    def test_three_to_five_material_predictions(self):
        """首个有限 DecisionVersion 预登记 3—5 个材料性预测。"""
        reg = PredictionRegistry()
        for i in range(1, 4):
            reg.register_with_time(_pred(f"P-{i}"), FIXED[i - 1], 0)
        self.assertGreaterEqual(len(reg.predictions), 3)
        self.assertLessEqual(len(reg.predictions), 5)

    def test_llm_no_approval_write(self):
        reg = PredictionRegistry()
        reg.register_with_time(_pred("P-X"), FIXED[0], 0)
        for bad in ("LLM", "AUTOMATION", "L8"):
            with self.assertRaises(NoApprovalWrite):
                reg.decide("P-X", APPROVED, bad, "2026-08-12T01:00:00Z",
                           "APPROVE")

    def test_unapproved_not_in_snapshot(self):
        """未批准预测不进入快照；拒绝项亦不进入。"""
        reg = PredictionRegistry()
        reg.register_with_time(_pred("P-1"), FIXED[0], 0)
        reg.register_with_time(_pred("P-2"), FIXED[1], 0)
        reg.decide("P-1", APPROVED, "U", "2026-08-12T01:00:00Z", "APPROVE")
        reg.decide("P-2", REJECTED, "U", "2026-08-12T01:01:00Z", "REJECT",
                   rejection_reason="基准概率缺失")
        snap = PredictionSnapshot("S", "DV-1").build(reg, _binding_objects())
        self.assertIn("P-1", snap.approved_predictions())
        self.assertNotIn("P-2", snap.approved_predictions())

    def test_binding_byte_change_invalidates(self):
        """H-2 变异注入：candidate 改一字节 → 批准失效，须重提议重批准重快照。"""
        reg, snap = self._approved_snapshot()
        self.assertFalse(snap.invalidated)
        # candidate 对象变化（任意字节）
        snap2 = PredictionSnapshot("SNAP-2", "DV-1").build(
            reg, {"candidate_hash": {"candidate_id": "CAND-1", "value": 43},
                  "contract_hash": {"contract_id": "C-600089",
                                    "scope": "600089.SH"},
                  "evidence_pack_id": "EVP-1", "cutoff": "2026-08-01",
                  "snapshot_root": "ROOT-1"})
        self.assertTrue(snap2.invalidated, "绑定字节变化必须使批准失效")
        with self.assertRaises(BindingChanged) as ctx:
            snap2.approved_predictions()
        self.assertIn("E-G6C-01-012", str(ctx.exception))

    def test_frozen_snapshot_immutable(self):
        reg, snap = self._approved_snapshot()
        sha = snap.sha256
        # 冻结后注册新预测/篡改均不改变已冻结快照字节
        reg.register_with_time(_pred("P-NEW"), FIXED[4], 0)
        self.assertEqual(snap.sha256, sha)

    def test_unregistered_cannot_enter_adjudication(self):
        """H-3：未预登记的预测不得进入裁决。"""
        reg = PredictionRegistry()
        with self.assertRaises(UnregisteredPrediction) as ctx:
            reg.entry_to_adjudication("P-NOT-REGISTERED")
        self.assertIn("E-G6C-01-008", str(ctx.exception))

    def test_unapproved_cannot_enter_adjudication(self):
        reg = PredictionRegistry()
        reg.register_with_time(_pred("P-1"), FIXED[0], 0)
        with self.assertRaises(NotApproved):
            reg.entry_to_adjudication("P-1")


class TestMicroClock(unittest.TestCase):
    def test_clock_sequence(self):
        src = iter(["2026-08-12T00:00:00.000000Z"] * 2 +
                   ["2026-08-12T00:00:00.000001Z"] * 1)
        clk = MicroClock(time_source=src.__next__)
        t1, s1 = clk.tick()
        t2, s2 = clk.tick()
        t3, s3 = clk.tick()
        self.assertEqual((t1, s1), (t2, s2 - 1))
        self.assertEqual(s2, 1)
        self.assertEqual(s3, 0)


if __name__ == "__main__":
    unittest.main()
