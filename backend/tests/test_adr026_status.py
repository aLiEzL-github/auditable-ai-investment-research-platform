"""ADR-026 single-person red-team status reachability tests."""

import copy
import contextlib
import datetime as dt
import importlib.util
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
import warnings
from unittest import mock


ROOT = Path(__file__).resolve().parents[2]
TOOLS = ROOT / "backend" / "tools"
sys.path.insert(0, str(TOOLS))

from red_team_marker_check import (  # noqa: E402
    RT_SOLO,
    attestation_missing,
    main as marker_main,
)


def _attestation():
    now = dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
    return {
        "task_status": RT_SOLO,
        "red_team_performed_at": now.isoformat().replace("+00:00", "Z"),
        "red_team_reviewer": "U",
        "independence": "same natural person",
        "independent_red_team_present": False,
        "reviewed_products": ["candidate"],
        "scope_not_covered": [],
        "red_team_evidence": [
            {"check": "verify", "command": "verify", "output": "PASS"},
        ],
        "agent_findings_disposition": [
            {"open_item_id": "OI-1", "disposition": "accepted", "basis": "evidence"},
        ],
        "findings": {
            "P0": 0,
            "P1": 0,
            "P2": [
                {"owner": "DEV", "due": "2026-08-22",
                 "materiality": "non-material", "basis": "fail closed"},
            ],
        },
    }


def _write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def _portfolio(root, record, persons=1):
    _write_json(root / "risk" / "open-items.json", {"items": []})
    records = {
        "G6A-01": "DONE",
        "G6A-02": "NOT_APPLICABLE_PENDING_PROVIDER",
        "G6A-03": "NOT_APPLICABLE_PENDING_PROVIDER",
        "G6A-04": "NOT_APPLICABLE_PENDING_PROVIDER",
        "G6A-05": "DONE",
        "G6B-01": "NOT_APPLICABLE",
        "G6B-02": "NOT_APPLICABLE",
        "G6B-03": "NOT_APPLICABLE",
        "G6B-04": "NOT_APPLICABLE",
        "G6C-01": "DONE",
        "G6C-02": "DONE",
        "G6C-03": "DONE",
        "G6-01": "READY_FOR_SIGNING",
    }
    for task_id, status in records.items():
        _write_json(root / "task-records" / f"{task_id}.json",
                    {"task_status": status})
    _write_json(root / "task-records" / "G6A-06.json", record)
    decisions = root / "decisions-v2"
    decisions.mkdir(parents=True, exist_ok=True)
    (decisions / "VD-02.md").write_text(
        f"baseline_natural_persons = {persons}\n", encoding="utf-8")
    (decisions / "VD-14.md").write_text(
        "backtest_mode = REMOVED\n", encoding="utf-8")
    (decisions / "VD-26.md").write_text(
        "CALIBRATION_PENDING\n", encoding="utf-8")


def _load_generator(filename, portfolio):
    name = f"_test_{filename}_{id(portfolio)}"
    spec = importlib.util.spec_from_file_location(name, TOOLS / filename)
    module = importlib.util.module_from_spec(spec)
    previous = os.environ.get("PORTFOLIO_ROOT")
    os.environ["PORTFOLIO_ROOT"] = str(portfolio)
    try:
        spec.loader.exec_module(module)
    finally:
        if previous is None:
            os.environ.pop("PORTFOLIO_ROOT", None)
        else:
            os.environ["PORTFOLIO_ROOT"] = previous
    module._real_status_of = module.status_of
    module.status_of = lambda cmd: (
        "H-8 PASS \u2014\u2014 \u68c0\u67e5\u5bf9\u8c61 1 \u4e2a"
        if "calibration_claim_check.py" in cmd else "OK")
    audit = "\u5408\u8ba1 72 \u9879\uff1aPASS 72 / FAIL 0"
    module.run = lambda _cmd: (0, audit, "")
    return module


def _run_generator(module):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", ResourceWarning)
        with contextlib.redirect_stderr(io.StringIO()):
            return module.main()


def _run_marker(portfolio):
    with mock.patch.object(sys, "argv", ["red_team_marker_check.py", str(portfolio)]):
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", ResourceWarning)
            with contextlib.redirect_stdout(io.StringIO()):
                return marker_main()


class TestAdr026StatusReachability(unittest.TestCase):
    def test_shared_predicate_accepts_complete_and_rejects_p1(self):
        record = _attestation()
        self.assertEqual(attestation_missing(record, 1), [])
        record["findings"]["P1"] = 1
        self.assertTrue(attestation_missing(record, 1))
        record = _attestation()
        record["agent_findings_disposition"] = []
        self.assertTrue(attestation_missing(record, 1))
        self.assertTrue(attestation_missing(_attestation(), 2))
        future = _attestation()
        future["red_team_performed_at"] = "2999-01-01T00:00:00Z"
        self.assertTrue(attestation_missing(future, 1))
        expired = _attestation()
        expired["red_team_performed_at"] = "2020-01-01T00:00:00Z"
        self.assertTrue(attestation_missing(expired, 1))
        no_time = _attestation()
        no_time.pop("red_team_performed_at")
        self.assertTrue(attestation_missing(no_time, 1))
        wrong_independence = _attestation()
        wrong_independence["independent_red_team_present"] = True
        self.assertTrue(attestation_missing(wrong_independence, 1))
        malformed_p2 = _attestation()
        malformed_p2["findings"]["P2"][0].pop("basis")
        self.assertTrue(attestation_missing(malformed_p2, 1))

    def test_gate6a_accepts_complete_precise_status(self):
        with tempfile.TemporaryDirectory() as temp:
            portfolio = Path(temp)
            _portfolio(portfolio, _attestation())
            module = _load_generator("build_gate6a_acceptance.py", portfolio)
            self.assertEqual(_run_generator(module), 0)
            name = "Gate6A-\u9a8c\u6536\u5305.md"
            package = (portfolio / name).read_text(encoding="utf-8")
            self.assertIn("G6A_\u5206\u652f_READY", package)
            self.assertIn(RT_SOLO, package)
            self.assertIn("independent_red_team_present = false", package)

    def test_gate6a_rejects_incomplete_precise_status(self):
        with tempfile.TemporaryDirectory() as temp:
            portfolio = Path(temp)
            record = copy.deepcopy(_attestation())
            record.pop("scope_not_covered")
            _portfolio(portfolio, record)
            module = _load_generator("build_gate6a_acceptance.py", portfolio)
            self.assertEqual(_run_generator(module), 0)
            name = "Gate6A-\u9a8c\u6536\u5305.md"
            package = (portfolio / name).read_text(encoding="utf-8")
            self.assertIn("G6A_\u5206\u652f_NOT_READY", package)
            self.assertIn("scope_not_covered", package)

    def test_gate6_precise_verdict_does_not_claim_blocked(self):
        with tempfile.TemporaryDirectory() as temp:
            portfolio = Path(temp)
            _portfolio(portfolio, _attestation())
            module = _load_generator("build_gate6_acceptance.py", portfolio)
            self.assertEqual(_run_generator(module), 0)
            name = "Gate6-\u9a8c\u6536\u5305.md"
            package = (portfolio / name).read_text(encoding="utf-8")
            self.assertIn("G6_JOINT_PASSED_SINGLE_PERSON_RED_TEAM", package)
            self.assertNotIn("G6_JOINT_BLOCKED", package)
            self.assertIn("independent_red_team_present = false", package)

    def test_gate6_status_parser_accepts_pass_without_false_not_run_label(self):
        with tempfile.TemporaryDirectory() as temp:
            portfolio = Path(temp)
            _portfolio(portfolio, _attestation())
            module = _load_generator("build_gate6_acceptance.py", portfolio)
            module.run = lambda _cmd: (
                0, "\u2705 H-8 \u8868\u8ff0\u5b88\u536b PASS \u2014\u2014 zero false claims", "")
            status = module._real_status_of("unused")
            self.assertIn("PASS", status)
            self.assertNotIn("\u672a\u8dd1\u8d77\u6765", status)
            module.run = lambda _cmd: (1, "PASS", "injected failure")
            self.assertFalse(module.status_ok(module._real_status_of("unused")))
            module.run = lambda _cmd: (0, "completed without marker", "")
            self.assertFalse(module.status_ok(module._real_status_of("unused")))
            module.run = lambda _cmd: (0, "PASS 1 / FAIL 1", "")
            self.assertFalse(module.status_ok(module._real_status_of("unused")))

    def test_gate6_blocks_when_any_engineering_check_fails(self):
        failures = (
            "backend.tests.test_g6a_01",
            "backend.tests.test_g6c_01",
            "unittest discover",
            "calibration_claim_check.py",
        )
        for failed_command in failures:
            with self.subTest(failed_command=failed_command):
                with tempfile.TemporaryDirectory() as temp:
                    portfolio = Path(temp)
                    _portfolio(portfolio, _attestation())
                    module = _load_generator("build_gate6_acceptance.py", portfolio)
                    module.status_of = lambda cmd: (
                        "FAILED injected" if failed_command in cmd else
                        ("H-8 PASS \u2014\u2014 \u68c0\u67e5\u5bf9\u8c61 1 \u4e2a"
                         if "calibration_claim_check.py" in cmd else "OK"))
                    self.assertEqual(_run_generator(module), 0)
                    package = (portfolio / "Gate6-\u9a8c\u6536\u5305.md").read_text(
                        encoding="utf-8")
                    self.assertIn("G6_JOINT_BLOCKED", package)
                    self.assertIn("FAILED injected", package.split("## 1", 1)[0])

        with tempfile.TemporaryDirectory() as temp:
            portfolio = Path(temp)
            _portfolio(portfolio, _attestation())
            module = _load_generator("build_gate6_acceptance.py", portfolio)
            module.status_of = lambda cmd: (
                "H-8 PASS \u2014\u2014 \u68c0\u67e5\u5bf9\u8c61 0 \u4e2a"
                if "calibration_claim_check.py" in cmd else "OK")
            self.assertEqual(_run_generator(module), 0)
            package = (portfolio / "Gate6-\u9a8c\u6536\u5305.md").read_text(
                encoding="utf-8")
            self.assertIn("G6_JOINT_BLOCKED", package)
            self.assertIn("\u68c0\u67e5\u5bf9\u8c61 0 \u4e2a", package)

    def test_gate6_blocks_when_ledger_audit_fails(self):
        failures = (
            (1, "\u5408\u8ba1 72 \u9879\uff1aPASS 72 / FAIL 0"),
            (0, "\u5408\u8ba1 72 \u9879\uff1aPASS 72 / FAIL 1"),
            (0, "\u5408\u8ba1 72 \u9879\uff1aPASS 71 / FAIL 0"),
            (0, "\u5408\u8ba1 0 \u9879\uff1aPASS 0 / FAIL 0"),
            (0, "audit completed without summary"),
        )
        for rc, audit_output in failures:
            with self.subTest(rc=rc, audit_output=audit_output):
                with tempfile.TemporaryDirectory() as temp:
                    portfolio = Path(temp)
                    _portfolio(portfolio, _attestation())
                    module = _load_generator("build_gate6_acceptance.py", portfolio)
                    module.run = lambda cmd: (
                        (rc, audit_output, "") if "audit_session.py" in cmd else
                        (0, "OK", ""))
                    self.assertEqual(_run_generator(module), 0)
                    package = (portfolio / "Gate6-\u9a8c\u6536\u5305.md").read_text(
                        encoding="utf-8")
                    self.assertIn("G6_JOINT_BLOCKED", package)
                    self.assertIn(audit_output, package)

    def test_person_count_selects_single_or_independent_path(self):
        with tempfile.TemporaryDirectory() as temp:
            portfolio = Path(temp)
            _portfolio(portfolio, {"task_status": "DONE"}, persons=1)
            gate6a = _load_generator("build_gate6a_acceptance.py", portfolio)
            gate6 = _load_generator("build_gate6_acceptance.py", portfolio)
            self.assertEqual(_run_generator(gate6a), 0)
            self.assertEqual(_run_generator(gate6), 0)
            self.assertIn("G6A_\u5206\u652f_NOT_READY", (
                portfolio / "Gate6A-\u9a8c\u6536\u5305.md").read_text(encoding="utf-8"))
            self.assertIn("G6_JOINT_BLOCKED", (
                portfolio / "Gate6-\u9a8c\u6536\u5305.md").read_text(encoding="utf-8"))

        with tempfile.TemporaryDirectory() as temp:
            portfolio = Path(temp)
            _portfolio(portfolio, _attestation(), persons=2)
            gate6a = _load_generator("build_gate6a_acceptance.py", portfolio)
            gate6 = _load_generator("build_gate6_acceptance.py", portfolio)
            self.assertEqual(_run_generator(gate6a), 0)
            self.assertEqual(_run_generator(gate6), 0)
            self.assertIn("G6A_\u5206\u652f_NOT_READY", (
                portfolio / "Gate6A-\u9a8c\u6536\u5305.md").read_text(encoding="utf-8"))
            self.assertIn("G6_JOINT_BLOCKED", (
                portfolio / "Gate6-\u9a8c\u6536\u5305.md").read_text(encoding="utf-8"))

        with tempfile.TemporaryDirectory() as temp:
            portfolio = Path(temp)
            independent = {
                "task_status": "DONE",
                "red_team_reviewer": "R2",
                "findings": {"P0": 0, "P1": 0},
            }
            _portfolio(portfolio, independent, persons=2)
            gate6a = _load_generator("build_gate6a_acceptance.py", portfolio)
            gate6 = _load_generator("build_gate6_acceptance.py", portfolio)
            self.assertEqual(_run_generator(gate6a), 0)
            self.assertEqual(_run_generator(gate6), 0)
            self.assertIn("G6A_\u5206\u652f_READY", (
                portfolio / "Gate6A-\u9a8c\u6536\u5305.md").read_text(encoding="utf-8"))
            self.assertIn("G6_JOINT_READY", (
                portfolio / "Gate6-\u9a8c\u6536\u5305.md").read_text(encoding="utf-8"))

        for malformed in (
                {"task_status": "DONE"},
                {"task_status": "DONE", "red_team_reviewer": "R2",
                 "findings": {"P0": 1, "P1": 0}}):
            with self.subTest(malformed=malformed):
                with tempfile.TemporaryDirectory() as temp:
                    portfolio = Path(temp)
                    _portfolio(portfolio, malformed, persons=2)
                    gate6a = _load_generator("build_gate6a_acceptance.py", portfolio)
                    gate6 = _load_generator("build_gate6_acceptance.py", portfolio)
                    self.assertEqual(_run_generator(gate6a), 0)
                    self.assertEqual(_run_generator(gate6), 0)
                    self.assertIn("G6A_\u5206\u652f_NOT_READY", (
                        portfolio / "Gate6A-\u9a8c\u6536\u5305.md").read_text(
                            encoding="utf-8"))
                    self.assertIn("G6_JOINT_BLOCKED", (
                        portfolio / "Gate6-\u9a8c\u6536\u5305.md").read_text(
                            encoding="utf-8"))

    def test_marker_entry_point_enforces_shared_predicate(self):
        with tempfile.TemporaryDirectory() as temp:
            portfolio = Path(temp)
            record = _attestation()
            _portfolio(portfolio, record)
            gate6 = _load_generator("build_gate6_acceptance.py", portfolio)
            self.assertEqual(_run_generator(gate6), 0)
            self.assertEqual(_run_marker(portfolio), 0)
            record["agent_findings_disposition"] = []
            _write_json(portfolio / "task-records" / "G6A-06.json", record)
            self.assertEqual(_run_marker(portfolio), 1)


if __name__ == "__main__":
    unittest.main()
