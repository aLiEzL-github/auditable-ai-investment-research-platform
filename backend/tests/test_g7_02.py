"""G7-02 全链真实候选 + 另一真实来源冒烟验收测试。

覆盖（对照 G7-02 原子任务书验收 + 首轮审查 13 项收口）：
  1. NBS 身份与网络边界：生产只允许 https://www.stats.gov.cn + 官方路径形状；
     禁任意 scheme/host/userinfo/端口/query/fragment/绝对 URL/路径穿越；
     publication_date 从 source_url 路径 tYYYYMMDD 确定并绑定；取得阶段即
     检查 cutoff；测试回环网络只经显式注入 adapter。
  2. MacroAdapter 防御：无日期失败关闭（不回退当前日期）；Content-Length /
     实际读取超限均失败关闭；G2-05 既有测试保持兼容。
  3. 仓外强制：--store/--out/--company-input/--macro-manifest/--macro-raw
     resolve 后必须在 Git ROOT 外；manifest O_NOFOLLOW+排他写禁止覆盖；
     nbs-acquire 要求 clean checkout 并把 source commit/tree 写入 manifest。
  4. strict JSON / 最小披露：parse_constant 拒绝 NaN/Infinity，canonical
     allow_nan=False；异常不含材料事实原值或原始时间字面量。
  5. macro manifest / rights 绑定：source_url、source revision、embedded
     RightsDecision 与 RightsGuard 重判一致；decided_at ≤ acquired_at；
     publication_date ≤ cutoff；不错误要求 acquired_at ≤ cutoff。
  6. raw 完整性：freeze 用 store.load() 校验哈希与字节（不只 exists()）；
     verify 默认从对象库加载 raw；--macro-raw 可选交叉比对。
  7. 600089 来源与覆盖：pack/request 绑定 SRC_CNINFO/600089-issuer-legal-
     filings/IMPORT/本次 RightsDecision；缺 artifact 明细降 PARTIAL 不升
     FULL；显式非官方 family BLOCKED；back_source 键集精确一致、时间 ISO。
  8. 期间覆盖防假绿：移除 /YYYY/ 回退；FULL 按 metric×period 矩阵；单个
     2024 事实不能使全体 FULL；pack 暴露 per-metric missing bindings。
  9. 真实事实进入 frozen context（g7_02_ 前缀键）；pack/stdout 无值。
  10. 整体状态分轴：顶层恒 PARTIAL + company.data_status 分离 +
      SINGLE_REVIEWER_ATTESTED + blocks_gate=G7。
  11. verify 交叉绑定：stored request 与 pack 逐字比对，错绑 E-G7-02-022。
  12. 测试质量：删除 object_count()==16 脆断言；验证禁止对象种类与全部
      声明依赖可读；每项至少一条回归。

全部 fixture 显式 SYNTHETIC_FIXTURE，数值为合成值，不复制任何真实披露。
"""
import argparse
import contextlib
import hashlib
import importlib.util
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "app"))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "tools"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from artifact_store import ArtifactStore  # noqa: E402
from candidate_service import FINAL_CANDIDATE_REQUEST_KIND  # noqa: E402
from g7_02_service import (  # noqa: E402
    G7_02Error,
    IMPORT_ACTION,
    IMPORT_SOURCE_FAMILY,
    IMPORT_SOURCE_KEY,
    NBS_SOURCE_FAMILY,
    NBS_SOURCE_ID,
    PACK_KIND,
    REQUIRED_PERIODS,
    SINGLE_REVIEWER_ATTESTED,
    WRITE_AXES,
    _canonical,
    freeze_pack,
    validate_company_input,
    validate_macro_manifest,
    verify_pack,
)
from macro_adapter import (  # noqa: E402
    NBS_PRODUCTION_BASE_URL,
    NBS_SCOPE_RE,
    MacroAdapter,
    _parse_publication_date,
    _publication_date_from_url,
    validate_nbs_target,
)
from rights_guard import GuardDenied, RightsGuard  # noqa: E402
from schema_validate import SchemaError, validate_object  # noqa: E402

FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "fixtures", "g7-02")
_REV_A = "a" * 40
_TREE_A = "b" * 40
_REV_B = "a" * 39 + "b"
CUTOFF = "2026-08-16T09:21:00Z"
AS_OF = "2026-08-16"
CONTRACT_ID = "C-600089-G7-02"
RUN_ID = "G7-02-test-run"
SYNTHETIC_RAW = b"SYNTHETIC G7-02 NBS page for smoke test only\n"


def _load(name):
    with open(os.path.join(FIXTURES, name), encoding="utf-8") as fh:
        return json.load(fh)


def _company_full():
    return _load("company_input_full.json")


def _company_missing_2024():
    return _load("company_input_missing_2024.json")


def _manifest():
    m = _load("macro_manifest.json")
    m["raw_sha256"] = hashlib.sha256(SYNTHETIC_RAW).hexdigest()
    m["raw_bytes"] = len(SYNTHETIC_RAW)
    # 官方形状 source_url 由测试侧构造（合成 fixture 不得含真实形态 locator）。
    m["source_url"] = ("https://www.stats.gov.cn/sj/zxfbhjd/202607/"
                       "t20260716_1.html")
    m["scope"] = "/sj/zxfbhjd/202607/t20260716_1.html"
    m["rights_decision"]["scope"] = "/sj/zxfbhjd/202607/t20260716_1.html"
    return m


def _sha(obj) -> str:
    return hashlib.sha256(_canonical(obj)).hexdigest()


def _import_deny_matrix():
    """CNINFO IMPORT 拒绝（600089 人工导入被权利门拦下）；NBS 保持 ALLOWED。"""
    return {
        "schema": "rights-matrix-mirror/1.0",
        "produced_at": "2026-08-16T00:00:00Z",
        "policy": {"default": "RESEARCH_ONLY"},
        "data_sources": [
            {"source_key": "SRC_NBS", "actions": {
                "acquire_public_statistics": "ALLOWED（测试）"}},
            {"source_key": "SRC_CNINFO", "actions": {
                "manual_download_by_human": "PROHIBITED（测试矩阵）"}},
        ],
    }


def _nbs_deny_matrix():
    """NBS FETCH 拒绝 —— 冒烟必须零网络失败关闭。"""
    return {
        "schema": "rights-matrix-mirror/1.0",
        "produced_at": "2026-08-16T00:00:00Z",
        "policy": {"default": "RESEARCH_ONLY"},
        "data_sources": [
            {"source_key": "SRC_NBS", "actions": {
                "acquire_public_statistics": "PROHIBITED（测试矩阵）"}},
        ],
    }


def _all_fact_values(company):
    return [f["value"] for f in company["facts"].values()]


class _StoreBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self.store = ArtifactStore(os.path.join(self._tmp, "lib"))

    def tearDown(self):
        shutil.rmtree(self._tmp, ignore_errors=True)

    def seed_raw(self):
        return self.store.store("g7_02_macro_raw", SYNTHETIC_RAW)

    def freeze(self, company=None, manifest=None, seed_raw=True, **kw):
        if seed_raw:
            self.seed_raw()
        company = _company_full() if company is None else company
        manifest = _manifest() if manifest is None else manifest
        return freeze_pack(
            self.store, company_input=company, macro_manifest=manifest,
            source_commit=kw.pop("source_commit", _REV_A),
            source_tree=kw.pop("source_tree", _TREE_A),
            cutoff_at=kw.pop("cutoff_at", CUTOFF),
            as_of_date=kw.pop("as_of_date", AS_OF),
            contract_id=kw.pop("contract_id", CONTRACT_ID),
            run_id=kw.pop("run_id", RUN_ID),
            company_raw_sha256=kw.pop("company_raw_sha256", _sha(company)),
            macro_manifest_raw_sha256=kw.pop(
                "macro_manifest_raw_sha256", _sha(manifest)), **kw)

    def object_path(self, digest):
        return os.path.join(str(self.store.root), digest[:2], digest[2:4],
                            digest[4:])

    def tamper(self, digest, data=b"tampered-byte-mutation"):
        target = self.object_path(digest)
        os.chmod(target, 0o600)
        with open(target, "wb") as fh:
            fh.write(data)

    def object_kinds(self):
        kinds = []
        for dp, _, fns in os.walk(str(self.store.root)):
            for fn in fns:
                with open(os.path.join(dp, fn), "rb") as fh:
                    body = fh.read()
                if body[:1] != b"{":
                    continue
                obj = json.loads(body.decode("utf-8"))
                if isinstance(obj, dict):
                    kinds.append(obj.get("kind"))
        return kinds


class TestCompanyInputValidation(unittest.TestCase):
    def test_full_input_fulls_and_covers_both_periods(self):
        v = validate_company_input(_company_full())
        self.assertEqual(v.data_status, "FULL")
        self.assertEqual(v.missing_periods, [])
        self.assertEqual(v.missing_bindings, [])
        self.assertEqual(v.period_status, {"2025": True, "2024": True})
        self.assertTrue(v.source_complete)
        self.assertEqual(v.ticker, "600089")
        self.assertEqual(v.source_doc_count, 1)
        self.assertEqual(v.material_fact_count, 6)
        self.assertEqual(v.material_verified_count, 6)
        self.assertRegex(v.input_sha256, r"^[0-9a-f]{64}$")

    def test_missing_2024_is_honest_partial_not_upgraded(self):
        company = _company_missing_2024()
        self.assertEqual(company["status"], "COMPLETE")
        v = validate_company_input(company)
        self.assertEqual(v.data_status, "PARTIAL")
        self.assertEqual(v.missing_periods, ["2024"])
        self.assertEqual(v.period_status, {"2025": True, "2024": False})
        self.assertIn("营业收入/2024", v.missing_bindings)
        self.assertIn("归母净利润/2024", v.missing_bindings)

    def test_single_2024_fact_does_not_make_full(self):
        """防假绿：单个 2024 事实不能使全体 FULL（矩阵按 metric×period）。"""
        company = _company_full()
        for metric in ("归母净利润_2024", "货币资金_2024"):
            del company["facts"][metric]
            del company["back_source"][metric]
        v = validate_company_input(company)
        self.assertEqual(v.data_status, "PARTIAL")
        self.assertIn("归母净利润/2024", v.missing_bindings)
        self.assertIn("货币资金/2024", v.missing_bindings)

    def test_locator_slash_year_fallback_removed(self):
        """防假绿：任意 /YYYY/ 回退已移除，只认年末日期或 #YYYY。"""
        company = _company_full()
        for metric in list(company["facts"]):
            loc = f"synthetic://g7-02/2025/source/{metric}"
            company["facts"][metric]["locator"] = loc
            company["back_source"][metric]["locator"] = loc
        v = validate_company_input(company)
        self.assertEqual(v.data_status, "PARTIAL")
        self.assertEqual(v.missing_periods, ["2025", "2024"])

    def test_anchored_hash_year_locator_accepted(self):
        """#YYYY 锚点被识别为参考期；同一归一 metric×period 不得重复（收口：
        后写覆盖前写已禁止，故各期 locator 分别锚定，不制造同格重复）。"""
        company = _company_full()
        for metric in list(company["facts"]):
            year = "2024" if metric.endswith("_2024") else "2025"
            loc = f"synthetic://g7-02/annual-report#{year}"
            company["facts"][metric]["locator"] = loc
            company["back_source"][metric]["locator"] = loc
        v = validate_company_input(company)
        self.assertEqual(v.missing_bindings, [])
        self.assertEqual(v.period_status, {"2025": True, "2024": True})
        self.assertEqual(v.data_status, "FULL")

    def test_missing_artifact_hash_degrades_partial_not_full(self):
        """兼容但降级：缺原始 artifact hash/source 明细只 PARTIAL，不升 FULL。"""
        company = _company_full()
        del company["source_docs"][0]["artifact_sha256"]
        v = validate_company_input(company)
        self.assertFalse(v.source_complete)
        self.assertEqual(v.data_status, "PARTIAL")
        self.assertEqual(v.missing_periods, [])

    def test_missing_artifact_source_family_degrades_partial(self):
        company = _company_full()
        company["source_docs"][0]["source_family"] = "unknown-family"
        v = validate_company_input(company)
        self.assertFalse(v.source_complete)
        self.assertEqual(v.data_status, "PARTIAL")

    def test_registered_at_must_be_iso(self):
        """收口：source_docs[].registered_at 必须是 ISO 时间。"""
        company = _company_full()
        company["source_docs"][0]["registered_at"] = "not-a-time"
        with self.assertRaises(G7_02Error) as cm:
            validate_company_input(company)
        self.assertIn("E-G7-02-000", str(cm.exception))

    def test_source_complete_requires_root_source_identity(self):
        """收口：source_complete=true 要求根 source_id/source_family 显式正确；
        外部 baseline 缺这些字段继续 PARTIAL。"""
        company = _company_full()
        del company["source_id"]
        v = validate_company_input(company)
        self.assertFalse(v.source_complete)
        self.assertEqual(v.data_status, "PARTIAL")
        company = _company_full()
        del company["source_family"]
        v = validate_company_input(company)
        self.assertFalse(v.source_complete)
        self.assertEqual(v.data_status, "PARTIAL")

    def test_source_complete_requires_doc_action_import(self):
        """收口：source_complete=true 要求每个 source doc 的 action=IMPORT 完整。"""
        company = _company_full()
        del company["source_docs"][0]["action"]
        v = validate_company_input(company)
        self.assertFalse(v.source_complete)
        self.assertEqual(v.data_status, "PARTIAL")

    def test_duplicate_normalized_metric_period_blocked(self):
        """收口：同一归一 metric+period 重复（后写覆盖前写）→ 失败关闭。"""
        company = _company_full()
        duplicate = dict(company["facts"]["营业收入_2024"])
        company["facts"]["营业收入_2025"] = duplicate
        company["back_source"]["营业收入_2025"] = dict(
            company["back_source"]["营业收入_2024"])
        with self.assertRaises(G7_02Error) as cm:
            validate_company_input(company)
        self.assertIn("E-G7-02-009", str(cm.exception))

    def test_wrong_ticker_blocked(self):
        company = _company_full()
        company["ticker"] = "600999"
        with self.assertRaises(G7_02Error):
            validate_company_input(company)

    def test_empty_source_docs_blocked(self):
        company = _company_full()
        company["source_docs"] = []
        with self.assertRaises(G7_02Error):
            validate_company_input(company)

    def test_missing_material_back_source_blocked(self):
        company = _company_full()
        del company["back_source"]["营业收入"]
        with self.assertRaises(G7_02Error) as cm:
            validate_company_input(company)
        self.assertIn("E-G7-02-006", str(cm.exception))

    def test_back_source_key_set_must_match_material_facts(self):
        company = _company_full()
        company["back_source"]["extra_key"] = dict(
            company["back_source"]["营业收入"])
        with self.assertRaises(G7_02Error):
            validate_company_input(company)
        company = _company_full()
        del company["back_source"]["营业收入_2024"]
        with self.assertRaises(G7_02Error):
            validate_company_input(company)

    def test_back_source_time_must_be_iso(self):
        company = _company_full()
        company["back_source"]["营业收入"]["at"] = "not-a-time"
        with self.assertRaises(G7_02Error):
            validate_company_input(company)

    def test_back_source_locator_mismatch_blocked(self):
        company = _company_full()
        company["back_source"]["营业收入"]["locator"] = \
            "synthetic://g7-02/wrong/locator"
        with self.assertRaises(G7_02Error) as cm:
            validate_company_input(company)
        self.assertIn("E-G7-02-007", str(cm.exception))

    def test_back_source_not_verified_blocked(self):
        company = _company_full()
        company["back_source"]["营业收入"]["state"] = "PENDING"
        with self.assertRaises(G7_02Error):
            validate_company_input(company)

    def test_back_source_reviewer_or_timestamp_empty_blocked(self):
        for key in ("reviewed_by", "at"):
            with self.subTest(key=key):
                company = _company_full()
                company["back_source"]["营业收入"][key] = "   "
                with self.assertRaises(G7_02Error):
                    validate_company_input(company)

    def test_non_finite_value_blocked(self):
        for bad in ("NaN", "Infinity", "1e999", "abc", "", "1,000"):
            with self.subTest(value=bad):
                company = _company_full()
                company["facts"]["营业收入"]["value"] = bad
                with self.assertRaises(G7_02Error) as cm:
                    validate_company_input(company)
                self.assertIn("E-G7-02-005", str(cm.exception))

    def test_missing_fact_locator_blocked(self):
        company = _company_full()
        company["facts"]["营业收入"]["locator"] = ""
        with self.assertRaises(G7_02Error):
            validate_company_input(company)

    def test_same_source_masquerade_blocked(self):
        company = _company_full()
        company["facts"]["营业收入"]["second_source"] = "SRC_NBS"
        with self.assertRaises(G7_02Error) as cm:
            validate_company_input(company)
        self.assertIn("E-G7-02-008", str(cm.exception))
        company = _company_full()
        company["back_source"]["营业收入"]["dual_source"] = True
        with self.assertRaises(G7_02Error):
            validate_company_input(company)

    def test_nbs_masquerade_as_financial_dual_source_blocked(self):
        company = _company_full()
        company["facts"]["营业收入"]["source_family"] = NBS_SOURCE_FAMILY
        with self.assertRaises(G7_02Error):
            validate_company_input(company)
        company = _company_full()
        company["back_source"]["营业收入"]["source_id"] = NBS_SOURCE_ID
        with self.assertRaises(G7_02Error):
            validate_company_input(company)

    def test_forbidden_declared_source_family_blocked(self):
        for bad in ("aggregator", "AKShare", "synthetic", "nbs-official",
                    "third-party"):
            for where in ("facts", "back_source"):
                with self.subTest(marker=bad, where=where):
                    company = _company_full()
                    owner = company[where]["营业收入"]
                    owner["source_family"] = bad
                    with self.assertRaises(G7_02Error):
                        validate_company_input(company)

    def test_wrong_issuer_source_id_blocked(self):
        company = _company_full()
        company["source_id"] = "SRC_OTHER"
        with self.assertRaises(G7_02Error):
            validate_company_input(company)

    def test_error_message_does_not_contain_fact_value(self):
        """最小披露：异常不得含材料事实原值或原始时间字面量。"""
        company = _company_full()
        company["facts"]["营业收入"]["value"] = "1234567890.55"
        company["facts"]["营业收入"]["value"] = "abc"
        with self.assertRaises(G7_02Error) as cm:
            validate_company_input(company)
        self.assertNotIn("1234567890.55", str(cm.exception))
        self.assertNotIn("2026-08-08T00:00:00+00:00", str(cm.exception))


class TestMacroManifestValidation(unittest.TestCase):
    def test_valid_manifest_passes(self):
        m, sha = validate_macro_manifest(
            _manifest(), cutoff_at=CUTOFF, source_commit=_REV_A,
            source_tree=_TREE_A)
        self.assertEqual(m["source_id"], NBS_SOURCE_ID)
        self.assertEqual(m["source_family"], NBS_SOURCE_FAMILY)
        self.assertRegex(sha, r"^[0-9a-f]{64}$")

    def test_valid_manifest_with_guard_rejudge_passes(self):
        guard = RightsGuard()
        m, _ = validate_macro_manifest(
            _manifest(), cutoff_at=CUTOFF, source_commit=_REV_A,
            source_tree=_TREE_A, guard=guard)
        self.assertEqual(
            m["rights_decision"]["policy_version"], guard.policy_version)

    def test_non_nbs_source_blocked(self):
        m = _manifest()
        m["source_id"] = "SRC_OTHER"
        with self.assertRaises(G7_02Error):
            validate_macro_manifest(m, cutoff_at=CUTOFF,
                                    source_commit=_REV_A, source_tree=_TREE_A)

    def test_wrong_family_blocked(self):
        m = _manifest()
        m["source_family"] = "aggregator"
        with self.assertRaises(G7_02Error):
            validate_macro_manifest(m, cutoff_at=CUTOFF,
                                    source_commit=_REV_A, source_tree=_TREE_A)

    def test_non_official_source_url_host_blocked(self):
        m = _manifest()
        m["source_url"] = "https://evil.example/sj/x/202607/t20260716_syn.html"
        with self.assertRaises(G7_02Error) as cm:
            validate_macro_manifest(m, cutoff_at=CUTOFF,
                                    source_commit=_REV_A, source_tree=_TREE_A)
        self.assertIn("E-G7-02-010", str(cm.exception))

    def test_source_url_http_port_query_fragment_blocked(self):
        for bad in (
                "http://www.stats.gov.cn/sj/x/t20260716_s.html",
                "https://www.stats.gov.cn:8443/sj/x/t20260716_s.html",
                "https://user@www.stats.gov.cn/sj/x/t20260716_s.html",
                "https://www.stats.gov.cn/sj/x/t20260716_s.html?page=2",
                "https://www.stats.gov.cn/sj/x/t20260716_s.html#frag",
                "https://www.stats.gov.cn/sj/x/../t20260716_s.html",
                "https://www.stats.gov.cn/not-sj/t20260716_s.html"):
            with self.subTest(url=bad):
                m = _manifest()
                m["source_url"] = bad
                with self.assertRaises(G7_02Error):
                    validate_macro_manifest(
                        m, cutoff_at=CUTOFF, source_commit=_REV_A,
                        source_tree=_TREE_A)

    def test_publication_date_not_bound_to_source_url_blocked(self):
        """发布日必须由 source_url 路径确定并绑定（不信任 manifest 自证）。"""
        m = _manifest()
        m["publication_date"] = "2026-07-15"
        with self.assertRaises(G7_02Error):
            validate_macro_manifest(m, cutoff_at=CUTOFF,
                                    source_commit=_REV_A, source_tree=_TREE_A)

    def test_source_url_missing_path_date_blocked(self):
        m = _manifest()
        m["source_url"] = "https://www.stats.gov.cn/sj/zxfb/synthetic.html"
        with self.assertRaises(G7_02Error):
            validate_macro_manifest(m, cutoff_at=CUTOFF,
                                    source_commit=_REV_A, source_tree=_TREE_A)

    def test_manifest_source_revision_drift_blocked(self):
        m = _manifest()
        m["source_commit"] = _REV_B
        with self.assertRaises(G7_02Error):
            validate_macro_manifest(m, cutoff_at=CUTOFF,
                                    source_commit=_REV_A, source_tree=_TREE_A)

    def test_manifest_source_revision_invalid_blocked(self):
        m = _manifest()
        m["source_tree"] = "short"
        with self.assertRaises(G7_02Error):
            validate_macro_manifest(m, cutoff_at=CUTOFF,
                                    source_commit=_REV_A, source_tree=_TREE_A)

    def test_non_allowed_rights_blocked(self):
        m = _manifest()
        m["rights_decision"]["verdict"] = "UNKNOWN"
        with self.assertRaises(G7_02Error):
            validate_macro_manifest(m, cutoff_at=CUTOFF,
                                    source_commit=_REV_A, source_tree=_TREE_A)
        m = _manifest()
        m["rights_decision"]["action"] = "PARSE"
        with self.assertRaises(G7_02Error):
            validate_macro_manifest(m, cutoff_at=CUTOFF,
                                    source_commit=_REV_A, source_tree=_TREE_A)

    def test_rights_decision_must_match_manifest_scope(self):
        m = _manifest()
        m["rights_decision"]["scope"] = "SRC_OTHER"
        with self.assertRaises(G7_02Error):
            validate_macro_manifest(m, cutoff_at=CUTOFF,
                                    source_commit=_REV_A, source_tree=_TREE_A)

    def test_manifest_scope_must_equal_source_url_path(self):
        """收口：scope 必须逐字等于 source_url 的 path，禁止抽象范围冒充。"""
        m = _manifest()
        m["scope"] = "/sj/zxfbhjd/202607/t20260716_2.html"
        with self.assertRaises(G7_02Error) as cm:
            validate_macro_manifest(m, cutoff_at=CUTOFF,
                                    source_commit=_REV_A, source_tree=_TREE_A)
        self.assertIn("E-G7-02-010", str(cm.exception))
        # 抽象 CN_A_SHARE 冒充实际取得范围同样拒绝。
        m = _manifest()
        m["scope"] = "CN_A_SHARE"
        m["rights_decision"]["scope"] = "CN_A_SHARE"
        with self.assertRaises(G7_02Error) as cm:
            validate_macro_manifest(m, cutoff_at=CUTOFF,
                                    source_commit=_REV_A, source_tree=_TREE_A)
        self.assertIn("E-G7-02-010", str(cm.exception))

    def test_rights_decision_scope_must_equal_source_url_path(self):
        """收口：embedded rights_decision.scope 也必须等于 source_url 的 path。"""
        m = _manifest()
        m["rights_decision"]["scope"] = "/sj/zxfbhjd/202607/t20260716_2.html"
        with self.assertRaises(G7_02Error) as cm:
            validate_macro_manifest(m, cutoff_at=CUTOFF,
                                    source_commit=_REV_A, source_tree=_TREE_A)
        self.assertIn("E-G7-02-011", str(cm.exception))

    def test_manifest_cutoff_mismatch_with_freeze_cutoff_blocked(self):
        """收口：manifest.cutoff_at 与 freeze/verify 传入 cutoff 规范化后须
        逐时刻相等（双 cutoff 不一致即失败关闭，不只分别晚于 publication_date）。"""
        m = _manifest()
        m["cutoff_at"] = "2026-08-16T10:21:00Z"  # 与 CUTOFF 不同时刻
        with self.assertRaises(G7_02Error) as cm:
            validate_macro_manifest(m, cutoff_at=CUTOFF,
                                    source_commit=_REV_A, source_tree=_TREE_A)
        self.assertIn("E-G7-02-014", str(cm.exception))
        # 不同时区但同一时刻：规范化后相等，须通过（防误红）。
        m = _manifest()
        m["cutoff_at"] = "2026-08-16T17:21:00+08:00"
        validate_macro_manifest(m, cutoff_at=CUTOFF,
                                source_commit=_REV_A, source_tree=_TREE_A)

    def test_path_publication_date_rejects_impossible_calendar_date(self):
        """收口：日期片段用真实 calendar date 校验（2026-02-30 非法）。"""
        m = _manifest()
        m["source_url"] = ("https://www.stats.gov.cn/sj/zxfbhjd/202602/"
                           "t20260230_1.html")
        with self.assertRaises(G7_02Error):
            validate_macro_manifest(m, cutoff_at=CUTOFF,
                                    source_commit=_REV_A, source_tree=_TREE_A)

    def test_rights_decision_policy_version_must_match_current_matrix(self):
        """embedded RightsDecision 的 policy_version 须与当前矩阵重判一致。"""
        m = _manifest()
        m["rights_decision"]["policy_version"] = "2020-01-01T00:00:00Z"
        guard = RightsGuard()
        with self.assertRaises(G7_02Error):
            validate_macro_manifest(m, cutoff_at=CUTOFF,
                                    source_commit=_REV_A, source_tree=_TREE_A,
                                    guard=guard)

    def test_decided_at_after_acquired_at_blocked(self):
        m = _manifest()
        m["rights_decision"]["decided_at"] = "2026-08-16T03:00:00+00:00"
        with self.assertRaises(G7_02Error) as cm:
            validate_macro_manifest(m, cutoff_at=CUTOFF,
                                    source_commit=_REV_A, source_tree=_TREE_A)
        self.assertIn("E-G7-02-011", str(cm.exception))

    def test_acquired_at_after_cutoff_allowed(self):
        """检索可晚于 cutoff —— 不错误要求 acquired_at ≤ cutoff。"""
        m = _manifest()
        m["acquired_at"] = "2026-08-17T00:00:00+00:00"
        m["rights_decision"]["decided_at"] = "2026-08-16T02:00:00+00:00"
        validate_macro_manifest(m, cutoff_at=CUTOFF,
                                source_commit=_REV_A, source_tree=_TREE_A)

    def test_empty_body_blocked(self):
        m = _manifest()
        m["raw_bytes"] = 0
        with self.assertRaises(G7_02Error) as cm:
            validate_macro_manifest(m, cutoff_at=CUTOFF,
                                    source_commit=_REV_A, source_tree=_TREE_A)
        self.assertIn("E-G7-02-013", str(cm.exception))

    def test_bad_raw_sha_blocked(self):
        m = _manifest()
        m["raw_sha256"] = "zz" * 32
        with self.assertRaises(G7_02Error):
            validate_macro_manifest(m, cutoff_at=CUTOFF,
                                    source_commit=_REV_A, source_tree=_TREE_A)

    def test_publication_after_cutoff_blocked(self):
        m = _manifest()
        m["publication_date"] = "2026-08-17"
        m["source_url"] = ("https://www.stats.gov.cn/sj/zxfbhjd/202608/"
                           "t20260817_1.html")
        m["scope"] = "/sj/zxfbhjd/202608/t20260817_1.html"
        m["rights_decision"]["scope"] = "/sj/zxfbhjd/202608/t20260817_1.html"
        with self.assertRaises(G7_02Error) as cm:
            validate_macro_manifest(m, cutoff_at=CUTOFF,
                                    source_commit=_REV_A, source_tree=_TREE_A)
        self.assertIn("E-G7-02-014", str(cm.exception))

    def test_nbs_claiming_financial_dual_source_blocked(self):
        m = _manifest()
        m["is_financial_dual_source_for_600089"] = True
        with self.assertRaises(G7_02Error) as cm:
            validate_macro_manifest(m, cutoff_at=CUTOFF,
                                    source_commit=_REV_A, source_tree=_TREE_A)
        self.assertIn("E-G7-02-012", str(cm.exception))

    def test_gate_status_must_be_partial_context_only(self):
        m = _manifest()
        m["gate_status"]["quality_status"] = "GATE_OK"
        with self.assertRaises(G7_02Error):
            validate_macro_manifest(m, cutoff_at=CUTOFF,
                                    source_commit=_REV_A, source_tree=_TREE_A)

    def test_missing_attribution_blocked(self):
        m = _manifest()
        m["attribution"] = ""
        with self.assertRaises(G7_02Error):
            validate_macro_manifest(m, cutoff_at=CUTOFF,
                                    source_commit=_REV_A, source_tree=_TREE_A)


class TestMacroAdapterDefense(unittest.TestCase):
    """G7-02 首轮审查负例（G2-05 既有测试保持兼容）。"""

    def test_no_date_fails_closed_no_wallclock_fallback(self):
        with self.assertRaises(ValueError):
            _parse_publication_date("<html>no dates here</html>")

    def test_publication_date_from_path_ok(self):
        self.assertEqual(
            _publication_date_from_url(
                "https://www.stats.gov.cn/sj/zxfbhjd/202607/t20260716_1.html"),
            "2026-07-16")

    def test_publication_date_from_path_missing_fails_closed(self):
        with self.assertRaises(RuntimeError):
            _publication_date_from_url(
                "https://www.stats.gov.cn/sj/zxfb/synthetic.html")

    def test_publication_date_from_path_rejects_impossible_calendar_date(self):
        """收口：路径日期片段用真实 calendar date 校验。"""
        with self.assertRaises(RuntimeError):
            _publication_date_from_url(
                "https://www.stats.gov.cn/sj/zxfbhjd/202602/t20260230_1.html")

    def test_parse_publication_date_rejects_impossible_calendar_date(self):
        with self.assertRaises(ValueError):
            _parse_publication_date("2026年2月30日发布")
        with self.assertRaises(ValueError):
            _parse_publication_date("2026年13月1日发布")

    def test_service_and_adapter_scope_regexes_are_single_source(self):
        """收口：app 层 service 与 tools 层 macro_adapter 的官方路径形状须
        逐字一致（行为单源）。"""
        import g7_02_service as _svc
        self.assertEqual(_svc.NBS_SCOPE_RE.pattern, NBS_SCOPE_RE.pattern)

    def test_strict_origin_rejects_wrong_release_page_shape(self):
        """收口：官方路径收紧为本任务实际发布页形状
        /sj/zxfbhjd/\d{6}/t\d{8}_\d+\.html。"""
        for bad in (
                "/sj/zxfb/202607/t20260716_1.html",       # 非 zxfbhjd 栏目
                "/sj/zxfbhjd/202607/t20260716_syn.html",  # 后缀非纯数字
                "/sj/zxfbhjd/202607/t20260716_1.htm",     # 非 .html
                "/sj/zxfbhjd/202607/1.html",              # 缺 t 日期片段
                "/sj/zxfbhjd/202607/t20260716_1.html/extra"):
            with self.subTest(scope=bad):
                with self.assertRaises(ValueError):
                    validate_nbs_target(NBS_PRODUCTION_BASE_URL, bad,
                                        strict_origin=True)

    def test_strict_origin_rejects_wrong_scheme(self):
        with self.assertRaises(ValueError):
            validate_nbs_target("http://www.stats.gov.cn", "/sj/x",
                                strict_origin=True)

    def test_strict_origin_rejects_wrong_host(self):
        with self.assertRaises(ValueError):
            validate_nbs_target("https://evil.example", "/sj/x",
                                strict_origin=True)

    def test_strict_origin_rejects_port_userinfo(self):
        with self.assertRaises(ValueError):
            validate_nbs_target("https://www.stats.gov.cn:8443", "/sj/x",
                                strict_origin=True)
        with self.assertRaises(ValueError):
            validate_nbs_target("https://user@www.stats.gov.cn", "/sj/x",
                                strict_origin=True)

    def test_scope_rejects_query_fragment_absolute_traversal(self):
        guard = RightsGuard()
        for scope in ("/sj/x?q=1", "/sj/x#f", "https://evil/x",
                      "//evil/x", "/sj/x/../t20260716.html",
                      "/sj/x/..", "/sj/foo//bar"):
            with self.subTest(scope=scope):
                with self.assertRaises(ValueError):
                    validate_nbs_target(NBS_PRODUCTION_BASE_URL, scope,
                                        strict_origin=True)

    def test_scope_requires_official_sj_shape(self):
        with self.assertRaises(ValueError):
            validate_nbs_target(NBS_PRODUCTION_BASE_URL, "/data",
                                strict_origin=True)
        with self.assertRaises(ValueError):
            validate_nbs_target(NBS_PRODUCTION_BASE_URL, "/xxkf/x",
                                strict_origin=True)

    def test_valid_official_scope_passes(self):
        url = validate_nbs_target(
            NBS_PRODUCTION_BASE_URL,
            "/sj/zxfbhjd/202607/t20260716_1.html", strict_origin=True)
        self.assertEqual(
            url,
            "https://www.stats.gov.cn/sj/zxfbhjd/202607/t20260716_1.html")

    def test_content_length_over_cap_failed_closed(self):
        srv = _Responder(body=b"x" * 65, status=200, content_length=65)()
        try:
            adapter = _loopback_adapter(
                f"http://127.0.0.1:{srv.server_address[1]}", max_body=64,
                mode="body")
            with self.assertRaises(RuntimeError) as cm:
                adapter.fetch("/sj/x")
            self.assertIn("Content-Length", str(cm.exception))
        finally:
            srv.shutdown()
            srv.server_close()

    def test_actual_body_over_cap_failed_closed(self):
        # 服务端不声明 Content-Length，实际读取超限须失败关闭。
        srv = _Responder(body=b"y" * 200, status=200, content_length=None)()
        try:
            adapter = _loopback_adapter(
                f"http://127.0.0.1:{srv.server_address[1]}", max_body=64,
                mode="body")
            with self.assertRaises(RuntimeError) as cm:
                adapter.fetch("/sj/x")
            self.assertIn("响应体上限", str(cm.exception))
        finally:
            srv.shutdown()
            srv.server_close()


class TestFreezePack(_StoreBase):
    def test_full_input_freezes_pack_with_honest_axes(self):
        result = self.freeze()
        self.assertRegex(result.pack_id, r"^[0-9a-f]{64}$")
        # 顶层分轴：company FULL，但 macro PARTIAL+CONTEXT_ONLY / G6A 四路
        # NOT_EVALUATED ⇒ 顶层恒 PARTIAL。
        self.assertEqual(result.company_data_status, "FULL")
        self.assertEqual(result.candidate_status, "PARTIAL")
        self.assertEqual(result.pack["candidate_status"], "PARTIAL")
        self.assertEqual(result.pack["company"]["data_status"], "FULL")
        self.assertEqual(result.pack["g6a_candidate"]["quality_status"],
                         "PARTIAL")
        self.assertIs(result.pack["g6a_candidate"]["release_eligible"], False)
        self.assertEqual(result.pack["g6a_candidate"]["product_count"], 11)
        # Gate/发布轴独立且不提升。
        self.assertIs(result.pack["gate_status"]["gate7_reached"], False)
        self.assertIs(result.pack["gate_status"]["gate_release_eligible"],
                      False)
        self.assertEqual(
            result.pack["single_source_disclosed"], "SINGLE_SOURCE_DISCLOSED")
        self.assertEqual(
            result.pack["reviewer_independence"], SINGLE_REVIEWER_ATTESTED)
        # 绑定哈希齐全。
        self.assertRegex(result.pack["company"]["input_sha256"],
                         r"^[0-9a-f]{64}$")
        self.assertRegex(result.pack["company"]["input_raw_sha256"],
                         r"^[0-9a-f]{64}$")
        self.assertRegex(result.pack["macro"]["manifest_sha256"],
                         r"^[0-9a-f]{64}$")
        self.assertRegex(result.pack["macro"]["manifest_raw_sha256"],
                         r"^[0-9a-f]{64}$")
        self.assertRegex(result.pack["macro"]["raw_sha256"],
                         r"^[0-9a-f]{64}$")

    def test_pack_binds_issuer_source_identity(self):
        """pack/受管 request 绑定发行人 source_id/family/action/RightsDecision。"""
        result = self.freeze()
        self.assertEqual(result.pack["company"]["source_id"], IMPORT_SOURCE_KEY)
        self.assertEqual(result.pack["company"]["source_family"],
                         IMPORT_SOURCE_FAMILY)
        self.assertEqual(result.pack["company"]["import_action"], IMPORT_ACTION)
        rd = result.pack["company"]["import_rights_decision"]
        self.assertEqual(rd["source_id"], IMPORT_SOURCE_KEY)
        self.assertEqual(rd["action"], IMPORT_ACTION)
        self.assertEqual(rd["verdict"], "ALLOWED")

    def test_open_items_policy_blocks_gate_is_g7(self):
        result = self.freeze()
        req = json.loads(self.store.load(result.request_hash))
        self.assertEqual(req["context"]["open_items_policy"]["blocks_gate"],
                         "G7")

    def test_missing_2024_yields_honest_partial_axes(self):
        result = self.freeze(company=_company_missing_2024())
        self.assertEqual(result.company_data_status, "PARTIAL")
        self.assertEqual(result.candidate_status, "PARTIAL")
        self.assertEqual(result.pack["missing_periods"], ["2024"])
        self.assertIn("营业收入/2024", result.missing_bindings)
        self.assertIs(result.pack["gate_status"]["gate7_reached"], False)
        self.assertIs(result.pack["gate_status"]["gate_release_eligible"],
                      False)

    def test_deterministic_same_inputs_same_pack_id(self):
        a = self.freeze()
        b = self.freeze()
        self.assertEqual(a.pack_id, b.pack_id)

    def test_different_revision_changes_pack_id(self):
        a = self.freeze()
        manifest = _manifest()
        manifest["source_commit"] = _REV_B
        manifest["source_tree"] = _TREE_A
        b = self.freeze(source_commit=_REV_B, source_tree=_TREE_A,
                        manifest=manifest)
        self.assertNotEqual(a.pack_id, b.pack_id)

    def test_manifest_revision_drift_at_freeze_blocked(self):
        """freeze 时 manifest source revision 与当前代码版本漂移 → 失败关闭。"""
        manifest = _manifest()
        manifest["source_commit"] = _REV_B
        with self.assertRaises(G7_02Error) as cm:
            self.freeze(manifest=manifest)
        self.assertIn("E-G7-02-010", str(cm.exception))

    def test_no_approval_release_writes_and_no_forbidden_kinds(self):
        result = self.freeze()
        self.assertEqual(result.pack["write_counts"],
                         {axis: 0 for axis in WRITE_AXES})
        kinds = set(self.object_kinds())
        for forbidden in ("Approval", "DecisionVersion", "Release",
                          "CurrentPointer", "latest", "trade"):
            self.assertNotIn(forbidden, kinds)

    def test_all_declared_dependencies_readable(self):
        """全部声明依赖（含 candidate 的 11 产品）可读且可校验。"""
        result = self.freeze()
        for dep in (result.pack["company"]["input_sha256"],
                    result.pack["macro"]["manifest_sha256"],
                    result.pack["macro"]["raw_sha256"],
                    result.pack["g6a_candidate"]["request_hash"],
                    result.pack["g6a_candidate"]["candidate_id"]):
            self.assertTrue(self.store.exists(dep))
            self.assertRegex(dep, r"^[0-9a-f]{64}$")
        cand = json.loads(self.store.load(result.candidate_id))
        for digest in cand["product_hashes"].values():
            self.assertTrue(self.store.exists(digest))
            self.assertRegex(digest, r"^[0-9a-f]{64}$")

    def test_facts_frozen_into_request_context_with_prefix_keys(self):
        """真实材料事实值进入 frozen context（g7_02_ 前缀，不冲突路由键）。"""
        result = self.freeze()
        req = json.loads(self.store.load(result.request_hash))
        facts = req["context"]["facts"]
        company = _company_full()
        for metric, fact in company["facts"].items():
            if fact["material"]:
                self.assertEqual(facts[f"g7_02_{metric}"], fact["value"])
        for rk in ("fcff", "fcfe", "eps", "book_per_share"):
            self.assertNotIn(rk, facts)

    def test_pack_contains_no_fact_values(self):
        result = self.freeze()
        pack_bytes = _canonical(result.pack)
        text = pack_bytes.decode("utf-8")
        for value in _all_fact_values(_company_full()):
            self.assertNotIn(value, text,
                             "pack 不得含材料性事实真实批量数值")
        self.assertNotIn('"value"', text, "pack 不得出现 value 键")
        self.assertNotIn(SYNTHETIC_RAW.decode("utf-8"), text)

    def test_import_rights_denied_zero_write(self):
        guard = RightsGuard(matrix=_import_deny_matrix())
        with self.assertRaises(G7_02Error) as cm:
            self.freeze(guard=guard, seed_raw=False)
        self.assertIn("E-G7-02-015", str(cm.exception))
        self.assertEqual(len(self.object_kinds()), 0)

    def test_freeze_rejects_non_sha256_company_raw(self):
        """收口：freeze 入口在任何对象写入前验证 company_raw_sha256 为严格 sha256。"""
        with self.assertRaises(G7_02Error) as cm:
            self.freeze(seed_raw=False, company_raw_sha256="short")
        self.assertIn("E-G7-02-001", str(cm.exception))
        self.assertEqual(len(self.object_kinds()), 0)

    def test_freeze_rejects_non_sha256_manifest_raw(self):
        with self.assertRaises(G7_02Error) as cm:
            self.freeze(seed_raw=False, macro_manifest_raw_sha256="xx" * 32)
        self.assertIn("E-G7-02-001", str(cm.exception))
        self.assertEqual(len(self.object_kinds()), 0)

    def test_freeze_rejects_foreign_rights_source_even_if_allowed(self):
        """收口：import_source_key 必须精确等于 SRC_CNINFO —— 禁止调用方用别的
        权利源但 pack 仍声称 CNINFO（即便该源在矩阵中 ALLOWED）。"""
        matrix = {
            "schema": "rights-matrix-mirror/1.0",
            "produced_at": "2026-08-16T00:00:00Z",
            "policy": {"default": "RESEARCH_ONLY"},
            "data_sources": [
                {"source_key": "SRC_OTHER", "actions": {
                    "manual_download_by_human": "ALLOWED（测试矩阵）"}},
            ],
        }
        guard = RightsGuard(matrix=matrix)
        with self.assertRaises(G7_02Error) as cm:
            self.freeze(guard=guard, import_source_key="SRC_OTHER",
                        seed_raw=False)
        self.assertIn("E-G7-02-001", str(cm.exception))
        self.assertEqual(len(self.object_kinds()), 0)

    def test_macro_raw_object_missing_blocked(self):
        with self.assertRaises(G7_02Error) as cm:
            self.freeze(seed_raw=False)
        self.assertIn("E-G7-02-016", str(cm.exception))
        self.assertEqual(len(self.object_kinds()), 0)

    def test_macro_raw_object_tampered_blocked(self):
        """freeze 前用 store.load() 校验 raw 内容哈希（不只 exists()）。"""
        self.seed_raw()
        company = _company_full()
        manifest = _manifest()
        self.tamper(manifest["raw_sha256"], b"tampered raw object")
        with self.assertRaises(G7_02Error) as cm:
            self.freeze(company=company, manifest=manifest, seed_raw=False)
        self.assertIn("E-G7-02-016", str(cm.exception))

    def test_macro_raw_object_byte_mismatch_blocked(self):
        """store.load() 成功但 raw_bytes 与 manifest 不符 → 失败关闭。"""
        self.store.store("g7_02_macro_raw", b"short-raw")
        manifest = _manifest()
        with self.assertRaises(G7_02Error) as cm:
            self.freeze(manifest=manifest, seed_raw=False)
        self.assertIn("E-G7-02-016", str(cm.exception))

    def test_publication_after_cutoff_freezing_blocked(self):
        manifest = _manifest()
        manifest["publication_date"] = "2026-08-17"
        manifest["source_url"] = ("https://www.stats.gov.cn/sj/zxfbhjd/202608/"
                                  "t20260817_1.html")
        manifest["scope"] = "/sj/zxfbhjd/202608/t20260817_1.html"
        manifest["rights_decision"]["scope"] = "/sj/zxfbhjd/202608/t20260817_1.html"
        with self.assertRaises(G7_02Error) as cm:
            self.freeze(manifest=manifest)
        self.assertIn("E-G7-02-014", str(cm.exception))

    def test_nbs_masquerade_financial_dual_source_blocked(self):
        manifest = _manifest()
        manifest["is_financial_dual_source_for_600089"] = True
        with self.assertRaises(G7_02Error):
            self.freeze(manifest=manifest)

    def test_valid_pack_passes_canonical_schema(self):
        result = self.freeze()
        self.assertIsNone(validate_object(PACK_KIND, result.pack))

    def test_pack_schema_mutations_rejected(self):
        result = self.freeze()
        pack = dict(result.pack)
        del pack["candidate_status"]
        with self.assertRaises(SchemaError):
            validate_object(PACK_KIND, pack)
        pack = dict(result.pack)
        pack["write_counts"] = dict(result.pack["write_counts"], Approval=1)
        with self.assertRaises(SchemaError):
            validate_object(PACK_KIND, pack)
        pack = dict(result.pack)
        pack["gate_status"] = {"gate7_reached": True,
                               "gate_release_eligible": False}
        with self.assertRaises(SchemaError):
            validate_object(PACK_KIND, pack)
        pack = dict(result.pack)
        pack["macro"]["source_family"] = "aggregator"
        with self.assertRaises(SchemaError):
            validate_object(PACK_KIND, pack)
        pack = dict(result.pack)
        pack["g6a_candidate"]["release_eligible"] = True
        with self.assertRaises(SchemaError):
            validate_object(PACK_KIND, pack)
        pack = dict(result.pack)
        pack["candidate_status"] = "FULL"
        with self.assertRaises(SchemaError):
            validate_object(PACK_KIND, pack)
        pack = dict(result.pack)
        del pack["company"]["data_status"]
        with self.assertRaises(SchemaError):
            validate_object(PACK_KIND, pack)
        pack = dict(result.pack)
        pack["company"]["source_id"] = "SRC_OTHER"
        with self.assertRaises(SchemaError):
            validate_object(PACK_KIND, pack)


class TestVerifyPack(_StoreBase):
    def verify(self, result, **kw):
        company = kw.pop("company_input", _company_full())
        manifest = kw.pop("macro_manifest", _manifest())
        return verify_pack(
            self.store, result.pack_id,
            company_input=company,
            macro_manifest=manifest,
            macro_raw=kw.pop("macro_raw", None),
            source_commit=kw.pop("source_commit", _REV_A),
            source_tree=kw.pop("source_tree", _TREE_A),
            company_raw_sha256=kw.pop("company_raw_sha256", _sha(company)),
            macro_manifest_raw_sha256=kw.pop(
                "macro_manifest_raw_sha256", _sha(manifest)), **kw)

    def _lift(self, result, mutate_req):
        """重存受管 request（含变异）并把 pack 的 request_hash 重新指向它。"""
        req = json.loads(self.store.load(result.request_hash))
        mutate_req(req)
        req_id = self.store.store(FINAL_CANDIDATE_REQUEST_KIND,
                                  _canonical(req))
        pack = dict(result.pack)
        pack["g6a_candidate"]["request_hash"] = req_id
        return self.store.store(PACK_KIND, _canonical(pack))

    def _verify_lifted(self, lifted_id, **kw):
        return verify_pack(
            self.store, lifted_id,
            company_input=kw.pop("company_input", _company_full()),
            macro_manifest=kw.pop("macro_manifest", _manifest()),
            source_commit=kw.pop("source_commit", _REV_A),
            source_tree=kw.pop("source_tree", _TREE_A),
            company_raw_sha256=kw.pop("company_raw_sha256",
                                      _sha(_company_full())),
            macro_manifest_raw_sha256=kw.pop("macro_manifest_raw_sha256",
                                             _sha(_manifest())), **kw)

    def test_verify_healthy_defaults_to_store_raw(self):
        result = self.freeze()
        out = self.verify(result)
        self.assertEqual(out["candidate_status"], "PARTIAL")
        self.assertEqual(out["company_data_status"], "FULL")
        self.assertEqual(out["product_count"], 11)
        self.assertEqual(out["quality_status"], "PARTIAL")
        self.assertIs(out["release_eligible"], False)
        self.assertIs(out["gate7_reached"], False)
        self.assertIs(out["gate_release_eligible"], False)
        self.assertEqual(out["reviewer_independence"],
                         SINGLE_REVIEWER_ATTESTED)

    def test_verify_missing_2024_pack_healthy(self):
        result = self.freeze(company=_company_missing_2024())
        out = self.verify(result, company_input=_company_missing_2024())
        self.assertEqual(out["candidate_status"], "PARTIAL")
        self.assertEqual(out["company_data_status"], "PARTIAL")
        self.assertEqual(out["missing_periods"], ["2024"])
        self.assertIn("营业收入/2024", out["missing_bindings"])

    def test_verify_optional_macro_raw_cross_check(self):
        result = self.freeze()
        self.verify(result, macro_raw=SYNTHETIC_RAW)
        with self.assertRaises(G7_02Error):
            self.verify(result, macro_raw=b"different raw bytes")

    def test_tampered_pack_fails(self):
        result = self.freeze()
        self.tamper(result.pack_id, b"corrupted pack body")
        with self.assertRaises(G7_02Error):
            self.verify(result)

    def test_tampered_candidate_fails(self):
        result = self.freeze()
        self.tamper(result.candidate_id, b"corrupted candidate body")
        with self.assertRaises(G7_02Error):
            self.verify(result)

    def test_tampered_macro_raw_object_fails(self):
        result = self.freeze()
        self.tamper(result.pack["macro"]["raw_sha256"], b"corrupted raw")
        with self.assertRaises(G7_02Error):
            self.verify(result)

    def test_company_input_byte_change_fails(self):
        result = self.freeze()
        company = _company_full()
        company["facts"]["营业收入"]["value"] = "999999999.99"
        with self.assertRaises(G7_02Error) as cm:
            self.verify(result, company_input=company)
        self.assertIn("E-G7-02-020", str(cm.exception))

    def test_company_raw_sha_mismatch_fails(self):
        result = self.freeze()
        company = _company_full()
        with self.assertRaises(G7_02Error):
            self.verify(result, company_raw_sha256="0" * 64)

    def test_verify_rejects_foreign_rights_source(self):
        """收口：verify 入口同样要求 import_source_key == SRC_CNINFO。"""
        result = self.freeze()
        with self.assertRaises(G7_02Error) as cm:
            self.verify(result, import_source_key="SRC_OTHER")
        self.assertIn("E-G7-02-020", str(cm.exception))

    def test_verify_rejects_non_sha256_raw_params(self):
        """收口：verify 入口验证 raw 哈希为严格 sha256。"""
        result = self.freeze()
        with self.assertRaises(G7_02Error) as cm:
            self.verify(result, company_raw_sha256="short")
        self.assertIn("E-G7-02-020", str(cm.exception))
        with self.assertRaises(G7_02Error):
            self.verify(result, macro_manifest_raw_sha256="x" * 64)

    def test_request_facts_misbind_with_consistent_hashes_fails(self):
        """收口：候选请求事实值错绑但 hash 字段自洽 → 必须失败（E-G7-02-022）。"""
        result = self.freeze()
        lifted = self._lift(
            result,
            lambda req: req["context"]["facts"].update(
                {"g7_02_营业收入": "999999999.99"}))
        with self.assertRaises(G7_02Error) as cm:
            self._verify_lifted(lifted)
        self.assertIn("facts", str(cm.exception))
        self.assertIn("E-G7-02-022", str(cm.exception))

    def test_request_macro_descriptor_misbind_fails(self):
        """收口：重建并比对完整 macro descriptor —— 错绑必须失败。"""
        result = self.freeze()
        lifted = self._lift(
            result,
            lambda req: req["context"]["macro"].update(
                {"publication_date": "2026-07-15"}))
        with self.assertRaises(G7_02Error) as cm:
            self._verify_lifted(lifted)
        self.assertIn("macro descriptor", str(cm.exception))

    def test_request_valuation_inputs_misbind_fails(self):
        result = self.freeze()
        lifted = self._lift(
            result,
            lambda req: req["context"]["valuation_inputs"].update(
                {"as_of": "2026-08-15"}))
        with self.assertRaises(G7_02Error) as cm:
            self._verify_lifted(lifted)
        self.assertIn("valuation_inputs", str(cm.exception))

    def test_request_contract_field_misbind_fails(self):
        """收口：contract_id/workflow/market_scope/currency/as_of_date/cutoff
        须与 pack 逐字一致。"""
        result = self.freeze()
        for fld, new in (("contract_id", "C-600089-WRONG"),
                         ("workflow", "OTHER_WORKFLOW"),
                         ("market_scope", "HK_EXCHANGE"),
                         ("currency", "USD"),
                         ("as_of_date", "2026-08-15"),
                         ("cutoff_at", "2026-08-16T10:00:00Z")):
            with self.subTest(fld=fld):
                lifted = self._lift(
                    result,
                    lambda req, f=fld, n=new:
                        req["context"]["contract"].update({f: n}))
                with self.assertRaises(G7_02Error) as cm:
                    self._verify_lifted(lifted)
                self.assertIn("E-G7-02-022", str(cm.exception))

    def test_macro_manifest_byte_change_fails(self):
        result = self.freeze()
        manifest = _manifest()
        manifest["publication_date"] = "2026-07-15"
        with self.assertRaises(G7_02Error):
            self.verify(result, macro_manifest=manifest)

    def test_macro_manifest_raw_sha_mismatch_fails(self):
        result = self.freeze()
        manifest = _manifest()
        with self.assertRaises(G7_02Error):
            self.verify(result, macro_manifest_raw_sha256="0" * 64)

    def test_missing_dependency_object_fails(self):
        result = self.freeze()
        os.remove(self.object_path(result.pack["macro"]["raw_sha256"]))
        with self.assertRaises(G7_02Error) as cm:
            self.verify(result)
        self.assertIn("E-G7-02-022", str(cm.exception))

    def test_source_revision_drift_fails(self):
        result = self.freeze()
        with self.assertRaises(G7_02Error):
            self.verify(result, source_commit=_REV_B, source_tree=_TREE_A)

    def test_request_pack_company_hash_misbinding_fails(self):
        """候选 request（公司 A）与 pack/input B 错绑 → E-G7-02-022。"""
        result = self.freeze()
        company_b = _company_full()
        company_b["facts"]["营业收入"]["value"] = "777777777.7"
        pack = dict(result.pack)
        pack["company"]["input_sha256"] = _sha(company_b)
        pack["company"]["input_raw_sha256"] = _sha(company_b)
        lifted = _canonical(pack)
        lifted_id = self.store.store(PACK_KIND, lifted)
        with self.assertRaises(G7_02Error) as cm:
            verify_pack(
                self.store, lifted_id, company_input=company_b,
                macro_manifest=_manifest(), source_commit=_REV_A,
                source_tree=_TREE_A, company_raw_sha256=_sha(company_b),
                macro_manifest_raw_sha256=_sha(_manifest()))
        self.assertIn("E-G7-02-022", str(cm.exception))

    def test_pack_issuer_rights_decision_mismatch_fails(self):
        result = self.freeze()
        pack = dict(result.pack)
        pack["company"]["import_rights_decision"] = dict(
            result.pack["company"]["import_rights_decision"],
            policy_version="tampered")
        lifted = _canonical(pack)
        lifted_id = self.store.store(PACK_KIND, lifted)
        with self.assertRaises(G7_02Error) as cm:
            verify_pack(
                self.store, lifted_id, company_input=_company_full(),
                macro_manifest=_manifest(), source_commit=_REV_A,
                source_tree=_TREE_A, company_raw_sha256=_sha(_company_full()),
                macro_manifest_raw_sha256=_sha(_manifest()))
        self.assertIn("E-G7-02-022", str(cm.exception))

    def test_request_pack_macro_hash_misbinding_fails(self):
        result = self.freeze()
        pack = dict(result.pack)
        pack["macro"]["manifest_sha256"] = "0" * 64
        lifted = _canonical(pack)
        lifted_id = self.store.store(PACK_KIND, lifted)
        with self.assertRaises(G7_02Error) as cm:
            verify_pack(
                self.store, lifted_id, company_input=_company_full(),
                macro_manifest=_manifest(), source_commit=_REV_A,
                source_tree=_TREE_A, company_raw_sha256=_sha(_company_full()),
                macro_manifest_raw_sha256=_sha(_manifest()))
        self.assertIn("E-G7-02-020", str(cm.exception))

    def test_lifted_gate_axis_fails_closed(self):
        result = self.freeze()
        pack = dict(result.pack)
        pack["gate_status"] = {"gate7_reached": True,
                               "gate_release_eligible": False}
        lifted = _canonical(pack)
        lifted_id = self.store.store(PACK_KIND, lifted)
        with self.assertRaises(G7_02Error):
            verify_pack(self.store, lifted_id, company_input=_company_full(),
                        macro_manifest=_manifest(), source_commit=_REV_A,
                        source_tree=_TREE_A, company_raw_sha256=_sha(
                            _company_full()),
                        macro_manifest_raw_sha256=_sha(_manifest()))


class TestArchGuardWiring(unittest.TestCase):
    """arch_import_check 的 G7 豁免断言必须真正接线（B-2c 变异模式）。"""

    @staticmethod
    def _pred():
        import arch_import_check as _mod
        for pth, _desc, pred in _mod.EXEMPT_ASSERTS["L3_fetch"]:
            if pth == "backend/tools/g7_02.py":
                return pred
        raise AssertionError("G7-02 豁免断言缺失")

    def _src(self):
        path = os.path.join(os.path.dirname(__file__), "..", "tools",
                            "g7_02.py")
        with open(path, encoding="utf-8") as fh:
            return fh.read()

    def test_g7_exemption_assert_is_wired(self):
        self.assertTrue(self._pred()(self._src()))

    def test_g7_exemption_mutation_catches_removed_guards(self):
        src = self._src()
        pred = self._pred()
        # 官方 endpoint 校验
        self.assertFalse(pred(src.replace("strict_origin", "origin")))
        self.assertFalse(pred(src.replace("www.stats.gov.cn", "stats.gov")))
        # 仓外路径门
        self.assertFalse(pred(src.replace("_resolve_outside_repo",
                                          "resolve_path")))
        # RightsGuard
        self.assertFalse(pred(src.replace("RightsGuard", "RightCheck")))


class _Responder:
    """合成 HTTP 响应（本地回环，不触网外）。"""

    def __init__(self, body=b"", status=200, content_length=0):
        self.body = body
        self.status = status
        self.content_length = content_length

    def __call__(self, *a, **kw):
        srv = ThreadingHTTPServer(("127.0.0.1", 0), _handler_factory(self))
        self.port = srv.server_address[1]
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.server = srv
        return srv


def _handler_factory(owner):
    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(owner.status)
            if owner.content_length is None:
                pass  # 不声明长度：读上限由实际读取捕获
            else:
                self.send_header("Content-Length", str(len(owner.body)))
            self.end_headers()
            if owner.body:
                self.wfile.write(owner.body)

        def log_message(self, *a):
            pass

    return H


def _loopback_adapter(base_url, guard=None, max_body=1 << 20, mode="path"):
    return MacroAdapter(guard or RightsGuard(), base_url=base_url,
                        min_interval=0.0, timeout=5.0, strict_origin=False,
                        publication_date_mode=mode, max_body_bytes=max_body)


_SMOKE_SCOPE = "/sj/zxfbhjd/202607/t20260716_1.html"


class TestCLI(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        path = os.path.join(os.path.dirname(__file__), "..", "tools",
                            "g7_02.py")
        spec = importlib.util.spec_from_file_location("g7_02_tool_test", path)
        cls.tool = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.tool)

    def test_cli_freeze_verify_roundtrip_no_value_leak(self):
        tool = self.tool
        original = tool.source_revision
        tool.source_revision = lambda: (_REV_A, _TREE_A)
        try:
            with tempfile.TemporaryDirectory() as tmp:
                store = os.path.join(tmp, "objects")
                ArtifactStore(store).store("g7_02_macro_raw", SYNTHETIC_RAW)
                company_path = os.path.join(tmp, "company.json")
                manifest_path = os.path.join(tmp, "manifest.json")
                with open(company_path, "w", encoding="utf-8") as fh:
                    json.dump(_company_full(), fh)
                with open(manifest_path, "w", encoding="utf-8") as fh:
                    json.dump(_manifest(), fh)

                args = argparse.Namespace(
                    company_input=company_path, macro_manifest=manifest_path,
                    store=store, cutoff_at=CUTOFF, as_of_date=AS_OF,
                    contract_id=CONTRACT_ID, run_id=RUN_ID)
                buf = io.StringIO()
                with contextlib.redirect_stdout(buf):
                    rc = tool.cmd_freeze(args)
                self.assertEqual(rc, 0)
                text = buf.getvalue()
                summary = json.loads(text)
                self.assertEqual(summary["gate7_reached"], False)
                self.assertEqual(summary["gate_release_eligible"], False)
                self.assertEqual(summary["reviewer_independence"],
                                 SINGLE_REVIEWER_ATTESTED)
                self.assertEqual(summary["candidate_status"], "PARTIAL")
                self.assertEqual(summary["company_data_status"], "FULL")
                for value in _all_fact_values(_company_full()):
                    self.assertNotIn(value, text,
                                     "stdout 不得泄漏材料性事实数值")

                # verify 不传 --macro-raw：默认从对象库加载 raw（闭环）。
                vargs = argparse.Namespace(
                    pack=summary["pack_id"], store=store,
                    company_input=company_path, macro_manifest=manifest_path,
                    macro_raw="")
                vbuf = io.StringIO()
                with contextlib.redirect_stdout(vbuf):
                    vrc = tool.cmd_verify(vargs)
                self.assertEqual(vrc, 0)
                verified = json.loads(vbuf.getvalue())
                self.assertEqual(verified["pack_id"], summary["pack_id"])
        finally:
            tool.source_revision = original

    def test_cli_requires_clean_checkout(self):
        tool = self.tool
        with tempfile.TemporaryDirectory() as tmp:
            repo = os.path.join(tmp, "repo")
            subprocess.run(["git", "init", "-q", repo], check=True)
            tracked = os.path.join(repo, "tracked.txt")
            with open(tracked, "w", encoding="utf-8") as fh:
                fh.write("clean\n")
            subprocess.run(["git", "-C", repo, "add", "tracked.txt"],
                           check=True)
            subprocess.run(
                ["git", "-C", repo, "-c", "user.name=F",
                 "-c", "user.email=f@example.invalid", "commit", "-qm",
                 "fixture"], check=True)
            original_root = tool.ROOT
            try:
                tool.ROOT = repo
                commit, tree = tool.source_revision()
                self.assertRegex(commit, r"^[0-9a-f]{40}$")
                self.assertRegex(tree, r"^[0-9a-f]{40}$")
                with open(tracked, "a", encoding="utf-8") as fh:
                    fh.write("dirty\n")
                with self.assertRaises(G7_02Error):
                    tool.source_revision()
            finally:
                tool.ROOT = original_root

    def _temp_clean_repo(self):
        tmp = tempfile.mkdtemp()
        repo = os.path.join(tmp, "repo")
        subprocess.run(["git", "init", "-q", repo], check=True)
        tracked = os.path.join(repo, "tracked.txt")
        with open(tracked, "w", encoding="utf-8") as fh:
            fh.write("clean\n")
        subprocess.run(["git", "-C", repo, "add", "tracked.txt"], check=True)
        subprocess.run(
            ["git", "-C", repo, "-c", "user.name=F",
             "-c", "user.email=f@example.invalid", "commit", "-qm",
             "fixture"], check=True)
        return tmp, repo

    def test_cli_rejects_in_repo_inputs(self):
        """仓外强制：--company-input / --store 在 Git ROOT 内 → E-G7-02-035。"""
        tool = self.tool
        original_root = tool.ROOT
        original_rev = tool.source_revision
        tool.source_revision = lambda: (_REV_A, _TREE_A)
        try:
            tmp, repo = self._temp_clean_repo()
            try:
                tool.ROOT = repo
                in_repo = os.path.join(repo, "company.json")
                with open(in_repo, "w", encoding="utf-8") as fh:
                    json.dump(_company_full(), fh)
                args = argparse.Namespace(
                    company_input=in_repo,
                    macro_manifest=os.path.join(tmp, "manifest.json"),
                    store=os.path.join(tmp, "objects"), cutoff_at=CUTOFF,
                    as_of_date=AS_OF, contract_id=CONTRACT_ID, run_id=RUN_ID)
                with self.assertRaises(G7_02Error) as cm:
                    tool.cmd_freeze(args)
                self.assertIn("E-G7-02-035", str(cm.exception))
                # store 在仓内也拒绝。
                args2 = argparse.Namespace(
                    company_input=os.path.join(tmp, "company.json"),
                    macro_manifest=os.path.join(tmp, "manifest.json"),
                    store=os.path.join(repo, "objects"), cutoff_at=CUTOFF,
                    as_of_date=AS_OF, contract_id=CONTRACT_ID, run_id=RUN_ID)
                with self.assertRaises(G7_02Error):
                    tool.cmd_freeze(args2)
            finally:
                shutil.rmtree(tmp, ignore_errors=True)
        finally:
            tool.ROOT = original_root
            tool.source_revision = original_rev

    def test_cli_rejects_path_in_other_git_repo(self):
        """收口：仓外门拒绝位于**任意** Git repository 内的路径，不只当前 ROOT。"""
        tool = self.tool
        original_root = tool.ROOT
        try:
            tmp, repo = self._temp_clean_repo()
            other = os.path.join(tmp, "elsewhere")
            os.makedirs(other)
            tool.ROOT = other  # ROOT 自身不是 git repo —— 证明不靠 ROOT 判断
            try:
                with self.assertRaises(G7_02Error) as cm:
                    tool._resolve_outside_repo(
                        os.path.join(repo, "objects"), "--store")
                self.assertIn("E-G7-02-035", str(cm.exception))
                # 尚不存在的 store 从最近存在父目录向上检测。
                with self.assertRaises(G7_02Error):
                    tool._resolve_outside_repo(
                        os.path.join(repo, "a", "b", "c", "objects"),
                        "--store")
            finally:
                shutil.rmtree(tmp, ignore_errors=True)
        finally:
            tool.ROOT = original_root

    def test_cli_rejects_linked_worktree_path(self):
        """收口：`.git` 为文件的 linked worktree 内的路径同样拒绝。"""
        tool = self.tool
        original_root = tool.ROOT
        with tempfile.TemporaryDirectory() as tmp:
            main = os.path.join(tmp, "main")
            subprocess.run(["git", "init", "-q", main], check=True)
            tracked = os.path.join(main, "tracked.txt")
            with open(tracked, "w", encoding="utf-8") as fh:
                fh.write("clean\n")
            subprocess.run(["git", "-C", main, "add", "tracked.txt"],
                           check=True)
            subprocess.run(
                ["git", "-C", main, "-c", "user.name=F",
                 "-c", "user.email=f@example.invalid", "commit", "-qm",
                 "fixture"], check=True)
            wt = os.path.join(tmp, "wt")
            subprocess.run(
                ["git", "-C", main, "worktree", "add", "-b", "wt-branch",
                 wt], check=True)
            # linked worktree 形状：wt/.git 是文件（gitdir 指针）。
            self.assertTrue(os.path.isfile(os.path.join(wt, ".git")))
            tool.ROOT = os.path.join(tmp, "elsewhere")
            os.makedirs(tool.ROOT)
            try:
                with self.assertRaises(G7_02Error) as cm:
                    tool._resolve_outside_repo(
                        os.path.join(wt, "objects"), "--store")
                self.assertIn("E-G7-02-035", str(cm.exception))
            finally:
                subprocess.run(["git", "-C", main, "worktree", "remove",
                                "--force", wt], check=False)
                tool.ROOT = original_root

    def test_default_run_id_format(self):
        """收口：run_id 无固定可复用默认 —— 未提供时生成
        G7-02-<UTC秒级>-<随机后缀>。"""
        tool = self.tool
        ids = {tool._default_run_id() for _ in range(5)}
        self.assertEqual(len(ids), 5)
        for rid in ids:
            self.assertRegex(rid, r"^G7-02-\d{10}-[0-9a-f]{8}$")

    def test_cli_freeze_generates_unique_run_id_when_absent(self):
        """收口：--run-id 未提供时 cmd_freeze 自动生成，contract_id 同 run_id
        派生；summary 继续返回定位但不泄漏值。"""
        tool = self.tool
        original = tool.source_revision
        tool.source_revision = lambda: (_REV_A, _TREE_A)
        try:
            with tempfile.TemporaryDirectory() as tmp:
                store = os.path.join(tmp, "objects")
                ArtifactStore(store).store("g7_02_macro_raw", SYNTHETIC_RAW)
                company_path = os.path.join(tmp, "company.json")
                manifest_path = os.path.join(tmp, "manifest.json")
                with open(company_path, "w", encoding="utf-8") as fh:
                    json.dump(_company_full(), fh)
                with open(manifest_path, "w", encoding="utf-8") as fh:
                    json.dump(_manifest(), fh)
                args = argparse.Namespace(
                    company_input=company_path, macro_manifest=manifest_path,
                    store=store, cutoff_at=CUTOFF, as_of_date=AS_OF,
                    contract_id=None, run_id=None)
                buf = io.StringIO()
                with contextlib.redirect_stdout(buf):
                    rc = tool.cmd_freeze(args)
                self.assertEqual(rc, 0)
                summary = json.loads(buf.getvalue())
                req = json.loads(ArtifactStore(store).load(
                    summary["request_hash"]))
                run_id = req["run_id"]
                self.assertRegex(run_id, r"^G7-02-\d{10}-[0-9a-f]{8}$")
                self.assertEqual(req["context"]["contract"]["contract_id"],
                                 f"C-600089-{run_id}")
                # summary 不泄漏真实事实值。
                for value in _all_fact_values(_company_full()):
                    self.assertNotIn(value, buf.getvalue())
        finally:
            tool.source_revision = original

    def test_cli_manifest_out_overwrite_rejected(self):
        """--out 排他写入：已存在文件禁止覆盖（O_NOFOLLOW + O_EXCL）。"""
        tool = self.tool
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "manifest.json")
            with open(out, "w", encoding="utf-8") as fh:
                fh.write("{}")
            with self.assertRaises(G7_02Error) as cm:
                tool._write_manifest_exclusive(out, {"a": 1})
            self.assertIn("E-G7-02-035", str(cm.exception))

    def test_cli_manifest_out_exclusive_write_ok(self):
        tool = self.tool
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "manifest.json")
            tool._write_manifest_exclusive(out, {"a": 1})
            with open(out, encoding="utf-8") as fh:
                self.assertEqual(json.load(fh), {"a": 1})

    def test_cli_manifest_out_no_follow_symlink(self):
        """O_NOFOLLOW：--out 指向 symlink 时拒绝（不跟随写穿）。"""
        tool = self.tool
        with tempfile.TemporaryDirectory() as tmp:
            target = os.path.join(tmp, "target")
            with open(target, "w", encoding="utf-8") as fh:
                fh.write("x")
            link = os.path.join(tmp, "link.json")
            os.symlink(target, link)
            with self.assertRaises(G7_02Error):
                tool._write_manifest_exclusive(link, {"a": 1})
            with open(target, encoding="utf-8") as fh:
                self.assertEqual(fh.read(), "x")

    def test_cli_rejects_in_repo_manifest_out(self):
        tool = self.tool
        original_root = tool.ROOT
        try:
            tmp, repo = self._temp_clean_repo()
            tool.ROOT = repo
            try:
                out = os.path.join(repo, "manifest.json")
                with self.assertRaises(G7_02Error):
                    tool._write_manifest_exclusive(out, {"a": 1})
            finally:
                shutil.rmtree(tmp, ignore_errors=True)
        finally:
            tool.ROOT = original_root

    def test_cli_company_input_nan_rejected(self):
        """strict JSON：company input 含 NaN 字面量 → 失败关闭。"""
        tool = self.tool
        original = tool.source_revision
        tool.source_revision = lambda: (_REV_A, _TREE_A)
        try:
            with tempfile.TemporaryDirectory() as tmp:
                store = os.path.join(tmp, "objects")
                ArtifactStore(store).store("g7_02_macro_raw", SYNTHETIC_RAW)
                company_path = os.path.join(tmp, "company.json")
                manifest_path = os.path.join(tmp, "manifest.json")
                with open(company_path, "w", encoding="utf-8") as fh:
                    fh.write('{"ticker": "600089", "value": NaN}')
                with open(manifest_path, "w", encoding="utf-8") as fh:
                    json.dump(_manifest(), fh)
                args = argparse.Namespace(
                    company_input=company_path, macro_manifest=manifest_path,
                    store=store, cutoff_at=CUTOFF, as_of_date=AS_OF,
                    contract_id=CONTRACT_ID, run_id=RUN_ID)
                with self.assertRaises(G7_02Error) as cm:
                    tool.cmd_freeze(args)
                self.assertIn("E-G7-02-031", str(cm.exception))
        finally:
            tool.source_revision = original

    def test_canonical_rejects_nan(self):
        with self.assertRaises(ValueError):
            _canonical({"x": float("nan")})

    def test_nbs_smoke_rights_denied_zero_network(self):
        tool = self.tool
        guard = RightsGuard(matrix=_nbs_deny_matrix())

        class _GuardedFakeAdapter:
            def __init__(self, guard, base_url=None):
                self.guard = guard

            def fetch(self, scope, record_decision=None,
                      reference_period=""):
                rd = self.guard.decide(NBS_SOURCE_ID, "FETCH", scope)
                if rd.verdict != "ALLOWED":
                    raise GuardDenied(
                        f"{rd.verdict}: {NBS_SOURCE_ID} FETCH {scope} —— "
                        "零请求/正文/缓存/解析/外发")
                raise AssertionError(
                    "权利拒绝后仍执行了出网动作 —— 零网络被破坏")

        adapter = _GuardedFakeAdapter(guard)
        with self.assertRaises(GuardDenied):
            tool.nbs_smoke("http://127.0.0.1:9", _SMOKE_SCOPE,
                           cutoff_at=CUTOFF, guard=guard, adapter=adapter)

    def test_nbs_smoke_empty_body_failed_closed(self):
        tool = self.tool
        srv = _Responder(body=b"", status=200)()
        try:
            adapter = _loopback_adapter(
                f"http://127.0.0.1:{srv.server_address[1]}")
            with self.assertRaises(G7_02Error) as cm:
                tool.nbs_smoke(f"http://127.0.0.1:{srv.server_address[1]}",
                               _SMOKE_SCOPE, cutoff_at=CUTOFF, adapter=adapter)
            self.assertIn("空正文", str(cm.exception))
        finally:
            srv.shutdown()
            srv.server_close()

    def test_nbs_smoke_403_failed_closed(self):
        tool = self.tool
        srv = _Responder(body=b"forbidden", status=403)()
        try:
            adapter = _loopback_adapter(
                f"http://127.0.0.1:{srv.server_address[1]}")
            with self.assertRaises(RuntimeError) as cm:
                tool.nbs_smoke(f"http://127.0.0.1:{srv.server_address[1]}",
                               _SMOKE_SCOPE, cutoff_at=CUTOFF, adapter=adapter)
            self.assertIn("403", str(cm.exception))
        finally:
            srv.shutdown()
            srv.server_close()

    def test_nbs_smoke_429_failed_closed(self):
        tool = self.tool
        srv = _Responder(body=b"rate limited", status=429)()
        try:
            adapter = _loopback_adapter(
                f"http://127.0.0.1:{srv.server_address[1]}")
            with self.assertRaises(RuntimeError) as cm:
                tool.nbs_smoke(f"http://127.0.0.1:{srv.server_address[1]}",
                               _SMOKE_SCOPE, cutoff_at=CUTOFF, adapter=adapter)
            self.assertIn("429", str(cm.exception))
        finally:
            srv.shutdown()
            srv.server_close()

    def test_nbs_smoke_ok_records_hashes_and_binds_path_date(self):
        tool = self.tool
        body = b"<html>SYNTHETIC page</html>"
        srv = _Responder(body=body, status=200)()
        try:
            adapter = _loopback_adapter(
                f"http://127.0.0.1:{srv.server_address[1]}")
            out = tool.nbs_smoke(f"http://127.0.0.1:{srv.server_address[1]}",
                                 _SMOKE_SCOPE, cutoff_at=CUTOFF,
                                 adapter=adapter)
            self.assertEqual(out["verdict"], "OK")
            self.assertEqual(out["source_id"], NBS_SOURCE_ID)
            self.assertEqual(out["source_family"], NBS_SOURCE_FAMILY)
            self.assertEqual(out["raw_sha256"],
                             hashlib.sha256(body).hexdigest())
            self.assertEqual(out["raw_bytes"], len(body))
            self.assertEqual(out["publication_date"], "2026-07-16")
            self.assertEqual(out["rights_decision"]["verdict"], "ALLOWED")
        finally:
            srv.shutdown()
            srv.server_close()

    def test_nbs_smoke_publication_after_cutoff_failed_closed(self):
        tool = self.tool
        body = b"<html>SYNTHETIC page</html>"
        srv = _Responder(body=body, status=200)()
        try:
            adapter = _loopback_adapter(
                f"http://127.0.0.1:{srv.server_address[1]}")
            # 路径日期晚于 cutoff（t20260817 > 2026-08-16T09:21Z）。
            late_scope = "/sj/zxfbhjd/202608/t20260817_1.html"
            with self.assertRaises(G7_02Error) as cm:
                tool.nbs_smoke(
                    f"http://127.0.0.1:{srv.server_address[1]}",
                    late_scope, cutoff_at=CUTOFF, adapter=adapter)
            self.assertIn("E-G7-02-032", str(cm.exception))
        finally:
            srv.shutdown()
            srv.server_close()

    def test_nbs_smoke_missing_cutoff_failed_closed(self):
        tool = self.tool
        srv = _Responder(body=b"<html>x</html>", status=200)()
        try:
            adapter = _loopback_adapter(
                f"http://127.0.0.1:{srv.server_address[1]}")
            with self.assertRaises(G7_02Error):
                tool.nbs_smoke(f"http://127.0.0.1:{srv.server_address[1]}",
                               _SMOKE_SCOPE, adapter=adapter)
        finally:
            srv.shutdown()
            srv.server_close()

    def test_production_adapter_rejects_non_official_target(self):
        """生产 CLI 不注入 adapter 时只允许官方域名（无测试绕口）。"""
        tool = self.tool
        guard = RightsGuard()
        with self.assertRaises(ValueError):
            tool.nbs_smoke("http://evil.example", _SMOKE_SCOPE,
                           cutoff_at=CUTOFF, guard=guard)

    def test_nbs_acquire_requires_clean_checkout(self):
        """nbs-acquire 先要求 clean checkout（脏树即拒绝，零网络）。"""
        tool = self.tool
        original_root = tool.ROOT
        try:
            tmp, repo = self._temp_clean_repo()
            tool.ROOT = repo
            try:
                with open(os.path.join(repo, "tracked.txt"), "a",
                          encoding="utf-8") as fh:
                    fh.write("dirty\n")
                args = argparse.Namespace(
                    store=os.path.join(tmp, "objects"),
                    base_url="https://www.stats.gov.cn", scope=_SMOKE_SCOPE,
                    reference_period="", cutoff_at=CUTOFF, out="")
                with self.assertRaises(G7_02Error) as cm:
                    tool.cmd_nbs_acquire(args)
                self.assertIn("E-G7-02-030", str(cm.exception))
            finally:
                shutil.rmtree(tmp, ignore_errors=True)
        finally:
            tool.ROOT = original_root

    def test_nbs_acquire_loopback_writes_manifest_with_revision(self):
        """acquire→manifest 闭环：source_url/commit/tree/cutoff 入 manifest。"""
        tool = self.tool
        guard = RightsGuard()
        body = b"<html>SYNTHETIC page</html>"
        srv = _Responder(body=body, status=200)()
        try:
            adapter = _loopback_adapter(
                f"http://127.0.0.1:{srv.server_address[1]}", guard=guard)
            with tempfile.TemporaryDirectory() as tmp:
                store = ArtifactStore(os.path.join(tmp, "objects"))
                out = os.path.join(tmp, "manifest.json")
                result = tool.nbs_acquire(
                    store,
                    f"http://127.0.0.1:{srv.server_address[1]}",
                    _SMOKE_SCOPE, cutoff_at=CUTOFF,
                    source_commit=_REV_A, source_tree=_TREE_A,
                    out_path=out, guard=guard, adapter=adapter)
                self.assertEqual(result["verdict"], "ACQUIRED")
                self.assertEqual(result["source_commit"], _REV_A)
                self.assertEqual(result["source_tree"], _TREE_A)
                with open(out, encoding="utf-8") as fh:
                    manifest = json.load(fh)
                self.assertEqual(manifest["source_url"],
                                 f"http://127.0.0.1:{srv.server_address[1]}"
                                 + _SMOKE_SCOPE)
                self.assertEqual(manifest["source_commit"], _REV_A)
                self.assertEqual(manifest["source_tree"], _TREE_A)
                self.assertEqual(manifest["cutoff_at"], CUTOFF)
                self.assertEqual(manifest["publication_date"], "2026-07-16")
                # manifest 可被 validate_macro_manifest 重新消费（闭环）——
                # 但须与 freeze 代码版本一致，故此处只验形状校验本身。
                self.assertEqual(manifest["raw_sha256"],
                                 hashlib.sha256(body).hexdigest())
        finally:
            srv.shutdown()
            srv.server_close()

    def test_loopback_manifest_rejected_at_freeze(self):
        """闭环边界：acquire（回环）产出的非官方 source_url manifest 在 freeze
        必须失败关闭 —— 生产 freeze 只接受官方域名 manifest（纵深防御）。"""
        tool = self.tool
        original_rev = tool.source_revision
        tool.source_revision = lambda: (_REV_A, _TREE_A)
        guard = RightsGuard()
        body = b"<html>SYNTHETIC page</html>"
        srv = _Responder(body=body, status=200)()
        try:
            adapter = _loopback_adapter(
                f"http://127.0.0.1:{srv.server_address[1]}", guard=guard)
            with tempfile.TemporaryDirectory() as tmp:
                store = os.path.join(tmp, "objects")
                out = os.path.join(tmp, "manifest.json")
                tool.nbs_acquire(
                    ArtifactStore(store),
                    f"http://127.0.0.1:{srv.server_address[1]}",
                    _SMOKE_SCOPE, cutoff_at=CUTOFF, source_commit=_REV_A,
                    source_tree=_TREE_A, out_path=out, guard=guard,
                    adapter=adapter)
                company_path = os.path.join(tmp, "company.json")
                with open(company_path, "w", encoding="utf-8") as fh:
                    json.dump(_company_full(), fh)
                fargs = argparse.Namespace(
                    company_input=company_path, macro_manifest=out,
                    store=store, cutoff_at=CUTOFF, as_of_date=AS_OF,
                    contract_id=CONTRACT_ID, run_id=RUN_ID)
                with self.assertRaises(G7_02Error) as cm:
                    tool.cmd_freeze(fargs)
                self.assertIn("E-G7-02-010", str(cm.exception))
        finally:
            srv.shutdown()
            srv.server_close()
            tool.source_revision = original_rev


if __name__ == "__main__":
    unittest.main()
