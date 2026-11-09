"""Native-container evidence must be complete and preserve byte differences."""
from __future__ import annotations

import argparse
import contextlib
import copy
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("container_native_suite", ROOT / "docker/native_suite.py")
native_suite = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(native_suite)


class ContainerNativeSuiteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.functions = native_suite.declarations()
        cls.inputs = native_suite.corpus(cls.functions)

    def _capture(self, root, target, *, mutate=None):
        directory = root / target
        directory.mkdir()
        inputs = native_suite.encoded(self.inputs)
        rows = [{"sample": sample["sample"], "bytes": "00" * sample["output_bytes"]}
                for sample in self.inputs["samples"]]
        if mutate:
            mutate(rows)
        outputs = b"".join(native_suite.encoded(row) for row in rows)
        manifest = {
            "artifact_type": "equilane.native-wrapper-capture", "schema_version": 1,
            "status": "captured", "target": target, "operations": 254, "samples": 762,
            "corpus_sha256": native_suite.digest(inputs), "outputs_sha256": native_suite.digest(outputs),
            "source_sha256": native_suite.source_hashes(),
            "architecture": native_suite.TARGETS[target][1][0],
            "executable_sha256": "1" * 64,
            "environment": {"execution_mode": "docker-emulated"},
            "scope": {"encoded_adapter_abi": False, "fp_exceptions": False},
        }
        (directory / "manifest.json").write_bytes(native_suite.encoded(manifest))
        (directory / "corpus.json").write_bytes(inputs)
        (directory / "outputs.jsonl").write_bytes(outputs)
        return directory

    def test_corpus_covers_every_official_case_and_all_variants(self):
        cases = {path.stem.replace("-", "_") for path in (ROOT / "10_official_suite/cases").glob("*.py")}
        self.assertEqual({name for name, _, _ in self.functions}, cases)
        self.assertEqual(len(self.inputs["samples"]), 762)
        self.assertEqual(len({sample["sample"] for sample in self.inputs["samples"]}), 762)
        by_name = {sample["sample"]: sample for sample in self.inputs["samples"]}
        # Signed zero, a signaling NaN and immediate boundary inputs are bit exact.
        self.assertEqual(by_name["add_f64x2#0"]["arguments"][0]["bytes"],
                         "00000000000000000000000000000080")
        self.assertEqual(by_name["add_f64x2#2"]["arguments"][0]["bytes"],
                         "420000000000f87f350000000000f07f")
        self.assertEqual(by_name["shuffle_i32x4#2"]["arguments"][-1]["bytes"], "ff000000")

    def test_generated_driver_calls_native_functions_and_checks_real_memory(self):
        for target, prefix in (("intel", "intel"), ("openpower", "power"), ("arm", "arm"), ("riscv", "riscv")):
            with self.subTest(target=target):
                generated = native_suite.source(self.functions, self.inputs, target)
                self.assertIn(f"returned = {prefix}_add_f64x2(p0,p1)", generated)
                self.assertIn(f"{prefix}_storeu_f64x2(p0,p1);", generated)
                self.assertIn("memcpy(actual+0,buffer0,64);", generated)
                self.assertEqual(generated.count('emit("'), 762)

    def test_capture_load_rejects_changed_output_even_when_valid_json(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = self._capture(Path(temporary), "arm")
            self.assertEqual(len(native_suite.load_capture(directory)[1]), 762)
            path = directory / "outputs.jsonl"
            path.write_bytes(path.read_bytes().replace(b'"bytes":"00', b'"bytes":"01', 1))
            with self.assertRaisesRegex(ValueError, "hash mismatch"):
                native_suite.load_capture(directory)

    def test_intel_driver_declarations_match_intrinsic_wrapper_types(self):
        generated = native_suite.source(self.functions, self.inputs, "intel")
        for alias in ("i8x16", "u8x16", "i16x8", "u16x8", "i32x4", "u32x4", "i64x2", "u64x2"):
            self.assertIn(f"typedef __m128i {alias};", generated)
        self.assertIn("typedef __m128 f32x4;", generated)
        self.assertIn("typedef __m128d f64x2;", generated)
        self.assertNotIn("vector_size", generated)

    def test_capture_load_rejects_missing_duplicate_and_wrong_size_samples(self):
        mutations = (
            lambda rows: rows.pop(),
            lambda rows: rows.__setitem__(1, copy.deepcopy(rows[0])),
            lambda rows: rows[0].__setitem__("bytes", "00"),
        )
        for mutate in mutations:
            with self.subTest(mutate=mutate), tempfile.TemporaryDirectory() as temporary:
                directory = self._capture(Path(temporary), "arm", mutate=mutate)
                with self.assertRaises(ValueError):
                    native_suite.load_capture(directory)

    def test_comparison_retains_exact_native_byte_mismatch_and_fails(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            reference = self._capture(root, "intel")
            observed = self._capture(root, "arm", mutate=lambda rows: rows[0].__setitem__(
                "bytes", "ff" + rows[0]["bytes"][2:]))
            output = root / "comparison.json"
            with contextlib.redirect_stdout(io.StringIO()):
                result = native_suite.compare(argparse.Namespace(reference=reference, observed=observed, output=output))
            report = json.loads(output.read_text())
            self.assertEqual(result, 1)
            self.assertEqual(report["matched"], 761)
            self.assertEqual(report["mismatch_count"], 1)
            self.assertEqual(report["mismatches"][0], {
                "sample": "add_f32x4#0", "expected_intel": "00" * 16, "actual": "ff" + "00" * 15})
            self.assertFalse(report["physical_hardware_evidence"])

    def test_comparison_rejects_different_checkout_before_writing_report(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            reference = self._capture(root, "intel")
            observed = self._capture(root, "riscv")
            path = observed / "manifest.json"
            manifest = json.loads(path.read_text())
            manifest["source_sha256"]["10_official_suite/riscv/f64x2.c"] = "1" * 64
            path.write_bytes(native_suite.encoded(manifest))
            output = root / "comparison.json"
            with self.assertRaisesRegex(ValueError, "different source checkouts"):
                native_suite.compare(argparse.Namespace(reference=reference, observed=observed, output=output))
            self.assertFalse(output.exists())

    def test_failed_comparison_invalidates_older_success_report(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            output = root / "comparison.json"
            output.write_text('{"status":"pass"}')
            with self.assertRaises(FileNotFoundError):
                native_suite.compare(argparse.Namespace(reference=root / "missing", observed=root / "arm", output=output))
            self.assertFalse(output.exists())

    def test_wrong_platform_capture_invalidates_older_success_manifest(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            directory = self._capture(root, "intel")
            with patch.object(native_suite.platform, "machine", return_value="aarch64"):
                with self.assertRaisesRegex(ValueError, "requires"):
                    native_suite.capture(argparse.Namespace(target="intel", output=directory))
            self.assertFalse((directory / "manifest.json").exists())

    def test_unaligned_operations_receive_unaligned_addresses(self):
        by_name = {sample["sample"]: sample for sample in self.inputs["samples"]}
        self.assertEqual(by_name["loadu_f64x2#1"]["arguments"][0]["offset"], 17)
        self.assertEqual(by_name["storeu_i32x4#2"]["arguments"][0]["offset"], 17)
        aligned = [sample for sample in self.inputs["samples"] if sample["operation"].startswith("load_")]
        self.assertTrue(all(argument["offset"] % 16 == 0 for sample in aligned for argument in sample["arguments"] if "offset" in argument))

    def test_malformed_or_missing_source_provenance_is_an_execution_error(self):
        for value in ([], {"source_sha256": None}, {"source_sha256": {}}):
            with self.subTest(value=value), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                reference = self._capture(root, "intel")
                observed = self._capture(root, "arm")
                marker = reference / "manifest.json"
                original = json.loads(marker.read_bytes())
                marker.write_bytes(native_suite.encoded(value if isinstance(value, list) else {**original, **value}))
                with contextlib.redirect_stderr(io.StringIO()):
                    code = native_suite.main(["compare", "--reference", str(reference), "--observed", str(observed), "--output", str(root / "compare.json")])
                self.assertEqual(code, 2)

    def test_combined_report_retains_errors_and_escapes_untrusted_text(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._capture(root, "intel")
            for target in ("openpower", "arm", "riscv"):
                self._capture(root, target)
            (root / "launcher-errors").mkdir()
            (root / "launcher-errors" / "test.json").write_text(json.dumps({"error": "<script>alert(1)</script>"}))
            with contextlib.redirect_stdout(io.StringIO()):
                code = native_suite.report_pipeline(argparse.Namespace(directory=root, output=None, canonical=False, tests=False))
            self.assertEqual(code, 2)
            summary = json.loads((root / "pipeline-summary.json").read_bytes())
            self.assertEqual(len(summary["wrapper_comparisons"]), 3)
            body = (root / "pipeline-report.html").read_text()
            self.assertNotIn("<script>", body)
            self.assertIn("&lt;script&gt;", body)

    def test_report_marks_partial_capture_as_error_instead_of_pass(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._capture(root, "intel")
            self._capture(root, "arm")
            with contextlib.redirect_stdout(io.StringIO()):
                code = native_suite.report_pipeline(argparse.Namespace(directory=root, output=None, canonical=True, tests=True))
            self.assertEqual(code, 2)
            summary = json.loads((root / "pipeline-summary.json").read_bytes())
            self.assertEqual(summary["status"], "error")
            self.assertEqual(sum(row["status"] == "error" for row in summary["wrapper_comparisons"]), 2)

    def test_native_gate_runs_codegen_checks_after_ctest(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            calls = []
            with patch.object(native_suite.platform, "machine", return_value="x86_64"), \
                 patch.object(native_suite, "command_run", side_effect=lambda command, log: calls.append(command) or ""), \
                 contextlib.redirect_stdout(io.StringIO()):
                code = native_suite.verify_native(argparse.Namespace(target="intel", output=root, jobs=2))
            self.assertEqual(code, 0)
            self.assertEqual(calls[-2][0], "ctest")
            self.assertIn("test_native_codegen.py", calls[-1])

    def test_report_reauthenticates_canonical_evidence_and_rejects_changed_counts(self):
        counts = {"record_count": 1, "matched_inputs": 1, "mismatched_inputs": 0,
                  "not_comparable_inputs": 0, "mismatch_atoms": 0}
        for invalid in (False, True):
            with self.subTest(invalid=invalid), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                for target in ("intel", "openpower", "arm", "riscv"):
                    self._capture(root, target)
                for target in ("openpower", "arm", "riscv"):
                    directory = root / "canonical" / f"compare-{target}"
                    directory.mkdir(parents=True)
                    summary = {"artifact_type": "ioitf.comparison-summary", "outcome": "pass", **counts}
                    if invalid:
                        summary.update(matched_inputs=0, mismatched_inputs=1, outcome="mismatch")
                    (directory / "summary.json").write_text(json.dumps(summary))
                with patch.object(native_suite, "authenticate_canonical_comparison", return_value=counts) as authenticate, \
                     contextlib.redirect_stdout(io.StringIO()):
                    code = native_suite.report_pipeline(argparse.Namespace(directory=root, output=None, canonical=True, tests=False))
                self.assertEqual(authenticate.call_count, 3)
                self.assertEqual(code, 2 if invalid else 0)


if __name__ == "__main__":
    unittest.main()
