/* Native RV64/RVV adaptation of the project SIMD wrappers. */
#include "../shortcuts.h"
#include "rvv128.h"


IOITF_BINARY(f64x2, f64x2, riscv_add_f64x2, rvv_add(a, b))
IOITF_BINARY(f64x2, f64x2, riscv_sub_f64x2, rvv_sub(a, b))
IOITF_BINARY(f64x2, f64x2, riscv_mul_f64x2, rvv_mul(a, b))
static unsigned long long div_f64_bits(unsigned long long a,
                                       unsigned long long b,
                                       unsigned long long result)
{
    unsigned long long a_magnitude = a & 0x7fffffffffffffffULL;
    unsigned long long b_magnitude = b & 0x7fffffffffffffffULL;
    int a_nan = (a & 0x7ff0000000000000ULL) == 0x7ff0000000000000ULL &&
                (a & 0x000fffffffffffffULL) != 0ULL;
    int b_nan = (b & 0x7ff0000000000000ULL) == 0x7ff0000000000000ULL &&
                (b & 0x000fffffffffffffULL) != 0ULL;
    if (a_nan) {
        return a | 0x0008000000000000ULL;
    }
    if (b_nan) {
        return b | 0x0008000000000000ULL;
    }
    if ((a_magnitude == 0ULL && b_magnitude == 0ULL) ||
        (a_magnitude == 0x7ff0000000000000ULL &&
         b_magnitude == 0x7ff0000000000000ULL)) {
        return 0xfff8000000000000ULL;
    }
    return result;
}

f64x2 riscv_div_f64x2(f64x2 a, f64x2 b)
{
    u64x2 a_bits = (u64x2)a;
    u64x2 b_bits = (u64x2)b;
    u64x2 result = (u64x2)rvv_div(a, b);
    return (f64x2)(u64x2){
        div_f64_bits(a_bits[0], b_bits[0], result[0]),
        div_f64_bits(a_bits[1], b_bits[1], result[1])};
}

static unsigned long long sqrt_f64_bits(unsigned long long input,
                                        unsigned long long result)
{
    unsigned long long magnitude = input & 0x7fffffffffffffffULL;
    if ((input & 0x7ff0000000000000ULL) == 0x7ff0000000000000ULL &&
        (input & 0x000fffffffffffffULL) != 0ULL) {
        return input | 0x0008000000000000ULL;
    }
    if ((input & 0x8000000000000000ULL) != 0ULL && magnitude != 0ULL) {
        return 0xfff8000000000000ULL;
    }
    return result;
}

f64x2 riscv_sqrt_f64x2(f64x2 a)
{
    u64x2 input = (u64x2)a;
    u64x2 result = (u64x2)rvv_sqrt(a);
    return (f64x2)(u64x2){
        sqrt_f64_bits(input[0], result[0]),
        sqrt_f64_bits(input[1], result[1])};
}

f64x2 riscv_sqrt_scalar_f64x2(f64x2 a)
{
    u64x2 input = (u64x2)a;
    u64x2 result = (u64x2)rvv_sqrt(a);
    return (f64x2)(u64x2){sqrt_f64_bits(input[0], result[0]), input[1]};
}
IOITF_BINARY(f64x2, f64x2, riscv_and_f64x2, (f64x2)((u64x2)a & (u64x2)b))
IOITF_BINARY(f64x2, f64x2, riscv_or_f64x2, (f64x2)((u64x2)a | (u64x2)b))
IOITF_BINARY(f64x2, f64x2, riscv_xor_f64x2, (f64x2)((u64x2)a ^ (u64x2)b))

f64x2 riscv_set1_f64x2(double x)
{
    return rvv_splats(x);
}

f64x2 riscv_set_scalar_f64x2(double value)
{
    union { double value; unsigned long long bits; } scalar = {value};
    return (f64x2)(u64x2){scalar.bits, 0};
}

f64x2 riscv_load1_f64x2(const void *source)
{
    unsigned long long value;
    __builtin_memcpy(&value, source, sizeof(value));
    return (f64x2)(u64x2){value, value};
}

IOITF_BINARY(f64x2, f64x2, riscv_move_f64x2, (f64x2){b[0], a[1]})
IOITF_BINARY(f64x2, f64x2, riscv_unpacklo_f64x2, (f64x2){a[0], b[0]})
IOITF_BINARY(f64x2, f64x2, riscv_unpackhi_f64x2, (f64x2){a[1], b[1]})

static unsigned long long mask(int yes)
{
    return yes ? ~0ULL : 0ULL;
}

IOITF_BINARY(static int, double, ordered, a == a && b == b)
IOITF_BINARY(f64x2, f64x2, riscv_cmpeq_f64x2, (f64x2)(u64x2){mask(a[0] == b[0]), mask(a[1] == b[1])})
IOITF_BINARY(f64x2, f64x2, riscv_cmpeq_scalar_f64x2, (f64x2)(u64x2){mask(a[0] == b[0]), ((u64x2)a)[1]})
IOITF_BINARY(f64x2, f64x2, riscv_cmplt_f64x2, (f64x2)(u64x2){mask(a[0] < b[0]), mask(a[1] < b[1])})
IOITF_BINARY(f64x2, f64x2, riscv_cmplt_scalar_f64x2, (f64x2)(u64x2){mask(a[0] < b[0]), ((u64x2)a)[1]})
IOITF_BINARY(f64x2, f64x2, riscv_cmple_f64x2, (f64x2)(u64x2){mask(a[0] <= b[0]), mask(a[1] <= b[1])})
IOITF_BINARY(f64x2, f64x2, riscv_cmple_scalar_f64x2, (f64x2)(u64x2){mask(a[0] <= b[0]), ((u64x2)a)[1]})
IOITF_BINARY(f64x2, f64x2, riscv_cmpgt_f64x2, (f64x2)(u64x2){mask(a[0] > b[0]), mask(a[1] > b[1])})
IOITF_BINARY(f64x2, f64x2, riscv_cmpgt_scalar_f64x2, (f64x2)(u64x2){mask(a[0] > b[0]), ((u64x2)a)[1]})
IOITF_BINARY(f64x2, f64x2, riscv_cmpge_f64x2, (f64x2)(u64x2){mask(a[0] >= b[0]), mask(a[1] >= b[1])})
IOITF_BINARY(f64x2, f64x2, riscv_cmpge_scalar_f64x2, (f64x2)(u64x2){mask(a[0] >= b[0]), ((u64x2)a)[1]})
IOITF_BINARY(f64x2, f64x2, riscv_cmpneq_f64x2, (f64x2)(u64x2){mask(a[0] != b[0]), mask(a[1] != b[1])})
IOITF_BINARY(f64x2, f64x2, riscv_cmpneq_scalar_f64x2, (f64x2)(u64x2){mask(a[0] != b[0]), ((u64x2)a)[1]})
IOITF_BINARY(f64x2, f64x2, riscv_cmpord_f64x2, (f64x2)(u64x2){mask(ordered(a[0], b[0])), mask(ordered(a[1], b[1]))})
IOITF_BINARY(f64x2, f64x2, riscv_cmpord_scalar_f64x2, (f64x2)(u64x2){mask(ordered(a[0], b[0])), ((u64x2)a)[1]})
IOITF_BINARY(f64x2, f64x2, riscv_cmpunord_f64x2, (f64x2)(u64x2){mask(!ordered(a[0], b[0])), mask(!ordered(a[1], b[1]))})
IOITF_BINARY(f64x2, f64x2, riscv_cmpunord_scalar_f64x2, (f64x2)(u64x2){mask(!ordered(a[0], b[0])), ((u64x2)a)[1]})

f64x2 riscv_shuffle_f64x2(f64x2 a, f64x2 b, unsigned imm)
{
    return (f64x2){a[imm & 1U], b[(imm >> 1) & 1U]};
}

f64x2 riscv_set_f64x2(double high, double low)
{
    return (f64x2){low, high};
}

f64x2 riscv_setr_f64x2(double low, double high)
{
    return (f64x2){low, high};
}

f64x2 riscv_cast_i64x2_f64x2(i64x2 value)
{
    return (f64x2)value;
}

i64x2 riscv_cast_f64x2_i64x2(f64x2 value)
{
    return (i64x2)value;
}

IOITF_BINARY(f64x2, f64x2, riscv_andnot_f64x2, (f64x2)((~(u64x2)a) & (u64x2)b))

int riscv_movemask_f64x2(f64x2 value)
{
    u64x2 bits = (u64x2)value;
    return (int)((bits[0] >> 63) | ((bits[1] >> 63) << 1));
}

IOITF_BINARY(f64x2, f64x2, riscv_cmpnlt_f64x2, (f64x2)(u64x2){mask(!(a[0] < b[0])), mask(!(a[1] < b[1]))})
IOITF_BINARY(f64x2, f64x2, riscv_cmpnlt_scalar_f64x2, (f64x2)(u64x2){mask(!(a[0] < b[0])), ((u64x2)a)[1]})
IOITF_BINARY(f64x2, f64x2, riscv_cmpnle_f64x2, (f64x2)(u64x2){mask(!(a[0] <= b[0])), mask(!(a[1] <= b[1]))})
IOITF_BINARY(f64x2, f64x2, riscv_cmpnle_scalar_f64x2, (f64x2)(u64x2){mask(!(a[0] <= b[0])), ((u64x2)a)[1]})
IOITF_BINARY(f64x2, f64x2, riscv_cmpngt_f64x2, (f64x2)(u64x2){mask(!(a[0] > b[0])), mask(!(a[1] > b[1]))})
IOITF_BINARY(f64x2, f64x2, riscv_cmpngt_scalar_f64x2, (f64x2)(u64x2){mask(!(a[0] > b[0])), ((u64x2)a)[1]})
IOITF_BINARY(f64x2, f64x2, riscv_cmpnge_f64x2, (f64x2)(u64x2){mask(!(a[0] >= b[0])), mask(!(a[1] >= b[1]))})
IOITF_BINARY(f64x2, f64x2, riscv_cmpnge_scalar_f64x2, (f64x2)(u64x2){mask(!(a[0] >= b[0])), ((u64x2)a)[1]})

static int nan_bits(unsigned long long bits)
{
    return (bits & 0x7ff0000000000000ULL) == 0x7ff0000000000000ULL &&
           (bits & 0x000fffffffffffffULL) != 0;
}

static unsigned long long minmax_bits(double a, double b,
                                      unsigned long long a_bits,
                                      unsigned long long b_bits,
                                      int select_min)
{
    if (nan_bits(a_bits) || nan_bits(b_bits) || a == b) {
        return b_bits;
    }
    if ((select_min && a < b) || (!select_min && a > b)) {
        return a_bits;
    }
    return b_bits;
}

f64x2 riscv_min_f64x2(f64x2 a, f64x2 b)
{
    u64x2 a_bits = (u64x2)a;
    u64x2 b_bits = (u64x2)b;
    return (f64x2)(u64x2){
        minmax_bits(a[0], b[0], a_bits[0], b_bits[0], 1),
        minmax_bits(a[1], b[1], a_bits[1], b_bits[1], 1)};
}

f64x2 riscv_min_scalar_f64x2(f64x2 a, f64x2 b)
{
    u64x2 a_bits = (u64x2)a;
    u64x2 b_bits = (u64x2)b;
    return (f64x2)(u64x2){
        minmax_bits(a[0], b[0], a_bits[0], b_bits[0], 1), a_bits[1]};
}

f64x2 riscv_max_f64x2(f64x2 a, f64x2 b)
{
    u64x2 a_bits = (u64x2)a;
    u64x2 b_bits = (u64x2)b;
    return (f64x2)(u64x2){
        minmax_bits(a[0], b[0], a_bits[0], b_bits[0], 0),
        minmax_bits(a[1], b[1], a_bits[1], b_bits[1], 0)};
}

f64x2 riscv_max_scalar_f64x2(f64x2 a, f64x2 b)
{
    u64x2 a_bits = (u64x2)a;
    u64x2 b_bits = (u64x2)b;
    return (f64x2)(u64x2){
        minmax_bits(a[0], b[0], a_bits[0], b_bits[0], 0), a_bits[1]};
}

IOITF_BINARY(int, f64x2, riscv_comieq_f64x2, ordered(a[0], b[0]) && a[0] == b[0])
IOITF_BINARY(int, f64x2, riscv_comilt_f64x2, ordered(a[0], b[0]) && a[0] < b[0])
IOITF_BINARY(int, f64x2, riscv_comile_f64x2, ordered(a[0], b[0]) && a[0] <= b[0])
IOITF_BINARY(int, f64x2, riscv_comigt_f64x2, ordered(a[0], b[0]) && a[0] > b[0])
IOITF_BINARY(int, f64x2, riscv_comige_f64x2, ordered(a[0], b[0]) && a[0] >= b[0])
IOITF_BINARY(int, f64x2, riscv_comineq_f64x2, !ordered(a[0], b[0]) || a[0] != b[0])

static int nearest_i32(double value)
{
    long long integral;
    double fraction;

    if (value != value || value < -2147483648.5 || value >= 2147483647.5) {
        return (-2147483647 - 1);
    }
    integral = (long long)value;
    fraction = value - (double)integral;
    if (fraction > 0.5 ||
        (fraction == 0.5 && integral % 2LL != 0LL)) {
        ++integral;
    } else if (fraction < -0.5 ||
               (fraction == -0.5 && integral % 2LL != 0LL)) {
        --integral;
    }
    return (int)integral;
}

static int truncate_i32(double value)
{
    if (value != value || value < -2147483648.0 || value >= 2147483648.0) {
        return (-2147483647 - 1);
    }
    return (int)value;
}

static long long nearest_i64(double value)
{
    long long integral;
    double fraction;

    if (value != value || value < -9223372036854775808.0 ||
        value >= 9223372036854775808.0) {
        return (-9223372036854775807LL - 1LL);
    }
    integral = (long long)value;
    fraction = value - (double)integral;
    if (fraction > 0.5 ||
        (fraction == 0.5 && integral % 2LL != 0LL)) {
        ++integral;
    } else if (fraction < -0.5 ||
               (fraction == -0.5 && integral % 2LL != 0LL)) {
        --integral;
    }
    return integral;
}

static long long truncate_i64(double value)
{
    if (value != value || value < -9223372036854775808.0 ||
        value >= 9223372036854775808.0) {
        return (-9223372036854775807LL - 1LL);
    }
    return (long long)value;
}

IOITF_UNARY(i32x4, f64x2, riscv_cvt_f64x2_i32x4, (i32x4){nearest_i32(a[0]), nearest_i32(a[1]), 0, 0})
IOITF_UNARY(i32x4, f64x2, riscv_cvtt_f64x2_i32x4, (i32x4){truncate_i32(a[0]), truncate_i32(a[1]), 0, 0})
IOITF_UNARY(int, f64x2, riscv_cvt_scalar_f64x2_i32, nearest_i32(a[0]))
IOITF_UNARY(int, f64x2, riscv_cvtt_scalar_f64x2_i32, truncate_i32(a[0]))
IOITF_UNARY(long long, f64x2, riscv_cvt_scalar_f64x2_i64, nearest_i64(a[0]))
IOITF_UNARY(long long, f64x2, riscv_cvtt_scalar_f64x2_i64, truncate_i64(a[0]))
IOITF_BINARY(f64x2, f64x2, riscv_add_scalar_f64x2, (f64x2){a[0] + b[0], a[1]})
IOITF_BINARY(f64x2, f64x2, riscv_sub_scalar_f64x2, (f64x2){a[0] - b[0], a[1]})
IOITF_BINARY(f64x2, f64x2, riscv_mul_scalar_f64x2, (f64x2){a[0] * b[0], a[1]})
f64x2 riscv_div_scalar_f64x2(f64x2 a, f64x2 b)
{
    u64x2 a_bits = (u64x2)a;
    u64x2 b_bits = (u64x2)b;
    union { double value; unsigned long long bits; } result = {a[0] / b[0]};
    return (f64x2)(u64x2){
        div_f64_bits(a_bits[0], b_bits[0], result.bits), a_bits[1]};
}

f64x2 riscv_loadu_f64x2(const void *source)
{
    unsigned long long lanes[2];
    __builtin_memcpy(lanes, source, sizeof(lanes));
    return (f64x2)(u64x2){lanes[0], lanes[1]};
}

static unsigned long long load_u64(const void *source)
{
    unsigned long long value;
    __builtin_memcpy(&value, source, sizeof(value));
    return value;
}

f64x2 riscv_load_scalar_f64x2(const void *source)
{
    return (f64x2)(u64x2){load_u64(source), 0};
}

f64x2 riscv_loadl_f64x2(f64x2 a, const void *source)
{
    return (f64x2)(u64x2){load_u64(source), ((u64x2)a)[1]};
}

f64x2 riscv_loadh_f64x2(f64x2 a, const void *source)
{
    return (f64x2)(u64x2){((u64x2)a)[0], load_u64(source)};
}

f64x2 riscv_loadr_f64x2(const void *source)
{
    unsigned long long lanes[2];
    __builtin_memcpy(lanes, source, sizeof(lanes));
    return (f64x2)(u64x2){lanes[1], lanes[0]};
}

void riscv_store_scalar_f64x2(void *destination, f64x2 a)
{
    unsigned long long value = ((u64x2)a)[0];
    __builtin_memcpy(destination, &value, sizeof(value));
}

void riscv_storeu_f64x2(void *destination, f64x2 a)
{
    unsigned long long values[2] = {((u64x2)a)[0], ((u64x2)a)[1]};
    __builtin_memcpy(destination, values, sizeof(values));
}

void riscv_storeh_f64x2(void *destination, f64x2 a)
{
    unsigned long long value = ((u64x2)a)[1];
    __builtin_memcpy(destination, &value, sizeof(value));
}

void riscv_storer_f64x2(void *destination, f64x2 a)
{
    unsigned long long values[2] = {((u64x2)a)[1], ((u64x2)a)[0]};
    __builtin_memcpy(destination, values, sizeof(values));
}
