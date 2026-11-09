"""Native replay keeps bundle authentication and executable-source boundaries."""

from __future__ import annotations

import copy
from dataclasses import replace
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "src"))

from equilane.artifacts import validate_input_artifact, validate_result_artifact
from equilane.canonical import atomic_write, dump_bytes, iter_canonical_jsonl, read_canonical_json, write_jsonl
from equilane.cases import CaseRegistry, load_case_definitions, validate_case_definition
from equilane.compare import compare_result_records
from equilane.errors import RunnerError, ValidationError
from equilane.fixture import run_fixture
from equilane.generator import generate_artifact
from equilane.isa import load_isa_registry
from equilane.native_replay import local_replay_cases, replay_native
from equilane.replay import load_failure_bundle
from equilane.report import write_failure_bundle
from equilane.targets import TARGETS


class NativeReplayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.isa = load_isa_registry(PROJECT / "contracts/isa-registry.json")
        cls.cases = load_case_definitions(
            PROJECT / "10_official_suite/cases/add-f64x2.py", isa_registry=cls.isa
        )

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        generated = generate_artifact(
            cases=self.cases, isa_registry=self.isa, output=self.root / "vectors", count_per_case=1,
        )
        inputs = validate_input_artifact(generated.manifest_path, self.cases, self.isa)
        intel = run_fixture(
            input_artifact=inputs, cases=self.cases, isa_registry=self.isa,
            role="intel", output=self.root / "intel",
        )
        power = run_fixture(
            input_artifact=inputs, cases=self.cases, isa_registry=self.isa,
            role="openpower", output=self.root / "power",
        )
        records = list(iter_canonical_jsonl(power.results_path))
        observed = records[0]["observed"]["return"]["lanes"]
        observed[0] = f"0x{int(observed[0], 16) ^ 1:016x}"
        count, length, digest = write_jsonl(power.results_path, records)
        manifest = read_canonical_json(power.manifest_path)
        manifest["results"] = {
            "byte_length": length, "file": power.results_path.name,
            "record_count": count, "sha256": digest,
        }
        atomic_write(power.manifest_path, dump_bytes(manifest, newline=True))
        native_intel = validate_result_artifact(intel.manifest_path, self.cases, self.isa, input_artifact=inputs)
        faulty_power = validate_result_artifact(power.manifest_path, self.cases, self.isa, input_artifact=inputs)
        record = inputs.records[0]
        comparison = compare_result_records(
            self.cases.get(str(record["case_id"])), record,
            native_intel.records[str(record["input_id"])], faulty_power.records[str(record["input_id"])],
        )
        self.bundle_path = write_failure_bundle(
            output=self.root / "comparison", input_artifact=inputs,
            intel_artifact=native_intel, openpower_artifact=faulty_power,
            cases=self.cases, isa_registry=self.isa, input_record=record, comparison=comparison,
        )
        self.bundle = load_failure_bundle(self.bundle_path)

    def test_replay_passes_authenticated_data_and_trusted_local_source_only(self):
        # A bundle may contain arbitrary extra files; none are executable input.
        (self.bundle.root / "model.py").write_text("raise AssertionError('bundle code executed')\n")
        result = SimpleNamespace(manifest_path=self.root / "native" / TARGETS["intel"].manifest_file)
        validated = SimpleNamespace(development_fixture=False, role="intel")
        with patch("equilane.native_replay._execute_native", return_value=result) as execute, \
             patch("equilane.native_replay.validate_result_artifact", return_value=validated) as validate:
            actual = replay_native(
                failure=self.bundle_path, role="intel", output=self.root / "native",
                local_cases=self.cases, library=self.root / "trusted" / "lib.so",
                compiler="clang-21", timeout=9.0, jobs=2,
            )
        self.assertIs(actual, result)
        kwargs = execute.call_args.kwargs
        self.assertEqual(kwargs["input_artifact"].sha256, self.bundle.input_artifact.sha256)
        self.assertEqual(kwargs["cases"].sha256, self.bundle.cases.sha256)
        selected = next(iter(kwargs["cases"]))
        self.assertEqual(selected.data, next(iter(self.bundle.cases)).data)
        self.assertEqual(selected.source_path, next(iter(self.cases)).source_path)
        self.assertEqual(kwargs["library"], self.root / "trusted" / "lib.so")
        self.assertEqual((kwargs["compiler"], kwargs["timeout"], kwargs["jobs"]), ("clang-21", 9.0, 2))
        self.assertEqual(validate.call_args.args[1].sha256, self.bundle.cases.sha256)

    def test_replay_rejects_a_runner_fixture_or_role_downgrade(self):
        result = SimpleNamespace(manifest_path=self.root / "native" / TARGETS["intel"].manifest_file)
        for validated in (
            SimpleNamespace(development_fixture=True, role="intel"),
            SimpleNamespace(development_fixture=False, role="arm"),
        ):
            with self.subTest(validated=validated), \
                 patch("equilane.native_replay._execute_native", return_value=result), \
                 patch("equilane.native_replay.validate_result_artifact", return_value=validated), \
                 self.assertRaises(RunnerError):
                replay_native(failure=self.bundle_path, role="intel", output=self.root / "native", local_cases=self.cases)

    def test_wrong_role_and_existing_completion_marker_do_not_execute(self):
        output = self.root / "native"
        with patch("equilane.native_replay._execute_native") as execute:
            with self.assertRaisesRegex(ValidationError, "role"):
                replay_native(failure=self.bundle_path, role="arm", output=output, local_cases=self.cases)
            self.assertFalse(output.exists())
            output.mkdir()
            (output / TARGETS["intel"].manifest_file).write_text("previous complete artifact")
            with self.assertRaisesRegex(ValidationError, "fresh directory"):
                replay_native(failure=self.bundle_path, role="intel", output=output, local_cases=self.cases)
            execute.assert_not_called()

    def test_contract_change_cannot_be_hidden_by_reusing_a_case_id(self):
        original = next(iter(self.cases))
        data = copy.deepcopy(original.data)
        data["description"] += " changed local contract"
        changed = validate_case_definition(data, isa_registry=self.isa, source_path=original.source_path)
        local = CaseRegistry({changed.id: changed}, self.cases.sha256)
        with patch("equilane.native_replay._execute_native") as execute, \
             self.assertRaisesRegex(ValidationError, "local contract differs"):
            replay_native(failure=self.bundle_path, role="intel", output=self.root / "native", local_cases=local)
        execute.assert_not_called()

    def test_old_optional_binding_projection_keeps_its_authenticated_contract(self):
        original = next(iter(self.bundle.cases))
        data = copy.deepcopy(original.data)
        data.pop("arm")
        data.pop("riscv")
        old_case = validate_case_definition(data, isa_registry=self.bundle.isa_registry)
        older = replace(self.bundle, cases=CaseRegistry({old_case.id: old_case}, "a" * 64))
        selected = local_replay_cases(older, self.cases)
        self.assertEqual(selected.sha256, older.cases.sha256)
        self.assertEqual(next(iter(selected)).data, data)
        self.assertEqual(next(iter(selected)).source_path, next(iter(self.cases)).source_path)


if __name__ == "__main__":
    unittest.main()
