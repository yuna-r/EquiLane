#include "../shortcuts.h"
#include "neon_helpers.h"

/* Native ARM FP semantics are retained, including NaN/zero/conversion differences. */

IOITF_BINARY(f32x4, f32x4, arm_add_f32x4, vaddq_f32(a, b))

f32x4 arm_add_scalar_f32x4(f32x4 a, f32x4 b)
{
    f32x4 value = vaddq_f32(vdupq_n_f32(a[0]), vdupq_n_f32(b[0]));
    return vsetq_lane_f32(value[0], a, 0);
}

IOITF_BINARY(f32x4, f32x4, arm_sub_f32x4, vsubq_f32(a, b))

f32x4 arm_sub_scalar_f32x4(f32x4 a, f32x4 b)
{
    f32x4 value = vsubq_f32(vdupq_n_f32(a[0]), vdupq_n_f32(b[0]));
    return vsetq_lane_f32(value[0], a, 0);
}

IOITF_BINARY(f32x4, f32x4, arm_mul_f32x4, vmulq_f32(a, b))

f32x4 arm_mul_scalar_f32x4(f32x4 a, f32x4 b)
{
    f32x4 value = vmulq_f32(vdupq_n_f32(a[0]), vdupq_n_f32(b[0]));
    return vsetq_lane_f32(value[0], a, 0);
}

IOITF_BINARY(f32x4, f32x4, arm_div_f32x4, vdivq_f32(a, b))

f32x4 arm_div_scalar_f32x4(f32x4 a, f32x4 b)
{
    f32x4 value = vdivq_f32(vdupq_n_f32(a[0]), vdupq_n_f32(b[0]));
    return vsetq_lane_f32(value[0], a, 0);
}

IOITF_BINARY(f32x4, f32x4, arm_min_f32x4, vminq_f32(a, b))

f32x4 arm_min_scalar_f32x4(f32x4 a, f32x4 b)
{
    f32x4 value = vminq_f32(vdupq_n_f32(a[0]), vdupq_n_f32(b[0]));
    return vsetq_lane_f32(value[0], a, 0);
}

IOITF_BINARY(f32x4, f32x4, arm_max_f32x4, vmaxq_f32(a, b))

f32x4 arm_max_scalar_f32x4(f32x4 a, f32x4 b)
{
    f32x4 value = vmaxq_f32(vdupq_n_f32(a[0]), vdupq_n_f32(b[0]));
    return vsetq_lane_f32(value[0], a, 0);
}

IOITF_UNARY(f32x4, f32x4, arm_sqrt_f32x4, vsqrtq_f32(a))

f32x4 arm_sqrt_scalar_f32x4(f32x4 a)
{
    f32x4 value = vsqrtq_f32(vdupq_n_f32(a[0]));
    return vsetq_lane_f32(value[0], a, 0);
}

IOITF_BINARY(f32x4, f32x4, arm_and_f32x4, (f32x4)(((u32x4)a) & ((u32x4)b)))

IOITF_BINARY(f32x4, f32x4, arm_or_f32x4, (f32x4)(((u32x4)a) | ((u32x4)b)))

IOITF_BINARY(f32x4, f32x4, arm_xor_f32x4, (f32x4)(((u32x4)a) ^ ((u32x4)b)))

IOITF_BINARY(f32x4, f32x4, arm_andnot_f32x4, (f32x4)((~((u32x4)a)) & ((u32x4)b)))

IOITF_BINARY(f32x4, f32x4, arm_cmpeq_f32x4, (f32x4)(vceqq_f32(a,b)))

f32x4 arm_cmpeq_scalar_f32x4(f32x4 a, f32x4 b)
{
    u32x4 mask = vceqq_f32(vdupq_n_f32(a[0]),vdupq_n_f32(b[0]));
    u32x4 bits = (u32x4)a;
    bits[0] = mask[0];
    return (f32x4)bits;
}

IOITF_BINARY(f32x4, f32x4, arm_cmpneq_f32x4, (f32x4)(~vceqq_f32(a,b)))

f32x4 arm_cmpneq_scalar_f32x4(f32x4 a, f32x4 b)
{
    u32x4 mask = ~vceqq_f32(vdupq_n_f32(a[0]),vdupq_n_f32(b[0]));
    u32x4 bits = (u32x4)a;
    bits[0] = mask[0];
    return (f32x4)bits;
}

IOITF_BINARY(f32x4, f32x4, arm_cmplt_f32x4, (f32x4)(vcltq_f32(a,b)))

f32x4 arm_cmplt_scalar_f32x4(f32x4 a, f32x4 b)
{
    u32x4 mask = vcltq_f32(vdupq_n_f32(a[0]),vdupq_n_f32(b[0]));
    u32x4 bits = (u32x4)a;
    bits[0] = mask[0];
    return (f32x4)bits;
}

IOITF_BINARY(f32x4, f32x4, arm_cmple_f32x4, (f32x4)(vcleq_f32(a,b)))

f32x4 arm_cmple_scalar_f32x4(f32x4 a, f32x4 b)
{
    u32x4 mask = vcleq_f32(vdupq_n_f32(a[0]),vdupq_n_f32(b[0]));
    u32x4 bits = (u32x4)a;
    bits[0] = mask[0];
    return (f32x4)bits;
}

IOITF_BINARY(f32x4, f32x4, arm_cmpgt_f32x4, (f32x4)(vcgtq_f32(a,b)))

f32x4 arm_cmpgt_scalar_f32x4(f32x4 a, f32x4 b)
{
    u32x4 mask = vcgtq_f32(vdupq_n_f32(a[0]),vdupq_n_f32(b[0]));
    u32x4 bits = (u32x4)a;
    bits[0] = mask[0];
    return (f32x4)bits;
}

IOITF_BINARY(f32x4, f32x4, arm_cmpge_f32x4, (f32x4)(vcgeq_f32(a,b)))

f32x4 arm_cmpge_scalar_f32x4(f32x4 a, f32x4 b)
{
    u32x4 mask = vcgeq_f32(vdupq_n_f32(a[0]),vdupq_n_f32(b[0]));
    u32x4 bits = (u32x4)a;
    bits[0] = mask[0];
    return (f32x4)bits;
}

IOITF_BINARY(f32x4, f32x4, arm_cmpnlt_f32x4, (f32x4)(~vcltq_f32(a,b)))

f32x4 arm_cmpnlt_scalar_f32x4(f32x4 a, f32x4 b)
{
    u32x4 mask = ~vcltq_f32(vdupq_n_f32(a[0]),vdupq_n_f32(b[0]));
    u32x4 bits = (u32x4)a;
    bits[0] = mask[0];
    return (f32x4)bits;
}

IOITF_BINARY(f32x4, f32x4, arm_cmpnle_f32x4, (f32x4)(~vcleq_f32(a,b)))

f32x4 arm_cmpnle_scalar_f32x4(f32x4 a, f32x4 b)
{
    u32x4 mask = ~vcleq_f32(vdupq_n_f32(a[0]),vdupq_n_f32(b[0]));
    u32x4 bits = (u32x4)a;
    bits[0] = mask[0];
    return (f32x4)bits;
}

IOITF_BINARY(f32x4, f32x4, arm_cmpngt_f32x4, (f32x4)(~vcgtq_f32(a,b)))

f32x4 arm_cmpngt_scalar_f32x4(f32x4 a, f32x4 b)
{
    u32x4 mask = ~vcgtq_f32(vdupq_n_f32(a[0]),vdupq_n_f32(b[0]));
    u32x4 bits = (u32x4)a;
    bits[0] = mask[0];
    return (f32x4)bits;
}

IOITF_BINARY(f32x4, f32x4, arm_cmpnge_f32x4, (f32x4)(~vcgeq_f32(a,b)))

f32x4 arm_cmpnge_scalar_f32x4(f32x4 a, f32x4 b)
{
    u32x4 mask = ~vcgeq_f32(vdupq_n_f32(a[0]),vdupq_n_f32(b[0]));
    u32x4 bits = (u32x4)a;
    bits[0] = mask[0];
    return (f32x4)bits;
}

IOITF_BINARY(f32x4, f32x4, arm_cmpord_f32x4, (f32x4)((vceqq_f32(a,a) & vceqq_f32(b,b))))

f32x4 arm_cmpord_scalar_f32x4(f32x4 a, f32x4 b)
{
    u32x4 mask = (vceqq_f32(vdupq_n_f32(a[0]),vdupq_n_f32(a[0])) & vceqq_f32(vdupq_n_f32(b[0]),vdupq_n_f32(b[0])));
    u32x4 bits = (u32x4)a;
    bits[0] = mask[0];
    return (f32x4)bits;
}

IOITF_BINARY(f32x4, f32x4, arm_cmpunord_f32x4, (f32x4)(~(vceqq_f32(a,a) & vceqq_f32(b,b))))

f32x4 arm_cmpunord_scalar_f32x4(f32x4 a, f32x4 b)
{
    u32x4 mask = ~(vceqq_f32(vdupq_n_f32(a[0]),vdupq_n_f32(a[0])) & vceqq_f32(vdupq_n_f32(b[0]),vdupq_n_f32(b[0])));
    u32x4 bits = (u32x4)a;
    bits[0] = mask[0];
    return (f32x4)bits;
}

IOITF_BINARY(int, f32x4, arm_comieq_f32x4, a[0] == b[0])

IOITF_BINARY(int, f32x4, arm_comineq_f32x4, a[0] != b[0])

IOITF_BINARY(int, f32x4, arm_comilt_f32x4, a[0] < b[0])

IOITF_BINARY(int, f32x4, arm_comile_f32x4, a[0] <= b[0])

IOITF_BINARY(int, f32x4, arm_comigt_f32x4, a[0] > b[0])

IOITF_BINARY(int, f32x4, arm_comige_f32x4, a[0] >= b[0])

f32x4 arm_set1_f32x4(float value) { return vdupq_n_f32(value); }
f32x4 arm_set_scalar_f32x4(float value) { return (f32x4){value, 0, 0, 0}; }
f32x4 arm_load1_f32x4(const void *source)
{
    unsigned value;
    __builtin_memcpy(&value, source, sizeof(value));
    return (f32x4)arm_splats(value);
}
int arm_movemask_f32x4(f32x4 a)
{
    u32x4 bits = (u32x4)a;
    unsigned mask = 0;
    for (unsigned lane = 0; lane < 4; ++lane) mask |= (unsigned)(bits[lane] >> 31) << lane;
    return (int)mask;
}

f32x4 arm_set_f32x4(float lane3,float lane2,float lane1,float lane0) { return (f32x4){lane0,lane1,lane2,lane3}; }
f32x4 arm_setr_f32x4(float lane0,float lane1,float lane2,float lane3) { return (f32x4){lane0,lane1,lane2,lane3}; }
IOITF_UNARY(f32x4, f64x2, arm_cast_f64x2_f32x4, vreinterpretq_f32_f64(a))
IOITF_UNARY(f64x2, f32x4, arm_cast_f32x4_f64x2, vreinterpretq_f64_f32(a))
IOITF_UNARY(i32x4, f32x4, arm_cast_f32x4_i32x4, vreinterpretq_s32_f32(a))
IOITF_UNARY(f32x4, i32x4, arm_cast_i32x4_f32x4, vreinterpretq_f32_s32(a))
IOITF_BINARY(f32x4, f32x4, arm_unpacklo_f32x4, vzip1q_f32(a,b))
IOITF_BINARY(f32x4, f32x4, arm_unpackhi_f32x4, vzip2q_f32(a,b))
IOITF_BINARY(f32x4, f32x4, arm_movehl_f32x4, (f32x4){b[2],b[3],a[2],a[3]})
IOITF_BINARY(f32x4, f32x4, arm_movelh_f32x4, (f32x4){a[0],a[1],b[0],b[1]})
IOITF_BINARY(f32x4, f32x4, arm_move_f32x4, vsetq_lane_f32(b[0],a,0))
f32x4 arm_shuffle_f32x4(f32x4 a,f32x4 b,unsigned imm)
{ return (f32x4){a[imm&3U],a[(imm>>2)&3U],b[(imm>>4)&3U],b[(imm>>6)&3U]}; }
/* Conversions use real FCVT/SCVTF instructions; ARM saturation/NaN results are not rewritten to SSE values. */
IOITF_UNARY(f64x2, f32x4, arm_cvt_f32x4_f64x2, vcvt_f64_f32(vget_low_f32(a)))
IOITF_UNARY(f64x2, i32x4, arm_cvt_i32x4_f64x2, vcvtq_f64_s64(vmovl_s32(vget_low_s32(a))))
IOITF_UNARY(f32x4, f64x2, arm_cvt_f64x2_f32x4, vcombine_f32(vcvt_f32_f64(a),vdup_n_f32(0)))
IOITF_UNARY(f32x4, i32x4, arm_cvt_i32x4_f32x4, vcvtq_f32_s32(a))
IOITF_UNARY(i32x4, f32x4, arm_cvt_f32x4_i32x4, vcvtnq_s32_f32(a))
IOITF_UNARY(i32x4, f32x4, arm_cvtt_f32x4_i32x4, vcvtq_s32_f32(a))
IOITF_UNARY(int, f32x4, arm_cvt_scalar_f32x4_i32, vcvtns_s32_f32(a[0]))
IOITF_UNARY(int, f32x4, arm_cvtt_scalar_f32x4_i32, vcvts_s32_f32(a[0]))
f32x4 arm_cvt_scalar_f64x2_f32x4(f32x4 a,f64x2 b)
{ return vsetq_lane_f32(vcvt_f32_f64(vdupq_n_f64(b[0]))[0],a,0); }
f64x2 arm_cvt_scalar_f32x4_f64x2(f64x2 a,f32x4 b)
{ return vsetq_lane_f64(vcvt_f64_f32(vdup_n_f32(b[0]))[0],a,0); }
f32x4 arm_cvt_i32_f32x4(f32x4 a,int value)
{ return vsetq_lane_f32(vcvtq_f32_s32(vdupq_n_s32(value))[0],a,0); }
f64x2 arm_cvt_i32_f64x2(f64x2 a,int value)
{ return vsetq_lane_f64(vcvtq_f64_s64(vdupq_n_s64(value))[0],a,0); }
f64x2 arm_cvt_i64_f64x2(f64x2 a,long long value)
{ return vsetq_lane_f64(vcvtq_f64_s64(vdupq_n_s64(value))[0],a,0); }

f32x4 arm_load_scalar_f32x4(const void *source)
{
    unsigned value;
    __builtin_memcpy(&value, source, sizeof(value));
    return (f32x4)(u32x4){value, 0, 0, 0};
}

f32x4 arm_loadu_f32x4(const void *source)
{
    unsigned values[4];
    __builtin_memcpy(values, source, sizeof(values));
    return (f32x4)(u32x4){values[0], values[1], values[2], values[3]};
}

f32x4 arm_loadr_f32x4(const void *source)
{
    unsigned values[4];
    __builtin_memcpy(values, source, sizeof(values));
    return (f32x4)(u32x4){values[3], values[2], values[1], values[0]};
}

void arm_store_scalar_f32x4(void *destination, f32x4 a)
{
    unsigned value = ((u32x4)a)[0];
    __builtin_memcpy(destination, &value, sizeof(value));
}

void arm_storeu_f32x4(void *destination, f32x4 a)
{
    unsigned values[4] = {
        ((u32x4)a)[0], ((u32x4)a)[1], ((u32x4)a)[2], ((u32x4)a)[3]};
    __builtin_memcpy(destination, values, sizeof(values));
}

void arm_storer_f32x4(void *destination, f32x4 a)
{
    unsigned values[4] = {
        ((u32x4)a)[3], ((u32x4)a)[2], ((u32x4)a)[1], ((u32x4)a)[0]};
    __builtin_memcpy(destination, values, sizeof(values));
}
