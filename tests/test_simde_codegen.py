"""Execute generated C through the fixed-width byte ABI, not a Python model."""

from __future__ import annotations

import copy
import ctypes
import json
from pathlib import Path
import platform
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from equilane.simde_codegen import generate_source, validate_catalog, verify_vendor


class SIMDeCodegenTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.catalog = json.loads((ROOT / "contracts/simde-pure-values.json").read_text())
        cls.rows = {row["name"]: row for row in cls.catalog["intrinsics"]}

    def test_vendor_and_catalog_changes_are_rejected(self):
        evidence = validate_catalog(self.catalog)
        self.assertEqual(len(evidence["vendor_header_tree_sha256"]), 64)
        for field, value in (("simde_alias_body", "invented_call(a)"),
                             ("input_byte_length", 0), ("intel_call_symbol", "invented_call")):
            changed = copy.deepcopy(self.catalog)
            changed["intrinsics"][0][field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                validate_catalog(changed)
        with tempfile.TemporaryDirectory() as temporary:
            vendor = Path(temporary) / "simde"
            shutil.copytree(ROOT / "vendor/simde", vendor)
            header = next((vendor / "simde/x86").glob("*.h"))
            header.write_text(header.read_text() + "\n/* changed */\n")
            with self.assertRaisesRegex(ValueError, "source hash changed"):
                verify_vendor(vendor)

    def test_actual_c_results_widths_fp_flags_and_caller_environment(self):
        names = ["_mm256_add_epi32", "_mm512_add_epi32", "_m_pavgb", "_mm256_add_ps",
                 "_mm512_cmpeq_ps_mask"]
        helper = r'''
int test_set_downward(void) { return fesetround(FE_DOWNWARD); }
int test_is_downward(void) { return fegetround() == FE_DOWNWARD; }
int test_raise_divzero(void) { return feclearexcept(FE_ALL_EXCEPT) || feraiseexcept(FE_DIVBYZERO); }
int test_has_divzero(void) { return !!fetestexcept(FE_DIVBYZERO); }
int test_has_inexact(void) { return !!fetestexcept(FE_INEXACT); }
int test_reset(void) { return fesetenv(FE_DFL_ENV); }
'''
        roles = ["portable", "intel"] if platform.machine() in {"x86_64", "AMD64"} else ["portable"]
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            for role in roles:
                with self.subTest(role=role):
                    source = directory / f"{role}.c"
                    source.write_text(generate_source(self.catalog, names, role=role) + helper)
                    library = directory / f"{role}.so"
                    result = subprocess.run(["clang", "-std=c11", "-O2", "-ffp-model=strict",
                        "-shared", "-fPIC", "-I", str(ROOT / "vendor/simde"), str(source),
                        "-lm", "-o", str(library)], text=True, capture_output=True)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    loaded = ctypes.CDLL(str(library))
                    call = loaded.equilane_ext_call_with_flags
                    call.argtypes = [ctypes.c_uint32, ctypes.c_void_p, ctypes.c_uint64,
                                     ctypes.c_void_p, ctypes.c_uint64, ctypes.POINTER(ctypes.c_uint32)]
                    call.restype = ctypes.c_int
                    examples = [
                        (names[0], struct.pack("<16I", *([0xffffffff] * 8 + [2] * 8)), struct.pack("<8I", *([1] * 8)), 0),
                        (names[1], struct.pack("<32I", *range(32)), struct.pack("<16I", *[16 + 2 * i for i in range(16)]), 0),
                        (names[2], bytes([0,1,2,3,252,253,254,255] + [1] * 8), bytes([1,1,2,2,127,127,128,128]), 0),
                        (names[3], struct.pack("<16I", *([0x3f800000] * 8 + [0x34400000] * 8)), struct.pack("<8I", *([0x3f800002] * 8)), 16),
                        (names[4], struct.pack("<32I", *([0x3f800000] * 32)), struct.pack("<H", 65535), 0),
                    ]
                    for name, raw, expected, expected_flags in examples:
                        self.assertEqual(loaded.test_set_downward(), 0)
                        self.assertEqual(loaded.test_raise_divzero(), 0)
                        try:
                            input_buffer = ctypes.create_string_buffer(raw)
                            output_buffer = ctypes.create_string_buffer(len(expected))
                            flags = ctypes.c_uint32(0xffffffff)
                            status = call(self.rows[name]["ordinal"], input_buffer, len(raw),
                                          output_buffer, len(expected), ctypes.byref(flags))
                            self.assertIn(status, {0, 3})
                            if role == "portable":
                                self.assertEqual(status, 0)
                            if status == 0:
                                self.assertEqual(output_buffer.raw, expected, name)
                                self.assertEqual(flags.value, expected_flags, name)
                            else:
                                self.assertEqual(flags.value, 0)
                            self.assertEqual(loaded.test_is_downward(), 1)
                            self.assertEqual(loaded.test_has_divzero(), 1)
                            self.assertEqual(loaded.test_has_inexact(), 0)
                        finally:
                            loaded.test_reset()
                    self.assertEqual(call(0xffffffff, None, 0, ctypes.create_string_buffer(1), 1, None), 1)
                    self.assertEqual(call(self.rows[names[0]]["ordinal"], None, 64, ctypes.create_string_buffer(32), 32, None), 2)


if __name__ == "__main__":
    unittest.main()
