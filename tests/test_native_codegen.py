"""Exercise every generated encoded symbol and actual native memory boundaries."""
from __future__ import annotations

import copy
import ctypes
import json
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tempfile
import unittest

from equilane.canonical import dump_bytes
from equilane.cases import load_case_definitions, validate_case_definition
from equilane.generator import generate_artifact
from equilane.isa import load_isa_registry
from equilane.native_codegen import BLOCKS, generate_native_suite, native_compile_flags, raw_declarations
from equilane.records import derive_input_id, validate_result_record

ROOT = Path(__file__).resolve().parents[1]
ROLES = {"x86_64": "intel", "amd64": "intel", "aarch64": "arm", "arm64": "arm",
         "ppc64le": "openpower", "powerpc64le": "openpower", "riscv64": "riscv"}


class Bytes(ctypes.Structure):
    _fields_ = [("data", ctypes.POINTER(ctypes.c_uint8)), ("size", ctypes.c_uint64)]


class Input(ctypes.Structure):
    _fields_ = [("version", ctypes.c_uint32), ("size", ctypes.c_uint32), ("arguments", Bytes),
                ("rounding", ctypes.c_uint32), ("fp_mode", ctypes.c_uint32)]


class Output(ctypes.Structure):
    _fields_ = [("version", ctypes.c_uint32), ("size", ctypes.c_uint32),
                ("returned", ctypes.POINTER(ctypes.c_uint8)), ("return_capacity", ctypes.c_uint64),
                ("return_size", ctypes.c_uint64), ("effects", ctypes.POINTER(ctypes.c_uint8)),
                ("effects_capacity", ctypes.c_uint64), ("effects_size", ctypes.c_uint64),
                ("flags", ctypes.c_uint32), ("status", ctypes.c_uint32)]


class NativeCodegenCatalogTests(unittest.TestCase):
    def test_every_official_operation_has_a_raw_declaration(self):
        names = {path.stem.replace("-", "_") for path in (ROOT / "10_official_suite/cases").glob("*.py")}
        self.assertEqual(set(raw_declarations(ROOT)), names)
        self.assertGreaterEqual(len(names), 268)

    def test_all_architectures_use_strict_floating_point(self):
        for role in ("intel", "openpower", "arm", "riscv"):
            self.assertIn("-ffp-model=strict", native_compile_flags(role))
            self.assertIn("-fno-fast-math", native_compile_flags(role))


@unittest.skipUnless(sys.platform == "linux" and platform.machine().lower() in ROLES and shutil.which("clang"),
                     "native encoded execution requires a supported Linux C target and Clang")
class NativeEncodedExecutionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temporary.cleanup)
        cls.directory = Path(cls.temporary.name)
        cls.role = ROLES[platform.machine().lower()]
        cls.isa = load_isa_registry(ROOT / "contracts/isa-registry.json")
        cls.cases = load_case_definitions(ROOT / "10_official_suite/cases", isa_registry=cls.isa)
        generated = generate_native_suite(cls.cases, cls.role, cls.directory, project_root=ROOT)
        cls.library_path = cls.directory / "encoded.so"
        command = ["clang", *native_compile_flags(cls.role), "-fPIC", "-shared", "-I" + str(ROOT),
                   "-I" + str(ROOT / "include"), str(generated),
                   str(ROOT / "adapters/common/native_suite_runtime.c"),
                   *(str(ROOT / "10_official_suite" / cls.role / (block + ".c")) for block in BLOCKS),
                   "-lm", "-o", str(cls.library_path)]
        subprocess.run(command, check=True, capture_output=True, timeout=300)
        cls.library = ctypes.CDLL(str(cls.library_path))
        cls.library.equilane_symbol_count.restype = ctypes.c_size_t
        cls.library.equilane_symbol_at.argtypes = [ctypes.c_size_t]
        cls.library.equilane_symbol_at.restype = ctypes.c_char_p
        cls.library.equilane_lookup.argtypes = [ctypes.c_char_p]
        cls.library.equilane_lookup.restype = ctypes.c_void_p
        generated_inputs = generate_artifact(cases=cls.cases, isa_registry=cls.isa,
                                             output=cls.directory / "vectors", count_per_case=3,
                                             retain_records=True)
        cls.records = generated_inputs.records
        cls.by_case = {record["case_id"]: record for record in cls.records}

    def call(self, case, payload, *, raw=None, capacity=2 * 1024 * 1024, rounding=0, library=None):
        encoded = dump_bytes(payload) if raw is None else raw
        arguments = (ctypes.c_uint8 * len(encoded)).from_buffer_copy(encoded)
        returned = (ctypes.c_uint8 * capacity)()
        effects = (ctypes.c_uint8 * capacity)()
        input_record = Input(1, ctypes.sizeof(Input), Bytes(arguments, len(encoded)), rounding, 0)
        output_record = Output(1, ctypes.sizeof(Output), returned, capacity, 0, effects, capacity, 0, 0, 0)
        function = getattr(library or self.library, case.data[self.role]["symbol"])
        function.argtypes = [ctypes.POINTER(Input), ctypes.POINTER(Output)]
        function.restype = ctypes.c_int
        result = function(ctypes.byref(input_record), ctypes.byref(output_record))
        observed = {}
        if result == 0 and output_record.status == 0:
            if case.signature["return"]["type"] != "void":
                observed["return"] = json.loads(bytes(returned[:output_record.return_size]))
            if any(argument["type"] == "pointer" for argument in case.signature["arguments"]):
                observed["buffers"] = json.loads(bytes(effects[:output_record.effects_size]))
        return result, output_record, observed

    @staticmethod
    def payload(record):
        return {"operands": record["operands"], "buffers": record.get("buffers", {}),
                "immediates": record.get("immediates", {})}

    def test_dynamic_registry_audits_all_registered_abi_symbols(self):
        count = self.library.equilane_symbol_count()
        names = [self.library.equilane_symbol_at(index).decode() for index in range(count)]
        self.assertEqual(names, sorted(case.data[self.role]["symbol"] for case in self.cases))
        self.assertEqual(count, len(self.cases))
        self.assertIsNone(self.library.equilane_symbol_at(count))
        self.assertIsNone(self.library.equilane_lookup(b"missing_symbol"))
        for name in names:
            self.assertTrue(self.library.equilane_lookup(name.encode()), name)

    def test_every_generated_input_executes_actual_encoded_abi(self):
        cases_seen = set()
        for record in self.records:
            case = self.cases.get(str(record["case_id"]))
            with self.subTest(case=case.id, input=record["input_id"]):
                code, output, observed = self.call(case, self.payload(record))
                self.assertEqual((code, output.status), (0, 0))
                result = {"schema_version": 1, "case_id": case.id, "input_id": record["input_id"],
                          "runner": self.role, "status": "ok", "duration_ns": 0, "observed": observed}
                validate_result_record(result, case, role=self.role, input_record=record)
                cases_seen.add(case.id)
        self.assertEqual(len(cases_seen), len(self.cases))

    def test_integer_add_produces_expected_bits_without_a_python_model(self):
        case = self.cases.get("sse2.add.i32x4.default")
        payload = {"buffers": {}, "immediates": {}, "operands": {
            "a": {"element": "i32", "lanes": ["0x7fffffff", "0xffffffff", "0x80000000", "0x00000003"]},
            "b": {"element": "i32", "lanes": ["0x00000001", "0x00000002", "0xffffffff", "0x00000004"]},
        }}
        code, output, observed = self.call(case, payload)
        self.assertEqual((code, output.status), (0, 0))
        self.assertEqual(observed["return"]["lanes"], ["0x80000000", "0x00000001", "0x7fffffff", "0x00000007"])

    def test_duplicate_unknown_and_malformed_json_are_rejected(self):
        case = self.cases.get("sse2.add.i32x4.default")
        payload = self.payload(self.by_case[case.id])
        good = dump_bytes(payload)
        bad_payload = copy.deepcopy(payload)
        bad_payload["operands"]["extra"] = bad_payload["operands"]["a"]
        malformed = [good[:-1], b"[]", good + b" {}", good.replace(b'"buffers":{}', b'"buffers":{},"buffers":{}'),
                     dump_bytes(bad_payload), good.replace(b'"i32"', b'"f32"', 1)]
        for raw in malformed:
            with self.subTest(raw=raw[:80]):
                code, output, _ = self.call(case, payload, raw=raw)
                self.assertEqual((code, output.status), (0, 2))
                self.assertEqual((output.return_size, output.effects_size), (0, 0))

    def test_undeclared_immediate_and_rounding_are_rejected(self):
        case = self.cases.get("sse2.shuffle.i32x4.imm8")
        payload = self.payload(self.by_case[case.id])
        payload["immediates"] = {"imm8": 2}
        code, output, _ = self.call(case, payload)
        self.assertEqual((code, output.status), (0, 2))
        payload["immediates"] = {"imm8": 27}
        code, output, _ = self.call(case, payload, rounding=1)
        self.assertEqual((code, output.status), (0, 2))

    def test_capacity_negotiation_does_not_write_output(self):
        case = self.cases.get("sse2.add.i32x4.default")
        code, output, _ = self.call(case, self.payload(self.by_case[case.id]), capacity=0)
        self.assertEqual(code, 2)
        self.assertGreater(output.return_size, 0)
        self.assertEqual(output.effects_size, 2)

    def test_pointer_alignment_bounds_and_buffer_preservation(self):
        case = self.cases.get("sse2.storeu.f64x2.default")
        payload = {"buffers": {"storage": {"alignment": 16, "bytes": "0x" + "a5" * 48}},
                   "immediates": {}, "operands": {
                       "destination": {"buffer": "storage", "offset": 7},
                       "a": {"element": "f64", "lanes": ["0x3ff0000000000000", "0x8000000000000000"]},
                   }}
        code, output, observed = self.call(case, payload)
        self.assertEqual((code, output.status), (0, 0))
        actual = bytes.fromhex(observed["buffers"]["storage"]["bytes"][2:])
        self.assertEqual(actual[:7], b"\xa5" * 7)
        self.assertEqual(actual[7:23], bytes.fromhex("000000000000f03f0000000000000080"))
        self.assertEqual(actual[23:], b"\xa5" * 25)
        payload["operands"]["destination"]["offset"] = 40
        code, output, _ = self.call(case, payload)
        self.assertEqual((code, output.status), (0, 2))

    def test_all_rounding_modes_capture_inexact_and_restore_callers_environment(self):
        original = self.cases.get("sse2.add.f64x2.default")
        data = copy.deepcopy(original.data)
        data["environment"] = {"fp_rounding_modes": ["nearest_even", "toward_negative", "toward_positive", "toward_zero"],
                               "observe_fp_exceptions": True}
        data[self.role]["symbol"] += "_round_modes"
        operands = {
            "a": {"element": "f64", "lanes": ["0x3ff0000000000000", "0xbff0000000000000"]},
            "b": {"element": "f64", "lanes": ["0x3ca0000000000000", "0xbca0000000000000"]},
        }
        data["regressions"] = {"rounding-witness.v1": {
            "input_id": derive_input_id({"case_id": original.id, "operands": operands,
                                         "environment": {"fp_mode": "ieee", "rounding": "nearest_even"}}),
            "expected_intel": {"status": "ok", "observed": {
                "return": {"element": "f64", "lanes": ["0x3ff0000000000000", "0xbff0000000000000"]},
                "fp_exceptions": ["inexact"],
            }},
        }}
        case = validate_case_definition(data, isa_registry=self.isa, source_path=original.source_path)
        directory = self.directory / "round-modes"
        generated = generate_native_suite([case], self.role, directory, project_root=ROOT)
        with generated.open("a") as stream:
            stream.write("\nint test_set_round(int mode) { return fesetround(mode); }\n"
                         "int test_get_round(void) { return fegetround(); }\n"
                         "int test_downward(void) { return FE_DOWNWARD; }\n")
        library_path = directory / "round.so"
        subprocess.run(["clang", *native_compile_flags(self.role), "-fPIC", "-shared", "-I" + str(ROOT),
                        "-I" + str(ROOT / "include"), str(generated),
                        str(ROOT / "adapters/common/native_suite_runtime.c"),
                        *(str(ROOT / "10_official_suite" / self.role / (block + ".c")) for block in BLOCKS),
                        "-lm", "-o", str(library_path)], check=True, capture_output=True, timeout=300)
        library = ctypes.CDLL(str(library_path))
        payload = {"buffers": {}, "immediates": {}, "operands": operands}
        expected = {0: ["0x3ff0000000000000", "0xbff0000000000000"],
                    1: ["0x3ff0000000000000", "0xbff0000000000000"],
                    2: ["0x3ff0000000000001", "0xbff0000000000000"],
                    3: ["0x3ff0000000000000", "0xbff0000000000001"]}
        saved = library.test_get_round()
        try:
            self.assertEqual(library.test_set_round(library.test_downward()), 0)
            for mode, bits in expected.items():
                with self.subTest(rounding=mode):
                    code, output, observed = self.call(case, payload, rounding=mode, library=library)
                    self.assertEqual((code, output.status), (0, 0))
                    self.assertEqual(observed["return"]["lanes"], bits)
                    self.assertEqual(output.flags & 16, 16, "inexact must be captured before extracting results")
                    self.assertEqual(library.test_get_round(), library.test_downward())
        finally:
            library.test_set_round(saved)


if __name__ == "__main__":
    unittest.main()
