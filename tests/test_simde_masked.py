"""Verify lane masks and suppressed exceptions in actual generated C."""

from __future__ import annotations

import copy
import ctypes
import json
from pathlib import Path
import platform
import struct
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from equilane.simde_masked import generate_source, validate_catalog


class MaskedCodegenTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.catalog = json.loads((ROOT / "contracts/simde-masked.json").read_text())
        cls.records = {r["name"]: r for r in cls.catalog["intrinsics"]}

    def test_unsafe_neutralization_and_wrong_mask_modes_are_rejected(self):
        row_index = next(i for i, r in enumerate(self.catalog["intrinsics"]) if r["name"] == "_mm256_mask_div_ps")
        for field, value in (("inactive_operand_bits", [0, 0]), ("mask_mode", "zero"),
                             ("mask_argument_index", 100), ("base_alias_body", "bad()")):
            changed = copy.deepcopy(self.catalog)
            changed["intrinsics"][row_index][field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                validate_catalog(changed)

    def test_inactive_snan_divzero_and_merge_source_are_bit_exact(self):
        names = ["_mm256_mask_div_ps", "_mm256_maskz_div_ps", "_mm256_mask_sqrt_ps",
                 "_mm256_mask_fmadd_ps", "_mm_maskz_div_ps"]
        roles = ["portable", "intel"] if platform.machine() in {"x86_64", "AMD64"} else ["portable"]
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for role in roles:
                source = root / f"masked-{role}.c"
                library = root / f"masked-{role}.so"
                source.write_text(generate_source(self.catalog, names, role=role))
                build = subprocess.run(["clang", "-std=c11", "-O2", "-ffp-model=strict", "-shared", "-fPIC",
                    "-I", str(ROOT / "vendor/simde"), str(source), "-lm", "-o", str(library)],
                    text=True, capture_output=True)
                self.assertEqual(build.returncode, 0, build.stderr)
                loaded = ctypes.CDLL(str(library))
                call = loaded.equilane_ext_call_with_flags
                call.argtypes = [ctypes.c_uint32, ctypes.c_void_p, ctypes.c_uint64,
                                 ctypes.c_void_p, ctypes.c_uint64, ctypes.POINTER(ctypes.c_uint32)]
                call.restype = ctypes.c_int
                for name in names:
                    row = self.records[name]
                    lanes = row["lane_count"]
                    src = [0x7F800123 + lane for lane in range(lanes)]
                    mask = 0x55 if lanes == 8 else 0xF0
                    values = {"src": src, "k": mask}
                    if "fmadd" in name:
                        values.update(a=[0x40000000 if mask & (1 << i) else src[i] for i in range(lanes)],
                                      b=[0x40400000 if mask & (1 << i) else 0x7F800001 for i in range(lanes)],
                                      c=[0x40800000 if mask & (1 << i) else 0x7F800001 for i in range(lanes)])
                        active = 0x41200000
                    elif "sqrt" in name:
                        values["a"] = [0x40800000 if mask & (1 << i) else 0xBF800000 for i in range(lanes)]
                        active = 0x40000000
                    else:
                        values.update(a=[0x40800000 if mask & (1 << i) else 0x7F800001 for i in range(lanes)],
                                      b=[0x40000000 if mask & (1 << i) else 0 for i in range(lanes)])
                        active = 0x40000000
                    raw = bytearray(row["input_byte_length"])
                    for argument in row["arguments"]:
                        value = values[argument["name"]]
                        data = (value.to_bytes(argument["byte_length"], "little") if argument["kind"] == "mask"
                                else struct.pack("<" + "I" * lanes, *value))
                        start = argument["offset"]
                        raw[start:start + len(data)] = data
                    merge = values[row["arguments"][row["merge_argument_index"]]["name"]] if row["mask_mode"] == "merge" else [0] * lanes
                    expected = struct.pack("<" + "I" * lanes,
                        *[active if mask & (1 << lane) else merge[lane] for lane in range(lanes)])
                    inp = ctypes.create_string_buffer(bytes(raw))
                    out = ctypes.create_string_buffer(row["output_byte_length"])
                    flags = ctypes.c_uint32(0xFFFFFFFF)
                    status = call(row["ordinal"], inp, len(raw), out, len(expected), ctypes.byref(flags))
                    with self.subTest(role=role, name=name):
                        self.assertIn(status, (0, 3))
                        if role == "portable":
                            self.assertEqual(status, 0)
                        if status == 0:
                            self.assertEqual(out.raw, expected)
                            self.assertEqual(flags.value, 0, "inactive lanes must not raise FP exceptions")
                        else:
                            self.assertEqual(flags.value, 0)


if __name__ == "__main__":
    unittest.main()
