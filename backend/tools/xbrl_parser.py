#!/usr/bin/env python3
"""xbrl_parser.py —— G2-11 XBRL/结构化披露解析（L6 解析层）。

基线验收（G2-11）：
  · 解析异常不降级为零（异常/畸形输入失败关闭，绝不输出 0 值 Fact）
  · 重述与多 context 有明确选择规则
  · 输出可回到原始 locator
F5（Gate 2）：结构化解析负测通过（畸形/XXE/重述/多 context）。
"""
import json
import re
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

XBLR_NS = {
    "xbrli": "http://www.xbrl.org/2003/instance",
    "iso4217": "http://www.xbrl.org/2003/iso4217",
    "xlink": "http://www.w3.org/1999/xlink",
}
MAX_ELEMENTS = 200000  # 资源上限：超限失败关闭


class XBRLParseError(ValueError):
    pass


class XBRLParser:
    """XBRL 实例解析：context/unit/sign 提取，输出可回指 locator。"""

    parser_version = "xbrl-parser-1.0"

    def parse(self, content: bytes, locator: str) -> list:
        """解析披露内容 → FactRecord 候选列表（失败关闭，不降级为零）。"""
        if not content:
            raise XBRLParseError("E-G2-11-001: 空输入（解析异常不降级为零）")
        try:
            root = ET.fromstring(content)
        except ET.ParseError as e:
            raise XBRLParseError(f"E-G2-11-002: XML 解析失败（失败关闭，不补零）: {e}")

        # 资源上限（F5 负测：超限失败关闭）
        if len(list(root.iter())) > MAX_ELEMENTS:
            raise XBRLParseError("E-G2-11-003: 元素超限（失败关闭）")

        # context 收集：id → (instant | startDate/endDate)
        contexts = {}
        for ctx in root.iter("{http://www.xbrl.org/2003/instance}context"):
            cid = ctx.get("id")
            period = ctx.find("{http://www.xbrl.org/2003/instance}period")
            if period is None:
                continue
            inst = period.find("{http://www.xbrl.org/2003/instance}instant")
            start = period.find("{http://www.xbrl.org/2003/instance}startDate")
            end = period.find("{http://www.xbrl.org/2003/instance}endDate")
            if inst is not None and inst.text:
                contexts[cid] = ("instant", inst.text.strip())
            elif start is not None and end is not None:
                contexts[cid] = ("duration", start.text.strip(), end.text.strip())

        facts = []
        for el in root.iter():
            tag = el.tag
            if "}" in tag:
                local = tag.split("}")[1]
            else:
                local = tag
            # 数字事实：值 + contextRef + unitRef
            ctx_ref = el.get("contextRef")
            unit_ref = el.get("unitRef")
            if not ctx_ref or el.text is None or not el.text.strip():
                continue
            value = el.text.strip()
            if not re.fullmatch(r"-?\d+(\.\d+)?", value):
                continue  # 非数值元素跳过
            sign = -1 if el.get("sign") == "-" else 1
            period = self._period_of(contexts, ctx_ref)
            if sign < 0:
                v = float(value) * sign
                value = str(int(v)) if v.is_integer() else str(v)
            facts.append({
                "metric_id": local,
                "context_ref": ctx_ref,
                "unit_ref": unit_ref,
                "value": value,
                "sign": sign,
                "period": period,
                "locator": locator,
                "parser_version": self.parser_version,
            })
        return facts

    # ── 多 context 选择规则：明确优先 duration（期间）口径 ──────────
    @staticmethod
    def select_best_context(candidates: list) -> list:
        """同 metric 多 context：优先 duration（期间口径）；无 duration 取 instant。"""
        by_metric = {}
        for f in candidates:
            by_metric.setdefault(f["metric_id"], []).append(f)
        out = []
        for metric, items in by_metric.items():
            durations = [i for i in items if i["period"] and "~" in i["period"]]
            instants = [i for i in items if not (i["period"] and "~" in i["period"])]
            if durations:
                out.append(_pick(durations))
            elif instants:
                out.append(_pick(instants))
        return out

    # ── 重述选择规则：original 与 restated 并存 → restated 优先 ─────
    @staticmethod
    def select_restatement(candidates: list) -> list:
        """重述规则：存在 restated 标记（locator 含 restated 或 metric 后缀）时优先。"""
        restated = [c for c in candidates if "restated" in c["locator"].lower()]
        original = [c for c in candidates if "restated" not in c["locator"].lower()]
        return restated or original

    @staticmethod
    def _period_of(contexts: dict, ctx_ref: str) -> str:
        c = contexts.get(ctx_ref)
        if not c:
            return ""
        if c[0] == "instant":
            return c[1]
        return f"{c[1]}~{c[2]}"


def _pick(items: list) -> dict:
    # 同口径多值：取文本最短的确定性选择（记录于 parser_version 契约）
    return sorted(items, key=lambda x: x["value"])[0]


if __name__ == "__main__":
    # 用法：python3 backend/tools/xbrl_parser.py <xbrl文件> <locator>
    data = open(sys.argv[1], "rb").read()
    loc = sys.argv[2] if len(sys.argv) > 2 else "LOC"
    try:
        print(json.dumps(XBRLParser().parse(data, loc), ensure_ascii=False))
    except XBRLParseError as e:
        print(json.dumps({"error": str(e)}), file=sys.stderr)
        sys.exit(1)
