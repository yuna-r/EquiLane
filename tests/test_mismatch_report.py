"""The detailed view preserves evidence boundaries and never reruns tests."""

from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
import copy
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "src"))

from equilane.canonical import dumps, sha256_bytes
from equilane.cli import main
from equilane.errors import ValidationError
from equilane.mismatch_report import python_summary, render_mismatch_report, write_mismatch_report


def quality():
    return {
        "status": "fail", "execution_environment": {"machine": "arm64", "system": "Darwin"},
        "failure_classification": {"portable_model_output_mismatches": 1,
                                   "other_assertion_failures": 0, "test_execution_errors": 0},
        "model_output_mismatches": [{
            "subject": "Python portable case model", "finding_family": "nan_indefinite_sign",
            "contract_scope": "official_nearest_even", "test_id": "test_nan",
            "oracle_reference": "tests/BUG_HUNT.md",
            "input": {"case_id": "sse2.add.f64x2.default", "input_id": "a" * 64,
                      "environment": {"rounding": "nearest_even"},
                      "operands": {"a": {"element": "f64", "lanes": ["0x7ff0000000000000", "0x0000000000000000"]}}},
            "expected": {"return": {"element": "f64", "lanes": ["0xfff8000000000000", "0x0000000000000000"]}},
            "actual": {"return": {"element": "f64", "lanes": ["0x7ff8000000000000", "0x0000000000000000"]}},
        }],
    }


def observations():
    return {"capture_date": "2026-10-16", "native_observations": {
        "arm": {"execution_kind": "native", "reference": "SSE2 under Rosetta",
                "capture_date": "2026-10-16", "representation": "hex integer bits",
                "coverage": "one selected diagnostic example", "environment": {"machine": "arm64"},
                "sample_counts": {"diagnostic_examples": 1}, "sources": ["tests/native/test_arm_wrappers.c"],
                "reproduce": ["native-selftest"], "differences": [{"label": "invalid add",
                    "input": "+inf + -inf", "expected": "0xfff8000000000000", "actual": "0x7ff8000000000000"}]},
        "openpower": {"execution_kind": "cross_build_only", "coverage": "runtime unverified",
                      "differences": [], "sample_counts": {}},
    }}


class Structure(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ids, self.anchors, self.external, self.external_links = set(), set(), [], []

    def handle_starttag(self, tag, attrs):
        value = dict(attrs)
        if "id" in value:
            self.ids.add(value["id"])
        for key in ("src", "href"):
            link = value.get(key, "")
            if link.startswith("#"):
                self.anchors.add(link[1:])
            elif link and not link.startswith("data:"):
                if tag == "a" and key == "href":
                    self.external_links.append(link)
                else:
                    self.external.append(link)


class MismatchReportTests(unittest.TestCase):
    def test_standalone_and_wrapped_quality_summaries_render_the_same_model_evidence(self):
        value = quality()
        wrapped = {"status": "fail", "gates": {"python_coverage": value}}
        self.assertEqual(python_summary(value), value)
        self.assertEqual(python_summary(wrapped), value)
        for summary in (value, wrapped):
            with self.subTest(wrapped="gates" in summary):
                html = render_mismatch_report(summary)
                self.assertIn("0xfff8000000000000", html)
                self.assertIn("0x7ff8000000000000", html)
                self.assertIn("sse2.add.f64x2.default", html)
                self.assertIn("各CPUの一致・不一致は判定しません", html)

    def test_page_is_self_contained_and_all_navigation_targets_exist(self):
        html = render_mismatch_report(quality(), observations=observations())
        parser = Structure()
        parser.feed(html)
        self.assertEqual(parser.external, [])
        self.assertTrue(all(url.startswith("https://www.intel.com/content/www/us/en/docs/intrinsics-guide/index.html")
                            for url in parser.external_links))
        self.assertLessEqual(parser.anchors, parser.ids)
        self.assertIn('lang="ja"', html)
        self.assertIn('name="viewport"', html)

    def test_native_examples_are_a_separate_snapshot_and_missing_execution_is_not_zero_mismatches(self):
        html = render_mismatch_report(quality(), observations=observations())
        self.assertIn("Pythonモデル検証とは別の実行記録", html)
        self.assertIn("検証を再実行していません", html)
        self.assertIn("2026-10-16", html)
        self.assertIn("クロスビルドのみ", html)
        self.assertIn("未実行の対象の一致を意味しません", html)
        self.assertIn("SSE2 under Rosetta", html)
        self.assertIn("tests/native/test_arm_wrappers.c", html)

    def test_native_and_provenance_fields_are_escaped(self):
        snapshot = observations()
        item = snapshot["native_observations"]["arm"]
        item["coverage"] = '<script>alert("coverage")</script>'
        item["differences"][0]["input"] = '</pre><img src=x onerror=alert(1)>'
        item["sources"] = ["<svg onload=alert(1)>"]
        snapshot["evidence"] = {"path": "<script>provenance</script>"}
        html = render_mismatch_report(quality(), observations=snapshot)
        for raw in ('<script>alert("coverage")', '<img src=x', '<svg onload=', '<script>provenance'):
            self.assertNotIn(raw, html)
        self.assertIn("&lt;script&gt;", html)
        self.assertIn("&lt;img", html)

    def test_timestamps_are_utc_and_do_not_claim_a_verification_execution_time(self):
        generated = datetime(2026, 10, 16, 9, 30, tzinfo=timezone(timedelta(hours=9)))
        html = render_mismatch_report(quality(), generated_at=generated)
        self.assertIn("2026-10-16 00:30:00 UTC", html)
        self.assertIn("検証実行日時を表すものではありません", html)

    def test_source_digest_authenticates_the_complete_input_not_only_findings(self):
        value = quality()
        html = render_mismatch_report(value)
        self.assertIn(sha256_bytes(dumps(value).encode()), html)
        changed = copy.deepcopy(value)
        changed["execution_environment"]["machine"] = "different"
        self.assertNotEqual(sha256_bytes(dumps(value).encode()), sha256_bytes(dumps(changed).encode()))

    def test_zero_model_findings_does_not_claim_native_equivalence(self):
        value = quality()
        value["model_output_mismatches"] = []
        value["failure_classification"]["portable_model_output_mismatches"] = 0
        html = render_mismatch_report(value)
        self.assertIn("<strong>0件</strong>", html)
        self.assertNotIn("PAIRED COMPARISON PASSED", html)
        self.assertIn("各CPUの一致・不一致は判定しません", html)

    def test_invalid_observations_do_not_replace_an_existing_report(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "report.html"
            output.write_bytes(b"previous report")
            with self.assertRaises(ValidationError):
                write_mismatch_report(output, summary=quality(), observations={"native_observations": []})
            self.assertEqual(output.read_bytes(), b"previous report")

    def test_cli_reads_pretty_json_and_writes_report_without_running_verification(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source, snapshot = root / "summary.json", root / "observations.json"
            source.write_text(json.dumps(quality(), indent=2), encoding="utf-8")
            snapshot.write_text(json.dumps(observations(), indent=2), encoding="utf-8")
            output = root / "nested/report.html"
            stdout, stderr = io.StringIO(), io.StringIO()
            with mock.patch("equilane.cli.run_quality_gates", side_effect=AssertionError("must not run")), \
                 mock.patch("equilane.development.load_development_case", side_effect=AssertionError("must not run")), \
                 redirect_stdout(stdout), redirect_stderr(stderr):
                code = main(["mismatch-report", "--quality", str(source), "--observations", str(snapshot),
                             "--output", str(output)])
            self.assertEqual(code, 0, stderr.getvalue())
            result = json.loads(stdout.getvalue())
            self.assertEqual(result["status"], "report_written")
            self.assertEqual(result["model_finding_count"], 1)
            self.assertIs(result["verification_rerun"], False)
            self.assertIs(result["native_observations_attached"], True)
            self.assertTrue(output.is_file())

    def test_cli_cannot_overwrite_either_evidence_file(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source, snapshot = root / "summary.json", root / "observations.json"
            source.write_text(json.dumps(quality()))
            snapshot.write_text(json.dumps(observations()))
            for destination in (source, snapshot):
                with self.subTest(destination=destination.name):
                    before = destination.read_bytes()
                    with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                        code = main(["mismatch-report", "--quality", str(source), "--observations", str(snapshot),
                                     "--output", str(destination)])
                    self.assertEqual(code, 2)
                    self.assertEqual(destination.read_bytes(), before)

    def test_invalid_quality_is_reported_without_a_traceback_or_output(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source, output = root / "summary.json", root / "report.html"
            source.write_text('{"model_output_mismatches":[] ,"model_output_mismatches":[]}')
            stderr = io.StringIO()
            with redirect_stdout(io.StringIO()), redirect_stderr(stderr):
                code = main(["mismatch-report", "--quality", str(source), "--output", str(output)])
            self.assertEqual(code, 2)
            self.assertNotIn("Traceback", stderr.getvalue())
            self.assertFalse(output.exists())

    def test_committed_native_snapshot_contains_no_private_absolute_paths(self):
        raw = (PROJECT / "tests/data/mismatch-report-2026-10-16.json").read_text()
        self.assertNotIn("/Users/", raw)
        self.assertNotIn("macstudio", raw)
        snapshot = json.loads(raw)
        html = render_mismatch_report(quality(), observations=snapshot)
        self.assertIn("RISC-V64 RVV", html)
        self.assertIn("エミュレーターで実行", html)


for _label, _value in {
    "missing_findings": {}, "null_findings": {"model_output_mismatches": None},
    "non_object_finding": {"model_output_mismatches": [1]},
    "count_disagrees": {"model_output_mismatches": [], "failure_classification": {"portable_model_output_mismatches": 1}},
    "boolean_count": {"model_output_mismatches": [{}], "failure_classification": {"portable_model_output_mismatches": True}},
    "invalid_gates": {"gates": []}, "missing_python_gate": {"gates": {}},
}.items():
    def _test(self, value=_value):
        with self.assertRaises(ValidationError):
            python_summary(value)
    setattr(MismatchReportTests, "test_reject_" + _label, _test)


if __name__ == "__main__":
    unittest.main()
