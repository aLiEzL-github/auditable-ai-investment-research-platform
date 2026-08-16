"""G7-01 真实后端 E2E 运行时测试（三例合成 golden fixture）。

覆盖：
  · fixture 校验（合成标记 / 结构 / expected 冻结基准 / 证据哈希 /
    claim refs 可解析 / ID 唯一 / 预测绑定合法 / 合成 locator / NaN）
  · 确定性 candidate 身份与闭包（canonical 字节派生，绝不写发布对象）
  · 端点门控（生产模式不暴露 / 默认拒绝入参 / launch-before-read /
    mutation 请求体精确 schema）
  · 三例 golden（POSITIVE 全 PASS / RESTATEMENT R10 受阻 / WRONG_BASIS
    R01/R06/R08 FAIL，其中 R08 仅因 period_basis 错配 —— 算术自洽）
  · wrong_basis 反事实：算术自洽不能消除 SINGLE-vs-ANNUAL 错配；
    契约 period/unit/多余字段漂移一律失败关闭
  · 资格重算：预测错绑定（未知 claim / 非法状态）后读取端失败关闭，
    而 /api/release/eligibility 如实 BLOCKED（E-G7-01-006 并入阻断）
  · 审计计数全部派生（非硬编码）；材料性 OPEN 开放项阻断并给出可见原因
  · 证据台账形状与 frontend/src/types.ts 对齐
  · 二返工：contract/expected 精确键集 + period_basis=ANNUAL（VD-21）；
    claims/predictions/evidence/facts/open_items 枚举/形状/布尔与数值区分
    全部 load 期失败关闭；post-load 未分类材料性变异同步阻断（审计门与
    release_eligible 共用 E-G7-01-010 谓词）
"""
import copy
import dataclasses
import json
import os
import socket
import sys
import tempfile
import threading
import unittest
import unittest.mock
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "app"))

import g7_e2e  # noqa: E402
import main  # noqa: E402
from main import G7E2EHandler, HealthHandler  # noqa: E402

FIXTURES = g7_e2e.DEFAULT_FIXTURES_DIR

CLAIM_REQUIRED = ("schema_version", "id", "statement", "refs", "status",
                  "category", "materiality")
EVIDENCE_REQUIRED = ("id", "artifact_id", "snapshot_id", "schema_ver",
                     "parser_version", "sha256", "content")
FACT_REQUIRED = ("id", "artifact_id", "metric", "value", "unit", "period",
                 "scope", "basis", "vintage", "locator", "parser_version")
PREDICTION_REQUIRED = ("id", "claim_id", "horizon", "probability",
                       "calibration_pending", "registered_at", "status")


def _load_case(selector):
    return g7_e2e.load_golden_case(f"G7-01-{selector}")


def _with_r08(case, single_quarter=None, values=None):
    spec = dict(case.rules["R08"])
    if single_quarter is not None:
        spec["single_quarter_or_cumulative"] = single_quarter
    if values is not None:
        spec["values"] = dict(values)
    rules = dict(case.rules)
    rules["R08"] = spec
    return dataclasses.replace(case, rules=rules)


def _raw_http(port, method, path, body=b"", content_length=None):
    """发送**未经 urllib 规范化**的原始 HTTP 请求（可携带畸形 Content-Length），
    返回 (status, body_text)。timeout=5 防挂起；Connection: close 防服务器
    等待后续请求。"""
    if content_length is None:
        content_length = str(len(body))
    req = (f"{method} {path} HTTP/1.1\r\nHost: 127.0.0.1\r\n"
           f"Content-Length: {content_length}\r\n"
           f"Connection: close\r\n\r\n").encode("ascii") + body
    with socket.create_connection(("127.0.0.1", port), timeout=5) as s:
        s.sendall(req)
        chunks = []
        while True:
            part = s.recv(65536)
            if not part:
                break
            chunks.append(part)
    resp = b"".join(chunks)
    head, _, resp_body = resp.partition(b"\r\n\r\n")
    status_line = head.split(b"\r\n", 1)[0]
    status = int(status_line.split(b" ", 2)[1])
    return status, resp_body.decode("utf-8", errors="replace")


class _Silent:
    def log_message(self, *a):
        pass


class TestFixtureValidation(unittest.TestCase):
    def test_all_golden_cases_load_and_validate(self):
        for selector in g7_e2e.G7_CASES:
            case = _load_case(selector)
            self.assertEqual(case.g7_case, selector)
            self.assertEqual(len(case.rules), 10)
            self.assertEqual(sorted(case.rules), list(g7_e2e.RULE_IDS))
            self.assertEqual(set(case.expected), set(g7_e2e.EXPECTED_FIELDS))

    def test_expected_frozen_basis_consistent_with_contract(self):
        for selector in g7_e2e.G7_CASES:
            case = _load_case(selector)
            self.assertEqual(case.expected["scope"], case.contract["scope"])
            self.assertEqual(case.expected["period"], case.contract["period"])
            self.assertEqual(case.expected["unit"], case.contract["unit"])
            self.assertEqual(case.expected["period_basis"], "ANNUAL")

    def test_unknown_case_selector_fails_closed(self):
        with self.assertRaises(g7_e2e.UnknownGoldenCase):
            g7_e2e.load_golden_case("G7-01-NOPE")
        with self.assertRaises(g7_e2e.UnknownGoldenCase):
            g7_e2e.resolve_case({"scope": "SYN-NO-SUCH"})

    def test_missing_golden_file_fails_closed(self):
        with self.assertRaises(g7_e2e.UnknownGoldenCase):
            g7_e2e.load_golden_case("G7-01-UNKNOWN")

    def test_fixture_without_synthetic_marker_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            bad = {"schema": g7_e2e.SCHEMA, "case_id": "G7-01-POSITIVE",
                   "g7_case": "POSITIVE", "contract": {}, "rules": {},
                   "predictions": [], "claims": [], "evidence": [],
                   "facts": [], "open_items": [], "checked_at": "x"}
            with open(os.path.join(tmp, "G7-01-POSITIVE.json"), "w") as f:
                json.dump(bad, f)
            with self.assertRaises(g7_e2e.GoldenCaseInvalid):
                g7_e2e.load_golden_case("G7-01-POSITIVE", fixtures_dir=tmp)

    def test_fixture_with_nan_constant_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "G7-01-POSITIVE.json")
            with open(path, "w") as f:
                f.write('{"schema": "g7-01-golden/1.0", "synthetic": true,'
                        ' "SYNTHETIC_FIXTURE": true, "case_id": "G7-01-POSITIVE",'
                        ' "g7_case": "POSITIVE", "rules": {"x": NaN}}')
            with self.assertRaises(g7_e2e.GoldenCaseInvalid):
                g7_e2e.load_golden_case("G7-01-POSITIVE", fixtures_dir=tmp)

    # ── fixture 完整性（G7-01 评审发现 3 + 二返工）────────────────────
    def _corrupt(self, mutate, selector="POSITIVE"):
        case = _load_case(selector)
        case_id = f"G7-01-{selector}"
        obj = json.loads(json.dumps({
            "schema": g7_e2e.SCHEMA, "synthetic": True,
            "SYNTHETIC_FIXTURE": True, "case_id": case_id,
            "g7_case": selector, "source": f"synthetic://g7-01/{selector}",
            "checked_at": case.checked_at, "contract": case.contract,
            "expected": case.expected, "rules": case.rules,
            "predictions": case.predictions, "claims": case.claims,
            "evidence": case.evidence, "facts": case.facts,
            "open_items": case.open_items}, ensure_ascii=False))
        return mutate(obj)

    def _reject(self, obj, what, selector="POSITIVE"):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, f"G7-01-{selector}.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump(obj, f, ensure_ascii=False)
            with self.assertRaises(g7_e2e.GoldenCaseInvalid) as ctx:
                g7_e2e.load_golden_case(f"G7-01-{selector}", fixtures_dir=tmp)
            self.assertIn(what, str(ctx.exception))

    def test_evidence_sha256_mismatch_rejected(self):
        obj = self._corrupt(lambda o: (
            o["evidence"][0].__setitem__("sha256", "0" * 64), o)[1])
        self._reject(obj, "sha256")

    def test_evidence_sha256_non_hex_rejected(self):
        obj = self._corrupt(lambda o: (
            o["evidence"][0].__setitem__("sha256", "not-a-hash"), o)[1])
        self._reject(obj, "64 位 hex")

    def test_claim_dangling_ref_rejected(self):
        obj = self._corrupt(lambda o: (
            o["claims"][0].__setitem__("refs", ["EV-NO-SUCH"]), o)[1])
        self._reject(obj, "悬空")

    def test_claim_empty_refs_rejected(self):
        obj = self._corrupt(lambda o: (
            o["claims"][0].__setitem__("refs", []), o)[1])
        self._reject(obj, "refs")

    def test_duplicate_ids_rejected(self):
        obj = self._corrupt(lambda o: (
            o["claims"].append(dict(o["claims"][0], id="EV-SYN-01")), o)[1])
        self._reject(obj, "重复 ID")

    def test_invalid_prediction_status_rejected(self):
        obj = self._corrupt(lambda o: (
            o["predictions"][0].__setitem__("status", "RESOLVED"), o)[1])
        self._reject(obj, "状态")

    def test_unknown_prediction_claim_rejected(self):
        obj = self._corrupt(lambda o: (
            o["predictions"][0].__setitem__("claim_id", "CLAIM-NO-SUCH"), o)[1])
        self._reject(obj, "未知 claim")

    def test_non_synthetic_rule_locator_rejected(self):
        obj = self._corrupt(lambda o: (
            o["rules"]["R01"].__setitem__(
                "locator", "https://example.com/real.pdf"), o)[1])
        self._reject(obj, "非合成 locator")

    def test_non_synthetic_source_rejected(self):
        obj = self._corrupt(lambda o: (
            o.__setitem__("source", "http://example.com/report.pdf"), o)[1])
        self._reject(obj, "source 非合成 locator")

    def test_missing_expected_rejected(self):
        obj = self._corrupt(lambda o: (o.pop("expected"), o)[1])
        self._reject(obj, "expected")

    def test_expected_mismatch_with_contract_rejected(self):
        obj = self._corrupt(lambda o: (
            o["expected"].__setitem__("scope", "SYN-OTHER"), o)[1])
        self._reject(obj, "expected")

    def test_evidence_missing_frontend_field_rejected(self):
        obj = self._corrupt(lambda o: (
            o["evidence"][0].pop("snapshot_id"), o)[1])
        self._reject(obj, "snapshot_id")

    def test_fact_missing_parser_version_rejected(self):
        obj = self._corrupt(lambda o: (
            o["facts"][0].pop("parser_version"), o)[1])
        self._reject(obj, "parser_version")

    # ── 二返工：精确键集 / 枚举 / 形状 / 布尔与数值区分（E-G7-01-002）─
    def test_contract_extra_key_rejected(self):
        obj = self._corrupt(lambda o: (
            o["contract"].__setitem__("extra", "surprise"), o)[1])
        self._reject(obj, "键集")

    def test_contract_missing_key_rejected(self):
        obj = self._corrupt(lambda o: (
            o["contract"].pop("workflow"), o)[1])
        self._reject(obj, "键集")

    def test_expected_extra_key_rejected(self):
        obj = self._corrupt(lambda o: (
            o["expected"].__setitem__("extra", "surprise"), o)[1])
        self._reject(obj, "键集")

    def test_expected_period_basis_must_be_annual_rejected(self):
        obj = self._corrupt(lambda o: (
            o["expected"].__setitem__("period_basis", "SINGLE"), o)[1])
        self._reject(obj, "ANNUAL")

    def test_claim_unknown_status_rejected(self):
        obj = self._corrupt(lambda o: (
            o["claims"][0].__setitem__("status", "HOLD"), o)[1])
        self._reject(obj, "claim")

    def test_claim_unknown_category_rejected(self):
        obj = self._corrupt(lambda o: (
            o["claims"][0].__setitem__("category", "Q"), o)[1])
        self._reject(obj, "category")

    def test_claim_unknown_materiality_rejected(self):
        obj = self._corrupt(lambda o: (
            o["claims"][0].__setitem__("materiality", "BOGUS"), o)[1])
        self._reject(obj, "materiality")

    def test_claim_duplicate_refs_rejected(self):
        obj = self._corrupt(lambda o: (
            o["claims"][0].__setitem__("refs", ["EV-SYN-01", "EV-SYN-01"]),
            o)[1])
        self._reject(obj, "重复项")

    def test_prediction_probability_gt_one_rejected(self):
        obj = self._corrupt(lambda o: (
            o["predictions"][0].__setitem__("probability", 1.5), o)[1])
        self._reject(obj, "[0,1]")

    def test_prediction_probability_negative_rejected(self):
        obj = self._corrupt(lambda o: (
            o["predictions"][0].__setitem__("probability", -0.1), o)[1])
        self._reject(obj, "[0,1]")

    def test_prediction_probability_string_rejected(self):
        obj = self._corrupt(lambda o: (
            o["predictions"][0].__setitem__("probability", "0.7"), o)[1])
        self._reject(obj, "数值")

    def test_prediction_probability_bool_rejected(self):
        # 布尔不得冒充数值 —— True 是 int 子类，必须显式排除
        obj = self._corrupt(lambda o: (
            o["predictions"][0].__setitem__("probability", True), o)[1])
        self._reject(obj, "布尔")

    def test_prediction_calibration_pending_not_bool_rejected(self):
        obj = self._corrupt(lambda o: (
            o["predictions"][0].__setitem__("calibration_pending", 1), o)[1])
        self._reject(obj, "calibration_pending")

    def test_open_item_unknown_status_rejected(self):
        obj = self._corrupt(lambda o: (
            o["open_items"][0].__setitem__("status", "HOLD"), o)[1],
            selector="RESTATEMENT")
        self._reject(obj, "OPEN", selector="RESTATEMENT")

    def test_open_item_material_not_bool_rejected(self):
        obj = self._corrupt(lambda o: (
            o["open_items"][0].__setitem__("material", "true"), o)[1],
            selector="RESTATEMENT")
        self._reject(obj, "布尔", selector="RESTATEMENT")

    def test_open_item_duplicate_blocks_rejected(self):
        obj = self._corrupt(lambda o: (
            o["open_items"][0].__setitem__("blocks", ["B1", "B1"]), o)[1],
            selector="RESTATEMENT")
        self._reject(obj, "重复项", selector="RESTATEMENT")

    def test_open_item_empty_title_rejected(self):
        obj = self._corrupt(lambda o: (
            o["open_items"][0].__setitem__("title", ""), o)[1],
            selector="RESTATEMENT")
        self._reject(obj, "title", selector="RESTATEMENT")

    # ── 三返工：rule values 键契约 / Decimal / 文本枚举 / 容差（E-G7-01-002）
    def test_rule_value_non_decimal_string_rejected(self):
        # 曾放行的形状：R02 net_profit="abc" 通过 load，拖到 rules_engine
        # 才裸抛 RuleEngineError —— 必须在 load 期 E-G7-01-002 判红
        obj = self._corrupt(lambda o: (
            o["rules"]["R02"]["values"].__setitem__("net_profit", "abc"), o)[1])
        self._reject(obj, "Decimal")

    def test_rule_value_non_string_rejected(self):
        obj = self._corrupt(lambda o: (
            o["rules"]["R02"]["values"].__setitem__("net_profit", 100), o)[1])
        self._reject(obj, "非字符串")

    def test_rule_value_extra_key_rejected(self):
        obj = self._corrupt(lambda o: (
            o["rules"]["R01"]["values"].__setitem__("surprise", "1"), o)[1])
        self._reject(obj, "键集")

    def test_rule_value_missing_key_rejected(self):
        obj = self._corrupt(lambda o: (
            o["rules"]["R01"]["values"].pop("eliminations"), o)[1])
        self._reject(obj, "键集")

    def test_rule_text_enum_basis_invalid_rejected(self):
        obj = self._corrupt(lambda o: (
            o["rules"]["R08"]["values"].__setitem__(
                "segment_measurement_basis", "BOGUS"), o)[1])
        self._reject(obj, "不在")

    def test_rule_text_enum_restatement_invalid_rejected(self):
        obj = self._corrupt(lambda o: (
            o["rules"]["R10"]["values"].__setitem__(
                "restatement_pending", "DONE"), o)[1])
        self._reject(obj, "不在")

    def test_rule_tolerance_negative_rejected(self):
        obj = self._corrupt(lambda o: (
            o["rules"]["R01"].__setitem__("absolute_tolerance", "-1"), o)[1])
        self._reject(obj, "非负")

    def test_rule_tolerance_invalid_rejected(self):
        obj = self._corrupt(lambda o: (
            o["rules"]["R01"].__setitem__("relative_tolerance", "abc"), o)[1])
        self._reject(obj, "Decimal")

    def test_rule_enum_instant_or_duration_invalid_rejected(self):
        obj = self._corrupt(lambda o: (
            o["rules"]["R01"].__setitem__("instant_or_duration", "MOMENT"),
            o)[1])
        self._reject(obj, "不在")

    def test_rule_failure_impact_invalid_rejected(self):
        obj = self._corrupt(lambda o: (
            o["rules"]["R01"].__setitem__("failure_impact", "MAYBE"), o)[1])
        self._reject(obj, "不在")

    def test_rule_applicability_predicate_invalid_rejected(self):
        obj = self._corrupt(lambda o: (
            o["rules"]["R01"].__setitem__("applicability_predicate", "ALWAYS"),
            o)[1])
        self._reject(obj, "APPLICABLE")

    def test_claim_refs_unhashable_rejected(self):
        # 不可哈希元素不得让 set(refs) 裸抛 TypeError —— 须先归一为
        # GoldenCaseInvalid（失败关闭），否则 load 期崩溃非受控错误。
        obj = self._corrupt(lambda o: (
            o["claims"][0].__setitem__("refs", [{"x": 1}]), o)[1])
        self._reject(obj, "非字符串")

    def test_open_item_blocks_unhashable_rejected(self):
        obj = self._corrupt(lambda o: (
            o["open_items"][0].__setitem__("blocks", [["nested"]]), o)[1],
            selector="RESTATEMENT")
        self._reject(obj, "非字符串", selector="RESTATEMENT")


class TestDeterministicCandidateIdentity(unittest.TestCase):
    def test_same_case_same_candidate_id(self):
        case = _load_case("POSITIVE")
        a = g7_e2e.build_candidate(case, case.contract)
        b = g7_e2e.build_candidate(case, case.contract)
        self.assertEqual(a["candidate_id"], b["candidate_id"])

    def test_different_cases_different_candidate_ids(self):
        ids = set()
        for selector in g7_e2e.G7_CASES:
            case = _load_case(selector)
            ids.add(g7_e2e.build_candidate(case, case.contract)["candidate_id"])
        self.assertEqual(len(ids), len(g7_e2e.G7_CASES))

    def test_candidate_identity_binds_contract_bytes(self):
        case = _load_case("POSITIVE")
        base = g7_e2e.build_candidate(case, case.contract)["candidate_id"]
        shifted = dict(case.contract, as_of="2026-08-12")
        other = g7_e2e.build_candidate(case, shifted)["candidate_id"]
        self.assertNotEqual(base, other)

    def test_closure_derived_from_canonical_bytes(self):
        case = _load_case("POSITIVE")
        c = g7_e2e.build_candidate(case, case.contract)
        self.assertEqual(c["closure"]["subject_root"], c["candidate_id"])
        self.assertEqual(c["closure"]["count"], 5)
        self.assertTrue(c["closure"]["complete"])
        self.assertEqual(c["closure"]["dangling"], 0)
        self.assertTrue(all(o["id"] == o["sha256"]
                            for o in c["closure"]["objects"]))

    def test_candidate_never_writes_forbidden_objects(self):
        case = _load_case("POSITIVE")
        c = g7_e2e.build_candidate(case, case.contract)
        blob = json.dumps(c["core"], sort_keys=True, ensure_ascii=False)
        for field in g7_e2e.FORBIDDEN_TOP_LEVEL_FIELDS:
            self.assertNotIn(field, blob, f"candidate 不得含 {field}")
        self.assertEqual(g7_e2e.approvals_view(c)["rows"], [])
        self.assertEqual(g7_e2e.releases_view(c)["keys"][0]["current"], None)

    def test_mutation_rebuilds_identity_from_canonical_bytes(self):
        case = _load_case("POSITIVE")
        base = g7_e2e.build_candidate(case, case.contract)
        misbound = g7_e2e.apply_mutation(
            g7_e2e.build_candidate(case, case.contract), "misbind_prediction")
        # 错绑定改变 core 字节 → 身份随之改变（身份恒来自 canonical 字节）
        self.assertNotEqual(base["candidate_id"], misbound["candidate_id"])
        self.assertEqual(
            misbound["candidate_id"],
            g7_e2e._sha256(g7_e2e.canonical_bytes(misbound["core"])))
        dropped = g7_e2e.apply_mutation(
            g7_e2e.build_candidate(case, case.contract), "drop_closure_object")
        # 闭包缺对象不改变 core 字节 → 身份不变，但闭包不完整
        self.assertEqual(base["candidate_id"], dropped["candidate_id"])
        self.assertFalse(dropped["closure"]["complete"])


class TestLaunchBeforeRead(unittest.TestCase):
    def test_read_before_launch_fails_closed(self):
        rt = g7_e2e.G7E2ERuntime()
        with self.assertRaises(g7_e2e.NotLaunched):
            rt.require_launched()

    def test_launch_then_read_ok(self):
        rt = g7_e2e.G7E2ERuntime()
        case = _load_case("POSITIVE")
        res = rt.launch(case.contract)
        self.assertTrue(res["ok"])
        self.assertTrue(res["run_id"].startswith("run-g7-01-"))
        self.assertEqual(res["state"], "CANDIDATE")
        self.assertEqual(res["source"], "backend")
        self.assertIsNotNone(rt.require_launched())

    def test_reset_clears_launch(self):
        rt = g7_e2e.G7E2ERuntime()
        case = _load_case("POSITIVE")
        rt.launch(case.contract)
        rt.reset()
        with self.assertRaises(g7_e2e.NotLaunched):
            rt.require_launched()

    def test_same_contract_same_run_id(self):
        rt = g7_e2e.G7E2ERuntime()
        case = _load_case("POSITIVE")
        a = rt.launch(case.contract)
        rt.reset()
        b = rt.launch(case.contract)
        self.assertEqual(a["run_id"], b["run_id"])
        self.assertEqual(a["candidate_id"], b["candidate_id"])


class _Server:
    """起一个线程化 HTTP 服务器，处理类可注入。"""

    def __init__(self, handler_cls):
        cls = type("Server", (handler_cls, _Silent), {})
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), cls)
        self.port = self.srv.server_address[1]
        self.t = threading.Thread(target=self.srv.serve_forever, daemon=True)
        self.t.start()

    def close(self):
        self.srv.shutdown()
        self.srv.server_close()

    def url(self, path):
        return f"http://127.0.0.1:{self.port}{path}"

    def req(self, path, method="GET", data=None, raw=None):
        body = raw if raw is not None else (
            json.dumps(data).encode() if data is not None else None)
        req = urllib.request.Request(self.url(path), data=body, method=method)
        if body is not None:
            req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            try:
                return e.code, json.loads(e.read())
            except Exception:
                return e.code, {}


class TestEndpointGating(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.prod = _Server(HealthHandler)
        G7E2EHandler.runtime = g7_e2e.G7E2ERuntime()
        cls.g7 = _Server(G7E2EHandler)

    @classmethod
    def tearDownClass(cls):
        cls.prod.close()
        cls.g7.close()

    def test_production_mode_does_not_expose_g7_endpoints(self):
        for path in ("/api/audit", "/api/predictions", "/api/closure",
                     "/api/releases", "/api/rules", "/api/evidence",
                     "/api/approvals"):
            code, _ = self.prod.req(path)
            self.assertEqual(code, 404, f"生产模式不得暴露 {path}")
        code, _ = self.prod.req("/api/g7/mutations", method="POST",
                                data={"selector": "x"})
        self.assertEqual(code, 404, "生产模式不得暴露 mutation 钩子")

    def test_g7_read_before_launch_409(self):
        code, body = self.g7.req("/api/audit")
        self.assertEqual(code, 409, body)
        self.assertEqual(body["error"], "E-G7-01-003")

    def test_g7_read_endpoint_default_deny_input(self):
        code, body = self.g7.req("/api/audit?foo=1")
        self.assertEqual(code, 400, body)
        self.assertEqual(body["error"], "E-G7-01-004")

    def test_g7_eligibility_default_deny_verdict_keys(self):
        code, body = self.g7.req("/api/release/eligibility?release_eligible=true")
        self.assertEqual(code, 400, body)
        self.assertEqual(body["error"], "E-G7-01-004")
        self.assertIn("release_eligible", body["verdict_keys"])

    def test_g7_eligibility_write_rejected(self):
        code, body = self.g7.req("/api/release/eligibility", method="POST",
                                 data={"release_eligible": True})
        self.assertIn(code, (400, 405), body)

    def test_launch_unknown_case_fails_closed(self):
        payload = {"scope": "SYN-NO-SUCH", "period": "2026", "unit": "CNY_million",
                   "vintage": "2026-08", "snapshot": "SNAP-001",
                   "security_code": "SYN-NO-SUCH.SH", "company_id": "SYN-NO-SUCH",
                   "as_of": "2026-08-11", "version": "v0.1.0",
                   "workflow": "a-share-single-company-research"}
        code, body = self.g7.req("/api/research/launch", method="POST", data=payload)
        self.assertEqual(code, 400, body)
        self.assertIn("E-G7-01-002", body["detail"])

    def test_launch_missing_fields_fails_closed(self):
        code, body = self.g7.req("/api/research/launch", method="POST",
                                 data={"scope": "SYN-700001"})
        self.assertEqual(code, 400, body)
        self.assertIn("E-G7-01-008", body["detail"])

    def test_launch_non_json_body_fails_closed(self):
        code, body = self.g7.req("/api/research/launch", method="POST",
                                 raw=b"not-json")
        self.assertEqual(code, 400, body)
        self.assertIn("E-G7-01-009", body["detail"])

    def test_mutation_unknown_selector_rejected(self):
        case = _load_case("POSITIVE")
        self.g7.req("/api/research/launch", method="POST", data=case.contract)
        code, body = self.g7.req("/api/g7/mutations", method="POST",
                                 data={"selector": "totally-unknown"})
        self.assertEqual(code, 400, body)
        self.assertEqual(body["error"], "E-G7-01-005")

    # ── mutation 请求体精确 schema（G7-01 评审发现 8）────────────────
    def test_mutation_extra_field_default_denied(self):
        case = _load_case("POSITIVE")
        self.g7.req("/api/research/launch", method="POST", data=case.contract)
        code, body = self.g7.req("/api/g7/mutations", method="POST",
                                 data={"selector": "drop_closure_object",
                                       "extra": 1})
        self.assertEqual(code, 400, body)
        self.assertEqual(body["error"], "E-G7-01-005")
        self.assertIn("extra", body["detail"])

    def test_mutation_empty_body_denied(self):
        case = _load_case("POSITIVE")
        self.g7.req("/api/research/launch", method="POST", data=case.contract)
        code, body = self.g7.req("/api/g7/mutations", method="POST",
                                 data={})
        self.assertEqual(code, 400, body)
        self.assertEqual(body["error"], "E-G7-01-005")


class TestGoldenCases(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        G7E2EHandler.runtime = g7_e2e.G7E2ERuntime()
        cls.srv = _Server(G7E2EHandler)

    @classmethod
    def tearDownClass(cls):
        cls.srv.close()

    def _launch(self, selector):
        case = _load_case(selector)
        code, body = self.srv.req("/api/research/launch", method="POST",
                                  data=case.contract)
        self.assertEqual(code, 200, body)
        return case

    def test_positive_all_rules_pass_and_eligible(self):
        self._launch("POSITIVE")
        code, audit = self.srv.req("/api/audit")
        self.assertEqual(code, 200)
        self.assertTrue(audit["audit"]["release_eligible"])
        self.assertEqual(audit["audit"]["failures"], [])
        rules_verdict = [g for g in audit["audit"]["gates"]
                         if g["gate"] == "rules"][0]["verdict"]
        self.assertEqual(rules_verdict, "PASS")
        code, rules = self.srv.req("/api/rules")
        self.assertEqual(code, 200)
        self.assertEqual(len(rules["rows"]), 10)
        self.assertTrue(all(r["status"] == "PASS" for r in rules["rows"]))

    def test_positive_four_prediction_states(self):
        self._launch("POSITIVE")
        code, preds = self.srv.req("/api/predictions")
        self.assertEqual(code, 200)
        states = {p["status"] for p in preds["rows"]}
        self.assertEqual(states, set(g7_e2e.PREDICTION_STATES))

    def test_restatement_r10_pending_blocked(self):
        self._launch("RESTATEMENT")
        code, audit = self.srv.req("/api/audit")
        self.assertEqual(code, 200)
        self.assertFalse(audit["audit"]["release_eligible"])
        self.assertTrue(any("R10" in f and "RESTATEMENT_PENDING" in f
                            for f in audit["audit"]["failures"]))
        code, rules = self.srv.req("/api/rules")
        r10 = [r for r in rules["rows"] if r["rule_id"] == "R10"][0]
        self.assertEqual(r10["status"], "RESTATEMENT_PENDING")
        code, elig = self.srv.req("/api/release/eligibility")
        self.assertEqual(code, 200)
        self.assertEqual(elig["status"], "BLOCKED")

    def test_wrong_basis_rules_fail_blocked(self):
        self._launch("WRONG_BASIS")
        code, audit = self.srv.req("/api/audit")
        self.assertEqual(code, 200)
        self.assertFalse(audit["audit"]["release_eligible"])
        code, rules = self.srv.req("/api/rules")
        failed = {r["rule_id"] for r in rules["rows"] if r["status"] == "FAIL"}
        self.assertEqual(failed, {"R01", "R06", "R08"})
        by_id = {r["rule_id"]: r for r in rules["rows"]}
        # 每条 wrong_basis 失败须给出**具体**错配维度，而非只报失败集合：
        # R01 = scope 错配（spec scope ≠ 契约 scope）
        r01 = by_id["R01"]
        self.assertIn("scope", r01["result"])
        self.assertIn("SYN-OTHER-SCOPE", r01["result"])
        self.assertIn("SYN-700003", r01["result"])
        # R06 = unit 错配（spec unit 为空 ≠ 冻结 unit）
        r06 = by_id["R06"]
        self.assertIn("unit", r06["result"])
        self.assertIn("CNY_million", r06["result"])
        r08 = by_id["R08"]
        self.assertEqual(r08["applicability"]["signature"],
                         g7_e2e.SYNTHETIC_MARKER)
        # R08 必须因 period_basis 错配失败，而非算术失败
        self.assertIn("period_basis", r08["result"])

    def test_publication_disabled(self):
        self._launch("POSITIVE")
        code, audit = self.srv.req("/api/audit")
        self.assertFalse(audit["gate7_reached"])
        self.assertEqual(audit["approvals"]["rows"], [])
        current = audit["releases"]["keys"][0]["current"]
        self.assertIsNone(current)
        code, elig = self.srv.req("/api/release/eligibility")
        self.assertEqual(code, 200)

    def test_backend_source_labeling(self):
        self._launch("RESTATEMENT")
        code, audit = self.srv.req("/api/audit")
        self.assertEqual(audit["audit"]["source"], "BACKEND")
        code, elig = self.srv.req("/api/release/eligibility")
        self.assertEqual(elig["source"], "BACKEND")

    def test_launch_response_backend_source_marker(self):
        case = _load_case("POSITIVE")
        code, body = self.srv.req("/api/research/launch", method="POST",
                                  data=case.contract)
        self.assertEqual(code, 200, body)
        self.assertEqual(body["source"], "backend")
        self.assertIn("run_id", body)
        self.assertIn("candidate_id", body)

    # ── wrong_basis 反事实（G7-01 评审发现 1）───────────────────────
    def test_r08_coherent_arithmetic_cannot_erase_single_annual_mismatch(self):
        case = _load_case("WRONG_BASIS")
        coherent = {"merged_profit": "200", "segment_profit_sum": "200",
                    "segment_eliminations": "0",
                    "segment_measurement_basis": "COMPARABLE"}
        # 算术完全自洽（residual=0），但 SINGLE-vs-ANNUAL 仍 FAIL
        c = g7_e2e.build_candidate(
            _with_r08(case, single_quarter="SINGLE", values=coherent),
            case.contract)
        self.assertEqual(c["core"]["rule_results"]["R08"]["status"],
                         g7_e2e.FAIL)
        self.assertIn("period_basis", c["core"]["rule_results"]["R08"]["detail"])
        self.assertFalse(c["release_eligible"])

    def test_r08_basis_fixed_and_arithmetic_coherent_passes(self):
        case = _load_case("WRONG_BASIS")
        coherent = {"merged_profit": "200", "segment_profit_sum": "200",
                    "segment_eliminations": "0",
                    "segment_measurement_basis": "COMPARABLE"}
        c = g7_e2e.build_candidate(
            _with_r08(case, single_quarter="ANNUAL", values=coherent),
            case.contract)
        self.assertEqual(c["core"]["rule_results"]["R08"]["status"],
                         g7_e2e.PASS)

    def test_r08_basis_fixed_but_arithmetic_inconsistent_still_fails(self):
        case = _load_case("WRONG_BASIS")
        incoherent = {"merged_profit": "200", "segment_profit_sum": "220",
                      "segment_eliminations": "0",
                      "segment_measurement_basis": "COMPARABLE"}
        c = g7_e2e.build_candidate(
            _with_r08(case, single_quarter="ANNUAL", values=incoherent),
            case.contract)
        # 基础正确时算术仍独立生效 —— 两个维度各自被强制
        self.assertEqual(c["core"]["rule_results"]["R08"]["status"],
                         g7_e2e.FAIL)
        self.assertIn("FAIL residual", c["core"]["rule_results"]["R08"]["detail"])

    # ── 契约漂移（G7-01 评审发现 1）─────────────────────────────────
    def test_launch_contract_period_drift_rejected(self):
        case = _load_case("POSITIVE")
        drift = dict(case.contract, period="2025")
        code, body = self.srv.req("/api/research/launch", method="POST",
                                  data=drift)
        self.assertEqual(code, 400, body)
        self.assertIn("E-G7-01-002", body["detail"])

    def test_launch_contract_unit_drift_rejected(self):
        case = _load_case("POSITIVE")
        drift = dict(case.contract, unit="CNY_ten_thousand")
        code, body = self.srv.req("/api/research/launch", method="POST",
                                  data=drift)
        self.assertEqual(code, 400, body)
        self.assertIn("E-G7-01-002", body["detail"])

    def test_launch_contract_extra_field_rejected(self):
        case = _load_case("POSITIVE")
        drift = dict(case.contract, extra="surprise")
        code, body = self.srv.req("/api/research/launch", method="POST",
                                  data=drift)
        self.assertEqual(code, 400, body)
        self.assertIn("E-G7-01-002", body["detail"])

    def test_launch_contract_same_scope_only_rejected(self):
        # 仅 scope 相同不算匹配 —— 契约须精确等于冻结 fixture
        case = _load_case("POSITIVE")
        drift = dict(case.contract, snapshot="SNAP-999")
        code, body = self.srv.req("/api/research/launch", method="POST",
                                  data=drift)
        self.assertEqual(code, 400, body)
        self.assertIn("E-G7-01-002", body["detail"])

    # ── 派生计数（G7-01 评审发现 4）─────────────────────────────────
    def test_audit_counts_derived_not_hardcoded(self):
        case = self._launch("POSITIVE")
        code, audit = self.srv.req("/api/audit")
        self.assertEqual(code, 200)
        by_gate = {g["gate"]: g for g in audit["audit"]["gates"]}
        self.assertEqual(by_gate["rules"]["checked"],
                         len(case.rules))
        self.assertEqual(by_gate["materiality"]["checked"],
                         len(case.claims))
        self.assertEqual(by_gate["open_items"]["checked"],
                         len(case.open_items))
        self.assertEqual(by_gate["closure"]["checked"], 5)
        # 记录完整性门检查的记录组与谓词一致：claims/evidence/facts/
        # predictions（open_items 允许为空，不计入）
        expected_records = (len(case.claims) + len(case.evidence)
                            + len(case.facts) + len(case.predictions))
        self.assertEqual(by_gate["completeness"]["checked"], expected_records)
        code, rules = self.srv.req("/api/rules")
        # 十条规则全部适用 → 分母派生为 10
        self.assertTrue(all(r["denominator"] == "10" for r in rules["rows"]))
        # 每条规则输入键派生自 values
        self.assertIn("merged_revenue", rules["rows"][0]["inputs"])

    # ── 证据台账形状（G7-01 评审发现 3）─────────────────────────────
    def test_evidence_view_shape_matches_frontend_types(self):
        self._launch("POSITIVE")
        code, ev = self.srv.req("/api/evidence")
        self.assertEqual(code, 200)
        for c in ev["claims"]:
            self.assertEqual(set(c), set(CLAIM_REQUIRED))
        for e in ev["evidence"]:
            self.assertEqual(set(e), set(EVIDENCE_REQUIRED))
        for f in ev["facts"]:
            self.assertEqual(set(f), set(FACT_REQUIRED))
        for o in ev["openItems"]:
            self.assertEqual(set(o),
                             {"id", "title", "status", "material", "blocks"})

    def test_evidence_claim_refs_resolve_to_served_evidence_ids(self):
        self._launch("POSITIVE")
        code, ev = self.srv.req("/api/evidence")
        ev_ids = {e["id"] for e in ev["evidence"]}
        for c in ev["claims"]:
            for ref in c["refs"]:
                self.assertIn(ref, ev_ids, f"claim {c['id']} ref {ref} 悬空")


class TestMaterialOpenItemBlocking(unittest.TestCase):
    def test_material_open_item_blocks_with_visible_reason(self):
        case = _load_case("RESTATEMENT")
        c = g7_e2e.build_candidate(case, case.contract)
        c["core"]["open_items"][0]["material"] = True
        c["core"]["open_items"][0]["status"] = "OPEN"
        g7_e2e._rebuild_identity(c)
        self.assertFalse(c["release_eligible"])
        open_failures = [f for f in c["failures"]
                         if f["code"].startswith("OPEN_ITEM:")]
        self.assertEqual(len(open_failures), 1)
        self.assertIn("OI-SYN-R1", open_failures[0]["code"])
        self.assertIn("OPEN", open_failures[0]["detail"])
        elig = g7_e2e.eligibility_view(c)
        self.assertEqual(elig["status"], "BLOCKED")
        self.assertTrue(any("OI-SYN-R1" in r["code"]
                            for r in elig["reasons"]))

    def test_material_closed_item_does_not_block(self):
        case = _load_case("RESTATEMENT")
        c = g7_e2e.build_candidate(case, case.contract)
        c["core"]["open_items"][0]["material"] = True
        c["core"]["open_items"][0]["status"] = "CLOSED"
        g7_e2e._rebuild_identity(c)
        self.assertFalse(any(r["code"].startswith("OPEN_ITEM:")
                             for r in c["failures"]))
        self.assertIn("E-G7-01-R10", [r["code"] for r in c["failures"]])


class TestMaterialityClassificationAgreement(unittest.TestCase):
    """二返工：post-load 变异把核心 claim 改成未知/未分类材料性时，审计门
    （materiality FAIL）与 release_eligible（False）必须由**同一谓词**同步
    阻断（E-G7-01-010）—— 不得审计门说 FAIL 而资格仍 True。"""

    def _unclassified_candidate(self):
        case = _load_case("POSITIVE")
        c = g7_e2e.build_candidate(case, case.contract)
        self.assertTrue(c["release_eligible"])
        c["core"]["claims"][0]["materiality"] = "UNCLASSIFIED"
        g7_e2e._rebuild_identity(c)
        return c

    def test_unclassified_mutation_blocks_and_gate_agrees(self):
        c = self._unclassified_candidate()
        self.assertFalse(c["release_eligible"])
        mat_failures = [f for f in c["failures"]
                        if f["code"] == g7_e2e.MATERIALITY_UNCLASSIFIED_CODE]
        self.assertEqual(len(mat_failures), 1)
        self.assertIn("CLAIM-SYN-01", mat_failures[0]["detail"])
        view = g7_e2e.audit_view(c)
        materiality = [g for g in view["audit"]["gates"]
                       if g["gate"] == "materiality"][0]
        self.assertEqual(materiality["verdict"], "FAIL")
        self.assertFalse(view["audit"]["release_eligible"])
        self.assertTrue(any("材料性未分类" in f
                            for f in view["audit"]["failures"]))

    def test_unknown_materiality_mutation_also_blocks(self):
        case = _load_case("POSITIVE")
        c = g7_e2e.build_candidate(case, case.contract)
        c["core"]["claims"][0]["materiality"] = "BOGUS"
        g7_e2e._rebuild_identity(c)
        self.assertFalse(c["release_eligible"])
        view = g7_e2e.audit_view(c)
        materiality = [g for g in view["audit"]["gates"]
                       if g["gate"] == "materiality"][0]
        self.assertEqual(materiality["verdict"], "FAIL")
        elig = g7_e2e.eligibility_view(c)
        self.assertEqual(elig["status"], "BLOCKED")
        self.assertTrue(any(r["code"] == g7_e2e.MATERIALITY_UNCLASSIFIED_CODE
                            for r in elig["reasons"]))

    def test_classified_mutation_does_not_block(self):
        case = _load_case("POSITIVE")
        c = g7_e2e.build_candidate(case, case.contract)
        c["core"]["claims"][0]["materiality"] = "NON_MATERIAL"
        g7_e2e._rebuild_identity(c)
        self.assertTrue(c["release_eligible"])
        self.assertFalse(any(r["code"] == g7_e2e.MATERIALITY_UNCLASSIFIED_CODE
                             for r in c["failures"]))


class TestFailureMutations(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        G7E2EHandler.runtime = g7_e2e.G7E2ERuntime()
        cls.srv = _Server(G7E2EHandler)

    @classmethod
    def tearDownClass(cls):
        cls.srv.close()

    def _launch(self, selector="POSITIVE"):
        case = _load_case(selector)
        code, body = self.srv.req("/api/research/launch", method="POST",
                                  data=case.contract)
        self.assertEqual(code, 200, body)
        return case

    def test_drop_closure_object_blocks(self):
        self._launch()
        code, body = self.srv.req("/api/g7/mutations", method="POST",
                                  data={"selector": "drop_closure_object"})
        self.assertEqual(code, 200, body)
        code, closure = self.srv.req("/api/closure")
        self.assertFalse(closure["complete"])
        self.assertEqual(closure["dangling"], 1)
        code, audit = self.srv.req("/api/audit")
        self.assertFalse(audit["audit"]["release_eligible"])
        self.assertTrue(any("E-G7-01-007" in f or "闭包不完整" in f
                            for f in audit["audit"]["failures"]))
        code, elig = self.srv.req("/api/release/eligibility")
        self.assertEqual(elig["status"], "BLOCKED")

    def test_misbind_prediction_fails_closed(self):
        self._launch()
        code, body = self.srv.req("/api/g7/mutations", method="POST",
                                  data={"selector": "misbind_prediction"})
        self.assertEqual(code, 200, body)
        code, body = self.srv.req("/api/predictions")
        self.assertEqual(code, 500, body)
        self.assertEqual(body["error"], "E-G7-01-006")
        code, audit = self.srv.req("/api/audit")
        self.assertEqual(code, 500, audit)

    def test_misbind_prediction_blocks_eligibility(self):
        # G7-01 评审发现 2：预测错绑定不得让资格端点保持 CLEAR
        self._launch()
        code, body = self.srv.req("/api/g7/mutations", method="POST",
                                  data={"selector": "misbind_prediction"})
        self.assertEqual(code, 200, body)
        code, elig = self.srv.req("/api/release/eligibility")
        self.assertEqual(code, 200, elig)
        self.assertEqual(elig["status"], "BLOCKED")
        self.assertTrue(any(r["code"] == "E-G7-01-006"
                            for r in elig["reasons"]))

    def test_corrupt_prediction_status_fails_closed_and_blocks_eligibility(self):
        self._launch()
        code, body = self.srv.req("/api/g7/mutations", method="POST",
                                  data={"selector": "corrupt_prediction_status"})
        self.assertEqual(code, 200, body)
        code, preds = self.srv.req("/api/predictions")
        self.assertEqual(code, 500, preds)
        self.assertEqual(preds["error"], "E-G7-01-006")
        code, audit = self.srv.req("/api/audit")
        self.assertEqual(code, 500, audit)
        code, elig = self.srv.req("/api/release/eligibility")
        self.assertEqual(code, 200, elig)
        self.assertEqual(elig["status"], "BLOCKED")
        self.assertTrue(any(r["code"] == "E-G7-01-006"
                            for r in elig["reasons"]))

    def test_unknown_mutation_default_deny(self):
        case = _load_case("POSITIVE")
        c = g7_e2e.build_candidate(case, case.contract)
        with self.assertRaises(g7_e2e.MutationDenied):
            g7_e2e.apply_mutation(c, "nope")

    def test_failed_mutation_leaves_state_unchanged(self):
        self._launch()
        code, before = self.srv.req("/api/audit")
        self.srv.req("/api/g7/mutations", method="POST",
                     data={"selector": "totally-unknown"})
        code, after = self.srv.req("/api/audit")
        self.assertEqual(before, after)

    def test_extra_field_mutation_leaves_state_unchanged(self):
        self._launch()
        code, before = self.srv.req("/api/audit")
        code, body = self.srv.req("/api/g7/mutations", method="POST",
                                  data={"selector": "drop_closure_object",
                                        "extra": 1})
        self.assertEqual(code, 400, body)
        self.assertEqual(body["error"], "E-G7-01-005")
        code, after = self.srv.req("/api/audit")
        self.assertEqual(before, after)


class TestControlledHttpInput(unittest.TestCase):
    """三返工：畸形 Content-Length / 深层嵌套 JSON 一律受控 400 ——
    不裸抛 ValueError / RecursionError、不做无界读取、不断开连接。"""

    @classmethod
    def setUpClass(cls):
        G7E2EHandler.runtime = g7_e2e.G7E2ERuntime()
        cls.g7 = _Server(G7E2EHandler)
        cls.prod = _Server(HealthHandler)

    @classmethod
    def tearDownClass(cls):
        cls.g7.close()
        cls.prod.close()

    def test_read_endpoint_malformed_content_length_400(self):
        status, body = _raw_http(self.g7.port, "GET", "/api/audit",
                                 body=b"{}", content_length="abc")
        self.assertEqual(status, 400, body)
        self.assertIn("E-G7-01-004", body)
        self.assertIn("Content-Length", body)

    def test_read_endpoint_negative_content_length_400(self):
        status, body = _raw_http(self.g7.port, "GET", "/api/audit",
                                 body=b"{}", content_length="-5")
        self.assertEqual(status, 400, body)
        self.assertIn("E-G7-01-004", body)
        self.assertIn("为负", body)

    def test_read_endpoint_oversized_content_length_400(self):
        # 超限 Content-Length 不得触发无界读取 —— 读取前即受控 400。
        status, body = _raw_http(
            self.g7.port, "GET", "/api/audit",
            body=b"{}", content_length=str(main.MAX_BODY_BYTES + 1))
        self.assertEqual(status, 400, body)
        self.assertIn("E-G7-01-004", body)
        self.assertIn("超过上限", body)

    def test_launch_malformed_content_length_400(self):
        status, body = _raw_http(self.g7.port, "POST", "/api/research/launch",
                                 body=b"{}", content_length="xyz")
        self.assertEqual(status, 400, body)
        self.assertIn("E-G7-01-009", body)
        self.assertIn("Content-Length", body)

    def test_launch_negative_content_length_400(self):
        status, body = _raw_http(self.g7.port, "POST", "/api/research/launch",
                                 body=b"{}", content_length="-1")
        self.assertEqual(status, 400, body)
        self.assertIn("E-G7-01-009", body)

    def test_launch_oversized_content_length_400(self):
        status, body = _raw_http(
            self.g7.port, "POST", "/api/research/launch",
            body=b"{}", content_length=str(main.MAX_BODY_BYTES + 1))
        self.assertEqual(status, 400, body)
        self.assertIn("E-G7-01-009", body)
        self.assertIn("超过上限", body)

    def test_launch_deeply_nested_json_body_400(self):
        # json.loads 对深层嵌套抛 RecursionError —— 必须归一为受控
        # E-G7-01-009，不得把裸 RecursionError 顶到 HTTP 层。
        depth = 100_000
        nested = b'{"a": ' + b"[" * depth + b"]" * depth + b"}"
        status, body = _raw_http(self.g7.port, "POST", "/api/research/launch",
                                 body=nested)
        self.assertEqual(status, 400, body)
        self.assertIn("E-G7-01-009", body)
        self.assertIn("嵌套过深", body)

    def test_production_eligibility_malformed_content_length_400(self):
        # G5 判定端点同样受控失败关闭（E-G5-002），不裸抛 ValueError。
        status, body = _raw_http(self.prod.port, "GET",
                                 "/api/release/eligibility",
                                 body=b"{}", content_length="not-a-number")
        self.assertEqual(status, 400, body)
        self.assertIn("E-G5-002", body)
        self.assertIn("Content-Length", body)

    def test_production_eligibility_oversized_content_length_400(self):
        status, body = _raw_http(
            self.prod.port, "GET", "/api/release/eligibility",
            body=b"{}", content_length=str(main.MAX_BODY_BYTES + 1))
        self.assertEqual(status, 400, body)
        self.assertIn("E-G5-002", body)


class TestRecordCompleteness(unittest.TestCase):
    """三返工：completeness 门不再硬编码 PASS —— 记录组非空 / refs 可解析 /
    evidence 哈希匹配由**同一谓词**同时驱动审计门与资格重算（E-G7-01-011）。"""

    def _candidate(self):
        case = _load_case("POSITIVE")
        c = g7_e2e.build_candidate(case, case.contract)
        self.assertTrue(c["release_eligible"])
        return c

    def _assert_completeness_fail(self, c):
        self.assertFalse(c["release_eligible"])
        self.assertTrue(any(f["code"] == g7_e2e.RECORD_COMPLETENESS_CODE
                            for f in c["failures"]))
        view = g7_e2e.audit_view(c)
        completeness = [g for g in view["audit"]["gates"]
                        if g["gate"] == "completeness"][0]
        self.assertEqual(completeness["verdict"], "FAIL")
        self.assertFalse(view["audit"]["release_eligible"])
        self.assertTrue(any("完整性阻断" in f
                            for f in view["audit"]["failures"]))
        elig = g7_e2e.eligibility_view(c)
        self.assertEqual(elig["status"], "BLOCKED")
        self.assertTrue(any(r["code"] == g7_e2e.RECORD_COMPLETENESS_CODE
                            for r in elig["reasons"]))

    def test_record_completeness_healthy_path(self):
        c = self._candidate()
        self.assertTrue(c["release_eligible"])
        view = g7_e2e.audit_view(c)
        completeness = [g for g in view["audit"]["gates"]
                        if g["gate"] == "completeness"][0]
        self.assertEqual(completeness["verdict"], "PASS")
        self.assertFalse(any(f["code"] == g7_e2e.RECORD_COMPLETENESS_CODE
                             for f in c["failures"]))

    def test_dropped_evidence_blocks_completeness(self):
        c = self._candidate()
        c["core"]["evidence"] = c["core"]["evidence"][:1]
        g7_e2e._rebuild_identity(c)
        self._assert_completeness_fail(c)

    def test_dangling_claim_ref_blocks_completeness(self):
        c = self._candidate()
        c["core"]["claims"][0]["refs"] = ["EV-NO-SUCH"]
        g7_e2e._rebuild_identity(c)
        self._assert_completeness_fail(c)

    def test_evidence_hash_drift_blocks_completeness(self):
        c = self._candidate()
        c["core"]["evidence"][0]["content"] = "SYNTHETIC：篡改后的内容"
        g7_e2e._rebuild_identity(c)
        self._assert_completeness_fail(c)


class TestMainHandlerSelection(unittest.TestCase):
    """三返工：main() 的 handler 选择由环境旗标决定 —— 未来把 G7 无条件
    挂载会立刻判红（无旗标必须仍是 HealthHandler，不暴露合成端点）。"""

    def _handler_for(self, env, *, absent=False):
        captured = {}

        class _FakeServer:
            def __init__(self, addr, handler_cls):
                captured["handler"] = handler_cls

            def serve_forever(self):
                pass

        settings = unittest.mock.Mock()
        settings.validate.return_value = []
        settings.log_level = "INFO"
        settings.bind_host = "127.0.0.1"
        settings.app_port = 8080
        with unittest.mock.patch.dict(os.environ, env, clear=False):
            if absent:
                os.environ.pop(g7_e2e.G7_E2E_MODE_ENV, None)
            with unittest.mock.patch("main.ThreadingHTTPServer", _FakeServer), \
                    unittest.mock.patch("main.setup_logging"), \
                    unittest.mock.patch("main.get_settings",
                                        return_value=settings), \
                    unittest.mock.patch("sys.argv",
                                        ["main.py", "--bind", "127.0.0.1"]):
                self.assertEqual(main.main(), 0)
        return captured["handler"]

    def test_main_no_flag_uses_health_handler(self):
        self.assertIs(self._handler_for({}, absent=True), HealthHandler)

    def test_main_flag_uses_g7_handler(self):
        self.assertIs(self._handler_for({g7_e2e.G7_E2E_MODE_ENV: "1"}),
                      G7E2EHandler)


if __name__ == "__main__":
    unittest.main()
