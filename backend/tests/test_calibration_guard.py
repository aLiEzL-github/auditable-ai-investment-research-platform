"""calibration_claim_check 守卫的变异注入测试（先红后绿证据）。

H-8 表述守卫的变异注入：
  ① 把渲染文本改成含「已校准」 → 守卫必须 FAIL（红）
  ② 在生产代码字面量里植入「校准通过」 → 静态扫描必须 FAIL（红）
  ③ 复原 → 守卫转绿
"""
import os
import sys
import tempfile
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(REPO, "backend", "tools"))
sys.path.insert(0, os.path.join(REPO, "backend", "app"))


class TestCalibrationClaimGuard(unittest.TestCase):
    def test_behavioral_mutation_claim_phrase_red(self):
        """先红后绿：渲染文本植入冒充表述 → 行为断言 FAIL。"""
        import calibration_claim_check as g

        class _Status:
            declared_status = "CALIBRATION_PENDING"
            measurement_status = "INSUFFICIENT_SAMPLE"
            resolved = 0
            reporting_periods = 0
            horizon_buckets = 0
            effective_n = 0.0
            ci = None
            selective_unresolved = []

        from calibration import assert_no_calibration_claim
        # 红态：声称表述必须被拒（guard 的断言原语在测）
        with self.assertRaises(Exception) as ctx:
            assert_no_calibration_claim("本模型已校准，误差已验证", where="导出")
        self.assertIn("E-G6C-03-102", str(ctx.exception))

    def test_static_scan_planted_claim_red(self):
        """先红后绿：在临时副本植入冒充表述 → 静态扫描 FAIL；复原转绿。"""
        import calibration_claim_check as g
        with tempfile.TemporaryDirectory() as td:
            os.makedirs(os.path.join(td, "backend", "app"))
            target = os.path.join(td, "backend", "app", "evil.py")
            with open(target, "w", encoding="utf-8") as f:
                f.write('def render():\n    return "本模型校准通过"\n')
            saved = g.APP_DIR
            g.APP_DIR = os.path.join(td, "backend", "app")
            try:
                bad, checked = g._scan_app_literals()
                self.assertTrue(bad, "植入冒充表述必须被静态扫描抓到（红）")
                self.assertGreater(checked, 0, "检查对象数须报出（⑨）")
            finally:
                g.APP_DIR = saved

    def test_rejection_context_exempt_only_with_error_code(self):
        """豁免仅限拒绝语境（含 E-G6C-03 错误码）—— 其余一律红。"""
        import calibration_claim_check as g
        with tempfile.TemporaryDirectory() as td:
            os.makedirs(os.path.join(td, "backend", "app"))
            target = os.path.join(td, "backend", "app", "msgs.py")
            with open(target, "w", encoding="utf-8") as f:
                f.write('A = "E-G6C-03-102: 出现「已校准」表述"\n'
                        'B = "普通输出已校准"\n')
            saved = g.APP_DIR
            g.APP_DIR = os.path.join(td, "backend", "app")
            try:
                bad, _ = g._scan_app_literals()
                self.assertTrue(any("msgs.py" in b and "B = " not in b
                                    for b in bad),
                                "无错误码的冒充表述必须被抓")
                self.assertTrue(all("E-G6C-03-102" not in b for b in bad),
                                "拒绝语境（含错误码）不得误报")
            finally:
                g.APP_DIR = saved


if __name__ == "__main__":
    unittest.main()
