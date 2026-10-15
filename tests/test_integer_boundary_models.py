"""Independent SSE2 integer oracles, including overflow and lane-order traps.

Expected values use unbounded Python integer arithmetic, explicit fixed-width
encoding, and byte-level memory operations. They never call the case generators,
comparators, or arithmetic helpers under test. Every input is validated before
its portable model executes; mismatches remain ordinary failing subtests.

Instruction semantics were checked against Intel's primary reference:
https://www.intel.com/content/dam/www/public/us/en/documents/manuals/64-ia-32-architectures-software-developer-vol-2b-manual.pdf
Relevant instructions include PADD/PSUB, PACKSS/PACKUS, PAVG, PMADDWD,
PMULHW/PMULHUW/PMULUDQ, PSLL/PSRL/PSRA, PSHUFD/HW/LW, and PUNPCK.
The original intrinsic signatures and extract/insert/movemask semantics are in:
https://www.intel.com/content/dam/develop/external/us/en/documents/18072-347603.pdf

Full imm8 coverage is an explicitly validated contract extension. The official
case packs keep their smaller immediate allowlists; they are never modified.
"""

from __future__ import annotations

import copy
from itertools import combinations, product
import unittest

from model_test_support import VerifiedModelMixin
from equilane.cases import validate_case_definition


def _vector(element, values):
    width = int(element[1:])
    return {
        "element": element,
        "lanes": [f"0x{value % (2 ** width):0{width // 4}x}" for value in values],
    }


def _scalar(element, value):
    width = int(element[1:])
    return {"element": element, "bits": f"0x{value % (2 ** width):0{width // 4}x}"}


def _signed(bits, width):
    return int.from_bytes(bits.to_bytes(width // 8, "little"), "little", signed=True)


def _boundaries(width):
    modulus = 2 ** width
    sign = modulus // 2
    # Both sides of zero, signed extrema, unsigned extrema, and dense patterns.
    return tuple(dict.fromkeys((
        0, 1, 2, sign - 1, sign, sign + 1, modulus - 2, modulus - 1,
        int("aa" * (width // 8), 16), int("55" * (width // 8), 16),
        int("33" * (width // 8), 16), int("cc" * (width // 8), 16),
    )))


def _pair_vectors(boundaries, lane_count):
    """Each ordered boundary pair occurs in lane zero with mixed other lanes."""

    for left_index, right_index in product(range(len(boundaries)), repeat=2):
        left = tuple(boundaries[(left_index + lane) % len(boundaries)]
                     for lane in range(lane_count))
        right = tuple(boundaries[(right_index + 5 * lane) % len(boundaries)]
                      for lane in range(lane_count))
        yield left_index, right_index, left, right


def _clamp(value, lower, upper):
    if value < lower:
        return lower
    if value > upper:
        return upper
    return value


def _shift(values, width, count, operation):
    if count >= width:
        return tuple(-1 if operation == "sra" and value >= 2 ** (width - 1) else 0
                     for value in values)
    factor = 2 ** count
    if operation == "sll":
        return tuple((value * factor) % (2 ** width) for value in values)
    if operation == "srl":
        return tuple(value // factor for value in values)
    return tuple(_signed(value, width) // factor for value in values)


class IntegerBoundaryModelTests(VerifiedModelMixin, unittest.TestCase):
    oracle_reference = "tests/test_integer_boundary_models.py"
    oracle_method = "independent_integer_reference"

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.oracle_reference_metadata = {
            "oracle": "Independent integer equations and byte-level reference operations",
            "source": "tests/test_integer_boundary_models.py",
            "case_definitions_sha256": cls.cases.sha256,
            "contract_extensions": ["full imm8 range 0..255"],
            "native_cross_host_evidence": False,
            "instruction_reference": "Intel SDM Volume 2B",
        }
        cls.immediate_cases = {}
        operations = ("slli", "srli", "srai")
        identifiers = [
            f"sse2.{operation}.i{width}x{128 // width}.imm8"
            for width in (16, 32, 64)
            for operation in operations if not (width == 64 and operation == "srai")
        ] + [
            "sse2.slli-bytes.u8x16.imm8", "sse2.srli-bytes.u8x16.imm8",
            "sse2.shuffle.i32x4.imm8", "sse2.shufflelo.i16x8.imm8",
            "sse2.shufflehi.i16x8.imm8",
        ]
        for case_id in identifiers:
            case = cls.cases.get(case_id)
            data = copy.deepcopy(case.data)
            data["immediates"]["imm8"]["values"] = list(range(256))
            cls.immediate_cases[case_id] = validate_case_definition(
                data, isa_registry=cls.isa, source_path=case.source_path,
            )

    def assert_immediate(self, case_id, operands, expected, immediate, *, family):
        original = self.cases.get(case_id)
        # Retain official scope for allowed inputs; label only additional inputs
        # as extensions instead of expanding the official coverage claim.
        allowed = original.data["immediates"]["imm8"]["values"]
        override = None if immediate in allowed else self.immediate_cases[case_id]
        self.assert_model(
            case_id, operands, expected, immediates={"imm8": immediate},
            case_override=override,
            contract_scope="validated_immediate_extension" if override else None,
            family=family,
        )

    def test_wrapping_add_sub_cross_every_signed_and_unsigned_boundary(self):
        for width in (8, 16, 32, 64):
            element, lanes = f"i{width}", 128 // width
            for operation in ("add", "sub"):
                for a_index, b_index, a, b in _pair_vectors(_boundaries(width), lanes):
                    expected = tuple(
                        (left + right if operation == "add" else left - right) % (2 ** width)
                        for left, right in zip(a, b)
                    )
                    with self.subTest(width=width, operation=operation, a=a_index, b=b_index):
                        self.assert_model(
                            f"sse2.{operation}.{element}x{lanes}.default",
                            {"a": _vector(element, a), "b": _vector(element, b)},
                            _vector(element, expected), family="integer_wraparound",
                        )

    def test_signed_and_unsigned_saturation_cross_both_clipping_limits(self):
        for width, signed in product((8, 16), (True, False)):
            element = ("i" if signed else "u") + str(width)
            lanes = 128 // width
            lower = -(2 ** (width - 1)) if signed else 0
            upper = 2 ** (width - 1) - 1 if signed else 2 ** width - 1
            for operation in ("adds", "subs"):
                for a_index, b_index, a, b in _pair_vectors(_boundaries(width), lanes):
                    left = tuple(_signed(value, width) if signed else value for value in a)
                    right = tuple(_signed(value, width) if signed else value for value in b)
                    mathematical = tuple(
                        x + y if operation == "adds" else x - y for x, y in zip(left, right)
                    )
                    expected = tuple(_clamp(value, lower, upper) for value in mathematical)
                    with self.subTest(width=width, signed=signed, operation=operation,
                                      a=a_index, b=b_index):
                        self.assert_model(
                            f"sse2.{operation}.{element}x{lanes}.default",
                            {"a": _vector(element, a), "b": _vector(element, b)},
                            _vector(element, expected), family="integer_saturation",
                        )

    def test_comparison_masks_use_signed_order_and_full_lane_ones(self):
        predicates = {"cmpeq": lambda a, b: a == b,
                      "cmpgt": lambda a, b: a > b, "cmplt": lambda a, b: a < b}
        for width in (8, 16, 32):
            element, lanes = f"i{width}", 128 // width
            for operation, predicate in predicates.items():
                for a_index, b_index, a, b in _pair_vectors(_boundaries(width), lanes):
                    expected = tuple(
                        2 ** width - 1 if predicate(_signed(x, width), _signed(y, width)) else 0
                        for x, y in zip(a, b)
                    )
                    with self.subTest(width=width, operation=operation, a=a_index, b=b_index):
                        self.assert_model(
                            f"sse2.{operation}.{element}x{lanes}.default",
                            {"a": _vector(element, a), "b": _vector(element, b)},
                            _vector(element, expected), family="integer_signed_comparison",
                        )

    def test_minmax_distinguish_signed_words_from_unsigned_bytes(self):
        for element, width, lanes, signed in (("i16", 16, 8, True), ("u8", 8, 16, False)):
            for operation in ("min", "max"):
                select = min if operation == "min" else max
                for a_index, b_index, a, b in _pair_vectors(_boundaries(width), lanes):
                    expected = tuple(select(_signed(x, width), _signed(y, width))
                                     if signed else select(x, y) for x, y in zip(a, b))
                    with self.subTest(element=element, operation=operation, a=a_index, b=b_index):
                        self.assert_model(
                            f"sse2.{operation}.{element}x{lanes}.default",
                            {"a": _vector(element, a), "b": _vector(element, b)},
                            _vector(element, expected), family="integer_extrema_signedness",
                        )

    def test_unsigned_average_rounds_odd_sums_up_without_intermediate_wrap(self):
        for width in (8, 16):
            element, lanes = f"u{width}", 128 // width
            for a_index, b_index, a, b in _pair_vectors(_boundaries(width), lanes):
                expected = tuple(sum((x, y)) // 2 + (sum((x, y)) % 2)
                                 for x, y in zip(a, b))
                with self.subTest(width=width, a=a_index, b=b_index):
                    self.assert_model(
                        f"sse2.avg.{element}x{lanes}.default",
                        {"a": _vector(element, a), "b": _vector(element, b)},
                        _vector(element, expected), family="integer_average_round_up",
                    )

    def test_bitwise_boolean_algebra_and_andnot_operand_order(self):
        modulus = 2 ** 128
        for operation in ("and", "or", "xor", "andnot"):
            for a_index, b_index, a, b in _pair_vectors(_boundaries(32), 4):
                left = int.from_bytes(b"".join(value.to_bytes(4, "little") for value in a), "little")
                right = int.from_bytes(b"".join(value.to_bytes(4, "little") for value in b), "little")
                result = {"and": left & right, "or": left | right, "xor": left ^ right,
                          "andnot": (modulus - 1 - left) & right}[operation]
                expected_bytes = result.to_bytes(16, "little")
                expected = tuple(int.from_bytes(expected_bytes[start:start + 4], "little")
                                 for start in range(0, 16, 4))
                with self.subTest(operation=operation, a=a_index, b=b_index):
                    self.assert_model(
                        f"sse2.{operation}.i32x4.default",
                        {"a": _vector("i32", a), "b": _vector("i32", b)},
                        _vector("i32", expected), family="integer_bitwise_operand_order",
                    )

    def test_multiply_high_low_and_unsigned_high_keep_the_correct_product_half(self):
        for operation, element, signed in (("mullo", "i16", True),
                                           ("mulhi", "i16", True), ("mulhi", "u16", False)):
            for a_index, b_index, a, b in _pair_vectors(_boundaries(16), 8):
                products = tuple((_signed(x, 16) * _signed(y, 16)) if signed else x * y
                                 for x, y in zip(a, b))
                full_products = tuple(value % (2 ** 32) for value in products)
                expected = tuple(value % (2 ** 16) if operation == "mullo"
                                 else value // (2 ** 16) for value in full_products)
                with self.subTest(operation=operation, element=element, a=a_index, b=b_index):
                    self.assert_model(
                        f"sse2.{operation}.{element}x8.default",
                        {"a": _vector(element, a), "b": _vector(element, b)},
                        _vector(element, expected), family="integer_multiply_product_half",
                    )

    def test_unsigned_doubleword_multiply_selects_even_lanes_and_retains_64_bits(self):
        for a_index, b_index, a, b in _pair_vectors(_boundaries(32), 4):
            expected = tuple(a[index] * b[index] for index in (0, 2))
            with self.subTest(a=a_index, b=b_index):
                self.assert_model(
                    "sse2.mul.u32x4.default",
                    {"a": _vector("u32", a), "b": _vector("u32", b)},
                    _vector("u64", expected), family="integer_multiply_even_lanes",
                )

    def test_madd_adjacent_signed_products_and_the_unique_overflow_at_each_pair(self):
        rows = list(_pair_vectors(_boundaries(16), 8))
        # Intel explicitly documents all four source words 0x8000 as PMADDWD's
        # sole overflow: 0x80000000, not signed saturation to 0x7fffffff.
        for pair in range(4):
            for altered_word in (None, 0, 1, 2, 3):
                a = [3, 7, 11, 13, 17, 19, 23, 29]
                b = [31, 37, 41, 43, 47, 53, 59, 61]
                start = 2 * pair
                a[start:start + 2] = [0x8000, 0x8000]
                b[start:start + 2] = [0x8000, 0x8000]
                if altered_word is not None:
                    operand = a if altered_word < 2 else b
                    operand[start + altered_word % 2] = 0x8001
                rows.append((f"overflow-pair-{pair}", altered_word, tuple(a), tuple(b)))
        for a_index, b_index, a, b in rows:
            expected = tuple(
                sum(_signed(a[index + lane], 16) * _signed(b[index + lane], 16)
                    for lane in range(2)) % (2 ** 32)
                for index in range(0, 8, 2)
            )
            with self.subTest(a=a_index, b=b_index):
                self.assert_model(
                    "sse2.madd.i16x8.default",
                    {"a": _vector("i16", a), "b": _vector("i16", b)},
                    _vector("i32", expected), family="integer_madd_wraparound",
                )

    def test_sad_unsigned_bytes_sum_each_eight_byte_half_independently(self):
        rows = list(_pair_vectors(_boundaries(8), 16))
        for lane, value in product(range(16), (1, 255)):
            a = [0] * 16
            a[lane] = value
            rows.append((f"single-lane-{lane}", value, tuple(a), (0,) * 16))
        for a_index, b_index, a, b in rows:
            expected = tuple(sum(abs(a[lane] - b[lane]) for lane in group)
                             for group in (range(8), range(8, 16)))
            with self.subTest(a=a_index, b=b_index):
                self.assert_model(
                    "sse2.sad.u8x16.default",
                    {"a": _vector("u8", a), "b": _vector("u8", b)},
                    _vector("u64", expected), family="integer_sad_half_boundaries",
                )

    def test_saturating_packs_preserve_source_order_and_signed_source_interpretation(self):
        for source_width, target_width, unsigned in ((16, 8, False), (32, 16, False), (16, 8, True)):
            source = f"i{source_width}"
            target = ("u" if unsigned else "i") + str(target_width)
            lower = 0 if unsigned else -(2 ** (target_width - 1))
            upper = 2 ** target_width - 1 if unsigned else 2 ** (target_width - 1) - 1
            values = tuple(dict.fromkeys(value % (2 ** source_width) for value in (
                -(2 ** (source_width - 1)), 2 ** (source_width - 1) - 1,
                lower - 1, lower, lower + 1, -1, 0, 1, upper - 1, upper, upper + 1,
            )))
            operation = "packus" if unsigned else "packs"
            for a_index, b_index, a, b in _pair_vectors(values, 128 // source_width):
                expected = tuple(_clamp(_signed(value, source_width), lower, upper)
                                 for value in (*a, *b))
                with self.subTest(source=source, target=target, a=a_index, b=b_index):
                    self.assert_model(
                        f"sse2.{operation}.{source}x{128 // source_width}.default",
                        {"a": _vector(source, a), "b": _vector(source, b)},
                        _vector(target, expected), family="integer_pack_signedness_and_order",
                    )

    def test_immediate_lane_shifts_cover_every_imm8_without_count_masking(self):
        for width in (16, 32, 64):
            element, lanes = f"i{width}", 128 // width
            values = _boundaries(width)
            for operation in (("sll", "srl", "sra") if width != 64 else ("sll", "srl")):
                case_id = f"sse2.{operation}i.{element}x{lanes}.imm8"
                for count in range(256):
                    a = tuple(values[(count + lane) % len(values)] for lane in range(lanes))
                    with self.subTest(width=width, operation=operation, count=count):
                        self.assert_immediate(
                            case_id, {"a": _vector(element, a)},
                            _vector(element, _shift(a, width, count, operation)), count,
                            family="integer_immediate_shift_count",
                        )

    def test_variable_lane_shifts_use_all_low_64_count_bits(self):
        counts = tuple(range(256)) + (256, 257, 2 ** 16, 2 ** 16 + 1,
                                      2 ** 32 - 1, 2 ** 32, 2 ** 32 + 1,
                                      2 ** 63, 2 ** 64 - 1)
        for width in (16, 32, 64):
            element, lanes = f"i{width}", 128 // width
            values = _boundaries(width)
            for operation in (("sll", "srl", "sra") if width != 64 else ("sll", "srl")):
                for index, count in enumerate(counts):
                    a = tuple(values[(index + lane) % len(values)] for lane in range(lanes))
                    upper_poison = (2 ** 64 - 1, 0, 2 ** 63, 1)[index % 4]
                    with self.subTest(width=width, operation=operation, count=hex(count)):
                        self.assert_model(
                            f"sse2.{operation}.{element}x{lanes}.vector-count",
                            {"a": _vector(element, a), "count": _vector("u64", (count, upper_poison))},
                            _vector(element, _shift(a, width, count, operation)),
                            family="integer_variable_shift_count",
                        )

    def test_variable_lane_shifts_ignore_every_bit_in_the_upper_count_lane(self):
        for width in (16, 32, 64):
            element, lanes = f"i{width}", 128 // width
            a = tuple((2 ** (width - 1) + 31 * lane + 1) % (2 ** width) for lane in range(lanes))
            for operation in (("sll", "srl", "sra") if width != 64 else ("sll", "srl")):
                for count, upper_bit in product((0, 1, width - 1, width), range(64)):
                    with self.subTest(width=width, operation=operation, count=count, upper_bit=upper_bit):
                        self.assert_model(
                            f"sse2.{operation}.{element}x{lanes}.vector-count",
                            {"a": _vector(element, a), "count": _vector("u64", (count, 2 ** upper_bit))},
                            _vector(element, _shift(a, width, count, operation)),
                            family="integer_variable_shift_ignored_high_lane",
                        )

    def test_byte_shifts_move_the_full_128_bits_and_zero_at_sixteen_bytes(self):
        a = bytes((17 * lane + 3) % 256 for lane in range(16))
        whole = int.from_bytes(a, "little")
        for operation in ("slli", "srli"):
            for count in range(256):
                if count >= 16:
                    expected = bytes(16)
                else:
                    factor = 256 ** count
                    shifted = whole * factor if operation == "slli" else whole // factor
                    expected = (shifted % (2 ** 128)).to_bytes(16, "little")
                with self.subTest(operation=operation, count=count):
                    self.assert_immediate(
                        f"sse2.{operation}-bytes.u8x16.imm8",
                        {"a": _vector("u8", a)}, _vector("u8", expected), count,
                        family="integer_byte_shift_lane_order",
                    )

    def test_shuffles_cover_every_control_and_preserve_the_untouched_half(self):
        for operation, element, a in (
            ("shuffle", "i32", (0x01234567, 0x89ABCDEF, 0xFFEEDDCC, 0x76543210)),
            ("shufflelo", "i16", (0x8001, 0x007F, 0xFF00, 0x55AA, 0x1111, 0x2222, 0x4444, 0x8888)),
            ("shufflehi", "i16", (0x8001, 0x007F, 0xFF00, 0x55AA, 0x1111, 0x2222, 0x4444, 0x8888)),
        ):
            for control in range(256):
                # Base-four digits independently select lanes from low to high.
                selectors = tuple((control // (4 ** lane)) % 4 for lane in range(4))
                expected = list(a)
                start = 4 if operation == "shufflehi" else 0
                expected[start:start + 4] = [a[start + selector] for selector in selectors]
                with self.subTest(operation=operation, control=control):
                    self.assert_immediate(
                        f"sse2.{operation}.{element}x{len(a)}.imm8",
                        {"a": _vector(element, a)}, _vector(element, expected), control,
                        family="integer_shuffle_control_and_preservation",
                    )

    def test_unpack_selects_the_correct_half_and_interleaves_a_before_b(self):
        for width in (8, 16, 32, 64):
            element, lanes = f"i{width}", 128 // width
            for operation in ("unpacklo", "unpackhi"):
                half = range(0, lanes // 2) if operation == "unpacklo" else range(lanes // 2, lanes)
                for a_index, b_index, a, b in _pair_vectors(_boundaries(width), lanes):
                    expected = tuple(value for lane in half for value in (a[lane], b[lane]))
                    with self.subTest(width=width, operation=operation, a=a_index, b=b_index):
                        self.assert_model(
                            f"sse2.{operation}.{element}x{lanes}.default",
                            {"a": _vector(element, a), "b": _vector(element, b)},
                            _vector(element, expected), family="integer_unpack_half_and_order",
                        )

    def test_extract_zero_extends_each_legal_lane_including_negative_word_bits(self):
        values = _boundaries(16)
        for rotation, lane in product(range(len(values)), range(8)):
            a = tuple(values[(rotation + index) % len(values)] for index in range(8))
            with self.subTest(rotation=rotation, lane=lane):
                self.assert_model(
                    "sse2.extract.i16x8.imm8", {"a": _vector("i16", a)},
                    _scalar("u32", a[lane]), immediates={"imm8": lane},
                    family="integer_extract_zero_extension",
                )

    def test_insert_uses_only_scalar_low_word_and_preserves_all_other_lanes(self):
        for rotation, lane, value in product(range(4), range(8), _boundaries(32)):
            a = tuple((0x8000 + 0x101 * index + rotation) % (2 ** 16) for index in range(8))
            expected = list(a)
            expected[lane] = value % (2 ** 16)
            with self.subTest(rotation=rotation, lane=lane, value=hex(value)):
                self.assert_model(
                    "sse2.insert.i16x8.imm8",
                    {"a": _vector("i16", a), "value": _scalar("i32", value)},
                    _vector("i16", expected), immediates={"imm8": lane},
                    family="integer_insert_truncation_and_preservation",
                )

    def test_movemask_sign_bits_define_lane_positions_without_sign_extending_the_result(self):
        masks = {0, 0xFFFF, 0xAAAA, 0x5555}
        masks.update(2 ** lane for lane in range(16))
        masks.update(0xFFFF - 2 ** lane for lane in range(16))
        masks.update(2 ** first + 2 ** second for first, second in combinations(range(16), 2))
        masks.update(range(0, 2 ** 16, 257))
        for mask in sorted(masks):
            a = tuple((73 * lane + mask) % 128 + (128 if (mask // (2 ** lane)) % 2 else 0)
                      for lane in range(16))
            with self.subTest(mask=hex(mask)):
                self.assert_model(
                    "sse2.movemask.i8x16.default", {"a": _vector("i8", a)},
                    _scalar("i32", mask), family="integer_movemask_bit_order",
                )

    def test_set_and_setr_constructors_and_broadcasts_preserve_named_lane_positions(self):
        for width in (8, 16, 32, 64):
            element, lanes = f"i{width}", 128 // width
            values = _boundaries(width)
            for rotation in range(len(values)):
                a = tuple(values[(rotation + lane) % len(values)] for lane in range(lanes))
                operands = {f"lane{lane}": _scalar(element, value) for lane, value in enumerate(a)}
                identifiers = [f"sse2.set.{element}x{lanes}.high-low"]
                if width != 64:
                    identifiers.append(f"sse2.setr.{element}x{lanes}.low-high")
                for case_id in identifiers:
                    with self.subTest(case_id=case_id, rotation=rotation):
                        self.assert_model(case_id, operands, _vector(element, a),
                                          family="integer_constructor_lane_order")
                with self.subTest(element=element, broadcast=rotation):
                    self.assert_model(
                        f"sse2.set1.{element}x{lanes}.default",
                        {"value": _scalar(element, values[rotation])},
                        _vector(element, (values[rotation],) * lanes),
                        family="integer_broadcast_width",
                    )

    def test_low_quadword_move_discards_poisoned_high_lane(self):
        for low, high in product(_boundaries(64), repeat=2):
            with self.subTest(low=hex(low), high=hex(high)):
                self.assert_model(
                    "sse2.move.i64x2.default", {"a": _vector("i64", (low, high))},
                    _vector("i64", (low, 0)), family="integer_move_zero_high",
                )

    def test_unaligned_integer_load_store_offsets_guards_and_unrelated_allocations(self):
        payloads = (
            bytes(range(16)), bytes(range(0x80, 0x90)), bytes(range(255, 239, -1)),
            bytes(16), b"\xff" * 16, b"\xaa\x55" * 8,
            b"\x00\x00\x00\x80\xff\xff\xff\x7f\xff\xff\xff\xff\x01\x00\x00\x00",
            bytes((37 * index + 11) % 256 for index in range(16)),
        )
        untouched = "0xdeadbeef0123456789"
        for width, lanes, loaded_bytes, load_case, store_case in (
            (32, 4, 16, "sse2.loadu.i32x4.default", "sse2.storeu.i32x4.default"),
            (64, 2, 8, "sse2.loadl.i64x2.zero-high", "sse2.storel.i64x2.low"),
        ):
            element = f"i{width}"
            byte_width = width // 8
            for offset, payload_index in product(range(32), range(len(payloads))):
                payload = payloads[payload_index]
                initial = bytes((index * 29 + 0xA3) % 256 for index in range(64))
                allocation = initial[:offset] + payload + initial[offset + 16:]
                buffers = {
                    "source": {"alignment": 1, "bytes": "0x" + allocation.hex()},
                    "unrelated": {"alignment": 1, "bytes": untouched},
                }
                source_values = tuple(int.from_bytes(payload[start:start + byte_width], "little")
                                      for start in range(0, 16, byte_width))
                loaded = source_values if loaded_bytes == 16 else (source_values[0], 0)
                with self.subTest(operation="load", width=width, offset=offset, payload=payload_index):
                    self.assert_model(
                        load_case, {"source": {"buffer": "source", "offset": offset}},
                        buffers=buffers,
                        observed={"return": _vector(element, loaded), "buffers": {
                            "source": {"byte_offset": 0, "bytes": "0x" + allocation.hex()},
                            "unrelated": {"byte_offset": 0, "bytes": untouched},
                        }}, family="integer_load_offset_zeroing_and_guards",
                    )
                expected_bytes = initial[:offset] + payload[:loaded_bytes] + initial[offset + loaded_bytes:]
                with self.subTest(operation="store", width=width, offset=offset, payload=payload_index):
                    self.assert_model(
                        store_case, {"destination": {"buffer": "destination", "offset": offset},
                                     "a": _vector(element, source_values)},
                        buffers={"destination": {"alignment": 1, "bytes": "0x" + initial.hex()},
                                 "unrelated": {"alignment": 1, "bytes": untouched}},
                        observed={"buffers": {
                            "destination": {"byte_offset": 0, "bytes": "0x" + expected_bytes.hex()},
                            "unrelated": {"byte_offset": 0, "bytes": untouched},
                        }}, family="integer_store_offset_preservation_and_guards",
                    )


if __name__ == "__main__":
    unittest.main()
