"""G6C-03 验收测试：预登记基准、Brier/skill、分层校准与充分性门。

基线（G6C-03）：
  · base rate、Brier/reference Brier/skill、按 scope/horizon/model/prompt/
    method 分层、cluster-aware effective_n/CI、展示政策、机器可读状态
  · 只有 resolved≥30、≥2 报告期、≥2 horizon bucket、clustered effective_n≥20、
    CI 存在且无材料性选择性未决时为 CALIBRATION_SUFFICIENT；否则仅
    CALIBRATION_PENDING / INSUFFICIENT_SAMPLE，不得宣称预测能力

执行计划（G6C-执行计划.md §4）：
  H-7  充分性门：样本量不足阻断「已校准」表述（而非附警告输出）；负测
  H-8  CALIBRATION_PENDING 不得冒充能力（一票否决）：声称 → FAIL；先红后绿
  H-9  「未校准」（VD-26 决策）与「校准失败」（测量）可分辨
  H-10 阈值有据：逐字取用基线 B §10A（GATE_THRESHOLDS），不另设
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
APP = os.path.join(os.path.dirname(__file__), "..", "app")
sys.path.insert(0, APP)

from calibration import (  # noqa: E402
    CALIBRATION_PENDING, CALIBRATION_SUFFICIENT, DECISION_VD26, MEASUREMENT,
    INSUFFICIENT_SAMPLE, CalibrationClaimDenied, CalibrationStatus,
    assert_no_calibration_claim, base_rate, brier, brier_score,
    check_sufficiency, cluster_effective_n, horizon_bucket,
    reference_brier, render_for_display, skill_score, stratified_scores,
    wilson_ci,
)


def _score_inputs(n=5, scope="600089.SH", start="2026-06-30",
                  end="2026-09-30", prob=0.65, ref=0.40, outcome=1,
                  horizon_spread=False):
    """构造评分输入（pred_dict, adj_dict）对。"""
    out = {}
    for i in range(n):
        s = start
        e = end
        if horizon_spread:
            s = ["2026-06-30", "2026-03-31", "2026-01-01",
                 "2026-06-30", "2026-03-31"][i % 5]
            e = ["2026-09-30", "2026-06-30", "2026-04-30",
                 "2026-09-30", "2026-06-30"][i % 5]
        pred = {"prediction_id": f"P-{i}", "scope": scope,
                "observation_period_start": s, "observation_period_end": e,
                "forecast_probability": str(prob),
                "reference_probability": str(ref),
                "model_version": "v1", "prompt_version": "p1",
                "method": "base_rate", "cluster_version": "c1"}
        adj = {"outcome": str(outcome)}
        out[f"ADJ-{i}"] = (pred, adj)
    return out


class TestBrier(unittest.TestCase):
    def test_brier_basic(self):
        self.assertEqual(brier("1.0", 1), 0.0)
        self.assertEqual(brier("0.0", 1), 1.0)
        self.assertAlmostEqual(brier("0.5", 0), 0.25)

    def test_brier_score_mean(self):
        fs = ["1.0", "0.0"]
        os_ = [1, 0]
        self.assertEqual(brier_score(fs, os_), 0.0)
        self.assertAlmostEqual(brier_score(["1.0", "1.0"], [1, 0]), 0.5)

    def test_reference_and_skill(self):
        fs = ["0.6", "0.7", "0.5"]
        rs = ["0.5", "0.5", "0.5"]
        os_ = [1, 1, 0]
        b = brier_score(fs, os_)
        rb = reference_brier(rs, os_)
        self.assertEqual(skill_score(b, rb), 1.0 - b / rb)

    def test_zero_reference_skill_undefined(self):
        with self.assertRaises(Exception) as ctx:
            skill_score(0.5, 0.0)
        self.assertIn("E-G6C-03-005", str(ctx.exception))

    def test_base_rate(self):
        self.assertEqual(base_rate([1, 1, 0]), 2 / 3)


class TestStratification(unittest.TestCase):
    def test_horizon_buckets(self):
        self.assertEqual(horizon_bucket("2026-06-30", "2026-07-15"), "LT1M")
        self.assertEqual(horizon_bucket("2026-06-30", "2026-09-30"), "1-3M")
        self.assertEqual(horizon_bucket("2026-01-01", "2026-04-30"), "GT3M")

    def test_stratified_by_scope(self):
        si = _score_inputs()
        strata = stratified_scores(si, "scope")
        self.assertIn("600089.SH", strata)
        self.assertEqual(strata["600089.SH"]["n"], len(si))

    def test_stratified_by_horizon(self):
        si = _score_inputs(horizon_spread=True)
        strata = stratified_scores(si, "horizon")
        self.assertGreaterEqual(len(strata), 2)

    def test_unknown_dimension_rejected(self):
        with self.assertRaises(Exception) as ctx:
            stratified_scores({}, "nope")
        self.assertIn("E-G6C-03-006", str(ctx.exception))


class TestEffectiveNAndCI(unittest.TestCase):
    def test_cluster_effective_n_reduces(self):
        """同一簇内样本按簇平均规模折减（保守调整）。"""
        si = _score_inputs(n=10)   # 全部同一 (scope, horizon) 簇
        eff = cluster_effective_n(si)
        self.assertEqual(eff, 1.0)  # 单簇 → effective_n=1

    def test_multi_cluster(self):
        si = _score_inputs(n=6, horizon_spread=True)
        eff = cluster_effective_n(si)
        self.assertGreater(eff, 1.0)
        self.assertLess(eff, 6.0)

    def test_wilson_ci_empty_is_none(self):
        self.assertIsNone(wilson_ci([]))

    def test_wilson_ci_exists(self):
        ci = wilson_ci([1, 1, 0, 1])
        self.assertIsNotNone(ci)
        self.assertLess(ci["low"], ci["high"])


class TestSufficiencyGate(unittest.TestCase):
    def test_small_sample_insufficient_blocks_claim(self):
        """H-7 负测：样本不足 → INSUFFICIENT_SAMPLE，且「已校准」表述被阻断。"""
        st = check_sufficiency(_score_inputs(n=5), selective_unresolved=[])
        self.assertEqual(st.measurement_status, INSUFFICIENT_SAMPLE)
        self.assertFalse(st.gate["resolved>=30"])
        # 渲染出的展示文本不得含冒充能力的表述（H-8 行为断言）
        text = render_for_display(st)
        assert_no_calibration_claim(text, where="render_for_display")

    def test_sufficient_only_with_all_criteria(self):
        """H-10：阈值逐字取用基线 B §10A —— 全部满足才 SUFFICIENT。"""
        si = _score_inputs(n=30)
        st = check_sufficiency(si, selective_unresolved=[])
        # 30 条同报告期、同 horizon → 报告期/桶/effective_n 判据失败
        self.assertTrue(st.gate["resolved>=30"],
                        "30 条样本应满足 resolved>=30 判据（其余判据失败）")
        self.assertEqual(st.measurement_status, INSUFFICIENT_SAMPLE)

    def test_zero_sample_distinguishable(self):
        """⑨：「样本 0 条，门未触发」与「样本充足，门已通过」可分辨。"""
        st0 = check_sufficiency({}, selective_unresolved=[])
        self.assertEqual(st0.resolved, 0)
        self.assertFalse(st0.gate["resolved>=30"])
        self.assertEqual(st0.measurement_status, INSUFFICIENT_SAMPLE)
        self.assertIsNotNone(st0.to_dict()["gate_detail"])

    def test_selective_unresolved_blocks(self):
        st = check_sufficiency(_score_inputs(n=30),
                               selective_unresolved=["P-31"])
        self.assertFalse(st.gate["无材料性选择性未决"])
        self.assertEqual(st.measurement_status, INSUFFICIENT_SAMPLE)

    def test_thresholds_from_baseline(self):
        """H-10：阈值落库且与基线 B §10A 一致（不另设、无凭空的数字）。"""
        from calibration import GATE_THRESHOLDS
        self.assertEqual(GATE_THRESHOLDS["min_resolved"], 30)
        self.assertEqual(GATE_THRESHOLDS["min_reporting_periods"], 2)
        self.assertEqual(GATE_THRESHOLDS["min_horizon_buckets"], 2)
        self.assertEqual(GATE_THRESHOLDS["min_clustered_effective_n"], 20)


class TestPermanentPending(unittest.TestCase):
    def test_declared_always_pending(self):
        """VD-26：declared_status 恒为 CALIBRATION_PENDING（终态）。"""
        st = check_sufficiency(_score_inputs(n=30),
                               selective_unresolved=[])
        self.assertEqual(st.declared_status, CALIBRATION_PENDING)

    def test_pending_never_claims_calibration(self):
        """H-8：把 PENDING 渲染成能力的表述必须 FAIL（先红后绿）。"""
        st = check_sufficiency({}, selective_unresolved=[])
        with self.assertRaises(CalibrationClaimDenied) as ctx:
            assert_no_calibration_claim("本模型已校准，误差已验证",
                                        where="导出文件")
        self.assertIn("E-G6C-03-102", str(ctx.exception))
        # render 输出本身不含冒充表述
        assert_no_calibration_claim(render_for_display(st),
                                    where="render_for_display")

    def test_pending_vs_failure_distinguishable(self):
        """H-9：「未校准」（决策）与「校准失败」（测量）字段级可分辨。

        同一状态对象上两个独立字段：
          · declared_status   = 决策维度（VD-26 终态，恒 CALIBRATION_PENDING）
          · measurement_status = 测量维度（本次测量的充分性门结果）
        「未校准」与「校准失败」在记录中不得混同。
        """
        st = check_sufficiency(_score_inputs(n=3), selective_unresolved=[])
        d = st.to_dict()
        self.assertEqual(d["declared_status"], CALIBRATION_PENDING)
        self.assertEqual(d["measurement_status"], INSUFFICIENT_SAMPLE)
        self.assertEqual(st.declared_status, CALIBRATION_PENDING)
        self.assertEqual(st.measurement_status, INSUFFICIENT_SAMPLE)

    def test_rendered_text_has_no_claim_phrase(self):
        """H-8 表述守卫的行为断言：渲染文本逐词不含 CLAIM_PHRASES。"""
        from calibration import CLAIM_PHRASES
        st = check_sufficiency(_score_inputs(n=5), selective_unresolved=[])
        text = render_for_display(st)
        for ph in CLAIM_PHRASES:
            self.assertNotIn(ph, text,
                             f"渲染文本不得含冒充能力表述 {ph}（H-8）")


if __name__ == "__main__":
    unittest.main()
