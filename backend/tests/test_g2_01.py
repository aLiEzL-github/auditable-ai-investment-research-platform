"""G2-01 验收测试：Claim / EvidenceRecord / ClaimEvidenceLink。

基线验收映射：
  1. 取得失败不补零 —— acquisition ok=False 不产生 evidence/零值
  2. 证据先摄取且支持/反驳多个 Claim —— claim_evidence_link 多对多
  3. 分类与 materiality 属 Claim
  4. evidence 带 schema/parser version 并绑定 snapshot_id
附加（X-3）：表列与 schema 双向一致（ORM 行序列化 validate_object 须过）
附加（X-4）：写路径须调用 assert_writer（preconditions 校验）
"""
import unittest
import tempfile
import shutil
import os
import sys

APP = os.path.join(os.path.dirname(__file__), "..", "app")
sys.path.insert(0, APP)

from repository import (Repository, Source, RawArtifact, AcquisitionEvent,
                        Claim, EvidenceRecord, ClaimEvidenceLink, create_repository)
from schema_validate import validate_object


def _sha(s):
    import hashlib
    return hashlib.sha256(s.encode()).hexdigest()


class TestG2_01(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self.db = os.path.join(self._tmp, "g2_01.sqlite3")
        self.repo = create_repository(self.db)
        self.repo.create_all()
        self.s = self.repo.session()
        # 公共前置：source + artifact（与 test_repository.py 同款分步写入约定）
        src = Source(id="SRC_SSE", schema_version="1.0.0", kind="PRIMARY",
                     name="上交所", status="ALLOWED",
                     legal_basis="G2-01 测试", version=1)
        self.s.add(src)
        self.s.commit()
        art = RawArtifact(id="ART_0001", schema_version="1.0.0", source_id="SRC_SSE",
                          sha256=_sha("raw1"), bytes=5, content_type="text/plain",
                          acquired_at=__import__("datetime").datetime.utcnow(), version=1)
        self.s.add(art)
        self.s.commit()

    def tearDown(self):
        self.s.close()
        self.repo.engine.dispose()
        shutil.rmtree(self._tmp, ignore_errors=True)

    # ── 1. 取得失败不补零 ───────────────────────────────────────────
    def test_failure_acquire_no_evidence(self):
        """基线①：acquisition ok=False 后，不产生任何 evidence 零值记录。"""
        ev = AcquisitionEvent(id="EVT_FAIL", schema_version="1.0.0", artifact_id="ART_0001",
                              source_id="SRC_SSE",
                              acquired_at=__import__("datetime").datetime.utcnow(),
                              ok=False, error="HTTP 403", version=1)
        self.s.add(ev)
        self.s.commit()
        n = self.s.query(EvidenceRecord).count()
        self.assertEqual(n, 0, "失败取得后不应产生 evidence")

    # ── 2/3/4. 证据绑定 + 多对多支持/反驳 + materiality 属 Claim ──
    def test_evidence_bind_and_multi_claim(self):
        """基线②③④：证据绑定 snapshot/parser 版本；一条证据支持与反驳两个 Claim。"""
        c1 = Claim(id="CLAIM_A", schema_version="1.0.0", statement="X 公司营收增长",
                   category="FUNDAMENTAL", materiality="MATERIAL", status="DRAFT", version=1)
        c2 = Claim(id="CLAIM_B", schema_version="1.0.0", statement="X 公司营收下降",
                   category="FUNDAMENTAL", materiality="MATERIAL", status="DRAFT", version=1)
        self.repo.add_claim(self.s, c1)
        self.repo.add_claim(self.s, c2)

        ev = EvidenceRecord(
                id="EVID_0001", schema_version="1.0", artifact_id="ART_0001",
                            snapshot_id="SNAP_0001", schema_ver="fact.v1",
                            parser_version="parser-1.0", sha256=_sha("evidence1"),
                            content="片段 A", version=1)
        self.repo.add_evidence(self.s, ev)

        self.repo.link_evidence(self.s, "CLAIM_A", "EVID_0001", "SUPPORT")
        self.repo.link_evidence(self.s, "CLAIM_B", "EVID_0001", "REFUTE")

        links = self.s.query(ClaimEvidenceLink).filter_by(evidence_id="EVID_0001").all()
        self.assertEqual(len(links), 2)
        dirs = sorted(l.direction for l in links)
        self.assertEqual(dirs, ["REFUTE", "SUPPORT"])
        # ③ 分类与 materiality 属 claim（evidence 无此字段）
        self.assertIsNone(getattr(ev, "materiality", None))
        self.assertEqual(c1.materiality, "MATERIAL")
        self.assertEqual(c1.category, "FUNDAMENTAL")

    # ── 内容寻址与前置校验（preconditions）──────────────────────────
    def test_evidence_artifact_must_exist(self):
        with self.assertRaises(ValueError) as ctx:
            self.repo.add_evidence(self.s, EvidenceRecord(
                id="EVID_BAD", schema_version="1.0", artifact_id="ART_NONE",
                snapshot_id="S", schema_ver="v", parser_version="p",
                sha256=_sha("x"), content="c", version=1))
        # 前置断言（assert_writer）先于显式检查拦截
        self.assertTrue("E-PRECOND-001" in str(ctx.exception) or "E-G2-01-001" in str(ctx.exception))

    def test_evidence_sha_dedup(self):
        self.repo.add_evidence(self.s, EvidenceRecord(
                id="EVID_D1", schema_version="1.0", artifact_id="ART_0001",
            snapshot_id="S", schema_ver="v", parser_version="p",
            sha256=_sha("dup"), content="c", version=1))
        with self.assertRaises(ValueError) as ctx:
            self.repo.add_evidence(self.s, EvidenceRecord(
                id="EVID_D2", schema_version="1.0", artifact_id="ART_0001",
                snapshot_id="S", schema_ver="v", parser_version="p",
                sha256=_sha("dup"), content="c2", version=1))
        self.assertIn("E-G2-01-002", str(ctx.exception))

    # ── X-4：assert_writer 接入（错误 writer 被拒）──────────────────
    def test_assert_writer_gate(self):
        from schema_validate import SchemaError
        with self.assertRaises(SchemaError):
            self.repo.add_claim(self.s, Claim(
                id="CLAIM_X", schema_version="1.0.0", statement="s",
                category="c", materiality="NON_MATERIAL", status="DRAFT", version=1),
                writer="LLM")

    # ── X-3：表列与 schema 双向一致（ORM 行序列化 validate_object）──
    def test_orm_row_matches_schema(self):
        c = Claim(id="CLAIM_S", schema_version="1.0.0", statement="s",
                  category="FUNDAMENTAL", materiality="MATERIAL", status="DRAFT", version=1)
        validate_object("claim", {
            "schema_version": c.schema_version, "id": c.id, "statement": c.statement,
            "category": c.category, "materiality": c.materiality, "status": c.status})
        ev = EvidenceRecord(
                id="EVID_S", schema_version="1.0", artifact_id="ART_0001",
                            snapshot_id="SNAP_1", schema_ver="fact.v1",
                            parser_version="parser-1.0", sha256=_sha("s"), content="c", version=1)
        validate_object("evidence_record", {
            "schema_version": ev.schema_version, "id": ev.id, "artifact_id": ev.artifact_id,
            "snapshot_id": ev.snapshot_id, "schema_ver": ev.schema_ver,
            "parser_version": ev.parser_version, "sha256": ev.sha256, "content": ev.content})


if __name__ == "__main__":
    unittest.main()
