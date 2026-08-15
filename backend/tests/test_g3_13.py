"""G3-13 验收测试：AssumptionProposal、ApprovalEvent 与不可变
AssumptionSnapshot。

基线：
  · 写权（LLM 无批准写权；批准仅人工 L12 端点语义）
  · payload hash、审批人、时点、拒绝记录
  · 拒绝项不进入计算
  · 批准 payload 任一字节变化即失效（变异注入：改 payload → INVALIDATED）
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
APP = os.path.join(os.path.dirname(__file__), "..", "app")
sys.path.insert(0, APP)

from assumption_snapshot import (  # noqa: E402
    AssumptionProposal, AssumptionRegistry, AssumptionSnapshot,
    ApprovalEvent, NoApprovalWrite, PayloadChanged, AssumptionError,
    PENDING, APPROVED, REJECTED, payload_hash,
)


class TestProposalAndWriteRights(unittest.TestCase):
    def setUp(self):
        self.reg = AssumptionRegistry()
        self.p = AssumptionProposal(
            "A-001", {"metric_id": "营收增速", "value": "8%",
                      "basis": "行业历史增速"}, proposed_by="L8")
        self.reg.propose(self.p)

    def test_proposal_pending_with_hash(self):
        self.assertEqual(self.p.status, PENDING)
        self.assertEqual(self.p.payload_sha256, payload_hash(self.p.payload))

    def test_duplicate_rejected(self):
        with self.assertRaises(AssumptionError) as ctx:
            self.reg.propose(AssumptionProposal("A-001", {"x": 1}, "L8"))
        self.assertIn("E-G3-13-001", str(ctx.exception))

    def test_llm_no_approval_write(self):
        """LLM 无批准写权。"""
        for bad in ("LLM", "AUTOMATION", "L8", "L9"):
            with self.assertRaises(NoApprovalWrite) as ctx:
                self.reg.decide("A-001", APPROVED, bad, "2026-08-11T06:00:00Z",
                                "APPROVE")
            self.assertIn("E-G3-13-003", str(ctx.exception))

    def test_approval_requires_explicit_token(self):
        """聊天“继续”不算批准：token 必须显式 APPROVE。"""
        with self.assertRaises(AssumptionError) as ctx:
            self.reg.decide("A-001", APPROVED, "U", "2026-08-11T06:00:00Z",
                            "继续")
        self.assertIn("E-G3-13-007", str(ctx.exception))


class TestDecideAndSnapshot(unittest.TestCase):
    def test_approved_enters_snapshot(self):
        reg = AssumptionRegistry()
        p1 = AssumptionProposal("A-1", {"g": "8%"}, "L8")
        p2 = AssumptionProposal("A-2", {"g": "12%"}, "L8")
        reg.propose(p1)
        reg.propose(p2)
        # A-1 批准，A-2 拒绝
        reg.decide("A-1", APPROVED, "U", "2026-08-11T06:00:00Z", "APPROVE")
        reg.decide("A-2", REJECTED, "U", "2026-08-11T06:01:00Z", "REJECT",
                   rejection_reason="过于乐观")
        snap = AssumptionSnapshot("SNAP-1").build(reg)
        self.assertIn("A-1", snap.approved_payloads())
        self.assertNotIn("A-2", snap.approved_payloads(),
                         "拒绝项不进入计算")
        self.assertFalse(snap.invalidated)

    def test_rejection_recorded(self):
        reg = AssumptionRegistry()
        p = AssumptionProposal("A-3", {"x": 1}, "L8")
        reg.propose(p)
        reg.decide("A-3", REJECTED, "U", "2026-08-11T06:00:00Z", "REJECT",
                   rejection_reason="缺证据")
        self.assertEqual(p.status, REJECTED)
        self.assertEqual(p.rejection_reason, "缺证据")
        snap = AssumptionSnapshot("S").build(reg)
        self.assertEqual(snap.approved_payloads(), {})

    def test_tampered_payload_invalidates(self):
        """变异注入：批准后 payload 任一字节变化 → 快照失效，不得进入计算。"""
        reg = AssumptionRegistry()
        p = AssumptionProposal("A-4", {"g": "8%"}, "L8")
        reg.propose(p)
        reg.decide("A-4", APPROVED, "U", "2026-08-11T06:00:00Z", "APPROVE")
        # 篡改 payload（批准锚定的是旧哈希）
        p.payload["g"] = "80%"   # 字节变化
        snap = AssumptionSnapshot("S2").build(reg)
        self.assertTrue(snap.invalidated, "批准 payload 变化必须失效")
        with self.assertRaises(PayloadChanged) as ctx:
            snap.approved_payloads()
        self.assertIn("E-G3-13-010", str(ctx.exception))

    def test_build_unserializable_drift_fails_closed(self):
        """批准后、build 前 payload 漂移为不可 JSON 序列化值（set）：
        不得泄漏裸 TypeError —— build 置失效、坏 payload 不进入正文，
        sha256/approved_payloads 均抛 PayloadChanged E-G3-13-010
        （与可序列化漂移同一可机检合同）。"""
        reg = AssumptionRegistry()
        p = AssumptionProposal("A-X", {"x": "1"}, "L8")
        reg.propose(p)
        reg.decide("A-X", APPROVED, "U", "2026-08-15T00:00:00Z", "APPROVE")
        p.payload["x"] = {"bad"}  # 批准后、build 前改为不可 JSON 序列化值
        snap = AssumptionSnapshot("S").build(reg)  # 不得抛裸 TypeError
        self.assertTrue(snap.invalidated, "不可序列化漂移必须置失效")
        self.assertNotIn("A-X", snap.approved, "坏 payload 不得进入快照正文")
        for accessor in (lambda: snap.sha256, snap.approved_payloads):
            with self.assertRaises(PayloadChanged) as ctx:
                accessor()
            self.assertIn("E-G3-13-010", str(ctx.exception))

    def test_hash_bound_to_proposal(self):
        reg = AssumptionRegistry()
        p = AssumptionProposal("A-5", {"k": "v"}, "L8")
        reg.propose(p)
        reg.decide("A-5", APPROVED, "U", "2026-08-11T06:00:00Z", "APPROVE")
        ev = reg.events[-1]
        self.assertEqual(ev.payload_sha256, p.payload_sha256)
        self.assertEqual(ev.approver, "U")
        self.assertIsNotNone(ev.decided_at)

    def test_approval_event_not_snapshotized_after_drift(self):
        """快照冻结后 proposal 变更不影响已冻结快照（不可变）。"""
        reg = AssumptionRegistry()
        p = AssumptionProposal("A-6", {"g": "8%"}, "L8")
        reg.propose(p)
        reg.decide("A-6", APPROVED, "U", "2026-08-11T06:00:00Z", "APPROVE")
        snap = AssumptionSnapshot("S3").build(reg)
        sha_before = snap.sha256
        p.payload["g"] = "9%"  # 冻结后篡改
        # 已冻结快照字节不变（不可变）
        self.assertEqual(snap.sha256, sha_before)


if __name__ == "__main__":
    unittest.main()
