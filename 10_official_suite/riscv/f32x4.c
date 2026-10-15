/* Native RV64/RVV adaptation of the project SIMD wrappers. */
#include "../shortcuts.h"
#include "rvv128.h"


f32x4 riscv_set_f32x4(float lane3, float lane2, float lane1, float lane0)
{
    return (f32x4){lane0, lane1, lane2, lane3};
}

f32x4 riscv_setr_f32x4(float lane0, float lane1, float lane2, float lane3)
{
    return (f32x4){lane0, lane1, lane2, lane3};
}

f32x4 riscv_set1_f32x4(float value)
{
    return rvv_splats(value);
}

f32x4 riscv_set_scalar_f32x4(float value)
{
    union { float value; unsigned bits; } scalar = {value};
    return (f32x4)(u32x4){scalar.bits, 0, 0, 0};
}

f32x4 riscv_load1_f32x4(const void *source)
{
    unsigned value;
    __builtin_memcpy(&value, source, sizeof(value));
    return (f32x4)(u32x4){value, value, value, value};
}

f32x4 riscv_cast_f64x2_f32x4(f64x2 a)
{
    u64x2 bits = (u64x2)a;
    return (f32x4)(u32x4){
        (unsigned)bits[0], (unsigned)(bits[0] >> 32),
        (unsigned)bits[1], (unsigned)(bits[1] >> 32)};
}

f64x2 riscv_cast_f32x4_f64x2(f32x4 a)
{
    u32x4 bits = (u32x4)a;
    return (f64x2)(u64x2){
        (unsigned long long)bits[0] | ((unsigned long long)bits[1] << 32),
        (unsigned long long)bits[2] | ((unsigned long long)bits[3] << 32)};
}

IOITF_UNARY(i32x4, f32x4, riscv_cast_f32x4_i32x4, (i32x4)a)
IOITF_UNARY(f32x4, i32x4, riscv_cast_i32x4_f32x4, (f32x4)a)

int riscv_movemask_f32x4(f32x4 a)
{
    u32x4 bits = (u32x4)a;
    return (int)((bits[0] >> 31) | ((bits[1] >> 31) << 1) |
                 ((bits[2] >> 31) << 2) | ((bits[3] >> 31) << 3));
}

IOITF_BINARY(f32x4, f32x4, riscv_unpacklo_f32x4, (f32x4){a[0], b[0], a[1], b[1]})
IOITF_BINARY(f32x4, f32x4, riscv_unpackhi_f32x4, (f32x4){a[2], b[2], a[3], b[3]})

static unsigned long long widen_f32_bits(unsigned bits)
{
    unsigned long long sign = (unsigned long long)(bits >> 31) << 63;
    unsigned exponent = (bits >> 23) & 0xffU;
    unsigned fraction = bits & 0x7fffffU;
    unsigned leading;

    if (exponent == 0xffU) {
        if (fraction == 0U) {
            return sign | 0x7ff0000000000000ULL;
        }
        return sign | 0x7ff0000000000000ULL |
               ((unsigned long long)fraction << 29) | (1ULL << 51);
    }
    if (exponent != 0U) {
        return sign | ((unsigned long long)(exponent + 896U) << 52) |
               ((unsigned long long)fraction << 29);
    }
    if (fraction == 0U) {
        return sign;
    }
    leading = 0U;
    while ((fraction >> (leading + 1U)) != 0U) {
        ++leading;
    }
    return sign | ((unsigned long long)(leading + 874U) << 52) |
           ((unsigned long long)(fraction - (1U << leading)) <<
            (52U - leading));
}

f64x2 riscv_cvt_f32x4_f64x2(f32x4 a)
{
    u32x4 bits = (u32x4)a;
    return (f64x2)(u64x2){widen_f32_bits(bits[0]),
                           widen_f32_bits(bits[1])};
}

IOITF_UNARY(f64x2, i32x4, riscv_cvt_i32x4_f64x2, (f64x2){(double)a[0], (double)a[1]})

static unsigned narrow_f64_bits(unsigned long long bits);
static unsigned convert_i32_f32_bits(int value);

f32x4 riscv_cvt_scalar_f64x2_f32x4(f32x4 a, f64x2 b)
{
    u32x4 a_bits = (u32x4)a;
    u64x2 b_bits = (u64x2)b;
    return (f32x4)(u32x4){
        narrow_f64_bits(b_bits[0]), a_bits[1], a_bits[2], a_bits[3]};
}

f64x2 riscv_cvt_scalar_f32x4_f64x2(f64x2 a, f32x4 b)
{
    u64x2 a_bits = (u64x2)a;
    u32x4 b_bits = (u32x4)b;
    return (f64x2)(u64x2){widen_f32_bits(b_bits[0]), a_bits[1]};
}

f32x4 riscv_cvt_i32_f32x4(f32x4 a, int value)
{
    u32x4 bits = (u32x4)a;
    return (f32x4)(u32x4){
        convert_i32_f32_bits(value), bits[1], bits[2], bits[3]};
}

f64x2 riscv_cvt_i32_f64x2(f64x2 a, int value)
{
    u64x2 bits = (u64x2)a;
    union { double value; unsigned long long bits; } converted = {(double)value};
    return (f64x2)(u64x2){converted.bits, bits[1]};
}

f64x2 riscv_cvt_i64_f64x2(f64x2 a, long long value)
{
    u64x2 bits = (u64x2)a;
    union { double value; unsigned long long bits; } converted = {(double)value};
    return (f64x2)(u64x2){converted.bits, bits[1]};
}

f32x4 riscv_shuffle_f32x4(f32x4 a, f32x4 b, unsigned imm)
{
    return (f32x4){a[imm & 3U], a[(imm >> 2) & 3U],
                    b[(imm >> 4) & 3U], b[(imm >> 6) & 3U]};
}

IOITF_BINARY(f32x4, f32x4, riscv_movehl_f32x4, (f32x4){b[2], b[3], a[2], a[3]})
IOITF_BINARY(f32x4, f32x4, riscv_movelh_f32x4, (f32x4){a[0], a[1], b[0], b[1]})
IOITF_BINARY(f32x4, f32x4, riscv_move_f32x4, (f32x4){b[0], a[1], a[2], a[3]})

static unsigned long long round_shift_even(unsigned long long value,
                                           unsigned shift)
{
    unsigned long long quotient;
    unsigned long long remainder;
    unsigned long long halfway;

    if (shift == 0U) {
        return value;
    }
    if (shift >= 64U) {
        return 0;
    }
    quotient = value >> shift;
    remainder = value - (quotient << shift);
    halfway = 1ULL << (shift - 1U);
    if (remainder > halfway ||
        (remainder == halfway && (quotient & 1ULL) != 0U)) {
        ++quotient;
    }
    return quotient;
}

static unsigned narrow_f64_bits(unsigned long long bits)
{
    unsigned sign = (unsigned)(bits >> 63) << 31;
    unsigned exponent = (unsigned)((bits >> 52) & 0x7ffU);
    unsigned long long fraction = bits & 0x000fffffffffffffULL;
    unsigned long long significand;
    unsigned long long rounded;
    int unbiased;

    if (exponent == 0x7ffU) {
        if (fraction == 0U) {
            return sign | 0x7f800000U;
        }
        return sign | 0x7f800000U | (unsigned)(fraction >> 29) |
               0x00400000U;
    }
    if (exponent == 0U) {
        return sign;
    }
    unbiased = (int)exponent - 1023;
    significand = (1ULL << 52) | fraction;
    if (unbiased > 127) {
        return sign | 0x7f800000U;
    }
    if (unbiased >= -126) {
        rounded = round_shift_even(significand, 29U);
        if (rounded == (1ULL << 24)) {
            rounded >>= 1;
            ++unbiased;
            if (unbiased > 127) {
                return sign | 0x7f800000U;
            }
        }
        return sign | ((unsigned)(unbiased + 127) << 23) |
               ((unsigned)rounded & 0x007fffffU);
    }
    rounded = round_shift_even(significand, (unsigned)(-unbiased - 97));
    if (rounded >= (1ULL << 23)) {
        return sign | 0x00800000U;
    }
    return sign | (unsigned)rounded;
}

static unsigned convert_i32_f32_bits(int value)
{
    unsigned sign = value < 0 ? 0x80000000U : 0U;
    unsigned magnitude = value < 0 ? 0U - (unsigned)value : (unsigned)value;
    unsigned probe = magnitude;
    unsigned leading = 0;
    unsigned long long rounded;

    if (magnitude == 0U) {
        return 0U;
    }
    while (probe > 1U) {
        probe >>= 1;
        ++leading;
    }
    if (leading <= 23U) {
        rounded = (unsigned long long)magnitude << (23U - leading);
    } else {
        rounded = round_shift_even(magnitude, leading - 23U);
        if (rounded == (1ULL << 24)) {
            rounded >>= 1;
            ++leading;
        }
    }
    return sign | ((leading + 127U) << 23) |
           ((unsigned)rounded & 0x007fffffU);
}

static int nearest_f32_i32(float value)
{
    double widened = (double)value;
    long long integral;
    double fraction;

    if (widened != widened || widened < -2147483648.5 ||
        widened >= 2147483647.5) {
        return (-2147483647 - 1);
    }
    integral = (long long)widened;
    fraction = widened - (double)integral;
    if (fraction > 0.5 ||
        (fraction == 0.5 && integral % 2LL != 0LL)) {
        ++integral;
    } else if (fraction < -0.5 ||
               (fraction == -0.5 && integral % 2LL != 0LL)) {
        --integral;
    }
    return (int)integral;
}

static int truncate_f32_i32(float value)
{
    double widened = (double)value;
    if (widened != widened || widened < -2147483648.0 ||
        widened >= 2147483648.0) {
        return (-2147483647 - 1);
    }
    return (int)widened;
}

f32x4 riscv_cvt_f64x2_f32x4(f64x2 a)
{
    u64x2 bits = (u64x2)a;
    return (f32x4)(u32x4){narrow_f64_bits(bits[0]),
                           narrow_f64_bits(bits[1]), 0, 0};
}

f32x4 riscv_cvt_i32x4_f32x4(i32x4 a)
{
    return (f32x4)(u32x4){convert_i32_f32_bits(a[0]),
                           convert_i32_f32_bits(a[1]),
                           convert_i32_f32_bits(a[2]),
                           convert_i32_f32_bits(a[3])};
}

i32x4 riscv_cvt_f32x4_i32x4(f32x4 a)
{
    return (i32x4){nearest_f32_i32(a[0]), nearest_f32_i32(a[1]),
                    nearest_f32_i32(a[2]), nearest_f32_i32(a[3])};
}

i32x4 riscv_cvtt_f32x4_i32x4(f32x4 a)
{
    return (i32x4){truncate_f32_i32(a[0]), truncate_f32_i32(a[1]),
                    truncate_f32_i32(a[2]), truncate_f32_i32(a[3])};
}

IOITF_UNARY(int, f32x4, riscv_cvt_scalar_f32x4_i32, nearest_f32_i32(a[0]))
IOITF_UNARY(int, f32x4, riscv_cvtt_scalar_f32x4_i32, truncate_f32_i32(a[0]))

static int ordered_f32(float a, float b)
{
    return a == a && b == b;
}

IOITF_BINARY(int, f32x4, riscv_comieq_f32x4, ordered_f32(a[0], b[0]) && a[0] == b[0])
IOITF_BINARY(int, f32x4, riscv_comilt_f32x4, ordered_f32(a[0], b[0]) && a[0] < b[0])
IOITF_BINARY(int, f32x4, riscv_comile_f32x4, ordered_f32(a[0], b[0]) && a[0] <= b[0])
IOITF_BINARY(int, f32x4, riscv_comigt_f32x4, ordered_f32(a[0], b[0]) && a[0] > b[0])
IOITF_BINARY(int, f32x4, riscv_comige_f32x4, ordered_f32(a[0], b[0]) && a[0] >= b[0])
IOITF_BINARY(int, f32x4, riscv_comineq_f32x4, !ordered_f32(a[0], b[0]) || a[0] != b[0])

IOITF_BINARY(f32x4, f32x4, riscv_add_f32x4, rvv_add(a, b))
IOITF_BINARY(f32x4, f32x4, riscv_sub_f32x4, rvv_sub(a, b))
IOITF_BINARY(f32x4, f32x4, riscv_mul_f32x4, rvv_mul(a, b))
static unsigned div_f32_bits(unsigned a, unsigned b, unsigned result)
{
    unsigned a_magnitude = a & 0x7fffffffU;
    unsigned b_magnitude = b & 0x7fffffffU;
    int a_nan = (a & 0x7f800000U) == 0x7f800000U &&
                (a & 0x007fffffU) != 0U;
    int b_nan = (b & 0x7f800000U) == 0x7f800000U &&
                (b & 0x007fffffU) != 0U;
    if (a_nan) {
        return a | 0x00400000U;
    }
    if (b_nan) {
        return b | 0x00400000U;
    }
    if ((a_magnitude == 0U && b_magnitude == 0U) ||
        (a_magnitude == 0x7f800000U && b_magnitude == 0x7f800000U)) {
        return 0xffc00000U;
    }
    return result;
}

f32x4 riscv_div_f32x4(f32x4 a, f32x4 b)
{
    u32x4 a_bits = (u32x4)a;
    u32x4 b_bits = (u32x4)b;
    u32x4 result = (u32x4)rvv_div(a, b);
    return (f32x4)(u32x4){
        div_f32_bits(a_bits[0], b_bits[0], result[0]),
        div_f32_bits(a_bits[1], b_bits[1], result[1]),
        div_f32_bits(a_bits[2], b_bits[2], result[2]),
        div_f32_bits(a_bits[3], b_bits[3], result[3])};
}
IOITF_BINARY(f32x4, f32x4, riscv_add_scalar_f32x4,
             (f32x4){a[0] + b[0], a[1], a[2], a[3]})
IOITF_BINARY(f32x4, f32x4, riscv_sub_scalar_f32x4,
             (f32x4){a[0] - b[0], a[1], a[2], a[3]})
IOITF_BINARY(f32x4, f32x4, riscv_mul_scalar_f32x4,
             (f32x4){a[0] * b[0], a[1], a[2], a[3]})
f32x4 riscv_div_scalar_f32x4(f32x4 a, f32x4 b)
{
    u32x4 a_bits = (u32x4)a;
    u32x4 b_bits = (u32x4)b;
    union { float value; unsigned bits; } result = {a[0] / b[0]};
    return (f32x4)(u32x4){
        div_f32_bits(a_bits[0], b_bits[0], result.bits),
        a_bits[1], a_bits[2], a_bits[3]};
}
IOITF_BINARY(f32x4, f32x4, riscv_and_f32x4,
             (f32x4)((u32x4)a & (u32x4)b))
IOITF_BINARY(f32x4, f32x4, riscv_or_f32x4,
             (f32x4)((u32x4)a | (u32x4)b))
IOITF_BINARY(f32x4, f32x4, riscv_xor_f32x4,
             (f32x4)((u32x4)a ^ (u32x4)b))
IOITF_BINARY(f32x4, f32x4, riscv_andnot_f32x4,
             (f32x4)((~(u32x4)a) & (u32x4)b))

static unsigned sqrt_f32_bits(unsigned input, unsigned result)
{
    unsigned magnitude = input & 0x7fffffffU;
    if ((input & 0x7f800000U) == 0x7f800000U &&
        (input & 0x007fffffU) != 0U) {
        return input | 0x00400000U;
    }
    if ((input & 0x80000000U) != 0U && magnitude != 0U) {
        return 0xffc00000U;
    }
    return result;
}

f32x4 riscv_sqrt_f32x4(f32x4 a)
{
    u32x4 input = (u32x4)a;
    u32x4 result = (u32x4)rvv_sqrt(a);
    return (f32x4)(u32x4){
        sqrt_f32_bits(input[0], result[0]),
        sqrt_f32_bits(input[1], result[1]),
        sqrt_f32_bits(input[2], result[2]),
        sqrt_f32_bits(input[3], result[3])};
}

f32x4 riscv_sqrt_scalar_f32x4(f32x4 a)
{
    u32x4 input = (u32x4)a;
    u32x4 result = (u32x4)rvv_sqrt(a);
    return (f32x4)(u32x4){
        sqrt_f32_bits(input[0], result[0]),
        input[1], input[2], input[3]};
}

static int nan_f32_bits(unsigned bits)
{
    return (bits & 0x7f800000U) == 0x7f800000U &&
           (bits & 0x007fffffU) != 0U;
}

static unsigned minmax_f32_bits(float a, float b, unsigned a_bits,
                                unsigned b_bits, int select_min)
{
    if (nan_f32_bits(a_bits) || nan_f32_bits(b_bits) || a == b) {
        return b_bits;
    }
    if ((select_min && a < b) || (!select_min && a > b)) {
        return a_bits;
    }
    return b_bits;
}

f32x4 riscv_min_f32x4(f32x4 a, f32x4 b)
{
    u32x4 a_bits = (u32x4)a;
    u32x4 b_bits = (u32x4)b;
    return (f32x4)(u32x4){
        minmax_f32_bits(a[0], b[0], a_bits[0], b_bits[0], 1),
        minmax_f32_bits(a[1], b[1], a_bits[1], b_bits[1], 1),
        minmax_f32_bits(a[2], b[2], a_bits[2], b_bits[2], 1),
        minmax_f32_bits(a[3], b[3], a_bits[3], b_bits[3], 1)};
}

f32x4 riscv_max_f32x4(f32x4 a, f32x4 b)
{
    u32x4 a_bits = (u32x4)a;
    u32x4 b_bits = (u32x4)b;
    return (f32x4)(u32x4){
        minmax_f32_bits(a[0], b[0], a_bits[0], b_bits[0], 0),
        minmax_f32_bits(a[1], b[1], a_bits[1], b_bits[1], 0),
        minmax_f32_bits(a[2], b[2], a_bits[2], b_bits[2], 0),
        minmax_f32_bits(a[3], b[3], a_bits[3], b_bits[3], 0)};
}

f32x4 riscv_min_scalar_f32x4(f32x4 a, f32x4 b)
{
    u32x4 a_bits = (u32x4)a;
    u32x4 b_bits = (u32x4)b;
    return (f32x4)(u32x4){
        minmax_f32_bits(a[0], b[0], a_bits[0], b_bits[0], 1),
        a_bits[1], a_bits[2], a_bits[3]};
}

f32x4 riscv_max_scalar_f32x4(f32x4 a, f32x4 b)
{
    u32x4 a_bits = (u32x4)a;
    u32x4 b_bits = (u32x4)b;
    return (f32x4)(u32x4){
        minmax_f32_bits(a[0], b[0], a_bits[0], b_bits[0], 0),
        a_bits[1], a_bits[2], a_bits[3]};
}

static unsigned mask_f32(int yes)
{
    return yes ? ~0U : 0U;
}

IOITF_BINARY(f32x4, f32x4, riscv_cmpeq_f32x4,
             (f32x4)(u32x4){mask_f32(a[0] == b[0]),
                             mask_f32(a[1] == b[1]),
                             mask_f32(a[2] == b[2]),
                             mask_f32(a[3] == b[3])})

f32x4 riscv_cmpeq_scalar_f32x4(f32x4 a, f32x4 b)
{
    u32x4 bits = (u32x4)a;
    return (f32x4)(u32x4){
        mask_f32(a[0] == b[0]), bits[1], bits[2], bits[3]};
}

IOITF_BINARY(f32x4, f32x4, riscv_cmpneq_f32x4,
             (f32x4)(u32x4){mask_f32(a[0] != b[0]),
                             mask_f32(a[1] != b[1]),
                             mask_f32(a[2] != b[2]),
                             mask_f32(a[3] != b[3])})

f32x4 riscv_cmpneq_scalar_f32x4(f32x4 a, f32x4 b)
{
    u32x4 bits = (u32x4)a;
    return (f32x4)(u32x4){
        mask_f32(a[0] != b[0]), bits[1], bits[2], bits[3]};
}
IOITF_BINARY(f32x4, f32x4, riscv_cmplt_f32x4,
             (f32x4)(u32x4){mask_f32(a[0] < b[0]),
                             mask_f32(a[1] < b[1]),
                             mask_f32(a[2] < b[2]),
                             mask_f32(a[3] < b[3])})

f32x4 riscv_cmplt_scalar_f32x4(f32x4 a, f32x4 b)
{
    u32x4 bits = (u32x4)a;
    return (f32x4)(u32x4){
        mask_f32(a[0] < b[0]), bits[1], bits[2], bits[3]};
}

IOITF_BINARY(f32x4, f32x4, riscv_cmple_f32x4,
             (f32x4)(u32x4){mask_f32(a[0] <= b[0]),
                             mask_f32(a[1] <= b[1]),
                             mask_f32(a[2] <= b[2]),
                             mask_f32(a[3] <= b[3])})

f32x4 riscv_cmple_scalar_f32x4(f32x4 a, f32x4 b)
{
    u32x4 bits = (u32x4)a;
    return (f32x4)(u32x4){
        mask_f32(a[0] <= b[0]), bits[1], bits[2], bits[3]};
}
IOITF_BINARY(f32x4, f32x4, riscv_cmpgt_f32x4,
             (f32x4)(u32x4){mask_f32(a[0] > b[0]),
                             mask_f32(a[1] > b[1]),
                             mask_f32(a[2] > b[2]),
                             mask_f32(a[3] > b[3])})

f32x4 riscv_cmpgt_scalar_f32x4(f32x4 a, f32x4 b)
{
    u32x4 bits = (u32x4)a;
    return (f32x4)(u32x4){
        mask_f32(a[0] > b[0]), bits[1], bits[2], bits[3]};
}

IOITF_BINARY(f32x4, f32x4, riscv_cmpge_f32x4,
             (f32x4)(u32x4){mask_f32(a[0] >= b[0]),
                             mask_f32(a[1] >= b[1]),
                             mask_f32(a[2] >= b[2]),
                             mask_f32(a[3] >= b[3])})

f32x4 riscv_cmpge_scalar_f32x4(f32x4 a, f32x4 b)
{
    u32x4 bits = (u32x4)a;
    return (f32x4)(u32x4){
        mask_f32(a[0] >= b[0]), bits[1], bits[2], bits[3]};
}
IOITF_BINARY(f32x4, f32x4, riscv_cmpnlt_f32x4,
             (f32x4)(u32x4){mask_f32(!(a[0] < b[0])),
                             mask_f32(!(a[1] < b[1])),
                             mask_f32(!(a[2] < b[2])),
                             mask_f32(!(a[3] < b[3]))})

f32x4 riscv_cmpnlt_scalar_f32x4(f32x4 a, f32x4 b)
{
    u32x4 bits = (u32x4)a;
    return (f32x4)(u32x4){
        mask_f32(!(a[0] < b[0])), bits[1], bits[2], bits[3]};
}
IOITF_BINARY(f32x4, f32x4, riscv_cmpnle_f32x4,
             (f32x4)(u32x4){mask_f32(!(a[0] <= b[0])),
                             mask_f32(!(a[1] <= b[1])),
                             mask_f32(!(a[2] <= b[2])),
                             mask_f32(!(a[3] <= b[3]))})

f32x4 riscv_cmpnle_scalar_f32x4(f32x4 a, f32x4 b)
{
    u32x4 bits = (u32x4)a;
    return (f32x4)(u32x4){
        mask_f32(!(a[0] <= b[0])), bits[1], bits[2], bits[3]};
}

IOITF_BINARY(f32x4, f32x4, riscv_cmpngt_f32x4,
             (f32x4)(u32x4){mask_f32(!(a[0] > b[0])),
                             mask_f32(!(a[1] > b[1])),
                             mask_f32(!(a[2] > b[2])),
                             mask_f32(!(a[3] > b[3]))})

f32x4 riscv_cmpngt_scalar_f32x4(f32x4 a, f32x4 b)
{
    u32x4 bits = (u32x4)a;
    return (f32x4)(u32x4){
        mask_f32(!(a[0] > b[0])), bits[1], bits[2], bits[3]};
}
IOITF_BINARY(f32x4, f32x4, riscv_cmpnge_f32x4,
             (f32x4)(u32x4){mask_f32(!(a[0] >= b[0])),
                             mask_f32(!(a[1] >= b[1])),
                             mask_f32(!(a[2] >= b[2])),
                             mask_f32(!(a[3] >= b[3]))})

f32x4 riscv_cmpnge_scalar_f32x4(f32x4 a, f32x4 b)
{
    u32x4 bits = (u32x4)a;
    return (f32x4)(u32x4){
        mask_f32(!(a[0] >= b[0])), bits[1], bits[2], bits[3]};
}
IOITF_BINARY(f32x4, f32x4, riscv_cmpord_f32x4,
             (f32x4)(u32x4){mask_f32(a[0] == a[0] && b[0] == b[0]),
                             mask_f32(a[1] == a[1] && b[1] == b[1]),
                             mask_f32(a[2] == a[2] && b[2] == b[2]),
                             mask_f32(a[3] == a[3] && b[3] == b[3])})

f32x4 riscv_cmpord_scalar_f32x4(f32x4 a, f32x4 b)
{
    u32x4 a_bits = (u32x4)a;
    u32x4 b_bits = (u32x4)b;
    int low_ordered = !nan_f32_bits(a_bits[0]) && !nan_f32_bits(b_bits[0]);
    return (f32x4)(u32x4){
        mask_f32(low_ordered), a_bits[1], a_bits[2], a_bits[3]};
}

f32x4 riscv_cmpunord_f32x4(f32x4 a, f32x4 b)
{
    u32x4 a_bits = (u32x4)a;
    u32x4 b_bits = (u32x4)b;
    return (f32x4)(u32x4){
        mask_f32(nan_f32_bits(a_bits[0]) || nan_f32_bits(b_bits[0])),
        mask_f32(nan_f32_bits(a_bits[1]) || nan_f32_bits(b_bits[1])),
        mask_f32(nan_f32_bits(a_bits[2]) || nan_f32_bits(b_bits[2])),
        mask_f32(nan_f32_bits(a_bits[3]) || nan_f32_bits(b_bits[3]))};
}

f32x4 riscv_cmpunord_scalar_f32x4(f32x4 a, f32x4 b)
{
    u32x4 a_bits = (u32x4)a;
    u32x4 b_bits = (u32x4)b;
    return (f32x4)(u32x4){
        mask_f32(nan_f32_bits(a_bits[0]) || nan_f32_bits(b_bits[0])),
        a_bits[1], a_bits[2], a_bits[3]};
}

f32x4 riscv_load_scalar_f32x4(const void *source)
{
    unsigned value;
    __builtin_memcpy(&value, source, sizeof(value));
    return (f32x4)(u32x4){value, 0, 0, 0};
}

f32x4 riscv_loadu_f32x4(const void *source)
{
    unsigned values[4];
    __builtin_memcpy(values, source, sizeof(values));
    return (f32x4)(u32x4){values[0], values[1], values[2], values[3]};
}

f32x4 riscv_loadr_f32x4(const void *source)
{
    unsigned values[4];
    __builtin_memcpy(values, source, sizeof(values));
    return (f32x4)(u32x4){values[3], values[2], values[1], values[0]};
}

void riscv_store_scalar_f32x4(void *destination, f32x4 a)
{
    unsigned value = ((u32x4)a)[0];
    __builtin_memcpy(destination, &value, sizeof(value));
}

void riscv_storeu_f32x4(void *destination, f32x4 a)
{
    unsigned values[4] = {
        ((u32x4)a)[0], ((u32x4)a)[1], ((u32x4)a)[2], ((u32x4)a)[3]};
    __builtin_memcpy(destination, values, sizeof(values));
}

void riscv_storer_f32x4(void *destination, f32x4 a)
{
    unsigned values[4] = {
        ((u32x4)a)[3], ((u32x4)a)[2], ((u32x4)a)[1], ((u32x4)a)[0]};
    __builtin_memcpy(destination, values, sizeof(values));
}
