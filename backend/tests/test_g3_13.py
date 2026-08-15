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


class TestSnapshotPreBuildContaminationFailClosed(unittest.TestCase):
    """OI-PF-201：内部批准正文与状态字段不得作为公开构造入参；
    build 前被直接预置/篡改 → E-G3-13-011 失败关闭，不保留不清空不洗白。

    原失败载荷：`AssumptionSnapshot(id, approved={...})` 把未批准正文直接
    构造进快照，或 build 前 `snap.approved['A-FAKE']={...}` —— 原 build 只
    从事件累加批准项，预置的未批准项原样保留进正文并随 values() 进入计算。
    """

    def test_snapshot_constructor_rejects_prepopulated_approved(self):
        """构造函数只接受 snapshot_id/version —— 预置 approved 一律 TypeError：
        位置参数与关键字参数均不得进入（内部字段不是公开构造入参）。"""
        with self.assertRaises(TypeError):
            AssumptionSnapshot("S", 1, {"A-FAKE": {"growth": "0.99"}})
        with self.assertRaises(TypeError):
            AssumptionSnapshot("S", approved={"A-FAKE": {"growth": "0.99"}})
        with self.assertRaises(TypeError):
            AssumptionSnapshot("S", _invalidated=True)
        with self.assertRaises(TypeError):
            AssumptionSnapshot("S", _sha256="0" * 64)

    def test_snapshot_build_rejects_mutated_prebuild_approved(self):
        """build 前直接篡改内部字段 → E-G3-13-011 失败关闭，快照不可用：
        已批准项不得被洗白、未批准预置项不得进入正文。"""
        reg = AssumptionRegistry()
        p = AssumptionProposal("A-OK", {"g": "8%"}, "L8")
        reg.propose(p)
        reg.decide("A-OK", APPROVED, "U", "2026-08-15T00:00:00Z", "APPROVE")
        mutations = [
            ("approved 预置未批准项",
             lambda s: s.approved.__setitem__("A-FAKE", {"growth": "0.99"})),
            ("_invalidated 预置为 True",
             lambda s: setattr(s, "_invalidated", True)),
            ("_sha256 预置非空",
             lambda s: setattr(s, "_sha256", "0" * 64)),
        ]
        for label, mutate in mutations:
            with self.subTest(mutation=label):
                snap = AssumptionSnapshot("S")
                mutate(snap)
                with self.assertRaises(AssumptionError) as ctx:
                    snap.build(reg)
                self.assertIn("E-G3-13-011", str(ctx.exception),
                              f"{label} 必须 E-G3-13-011 失败关闭")
                self.assertFalse(snap._frozen, "被污染的 build 不得冻结快照")
                with self.assertRaises(AssumptionError):
                    snap.sha256   # 不可用：未冻结/被拒，不得产出可用哈希
                with self.assertRaises(AssumptionError):
                    snap.approved_payloads()   # 不可用：预置正文不得读出
                self.assertNotIn("A-OK", snap.approved,
                                 "被拒 build 不得把已批准项并入预置正文（不洗白）")

    def test_empty_registry_builds_empty_snapshot(self):
        """注册表零批准事件 → 空批准集快照，正文可用且不失效。"""
        reg = AssumptionRegistry()
        snap = AssumptionSnapshot("S-EMPTY").build(reg)
        self.assertFalse(snap.invalidated)
        self.assertEqual(snap.approved_payloads(), {},
                         "零批准事件的注册表必须产出空批准集")
        self.assertRegex(snap.sha256, r"^[0-9a-f]{64}$",
                         "空正文快照哈希须可用（空集是合法态）")
        # 干净空快照 build 可重复冻结（无污染即通过）
        self.assertEqual(AssumptionSnapshot("S-EMPTY-2").build(reg)
                         .approved_payloads(), {})


class TestApprovedKeyCollisionFailClosed(unittest.TestCase):
    """OI-PF-206：批准假设键全局唯一性。

    原失败载荷：两个独立 APPROVED proposal 携带同一假设键（如 A-1 与 A-2
    都批准 {"growth": ...}），build 把两项都收进 approved，ResearchContext.
    values() 按事件顺序静默采用最后写入值 —— last-write-wins 且能直接改变
    估值输入。本类证明：冲突 build 必须 E-G3-13-012 失败关闭、快照永久失效、
    不把部分批准正文留在 snapshot，且错误与批准顺序无关。
    """

    def _conflict_registry(self, order):
        """批准顺序可变的冲突注册表：A-1 与 A-2 均批准同键 growth。"""
        reg = AssumptionRegistry()
        p1 = AssumptionProposal("A-1", {"growth": "0.05"}, proposed_by="L8")
        p2 = AssumptionProposal("A-2", {"growth": "0.20"}, proposed_by="L8")
        reg.propose(p1)
        reg.propose(p2)
        for i, pid in enumerate(order):
            reg.decide(pid, APPROVED, "U",
                       f"2026-08-12T12:00:0{i}Z", "APPROVE")
        return reg

    def test_conflicting_approved_keys_fail_closed(self):
        """同一假设键来自两个不同批准 proposal → build 抛 E-G3-13-012 点名
        冲突键与 proposal id；快照永久失效且不残留部分批准正文；
        sha256/approved_payloads 均拒绝。"""
        snap = AssumptionSnapshot("SNAP-CONFLICT")
        with self.assertRaises(AssumptionError) as cm:
            snap.build(self._conflict_registry(["A-1", "A-2"]))
        msg = str(cm.exception)
        self.assertIn("E-G3-13-012", msg, "冲突必须用新稳定错误码失败关闭")
        self.assertIn("growth", msg, "错误必须点名冲突键")
        self.assertIn("A-1", msg, "错误必须点名冲突 proposal id")
        self.assertIn("A-2", msg, "错误必须点名冲突 proposal id")
        self.assertTrue(snap.invalidated, "冲突快照必须置为永久失效态")
        self.assertEqual(snap.approved, {},
                         "冲突时不得把部分批准正文留在 snapshot")
        with self.assertRaises(PayloadChanged):
            snap.sha256
        with self.assertRaises(PayloadChanged):
            snap.approved_payloads()

    def test_conflicting_approved_keys_order_reversal_same_error(self):
        """加入顺序反转变异：两种批准顺序都稳定拒绝同一错误码 E-G3-13-012，
        且错误文本逐字一致 —— 证明不再 last-write-wins（不得选胜者）。"""
        err_a = err_b = None
        snap_a = AssumptionSnapshot("SNAP-A")
        snap_b = AssumptionSnapshot("SNAP-B")
        with self.assertRaises(AssumptionError) as cm:
            snap_a.build(self._conflict_registry(["A-1", "A-2"]))
        err_a = str(cm.exception)
        with self.assertRaises(AssumptionError) as cm:
            snap_b.build(self._conflict_registry(["A-2", "A-1"]))
        err_b = str(cm.exception)
        self.assertIn("E-G3-13-012", err_a)
        self.assertIn("E-G3-13-012", err_b)
        self.assertEqual(err_a, err_b,
                         "冲突错误不得随批准顺序变化（无事件顺序选胜者）")
        self.assertTrue(snap_a.invalidated)
        self.assertTrue(snap_b.invalidated)

    def test_conflicting_keys_with_rejected_sibling_still_fails(self):
        """同键冲突 + 不同键的拒绝项：拒绝项不进入计算，不能“救援”冲突
        —— 仍 E-G3-13-012 失败关闭（拒绝项/冲突判定互不影响）。"""
        reg = AssumptionRegistry()
        p1 = AssumptionProposal("A-1", {"growth": "0.05"}, proposed_by="L8")
        p2 = AssumptionProposal("A-2", {"growth": "0.20"}, proposed_by="L8")
        p3 = AssumptionProposal("A-3", {"wacc": "0.09"}, proposed_by="L8")
        reg.propose(p1)
        reg.propose(p2)
        reg.propose(p3)
        reg.decide("A-1", APPROVED, "U", "2026-08-12T12:00:00Z", "APPROVE")
        reg.decide("A-3", REJECTED, "U", "2026-08-12T12:00:01Z", "REJECT",
                   rejection_reason="缺证据")
        reg.decide("A-2", APPROVED, "U", "2026-08-12T12:00:02Z", "APPROVE")
        snap = AssumptionSnapshot("SNAP-C")
        with self.assertRaises(AssumptionError) as cm:
            snap.build(reg)
        self.assertIn("E-G3-13-012", str(cm.exception))
        self.assertTrue(snap.invalidated)

    def test_multiple_approved_distinct_keys_ok(self):
        """不同键的多个批准 proposal（无冲突）→ 正常构建、不失效、正文完整。
        唯一性检查只拦同键，不误伤不同键的多项批准。"""
        reg = AssumptionRegistry()
        p1 = AssumptionProposal("A-1", {"growth": "0.08"}, proposed_by="L8")
        p2 = AssumptionProposal("A-2", {"wacc": "0.09"}, proposed_by="L8")
        reg.propose(p1)
        reg.propose(p2)
        reg.decide("A-1", APPROVED, "U", "2026-08-12T12:00:00Z", "APPROVE")
        reg.decide("A-2", APPROVED, "U", "2026-08-12T12:00:01Z", "APPROVE")
        snap = AssumptionSnapshot("SNAP-OK").build(reg)
        self.assertFalse(snap.invalidated)
        self.assertEqual(snap.approved_payloads(),
                         {"A-1": {"growth": "0.08"},
                          "A-2": {"wacc": "0.09"}})
        self.assertRegex(snap.sha256, r"^[0-9a-f]{64}$")


if __name__ == "__main__":
    unittest.main()
