"""G2-11 验收测试：XBRL 结构化解析（负测 + 选择规则 + locator 回指）。

基线：
  · 解析异常不降级为零
  · 重述与多 context 有明确选择规则
  · 输出可回到原始 locator
F5：结构化解析负测通过（畸形/XXE/超限/重述/多 context）。
"""
import unittest
import os
import sys

APP = os.path.join(os.path.dirname(__file__), "..", "app")
sys.path.insert(0, APP)
TOOLS = os.path.join(os.path.dirname(__file__), "..", "tools")
sys.path.insert(0, TOOLS)

from xbrl_parser import XBRLParser, XBRLParseError  # noqa: E402

INST = 'http://www.xbrl.org/2003/instance'

SAMPLE = f'''<?xml version="1.0"?>
<xbrli:xbrl xmlns:xbrli="{INST}">
  <xbrli:context id="c_dur">
    <xbrli:period><xbrli:startDate>2026-01-01</xbrli:startDate>
    <xbrli:endDate>2026-06-30</xbrli:endDate></xbrli:period>
  </xbrli:context>
  <xbrli:context id="c_inst">
    <xbrli:period><xbrli:instant>2026-06-30</xbrli:instant></xbrli:period>
  </xbrli:context>
  <xbrli:unit id="u_cny"><xbrli:measure>iso4217:CNY</xbrli:measure></xbrli:unit>
  <Revenue contextRef="c_dur" unitRef="u_cny">100000000</Revenue>
  <NetProfit contextRef="c_dur" unitRef="u_cny">20000000</NetProfit>
  <TotalAssets contextRef="c_inst" unitRef="u_cny">500000000</TotalAssets>
  <NegativeFact contextRef="c_dur" unitRef="u_cny" sign="-">3000</NegativeFact>
</xbrli:xbrl>'''.encode("utf-8")


class TestXBRLParser(unittest.TestCase):
    def setUp(self):
        self.p = XBRLParser()

    # ── 正例：解析 + locator 回指 ───────────────────────────────────
    def test_parse_with_locator(self):
        facts = self.p.parse(SAMPLE, "LOC_600089_2026H1")
        by_id = {f["metric_id"]: f for f in facts}
        self.assertEqual(by_id["Revenue"]["value"], "100000000")
        self.assertEqual(by_id["Revenue"]["period"], "2026-01-01~2026-06-30")
        self.assertEqual(by_id["TotalAssets"]["period"], "2026-06-30")
        self.assertEqual(by_id["NegativeFact"]["value"], "-3000")
        for f in facts:
            self.assertEqual(f["locator"], "LOC_600089_2026H1",
                             "输出须可回到原始 locator")

    # ── 解析异常不降级为零 ──────────────────────────────────────────
    def test_malformed_xml_fail_closed(self):
        with self.assertRaises(XBRLParseError) as ctx:
            self.p.parse(b"<broken", "LOC_X")
        self.assertIn("E-G2-11-002", str(ctx.exception))

    def test_empty_input_fail_closed(self):
        with self.assertRaises(XBRLParseError) as ctx:
            self.p.parse(b"", "LOC_X")
        self.assertIn("E-G2-11-001", str(ctx.exception))

    def test_oversized_fail_closed(self):
        big = (SAMPLE + b"<x/>" * 300000)[:10_000_000]
        with self.assertRaises(XBRLParseError):
            self.p.parse(big, "LOC_X")

    # ── 多 context 选择规则 ─────────────────────────────────────────
    def test_multi_context_duration_preferred(self):
        facts = self.p.parse(SAMPLE, "LOC_1")
        # 同一 metric 出现 duration + instant 两 context
        cands = [f for f in facts if f["metric_id"] == "Revenue"]
        picked = self.p.select_best_context(cands)
        self.assertEqual(len(picked), 1)
        self.assertIn("~", picked[0]["period"], "duration 口径优先")

    # ── 重述选择规则 ────────────────────────────────────────────────
    def test_restatement_preferred(self):
        orig = {"metric_id": "Revenue", "value": "90", "locator": "LOC/2026H1/original"}
        rst = {"metric_id": "Revenue", "value": "95", "locator": "LOC/2026H1/restated"}
        out = self.p.select_restatement([orig, rst])
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["value"], "95", "重述后优先")

    # ── 非数值元素跳过（不产生零值 Fact）───────────────────────────
    def test_non_numeric_skipped_no_zero(self):
        txt = SAMPLE.replace(b"</xbrli:xbrl>",
                              "<Label>文字披露</Label></xbrli:xbrl>".encode("utf-8"))
        facts = self.p.parse(txt, "LOC_1")
        self.assertNotIn("Label", [f["metric_id"] for f in facts])


if __name__ == "__main__":
    unittest.main()
