from __future__ import annotations

import copy
from pathlib import Path
import sys
import unittest


PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "src"))

from equilane.canonical import dump_bytes, sha256_bytes
from equilane.cases import load_case_definitions, validate_case_definition
from equilane.errors import ValidationError
from equilane.isa import load_isa_registry, project_used_isa, validate_used_isa_contract
from equilane.targets import FP_CONTROL_KEYS, TARGETS, TARGET_ROLES, TARGET_TARGETS


class TargetContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.isa = load_isa_registry(PROJECT / "contracts/isa-registry.json")
        cls.cases = load_case_definitions(PROJECT / "10_official_suite/cases", isa_registry=cls.isa)

    def test_registry_keeps_legacy_paths_and_defines_arm64_and_rvv_profiles(self):
        self.assertEqual(TARGET_ROLES, ("intel", "openpower", "arm", "riscv"))
        self.assertEqual(TARGET_TARGETS, ("openpower", "arm", "riscv"))
        self.assertEqual((TARGETS["openpower"].results_file, TARGETS["openpower"].manifest_file),
                         ("power-results.jsonl", "power-results.manifest.json"))
        self.assertEqual(TARGETS["arm"].architecture, "aarch64")
        self.assertEqual(TARGETS["riscv"].architecture, "riscv64")
        self.assertEqual(self.isa.get("neon")["detector"]["id"], "linux.auxv.asimd")
        self.assertEqual(self.isa.get("rvv")["detector"]["id"], "linux.hwprobe.rvv")
        self.assertEqual(FP_CONTROL_KEYS["aarch64"],
                         {"exception_traps_enabled", "fpcr_dn", "fpcr_fz"})
        self.assertEqual(FP_CONTROL_KEYS["riscv64"], {"exception_traps_enabled"})

    def test_every_official_pack_has_valid_architecture_specific_bindings(self):
        self.assertEqual(len(self.cases), 254)
        for case in self.cases:
            with self.subTest(case=case.id):
                self.assertEqual(case.target_roles, TARGET_ROLES)
                intel_symbol = case.data["intel"]["symbol"]
                suffix = intel_symbol.removeprefix("intel_")
                for role, token in (("arm", "neon"), ("riscv", "rvv")):
                    self.assertEqual(case.data[role]["symbol"], role + "_" + suffix)
                    self.assertEqual(case.required_isa(role), (token,))
                    self.assertEqual(self.isa.closure(case.required_isa(role),
                                                     architecture=TARGETS[role].architecture), (token,))

    def test_legacy_two_role_projection_retains_its_exact_shape_and_digest(self):
        data = copy.deepcopy(self.cases.get("sse2.add.i64x2.default").data)
        data.pop("arm")
        data.pop("riscv")
        case = validate_case_definition(data, isa_registry=self.isa)
        projected = project_used_isa(self.isa, [case])
        expected = {
            "schema_version": 1,
            "cases": [{"id": case.id, "intel": ["sse2"], "openpower": ["power8", "vsx"]}],
            "tokens": [self.isa.get(token) for token in ("power8", "sse2", "vsx")],
        }
        self.assertEqual(case.target_roles, ("intel", "openpower"))
        self.assertEqual(projected.data, expected)
        self.assertEqual(projected.sha256, sha256_bytes(dump_bytes(expected, newline=True)))
        with self.assertRaisesRegex(ValidationError, "no binding"):
            case.required_isa("arm")

    def test_optional_bindings_project_only_present_targets_and_reject_tampering(self):
        data = copy.deepcopy(self.cases.get("sse2.add.i64x2.default").data)
        data.pop("riscv")
        case = validate_case_definition(data, isa_registry=self.isa)
        used = project_used_isa(self.isa, [case])
        self.assertEqual(set(used.data["cases"][0]), {"id", "intel", "openpower", "arm"})
        self.assertEqual([entry["token"] for entry in used.data["tokens"]],
                         ["neon", "power8", "sse2", "vsx"])
        validate_used_isa_contract(used.data, registry=self.isa, cases=[case])
        tampered = copy.deepcopy(used.data)
        tampered["cases"][0]["riscv"] = ["rvv"]
        with self.assertRaisesRegex(ValidationError, "projection"):
            validate_used_isa_contract(tampered, registry=self.isa, cases=[case])

    def test_optional_bindings_are_closed_and_cannot_claim_another_architectures_isa(self):
        for role, field, value in (("arm", "required_isa", ["sse2"]),
                                   ("riscv", "required_isa", ["neon"]),
                                   ("arm", "native_evidence", True)):
            with self.subTest(role=role, field=field):
                data = copy.deepcopy(self.cases.get("sse2.add.i64x2.default").data)
                data[role][field] = value
                with self.assertRaises(ValidationError):
                    validate_case_definition(data, isa_registry=self.isa)

    def test_original_reference_and_openpower_bindings_remain_required(self):
        for role in ("intel", "openpower"):
            with self.subTest(role=role):
                data = copy.deepcopy(self.cases.get("sse2.add.i64x2.default").data)
                data.pop(role)
                with self.assertRaises(ValidationError):
                    validate_case_definition(data, isa_registry=self.isa)


if __name__ == "__main__":
    unittest.main()
