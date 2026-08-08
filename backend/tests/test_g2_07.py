"""G2-07 验收测试：FactRecord 归一化、冲突与预注册容差。

基线：
  1. 五要素不可比 → NOT_COMPARABLE
  2. 缺失不补零
  3. 关键冲突阻断
  4. 同源镜像不算独立来源
"""
import unittest
import tempfile
import shutil
import os
import sys

APP = os.path.join(os.path.dirname(__file__), "..", "app")
sys.path.insert(0, APP)

from fact_normalizer import FactNormalizer  # noqa: E402
from repository import create_repository, Source, RawArtifact, FactRecord  # noqa: E402


def _fact(**kw):
    base = dict(id="FACT_001", schema_version="1.0", artifact_id="ART_P", source_id="SRC_SSE",
                metric="REVENUE", value="100", unit="CNY_M", period="2026-06",
                scope="600089", basis="IFRS", vintage="v1", parser_version="p1")
    base.update(kw)
    return FactRecord(**base)


class TestFactNormalizer(unittest.TestCase):
    def setUp(self):
        self.n = FactNormalizer(material_metrics={"REVENUE", "EPS"})
        self._tmp = tempfile.mkdtemp()
        self.repo = create_repository(os.path.join(self._tmp, "g2_07.sqlite3"))
        self.repo.create_all()
        self.s = self.repo.session()
        for sid in ("SRC_SSE", "SRC_NBS"):
            self.s.add(Source(id=sid, schema_version="1.0", kind="PRIMARY",
                              name=sid, status="ALLOWED", legal_basis="G2-07 测试", version=1))
        self.s.commit()
        import hashlib
        self.s.add(RawArtifact(id="ART_P", schema_version="1.0", source_id="SRC_SSE",
                               sha256=hashlib.sha256(b"p").hexdigest(), bytes=1,
                               content_type="text/plain",
                               acquired_at=__import__("datetime").datetime.utcnow(), version=1))
        self.s.commit()

    def tearDown(self):
        self.s.close()
        self.repo.engine.dispose()
        shutil.rmtree(self._tmp, ignore_errors=True)

    # ── 1. 五要素不可比 → NOT_COMPARABLE ────────────────────────────
    def test_not_comparable(self):
        f1 = _fact(period="2026-06")
        f2 = _fact(id="FACT_002", period="2026-07")  # period 不同
        self.assertEqual(self.n.comparability(f1, f2), "NOT_COMPARABLE")
        f3 = _fact(id="FACT_003", unit="CNY")  # unit 不同
        self.assertEqual(self.n.comparability(f1, f3), "NOT_COMPARABLE")

    def test_comparable(self):
        f1 = _fact()
        f2 = _fact(id="FACT_002", value="101")
        self.assertEqual(self.n.comparability(f1, f2), "COMPARABLE")

    # ── 2. 缺失不补零 ───────────────────────────────────────────────
    def test_missing_value_rejected(self):
        f = _fact(value="")
        with self.assertRaises(ValueError) as ctx:
            self.n.check_missing_value(f)
        self.assertIn("E-G2-07-001", str(ctx.exception))

    def test_zero_is_valid_not_placeholder(self):
        """显式 0 是合法值（真实为零），区别于缺失。"""
        f = _fact(value="0")
        self.n.check_missing_value(f)  # 不抛错

    # ── 3. 关键冲突阻断 ─────────────────────────────────────────────
    def test_material_conflict_blocks(self):
        f1 = _fact(value="100")
        f2 = _fact(id="FACT_002", source_id="SRC_NBS", value="150")  # 异源（独立来源）
        with self.assertRaises(ValueError) as ctx:
            self.n.detect_conflict(f1, f2)
        self.assertIn("E-G2-07-002", str(ctx.exception))

    def test_non_material_conflict_reported(self):
        n = FactNormalizer(material_metrics={"REVENUE"})
        f1 = _fact(metric="OTHER", value="100")
        f2 = _fact(id="FACT_002", source_id="SRC_NBS", metric="OTHER", value="150")
        c = n.detect_conflict(f1, f2)
        self.assertEqual(c.kind, "CONFLICT")
        self.assertEqual(c.left, "100")

    # ── 4. 同源镜像不算独立来源 ────────────────────────────────────
    def test_mirror_not_independent(self):
        f1 = _fact(source_id="SRC_SSE")
        f2 = _fact(id="FACT_002", source_id="SRC_SSE", value="100.0")  # 同源重复
        self.assertTrue(self.n.is_mirror(f1, f2))
        c = self.n.detect_conflict(f1, f2)
        self.assertEqual(c.kind, "MIRROR")
        # 不同源 → 非镜像 → 冲突/通过
        f3 = _fact(id="FACT_003", source_id="SRC_NBS", value="100.0")
        self.assertFalse(self.n.is_mirror(f1, f3))

    # ── 预注册容差：容差内不冲突 ────────────────────────────────────
    def test_tolerance_within_no_conflict(self):
        n = FactNormalizer(tolerance=0.01, material_metrics={"REVENUE"})
        f1 = _fact(value="100")
        f2 = _fact(id="FACT_002", source_id="SRC_NBS", value="100.5")  # 异源 0.5% < 1%
        self.assertIsNone(n.detect_conflict(f1, f2))
        f3 = _fact(id="FACT_003", source_id="SRC_NBS", value="105")  # 异源 5% > 1% → 阻断
        with self.assertRaises(ValueError):
            n.detect_conflict(f1, f3)

    # ── X-3：ORM 行 ↔ schema 双向一致 ───────────────────────────────
    def test_orm_row_matches_schema(self):
        from schema_validate import validate_object
        f = _fact(schema_version="1.0.0")
        validate_object("fact", {
            "schema_version": f.schema_version, "id": f.id, "artifact_id": f.artifact_id,
            "metric": f.metric, "value": f.value, "unit": f.unit, "period": f.period,
            "scope": f.scope, "basis": f.basis, "vintage": f.vintage,
            "locator": f.locator or "LOC_600089", "parser_version": f.parser_version})


if __name__ == "__main__":
    unittest.main()
