from __future__ import annotations

import copy
from pathlib import Path
import sys
import tempfile
from types import MappingProxyType
import unittest


PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "src"))

from equilane.artifacts import validate_input_artifact, validate_result_artifact
from equilane.canonical import atomic_write, dump_bytes, sha256_bytes
from equilane.cases import CaseRegistry, load_case_definitions, validate_case_definition
from equilane.errors import ValidationError
from equilane.fixture import run_fixture
from equilane.generator import generate_artifact
from equilane.isa import load_isa_registry
from equilane.targets import FP_CONTROL_KEYS, TARGETS, TARGET_ROLES


class TargetArtifactTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.isa = load_isa_registry(PROJECT / "contracts/isa-registry.json")
        cls.cases = load_case_definitions(PROJECT / "10_official_suite/cases/add-f32x4.py",
                                         isa_registry=cls.isa)

    def _artifacts(self, root, role, *, cases=None):
        cases = self.cases if cases is None else cases
        generated = generate_artifact(cases=cases, isa_registry=self.isa, output=root / "vectors",
                                      count_per_case=2)
        inputs = validate_input_artifact(generated.manifest_path, cases, self.isa)
        fixture = run_fixture(input_artifact=inputs, cases=cases, isa_registry=self.isa,
                              role=role, output=root / role)
        return inputs, fixture

    def _validate(self, fixture, inputs, *, cases=None):
        return validate_result_artifact(fixture.manifest_path,
                                        self.cases if cases is None else cases,
                                        self.isa, input_artifact=inputs)

    def _write_manifest(self, fixture, mutate):
        manifest = copy.deepcopy(fixture.manifest)
        mutate(manifest)
        atomic_write(fixture.manifest_path, dump_bytes(manifest, newline=True))

    def test_four_roles_publish_distinct_validated_development_artifacts(self):
        for role in TARGET_ROLES:
            with self.subTest(role=role), tempfile.TemporaryDirectory() as temporary:
                inputs, fixture = self._artifacts(Path(temporary), role)
                target = TARGETS[role]
                result = self._validate(fixture, inputs)
                self.assertEqual(result.role, role)
                self.assertTrue(result.development_fixture)
                self.assertEqual(fixture.results_path.name, target.results_file)
                self.assertEqual(fixture.manifest_path.name, target.manifest_file)
                self.assertEqual(fixture.manifest["environment"]["architecture"], target.architecture)
                self.assertEqual(fixture.manifest["environment"]["fp_controls"],
                                 {name: False for name in FP_CONTROL_KEYS[target.architecture]})
                self.assertEqual(fixture.manifest["environment"]["cpu_model"],
                                 "non-native-development-fixture")
                self.assertTrue(fixture.manifest["runner"]["build_id"].startswith("development-fixture:"))
                self.assertEqual(result.record_count, 2)
                self.assertEqual(result.ordered_records, fixture.records)
                self.assertEqual({record["runner"] for record in result.ordered_records}, {role})

    def test_new_roles_reject_cross_architecture_environment_and_foreign_controls(self):
        for role in ("arm", "riscv"):
            for mutation in ("architecture", "foreign_control", "enabled_control"):
                with self.subTest(role=role, mutation=mutation), tempfile.TemporaryDirectory() as temporary:
                    inputs, fixture = self._artifacts(Path(temporary), role)

                    def change(manifest):
                        environment = manifest["environment"]
                        if mutation == "architecture":
                            environment["architecture"] = "x86_64"
                        elif mutation == "foreign_control":
                            environment["fp_controls"]["fpscr_ni"] = False
                        else:
                            environment["fp_controls"]["exception_traps_enabled"] = True

                    self._write_manifest(fixture, change)
                    with self.assertRaises(ValidationError):
                        self._validate(fixture, inputs)

    def test_new_role_isa_metadata_cannot_claim_another_target(self):
        for role in ("arm", "riscv"):
            with self.subTest(role=role), tempfile.TemporaryDirectory() as temporary:
                inputs, fixture = self._artifacts(Path(temporary), role)
                self._write_manifest(fixture, lambda manifest:
                                     manifest["environment"].update(available_isa=["sse2"]))
                with self.assertRaisesRegex(ValidationError, "wrong architecture"):
                    self._validate(fixture, inputs)

    def test_new_role_floating_results_require_their_own_complete_preflight_controls(self):
        for role in ("arm", "riscv"):
            with self.subTest(role=role), tempfile.TemporaryDirectory() as temporary:
                inputs, fixture = self._artifacts(Path(temporary), role)

                def change(manifest):
                    preflight = manifest["preflight"]
                    suite = preflight["probe_suite"]
                    suite[0]["expected"]["boolean_controls"].pop("exception_traps_enabled")
                    preflight["probe_suite_sha256"] = sha256_bytes(dump_bytes(suite, newline=True))

                self._write_manifest(fixture, change)
                with self.assertRaisesRegex(ValidationError, "missing floating-point control"):
                    self._validate(fixture, inputs)

    def test_fixtures_cannot_be_rebranded_as_native_by_replacing_the_build_id(self):
        for role in TARGET_ROLES:
            with self.subTest(role=role), tempfile.TemporaryDirectory() as temporary:
                inputs, fixture = self._artifacts(Path(temporary), role)
                self._write_manifest(fixture, lambda manifest:
                                     manifest["runner"].update(build_id=f"native:{role}:test"))
                with self.assertRaisesRegex(ValidationError, "native results require an adapter or SUT"):
                    self._validate(fixture, inputs)

    def test_legacy_pack_without_new_binding_produces_unsupported_instead_of_model_evidence(self):
        original = next(iter(self.cases))
        data = copy.deepcopy(original.data)
        data.pop("arm")
        data.pop("riscv")
        case = validate_case_definition(data, isa_registry=self.isa, source_path=original.source_path)
        cases = CaseRegistry(MappingProxyType({case.id: case}), sha256_bytes(dump_bytes([data], newline=True)))
        for role in ("arm", "riscv"):
            with self.subTest(role=role), tempfile.TemporaryDirectory() as temporary:
                inputs, fixture = self._artifacts(Path(temporary), role, cases=cases)
                result = self._validate(fixture, inputs, cases=cases)
                self.assertTrue(result.development_fixture)
                for record in result.ordered_records:
                    self.assertEqual(record["status"], "unsupported")
                    self.assertEqual(record["error"], {"stage": "capability", "code": "target_binding_unavailable"})
                    self.assertNotIn("observed", record)
                self.assertEqual(fixture.manifest["environment"]["available_isa"], [])


if __name__ == "__main__":
    unittest.main()
