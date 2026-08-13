"""G3-06 敏感性测试矩阵（基线 B §270 交付件，此前**全仓库无对应物**）。

基线 B §270 对 G3-06 的交付件是「路由/双算/**敏感性测试矩阵**」。
路由与双算在 test_g3_06.py 中已有用例；敏感性此前一个都没有 ——
2026-08-12 广搜「敏感性 / sensitivity / 弹性 / 扰动」在 backend/ 与
contracts/ 零命中。

**这道缺失有直接后果**：OI-PF-162（fcff_valuation 的 growth 形参被接收、
赋值、从不读取；实测 growth=-0.30 与 growth=0.50 输出逐字相同）。
若本矩阵当初就建了，该缺陷在 G3-06 交付当天即现形，
不必等到 G6A-05 的 PRODUCT_DEPS 依赖审计。

**判据来自契约而非实现**：contracts/valuation_sensitivity.json 声明每个
(方法, 参数) 期望 SENSITIVE 还是 INSENSITIVE。拿实现推期望等于拿实现测自己。
声明 INSENSITIVE 须填 reason —— 按规则 ㉚，它等同于一条豁免。
"""
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "app"))

from valuation_engine import (  # noqa: E402
    BASE, ValuationInputs, fcff_valuation, fcfe_valuation,
    relative_valuation, pe_roe_pb_valuation, sotp_valuation,
)

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
_CONTRACT = os.path.join(_ROOT, "contracts", "valuation_sensitivity.json")


def _inputs():
    return ValuationInputs(
        scope="sensitivity-probe", currency="CNY", as_of="2026-06-30",
        price="10", shares_outstanding="1000000",
        net_debt="0", minority_interest="0",
        statuses={k: "READY" for k in ("price", "shares_outstanding",
                                       "net_debt", "minority_interest")})


# 每个方法的基准调用（关键字形式，便于逐参数替换）
_BASELINE = {
    # growth 已于 2026-08-12 从 fcff_valuation 移除（OI-PF-162）——
    # 单阶段 Gordon 永续模型里没有它的位置。
    "fcff_valuation": dict(fcff="1000000", wacc="0.10",
                           terminal_growth="0.03"),
    "fcfe_valuation": dict(fcfe="1000000", growth="0.05", ke="0.10"),
    "relative_valuation": dict(target_pe="12", eps="1.5"),
    "pe_roe_pb_valuation": dict(roe="0.10", book_per_share="8",
                                target_pe="12"),
    # OI-PF-172：SOTP 入参是 Dict（分部 → 值），探针形态是**整表替换**。
    # 基准须合法：非空、且重叠 ≤ 分部合计 50%（否则触发 SotpDoubleCount）。
    "sotp_valuation": dict(segments={"segA": "1000000", "segB": "500000"},
                           overlaps={"ovl": "100000"}),
}
_FN = {
    "fcff_valuation": fcff_valuation,
    "fcfe_valuation": fcfe_valuation,
    "relative_valuation": relative_valuation,
    "pe_roe_pb_valuation": pe_roe_pb_valuation,
    "sotp_valuation": sotp_valuation,
}


def _call(method, **over):
    kw = dict(_BASELINE[method])
    kw.update(over)
    return _FN[method](_inputs(), BASE, **kw).per_share_base


class TestValuationSensitivityMatrix(unittest.TestCase):
    """契约驱动：矩阵里每一格都是一个断言。"""

    @classmethod
    def setUpClass(cls):
        with open(_CONTRACT, encoding="utf-8") as f:
            cls.c = json.load(f)

    def test_contract_covers_every_declared_method(self):
        """**矩阵不得空转**：契约里的方法须都能被调用到。
        一个引用了不存在方法的矩阵会全绿而什么也没测（死豁免同款）。"""
        for entry in self.c["matrix"]:
            self.assertIn(entry["method"], _FN,
                          f"契约声明了 {entry['method']} 但本文件无法调用它")
            self.assertIn(entry["method"], _BASELINE)

    def test_every_cell_has_two_distinct_probes(self):
        """探针须真的不同 —— 两个相同的探针会让 SENSITIVE 断言恒假成立不了、
        让 INSENSITIVE 恒真通过。**判据本身也要能被检查。**"""
        for entry in self.c["matrix"]:
            for p, spec in entry["params"].items():
                a, b = spec["probe"]
                self.assertNotEqual(a, b,
                                    f"{entry['method']}.{p} 的两个探针相同")

    def test_insensitive_declarations_carry_a_reason(self):
        """㉚：声明 INSENSITIVE 等同于加一条豁免，须写明理由。"""
        for entry in self.c["matrix"]:
            for p, spec in entry["params"].items():
                if spec["expect"] == "INSENSITIVE":
                    self.assertTrue(spec.get("reason", "").strip(),
                                    f"{entry['method']}.{p} 声明不敏感却无理由")

    def test_sensitivity_matrix(self):
        """矩阵主体：逐格实测。

        **探针非法与参数惰性必须可分辨**（㉟）：探针越界会抛 ValuationError
        （如 FCFE 路要求 Ke > 增速），若把它并进 failures，读者会以为是
        敏感性缺陷；反过来若吞掉它，那一格等于没测而矩阵照绿。
        故单列 invalid，并同样使本用例 FAIL —— **没测 ≠ 测过了**。
        """
        failures, invalid = [], []
        for entry in self.c["matrix"]:
            m = entry["method"]
            for p, spec in entry["params"].items():
                a, b = spec["probe"]
                try:
                    va, vb = _call(m, **{p: a}), _call(m, **{p: b})
                except Exception as e:
                    invalid.append(f"{m}.{p}: 探针 {a}/{b} 越界或调用失败 —— "
                                   f"{type(e).__name__}: {e} "
                                   f"（**本格未被测到**，须改探针值）")
                    continue
                changed = (va != vb)
                if spec["expect"] == "SENSITIVE" and not changed:
                    failures.append(
                        f"{m}.{p}: 声明 SENSITIVE，但 {p}={a} 与 {p}={b} "
                        f"产出**逐字相同**（{va}）—— 参数不生效")
                elif spec["expect"] == "INSENSITIVE" and changed:
                    failures.append(
                        f"{m}.{p}: 声明 INSENSITIVE，但改动使产出由 {va} 变为 {vb}")
        msg = []
        if invalid:
            msg.append("**未被测到的格子（探针非法）**：\n  " + "\n  ".join(invalid))
        if failures:
            msg.append("**敏感性断言不成立**：\n  " + "\n  ".join(failures))
        self.assertEqual([], invalid + failures, "\n" + "\n".join(msg))


if __name__ == "__main__":
    unittest.main()
