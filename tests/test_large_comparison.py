"""Bounded presentation must preserve every comparison verdict and exact input."""
from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from equilane.artifacts import validate_input_artifact, validate_result_artifact
from equilane.canonical import dump_bytes, iter_canonical_jsonl, read_canonical_json, sha256_bytes, write_jsonl
from equilane.cases import load_case_definitions
from equilane.comparison import ComparisonContext, compare_artifacts
from equilane.errors import ValidationError
from equilane.fixture import run_fixture
from equilane.generator import generate_artifact
from equilane.isa import load_isa_registry
from equilane.replay import load_failure_bundle
from equilane.showcase import _native_result_details


class LargeComparisonTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temporary.cleanup)
        cls.root = Path(cls.temporary.name)
        cls.isa = load_isa_registry(ROOT / "contracts/isa-registry.json")
        cls.cases = load_case_definitions(ROOT / "10_official_suite/cases/add-f64x2.py", isa_registry=cls.isa)
        generated = generate_artifact(cases=cls.cases, isa_registry=cls.isa,
                                      output=cls.root / "vectors", count_per_case=8)
        inputs = validate_input_artifact(generated.manifest_path, cls.cases, cls.isa)
        for role in ("intel", "openpower"):
            run_fixture(input_artifact=inputs, cases=cls.cases, isa_registry=cls.isa,
                        role=role, output=cls.root / role)
        manifest_path = cls.root / "openpower/power-results.manifest.json"
        manifest = read_canonical_json(manifest_path)
        records_path = manifest_path.parent / manifest["results"]["file"]
        records = list(iter_canonical_jsonl(records_path))
        for index, record in enumerate(records):
            # Force a real observed finite value/status difference, independent
            # of a model's numerical output (NaN comparison policies included).
            record.pop("observed")
            record["status"] = "runtime_error"
            record["error"] = {"stage": "execution", "code": "test.injected"}
        count, length, digest = write_jsonl(records_path, records)
        manifest["results"].update(record_count=count, byte_length=length, sha256=digest)
        manifest_path.write_bytes(dump_bytes(manifest, newline=True))
        cls.context = ComparisonContext(cls.cases, cls.isa, inputs,
            validate_result_artifact(cls.root / "intel", cls.cases, cls.isa, input_artifact=inputs),
            validate_result_artifact(cls.root / "openpower", cls.cases, cls.isa, input_artifact=inputs))

    def test_limits_preserve_all_verdicts_exact_records_and_authenticated_archive(self):
        for limit in (0, 1, 3, 20):
            with self.subTest(limit=limit), tempfile.TemporaryDirectory() as temporary:
                output = Path(temporary)
                run = compare_artifacts(self.context, output=output,
                    allow_development_fixtures=True, failure_limit=limit)
                self.assertEqual((run.record_count, run.mismatched_inputs, run.exit_code), (8, 8, 1))
                summary = read_canonical_json(run.files.summary_path)
                self.assertEqual(summary["failure_bundles_saved"], min(8, limit))
                self.assertEqual(summary["failure_bundles_omitted"], max(0, 8 - limit))
                self.assertEqual(len(summary["failure_bundles"]), min(8, limit))
                metadata = summary["differences"]
                raw = (output / metadata["file"]).read_bytes()
                self.assertEqual((metadata["record_count"], metadata["byte_length"], metadata["sha256"]),
                                 (8, len(raw), sha256_bytes(raw)))
                rows = list(iter_canonical_jsonl(output / metadata["file"]))
                self.assertEqual([row["input"] for row in rows], list(self.context.input_artifact.records))
                for row in rows:
                    self.assertEqual(row["comparison"]["outcome"], "mismatch")
                    self.assertEqual(row["intel"], self.context.intel_artifact.records[row["input_id"]])
                    self.assertEqual(row["target"], self.context.openpower_artifact.records[row["input_id"]])
                suite = ET.parse(run.files.junit_path).getroot()
                self.assertEqual((suite.get("tests"), suite.get("failures")), ("8", "8"))
                for bundle in summary["failure_bundles"]:
                    self.assertIsNotNone(load_failure_bundle(output / bundle))

    def test_adversarial_defaults_to_bounded_archive_without_rewriting_inputs(self):
        inputs = replace(self.context.input_artifact,
                         manifest={**self.context.input_artifact.manifest, "profile": "adversarial"})
        with tempfile.TemporaryDirectory() as temporary:
            run = compare_artifacts(replace(self.context, input_artifact=inputs),
                                    output=temporary, allow_development_fixtures=True)
            summary = read_canonical_json(run.files.summary_path)
            self.assertEqual(summary["failure_bundle_limit"], 128)
            self.assertEqual(summary["differences"]["record_count"], 8)

    def test_invalid_limits_do_not_publish_a_summary(self):
        for limit in (-1, True, "1"):
            with tempfile.TemporaryDirectory() as temporary:
                with self.assertRaises(ValidationError):
                    compare_artifacts(self.context, output=temporary,
                        allow_development_fixtures=True, failure_limit=limit)
                self.assertFalse((Path(temporary) / "summary.json").exists())

    def test_failed_summary_publish_never_leaves_an_incomplete_completion_marker(self):
        from equilane import report
        original = report.atomic_write
        def fail_summary(path, data):
            if Path(path).name == "summary.json":
                raise OSError("simulated output failure")
            return original(path, data)
        with tempfile.TemporaryDirectory() as temporary:
            with patch("equilane.report.atomic_write", side_effect=fail_summary):
                with self.assertRaises(OSError):
                    compare_artifacts(self.context, output=temporary,
                        allow_development_fixtures=True, failure_limit=0)
            self.assertTrue((Path(temporary) / "differences.jsonl").exists())
            self.assertFalse((Path(temporary) / "summary.json").exists())

    def test_report_discloses_display_limit_and_offers_complete_archive(self):
        # No rendered cards are needed to verify the download remains present
        # even when failure-limit=0 intentionally keeps the HTML minimal.
        html = _native_result_details({"native_findings": [], "native_findings_total": 8,
            "differences": {"file": "differences.jsonl", "record_count": 8}}, native_evidence=True)
        self.assertIn('href="differences.jsonl"', html)
        self.assertIn("全 8件", html)


if __name__ == "__main__":
    unittest.main()
