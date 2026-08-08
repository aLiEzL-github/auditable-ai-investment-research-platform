"""metric_spec.py —— G2-10 20 项 MetricSpec 注册表（漂移阻断）。

基线验收（G2-10）：
  · 20/20 逐字匹配且 origin 合同完整
  · 缺项不补默认值
  · 漂移阻断（冻结哈希）
  · 5 指标 PoC 也须保留 20 项 Schema，未实现项标 NOT_IMPLEMENTED / NOT_RELEASE_ELIGIBLE
"""
import hashlib
import json
import os

CONTRACTS = os.path.join(os.path.dirname(__file__), "..", "..", "contracts")
SPEC_PATH = os.path.join(CONTRACTS, "metric_spec.json")


class MetricSpecError(ValueError):
    pass


def load_spec() -> dict:
    with open(SPEC_PATH, encoding="utf-8") as f:
        return json.load(f)


def _canon(metrics) -> str:
    return json.dumps(metrics, ensure_ascii=False, sort_keys=True)


def verify_frozen(metrics) -> None:
    """漂移阻断：20 项清单的冻结哈希逐字比对。"""
    actual = hashlib.sha256(_canon(metrics).encode("utf-8")).hexdigest()
    expected = load_spec()["frozen_sha256"]
    if actual != expected:
        raise MetricSpecError(
            f"E-G2-10-003: MetricSpec 漂移阻断 —— 实算 {actual[:16]}… ≠ 冻结 {expected[:16]}…")


def check_20_spec() -> int:
    """20/20 完整性：缺项不补默认值。"""
    spec = load_spec()
    metrics = spec["metrics"]
    if len(metrics) != 20:
        raise MetricSpecError(
            f"E-G2-10-001: 20 项 MetricSpec 不完整: {len(metrics)}/20（缺项不补默认值）")
    for m in metrics:
        for k in ("metric_id", "expected_origin", "caliber"):
            if not m.get(k):
                raise MetricSpecError(f"E-G2-10-002: 缺 {k}（不补默认值）: {m.get('metric_id')}")
    verify_frozen(metrics)
    return 20


def status_of(metric_id: str) -> str:
    """5 指标 PoC 也须保留 20 项：未实现项标 NOT_IMPLEMENTED / NOT_RELEASE_ELIGIBLE。"""
    spec = load_spec()
    ids = [m["metric_id"] for m in spec["metrics"]]
    if metric_id not in ids:
        raise MetricSpecError(f"E-G2-10-004: 未知指标（20 项 Schema 外）: {metric_id}")
    # 当前仅 PoC 实现指标白名单（G2-14 前全部未实现）
    poc_implemented = {"营业收入", "归母净利润", "总股本", "货币资金", "存货"}
    if metric_id in poc_implemented:
        return "IMPLEMENTED"
    return "NOT_IMPLEMENTED / NOT_RELEASE_ELIGIBLE"
