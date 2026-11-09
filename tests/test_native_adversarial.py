"""Independent, adversarial oracles executed through the real encoded C ABI.

The byte arithmetic matrices exhaust all 65,536 operand pairs. Wider integer
matrices cross signed/unsigned boundaries; floating data movement uses raw bits,
including signaling NaNs. No portable operation model supplies expectations.
NaN arithmetic/conversion port findings belong to the Intel-baseline comparison;
these tests assert exact integer, memory and representable finite FP behavior.
"""
from __future__ import annotations

import copy
import ctypes
from itertools import product
import json
from pathlib import Path
import platform
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest

from equilane.canonical import dump_bytes
from equilane.cases import load_case_definitions
from equilane.isa import load_isa_registry
from equilane.native_codegen import BLOCKS, generate_native_suite, native_compile_flags


ROOT = Path(__file__).resolve().parents[1]
ROLES = {"x86_64": "intel", "amd64": "intel", "aarch64": "arm", "arm64": "arm",
         "ppc64le": "openpower", "powerpc64le": "openpower", "riscv64": "riscv"}


class _Bytes(ctypes.Structure):
    _fields_ = [("data", ctypes.POINTER(ctypes.c_uint8)), ("size", ctypes.c_uint64)]


class _Input(ctypes.Structure):
    _fields_ = [("version", ctypes.c_uint32), ("size", ctypes.c_uint32), ("arguments", _Bytes),
                ("rounding", ctypes.c_uint32), ("fp_mode", ctypes.c_uint32)]


class _Output(ctypes.Structure):
    _fields_ = [("version", ctypes.c_uint32), ("size", ctypes.c_uint32),
                ("returned", ctypes.POINTER(ctypes.c_uint8)), ("return_capacity", ctypes.c_uint64),
                ("return_size", ctypes.c_uint64), ("effects", ctypes.POINTER(ctypes.c_uint8)),
                ("effects_capacity", ctypes.c_uint64), ("effects_size", ctypes.c_uint64),
                ("flags", ctypes.c_uint32), ("status", ctypes.c_uint32)]


def _vector(element, values):
    width = int(element[1:])
    return {"element": element, "lanes": [f"0x{value & ((1 << width) - 1):0{width // 4}x}" for value in values]}


def _scalar(element, value):
    return {"element": element, "bits": _vector(element, [value])["lanes"][0]}


def _signed(value, width):
    return value - (1 << width) if value & (1 << (width - 1)) else value


def _boundaries(width):
    sign, mask = 1 << (width - 1), (1 << width) - 1
    return tuple(dict.fromkeys((0, 1, 2, sign - 2, sign - 1, sign, sign + 1,
                                mask - 1, mask, int("aa" * (width // 8), 16),
                                int("55" * (width // 8), 16))))


def _fp_patterns(width):
    sign = 1 << (width - 1)
    exponent = (0xff if width == 32 else 0x7ff) << (23 if width == 32 else 52)
    quiet = 1 << (22 if width == 32 else 51)
    return (0, sign, 1, sign | 1, exponent - 1, exponent, sign | exponent,
            exponent | 1, sign | exponent | 1, exponent | quiet | 0x42,
            sign | exponent | quiet | 0x123, sign - 1)


def _fp_bits(value, width):
    return int.from_bytes(struct.pack("<f" if width == 32 else "<d", value), "little")


@unittest.skipUnless(sys.platform == "linux" and platform.machine().lower() in ROLES and shutil.which("clang"),
                     "native adversarial oracles require a supported Linux C target and Clang")
class NativeAdversarialTests(unittest.TestCase):
    native_calls = 0
    native_lane_checks = 0
    native_case_stems = set()

    @classmethod
    def setUpClass(cls):
        cls.native_calls = cls.native_lane_checks = 0
        cls.native_case_stems = set()
        cls.temporary = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temporary.cleanup)
        directory = Path(cls.temporary.name)
        cls.role = ROLES[platform.machine().lower()]
        cases = load_case_definitions(ROOT / "10_official_suite/cases",
                                      isa_registry=load_isa_registry(ROOT / "contracts/isa-registry.json"))
        cls.case_definitions_sha256 = cases.sha256
        cls.by_stem = {case.source_path.stem: case for case in cases}
        generated = generate_native_suite(cases, cls.role, directory, project_root=ROOT)
        library_path = directory / "adversarial.so"
        subprocess.run(["clang", *native_compile_flags(cls.role), "-fPIC", "-shared", "-I" + str(ROOT),
                        "-I" + str(ROOT / "include"), str(generated),
                        str(ROOT / "adapters/common/native_suite_runtime.c"),
                        *(str(ROOT / "10_official_suite" / cls.role / (block + ".c")) for block in BLOCKS),
                        "-lm", "-o", str(library_path)], check=True, capture_output=True, timeout=300)
        cls.library = ctypes.CDLL(str(library_path))
        cls.functions = {}
        for stem, case in cls.by_stem.items():
            function = getattr(cls.library, case.data[cls.role]["symbol"])
            function.argtypes = [ctypes.POINTER(_Input), ctypes.POINTER(_Output)]
            function.restype = ctypes.c_int
            cls.functions[stem] = function

    def call(self, stem, operands=None, *, immediates=None, buffers=None, raw=None,
             return_capacity=2048, effects_capacity=4096, mutate=None):
        payload = {"operands": operands or {}, "immediates": immediates or {}, "buffers": buffers or {}}
        encoded = dump_bytes(payload) if raw is None else raw
        arguments = (ctypes.c_uint8 * len(encoded)).from_buffer_copy(encoded)
        # Guard bytes are outside the advertised capacity, including capacity=0.
        returned = (ctypes.c_uint8 * (return_capacity + 16))()
        effects = (ctypes.c_uint8 * (effects_capacity + 16))()
        ctypes.memset(returned, 0xa5, len(returned))
        ctypes.memset(effects, 0x5a, len(effects))
        input_record = _Input(1, ctypes.sizeof(_Input), _Bytes(arguments, len(encoded)), 0, 0)
        output = _Output(1, ctypes.sizeof(_Output), returned, return_capacity, 0,
                         effects, effects_capacity, 0, 0, 0)
        if mutate:
            mutate(input_record, output)
        code = self.functions[stem](ctypes.byref(input_record), ctypes.byref(output))
        type(self).native_calls += 1
        type(self).native_case_stems.add(stem)
        self.assertEqual(bytes(returned[return_capacity:]), b"\xa5" * 16, "return guard corrupted")
        self.assertEqual(bytes(effects[effects_capacity:]), b"\x5a" * 16, "memory-effects guard corrupted")
        observed = None
        if code == 0 and output.status == 0:
            observed = {"return": json.loads(bytes(returned[:output.return_size])),
                        "buffers": json.loads(bytes(effects[:output.effects_size]))}
        elif code == 2 or code == 1 or output.status == 2:
            self.assertEqual(bytes(returned), b"\xa5" * len(returned), "rejected call wrote a return")
            self.assertEqual(bytes(effects), b"\x5a" * len(effects), "rejected call wrote memory effects")
        return code, output, observed

    def assert_result(self, stem, operands, expected, **kwargs):
        code, output, observed = self.call(stem, operands, **kwargs)
        self.assertEqual((code, output.status), (0, 0), stem)
        self.assertEqual(observed["return"], expected, (stem, operands, kwargs))
        type(self).native_lane_checks += len(expected.get("lanes", [0])) if expected else 0
        return observed

    def integer_matrix(self, operation, element, exhaustive=False):
        width, count = int(element[1:]), 128 // int(element[1:])
        mask = (1 << width) - 1
        values = range(256) if exhaustive else _boundaries(width)
        pairs = list(product(values, repeat=2))
        for start in range(0, len(pairs), count):
            batch = pairs[start:start + count]
            batch += [batch[-1]] * (count - len(batch))
            a, b = [pair[0] for pair in batch], [pair[1] for pair in batch]
            expected = []
            for left, right in batch:
                signed_left, signed_right = _signed(left, width), _signed(right, width)
                if operation in ("add", "sub"):
                    result = left + right if operation == "add" else left - right
                elif operation in ("adds", "subs"):
                    x, y = (signed_left, signed_right) if element[0] == "i" else (left, right)
                    low, high = (-(1 << (width - 1)), (1 << (width - 1)) - 1) if element[0] == "i" else (0, mask)
                    result = min(high, max(low, x + y if operation == "adds" else x - y))
                elif operation == "avg":
                    result = (left + right + 1) // 2
                elif operation in ("min", "max"):
                    x, y = (signed_left, signed_right) if element[0] == "i" else (left, right)
                    result = min(x, y) if operation == "min" else max(x, y)
                else:
                    predicate = {"cmpeq": left == right, "cmpgt": signed_left > signed_right,
                                 "cmplt": signed_left < signed_right}[operation]
                    result = mask if predicate else 0
                expected.append(result & mask)
            with self.subTest(pair_start=start):
                self.assert_result(f"{operation}-{element}x{count}", {"a": _vector(element, a), "b": _vector(element, b)},
                                   _vector(element, expected))

    def bitwise_matrix(self, operation, element):
        width, mask = int(element[1:]), (1 << 128) - 1
        count, lane_mask = 128 // width, (1 << width) - 1
        def lanes(word):
            return _vector(element, [(word >> (i * width)) & lane_mask for i in range(count)])
        for bit, partner in product(range(128), range(4)):
            a = 1 << bit
            b = (a, mask ^ a, 1 << ((bit + 37) % 128), int("a55a" * 8, 16))[partner]
            expected = {"and": a & b, "or": a | b, "xor": a ^ b, "andnot": (~a) & b}[operation]
            with self.subTest(bit=bit, partner=partner):
                self.assert_result(f"{operation}-{element}x{count}", {"a": lanes(a), "b": lanes(b)}, lanes(expected))

    def shift_matrix(self, operation, width, immediate):
        element, count = f"i{width}", 128 // width
        stem = f"{operation}{'i' if immediate else ''}-{element}x{count}"
        counts = self.by_stem[stem].data["immediates"]["imm8"]["values"] if immediate else (
            0, 1, width - 1, width, width + 1, 127, 128, 255, 256, 1 << 32, 1 << 63, (1 << 64) - 1)
        patterns = _boundaries(width) + tuple(1 << i for i in range(width))
        for shift, index in product(counts, range(len(patterns))):
            a = [patterns[(index + lane) % len(patterns)] for lane in range(count)]
            if operation == "sra":
                expected = [_signed(value, width) >> min(shift, width - 1) for value in a]
            elif shift >= width:
                expected = [0] * count
            else:
                expected = [value << shift if operation == "sll" else value >> shift for value in a]
            operands = {"a": _vector(element, a)}
            options = {"immediates": {"imm8": shift}} if immediate else {}
            if not immediate:
                # SSE uses the complete low 64 bits and ignores the high 64 bits.
                operands["count"] = _vector("u64", [shift, (1 << 64) - 1 - index])
            with self.subTest(shift=shift, pattern=index):
                self.assert_result(stem, operands, _vector(element, expected), **options)

    def rearrangement(self, stem):
        case = self.by_stem[stem]
        args, returned = case.signature["arguments"], case.signature["return"]
        for index in range(24):
            operands, raw_values = {}, {}
            for argument in args:
                if argument["type"] != "vector":
                    continue
                element, count = argument["element"], argument["lanes"]
                patterns = _fp_patterns(int(element[1:])) if element.startswith("f") else _boundaries(int(element[1:]))
                values = [patterns[(index + lane * 3 + (5 if argument["name"] == "b" else 0)) % len(patterns)]
                          for lane in range(count)]
                operands[argument["name"]] = _vector(element, values)
                raw_values[argument["name"]] = values
            a, b = raw_values["a"], raw_values.get("b")
            immediates = case.data.get("immediates", {}).get("imm8", {}).get("values", [None])
            for immediate in immediates:
                if stem.startswith("cast-"):
                    width = int(args[0]["element"][1:]) // 8
                    raw = b"".join(value.to_bytes(width, "little") for value in a)
                    target_width = int(returned["element"][1:]) // 8
                    expected = [int.from_bytes(raw[i:i + target_width], "little") for i in range(0, 16, target_width)]
                elif stem.startswith("unpack"):
                    half = len(a) // 2
                    offset = half if stem.startswith("unpackhi") else 0
                    expected = [value for pair in zip(a[offset:offset + half], b[offset:offset + half]) for value in pair]
                elif stem == "shuffle-f64x2":
                    expected = [a[immediate & 1], b[(immediate >> 1) & 1]]
                elif stem == "shuffle-f32x4":
                    expected = [a[(immediate >> (2 * lane)) & 3] if lane < 2 else b[(immediate >> (2 * lane)) & 3]
                                for lane in range(4)]
                elif stem.startswith("shufflehi"):
                    expected = a[:4] + [a[4 + ((immediate >> (2 * lane)) & 3)] for lane in range(4)]
                elif stem.startswith("shufflelo"):
                    expected = [a[(immediate >> (2 * lane)) & 3] for lane in range(4)] + a[4:]
                elif stem.startswith("shuffle"):
                    expected = [a[(immediate >> (2 * lane)) & 3] for lane in range(4)]
                elif stem.startswith("slli-bytes"):
                    expected = ([0] * immediate + a[:16 - immediate]) if immediate < 16 else [0] * 16
                elif stem.startswith("srli-bytes"):
                    expected = (a[immediate:] + [0] * immediate) if immediate < 16 else [0] * 16
                elif stem == "movehl-f32x4":
                    expected = b[2:] + a[2:]
                elif stem == "movelh-f32x4":
                    expected = a[:2] + b[:2]
                elif stem == "move-i64x2":
                    expected = [a[0], 0]
                else:
                    expected = [b[0]] + a[1:]
                with self.subTest(pattern=index, immediate=immediate):
                    self.assert_result(stem, operands, _vector(returned["element"], expected),
                                       immediates={} if immediate is None else {"imm8": immediate})

    def pack_matrix(self, stem):
        case = self.by_stem[stem]
        element = case.signature["arguments"][0]["element"]
        width, count = int(element[1:]), case.signature["arguments"][0]["lanes"]
        target = case.signature["return"]["element"]
        target_width = int(target[1:])
        low, high = (0, (1 << target_width) - 1) if target.startswith("u") else (-(1 << (target_width - 1)), (1 << (target_width - 1)) - 1)
        boundaries = tuple(dict.fromkeys((-(1 << (width - 1)), -1, 0, 1, low - 1, low, low + 1,
                                          high - 1, high, high + 1, (1 << (width - 1)) - 1)))
        for i, j in product(range(len(boundaries)), repeat=2):
            a = [boundaries[(i + lane) % len(boundaries)] for lane in range(count)]
            b = [boundaries[(j + lane) % len(boundaries)] for lane in range(count)]
            with self.subTest(left=i, right=j):
                self.assert_result(stem, {"a": _vector(element, a), "b": _vector(element, b)},
                                   _vector(target, [min(high, max(low, value)) for value in a + b]))

    def multiply_matrix(self, stem):
        case = self.by_stem[stem]
        element = case.signature["arguments"][0]["element"]
        width, count = int(element[1:]), case.signature["arguments"][0]["lanes"]
        boundaries = _boundaries(width)
        for i, j in product(range(len(boundaries)), repeat=2):
            a = [boundaries[(i + lane) % len(boundaries)] for lane in range(count)]
            b = [boundaries[(j + lane * 3) % len(boundaries)] for lane in range(count)]
            if stem == "mul-u32x4":
                expected = [a[lane] * b[lane] for lane in (0, 2)]
            elif stem == "madd-i16x8":
                products = [_signed(x, width) * _signed(y, width) for x, y in zip(a, b)]
                expected = [products[lane] + products[lane + 1] for lane in range(0, 8, 2)]
            elif stem == "sad-u8x16":
                expected = [sum(abs(a[lane] - b[lane]) for lane in range(start, start + 8)) for start in (0, 8)]
            elif stem.startswith("mulhi"):
                products = [(x if element[0] == "u" else _signed(x, width)) *
                            (y if element[0] == "u" else _signed(y, width)) for x, y in zip(a, b)]
                expected = [value >> width for value in products]
            else:
                expected = [x * y for x, y in zip(a, b)]
            with self.subTest(left=i, right=j):
                self.assert_result(stem, {"a": _vector(element, a), "b": _vector(element, b)},
                                   _vector(case.signature["return"]["element"], expected))

    def set_matrix(self, stem):
        case = self.by_stem[stem]
        returned, args = case.signature["return"], case.signature["arguments"]
        element, count = returned["element"], returned["lanes"]
        patterns = _fp_patterns(int(element[1:])) if element.startswith("f") else _boundaries(int(element[1:]))
        for index in range(len(patterns)):
            operands = {argument["name"]: _scalar(argument["element"], patterns[(index + n) % len(patterns)])
                        for n, argument in enumerate(args)}
            if stem.startswith("set1-"):
                expected = [patterns[index]] * count
            elif stem.startswith(("set-scalar-", "cvtsi32-", "cvtsi64-")):
                expected = [patterns[index]] + [0] * (count - 1)
            elif args[0]["name"].startswith("lane"):
                expected = [int(operands[f"lane{lane}"]["bits"], 16) for lane in range(count)]
            else:
                expected = [int(operands[args[1]["name"]]["bits"], 16), int(operands[args[0]["name"]]["bits"], 16)] if stem.startswith("set-") else [int(operands[argument["name"]]["bits"], 16) for argument in args]
            with self.subTest(pattern=index):
                self.assert_result(stem, operands, _vector(element, expected))

    def movemask_matrix(self, element):
        width, count = int(element[1:]), 128 // int(element[1:])
        # Every sign assignment for 2/4 lanes; all one-hot and complement signs
        # plus alternating signs for 16 lanes. Low bits must never leak into mask.
        masks = range(1 << count) if count <= 4 else (0, 0xffff, 0x5555, 0xaaaa, *(1 << i for i in range(16)), *(0xffff ^ (1 << i) for i in range(16)))
        for mask, low in product(masks, (0, 1, (1 << (width - 1)) - 1)):
            values = [low | (((mask >> lane) & 1) << (width - 1)) for lane in range(count)]
            with self.subTest(mask=mask, low=low):
                self.assert_result(f"movemask-{element}x{count}", {"a": _vector(element, values)}, _scalar("i32", mask))

    def memory_matrix(self, stem, exact_fit):
        case = self.by_stem[stem]
        args = case.signature["arguments"]
        pointer = next(argument["name"] for argument in args if argument["type"] == "pointer")
        contract = case.data["memory_contract"][pointer]
        store = bool(contract["write_ranges"])
        value_contract = next((argument for argument in args if argument["type"] == "vector"), case.signature["return"])
        element, count = value_contract["element"], value_contract["lanes"]
        width = int(element[1:]) // 8
        extent = max(region["offset"] + region["byte_length"] for region in contract["read_ranges"] + contract["write_ranges"])
        offsets = range(32) if contract["required_alignment"] == 1 else (0, 16, 32, 48)
        patterns = _fp_patterns(width * 8) if element.startswith("f") else _boundaries(width * 8)
        for offset, pattern in product(offsets, range(4)):
            values = [patterns[(pattern + lane * 3) % len(patterns)] for lane in range(count)]
            before = bytes((i * 29 + pattern + 17) & 255 for i in range(offset + extent + (0 if exact_fit else 19)))
            operands = {pointer: {"buffer": "selected", "offset": offset}}
            if store or len(args) > 1:
                operands["a"] = _vector(element, values)
            after = before
            if store:
                selected = values[count // 2:] if stem.startswith("storeh") else list(reversed(values)) if stem.startswith("storer") else values
                payload = b"".join(value.to_bytes(width, "little") for value in selected)[:extent]
                after = before[:offset] + payload + before[offset + extent:]
                expected = None
            else:
                loaded = [int.from_bytes(before[i:i + width], "little") for i in range(offset, offset + extent, width)]
                if stem.startswith("loadr"):
                    expected = list(reversed(loaded))
                elif stem.startswith("load1"):
                    expected = loaded * count
                elif stem == "loadh-f64x2":
                    expected = [values[0], loaded[0]]
                elif stem == "loadl-f64x2":
                    expected = [loaded[0], values[1]]
                elif stem == "loadh-f32x4":
                    expected = values[:2] + loaded
                elif stem == "loadl-f32x4":
                    expected = loaded + values[2:]
                else:
                    expected = loaded + [0] * (count - len(loaded))
                expected = _vector(case.signature["return"]["element"], expected)
            buffers = {"selected": {"alignment": contract["required_alignment"], "bytes": "0x" + before.hex()},
                       "unrelated": {"alignment": 1, "bytes": "0x00ff807f12"},
                       "empty": {"alignment": 4096, "bytes": "0x"}}
            original = copy.deepcopy((operands, buffers))
            with self.subTest(offset=offset, pattern=pattern):
                observed = self.assert_result(stem, operands, expected, buffers=buffers)
                self.assertEqual(observed["buffers"], {
                    "selected": {"byte_offset": 0, "bytes": "0x" + after.hex()},
                    "unrelated": {"byte_offset": 0, "bytes": "0x00ff807f12"},
                    "empty": {"byte_offset": 0, "bytes": "0x"}})
                self.assertEqual((operands, buffers), original, "call mutated the Python input")

    def zero_constructor(self, shape):
        element, lanes = shape.split("x")
        stem = "setzero-" + shape
        self.assert_result(stem, {}, _vector(element, [0] * int(lanes)))
        code, output, _ = self.call(stem, {"unexpected": _scalar("i32", 0)})
        self.assertEqual((code, output.status), (0, 2))

    def aligned_rejections(self, stem):
        case = self.by_stem[stem]
        pointer = next(arg["name"] for arg in case.signature["arguments"] if arg["type"] == "pointer")
        operands = {pointer: {"buffer": "storage", "offset": 0}}
        for arg in case.signature["arguments"]:
            if arg["type"] == "vector":
                operands[arg["name"]] = _vector(arg["element"], [0] * arg["lanes"])
        for alignment, offset in product((1, 2, 4, 8, 16, 32), range(16)):
            if alignment >= 16 and offset == 0:
                continue
            buffers = {"storage": {"alignment": alignment, "bytes": "0x" + "a5" * 32}}
            operands[pointer]["offset"] = offset
            with self.subTest(alignment=alignment, offset=offset):
                code, output, _ = self.call(stem, operands, buffers=buffers)
                self.assertEqual((code, output.status, output.return_size, output.effects_size), (0, 2, 0, 0))

    def finite_scalar_matrix(self, width, operation):
        element, count = f"f{width}", 128 // width
        inactive = _fp_patterns(width)
        # These exact powers of two and integers need no host floating oracle.
        calculations = {"add": (4, 2, 6), "sub": (4, 2, 2), "mul": (4, 2, 8), "div": (4, 2, 2),
                        "sqrt": (4, None, 2), "min": (4, 2, 2), "max": (4, 2, 4)}
        x, y, result = calculations[operation]
        for index in range(len(inactive)):
            a = [_fp_bits(x, width)] + [inactive[(index + lane) % len(inactive)] for lane in range(1, count)]
            operands = {"a": _vector(element, a)}
            if y is not None:
                b = [_fp_bits(y, width)] + [inactive[(index + lane + 5) % len(inactive)] for lane in range(1, count)]
                operands["b"] = _vector(element, b)
            with self.subTest(inactive_pattern=index):
                self.assert_result(f"{operation}-scalar-{element}x{count}", operands,
                                   _vector(element, [_fp_bits(result, width)] + a[1:]))

    def test_insert_and_extract_all_lanes_and_truncation(self):
        for lane, value in product(range(8), (0, 1, 0x7fff, 0x8000, 0xffff, 0x10000, 0x12345678, 0xffffffff)):
            a = [0x8001 + 17 * n for n in range(8)]
            with self.subTest(lane=lane, value=value):
                self.assert_result("extract-i16x8", {"a": _vector("i16", a)}, _scalar("u32", a[lane]), immediates={"imm8": lane})
                expected = a.copy()
                expected[lane] = value & 0xffff
                self.assert_result("insert-i16x8", {"a": _vector("i16", a), "value": _scalar("i32", value)},
                                   _vector("i16", expected), immediates={"imm8": lane})

    def test_scalar_extraction_preserves_every_low_bit(self):
        for width in (32, 64):
            for value in _boundaries(width) + tuple(1 << bit for bit in range(width)):
                with self.subTest(width=width, value=value):
                    self.assert_result(f"cvtsi128-i{width}x{128 // width}",
                                       {"a": _vector(f"i{width}", [value] + [(1 << width) - 1] * (128 // width - 1))},
                                       _scalar(f"i{width}", value))

    def test_exact_capacity_and_each_one_byte_short_boundary(self):
        operands = {"a": _vector("i32", [1, 2, 3, 4]), "b": _vector("i32", [5, 6, 7, 8])}
        code, output, _ = self.call("add-i32x4", operands, return_capacity=0, effects_capacity=0)
        self.assertEqual(code, 2)
        required_return, required_effects = output.return_size, output.effects_size
        for a, b in ((required_return - 1, required_effects), (required_return, required_effects - 1),
                     (0, required_effects), (required_return, 0)):
            with self.subTest(return_capacity=a, effects_capacity=b):
                code, output, _ = self.call("add-i32x4", operands, return_capacity=a, effects_capacity=b)
                self.assertEqual(code, 2)
                self.assertEqual((output.return_size, output.effects_size), (required_return, required_effects))
        self.assert_result("add-i32x4", operands, _vector("i32", [6, 8, 10, 12]),
                           return_capacity=required_return, effects_capacity=required_effects)

    def test_store_capacity_negotiation_precedes_all_writes(self):
        operands = {"destination": {"buffer": "storage", "offset": 7},
                    "a": _vector("f64", [0x7ff0000000000001, 0x8000000000000000])}
        buffers = {"storage": {"alignment": 1, "bytes": "0x" + "a5" * 23}}
        code, output, _ = self.call("storeu-f64x2", operands, buffers=buffers,
                                    return_capacity=0, effects_capacity=0)
        self.assertEqual(code, 2)
        required_return, required_effects = output.return_size, output.effects_size
        for a, b in ((required_return - 1, required_effects), (required_return, required_effects - 1)):
            with self.subTest(return_capacity=a, effects_capacity=b):
                code, output, _ = self.call("storeu-f64x2", operands, buffers=buffers,
                                            return_capacity=a, effects_capacity=b)
                self.assertEqual(code, 2)
                self.assertEqual((output.return_size, output.effects_size), (required_return, required_effects))
        observed = self.assert_result("storeu-f64x2", operands, None, buffers=buffers,
                                      return_capacity=required_return, effects_capacity=required_effects)
        expected = "0x" + "a5" * 7 + "010000000000f07f0000000000000080"
        self.assertEqual(observed["buffers"]["storage"], {"byte_offset": 0, "bytes": expected})

    def test_all_sixteen_buffers_are_preserved_by_readonly_load(self):
        buffers = {f"storage{i}": {"alignment": 1 << (i % 13), "bytes": "0x" + f"{i:02x}" * 16}
                   for i in range(16)}
        # Reversing JSON member order must still resolve the intended buffer.
        raw = json.dumps({"immediates": {}, "buffers": dict(reversed(list(buffers.items()))),
                          "operands": {"source": {"offset": 0, "buffer": "storage15"}}}, indent=2).encode()
        observed = self.assert_result("loadu-i32x4", {}, _vector("i32", [0x0f0f0f0f] * 4), raw=raw)
        self.assertEqual(observed["buffers"], {name: {"byte_offset": 0, "bytes": value["bytes"]}
                                                for name, value in buffers.items()})

    def test_every_immediate_contract_rejects_out_of_domain_values(self):
        for stem, case in sorted(self.by_stem.items()):
            immediate_contracts = case.data.get("immediates", {})
            if not immediate_contracts:
                continue
            operands = {}
            for argument in case.signature["arguments"]:
                if argument["type"] == "vector":
                    operands[argument["name"]] = _vector(argument["element"], [0] * argument["lanes"])
                elif argument["type"] == "scalar":
                    operands[argument["name"]] = _scalar(argument["element"], 0)
            allowed = immediate_contracts["imm8"]["values"]
            invalid = next(value for value in range(256) if value not in allowed)
            for value in (-1, invalid, 256, (1 << 63) - 1):
                with self.subTest(stem=stem, immediate=value):
                    # Deliberately bypass canonical JSON's safe-integer check:
                    # this is a malformed wire-input test of the C decoder.
                    raw = json.dumps({"operands": operands, "immediates": {"imm8": value}, "buffers": {}}).encode()
                    code, output, _ = self.call(stem, raw=raw)
                    self.assertEqual((code, output.status, output.return_size, output.effects_size), (0, 2, 0, 0))

    def test_all_abi_header_and_null_pointer_failures_are_nonwriting(self):
        operands = {"a": _vector("i32", [1, 2, 3, 4]), "b": _vector("i32", [5, 6, 7, 8])}
        mutations = [lambda i, o: setattr(i, "version", 0), lambda i, o: setattr(i, "version", 2),
                     lambda i, o: setattr(o, "version", 0), lambda i, o: setattr(o, "version", 2),
                     lambda i, o: setattr(i, "size", 0), lambda i, o: setattr(i, "size", ctypes.sizeof(_Input) - 1),
                     lambda i, o: setattr(i, "size", ctypes.sizeof(_Input) + 1),
                     lambda i, o: setattr(o, "size", ctypes.sizeof(_Output) - 1),
                     lambda i, o: setattr(o, "size", ctypes.sizeof(_Output) + 1),
                     lambda i, o: setattr(i.arguments, "data", ctypes.POINTER(ctypes.c_uint8)()),
                     lambda i, o: setattr(o, "returned", ctypes.POINTER(ctypes.c_uint8)()),
                     lambda i, o: setattr(o, "effects", ctypes.POINTER(ctypes.c_uint8)())]
        for index, mutation in enumerate(mutations):
            with self.subTest(header=index):
                self.assertEqual(self.call("add-i32x4", operands, mutate=mutation)[0], 1)
        self.assertEqual(self.functions["add-i32x4"](None, None), 1)

    def test_input_size_limits_and_null_zero_capacity_negotiation(self):
        operands = {"a": _vector("i32", [1, 2, 3, 4]), "b": _vector("i32", [5, 6, 7, 8])}
        for size in (0, (1 << 20) + 1, (1 << 64) - 1):
            with self.subTest(encoded_size=size):
                code, output, _ = self.call("add-i32x4", operands,
                                            mutate=lambda i, o: setattr(i.arguments, "size", size))
                self.assertEqual((code, output.status), (0, 2))
        def null_output(input_record, output):
            output.returned = ctypes.POINTER(ctypes.c_uint8)()
            output.effects = ctypes.POINTER(ctypes.c_uint8)()
        code, output, _ = self.call("add-i32x4", operands, return_capacity=0,
                                    effects_capacity=0, mutate=null_output)
        self.assertEqual(code, 2)
        self.assertGreater(output.return_size, 0)
        self.assertEqual(output.effects_size, 2)

    def test_malformed_json_and_typed_operands_are_nonwriting(self):
        operands = {"a": _vector("i32", [1, 2, 3, 4]), "b": _vector("i32", [5, 6, 7, 8])}
        good = dump_bytes({"operands": operands, "immediates": {}, "buffers": {}})
        raws = [b"", b" ", b"null", b"[]", b"{}", good[:-1], good + b"x", good + good,
                good.replace(b'"buffers":{}', b'"buffers":{},"buffers":{}'),
                good.replace(b'"element":"i32"', b'"element":"i32","element":"i32"', 1),
                good.replace(b'"a":{', b'"a":null,"unused":{', 1),
                good.replace(b'"i32"', b'"i\\u0033\\u0032"', 1),
                good.replace(b'0x00000001', b'0X00000001'), good.replace(b'0x00000001', b'0x0000000A'),
                good.replace(b'0x00000001', b'0x000001'), good.replace(b'0x00000001', b'0x000000001'),
                good.replace(b'0x00000001', b'-000000001'), good.replace(b'0x00000001', b'0x0000000g'),
                good.replace(b'"i32"', b'"u32"', 1), good.replace(b'"lanes":', b'"bits":', 1),
                good.replace(b'["0x00000001",', b'['), good.replace(b'"0x00000004"]', b'"0x00000004","0x00000005"]'),
                b'[' * 34 + b'0' + b']' * 34]
        for index, raw in enumerate(raws):
            with self.subTest(invalid_json=index):
                code, output, _ = self.call("add-i32x4", raw=raw)
                self.assertEqual((code, output.status, output.return_size, output.effects_size), (0, 2, 0, 0))
        for field, value in product(("rounding", "fp_mode"), (4, 255, 0xffffffff)):
            with self.subTest(field=field, value=value):
                code, output, _ = self.call("add-i32x4", operands, mutate=lambda i, o: setattr(i, field, value))
                self.assertEqual((code, output.status), (0, 2))

    def test_pointer_bounds_alignment_buffer_limits_and_names(self):
        operands = {"source": {"buffer": "storage", "offset": 0}}
        buffers = {"storage": {"alignment": 16, "bytes": "0x" + "a5" * 32}}
        bad_buffers = []
        for alignment in (0, 3, -1, 8192):
            item = copy.deepcopy(buffers)
            item["storage"]["alignment"] = alignment
            bad_buffers.append(item)
        for name in ("", "0bad", "with-dash", "space name"):
            bad_buffers.append({name: buffers["storage"]})
        bad_buffers.extend(({}, {f"buffer{i}": buffers["storage"] for i in range(17)},
                            {"storage": {"alignment": 16, "bytes": "0x" + "aa" * 65537}}))
        for index, bad in enumerate(bad_buffers):
            with self.subTest(buffer=index):
                self.assertEqual(self.call("loadu-i32x4", operands, buffers=bad)[1].status, 2)
        for offset in (-1, 17, 32, 33, 1 << 63):
            with self.subTest(offset=offset):
                bad = {"source": {"buffer": "storage", "offset": offset}}
                raw = json.dumps({"operands": bad, "buffers": buffers, "immediates": {}}).encode()
                self.assertEqual(self.call("loadu-i32x4", raw=raw)[1].status, 2)
        for offset in (1, 7, 15, 17):
            with self.subTest(misaligned_offset=offset):
                bad = {"source": {"buffer": "storage", "offset": offset}}
                self.assertEqual(self.call("loadr-f64x2", bad, buffers=buffers)[1].status, 2)
        for alignment in (1, 2, 4, 8):
            with self.subTest(insufficient_alignment=alignment):
                bad = {"storage": {"alignment": alignment, "bytes": buffers["storage"]["bytes"]}}
                self.assertEqual(self.call("loadr-f64x2", operands, buffers=bad)[1].status, 2)
        maximum = {"storage": {"alignment": 4096, "bytes": "0x" + "a5" * 65536}}
        observed = self.assert_result("loadu-i32x4", {"source": {"buffer": "storage", "offset": 65520}},
                                      _vector("i32", [0xa5a5a5a5] * 4), buffers=maximum, effects_capacity=132000)
        self.assertEqual(observed["buffers"]["storage"]["bytes"], maximum["storage"]["bytes"])


def _install(name, method, *args):
    def test(self):
        getattr(self, method)(*args)
    test.__name__ = "test_" + name.replace("-", "_")
    setattr(NativeAdversarialTests, test.__name__, test)


for _width in (8, 16, 32, 64):
    for _operation in ("add", "sub"):
        _install(f"{_operation}_i{_width}_{'all_byte_pairs' if _width == 8 else 'boundary_pairs'}",
                 "integer_matrix", _operation, f"i{_width}", _width == 8)
for _width in (8, 16):
    for _operation, _kind in product(("adds", "subs"), ("i", "u")):
        _install(f"{_operation}_{_kind}{_width}_saturation", "integer_matrix", _operation, f"{_kind}{_width}", _width == 8)
    _install(f"avg_u{_width}_rounded_up", "integer_matrix", "avg", f"u{_width}", _width == 8)
for _width in (8, 16, 32):
    for _operation in ("cmpeq", "cmpgt", "cmplt"):
        _install(f"{_operation}_i{_width}_signed", "integer_matrix", _operation, f"i{_width}", _width == 8)
for _operation, _element in product(("min", "max"), ("u8", "i16")):
    _install(f"{_operation}_{_element}_signedness", "integer_matrix", _operation, _element, _element == "u8")
for _operation, _element in product(("and", "or", "xor", "andnot"), ("i32", "f32", "f64")):
    _install(f"{_operation}_{_element}_all_128_bit_positions", "bitwise_matrix", _operation, _element)
for _width, _operation, _immediate in product((16, 32, 64), ("sll", "srl", "sra"), (False, True)):
    if _width != 64 or _operation != "sra":
        _install(f"{_operation}_{_width}_{'immediate' if _immediate else 'full_64bit_count'}", "shift_matrix", _operation, _width, _immediate)
for _stem in ("cast-f32x4-f64x2", "cast-f32x4-i32x4", "cast-f64x2-f32x4", "cast-f64x2-i64x2", "cast-i32x4-f32x4", "cast-i64x2-f64x2",
              "shuffle-f32x4", "shuffle-f64x2", "shuffle-i32x4", "shufflehi-i16x8", "shufflelo-i16x8",
              "slli-bytes-u8x16", "srli-bytes-u8x16", "move-f32x4", "move-f64x2", "move-i64x2", "movehl-f32x4", "movelh-f32x4",
              *(f"unpack{half}-{shape}" for half, shape in product(("lo", "hi"), ("i8x16", "i16x8", "i32x4", "i64x2", "f32x4", "f64x2")))):
    _install(_stem + "_raw_lane_order", "rearrangement", _stem)
for _stem in ("packs-i16x8", "packs-i32x4", "packus-i16x8"):
    _install(_stem + "_all_saturation_edges", "pack_matrix", _stem)
for _stem in ("mullo-i16x8", "mulhi-i16x8", "mulhi-u16x8", "madd-i16x8", "mul-u32x4", "sad-u8x16"):
    _install(_stem + "_overflow_and_lane_grouping", "multiply_matrix", _stem)
for _shape in ("i8x16", "i16x8", "i32x4", "i64x2", "f32x4", "f64x2"):
    for _operation in ("set", "set1", "setr"):
        if _operation != "setr" or _shape != "i64x2":
            _install(f"{_operation}-{_shape}_raw_bits", "set_matrix", f"{_operation}-{_shape}")
for _stem in ("set-scalar-f32x4", "set-scalar-f64x2", "cvtsi32-i32x4", "cvtsi64-i64x2"):
    _install(_stem + "_zero_inactive_lanes", "set_matrix", _stem)
for _element in ("i8", "f32", "f64"):
    _install("movemask_" + _element + "_sign_positions", "movemask_matrix", _element)
for _stem in ("load-scalar-f32x4", "load-scalar-f64x2", "load1-f32x4", "load1-f64x2", "loadh-f64x2", "loadl-f64x2", "loadl-i64x2",
              "loadr-f32x4", "loadr-f64x2", "loadu-f32x4", "loadu-f64x2", "loadu-i32x4",
              "store-scalar-f32x4", "store-scalar-f64x2", "storeh-f64x2", "storel-i64x2", "storer-f32x4", "storer-f64x2",
              "storeu-f32x4", "storeu-f64x2", "storeu-i32x4"):
    for _exact_fit in (False, True):
        _install(_stem + ("_exact_fit" if _exact_fit else "_guard_bytes"), "memory_matrix", _stem, _exact_fit)
for _width, _operation in product((32, 64), ("add", "sub", "mul", "div", "sqrt", "min", "max")):
    _install(f"{_operation}_scalar_f{_width}_inactive_nan_bits", "finite_scalar_matrix", _width, _operation)
for _shape in ("f32x4", "f64x2", "i32x4"):
    _install("setzero_" + _shape + "_no_operands", "zero_constructor", _shape)
    for _operation in ("load", "store"):
        _stem = _operation + "-" + _shape
        _install(_stem + "_alignment_rejection", "aligned_rejections", _stem)
        for _exact_fit in (False, True):
            _install(_stem + ("_exact_fit" if _exact_fit else "_guard_bytes"), "memory_matrix", _stem, _exact_fit)
for _stem in ("loadh-f32x4", "loadl-f32x4", "storeh-f32x4", "storel-f32x4", "storel-f64x2"):
    for _exact_fit in (False, True):
        _install(_stem + ("_exact_fit" if _exact_fit else "_guard_bytes"), "memory_matrix", _stem, _exact_fit)


if __name__ == "__main__":
    unittest.main()
