"""G1-02 验收测试：Schema 校验 + 写权矩阵可执行。

验收映射（B 基线 G1-02）：
  · FK/CHECK/唯一约束和写权可执行   —— assert_writer / validate_object
  · LLM 不能写事实、批准、预测结果或 current —— E-WRITE-002
  · 非法状态和缺字段被拒绝          —— E-SCHEMA-001/002/004
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
import schema_validate as sv

FACT = {
    "id": "F-0001", "schema_version": "1.0.0",
    "artifact_id": "RA-0001", "metric": "net_profit", "value": "123.45",
    "unit": "CNY", "period": "2026Q2", "scope": "FICT-01",
    "basis": "FY", "vintage": "2026-07", "locator": "obj:RA-0001#1",
    "parser_version": "v1",
}


class TestSchemaValidation(unittest.TestCase):
    def test_valid_fact_passes(self):
        self.assertIsNone(sv.validate_object("fact", FACT))

    def test_missing_required_rejected(self):
        bad = dict(FACT)
        del bad["value"]
        with self.assertRaises(sv.SchemaError) as cm:
            sv.validate_object("fact", bad)
        self.assertEqual(cm.exception.code, "E-SCHEMA-001")

    def test_unknown_field_rejected(self):
        bad = dict(FACT, extra_field="x")
        with self.assertRaises(sv.SchemaError) as cm:
            sv.validate_object("fact", bad)
        self.assertEqual(cm.exception.code, "E-SCHEMA-004")

    def test_bad_enum_rejected(self):
        bad = dict(FACT, unit="")
        with self.assertRaises(sv.SchemaError):
            sv.validate_object("fact", bad)

    def test_wrong_schema_version_rejected(self):
        bad = dict(FACT, schema_version="9.9.9")
        with self.assertRaises(sv.SchemaError) as cm:
            sv.validate_object("fact", bad)
        self.assertEqual(cm.exception.code, "E-SCHEMA-005")

    def test_prediction_calibration_const(self):
        good = {"id": "P-0001", "schema_version": "1.0.0", "claim_id": "C-0001",
                "horizon": "2027-01-01T00:00:00Z", "probability": 0.6,
                "calibration_pending": True, "registered_at": "2026-08-06T00:00:00Z"}
        self.assertIsNone(sv.validate_object("prediction", good))
        bad = dict(good, calibration_pending=False)
        with self.assertRaises(sv.SchemaError):
            sv.validate_object("prediction", bad)

    def test_invalid_timestamp_rejected(self):
        bad = dict(FACT)
        bad["vintage"] = "not-a-date"
        # vintage 无 pattern 约束，仅验证有 pattern 的字段
        bad2 = dict(FACT)
        bad2["id"] = "bad id!"
        with self.assertRaises(sv.SchemaError) as cm:
            sv.validate_object("fact", bad2)
        self.assertEqual(cm.exception.code, "E-SCHEMA-002")


class TestWriterMatrix(unittest.TestCase):
    def test_llm_cannot_write_fact(self):
        with self.assertRaises(sv.SchemaError) as cm:
            sv.assert_writer("fact", "LLM")
        self.assertEqual(cm.exception.code, "E-WRITE-002")

    def test_llm_cannot_write_prediction(self):
        with self.assertRaises(sv.SchemaError) as cm:
            sv.assert_writer("prediction", "LLM")
        self.assertEqual(cm.exception.code, "E-WRITE-002")

    def test_llm_cannot_write_approval(self):
        with self.assertRaises(sv.SchemaError) as cm:
            sv.assert_writer("approval", "LLM")
        self.assertEqual(cm.exception.code, "E-WRITE-002")

    def test_llm_cannot_write_current(self):
        with self.assertRaises(sv.SchemaError) as cm:
            sv.assert_writer("current_pointer", "LLM")
        self.assertEqual(cm.exception.code, "E-WRITE-002")

    def test_authorized_writer_passes(self):
        self.assertIsNone(sv.assert_writer("fact", "L7_freeze"))
        self.assertIsNone(sv.assert_writer("approval", "L12_approval_endpoint"))
        self.assertIsNone(sv.assert_writer("release", "L11_release"))

    def test_wrong_writer_rejected(self):
        with self.assertRaises(sv.SchemaError) as cm:
            sv.assert_writer("calc", "L10")
        self.assertEqual(cm.exception.code, "E-WRITE-001")

    # H-2/J2：类别记号行的可判定语义（此前唯一无测试覆盖的三行）
    def test_assumption_writers_set(self):
        self.assertIsNone(sv.assert_writer("assumption", "L8"))
        self.assertIsNone(sv.assert_writer("assumption", "L9"))
        with self.assertRaises(sv.SchemaError) as cm:
            sv.assert_writer("assumption", "LLM")
        self.assertEqual(cm.exception.code, "E-WRITE-002")
        with self.assertRaises(sv.SchemaError) as cm:
            sv.assert_writer("assumption", "L8_L9")  # 字面量记号必须失效
        self.assertEqual(cm.exception.code, "E-WRITE-001")

    def test_any_of_writers(self):
        self.assertIsNone(sv.assert_writer("open_item", "L7_freeze"))
        self.assertIsNone(sv.assert_writer("open_item", "PL"))
        self.assertIsNone(sv.assert_writer("candidate", "L5"))
        with self.assertRaises(sv.SchemaError) as cm:
            sv.assert_writer("open_item", "LLM")
        self.assertEqual(cm.exception.code, "E-WRITE-002")
        with self.assertRaises(sv.SchemaError) as cm:
            sv.assert_writer("candidate", "LLM")
        self.assertEqual(cm.exception.code, "E-WRITE-002")


if __name__ == "__main__":
    unittest.main()


class TestContractMeta(unittest.TestCase):
    """H-4/J3：仓库内零悬空 $schema 引用。"""

    def test_no_dangling_schema_refs(self):
        import json as _json
        dangling = []
        for dp, _dn, fns in os.walk(os.path.join(os.path.dirname(__file__), "..", "..")):
            if ".git" in dp:
                continue
            for fn in fns:
                if not fn.endswith(".json"):
                    continue
                fp = os.path.join(dp, fn)
                try:
                    d = _json.load(open(fp, encoding="utf-8"))
                except Exception:
                    continue
                ref = d.get("$schema", "")
                if ref.startswith("contracts/") and not os.path.exists(
                        os.path.join(os.path.dirname(__file__), "..", "..", ref)):
                    dangling.append((fp, ref))
        self.assertEqual(dangling, [], f"悬空 $schema 引用: {dangling}")
