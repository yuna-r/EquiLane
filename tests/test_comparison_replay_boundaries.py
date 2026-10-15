"""Authenticated stream, comparison lifecycle, and replay integrity regressions.

Small real artifacts exercise the public boundaries. Corrupted stream bytes are
deliberately rehashed so rejection must come from their contents, not a stale
checksum. Replay expectations refer to literal changed paths and values.
"""

from __future__ import annotations

import copy
from concurrent.futures import Future
from dataclasses import replace
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "src"))

from equilane.artifacts import validate_input_artifact, validate_result_artifact
from equilane.canonical import dump_bytes, iter_canonical_jsonl, read_canonical_json, sha256_bytes, write_jsonl
from equilane.cases import CaseRegistry, load_case_definitions, validate_case_definition
from equilane.compare import ComparisonResult, compare_float_bits, compare_result_record_set, compare_result_records
from equilane.comparison import compare_artifacts, load_comparison_context
from equilane.errors import RunnerError, UnsupportedError, ValidationError
from equilane.fixture import run_fixture
from equilane.generator import generate_artifact
from equilane.isa import load_isa_registry
from equilane.records import STATUS_STAGE, derive_input_id
from equilane.replay import load_failure_bundle, verify_replay_artifacts
from equilane.report import write_failure_bundle


def _write_value(path, value):
    path.write_bytes(dump_bytes(value, newline=True))


def _set_status(record, status, code="fixture.failure"):
    record["status"] = status
    record.pop("observed", None)
    record["error"] = {"code": code, "stage": STATUS_STAGE[status]}


def _flip_lane(record, lane=0, bit=1):
    lanes = record["observed"]["return"]["lanes"]
    lanes[lane] = f"0x{int(lanes[lane], 16) ^ bit:016x}"


class ComparisonReplayBoundaries(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.template = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.template.cleanup)
        cls.template_root = Path(cls.template.name)
        cls.isa = load_isa_registry(PROJECT / "contracts/isa-registry.json")
        cls.cases = load_case_definitions(PROJECT / "10_official_suite/cases/add-f64x2.py", isa_registry=cls.isa)
        generated = generate_artifact(cases=cls.cases, isa_registry=cls.isa,
                                      output=cls.template_root / "vectors", count_per_case=3)
        inputs = validate_input_artifact(generated.manifest_path, cls.cases, cls.isa)
        for role in ("intel", "openpower"):
            run_fixture(input_artifact=inputs, cases=cls.cases, isa_registry=cls.isa,
                        role=role, output=cls.template_root / role)
        shutil.copytree(cls.template_root / "openpower", cls.template_root / "faulty-power")
        faulty_manifest = cls.template_root / "faulty-power/power-results.manifest.json"
        cls.rewrite(faulty_manifest, lambda rows: _flip_lane(rows[1]))
        intel = validate_result_artifact(cls.template_root / "intel", cls.cases, cls.isa, input_artifact=inputs)
        power = validate_result_artifact(faulty_manifest, cls.cases, cls.isa, input_artifact=inputs)
        record = inputs.records[1]
        compared = compare_result_records(cls.cases.get(record["case_id"]), record,
                                          intel.records[record["input_id"]], power.records[record["input_id"]])
        cls.bundle_relative = write_failure_bundle(
            output=cls.template_root / "comparison", input_artifact=inputs,
            intel_artifact=intel, openpower_artifact=power, cases=cls.cases,
            isa_registry=cls.isa, input_record=record, comparison=compared,
        ).relative_to(cls.template_root)

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / "copy"
        shutil.copytree(self.template_root, self.root)

    @staticmethod
    def rewrite(manifest_path, mutation):
        manifest = read_canonical_json(manifest_path)
        metadata = manifest["results"]
        data_path = manifest_path.parent / metadata["file"]
        records = list(iter_canonical_jsonl(data_path))
        mutation(records)
        count, length, digest = write_jsonl(data_path, records)
        metadata.update(record_count=count, byte_length=length, sha256=digest)
        _write_value(manifest_path, manifest)

    def input(self):
        return validate_input_artifact(self.root / "vectors", self.cases, self.isa)

    def result(self, role="intel", *, inputs=None):
        return validate_result_artifact(self.root / role, self.cases, self.isa,
                                        input_artifact=inputs if inputs is not None else self.input())

    def context(self):
        return load_comparison_context(cases=self.cases, isa_registry=self.isa,
                                       input_manifest=self.root / "vectors",
                                       intel_manifest=self.root / "intel",
                                       openpower_manifest=self.root / "openpower")

    @property
    def bundle_path(self):
        return self.root / self.bundle_relative

    def replay(self, bundle=None):
        bundle = bundle or load_failure_bundle(self.bundle_path)
        replay_root = self.root / "replay"
        shutil.copytree(bundle.root / "baseline", replay_root)
        intel = replay_root / "intel/intel-results.manifest.json"
        power = replay_root / "openpower/power-results.manifest.json"
        return bundle, intel, power

    def verify(self, bundle, intel, power):
        return verify_replay_artifacts(bundle, intel_manifest=intel, openpower_manifest=power)[0]

    def check_stream_corruption(self, scope, corruption):
        manifest_path = (self.root / "vectors/test-vectors.manifest.json" if scope == "input"
                         else self.root / "intel/intel-results.manifest.json")
        manifest = read_canonical_json(manifest_path)
        metadata = manifest["test_vectors" if scope == "input" else "results"]
        data_path = manifest_path.parent / metadata["file"]
        corrupted = corruption(data_path.read_bytes())
        data_path.write_bytes(corrupted)
        metadata.update(byte_length=len(corrupted), sha256=sha256_bytes(corrupted))
        _write_value(manifest_path, manifest)
        with self.assertRaises(ValidationError):
            self.input() if scope == "input" else self.result()

    def check_snapshot_mutation(self, mutation):
        captured = copy.deepcopy(self.input())
        captured = mutation(captured)
        with self.assertRaises(ValidationError):
            self.result(inputs=captured)

    def test_input_snapshot_survives_source_file_removal(self):
        captured = self.input()
        shutil.rmtree(self.root / "vectors")
        accepted = self.result(inputs=captured)
        self.assertEqual(accepted.record_count, 3)
        self.assertEqual(set(accepted.records), {row["input_id"] for row in captured.records})

    def test_result_validation_does_not_modify_the_captured_input(self):
        captured = self.input()
        original = copy.deepcopy(captured)
        self.result(inputs=captured)
        self.assertEqual(captured, original)

    def test_full_input_isa_hash_is_provenance_not_a_projection_gate(self):
        path = self.root / "vectors/test-vectors.manifest.json"
        manifest = read_canonical_json(path)
        manifest["isa_registry_sha256"] = "0" * 64
        _write_value(path, manifest)
        captured = self.input()
        self.assertFalse(captured.isa_registry_matches_local)
        self.assertEqual(self.result(inputs=captured).record_count, 3)

    def test_full_result_isa_hash_is_provenance_not_a_projection_gate(self):
        path = self.root / "intel/intel-results.manifest.json"
        manifest = read_canonical_json(path)
        manifest["isa_registry_sha256"] = "0" * 64
        _write_value(path, manifest)
        self.assertFalse(self.result().isa_registry_matches_local)

    def test_result_directory_rejects_ambiguous_role_completion_markers(self):
        shutil.copyfile(self.root / "openpower/power-results.manifest.json",
                        self.root / "intel/power-results.manifest.json")
        with self.assertRaisesRegex(ValidationError, "exactly one role"):
            self.result()

    def test_result_directory_rejects_missing_completion_marker(self):
        (self.root / "intel/intel-results.manifest.json").unlink()
        with self.assertRaisesRegex(ValidationError, "exactly one role"):
            self.result()

    def test_result_jsonl_argument_resolves_its_completion_marker(self):
        accepted = validate_result_artifact(self.root / "intel/intel-results.jsonl", self.cases,
                                            self.isa, input_artifact=self.input())
        self.assertEqual((accepted.role, accepted.record_count), ("intel", 3))

    def test_input_jsonl_argument_resolves_its_completion_marker(self):
        accepted = validate_input_artifact(self.root / "vectors/test-vectors.jsonl", self.cases, self.isa)
        self.assertEqual(accepted.sha256, self.input().sha256)

    def test_result_cannot_use_another_roles_manifest_filename(self):
        source = self.root / "intel/intel-results.manifest.json"
        wrong = source.with_name("power-results.manifest.json")
        shutil.copyfile(source, wrong)
        with self.assertRaisesRegex(ValidationError, "requires manifest name"):
            validate_result_artifact(wrong, self.cases, self.isa, input_artifact=self.input())

    def test_reversed_results_are_compared_by_identity_in_input_order(self):
        self.rewrite(self.root / "intel/intel-results.manifest.json", lambda rows: rows.reverse())
        self.rewrite(self.root / "openpower/power-results.manifest.json", lambda rows: _flip_lane(rows[2], lane=1))
        context = self.context()
        progress = []
        comparisons = compare_result_record_set(cases=self.cases, input_records=context.input_artifact.records,
                                                intel_records=context.intel_artifact.records,
                                                openpower_records=context.openpower_artifact.records,
                                                progress=progress.append)
        self.assertEqual([item.outcome for item in comparisons], ["match", "match", "mismatch"])
        self.assertEqual(comparisons[2].first_difference["lane"], 1)
        self.assertEqual(progress, [1, 2, 3])

    def test_parallel_batches_restore_input_order_after_reverse_completion(self):
        context = self.context()
        first_case = self.cases.get(self.cases.ids[0])
        other_data = copy.deepcopy(first_case.data)
        other_data["id"] = "sse2.other.f64x2.default"
        other_case = validate_case_definition(other_data, isa_registry=self.isa)
        cases = CaseRegistry({first_case.id: first_case, other_case.id: other_case}, "unused")
        records = [copy.deepcopy(row) for row in context.input_artifact.records]
        records[2]["case_id"] = other_case.id
        records[2]["input_id"] = derive_input_id(records[2])
        tail = copy.deepcopy(records[0])
        tail["operands"]["a"]["lanes"][0] = "0x3ff0000000000000"
        tail["input_id"] = derive_input_id(tail)
        records.append(tail)
        intel, power = {}, {}
        for index, record in enumerate(records):
            for role, output in (("intel", intel), ("openpower", power)):
                result = copy.deepcopy(context.intel_artifact.ordered_records[0])
                result.update(runner=role, case_id=record["case_id"], input_id=record["input_id"])
                if role == "openpower" and index == 2:
                    _flip_lane(result)
                output[record["input_id"]] = result
        submitted = []
        class ImmediateExecutor:
            def __init__(self, max_workers):
                submitted.append(max_workers)
            def __enter__(self):
                return self
            def __exit__(self, *args):
                return False
            def submit(self, action, work):
                future = Future()
                future.set_result(action(work))
                return future
        progress = []
        with patch("equilane.compare.ProcessPoolExecutor", ImmediateExecutor), \
                patch("equilane.compare.as_completed", lambda futures: reversed(list(futures))):
            compared = compare_result_record_set(cases=cases, input_records=tuple(records),
                                                 intel_records=intel, openpower_records=power,
                                                 jobs=20, progress=progress.append)
        self.assertEqual(submitted, [3])
        self.assertEqual(progress, [1, 2, 4])
        self.assertEqual([item.outcome for item in compared], ["match", "match", "mismatch", "match"])

    def test_empty_record_set_is_valid_without_progress_notifications(self):
        progress = []
        self.assertEqual(compare_result_record_set(cases=self.cases, input_records=(), intel_records={},
                                                   openpower_records={}, jobs=4, progress=progress.append), [])
        self.assertEqual(progress, [])

    def test_zero_workers_are_rejected_before_any_comparison(self):
        with self.assertRaisesRegex(ValidationError, "at least 1"):
            compare_result_record_set(cases=self.cases, input_records=(), intel_records={},
                                      openpower_records={}, jobs=0)

    def test_negative_workers_are_rejected_before_any_comparison(self):
        with self.assertRaisesRegex(ValidationError, "at least 1"):
            compare_result_record_set(cases=self.cases, input_records=(), intel_records={},
                                      openpower_records={}, jobs=-1)

    def test_corrupt_intel_result_is_classified_as_runner_error(self):
        (self.root / "intel/intel-results.jsonl").unlink()
        with self.assertRaisesRegex(RunnerError, "invalid Intel result"):
            self.context()

    def test_corrupt_target_result_is_classified_as_runner_error(self):
        (self.root / "openpower/power-results.jsonl").unlink()
        with self.assertRaisesRegex(RunnerError, "invalid OpenPOWER result"):
            self.context()

    def test_corrupt_inputs_keep_the_specification_error_classification(self):
        (self.root / "vectors/test-vectors.jsonl").unlink()
        with self.assertRaises(ValidationError) as caught:
            self.context()
        self.assertNotIsInstance(caught.exception, RunnerError)

    def test_development_comparison_without_opt_in_writes_no_completion_marker(self):
        output = self.root / "new-comparison"
        with self.assertRaises(UnsupportedError):
            compare_artifacts(self.context(), output=output)
        self.assertFalse(output.exists())

    def test_completed_output_cannot_be_overwritten(self):
        context = self.context()
        output = self.root / "new-comparison"
        run = compare_artifacts(context, output=output, allow_development_fixtures=True)
        original = run.files.summary_path.read_bytes()
        with self.assertRaisesRegex(ValidationError, "already contains summary.json"):
            compare_artifacts(context, output=output, allow_development_fixtures=True)
        self.assertEqual(run.files.summary_path.read_bytes(), original)

    def test_reference_error_marker_reserves_comparison_output(self):
        output = self.root / "new-comparison"
        output.mkdir()
        (output / "reference-error.json").write_bytes(b"reserved\n")
        with self.assertRaisesRegex(ValidationError, "reference-error.json"):
            compare_artifacts(self.context(), output=output, allow_development_fixtures=True)
        self.assertFalse((output / "summary.json").exists())

    def test_incomplete_report_directory_can_be_completed(self):
        output = self.root / "new-comparison"
        output.mkdir()
        (output / "junit.xml").write_bytes(b"interrupted")
        run = compare_artifacts(self.context(), output=output, allow_development_fixtures=True)
        self.assertEqual((run.exit_code, run.record_count), (0, 3))
        self.assertTrue(run.files.junit_path.read_bytes().startswith(b"<?xml"))

    def test_swapped_comparison_roles_are_rejected_before_writing(self):
        context = self.context()
        swapped = replace(context, intel_artifact=context.openpower_artifact,
                          openpower_artifact=context.intel_artifact)
        output = self.root / "new-comparison"
        with self.assertRaisesRegex(ValidationError, "roles do not match"):
            compare_artifacts(swapped, output=output, allow_development_fixtures=True)
        self.assertFalse(output.exists())

    def check_coordinator_status(self, pairs, expected_exit, mismatches, not_comparable):
        for role, column in (("intel", 0), ("openpower", 1)):
            path = self.root / role / ("intel-results.manifest.json" if role == "intel" else "power-results.manifest.json")
            def mutate(rows):
                for index, pair in enumerate(pairs):
                    if pair[column] != "ok":
                        _set_status(rows[index], pair[column])
            self.rewrite(path, mutate)
        output = self.root / "new-comparison"
        run = compare_artifacts(self.context(), output=output, allow_development_fixtures=True)
        self.assertEqual((run.exit_code, run.mismatched_inputs, run.not_comparable_inputs),
                         (expected_exit, mismatches, not_comparable))
        summary = read_canonical_json(run.files.summary_path)
        self.assertEqual((summary["record_count"], summary["mismatched_inputs"], summary["not_comparable_inputs"]),
                         (3, mismatches, not_comparable))
        self.assertEqual(len(summary["failure_bundles"]), mismatches)
        for relative in summary["failure_bundles"]:
            loaded = load_failure_bundle(output / relative)
            self.assertEqual(loaded.failure["first_difference"]["kind"], "status")

    def test_bundle_projects_a_nonfirst_input_without_changing_its_identity(self):
        original = self.input().records[1]
        bundled = load_failure_bundle(self.bundle_path)
        self.assertEqual(original["sequence"], 2)
        self.assertEqual(bundled.input_artifact.records[0]["sequence"], 1)
        self.assertEqual(bundled.input_artifact.records[0]["input_id"], original["input_id"])
        self.assertEqual(bundled.input_artifact.record_count, 1)

    def test_bundle_remains_self_contained_after_source_artifacts_are_deleted(self):
        for directory in ("vectors", "intel", "openpower", "faulty-power"):
            shutil.rmtree(self.root / directory)
        bundled = load_failure_bundle(self.bundle_path)
        self.assertEqual(bundled.failure["mismatch_count"], 1)
        self.assertEqual(bundled.cases.ids, self.cases.ids)

    def test_bundle_publishing_uses_captured_records_after_sources_are_deleted(self):
        context = self.context()
        input_record = context.input_artifact.records[1]
        power = copy.deepcopy(context.openpower_artifact)
        _flip_lane(power.records[input_record["input_id"]])
        compared = compare_result_records(self.cases.get(input_record["case_id"]), input_record,
                                          context.intel_artifact.records[input_record["input_id"]],
                                          power.records[input_record["input_id"]])
        for directory in ("vectors", "intel", "openpower"):
            shutil.rmtree(self.root / directory)
        bundle = write_failure_bundle(output=self.root / "captured", input_artifact=context.input_artifact,
                                     intel_artifact=context.intel_artifact, openpower_artifact=power,
                                     cases=self.cases, isa_registry=self.isa,
                                     input_record=input_record, comparison=compared)
        self.assertEqual(load_failure_bundle(bundle).failure["mismatch_count"], 1)

    def test_bundle_refuses_to_overwrite_a_completed_failure(self):
        context = self.context()
        faulty = validate_result_artifact(self.root / "faulty-power", self.cases, self.isa,
                                          input_artifact=context.input_artifact)
        record = context.input_artifact.records[1]
        compared = compare_result_records(self.cases.get(record["case_id"]), record,
                                          context.intel_artifact.records[record["input_id"]], faulty.records[record["input_id"]])
        marker = self.bundle_path / "failure.json"
        before = marker.read_bytes()
        with self.assertRaisesRegex(ValidationError, "existing failure bundle"):
            write_failure_bundle(output=self.root / "comparison", input_artifact=context.input_artifact,
                                 intel_artifact=context.intel_artifact, openpower_artifact=faulty,
                                 cases=self.cases, isa_registry=self.isa,
                                 input_record=record, comparison=compared)
        self.assertEqual(marker.read_bytes(), before)

    def check_bundle_comparison_metadata(self, field):
        context = self.context()
        faulty = validate_result_artifact(self.root / "faulty-power", self.cases, self.isa,
                                          input_artifact=context.input_artifact)
        record = context.input_artifact.records[1]
        actual = compare_result_records(self.cases.get(record["case_id"]), record,
                                         context.intel_artifact.records[record["input_id"]],
                                         faulty.records[record["input_id"]])
        if field == "count":
            supplied = replace(actual, mismatch_count=2)
        elif field == "bool_count":
            supplied = replace(actual, mismatch_count=True)
        else:
            first = copy.deepcopy(actual.first_difference)
            first["lane"] = False if field == "bool_lane" else 1
            supplied = replace(actual, first_difference=first)
        output = self.root / "invalid-bundle"
        with self.assertRaisesRegex(ValidationError, "comparison.*baselines"):
            write_failure_bundle(output=output, input_artifact=context.input_artifact,
                                 intel_artifact=context.intel_artifact, openpower_artifact=faulty,
                                 cases=self.cases, isa_registry=self.isa,
                                 input_record=record, comparison=supplied)
        self.assertEqual(list((output / "failures").iterdir()), [])

    def test_bundle_rejects_incorrect_mismatch_atom_count_before_publishing(self):
        self.check_bundle_comparison_metadata("count")

    def test_bundle_rejects_incorrect_first_difference_before_publishing(self):
        self.check_bundle_comparison_metadata("first")

    def test_bundle_rejects_boolean_atom_count_before_publishing(self):
        self.check_bundle_comparison_metadata("bool_count")

    def test_bundle_rejects_boolean_lane_before_publishing(self):
        self.check_bundle_comparison_metadata("bool_lane")

    def test_bundle_loader_rejects_boolean_lane_that_compares_equal_to_zero(self):
        marker = self.bundle_path / "failure.json"
        failure = read_canonical_json(marker)
        self.assertEqual(failure["first_difference"]["lane"], 0)
        failure["first_difference"]["lane"] = False
        _write_value(marker, failure)
        with self.assertRaisesRegex(ValidationError, "first_difference"):
            load_failure_bundle(self.bundle_path)

    def test_failed_bundle_write_removes_temporary_directory_and_marker(self):
        context = self.context()
        record = context.input_artifact.records[0]
        output = self.root / "interrupted-bundle"
        compared = ComparisonResult("mismatch", 1, {"kind": "return", "lane": 0,
                                                     "intel": "0x0000000000000000", "openpower": "0x0000000000000001"})
        with patch("equilane.report.atomic_write", side_effect=OSError("simulated storage failure")):
            with self.assertRaisesRegex(OSError, "simulated storage failure"):
                write_failure_bundle(output=output, input_artifact=context.input_artifact,
                                     intel_artifact=context.intel_artifact, openpower_artifact=context.openpower_artifact,
                                     cases=self.cases, isa_registry=self.isa, input_record=record, comparison=compared)
        self.assertEqual(list((output / "failures").iterdir()), [])

    def test_replay_reproduces_unchanged_authenticated_baselines(self):
        bundle, intel, power = self.replay()
        verification = self.verify(bundle, intel, power)
        self.assertTrue(verification.reproduced)
        self.assertEqual(verification.result_differences, [])
        self.assertEqual(verification.replay_comparison["mismatch_count"], 1)

    def test_replay_ignores_duration_even_at_safe_integer_limit(self):
        bundle, intel, power = self.replay()
        self.rewrite(intel, lambda rows: rows[0].__setitem__("duration_ns", 0))
        self.rewrite(power, lambda rows: rows[0].__setitem__("duration_ns", (1 << 53) - 1))
        self.assertTrue(self.verify(bundle, intel, power).reproduced)

    def test_replay_requires_baseline_values_even_if_same_mismatch_is_preserved(self):
        bundle, intel, power = self.replay()
        for path in (intel, power):
            self.rewrite(path, lambda rows: _flip_lane(rows[0], lane=1, bit=8))
        verification = self.verify(bundle, intel, power)
        self.assertFalse(verification.reproduced)
        self.assertEqual(verification.replay_comparison["first_difference"], bundle.failure["first_difference"])
        self.assertEqual(verification.replay_comparison["mismatch_count"], 1)
        self.assertEqual([row["role"] for row in verification.result_differences], ["intel", "openpower"])
        self.assertEqual(verification.result_differences[0]["difference"]["path"], "observed.return.lanes[1]")

    def test_replay_fixed_target_value_reports_match_without_claiming_reproduction(self):
        bundle, intel, power = self.replay()
        expected = copy.deepcopy(bundle.baseline_intel.records[bundle.failure["input_id"]]["observed"])
        self.rewrite(power, lambda rows: rows[0].__setitem__("observed", expected))
        verification = self.verify(bundle, intel, power)
        self.assertFalse(verification.reproduced)
        self.assertEqual(verification.replay_comparison,
                         {"first_difference": None, "mismatch_count": 0, "outcome": "match"})

    def test_replay_swapped_roles_are_invalid_even_when_each_artifact_is_valid(self):
        bundle, intel, power = self.replay()
        with self.assertRaisesRegex(ValidationError, "roles do not match"):
            self.verify(bundle, power, intel)

    def test_replay_target_override_must_match_bundle(self):
        bundle, intel, power = self.replay()
        with self.assertRaisesRegex(ValidationError, "target does not match"):
            verify_replay_artifacts(bundle, intel_manifest=intel, openpower_manifest=power, target_role="arm")

    def test_replay_uses_loaded_bundle_snapshot_after_original_baselines_disappear(self):
        bundle, intel, power = self.replay()
        shutil.rmtree(self.bundle_path)
        verification = self.verify(bundle, intel, power)
        self.assertTrue(verification.reproduced)

    def test_replay_build_and_link_changes_are_diagnostics_with_precise_paths(self):
        bundle, intel, power = self.replay()
        manifest = read_canonical_json(power)
        environment = manifest["environment"]
        environment["build_units"][0]["compiler"]["version"] = "different-compiler-version"
        environment["link"]["link_options"] = ["-Wl,--as-needed"]
        environment["link"]["binary_sha256"] = "a" * 64
        _write_value(power, manifest)
        verification = self.verify(bundle, intel, power)
        self.assertTrue(verification.reproduced)
        self.assertEqual(verification.environment_differences["openpower"], [
            "environment.build_units[0].compiler.version", "environment.link.binary_sha256",
            "environment.link.link_options.length",
        ])

    def runtime_bundle(self):
        self.rewrite(self.root / "openpower/power-results.manifest.json",
                     lambda rows: _set_status(rows[0], "runtime_error"))
        output = self.root / "runtime-comparison"
        run = compare_artifacts(self.context(), output=output, allow_development_fixtures=True)
        summary = read_canonical_json(run.files.summary_path)
        return load_failure_bundle(output / summary["failure_bundles"][0])

    def test_replay_changed_error_code_fails_even_if_status_mismatch_is_identical(self):
        bundle, intel, power = self.replay(self.runtime_bundle())
        self.rewrite(power, lambda rows: rows[0]["error"].__setitem__("code", "fixture.other-failure"))
        verification = self.verify(bundle, intel, power)
        self.assertFalse(verification.reproduced)
        self.assertEqual(verification.replay_comparison["first_difference"], bundle.failure["first_difference"])
        self.assertEqual(verification.result_differences, [{"role": "openpower", "difference": {
            "path": "error.code", "baseline": "fixture.failure", "replay": "fixture.other-failure",
        }}])

    def test_replay_equal_runtime_errors_are_not_comparable(self):
        bundle, intel, power = self.replay(self.runtime_bundle())
        self.rewrite(intel, lambda rows: _set_status(rows[0], "runtime_error"))
        verification = self.verify(bundle, intel, power)
        self.assertFalse(verification.reproduced)
        self.assertEqual(verification.replay_comparison,
                         {"outcome": "not_comparable", "mismatch_count": 0, "first_difference": None})

    def test_replay_error_duration_is_ignored_while_stable_code_is_preserved(self):
        bundle, intel, power = self.replay(self.runtime_bundle())
        self.rewrite(power, lambda rows: rows[0].__setitem__("duration_ns", 1234567))
        verification = self.verify(bundle, intel, power)
        self.assertTrue(verification.reproduced)
        self.assertEqual(verification.result_differences, [])

    def check_missing_bundle_file(self, relative):
        (self.bundle_path / relative).unlink()
        with self.assertRaises(ValidationError):
            load_failure_bundle(self.bundle_path)

    def check_reproduction_command(self, role, mutation):
        marker = self.bundle_path / "failure.json"
        failure = read_canonical_json(marker)
        mutation(failure["reproduce"][role])
        _write_value(marker, failure)
        with self.assertRaisesRegex(ValidationError, "commands do not match"):
            load_failure_bundle(self.bundle_path)


def _install(name, action):
    def test(self):
        action(self)
    test.__name__ = "test_" + name
    setattr(ComparisonReplayBoundaries, test.__name__, test)


# Each encoding failure is rehashed and tested for both input and result data.
for _scope in ("input", "result"):
    for _name, _corruption in (
        ("truncated_final_record", lambda raw: raw[:-20]),
        ("missing_final_lf", lambda raw: raw[:-1]),
        ("empty_trailing_record", lambda raw: raw + b"\n"),
        ("empty_interior_record", lambda raw: raw.replace(b"\n", b"\n\n", 1)),
        ("crlf_records", lambda raw: raw.replace(b"\n", b"\r\n")),
        ("bom_before_record", lambda raw: b"\xef\xbb\xbf" + raw),
        ("invalid_utf8_inside_record", lambda raw: raw.replace(b'"schema_version"', b'"schema_\xffversion"', 1)),
        ("leading_record_whitespace", lambda raw: b" " + raw),
        ("json_array_instead_of_record", lambda raw: b"[]\n" + raw.split(b"\n", 1)[1]),
        ("duplicate_record_key", lambda raw: raw.replace(b'{"case_id":', b'{"schema_version":1,"case_id":', 1)),
    ):
        _install(f"rehashed_{_scope}_stream_{_name}",
                 lambda self, s=_scope, c=_corruption: self.check_stream_corruption(s, c))


def _mutate_manifest(artifact):
    artifact.manifest["profile"] = "invalid"
    return artifact


def _mutate_operand(artifact):
    artifact.records[0]["operands"]["a"]["lanes"][0] = "0x0123456789abcdef"
    return artifact


for _name, _mutation in (
    ("manifest", _mutate_manifest),
    ("captured_record", _mutate_operand),
    ("record_count", lambda a: replace(a, record_count=2)),
    ("data_digest", lambda a: replace(a, sha256="0" * 64)),
    ("case_ids", lambda a: replace(a, case_ids=())),
    ("case_projection", lambda a: replace(a, case_definitions_sha256="0" * 64)),
    ("used_isa_projection", lambda a: replace(a, used_isa_contract=replace(a.used_isa_contract, sha256="0" * 64))),
):
    _install(f"captured_input_rejects_mutated_{_name}",
             lambda self, m=_mutation: self.check_snapshot_mutation(m))

for _name, _pairs, _exit, _mismatches, _nc in (
    ("all_match", (), 0, 0, 0),
    ("runtime_status_mismatch", (("ok", "runtime_error"),), 1, 1, 0),
    ("unsupported_status_mismatch", (("ok", "unsupported"),), 3, 1, 0),
    ("both_runtime_error", (("runtime_error", "runtime_error"),), 4, 0, 1),
    ("both_unsupported", (("unsupported", "unsupported"),), 3, 0, 1),
    ("noncomparable_dominates_mismatch", (("runtime_error", "runtime_error"), ("ok", "signal")), 4, 1, 1),
    ("unsupported_dominates_noncomparable", (("signal", "signal"), ("ok", "unsupported")), 3, 1, 1),
    ("different_failures_are_status_mismatch", (("signal", "runtime_error"),), 1, 1, 0),
):
    _install(f"comparison_exit_{_name}", lambda self, p=_pairs, e=_exit, m=_mismatches, n=_nc:
             self.check_coordinator_status(p, e, m, n))

for _relative in (
    "failure.json", "test-vectors.manifest.json", "test-vectors.jsonl",
    "contracts/case-definitions.json", "contracts/isa-used.json",
    "baseline/intel/intel-results.manifest.json", "baseline/intel/intel-results.jsonl",
    "baseline/openpower/power-results.manifest.json", "baseline/openpower/power-results.jsonl",
):
    _install(f"bundle_missing_{_relative.replace('/', '_').replace('.', '_')}",
             lambda self, r=_relative: self.check_missing_bundle_file(r))

for _role in ("intel", "openpower", "verify"):
    for _name, _mutation in (
        ("extra_shell_token", lambda command: command.append("; touch /tmp/untrusted")),
        ("changed_executable", lambda command: command.__setitem__(0, "unexpected-tool")),
        ("missing_last_argument", lambda command: command.pop()),
    ):
        _install(f"bundle_replay_command_{_role}_{_name}",
                 lambda self, r=_role, m=_mutation: self.check_reproduction_command(r, m))


class MemoryComparisonBoundaries(unittest.TestCase):
    def setUp(self):
        self.case = validate_case_definition({
            "schema_version": 1, "id": "sse2.offsets.memory.default", "description": "Offset memory fixture",
            "tags": [], "intel": {"symbol": "intel_memory", "required_isa": ["sse2"]},
            "openpower": {"symbol": "power_memory", "required_isa": ["power8", "vsx"]},
            "arm": {"symbol": "arm_memory", "required_isa": ["neon"]},
            "signature": {"arguments": [{"name": "dst", "type": "pointer"}, {"name": "src", "type": "pointer"}],
                          "return": {"type": "scalar", "element": "u32"}},
            "memory_contract": {
                "dst": {"access": "write", "required_alignment": 1, "read_ranges": [],
                        "write_ranges": [{"offset": 1, "byte_length": 2}]},
                "src": {"access": "read", "required_alignment": 1,
                        "read_ranges": [{"offset": 0, "byte_length": 2}], "write_ranges": []},
            },
            "input_domain": {"exclude": []}, "comparison": {"mode": "bit_exact"},
            "environment": {"fp_rounding_modes": ["nearest_even"], "observe_fp_exceptions": False},
        })
        self.record = {
            "schema_version": 1, "case_id": self.case.id, "sequence": 1, "generation": {"class": "structured"},
            "environment": {"fp_mode": "ieee", "rounding": "nearest_even"},
            "operands": {"dst": {"buffer": "target", "offset": 2}, "src": {"buffer": "alpha", "offset": 1}},
            "buffers": {name: {"alignment": 1, "bytes": "0x0001020304050607"} for name in ("target", "zeta", "alpha")},
        }
        self.record["input_id"] = derive_input_id(self.record)

    def result(self, role, changes=None, returned="0x00000000"):
        buffers = {}
        for name in ("zeta", "target", "alpha"):
            content = bytearray(range(8))
            for offset, value in (changes or {}).get(name, {}).items():
                content[offset] = value
            buffers[name] = {"byte_offset": 0, "bytes": "0x" + content.hex()}
        observed = {"buffers": buffers}
        if self.case.signature["return"]["type"] != "void":
            observed["return"] = {"element": "u32", "bits": returned}
        return {"schema_version": 1, "case_id": self.case.id, "input_id": self.record["input_id"],
                "runner": role, "duration_ns": 0, "status": "ok", "observed": observed}

    def compare(self, intel=None, power=None, *, returned="0x00000000", target="openpower"):
        return compare_result_records(self.case, self.record, self.result("intel", intel),
                                      self.result(target, power, returned), target_role=target)

    def test_relative_write_range_applies_pointer_and_contract_offsets(self):
        changes = {"target": {3: 170, 4: 187}}
        compared = self.compare(changes, changes)
        self.assertEqual((compared.outcome, compared.mismatch_count), ("match", 0))

    def test_byte_immediately_before_write_range_is_a_contract_violation(self):
        compared = self.compare({"target": {2: 170}})
        self.assertEqual(compared.mismatch_count, 1)
        self.assertEqual(compared.first_difference, {"kind": "memory_contract", "runner": "intel",
                                                    "buffer": "target", "byte_offset": 2,
                                                    "before": "0x02", "after": "0xaa"})

    def test_half_open_write_range_excludes_its_end_even_if_both_runners_agree(self):
        changes = {"target": {5: 170}}
        compared = self.compare(changes, changes)
        self.assertEqual(compared.mismatch_count, 2)
        self.assertEqual(compared.first_difference["byte_offset"], 5)

    def test_read_only_source_cannot_change_even_with_equal_outputs(self):
        changes = {"alpha": {1: 170}}
        compared = self.compare(changes, changes)
        self.assertEqual(compared.mismatch_count, 2)
        self.assertEqual(compared.first_difference["buffer"], "alpha")

    def test_unreferenced_buffer_is_also_protected(self):
        compared = self.compare(power={"zeta": {7: 255}})
        self.assertEqual(compared.mismatch_count, 1)
        self.assertEqual(compared.first_difference["runner"], "openpower")
        self.assertEqual(compared.first_difference["buffer"], "zeta")

    def test_allowed_write_value_difference_counts_one_buffer_atom(self):
        compared = self.compare({"target": {3: 170}}, {"target": {3: 187}})
        self.assertEqual(compared.mismatch_count, 1)
        self.assertEqual(compared.first_difference, {"kind": "buffer", "buffer": "target", "byte_offset": 3,
                                                    "intel": "0xaa", "openpower": "0xbb"})

    def test_two_forbidden_writes_are_not_counted_again_as_buffer_difference(self):
        compared = self.compare({"target": {2: 170}}, {"target": {2: 187}})
        self.assertEqual(compared.mismatch_count, 2)
        self.assertEqual(compared.first_difference["kind"], "memory_contract")

    def test_memory_first_difference_prioritizes_runner_before_buffer_and_offset(self):
        compared = self.compare({"zeta": {7: 255}}, {"alpha": {0: 255}})
        self.assertEqual(compared.mismatch_count, 2)
        self.assertEqual((compared.first_difference["runner"], compared.first_difference["buffer"],
                          compared.first_difference["byte_offset"]), ("intel", "zeta", 7))

    def test_memory_first_difference_sorts_buffers_independently_of_insertion_order(self):
        compared = self.compare({"zeta": {0: 255}, "alpha": {6: 255, 1: 255}})
        self.assertEqual(compared.mismatch_count, 3)
        self.assertEqual((compared.first_difference["buffer"], compared.first_difference["byte_offset"]), ("alpha", 1))

    def test_scalar_return_precedes_allowed_buffer_difference_without_lane_field(self):
        compared = self.compare(power={"target": {3: 187}}, returned="0x00000001")
        self.assertEqual(compared.mismatch_count, 2)
        self.assertEqual(compared.first_difference, {"kind": "return", "intel": "0x00000000", "openpower": "0x00000001"})

    def test_void_return_still_compares_memory(self):
        data = copy.deepcopy(self.case.data)
        data["signature"]["return"] = {"type": "void"}
        self.case = validate_case_definition(data)
        compared = self.compare(power={"target": {4: 255}})
        self.assertEqual((compared.outcome, compared.mismatch_count), ("mismatch", 1))
        self.assertEqual(compared.first_difference["kind"], "buffer")

    def test_arm_memory_violation_uses_actual_runner_identity(self):
        compared = self.compare(power={"alpha": {0: 255}}, target="arm")
        self.assertEqual(compared.first_difference["runner"], "arm")
        self.assertNotIn("openpower", compared.first_difference)


class FloatClassificationBoundaries(unittest.TestCase):
    def check_pair(self, element, left, right, expected):
        policy = {"mode": "classification", "signed_zero": "equal",
                  "nan": {"both_nan": "equal", "payload": "ignore", "quiet_signaling": "ignore", "sign": "ignore"}}
        self.assertEqual(compare_float_bits(left, right, element=element, comparison=policy), expected)
        self.assertEqual(compare_float_bits(right, left, element=element, comparison=policy), expected)


for _element, _pairs in {
    "f32": (
        ("same_sign_subnormals", "0x00000001", "0x007fffff", True),
        ("opposite_sign_subnormals", "0x00000001", "0x80000001", False),
        ("subnormal_normal_transition", "0x007fffff", "0x00800000", False),
        ("same_sign_extreme_normals", "0x00800000", "0x7f7fffff", True),
        ("opposite_sign_normals", "0x3f800000", "0xbf800000", False),
        ("zero_subnormal_transition", "0x00000000", "0x00000001", False),
    ),
    "f64": (
        ("same_sign_subnormals", "0x0000000000000001", "0x000fffffffffffff", True),
        ("opposite_sign_subnormals", "0x0000000000000001", "0x8000000000000001", False),
        ("subnormal_normal_transition", "0x000fffffffffffff", "0x0010000000000000", False),
        ("same_sign_extreme_normals", "0x0010000000000000", "0x7fefffffffffffff", True),
        ("opposite_sign_normals", "0x3ff0000000000000", "0xbff0000000000000", False),
        ("zero_subnormal_transition", "0x0000000000000000", "0x0000000000000001", False),
    ),
}.items():
    for _name, _left, _right, _expected in _pairs:
        def _classification_test(self, e=_element, a=_left, b=_right, expected=_expected):
            self.check_pair(e, a, b, expected)
        setattr(FloatClassificationBoundaries, f"test_{_element}_{_name}", _classification_test)


if __name__ == "__main__":
    unittest.main()
