"""Dense Intel port-conformance oracles beyond the sparse boundary matrices.

The byte arithmetic matrix checks every ordered operand pair. The pack matrix
checks every signed 16-bit source value; the SAD matrix checks every attainable
half-register sum. Larger integer cases exercise carries at every bit position.
Numeric conversions use integer quotient/remainder arithmetic, never Python
float, the model's conversion helpers, or generated expected fixtures.

These are portable-model checks, not evidence of execution on four native CPUs.
Failures retain ModelOutputMismatch input/expected/actual evidence. Directed
rounding uses a validated contract extension and is labeled separately.
"""

from __future__ import annotations

from itertools import product
import unittest

from model_test_support import VerifiedModelMixin


_MODES = ("nearest_even", "toward_negative", "toward_positive", "toward_zero")
_BYTE_OPERATIONS = (
    ("add", "i8"), ("sub", "i8"), ("adds", "i8"), ("subs", "i8"),
    ("adds", "u8"), ("subs", "u8"), ("avg", "u8"), ("min", "u8"),
    ("max", "u8"), ("cmpeq", "i8"), ("cmpgt", "i8"), ("cmplt", "i8"),
)


def _vector(element, values):
    width = int(element[1:])
    return {"element": element, "lanes": [
        f"0x{value % (1 << width):0{width // 4}x}" for value in values
    ]}


def _scalar(element, value):
    width = int(element[1:])
    return {"element": element, "bits": f"0x{value % (1 << width):0{width // 4}x}"}


def _signed(value, width):
    return value - (1 << width) if value >= (1 << (width - 1)) else value


def _clamp(value, lower, upper):
    return max(lower, min(value, upper))


def _byte_result(operation, element, left, right):
    a, b = (_signed(left, 8), _signed(right, 8)) if element == "i8" else (left, right)
    if operation in ("add", "sub"):
        return (a + b if operation == "add" else a - b) % 256
    if operation in ("adds", "subs"):
        lower, upper = (-128, 127) if element == "i8" else (0, 255)
        return _clamp(a + b if operation == "adds" else a - b, lower, upper)
    if operation == "avg":
        return (a + b + 1) // 2
    if operation == "min":
        return min(a, b)
    if operation == "max":
        return max(a, b)
    predicate = {"cmpeq": a == b, "cmpgt": a > b, "cmplt": a < b}[operation]
    return 255 if predicate else 0


def _format(width):
    return (23, 8, 127) if width == 32 else (52, 11, 1023)


def _rounded_quotient(numerator, denominator, negative, mode):
    """Round a nonnegative exact ratio, with the sign applied afterwards."""
    quotient, remainder = divmod(numerator, denominator)
    if not remainder:
        return quotient
    if mode == "nearest_even":
        doubled = remainder * 2
        increment = doubled > denominator or (doubled == denominator and quotient % 2)
    elif mode == "toward_negative":
        increment = negative
    elif mode == "toward_positive":
        increment = not negative
    else:
        increment = False
    return quotient + bool(increment)


def _ratio_to_bits(numerator, denominator, width, *, negative=False, mode="nearest_even"):
    """Encode a finite nonnegative ratio using explicit IEEE binade arithmetic."""
    fraction, exponent_bits, bias = _format(width)
    sign = int(negative) << (width - 1)
    if numerator == 0:
        return sign
    exponent = numerator.bit_length() - denominator.bit_length()
    if exponent >= 0:
        if numerator < denominator * (1 << exponent):
            exponent -= 1
    elif numerator * (1 << -exponent) < denominator:
        exponent -= 1
    # Subnormals share the minimum normal exponent rather than a hidden bit.
    minimum = 1 - bias
    scale = max(exponent, minimum) - fraction
    scaled_numerator = numerator << max(0, -scale)
    scaled_denominator = denominator << max(0, scale)
    significand = _rounded_quotient(scaled_numerator, scaled_denominator, negative, mode)
    if significand >= 1 << (fraction + 1):
        significand >>= 1
        exponent += 1
    if exponent > bias:
        toward_infinity = mode == "nearest_even" or (
            mode == "toward_negative" and negative
        ) or (mode == "toward_positive" and not negative)
        infinity = ((1 << exponent_bits) - 1) << fraction
        return sign | (infinity if toward_infinity else infinity - 1)
    if significand < 1 << fraction:
        return sign | significand
    encoded_exponent = max(exponent, minimum) + bias
    return sign | (encoded_exponent << fraction) | (significand - (1 << fraction))


def _decode_ratio(bits, width):
    fraction, exponent_bits, bias = _format(width)
    negative = bool(bits >> (width - 1))
    exponent = (bits >> fraction) & ((1 << exponent_bits) - 1)
    mantissa = bits & ((1 << fraction) - 1)
    if exponent == (1 << exponent_bits) - 1:
        return negative, None, None
    significand = mantissa if exponent == 0 else mantissa + (1 << fraction)
    shift = (1 - bias if exponent == 0 else exponent - bias) - fraction
    return negative, significand << max(0, shift), 1 << max(0, -shift)


def _float_to_integer(bits, source_width, target_width, mode, truncate):
    negative, numerator, denominator = _decode_ratio(bits, source_width)
    indefinite = 1 << (target_width - 1)
    if numerator is None:
        return indefinite
    magnitude = _rounded_quotient(
        numerator, denominator, negative, "toward_zero" if truncate else mode,
    )
    result = -magnitude if negative else magnitude
    return result if -indefinite <= result < indefinite else indefinite


def _integer_values(width, precision):
    """Ties and both neighbors at every potentially lossy integer binade."""
    limit = 1 << (width - 1)
    values = {0, 1, -1, limit - 1, -limit, -limit + 1}
    for exponent in range(width):
        step = 1 << max(0, exponent - precision + 1)
        for base, offset in product((1 << exponent, (1 << exponent) + step,
                                     (1 << (exponent + 1)) - step),
                                    (-1, 0, 1, step // 2 - 1, step // 2, step // 2 + 1)):
            for sign in (-1, 1):
                value = sign * (base + offset)
                if -limit <= value < limit:
                    values.add(value)
    return tuple(sorted(values))


def _float_integer_values(source_width, target_width):
    """Raw IEEE neighbors of every signed magnitude/halfway transition."""
    fraction, exponent_bits, _bias = _format(source_width)
    sign_bit = 1 << (source_width - 1)
    infinity = ((1 << exponent_bits) - 1) << fraction
    values = {0, sign_bit, 1, sign_bit | 1, infinity, sign_bit | infinity,
              infinity | 1, infinity | (1 << (fraction - 1)) | 0x127,
              sign_bit | infinity | 1}
    for exponent in range(-2, target_width + 1):
        for numerator, denominator in ((1 << max(exponent, 0), 1 << max(-exponent, 0)),
                                       ((1 << max(exponent + 1, 0)) + 1,
                                        2 << max(-exponent - 1, 0))):
            center = _ratio_to_bits(numerator, denominator, source_width)
            for delta, negative in product((-2, -1, 0, 1, 2), (False, True)):
                magnitude = center + delta
                if 0 <= magnitude < infinity:
                    values.add(magnitude | (sign_bit if negative else 0))
    return tuple(sorted(values))


class DenseIntegerPortConformanceTests(VerifiedModelMixin, unittest.TestCase):
    oracle_reference = "tests/test_port_conformance_matrix.py"
    oracle_method = "exhaustive_integer_domain_and_exact_quotient_remainder"

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.oracle_reference_metadata = {
            "oracle": "Complete byte domains, complete signed-word pack domain, and exact rational rounding",
            "source": cls.oracle_reference,
            "case_definitions_sha256": cls.cases.sha256,
            "native_cross_host_evidence": False,
            "contract_extensions": ["directed rounding with per-input regression witnesses"],
            "byte_pairs_per_operation": 65536,
            "signed_word_values_per_pack": 65536,
        }

    def check_byte_tile(self, operation, element, high_nibble):
        case_id = f"sse2.{operation}.{element}x16.default"
        for low_nibble, right_block in product(range(16), repeat=2):
            a = high_nibble * 16 + low_nibble
            b = tuple(range(right_block * 16, right_block * 16 + 16))
            expected = tuple(_byte_result(operation, element, a, value) for value in b)
            with self.subTest(left=a, right_block=right_block):
                self.assert_model(
                    case_id, {"a": _vector(element, (a,) * 16), "b": _vector(element, b)},
                    _vector(element, expected), family="exhaustive_byte_operand_pairs",
                )

    def check_pack_tile(self, unsigned, high_nibble):
        operation, target = ("packus", "u8") if unsigned else ("packs", "i8")
        lower, upper = (0, 255) if unsigned else (-128, 127)
        for block in range(256):
            values = tuple(range(high_nibble * 4096 + block * 16,
                                 high_nibble * 4096 + block * 16 + 16))
            expected = tuple(_clamp(_signed(value, 16), lower, upper) for value in values)
            with self.subTest(block=block):
                self.assert_model(
                    f"sse2.{operation}.i16x8.default",
                    {"a": _vector("i16", values[:8]), "b": _vector("i16", values[8:])},
                    _vector(target, expected), family="exhaustive_word_pack_domain",
                )

    def check_carry_bit_range(self, width, operation, quarter):
        lanes = 128 // width
        mask = (1 << width) - 1
        seen = set()
        for bit in range(width):
            for delta, small, negative in product((-2, -1, 0, 1, 2), (1, 2, 3, 17), (False, True)):
                value = ((1 << bit) + delta) & mask
                if negative:
                    value = (-value) & mask
                a = tuple((value + lane * 0x0101010101010101) & mask for lane in range(lanes))
                b = tuple((small + lane * 0x0305070B0D111317) & mask for lane in range(lanes))
                if (a, b) in seen:
                    continue
                seen.add((a, b))
                if bit // (width // 4) != quarter:
                    continue
                expected = tuple((left + right if operation == "add" else left - right) & mask
                                 for left, right in zip(a, b))
                with self.subTest(bit=bit, delta=delta, small=small, negative=negative):
                    self.assert_model(
                        f"sse2.{operation}.i{width}x{lanes}.default",
                        {"a": _vector(f"i{width}", a), "b": _vector(f"i{width}", b)},
                        _vector(f"i{width}", expected), family="integer_every_bit_carry_borrow",
                    )

    def check_sad_sum_tile(self, tile):
        for total in range(tile * 128, min((tile + 1) * 128, 2041)):
            def payload(value):
                return tuple(_clamp(value - 255 * lane, 0, 255) for lane in range(8))
            a = payload(total) + payload(2040 - total)
            # A nonzero operand makes all differences run in the opposite direction
            # in the second half, while both half sums stay independently known.
            b = (0,) * 8 + (255,) * 8
            a = a[:8] + tuple(255 - value for value in a[8:])
            with self.subTest(total=total):
                self.assert_model(
                    "sse2.sad.u8x16.default", {"a": _vector("u8", a), "b": _vector("u8", b)},
                    _vector("u64", (total, 2040 - total)), family="sad_every_reachable_half_sum",
                )

    def check_product_bit_range(self, operation, signed, quarter):
        element = "i16" if signed else "u16"
        seen = set()
        for bit in range(16):
            for delta, multiplier in product(range(-3, 4), (1, 2, 3, 255, 256, 257, 32767, 32768, 65535)):
                a = tuple(((1 << bit) + delta + lane * 8191) % 65536 for lane in range(8))
                b = tuple((multiplier + lane * 257) % 65536 for lane in range(8))
                if (a, b) in seen:
                    continue
                seen.add((a, b))
                if bit // 4 != quarter:
                    continue
                products = tuple((_signed(x, 16) * _signed(y, 16)) if signed else x * y
                                 for x, y in zip(a, b))
                expected = tuple((value % (1 << 32)) // (1 << 16) if operation == "mulhi"
                                 else value % (1 << 16) for value in products)
                with self.subTest(bit=bit, delta=delta, multiplier=multiplier):
                    self.assert_model(
                        f"sse2.{operation}.{element}x8.default",
                        {"a": _vector(element, a), "b": _vector(element, b)},
                        _vector(element, expected), family="multiply_bit_transition_products",
                    )


class RationalConversionPortConformanceTests(VerifiedModelMixin, unittest.TestCase):
    oracle_reference = "tests/test_port_conformance_matrix.py"
    oracle_method = "integer_only_ieee_ratio_rounding"

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.oracle_reference_metadata = {
            "oracle": "Independent IEEE significand/exponent and integer quotient/remainder equations",
            "source": cls.oracle_reference,
            "case_definitions_sha256": cls.cases.sha256,
            "native_cross_host_evidence": False,
            "contract_extensions": ["directed rounding with per-input regression witnesses"],
        }

    def check_integer_to_float(self, source_width, target_width, scalar, mode):
        fraction, _bits, _bias = _format(target_width)
        values = _integer_values(source_width, fraction + 1)
        lanes = 128 // target_width
        poison = (0x7F800123, 0xFFC00456, 0x80000000, 0x00000001) if target_width == 32 else (
            0x7FF0000000000123, 0xFFF8000000000456)
        case_id = (f"sse2.cvt.i{source_width}.f{target_width}x{lanes}.scalar" if scalar else
                   f"sse2.cvt.i32x4.f{target_width}x{lanes}")
        for index, value in enumerate(values):
            if scalar:
                operands = {"a": _vector(f"f{target_width}", poison[:lanes]),
                            "value": _scalar(f"i{source_width}", value)}
                expected = list(poison[:lanes])
                expected[0] = _ratio_to_bits(abs(value), 1, target_width,
                                             negative=value < 0, mode=mode)
            else:
                signed = tuple(values[(index + lane * 17) % len(values)] for lane in range(4))
                operands = {"a": _vector("i32", signed)}
                expected = [_ratio_to_bits(abs(item), 1, target_width, negative=item < 0, mode=mode)
                            for item in signed[:lanes]]
            with self.subTest(value=value):
                self.assert_model(case_id, operands, _vector(f"f{target_width}", expected),
                                  rounding=mode, extended=mode != "nearest_even",
                                  family="integer_to_float_all_precision_transitions")

    def check_float_to_integer(self, source_width, target_width, scalar, truncate, mode):
        values = _float_integer_values(source_width, target_width)
        lanes = 128 // source_width
        operation = "cvtt" if truncate else "cvt"
        target = f"i{target_width}" if scalar else "i32x4"
        suffix = ".scalar" if scalar else ""
        case_id = f"sse2.{operation}.f{source_width}x{lanes}.{target}{suffix}"
        for index, bits in enumerate(values):
            inputs = tuple(values[(index + lane * 71) % len(values)] for lane in range(lanes))
            expected = tuple(_float_to_integer(value, source_width, target_width, mode, truncate)
                             for value in inputs[:1]) if scalar else tuple(
                _float_to_integer(value, source_width, target_width, mode, truncate) for value in inputs
            ) + (0,) * (4 - lanes)
            output = _scalar(target, expected[0]) if scalar else _vector("i32", expected)
            with self.subTest(bits=hex(bits)):
                self.assert_model(case_id, {"a": _vector(f"f{source_width}", inputs)}, output,
                                  rounding=mode, extended=mode != "nearest_even",
                                  family="float_to_integer_all_signed_binade_neighbors")

    def check_narrowing(self, scalar, mode, exponent_group):
        exponents = ((-151, -150, -149, -148, -128, -127, -126),
                     (-25, -24, -23, -2, -1, 0, 1),
                     (22, 23, 24, 25, 125, 126, 127, 128))[exponent_group]
        values = set()
        for exponent, mantissa in product(exponents, (0, 1, 2, 3, (1 << 23) - 1)):
            # Exactly halfway between adjacent binary32 significands, then
            # neighboring binary64 numbers on both sides of that midpoint.
            numerator = (1 << 24) + 2 * mantissa + 1
            scale = exponent - 24
            center = _ratio_to_bits(numerator << max(0, scale), 1 << max(0, -scale), 64)
            for delta, negative in product((-1, 0, 1), (False, True)):
                values.add((center + delta) | (int(negative) << 63))
        poison = (0x7F800127, 0xFFC00831, 0x80000000, 0x7FC01481)
        values = sorted(values)
        case_id = "sse2.cvt.f64x2.f32x4" + (".scalar" if scalar else "")
        for index, bits in enumerate(values):
            inputs = (bits, values[(index + 37) % len(values)])
            converted = []
            for value in inputs[:1 if scalar else 2]:
                negative, numerator, denominator = _decode_ratio(value, 64)
                converted.append(_ratio_to_bits(numerator, denominator, 32, negative=negative, mode=mode))
            expected = converted + (list(poison[1:]) if scalar else [0, 0])
            operands = {"a": _vector("f32", poison), "b": _vector("f64", inputs)} if scalar else {
                "a": _vector("f64", inputs)}
            with self.subTest(bits=hex(bits)):
                self.assert_model(case_id, operands, _vector("f32", expected), rounding=mode,
                                  extended=mode != "nearest_even",
                                  family="narrowing_exact_halfway_all_transition_regions")


class RationalOracleAnchorTests(unittest.TestCase):
    """Fixed encodings catch errors in the independent arithmetic itself."""

    def test_integer_and_fraction_encodings(self):
        for numerator, denominator, width, expected in (
            (0, 1, 32, 0), (1, 1, 32, 0x3F800000), (3, 2, 32, 0x3FC00000),
            (16777217, 1, 32, 0x4B800000), (16777219, 1, 32, 0x4B800002),
            (1, 1 << 149, 32, 1), (1, 1 << 150, 32, 0),
            (3, 1 << 150, 32, 2), (1, 1 << 126, 32, 0x00800000),
            (1, 1, 64, 0x3FF0000000000000), (3, 2, 64, 0x3FF8000000000000),
            (9007199254740993, 1, 64, 0x4340000000000000),
        ):
            with self.subTest(numerator=numerator, denominator=denominator, width=width):
                self.assertEqual(_ratio_to_bits(numerator, denominator, width), expected)

    def test_directed_overflow_underflow_and_ties(self):
        for negative, mode, expected in (
            (False, "nearest_even", 0x7F800000), (False, "toward_zero", 0x7F7FFFFF),
            (False, "toward_positive", 0x7F800000), (False, "toward_negative", 0x7F7FFFFF),
            (True, "toward_positive", 0xFF7FFFFF), (True, "toward_negative", 0xFF800000),
        ):
            with self.subTest(negative=negative, mode=mode):
                self.assertEqual(_ratio_to_bits(1 << 128, 1, 32, negative=negative, mode=mode), expected)
        self.assertEqual(_ratio_to_bits(1, 1 << 151, 32, mode="toward_positive"), 1)
        self.assertEqual(_ratio_to_bits(1, 1 << 151, 32, negative=True, mode="toward_negative"), 0x80000001)
        self.assertEqual(_float_to_integer(0x3FC00000, 32, 32, "nearest_even", False), 2)
        self.assertEqual(_float_to_integer(0x40200000, 32, 32, "nearest_even", False), 2)
        self.assertEqual(_float_to_integer(0xBFC00000, 32, 32, "toward_negative", False), -2)
        self.assertEqual(_float_to_integer(0x7FC00001, 32, 32, "nearest_even", False), 0x80000000)


def _install(cls, name, method, *args):
    def test(self):
        getattr(self, method)(*args)
    test.__name__ = "test_" + name
    setattr(cls, test.__name__, test)


for _operation, _element in _BYTE_OPERATIONS:
    for _tile in range(16):
        _install(DenseIntegerPortConformanceTests, f"all_byte_pairs_{_operation}_{_element}_{_tile:02x}",
                 "check_byte_tile", _operation, _element, _tile)
for _unsigned, _tile in product((False, True), range(16)):
    _install(DenseIntegerPortConformanceTests, f"all_word_sources_pack_{'unsigned' if _unsigned else 'signed'}_{_tile:02x}",
             "check_pack_tile", _unsigned, _tile)
for _width, _operation, _quarter in product((16, 32, 64), ("add", "sub"), range(4)):
    _install(DenseIntegerPortConformanceTests, f"carry_borrow_i{_width}_{_operation}_{_quarter}",
             "check_carry_bit_range", _width, _operation, _quarter)
for _tile in range(16):
    _install(DenseIntegerPortConformanceTests, f"all_sad_half_sums_{_tile:02x}", "check_sad_sum_tile", _tile)
for (_operation, _signedness), _quarter in product((("mullo", True), ("mulhi", True), ("mulhi", False)), range(4)):
    _install(DenseIntegerPortConformanceTests, f"product_bits_{_operation}_{'signed' if _signedness else 'unsigned'}_{_quarter}",
             "check_product_bit_range", _operation, _signedness, _quarter)
for (_source, _target, _scalar_flag), _mode in product(
    ((32, 32, False), (32, 64, False), (32, 32, True), (32, 64, True), (64, 64, True)), _MODES,
):
    _install(RationalConversionPortConformanceTests, f"int{_source}_to_f{_target}_{'scalar' if _scalar_flag else 'packed'}_{_mode}",
             "check_integer_to_float", _source, _target, _scalar_flag, _mode)
for (_source, _target, _scalar_flag), _truncate, _mode in product(
    ((32, 32, False), (64, 32, False), (32, 32, True), (64, 32, True), (64, 64, True)), (False, True), _MODES,
):
    _install(RationalConversionPortConformanceTests, f"f{_source}_to_i{_target}_{'scalar' if _scalar_flag else 'packed'}_{'truncate' if _truncate else 'rounded'}_{_mode}",
             "check_float_to_integer", _source, _target, _scalar_flag, _truncate, _mode)
for _scalar_flag, _mode, _group in product((False, True), _MODES, range(3)):
    _install(RationalConversionPortConformanceTests, f"narrow_f64_f32_{'scalar' if _scalar_flag else 'packed'}_{_mode}_{_group}",
             "check_narrowing", _scalar_flag, _mode, _group)


if __name__ == "__main__":
    unittest.main()
