"""Independent bit-level matrices for FP predicates, bitwise and memory models.

The ordering oracle never converts to Python float: IEEE sign/magnitude bits
define finite/infinite order, both zero encodings compare equal, and any NaN
is unordered. Memory expectations use explicit byte slices, including exact-fit
allocations and non-default buffer names. Each operation is discoverable alone.
"""

from __future__ import annotations

import copy
from itertools import product
import unittest

from model_test_support import VerifiedModelMixin


def _vector(element, values):
    width = int(element[1:])
    return {"element": element,
            "lanes": ["0x" + value.to_bytes(width // 8, "big").hex() for value in values]}


def _specials(width):
    fraction = 23 if width == 32 else 52
    exponent_bits = 8 if width == 32 else 11
    sign = 1 << (width - 1)
    infinity = ((1 << exponent_bits) - 1) << fraction
    one = ((1 << (exponent_bits - 1)) - 1) << fraction
    magnitudes = (0, 1, 2, (1 << fraction) - 1, 1 << fraction,
                  (1 << fraction) + 1, one - 1, one, one + 1,
                  infinity - 1, infinity, infinity | 1,
                  infinity | (1 << (fraction - 1)) | 0x42)
    return tuple(value | polarity for value in magnitudes for polarity in (0, sign))


def _relation(left, right, width):
    sign = 1 << (width - 1)
    fraction = 23 if width == 32 else 52
    infinity = (((1 << (8 if width == 32 else 11)) - 1) << fraction)
    a, b = left & (sign - 1), right & (sign - 1)
    if a > infinity or b > infinity:
        return None
    if a == b == 0 or left == right:
        return 0
    # Positive encodings increase with magnitude; negative encodings decrease.
    a_key = -a if left & sign else a
    b_key = -b if right & sign else b
    return (a_key > b_key) - (a_key < b_key)


_PREDICATES = {
    "cmpeq": lambda relation: relation == 0,
    "cmpneq": lambda relation: relation != 0,
    "cmplt": lambda relation: relation == -1,
    "cmple": lambda relation: relation in (-1, 0),
    "cmpgt": lambda relation: relation == 1,
    "cmpge": lambda relation: relation in (0, 1),
    "cmpnlt": lambda relation: relation != -1,
    "cmpnle": lambda relation: relation not in (-1, 0),
    "cmpngt": lambda relation: relation != 1,
    "cmpnge": lambda relation: relation not in (0, 1),
    "cmpord": lambda relation: relation is not None,
    "cmpunord": lambda relation: relation is None,
}


class BitPatternModelMatrixTests(VerifiedModelMixin, unittest.TestCase):
    oracle_reference = "tests/test_model_boundary_matrix.py"
    oracle_method = "independent_bit_order_and_byte_reference"

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.oracle_reference_metadata = {
            "oracle": "IEEE encoding order and explicit memory byte slices",
            "source": cls.oracle_reference,
            "case_definitions_sha256": cls.cases.sha256,
            "native_cross_host_evidence": False,
        }

    def check_predicate(self, width, operation, scalar):
        element, lanes = f"f{width}", 128 // width
        values = _specials(width)
        case_id = f"sse2.{operation}.{element}x{lanes}.{'scalar' if scalar else 'default'}"
        for i, j in product(range(len(values)), repeat=2):
            # Inactive lanes deliberately contain NaNs and unequal sentinels.
            a = [values[(i + lane * 7) % len(values)] for lane in range(lanes)]
            b = [values[(j + lane * 11) % len(values)] for lane in range(lanes)]
            expected = list(a)
            for lane in range(1 if scalar else lanes):
                relation = _relation(a[lane], b[lane], width)
                expected[lane] = (1 << width) - 1 if _PREDICATES[operation](relation) else 0
            with self.subTest(left=i, right=j):
                self.assert_model(case_id, {"a": _vector(element, a), "b": _vector(element, b)},
                                  _vector(element, expected), family="fp_bit_order_predicates")

    def check_minmax(self, width, operation, scalar):
        element, lanes = f"f{width}", 128 // width
        values = _specials(width)
        case_id = f"sse2.{operation}.{element}x{lanes}.{'scalar' if scalar else 'default'}"
        for i, j in product(range(len(values)), repeat=2):
            a = [values[(i + lane * 7) % len(values)] for lane in range(lanes)]
            b = [values[(j + lane * 11) % len(values)] for lane in range(lanes)]
            expected = list(a)
            for lane in range(1 if scalar else lanes):
                relation = _relation(a[lane], b[lane], width)
                expected[lane] = a[lane] if relation == (-1 if operation == "min" else 1) else b[lane]
            with self.subTest(left=i, right=j):
                self.assert_model(case_id, {"a": _vector(element, a), "b": _vector(element, b)},
                                  _vector(element, expected), family="fp_minmax_raw_selection")

    def check_bitwise(self, width, operation):
        element, lanes = f"f{width}", 128 // width
        mask = (1 << width) - 1
        for bit, pattern in product(range(128), range(4)):
            a_word = 1 << bit
            b_word = (a_word, ((1 << 128) - 1) ^ a_word,
                      1 << ((bit + 37) % 128),
                      int("a55aa55a" * 4, 16) ^ a_word)[pattern]
            if operation == "and":
                expected_word = a_word & b_word
            elif operation == "or":
                expected_word = a_word | b_word
            elif operation == "xor":
                expected_word = a_word ^ b_word
            else:
                expected_word = ((1 << 128) - 1 - a_word) & b_word
            def lanes_of(word):
                return [(word >> (lane * width)) & mask for lane in range(lanes)]
            with self.subTest(bit=bit, pattern=pattern):
                self.assert_model(f"sse2.{operation}.{element}x{lanes}.default",
                                  {"a": _vector(element, lanes_of(a_word)),
                                   "b": _vector(element, lanes_of(b_word))},
                                  _vector(element, lanes_of(expected_word)), family="fp_bitwise_one_hot")

    def check_memory(self, case_id, width, mode, store, exact_fit):
        element, lanes = f"f{width}", 128 // width
        size = width // 8
        loaded_lanes = lanes if mode in ("all", "reverse") else 1
        payload_size = loaded_lanes * size
        aligned = mode == "reverse"
        offsets = (0, 16, 32, 48) if aligned else range(32)
        values = _specials(width)
        for offset, pattern in product(offsets, range(4)):
            # Payload mixes signaling/quiet NaNs, negative zero and subnormals.
            base = (0, 1, 22, 24)[pattern]
            words = [values[(base + lane * 7) % len(values)] for lane in range(lanes)]
            preserved = [word ^ ((1 << width) - 1) for word in words]
            initial = bytes((17 + index * 29 + pattern) % 256
                            for index in range(offset + payload_size + (0 if exact_fit else 9)))
            if not store:
                payload = b"".join(word.to_bytes(size, "little") for word in words[:loaded_lanes])
                initial = initial[:offset] + payload + initial[offset + payload_size:]
            buffers = {"selected": {"alignment": 16 if aligned else 1, "bytes": "0x" + initial.hex()},
                       "unrelated": {"alignment": 1, "bytes": "0x00ff807f12"}}
            observed = {name: {"byte_offset": 0, "bytes": item["bytes"]} for name, item in buffers.items()}
            if store:
                selected = (list(reversed(words)) if mode == "reverse" else words if mode == "all"
                            else [words[1]] if mode == "high" else [words[0]])
                payload = b"".join(word.to_bytes(size, "little") for word in selected)
                final = initial[:offset] + payload + initial[offset + len(payload):]
                observed["selected"]["bytes"] = "0x" + final.hex()
                operands = {"destination": {"buffer": "selected", "offset": offset},
                            "a": _vector(element, words)}
                wanted = {"buffers": observed}
            else:
                loaded = [int.from_bytes(initial[offset + lane * size:offset + (lane + 1) * size], "little")
                          for lane in range(loaded_lanes)]
                result = (list(reversed(loaded)) if mode == "reverse" else loaded if mode == "all"
                          else loaded * lanes if mode == "broadcast"
                          else [loaded[0], preserved[1]] if mode == "low"
                          else [preserved[0], loaded[0]] if mode == "high"
                          else loaded + [0] * (lanes - 1))
                operands = {"source": {"buffer": "selected", "offset": offset}}
                if mode in ("low", "high"):
                    operands["a"] = _vector(element, preserved)
                wanted = {"buffers": observed, "return": _vector(element, result)}
            before = copy.deepcopy((operands, buffers))
            with self.subTest(offset=offset, pattern=pattern):
                self.assert_model(case_id, operands, buffers=buffers, observed=wanted,
                                  family="fp_memory_exact_fit" if exact_fit else "fp_memory_guards")
                self.assertEqual((operands, buffers), before, "execution must not mutate its input")


def _install(name, method, *args):
    def test(self):
        getattr(self, method)(*args)
    test.__name__ = "test_" + name
    setattr(BitPatternModelMatrixTests, test.__name__, test)


for _width, _operation, _scalar in product((32, 64), _PREDICATES, (False, True)):
    _install(f"{_operation}_f{_width}_{'scalar' if _scalar else 'packed'}", "check_predicate",
             _width, _operation, _scalar)
for _width, _operation, _scalar in product((32, 64), ("min", "max"), (False, True)):
    _install(f"{_operation}_f{_width}_{'scalar' if _scalar else 'packed'}", "check_minmax",
             _width, _operation, _scalar)
for _width, _operation in product((32, 64), ("and", "or", "xor", "andnot")):
    _install(f"{_operation}_f{_width}_each_bit", "check_bitwise", _width, _operation)

_MEMORY = (
    ("load.f32x4.scalar", 32, "scalar", False),
    ("load1.f32x4.broadcast", 32, "broadcast", False),
    ("loadu.f32x4.default", 32, "all", False),
    ("loadr.f32x4.reverse", 32, "reverse", False),
    ("load.f64x2.scalar", 64, "scalar", False),
    ("load1.f64x2.broadcast", 64, "broadcast", False),
    ("loadu.f64x2.default", 64, "all", False),
    ("loadl.f64x2.preserve-high", 64, "low", False),
    ("loadh.f64x2.preserve-low", 64, "high", False),
    ("loadr.f64x2.reverse", 64, "reverse", False),
    ("store.f32x4.scalar", 32, "low", True),
    ("storeu.f32x4.default", 32, "all", True),
    ("storer.f32x4.reverse", 32, "reverse", True),
    ("store.f64x2.scalar", 64, "low", True),
    ("storeh.f64x2.high", 64, "high", True),
    ("storeu.f64x2.default", 64, "all", True),
    ("storer.f64x2.reverse", 64, "reverse", True),
)
for _case, _width, _mode, _store in _MEMORY:
    for _exact in (False, True):
        _install(_case.replace(".", "_") + ("_exact_fit" if _exact else "_guards"),
                 "check_memory", "sse2." + _case, _width, _mode, _store, _exact)


if __name__ == "__main__":
    unittest.main()
