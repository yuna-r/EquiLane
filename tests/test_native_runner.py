"""Native coordinator boundaries without attributing synthetic IPC to a CPU.

These tests exercise process death, deadlines and hostile worker/probe output.
The synthetic workers are protocol fixtures and never publish native artifacts.
"""

from __future__ import annotations

import copy
import ctypes as ct
import json
import multiprocessing as mp
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "src"))

from equilane import native
from equilane.canonical import dump_bytes, sha256_bytes
from equilane.cases import load_case_definitions, validate_case_definition
from equilane.errors import RunnerError, UnsupportedError, ValidationError
from equilane.generator import generate_artifact
from equilane.isa import load_isa_registry


def synthetic_worker(mode, case, records, role, connection):
    """Controlled IPC only; this function does not call a native implementation."""
    try:
        import resource
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
        if mode == "exit-before-start":
            os._exit(17)
        for record in records:
            connection.send(("started", record["input_id"]))
            if record["sequence"] == 1:
                if mode == "signal":
                    os.kill(os.getpid(), signal.SIGTERM)
                if mode == "timeout":
                    time.sleep(5)
                if mode == "wrong-identity":
                    bad = native._error(record, role, "runtime_error", "synthetic_response")
                    bad["input_id"] = "f" * 64
                    connection.send(("result", bad))
                    continue
            connection.send(("result", native._error(record, role, "runtime_error", "synthetic_response")))
    finally:
        connection.close()


class NativeIPCAndABITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.isa = load_isa_registry(PROJECT / "contracts/isa-registry.json")
        cls.cases = load_case_definitions(PROJECT / "10_official_suite/cases/add-f64x2.py", isa_registry=cls.isa)
        cls.case = next(iter(cls.cases))
        cls.temporary = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temporary.cleanup)
        cls.inputs = generate_artifact(
            cases=cls.cases, isa_registry=cls.isa,
            output=Path(cls.temporary.name) / "vectors", count_per_case=3, retain_records=True,
        )
        cls.records = cls.inputs.records[:3]

    def _execute(self, mode, timeout=1):
        before = {p.pid for p in mp.active_children()}
        progress = []
        with patch("equilane.native._batch", synthetic_worker):
            results = native._execute(mode, self.cases, self.records, "arm", timeout, 2, progress.append)
        self.assertEqual({p.pid for p in mp.active_children()} - before, set())
        self.assertEqual([r["input_id"] for r in results], [r["input_id"] for r in self.records])
        self.assertEqual(progress, [1, 2, 3])
        return results

    def test_ctypes_bridge_matches_the_public_lp64_c_abi(self):
        self.assertEqual(ct.sizeof(native.InputBytes), 16)
        self.assertEqual(ct.sizeof(native.ABIInput), 32)
        self.assertEqual(ct.sizeof(native.ABIOutput), 64)
        self.assertEqual(native.ABIInput.encoded_arguments.offset, 8)
        self.assertEqual(native.ABIInput.rounding_mode.offset, 24)
        self.assertEqual(native.ABIOutput.encoded_memory_effects.offset, 32)
        self.assertEqual(native.ABIOutput.normalized_fp_exceptions.offset, 56)

    def test_signal_is_attributed_to_active_input_and_remaining_inputs_restart(self):
        results = self._execute("signal")
        self.assertEqual(results[0]["status"], "signal")
        self.assertEqual(results[0]["error"], {"stage": "signal", "code": f"signal_{signal.SIGTERM}"})
        self.assertEqual([r["error"]["code"] for r in results[1:]], ["synthetic_response"] * 2)

    def test_deadline_kills_stuck_worker_and_does_not_discard_remaining_inputs(self):
        started = time.monotonic()
        results = self._execute("timeout", timeout=0.1)
        self.assertLess(time.monotonic() - started, 2)
        self.assertEqual(results[0]["status"], "runtime_error")
        self.assertEqual(results[0]["error"]["code"], "execution_timeout")
        self.assertEqual([r["error"]["code"] for r in results[1:]], ["synthetic_response"] * 2)

    def test_worker_exit_before_call_is_an_infrastructure_error_for_each_input(self):
        results = self._execute("exit-before-start")
        self.assertEqual([r["status"] for r in results], ["infrastructure_error"] * 3)
        self.assertEqual({r["error"]["code"] for r in results}, {"worker_exit"})

    def test_worker_cannot_replace_an_authenticated_input_identity(self):
        before = {p.pid for p in mp.active_children()}
        with patch("equilane.native._batch", synthetic_worker), self.assertRaisesRegex(RunnerError, "identity"):
            native._execute("wrong-identity", self.cases, self.records, "arm", 1, 2, None)
        self.assertEqual({p.pid for p in mp.active_children()} - before, set())

    def test_abi_transport_uses_canonical_payload_and_preserves_return_bits(self):
        expected = self.records[0]["operands"]["a"]
        captured = {}
        callback_type = ct.CFUNCTYPE(ct.c_int, ct.POINTER(native.ABIInput), ct.POINTER(native.ABIOutput))

        @callback_type
        def callback(argument_pointer, output_pointer):
            argument, output = argument_pointer.contents, output_pointer.contents
            raw = ct.string_at(argument.encoded_arguments.data, argument.encoded_arguments.size)
            captured["raw"] = raw
            captured["rounding"] = argument.rounding_mode
            value = dump_bytes(expected)
            ct.memmove(output.encoded_return_value, value, len(value))
            output.return_size = len(value)
            output.effects_size = 0
            output.status = 0
            return 0

        actual = native._call(callback, self.case, self.records[0], "arm")
        self.assertEqual(captured["raw"], dump_bytes({
            "operands": self.records[0]["operands"], "buffers": {}, "immediates": {},
        }))
        self.assertEqual(captured["rounding"], 0)
        self.assertEqual(actual["observed"], {"return": expected})

    def test_abi_cannot_claim_output_larger_than_supplied_capacity(self):
        callback_type = ct.CFUNCTYPE(ct.c_int, ct.POINTER(native.ABIInput), ct.POINTER(native.ABIOutput))

        @callback_type
        def callback(_argument_pointer, output_pointer):
            output = output_pointer.contents
            output.status = 0
            output.return_size = output.return_capacity + 1
            return 0

        with self.assertRaisesRegex(RunnerError, "capacity"):
            native._call(callback, self.case, self.records[0], "arm")

    def test_unknown_observed_fp_flag_cannot_be_published(self):
        data = copy.deepcopy(self.case.data)
        data["environment"]["observe_fp_exceptions"] = True
        data["regressions"] = {"fp-flags.v1": {
            "input_id": self.records[0]["input_id"],
            "expected_intel": {"status": "ok", "observed": {
                "return": self.records[0]["operands"]["a"], "fp_exceptions": [],
            }},
        }}
        case = validate_case_definition(data, isa_registry=self.isa)
        callback_type = ct.CFUNCTYPE(ct.c_int, ct.POINTER(native.ABIInput), ct.POINTER(native.ABIOutput))

        @callback_type
        def callback(_argument_pointer, output_pointer):
            output = output_pointer.contents
            value = dump_bytes(self.records[0]["operands"]["a"])
            ct.memmove(output.encoded_return_value, value, len(value))
            output.return_size = len(value)
            output.normalized_fp_exceptions = 32
            output.status = 0
            return 0

        with self.assertRaisesRegex(RunnerError, "exception bit"):
            native._call(callback, case, self.records[0], "arm")


class NativeEvidenceBoundaryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.isa = load_isa_registry(PROJECT / "contracts/isa-registry.json")
        cls.cases = load_case_definitions(PROJECT / "10_official_suite/cases/add-f64x2.py", isa_registry=cls.isa)

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def _evidence(self):
        library = self.root / "libsynthetic.so"
        library.write_bytes(b"synthetic build evidence only; not executable")
        source = self.root / "source.c"
        source.write_text("int synthetic;\n")
        evidence = {
            "schema_version": 1, "artifact_type": "equilane.native-build", "role": "arm",
            "library": str(library), "binary_sha256": sha256_bytes(library.read_bytes()),
            "source_files": {str(source): sha256_bytes(source.read_bytes())},
            "symbols": {case.id: case.data["arm"]["symbol"] for case in self.cases},
            "case_contract_sha256": {case.id: native._contract_digest(case, "arm") for case in self.cases},
            "case_operations": {case.id: case.source_path.stem for case in self.cases},
        }
        path = self.root / "build-evidence.json"
        path.write_bytes(dump_bytes(evidence, newline=True))
        return library, path, evidence

    def test_changed_library_or_source_cannot_reuse_build_evidence(self):
        library, _, evidence = self._evidence()
        library.write_bytes(library.read_bytes() + b"changed")
        with self.assertRaises(ValidationError):
            native._load_build(library, self.cases, "arm")
        library, _, evidence = self._evidence()
        Path(next(iter(evidence["source_files"]))).write_text("changed source\n")
        with self.assertRaisesRegex(ValidationError, "source snapshot"):
            native._load_build(library, self.cases, "arm")

    def test_same_symbol_with_changed_contract_cannot_reuse_build(self):
        library, _, _ = self._evidence()
        original = next(iter(self.cases))
        data = copy.deepcopy(original.data)
        data["description"] += " modified contract"
        changed = validate_case_definition(data, isa_registry=self.isa, source_path=original.source_path)
        from equilane.cases import CaseRegistry
        cases = CaseRegistry({changed.id: changed}, self.cases.sha256)
        with self.assertRaisesRegex(ValidationError, "contract"):
            native._load_build(library, cases, "arm")

    def test_non_object_or_incomplete_sidecar_is_a_validation_error(self):
        library, path, evidence = self._evidence()
        for value in ([], None, {"artifact_type": "equilane.native-build", "role": "arm"}):
            with self.subTest(value=value):
                path.write_bytes(dump_bytes(value, newline=True))
                with self.assertRaises(ValidationError):
                    native._load_build(library, self.cases, "arm")

    def test_preflight_process_timeout_and_failed_launch_are_runner_errors(self):
        errors = (
            subprocess.TimeoutExpired(["probe"], 30),
            OSError("probe interpreter unavailable"),
        )
        for error in errors:
            with self.subTest(error=error), patch("equilane.native.subprocess.run", side_effect=error), \
                 self.assertRaises(RunnerError):
                native._preflight(self.root / "library.so", self.root, 1)

    def test_malformed_successful_probe_is_a_runner_error(self):
        for stdout in ("not JSON", "[]", '{"status":"passed"}'):
            completed = subprocess.CompletedProcess([], 0, stdout, "")
            with self.subTest(stdout=stdout), patch("equilane.native.subprocess.run", return_value=completed), \
                 self.assertRaises(RunnerError):
                native._preflight(self.root / "library.so", self.root, 1)

    def test_failed_probe_keeps_unsupported_distinct_from_success(self):
        completed = subprocess.CompletedProcess([], 2, "", "missing vector instructions")
        with patch("equilane.native.subprocess.run", return_value=completed), self.assertRaises(UnsupportedError):
            native._preflight(self.root / "library.so", self.root, 1)


if __name__ == "__main__":
    unittest.main()
