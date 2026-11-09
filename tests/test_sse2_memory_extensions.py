"""Independent byte oracles for the newly registered SSE/SSE2 memory cases."""
from __future__ import annotations

import copy
from itertools import product
import unittest

from equilane.development import load_development_case
from model_test_support import VerifiedModelMixin


def _vector(element, values):
    width = int(element[1:])
    return {"element": element, "lanes": [f"0x{value:0{width // 4}x}" for value in values]}


class SSEMemoryExtensionModelTests(VerifiedModelMixin, unittest.TestCase):
    oracle_reference = "tests/test_sse2_memory_extensions.py"
    oracle_method = "independent_bit_patterns_and_memory_slices"

    def zero(self, shape):
        element, lanes = shape.split("x")
        case_id = f"sse2.setzero.{shape}.default"
        self.assert_model(case_id, {}, _vector(element, [0] * int(lanes)), family="zero_constructor")
        case = self.cases.get(case_id)
        candidates = list(load_development_case(case).candidates(case, seed_text="0x0123456789abcdef"))
        self.assertEqual(len(candidates), 1, "a no-argument, single-mode intrinsic has exactly one input")
        self.assertEqual(candidates[0]["operands"], {})

    def memory(self, case_id, mode, store):
        case = self.cases.get(case_id)
        value = next((arg for arg in case.signature["arguments"] if arg["type"] == "vector"), case.signature["return"])
        element, count = value["element"], value["lanes"]
        width = int(element[1:]) // 8
        pointer = "destination" if store else "source"
        contract = case.data["memory_contract"][pointer]
        alignment = contract["required_alignment"]
        extent = max(row["byte_length"] for row in contract["read_ranges"] + contract["write_ranges"])
        offsets = (0, 16, 32, 48) if alignment == 16 else range(32)
        patterns = ((0, 0x80000000, 0x7f800001, 0xffc12345) if width == 4 else
                    (0x7ff0000000000001, 0x8000000000000000, 0xfff8000000012345, 1))
        for offset, pattern, exact_fit in product(offsets, range(4), (False, True)):
            words = [patterns[(pattern + lane) % len(patterns)] for lane in range(count)]
            initial = bytes((29 * i + 17 + pattern) & 255 for i in range(offset + extent + (0 if exact_fit else 11)))
            after = initial
            operands = {pointer: {"buffer": "selected", "offset": offset}}
            if store or mode in ("low", "high"):
                operands["a"] = _vector(element, words)
            if store:
                selected = words[-extent // width:] if mode == "high" else words[:extent // width]
                payload = b"".join(word.to_bytes(width, "little") for word in selected)
                after = initial[:offset] + payload + initial[offset + extent:]
            else:
                loaded = [int.from_bytes(initial[i:i + width], "little") for i in range(offset, offset + extent, width)]
                expected = loaded if mode == "all" else loaded + words[len(loaded):] if mode == "low" else words[:-len(loaded)] + loaded
            buffers = {"selected": {"alignment": alignment, "bytes": "0x" + initial.hex()},
                       "untouched": {"alignment": 1, "bytes": "0xff00017f"}}
            observed = {"buffers": {"selected": {"byte_offset": 0, "bytes": "0x" + after.hex()},
                                    "untouched": {"byte_offset": 0, "bytes": "0xff00017f"}}}
            if not store:
                observed["return"] = _vector(element, expected)
            original = copy.deepcopy((operands, buffers))
            with self.subTest(offset=offset, pattern=pattern, exact_fit=exact_fit):
                self.assert_model(case_id, operands, buffers=buffers, observed=observed,
                                  family="sse_memory_extensions")
                self.assertEqual((operands, buffers), original)


def _install(name, method, *args):
    def test(self):
        getattr(self, method)(*args)
    test.__name__ = "test_" + name
    setattr(SSEMemoryExtensionModelTests, test.__name__, test)


for _shape in ("f32x4", "f64x2", "i32x4"):
    _install("setzero_" + _shape, "zero", _shape)
    for _store in (False, True):
        _operation = "store" if _store else "load"
        _install(_operation + "_aligned_" + _shape, "memory", f"sse2.{_operation}.{_shape}.aligned", "all", _store)
for _mode in ("low", "high"):
    _half = "l" if _mode == "low" else "h"
    _preserved = "high" if _mode == "low" else "low"
    _install("load" + _half + "_f32x4", "memory", f"sse2.load{_half}.f32x4.preserve-{_preserved}", _mode, False)
    _install("store" + _half + "_f32x4", "memory", f"sse2.store{_half}.f32x4.{_mode}", _mode, True)
_install("storel_f64x2", "memory", "sse2.storel.f64x2.low", "low", True)


if __name__ == "__main__":
    unittest.main()
