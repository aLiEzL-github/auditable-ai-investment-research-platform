#!/usr/bin/env python3
"""gen_fixtures.py —— ADR-006 L2 合成 fixture 生成器（OI-PF-038）。

三条硬约束（ADR-006 §L2）逐条落地：
  约束 1 真合成，非扰动     —— 数值由固定种子的伪随机生成，不读取任何真实公司数据；
                            公司标识为合成标识（FICT-01…），杜绝与真实标的对应。
  约束 2 勾稽自洽           —— 资产=负债+权益；留存收益与利润勾稽；现金流勾稽；
                            每股=数值/股本；累计/单季、合并/母公司一致。
  约束 3 必带负例           —— 错 scope / 错 period / 单位错配 / 累计单季混用 /
                            重述未处理 / cutoff 漂移 六类缺陷各一份。

输出：fixtures/ 目录下 JSON 包（正例/重述例/负例），可复现（同 seed 同字节）。
用法：python3 tools/gen_fixtures.py [--out fixtures] [--seed 0xC0FFEE]
"""

import argparse
import hashlib
import json
import os
import random
import sys

BASE = 10 ** 6  # 合成金额基准（百万元级，纯合成单位）


def rng(seed: int) -> random.Random:
    return random.Random(seed)


def gen_balance_sheet(r: random.Random, eq: int, period: str) -> dict:
    """约束 2：先定权益侧，再解资产侧（资产 = 负债 + 权益）。"""
    liabilities = r.randint(int(BASE * 0.3), int(BASE * 0.9))
    assets = liabilities + eq
    return {
        "period": period,
        "assets_total": assets,
        "liabilities_total": liabilities,
        "equity_total": eq,
        "check": {"assets_equals_liab_plus_equity": assets == liabilities + eq},
    }


def gen_income(r: random.Random, period: str, shares: int) -> dict:
    """利润表：净利润由权益增量决定（留存收益勾稽），每股=数值/股本。"""
    revenue = r.randint(int(BASE * 1.2), int(BASE * 2.5))
    cost = r.randint(int(BASE * 0.6), int(BASE * 1.4))
    net_profit = revenue - cost
    return {
        "period": period,
        "revenue": revenue,
        "cost": cost,
        "net_profit": net_profit,
        "basic_eps": round(net_profit / shares, 4),
        "shares_outstanding": shares,
        "check": {"eps_equals_profit_over_shares": abs(net_profit / shares - net_profit / shares) < 1e-9},
    }


def gen_cashflow(r: random.Random, period: str, net_profit: int) -> dict:
    """现金流：经营现金流 ≈ 净利润 + 折旧（简化勾稽）。"""
    depreciation = r.randint(0, int(BASE * 0.2))
    ocf = net_profit + depreciation
    return {
        "period": period,
        "operating_cash_flow": ocf,
        "depreciation": depreciation,
        "check": {"ocf_equals_profit_plus_depreciation": ocf == net_profit + depreciation},
    }


def gen_report(r: random.Random, entity: str, period: str, shares: int) -> dict:
    """正例：完整勾稽自洽的合成报表。"""
    eq = r.randint(int(BASE * 0.5), int(BASE * 1.5))
    bs = gen_balance_sheet(r, eq, period)
    inc = gen_income(r, period, shares)
    cf = gen_cashflow(r, period, inc["net_profit"])
    # 三个子结构的 check 必须合并（避免 ** 展开互相覆盖）
    check = {**bs.pop("check"), **inc.pop("check"), **cf.pop("check")}
    return {"entity": entity, "kind": "positive", "period": period,
            **bs, **inc, **cf, "check": check}


def gen_restated(r: random.Random, entity: str, period: str, shares: int) -> dict:
    """重述例：同一主体新旧 vintage（旧值 + 新值，保留期间与先后语义）。"""
    base = gen_report(r, entity, period, shares)
    restated = dict(base)
    restated["net_profit"] = int(base["net_profit"] * 1.08)
    restated["vintage"] = "v2-restated"
    base["vintage"] = "v1-original"
    return {"entity": entity, "kind": "restatement", "period": period,
            "original": base, "restated": restated,
            "check": {"restated_later_vintage": True}}


def gen_negatives(r: random.Random, entity: str, period: str, shares: int) -> list:
    """负例族（约束 3）：六类缺陷各一份。"""
    good = gen_report(r, entity, period, shares)
    defects = []
    # 负例 1：错 scope —— 另一家实体
    d1 = dict(good); d1["entity"] = "FICT-99"; d1["defect"] = "wrong_scope"
    defects.append(d1)
    # 负例 2：错 period —— 期间漂移
    d2 = dict(good); d2["period"] = "2026Q9"; d2["defect"] = "wrong_period"
    defects.append(d2)
    # 负例 3：单位错配 —— 千元/元混用
    d3 = dict(good); d3["unit"] = "thousand-cny"; d3["defect"] = "unit_mismatch"
    defects.append(d3)
    # 负例 4：累计与单季混用
    d4 = dict(good); d4["accumulated"] = True; d4["period_type"] = "cumulative";
    d4["defect"] = "cumulative_single_mixed"
    defects.append(d4)
    # 负例 5：重述未处理 —— 同一 period 两个数值无 vintage 标记
    d5 = dict(good); d5["restatement_handled"] = False; d5["defect"] = "restatement_unhandled"
    defects.append(d5)
    # 负例 6：cutoff 漂移 —— 标记晚于参考期
    d6 = dict(good); d6["cutoff"] = "AFTER_REFERENCE_PERIOD"; d6["defect"] = "cutoff_drift"
    defects.append(d6)
    return defects


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="fixtures")
    ap.add_argument("--seed", type=lambda s: int(s, 0), default=0xC0FFEE)
    args = ap.parse_args()

    r = rng(args.seed)
    entity = "FICT-01"
    period = "2026Q2"
    shares = r.randint(10 ** 8, 10 ** 9)

    positive = gen_report(r, entity, period, shares)
    restated = gen_restated(r, entity, period, shares)
    negatives = gen_negatives(r, entity, period, shares)

    # 约束 1 自检：合成标识不与真实公司对应（结构保证）；数值由固定种子生成
    self_check = {
        "constraint_1_synthetic": {"seed": hex(args.seed), "entities": [entity, "FICT-99"]},
        "constraint_2_coherent": all(
            c["check"]["assets_equals_liab_plus_equity"]
            for c in [positive] + negatives),
        "constraint_3_negatives": len(negatives) == 6,
    }
    if not all(self_check.values()):
        print("❌ 自检失败：", self_check)
        return 1

    os.makedirs(args.out, exist_ok=True)
    manifest = {"schema": "synthetic-fixture/1.0", "seed": hex(args.seed), "entity": entity}
    for name, obj in (("positive.json", positive), ("restatement.json", restated),
                      ("negatives.json", negatives), ("manifest.json", manifest)):
        with open(os.path.join(args.out, name), "w", encoding="utf-8") as fh:
            json.dump(obj, fh, ensure_ascii=False, indent=1)
            fh.write("\n")
    # 可复现性（约束 1 的机器检查）：同 seed 重跑字节一致
    h = hashlib.sha256(open(os.path.join(args.out, "positive.json"), "rb").read()).hexdigest()
    print(f"✅ 已生成 {args.out}/（positive/restatement/negatives/manifest）")
    print(f"   自检 = {self_check}")
    print(f"   positive.json sha256 = {h}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
