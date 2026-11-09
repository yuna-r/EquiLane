"""Trace verification uses synthetic metadata, never synthetic CPU evidence."""

from __future__ import annotations

import copy
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "src"))

from equilane.canonical import dump_bytes, sha256_bytes
from equilane.cases import load_case_definitions
from equilane.errors import ValidationError
from equilane.generator import generate_artifact
from equilane.isa import load_isa_registry
from equilane.native_traceability import _referenced_file, validate_traceability
from equilane.records import FP_EXCEPTION_ORDER
from equilane.targets import FP_CONTROL_KEYS, TARGETS


class NativeTraceabilityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.isa = load_isa_registry(PROJECT / "contracts/isa-registry.json")
        cls.cases = load_case_definitions(PROJECT / "10_official_suite/cases/add-f64x2.py", isa_registry=cls.isa)
        cls.case = next(iter(cls.cases))

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.inputs = generate_artifact(
            cases=self.cases, isa_registry=self.isa, output=self.root / "vectors", count_per_case=3, retain_records=True,
        )
        self.directory = self.root / "run"
        self.directory.mkdir()
        self.fixture()

    def fixture(self):
        build = self.directory / "build"
        build.mkdir(exist_ok=True)
        self.library = build / "libsynthetic.so"
        self.library.write_bytes(b"synthetic metadata fixture; not executable")
        self.object = build / "adapter.encoded.o"
        self.object.write_bytes(b"synthetic object identity")
        binary_hash = sha256_bytes(self.library.read_bytes())
        unit = {"id": "adapter.encoded", "object_sha256": sha256_bytes(self.object.read_bytes())}
        controls = {name: False for name in FP_CONTROL_KEYS["aarch64"]}
        roundings = ["nearest_even", "toward_negative", "toward_positive", "toward_zero"]
        libraries = [{"path": str(self.library), "sha256": binary_hash}]
        self.probes = {
            "status": "passed", "detector": "linux.auxv.asimd", "available_isa": ["neon"],
            "boolean_controls": controls, "rounding_modes": roundings,
            "fp_exception_flags": FP_EXCEPTION_ORDER, "loaded_libraries": libraries,
        }
        projection = {key: value for key, value in self.case.data.items() if key not in TARGETS or key == "arm"}
        contract_hash = sha256_bytes(dump_bytes(projection, newline=True))
        self.evidence = {
            "schema_version": 1, "artifact_type": "equilane.native-build", "role": "arm",
            "library": str(self.library), "binary_sha256": binary_hash, "git_commit": "a" * 40,
            "build_units": [unit], "link_options": ["-shared", "-lm"],
            "symbols": {self.case.id: self.case.data["arm"]["symbol"]},
            "source_files": {"/synthetic/source.c": "b" * 64}, "source_root": "/synthetic",
            "case_contract_sha256": {self.case.id: contract_hash},
            "case_operations": {self.case.id: self.case.source_path.stem},
            "symbol_audit": {"registered": 1, "missing": []},
        }
        self.manifest = {
            "environment": {
                "git_commit": "a" * 40, "available_isa": ["neon"], "fp_controls": controls,
                "fp_rounding_modes": ["nearest_even"], "build_units": [unit],
                "case_build_units": {self.case.id: ["adapter.encoded"]},
                "link": {"binary_sha256": binary_hash, "link_options": ["-shared", "-lm"], "loaded_libraries": libraries},
            },
            "preflight": {"probe_suite": [{"expected": {
                "boolean_controls": controls, "rounding_modes": roundings, "fp_exception_flags": FP_EXCEPTION_ORDER,
            }}]},
        }
        self.result = SimpleNamespace(role="arm", development_fixture=False, manifest=self.manifest, sha256="c" * 64)
        self.trace = {
            "schema_version": 1, "artifact_type": "equilane.native-traceability", "complete": True,
            "input_sha256": self.inputs.sha256, "result_sha256": self.result.sha256,
            "library_sha256": binary_hash, "source_commit": "a" * 40,
            "symbol_audit": {"registered": 1, "missing": []},
            "execution": {
                "mode": "native-local", "docker_image_id": None, "host_architecture": None, "qemu_cpu": None,
                "process_isolation": "case-worker-restarted-after-signal-or-timeout", "timeout_ms": 5000,
            },
            "cases": {self.case.id: {
                "symbol": self.case.data["arm"]["symbol"], "build_units": ["adapter.encoded"],
                "contract_sha256": contract_hash, "operation": self.case.source_path.stem,
                "inputs": len(self.inputs.records),
                "input_ids_sha256": sha256_bytes(dump_bytes([record["input_id"] for record in self.inputs.records])),
            }},
        }
        self.publish()

    def publish(self):
        for key, filename, data in (
            ("build_evidence", "native-build-evidence.json", self.evidence),
            ("preflight_observations", "preflight-observations.json", self.probes),
        ):
            blob = dump_bytes(data, newline=True)
            (self.directory / filename).write_bytes(blob)
            self.trace[key] = {"file": filename, "sha256": sha256_bytes(blob)}
        (self.directory / "native-traceability.json").write_bytes(dump_bytes(self.trace, newline=True))

    def validate(self):
        # Metadata consistency is the unit under test. Real native artifact and
        # ABI execution checks run separately; this mock never writes a result.
        with patch("equilane.native_traceability.validate_result_artifact", return_value=self.result):
            return validate_traceability(
                self.directory, input_artifact=self.inputs, cases=self.cases, isa_registry=self.isa, role="arm",
            )

    def test_consistent_trace_authenticates_binary_objects_and_case_input_links(self):
        self.assertEqual(self.validate(), self.trace)

    def test_edited_binary_or_object_file_cannot_retain_verified_trace(self):
        for filename in ("library", "object"):
            with self.subTest(filename=filename):
                self.fixture()
                path = getattr(self, filename)
                path.write_bytes(path.read_bytes() + b"changed")
                with self.assertRaisesRegex(ValidationError, "bytes"):
                    self.validate()

    def test_case_contract_symbol_operation_count_and_input_order_are_bound(self):
        mutations = {
            "contract_sha256": "d" * 64, "symbol": "arm_other_symbol", "operation": "sub-f64x2",
            "inputs": True, "input_ids_sha256": "e" * 64, "build_units": [],
        }
        for field, value in mutations.items():
            with self.subTest(field=field):
                self.fixture()
                self.trace["cases"][self.case.id][field] = value
                self.publish()
                with self.assertRaises(ValidationError):
                    self.validate()

    def test_sidecar_metadata_cannot_replace_manifest_build_or_probe_measurements(self):
        self.evidence["build_units"] = copy.deepcopy(self.evidence["build_units"])
        self.evidence["build_units"][0]["object_sha256"] = "d" * 64
        self.publish()
        with self.assertRaisesRegex(ValidationError, "build_units"):
            self.validate()
        self.fixture()
        self.probes["boolean_controls"] = {"fpcr_dn": True, "fpcr_fz": False, "exception_traps_enabled": False}
        self.publish()
        with self.assertRaisesRegex(ValidationError, "boolean_controls"):
            self.validate()

    def test_pass_status_without_measured_preflight_is_rejected(self):
        self.probes = {"status": "passed"}
        self.publish()
        with self.assertRaises(ValidationError):
            self.validate()

    def test_modified_sidecar_is_rejected_even_when_json_remains_valid(self):
        path = self.directory / "native-build-evidence.json"
        data = json.loads(path.read_bytes())
        data["source_root"] = "/changed"
        path.write_bytes(dump_bytes(data, newline=True))
        with self.assertRaisesRegex(ValidationError, "sha256"):
            self.validate()

    def test_referenced_evidence_is_parsed_from_the_authenticated_read_snapshot(self):
        path = self.directory / "native-build-evidence.json"
        original = path.read_bytes()
        replacement = dump_bytes({"artifact_type": "changed-between-reads"}, newline=True)
        real_read = Path.read_bytes
        reads = []

        def read_then_replace(target):
            if target == path:
                reads.append(target)
                raw = real_read(target)
                target.write_bytes(replacement)
                return raw
            return real_read(target)

        with patch.object(Path, "read_bytes", read_then_replace):
            evidence = _referenced_file(self.directory, self.trace["build_evidence"], path.name)
        self.assertEqual(evidence, json.loads(original))
        self.assertEqual(len(reads), 1)
        self.assertEqual(path.read_bytes(), replacement)

    def test_noncanonical_trace_and_development_fixture_are_rejected(self):
        path = self.directory / "native-traceability.json"
        path.write_text(json.dumps(self.trace, indent=2) + "\n")
        with self.assertRaisesRegex(ValidationError, "canonical"):
            self.validate()
        self.fixture()
        self.result.development_fixture = True
        with self.assertRaisesRegex(ValidationError, "native result"):
            self.validate()

    def test_docker_mode_requires_exact_image_identity(self):
        self.trace["execution"]["mode"] = "docker-emulated"
        self.publish()
        with self.assertRaisesRegex(ValidationError, "image identity"):
            self.validate()


if __name__ == "__main__":
    unittest.main()
