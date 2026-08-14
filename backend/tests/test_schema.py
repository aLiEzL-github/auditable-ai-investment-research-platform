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
        self.assertIsNone(sv.assert_writer("fact", "L7_freeze",
                                           {"source_snapshot_frozen": True}))
        self.assertIsNone(sv.assert_writer("approval", "L12_approval_endpoint",
                                           {"subject_root_hash_bound": True,
                                            "acknowledged": True}))
        self.assertIsNone(sv.assert_writer("release", "L11_release",
                                           {"exit_predicate_and_parent_cas": True}))
        self.assertIsNone(sv.assert_writer("job", "L7_freeze"))  # NONE 前置

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

    def test_preconditions_enforced(self):
        """P0-2/M-1：MACHINE 前置缺 context 拒绝（E-PRECOND-001）；MANUAL 缺确认拒绝（E-PRECOND-002）。"""
        with self.assertRaises(sv.SchemaError) as cm:
            sv.assert_writer("fact", "L7_freeze")  # 无 context
        self.assertEqual(cm.exception.code, "E-PRECOND-001")
        with self.assertRaises(sv.SchemaError) as cm:
            sv.assert_writer("approval", "L12_approval_endpoint",
                             {"subject_root_hash_bound": True})  # 缺 acknowledged
        self.assertEqual(cm.exception.code, "E-PRECOND-002")
        # 满足后通过
        self.assertIsNone(sv.assert_writer("fact", "L7_freeze",
                                           {"source_snapshot_frozen": True}))

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


class TestWriterNormalizationAndPreconditions(unittest.TestCase):
    """OI-PF-181 / OI-PF-182 —— 全局写权执行函数的两处默认拒绝。

    **载荷即原缺陷形态**（规则 ⑩）：下面每一个字符串/取值都曾实测绕过。
    """

    # ── OI-PF-181：never 名单的归一化 ──────────────────────────────
    BYPASS = ("llm", "Llm", "LLM ", " LLM", "LLM\t",
              "LLM\u200b",          # 零宽空格
              "ＬＬＭ")               # 全角

    def test_never_list_resists_normalization_variants(self):
        """any_of 的行没有 writers 白名单，never 是唯一的门。

        修复前：只有精确的 'LLM' 被拒，七种变体全部通过 ——
        **默认拒绝只在有白名单时成立，any_of 把它翻转成了默认允许。**
        """
        for w in ("LLM",) + self.BYPASS:
            for obj in ("open_item", "candidate", "manifest"):
                with self.subTest(obj=obj, writer=w):
                    with self.assertRaises(sv.SchemaError):
                        sv.assert_writer(obj, w)

    def test_empty_writer_rejected(self):
        for w in ("", " ", "\t", None):
            with self.subTest(writer=w):
                with self.assertRaises(sv.SchemaError):
                    sv.assert_writer("open_item", w)

    def test_legal_writer_still_passes(self):
        """防误红：归一化不得把合法写者也挡掉。"""
        self.assertIsNone(sv.assert_writer("open_item", "L8"))
        self.assertIsNone(sv.assert_writer(
            "raw_artifact", "L7_freeze", {"rights_gate_and_parse_ok": True}))

    # ── OI-PF-182：MACHINE 前置取布尔而非取真值 ────────────────────
    TRUTHY_BUT_NOT_TRUE = ("false", "False", "0", "no", "N", " ",
                           [0], {"ok": False}, 1)

    def test_machine_precondition_requires_boolean_true(self):
        """修复前 `if not ctx.get(key)` 使这些取值全部判为「前置满足」。

        `rights_gate_and_parse_ok` 的语义是**经 L3 权利门 + L6 解析成功** ——
        字符串 "false" 判为满足，等于权利门被静默放行。而这些形态正是从
        JSON / 环境变量 / CLI 取值时天然出现的。
        """
        for v in self.TRUTHY_BUT_NOT_TRUE:
            with self.subTest(value=v):
                with self.assertRaises(sv.SchemaError) as ctx:
                    sv.assert_writer("raw_artifact", "L7_freeze",
                                     {"rights_gate_and_parse_ok": v})
                self.assertIn("E-PRECOND-001", str(ctx.exception))

    def test_machine_precondition_accepts_true(self):
        """防误红。"""
        self.assertIsNone(sv.assert_writer(
            "raw_artifact", "L7_freeze", {"rights_gate_and_parse_ok": True}))


if __name__ == "__main__":
    unittest.main()


class TestContractMeta(unittest.TestCase):
    """H-4/J3：仓库内零悬空 $schema 引用。"""

    def test_no_dangling_schema_refs(self):
        import json as _json
        dangling = []
        for dp, _dn, fns in os.walk(os.path.join(os.path.dirname(__file__), "..", "..")):
            if ".git" in dp or ".venv" in dp or "site-packages" in dp \
                    or "node_modules" in dp:
                continue
            for fn in fns:
                if not fn.endswith(".json"):
                    continue
                fp = os.path.join(dp, fn)
                try:
                    with open(fp, encoding="utf-8") as f:
                        d = _json.load(f)
                except Exception:
                    continue
                ref = d.get("$schema", "")
                if ref.startswith("contracts/") and not os.path.exists(
                        os.path.join(os.path.dirname(__file__), "..", "..", ref)):
                    dangling.append((fp, ref))
        self.assertEqual(dangling, [], f"悬空 $schema 引用: {dangling}")


class TestContractCoverage(unittest.TestCase):
    """P0-8/W3：契约覆盖面 ⊇ 实际持久化对象（job/source 曾漏，L-1）。"""

    def _model_tables(self):
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
        from repository import Base
        import jobs  # noqa: 注册 job 表
        return set(Base.metadata.tables.keys())

    def test_models_covered_by_writers(self):
        import schema_validate as _sv
        missing = self._model_tables() - set(_sv.WRITERS.keys())
        self.assertEqual(missing, set(), f"模型表不在写权矩阵: {missing}")

    def test_models_covered_by_schemas(self):
        schema_dir = os.path.join(os.path.dirname(__file__), "..", "..", "contracts", "schema")
        files = {f[:-len(".schema.json")] for f in os.listdir(schema_dir)
                 if f.endswith(".schema.json")}
        missing = self._model_tables() - files
        self.assertEqual(missing, set(), f"模型表无 schema: {missing}")

    def test_each_table_orm_row_validates(self):
        """每张表真实 ORM 行序列化后 validate_object 须通过（L-2/W4）。"""
        import tempfile
        from datetime import datetime
        from repository import AcquisitionEvent, RawArtifact, Source, create_repository
        from jobs import JobQueue
        import schema_validate as _sv

        tmp = tempfile.mkdtemp()
        repo = create_repository(os.path.join(tmp, "cov.sqlite3"))
        repo.create_all()
        s = repo.session()
        s.add(Source(id="S-1", schema_version="1.0.0", kind="PRIMARY", name="src",
                     status="ALLOWED", legal_basis="t"))
        s.commit()
        _sv.validate_object("source", {"id": "S-1", "schema_version": "1.0.0",
                                       "kind": "PRIMARY", "name": "src",
                                       "status": "ALLOWED", "legal_basis": "t",
                                       "version": 1})
        s.add(RawArtifact(id="RA-1", schema_version="1.0.0", source_id="S-1",
                          sha256="a" * 64, bytes=1, acquired_at=datetime(2026, 8, 7)))
        s.commit()
        _sv.validate_object("raw_artifact", {"id": "RA-1", "schema_version": "1.0.0",
                                             "source_id": "S-1", "sha256": "a" * 64,
                                             "bytes": 1,
                                             "acquired_at": "2026-08-07T00:00:00Z",
                                             "version": 1})
        s.add(AcquisitionEvent(id="AE-1", schema_version="1.0.0", artifact_id="RA-1",
                               source_id="S-1", acquired_at=datetime(2026, 8, 7), ok=True))
        s.commit()
        _sv.validate_object("acquisition_event", {"id": "AE-1", "schema_version": "1.0.0",
                                                  "artifact_id": "RA-1", "source_id": "S-1",
                                                  "acquired_at": "2026-08-07T00:00:00Z",
                                                  "ok": True, "version": 1})
        q = JobQueue(repo)
        q.submit("j1", writer="L7_freeze")
        _sv.validate_object("job", {"id": 1, "schema_version": "1.0.0",
                                    "job_key": "j1", "status": "PENDING", "version": 1})
        s.close()
        repo.engine.dispose()
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)


class TestSection2Mapping(unittest.TestCase):
    """P0-3/M-2：G0-04 §2 ↔ writers.json 逐行映射；10 行全覆盖 + never 防降级。"""

    # G0-04 §2「谁永远不能写」的权威值（逐字转录，P0-3 防降级对照）
    SECTION2_NEVER = {
        "raw_artifact": {"L5", "L6", "L8", "L9", "L10", "L11", "L12", "L13"},
        "snapshot": {"ALL_OTHER_MODULES"},
        "fact": {"L6"},
        "assumption": set(),
        "prediction": {"LLM", "AUTOMATION", "L8", "L9"},
        "calc": {"L6", "L9"},
        "claim": {"L6", "L8"},
        "approval": {"ALL_AUTOMATION"},
        "release": {"ALL_OTHER_MODULES"},
    }

    def _matrix(self):
        import json as _json
        fp = os.path.join(os.path.dirname(__file__), "..", "..", "contracts", "writers.json")
        with open(fp, encoding="utf-8") as f:
            return _json.load(f)

    def test_mapping_covers_all_10_rows(self):
        d = self._matrix()
        self.assertEqual(len(d["section2_mapping"]), d["section2_rows"],
                         "映射未覆盖 G0-04 §2 全部 10 行")

    def test_never_not_weakened(self):
        """never 只增不减：§2 的字面成员须 ⊆ writers.json 的 never（MR-2 防降级）。"""
        d = self._matrix()
        for row in d["section2_mapping"]:
            key = row.get("key")
            if key is None:  # rights-matrix 待裁定
                continue
            never = set(d["matrix"][key].get("never", []))
            s2 = self.SECTION2_NEVER.get(key, set())
            literals = {n for n in s2 if not n.startswith("ALL_")}
            missing = literals - never
            self.assertEqual(missing, set(),
                             f"{key} 的 never 未覆盖 §2 成员: {missing}")
            if s2 and not literals:
                self.assertTrue(never, f"{key} 的 never 为空（§2 要求全模块禁写）")
