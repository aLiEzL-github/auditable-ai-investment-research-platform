#!/usr/bin/env python3
"""test_g7_06_first_screen.py —— OI-PF-154：G7-06 准出记录首屏声明硬约束。

复用 claim_engine.verify_first_screen（C-10，OI-PF-070 U 裁定：前 3 行）：
G7-06 准出记录（对外交付形态）首屏前 3 行须命中 SINGLE_REVIEWER_ATTESTED，
缺失即准出失败（不得只放脚注）。

变异注入配对：
  首屏命中   → 通过
  首屏缺失（仅脚注）→ 失败（E-G3-05-013）
  完全缺失   → 失败
"""
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "app"))

from claim_engine import verify_first_screen, verify_disclaimer, FirstScreenGuardFail  # noqa: E402

ATTESTATION = "SINGLE_REVIEWER_ATTESTED"


class TestG706FirstScreen(unittest.TestCase):
    """OI-PF-154：G7-06 准出记录首屏声明（前 3 行）硬约束。"""

    def test_first_screen_attestation_pass(self):
        """首屏前 3 行命中 → 通过。"""
        good = f"""# 600089 研究准出
{ATTESTATION}
研究信息不构成投资建议。"""
        verify_first_screen("g7-06-record.md", good)

    def test_first_screen_only_footer_fail(self):
        """仅脚注命中 → 失败（OI-PF-070：不得只放脚注）。"""
        bad = f"""# 600089 研究准出
本记录载明如下声明。

（正文…）

{ATTESTATION}"""
        with self.assertRaises(FirstScreenGuardFail) as ctx:
            verify_first_screen("g7-06-record.md", bad)
        self.assertIn("E-G3-05-013", str(ctx.exception))

    def test_attestation_missing_fail(self):
        """完全缺失 → 失败。"""
        with self.assertRaises(FirstScreenGuardFail):
            verify_first_screen("g7-06-record.md", "# 无声明记录\n正文")

    def test_disclaimer_required(self):
        """每份准出记录须载明「不构成投资建议」。"""
        good = "正文\n研究信息不构成投资建议。"
        verify_disclaimer("g7-06-record.md", good)
        with self.assertRaises(FirstScreenGuardFail):
            verify_disclaimer("g7-06-record.md", "正文无免责声明")


if __name__ == "__main__":
    unittest.main()
