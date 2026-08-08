#!/usr/bin/env python3
"""xlsx_golden_reader.py —— G2-14 xlsx 财报整理表解析（标准库，无新依赖）。

输入：用户人工取得的真实披露整理表（xlsx：利润表/资产负债表/现金流量表）。
输出：20 项 MetricSpec 适用指标的逐期事实（locator = 文件 + sheet + 行标签 + 期间）。
数据禁入（ADR-006）：只输出事实 JSON，原始 xlsx 不入仓库。
"""
import json
import re
import sys
import zipfile
from datetime import datetime, timedelta
from xml.etree import ElementTree as ET

NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
T = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
SHEETS = ("xl/worksheets/sheet1.xml", "xl/worksheets/sheet2.xml",
          "xl/worksheets/sheet3.xml")
PERIODS = "BCDEFGHIJKL"

# sheet 索引 → 指标提取（行标签包含匹配 + metric_id + 单位）
EXTRACT = {
    0: [  # 利润表
        (("营业收入", "营业收入", "营业总收入"), "营业收入", "CNY"),
        (("归属于母公司所有者的净利",), "归母净利润", "CNY"),
    ],
    1: [  # 资产负债表
        (("货币资金",), "货币资金", "CNY"),
        (("存货",), "存货", "CNY"),
        (("在建工程(合计)",), "在建工程", "CNY"),
        (("商誉",), "商誉", "CNY"),
        (("实收资本(或股本)",), "总股本", "股"),
    ],
    2: [  # 现金流量表
        (("经营活动产生的现金流量净额",), "经营活动现金流净额", "CNY"),
    ],
}


def excel_date(serial: str) -> str:
    return (datetime(1899, 12, 30) + timedelta(days=int(float(serial)))).strftime("%Y-%m-%d")


def read_workbook(path: str) -> dict:
    z = zipfile.ZipFile(path)
    ss = []
    root = ET.fromstring(z.read("xl/sharedStrings.xml"))
    for si in root.findall("m:si", NS):
        ss.append("".join(t.text or "" for t in si.iter(T + "t")))

    def rows_of(sheet_file):
        root = ET.fromstring(z.read(sheet_file))
        out = []
        for row in root.findall(".//m:row", NS):
            cells = {}
            for c in row.findall("m:c", NS):
                col = re.match(r"([A-Z]+)", c.get("r")).group(1)
                v = c.find("m:v", NS)
                val = v.text if v is not None else ""
                if c.get("t") == "s" and val:
                    val = ss[int(val)]
                cells[col] = val
            out.append(cells)
        return out

    rows = [rows_of(s) for s in SHEETS]
    # 表头（第 2 行 = 期间日期序列号）
    headers = []
    for r in rows:
        dates = []
        for c in PERIODS:
            v = r[1].get(c, "")
            dates.append(excel_date(v) if v else c)
        headers.append(dates)
    return {"sheets": rows, "headers": headers}


def extract_facts(book: dict, source_name: str) -> list:
    facts = []
    for si, extractors in EXTRACT.items():
        rows = book["sheets"][si]
        headers = book["headers"][si]
        for row in rows:
            label = (row.get("A") or "").strip()
            if not label:
                continue
            for keys, metric, unit in extractors:
                if label == keys[0] or (keys[0] in label and not any(
                        k in label for k in keys[1:])):
                    for i, col in enumerate(PERIODS):
                        v = row.get(col, "").strip()
                        if not v or v == "-":
                            continue
                        facts.append({
                            "metric_id": metric, "unit": unit,
                            "period": headers[i],
                            "value": v,
                            "locator": f"{source_name}#sheet{si + 1}#{label}#{headers[i]}",
                        })
    return facts


if __name__ == "__main__":
    src = sys.argv[1]
    import os
    book = read_workbook(src)
    facts = extract_facts(book, os.path.basename(src))
    print(json.dumps(facts, ensure_ascii=False, indent=1))
    print(f"# {len(facts)} facts", file=sys.stderr)
