#include "../shortcuts.h"
#include "neon_helpers.h"

/* Native ARM FP semantics are retained, including NaN/zero/conversion differences. */

IOITF_BINARY(f64x2, f64x2, arm_add_f64x2, vaddq_f64(a, b))

f64x2 arm_add_scalar_f64x2(f64x2 a, f64x2 b)
{
    f64x2 value = vaddq_f64(vdupq_n_f64(a[0]), vdupq_n_f64(b[0]));
    return vsetq_lane_f64(value[0], a, 0);
}

IOITF_BINARY(f64x2, f64x2, arm_sub_f64x2, vsubq_f64(a, b))

f64x2 arm_sub_scalar_f64x2(f64x2 a, f64x2 b)
{
    f64x2 value = vsubq_f64(vdupq_n_f64(a[0]), vdupq_n_f64(b[0]));
    return vsetq_lane_f64(value[0], a, 0);
}

IOITF_BINARY(f64x2, f64x2, arm_mul_f64x2, vmulq_f64(a, b))

f64x2 arm_mul_scalar_f64x2(f64x2 a, f64x2 b)
{
    f64x2 value = vmulq_f64(vdupq_n_f64(a[0]), vdupq_n_f64(b[0]));
    return vsetq_lane_f64(value[0], a, 0);
}

IOITF_BINARY(f64x2, f64x2, arm_div_f64x2, vdivq_f64(a, b))

f64x2 arm_div_scalar_f64x2(f64x2 a, f64x2 b)
{
    f64x2 value = vdivq_f64(vdupq_n_f64(a[0]), vdupq_n_f64(b[0]));
    return vsetq_lane_f64(value[0], a, 0);
}

IOITF_BINARY(f64x2, f64x2, arm_min_f64x2, vminq_f64(a, b))

f64x2 arm_min_scalar_f64x2(f64x2 a, f64x2 b)
{
    f64x2 value = vminq_f64(vdupq_n_f64(a[0]), vdupq_n_f64(b[0]));
    return vsetq_lane_f64(value[0], a, 0);
}

IOITF_BINARY(f64x2, f64x2, arm_max_f64x2, vmaxq_f64(a, b))

f64x2 arm_max_scalar_f64x2(f64x2 a, f64x2 b)
{
    f64x2 value = vmaxq_f64(vdupq_n_f64(a[0]), vdupq_n_f64(b[0]));
    return vsetq_lane_f64(value[0], a, 0);
}

IOITF_UNARY(f64x2, f64x2, arm_sqrt_f64x2, vsqrtq_f64(a))

f64x2 arm_sqrt_scalar_f64x2(f64x2 a)
{
    f64x2 value = vsqrtq_f64(vdupq_n_f64(a[0]));
    return vsetq_lane_f64(value[0], a, 0);
}

IOITF_BINARY(f64x2, f64x2, arm_and_f64x2, (f64x2)(((u64x2)a) & ((u64x2)b)))

IOITF_BINARY(f64x2, f64x2, arm_or_f64x2, (f64x2)(((u64x2)a) | ((u64x2)b)))

IOITF_BINARY(f64x2, f64x2, arm_xor_f64x2, (f64x2)(((u64x2)a) ^ ((u64x2)b)))

IOITF_BINARY(f64x2, f64x2, arm_andnot_f64x2, (f64x2)((~((u64x2)a)) & ((u64x2)b)))

IOITF_BINARY(f64x2, f64x2, arm_cmpeq_f64x2, (f64x2)(vceqq_f64(a,b)))

f64x2 arm_cmpeq_scalar_f64x2(f64x2 a, f64x2 b)
{
    u64x2 mask = vceqq_f64(vdupq_n_f64(a[0]),vdupq_n_f64(b[0]));
    u64x2 bits = (u64x2)a;
    bits[0] = mask[0];
    return (f64x2)bits;
}

IOITF_BINARY(f64x2, f64x2, arm_cmpneq_f64x2, (f64x2)(~vceqq_f64(a,b)))

f64x2 arm_cmpneq_scalar_f64x2(f64x2 a, f64x2 b)
{
    u64x2 mask = ~vceqq_f64(vdupq_n_f64(a[0]),vdupq_n_f64(b[0]));
    u64x2 bits = (u64x2)a;
    bits[0] = mask[0];
    return (f64x2)bits;
}

IOITF_BINARY(f64x2, f64x2, arm_cmplt_f64x2, (f64x2)(vcltq_f64(a,b)))

f64x2 arm_cmplt_scalar_f64x2(f64x2 a, f64x2 b)
{
    u64x2 mask = vcltq_f64(vdupq_n_f64(a[0]),vdupq_n_f64(b[0]));
    u64x2 bits = (u64x2)a;
    bits[0] = mask[0];
    return (f64x2)bits;
}

IOITF_BINARY(f64x2, f64x2, arm_cmple_f64x2, (f64x2)(vcleq_f64(a,b)))

f64x2 arm_cmple_scalar_f64x2(f64x2 a, f64x2 b)
{
    u64x2 mask = vcleq_f64(vdupq_n_f64(a[0]),vdupq_n_f64(b[0]));
    u64x2 bits = (u64x2)a;
    bits[0] = mask[0];
    return (f64x2)bits;
}

IOITF_BINARY(f64x2, f64x2, arm_cmpgt_f64x2, (f64x2)(vcgtq_f64(a,b)))

f64x2 arm_cmpgt_scalar_f64x2(f64x2 a, f64x2 b)
{
    u64x2 mask = vcgtq_f64(vdupq_n_f64(a[0]),vdupq_n_f64(b[0]));
    u64x2 bits = (u64x2)a;
    bits[0] = mask[0];
    return (f64x2)bits;
}

IOITF_BINARY(f64x2, f64x2, arm_cmpge_f64x2, (f64x2)(vcgeq_f64(a,b)))

f64x2 arm_cmpge_scalar_f64x2(f64x2 a, f64x2 b)
{
    u64x2 mask = vcgeq_f64(vdupq_n_f64(a[0]),vdupq_n_f64(b[0]));
    u64x2 bits = (u64x2)a;
    bits[0] = mask[0];
    return (f64x2)bits;
}

IOITF_BINARY(f64x2, f64x2, arm_cmpnlt_f64x2, (f64x2)(~vcltq_f64(a,b)))

f64x2 arm_cmpnlt_scalar_f64x2(f64x2 a, f64x2 b)
{
    u64x2 mask = ~vcltq_f64(vdupq_n_f64(a[0]),vdupq_n_f64(b[0]));
    u64x2 bits = (u64x2)a;
    bits[0] = mask[0];
    return (f64x2)bits;
}

IOITF_BINARY(f64x2, f64x2, arm_cmpnle_f64x2, (f64x2)(~vcleq_f64(a,b)))

f64x2 arm_cmpnle_scalar_f64x2(f64x2 a, f64x2 b)
{
    u64x2 mask = ~vcleq_f64(vdupq_n_f64(a[0]),vdupq_n_f64(b[0]));
    u64x2 bits = (u64x2)a;
    bits[0] = mask[0];
    return (f64x2)bits;
}

IOITF_BINARY(f64x2, f64x2, arm_cmpngt_f64x2, (f64x2)(~vcgtq_f64(a,b)))

f64x2 arm_cmpngt_scalar_f64x2(f64x2 a, f64x2 b)
{
    u64x2 mask = ~vcgtq_f64(vdupq_n_f64(a[0]),vdupq_n_f64(b[0]));
    u64x2 bits = (u64x2)a;
    bits[0] = mask[0];
    return (f64x2)bits;
}

IOITF_BINARY(f64x2, f64x2, arm_cmpnge_f64x2, (f64x2)(~vcgeq_f64(a,b)))

f64x2 arm_cmpnge_scalar_f64x2(f64x2 a, f64x2 b)
{
    u64x2 mask = ~vcgeq_f64(vdupq_n_f64(a[0]),vdupq_n_f64(b[0]));
    u64x2 bits = (u64x2)a;
    bits[0] = mask[0];
    return (f64x2)bits;
}

IOITF_BINARY(f64x2, f64x2, arm_cmpord_f64x2, (f64x2)((vceqq_f64(a,a) & vceqq_f64(b,b))))

f64x2 arm_cmpord_scalar_f64x2(f64x2 a, f64x2 b)
{
    u64x2 mask = (vceqq_f64(vdupq_n_f64(a[0]),vdupq_n_f64(a[0])) & vceqq_f64(vdupq_n_f64(b[0]),vdupq_n_f64(b[0])));
    u64x2 bits = (u64x2)a;
    bits[0] = mask[0];
    return (f64x2)bits;
}

IOITF_BINARY(f64x2, f64x2, arm_cmpunord_f64x2, (f64x2)(~(vceqq_f64(a,a) & vceqq_f64(b,b))))

f64x2 arm_cmpunord_scalar_f64x2(f64x2 a, f64x2 b)
{
    u64x2 mask = ~(vceqq_f64(vdupq_n_f64(a[0]),vdupq_n_f64(a[0])) & vceqq_f64(vdupq_n_f64(b[0]),vdupq_n_f64(b[0])));
    u64x2 bits = (u64x2)a;
    bits[0] = mask[0];
    return (f64x2)bits;
}

IOITF_BINARY(int, f64x2, arm_comieq_f64x2, a[0] == b[0])

IOITF_BINARY(int, f64x2, arm_comineq_f64x2, a[0] != b[0])

IOITF_BINARY(int, f64x2, arm_comilt_f64x2, a[0] < b[0])

IOITF_BINARY(int, f64x2, arm_comile_f64x2, a[0] <= b[0])

IOITF_BINARY(int, f64x2, arm_comigt_f64x2, a[0] > b[0])

IOITF_BINARY(int, f64x2, arm_comige_f64x2, a[0] >= b[0])

f64x2 arm_set1_f64x2(double value) { return vdupq_n_f64(value); }
f64x2 arm_set_scalar_f64x2(double value) { return (f64x2){value, 0}; }
f64x2 arm_load1_f64x2(const void *source)
{
    unsigned long long value;
    __builtin_memcpy(&value, source, sizeof(value));
    return (f64x2)arm_splats(value);
}
int arm_movemask_f64x2(f64x2 a)
{
    u64x2 bits = (u64x2)a;
    unsigned mask = 0;
    for (unsigned lane = 0; lane < 2; ++lane) mask |= (unsigned)(bits[lane] >> 63) << lane;
    return (int)mask;
}

f64x2 arm_set_f64x2(double high,double low) { return (f64x2){low,high}; }
f64x2 arm_setr_f64x2(double low,double high) { return (f64x2){low,high}; }
IOITF_UNARY(f64x2, i64x2, arm_cast_i64x2_f64x2, vreinterpretq_f64_s64(a))
IOITF_UNARY(i64x2, f64x2, arm_cast_f64x2_i64x2, vreinterpretq_s64_f64(a))
IOITF_BINARY(f64x2, f64x2, arm_move_f64x2, vsetq_lane_f64(b[0],a,0))
IOITF_BINARY(f64x2, f64x2, arm_unpacklo_f64x2, vzip1q_f64(a,b))
IOITF_BINARY(f64x2, f64x2, arm_unpackhi_f64x2, vzip2q_f64(a,b))
f64x2 arm_shuffle_f64x2(f64x2 a,f64x2 b,unsigned imm)
{ return (f64x2){a[imm&1U],b[(imm>>1)&1U]}; }
IOITF_UNARY(i32x4, f64x2, arm_cvt_f64x2_i32x4, (i32x4){arm_nearest_f64_i32(a[0]),arm_nearest_f64_i32(a[1]),0,0})
IOITF_UNARY(i32x4, f64x2, arm_cvtt_f64x2_i32x4, (i32x4){arm_truncate_f64_i32(a[0]),arm_truncate_f64_i32(a[1]),0,0})
IOITF_UNARY(int, f64x2, arm_cvt_scalar_f64x2_i32, arm_nearest_f64_i32(a[0]))
IOITF_UNARY(int, f64x2, arm_cvtt_scalar_f64x2_i32, arm_truncate_f64_i32(a[0]))
IOITF_UNARY(long long, f64x2, arm_cvt_scalar_f64x2_i64, vcvtnd_s64_f64(a[0]))
IOITF_UNARY(long long, f64x2, arm_cvtt_scalar_f64x2_i64, vcvtd_s64_f64(a[0]))

f64x2 arm_loadu_f64x2(const void *source)
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

f64x2 arm_load_scalar_f64x2(const void *source)
{
    return (f64x2)(u64x2){load_u64(source), 0};
}

f64x2 arm_loadl_f64x2(f64x2 a, const void *source)
{
    return (f64x2)(u64x2){load_u64(source), ((u64x2)a)[1]};
}

f64x2 arm_loadh_f64x2(f64x2 a, const void *source)
{
    return (f64x2)(u64x2){((u64x2)a)[0], load_u64(source)};
}

f64x2 arm_loadr_f64x2(const void *source)
{
    unsigned long long lanes[2];
    __builtin_memcpy(lanes, source, sizeof(lanes));
    return (f64x2)(u64x2){lanes[1], lanes[0]};
}

void arm_store_scalar_f64x2(void *destination, f64x2 a)
{
    unsigned long long value = ((u64x2)a)[0];
    __builtin_memcpy(destination, &value, sizeof(value));
}

void arm_storeu_f64x2(void *destination, f64x2 a)
{
    unsigned long long values[2] = {((u64x2)a)[0], ((u64x2)a)[1]};
    __builtin_memcpy(destination, values, sizeof(values));
}

void arm_storeh_f64x2(void *destination, f64x2 a)
{
    unsigned long long value = ((u64x2)a)[1];
    __builtin_memcpy(destination, &value, sizeof(value));
}

void arm_storer_f64x2(void *destination, f64x2 a)
{
    unsigned long long values[2] = {((u64x2)a)[1], ((u64x2)a)[0]};
    __builtin_memcpy(destination, values, sizeof(values));
}
