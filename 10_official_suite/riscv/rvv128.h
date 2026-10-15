#ifndef EQUILANE_RVV128_H
#define EQUILANE_RVV128_H
/* RVV 1.0 backend with a fixed 16-byte C-vector interface.
 * RVV values never cross the public ABI. Each intrinsic uses AVL=128/SEW,
 * so larger architectural VLEN does not change logical lane count.
 * Lane/pack adaptations below are native RISC-V C, not Python fixtures.
 */
#if !defined(__riscv) || __riscv_xlen != 64 || !defined(__riscv_vector)
#error "EquiLane RVV wrappers require RV64 with RVV 1.0"
#endif
#if !defined(__riscv_flen) || __riscv_flen < 64
#error "EquiLane RVV floating-point wrappers require the D extension"
#endif
#if defined(__riscv_v_fixed_vlen)
#error "Use -mrvv-vector-bits=scalable: the 16-byte ABI must support larger VLEN"
#endif
#if defined(__BYTE_ORDER__) && __BYTE_ORDER__ != __ORDER_LITTLE_ENDIAN__
#error "EquiLane schema v1 requires little-endian vector storage"
#endif
#include <riscv_vector.h>
#include <stdint.h>
typedef signed char i8x16 __attribute__((vector_size(16)));
_Static_assert(sizeof(i8x16) == 16, "fixed128 ABI");
static inline i8x16 rvv_splat_i8x16(signed char x)
{
    i8x16 out;
    __riscv_vse8_v_i8m1((int8_t *)&out,
        __riscv_vmv_v_x_i8m1(x, 16), 16);
    return out;
}
typedef unsigned char u8x16 __attribute__((vector_size(16)));
_Static_assert(sizeof(u8x16) == 16, "fixed128 ABI");
static inline u8x16 rvv_splat_u8x16(unsigned char x)
{
    u8x16 out;
    __riscv_vse8_v_u8m1((uint8_t *)&out,
        __riscv_vmv_v_x_u8m1(x, 16), 16);
    return out;
}
typedef short i16x8 __attribute__((vector_size(16)));
_Static_assert(sizeof(i16x8) == 16, "fixed128 ABI");
static inline i16x8 rvv_splat_i16x8(short x)
{
    i16x8 out;
    __riscv_vse16_v_i16m1((int16_t *)&out,
        __riscv_vmv_v_x_i16m1(x, 8), 8);
    return out;
}
typedef unsigned short u16x8 __attribute__((vector_size(16)));
_Static_assert(sizeof(u16x8) == 16, "fixed128 ABI");
static inline u16x8 rvv_splat_u16x8(unsigned short x)
{
    u16x8 out;
    __riscv_vse16_v_u16m1((uint16_t *)&out,
        __riscv_vmv_v_x_u16m1(x, 8), 8);
    return out;
}
typedef int i32x4 __attribute__((vector_size(16)));
_Static_assert(sizeof(i32x4) == 16, "fixed128 ABI");
static inline i32x4 rvv_splat_i32x4(int x)
{
    i32x4 out;
    __riscv_vse32_v_i32m1((int32_t *)&out,
        __riscv_vmv_v_x_i32m1(x, 4), 4);
    return out;
}
typedef unsigned int u32x4 __attribute__((vector_size(16)));
_Static_assert(sizeof(u32x4) == 16, "fixed128 ABI");
static inline u32x4 rvv_splat_u32x4(unsigned int x)
{
    u32x4 out;
    __riscv_vse32_v_u32m1((uint32_t *)&out,
        __riscv_vmv_v_x_u32m1(x, 4), 4);
    return out;
}
typedef long long i64x2 __attribute__((vector_size(16)));
_Static_assert(sizeof(i64x2) == 16, "fixed128 ABI");
static inline i64x2 rvv_splat_i64x2(long long x)
{
    i64x2 out;
    __riscv_vse64_v_i64m1((int64_t *)&out,
        __riscv_vmv_v_x_i64m1(x, 2), 2);
    return out;
}
typedef unsigned long long u64x2 __attribute__((vector_size(16)));
_Static_assert(sizeof(u64x2) == 16, "fixed128 ABI");
static inline u64x2 rvv_splat_u64x2(unsigned long long x)
{
    u64x2 out;
    __riscv_vse64_v_u64m1((uint64_t *)&out,
        __riscv_vmv_v_x_u64m1(x, 2), 2);
    return out;
}
typedef float f32x4 __attribute__((vector_size(16)));
_Static_assert(sizeof(f32x4) == 16, "fixed128 ABI");
static inline f32x4 rvv_splat_f32x4(float x)
{
    f32x4 out;
    __riscv_vse32_v_f32m1((float *)&out,
        __riscv_vfmv_v_f_f32m1(x, 4), 4);
    return out;
}
typedef double f64x2 __attribute__((vector_size(16)));
_Static_assert(sizeof(f64x2) == 16, "fixed128 ABI");
static inline f64x2 rvv_splat_f64x2(double x)
{
    f64x2 out;
    __riscv_vse64_v_f64m1((double *)&out,
        __riscv_vfmv_v_f_f64m1(x, 2), 2);
    return out;
}
#define rvv_splats(x) _Generic((x), signed char: rvv_splat_i8x16, unsigned char: rvv_splat_u8x16, short: rvv_splat_i16x8, unsigned short: rvv_splat_u16x8, int: rvv_splat_i32x4, unsigned int: rvv_splat_u32x4, long long: rvv_splat_i64x2, unsigned long long: rvv_splat_u64x2, float: rvv_splat_f32x4, double: rvv_splat_f64x2)(x)
static inline f32x4 rvv_add_f32x4(f32x4 a, f32x4 b)
{
    f32x4 out;
    __riscv_vse32_v_f32m1((float *)&out,
        __riscv_vfadd_vv_f32m1(
            __riscv_vle32_v_f32m1((const float *)&a, 4),
            __riscv_vle32_v_f32m1((const float *)&b, 4), 4), 4);
    return out;
}
static inline f32x4 rvv_sub_f32x4(f32x4 a, f32x4 b)
{
    f32x4 out;
    __riscv_vse32_v_f32m1((float *)&out,
        __riscv_vfsub_vv_f32m1(
            __riscv_vle32_v_f32m1((const float *)&a, 4),
            __riscv_vle32_v_f32m1((const float *)&b, 4), 4), 4);
    return out;
}
static inline f32x4 rvv_mul_f32x4(f32x4 a, f32x4 b)
{
    f32x4 out;
    __riscv_vse32_v_f32m1((float *)&out,
        __riscv_vfmul_vv_f32m1(
            __riscv_vle32_v_f32m1((const float *)&a, 4),
            __riscv_vle32_v_f32m1((const float *)&b, 4), 4), 4);
    return out;
}
static inline f32x4 rvv_div_f32x4(f32x4 a, f32x4 b)
{
    f32x4 out;
    __riscv_vse32_v_f32m1((float *)&out,
        __riscv_vfdiv_vv_f32m1(
            __riscv_vle32_v_f32m1((const float *)&a, 4),
            __riscv_vle32_v_f32m1((const float *)&b, 4), 4), 4);
    return out;
}
static inline f32x4 rvv_sqrt_f32x4(f32x4 a)
{
    f32x4 out;
    __riscv_vse32_v_f32m1((float *)&out,
        __riscv_vfsqrt_v_f32m1(__riscv_vle32_v_f32m1((const float *)&a, 4), 4), 4);
    return out;
}
static inline f64x2 rvv_add_f64x2(f64x2 a, f64x2 b)
{
    f64x2 out;
    __riscv_vse64_v_f64m1((double *)&out,
        __riscv_vfadd_vv_f64m1(
            __riscv_vle64_v_f64m1((const double *)&a, 2),
            __riscv_vle64_v_f64m1((const double *)&b, 2), 2), 2);
    return out;
}
static inline f64x2 rvv_sub_f64x2(f64x2 a, f64x2 b)
{
    f64x2 out;
    __riscv_vse64_v_f64m1((double *)&out,
        __riscv_vfsub_vv_f64m1(
            __riscv_vle64_v_f64m1((const double *)&a, 2),
            __riscv_vle64_v_f64m1((const double *)&b, 2), 2), 2);
    return out;
}
static inline f64x2 rvv_mul_f64x2(f64x2 a, f64x2 b)
{
    f64x2 out;
    __riscv_vse64_v_f64m1((double *)&out,
        __riscv_vfmul_vv_f64m1(
            __riscv_vle64_v_f64m1((const double *)&a, 2),
            __riscv_vle64_v_f64m1((const double *)&b, 2), 2), 2);
    return out;
}
static inline f64x2 rvv_div_f64x2(f64x2 a, f64x2 b)
{
    f64x2 out;
    __riscv_vse64_v_f64m1((double *)&out,
        __riscv_vfdiv_vv_f64m1(
            __riscv_vle64_v_f64m1((const double *)&a, 2),
            __riscv_vle64_v_f64m1((const double *)&b, 2), 2), 2);
    return out;
}
static inline f64x2 rvv_sqrt_f64x2(f64x2 a)
{
    f64x2 out;
    __riscv_vse64_v_f64m1((double *)&out,
        __riscv_vfsqrt_v_f64m1(__riscv_vle64_v_f64m1((const double *)&a, 2), 2), 2);
    return out;
}
#define rvv_add(a, b) _Generic((a), f32x4: rvv_add_f32x4, f64x2: rvv_add_f64x2)(a, b)
#define rvv_sub(a, b) _Generic((a), f32x4: rvv_sub_f32x4, f64x2: rvv_sub_f64x2)(a, b)
#define rvv_mul(a, b) _Generic((a), f32x4: rvv_mul_f32x4, f64x2: rvv_mul_f64x2)(a, b)
#define rvv_div(a, b) _Generic((a), f32x4: rvv_div_f32x4, f64x2: rvv_div_f64x2)(a, b)
#define rvv_sqrt(a) _Generic((a), f32x4: rvv_sqrt_f32x4, f64x2: rvv_sqrt_f64x2)(a)
static inline i8x16 rvv_adds_i8x16(i8x16 a, i8x16 b)
{
    i8x16 out;
    __riscv_vse8_v_i8m1((int8_t *)&out,
        __riscv_vsadd_vv_i8m1(
            __riscv_vle8_v_i8m1((const int8_t *)&a, 16),
            __riscv_vle8_v_i8m1((const int8_t *)&b, 16), 16), 16);
    return out;
}
static inline i8x16 rvv_subs_i8x16(i8x16 a, i8x16 b)
{
    i8x16 out;
    __riscv_vse8_v_i8m1((int8_t *)&out,
        __riscv_vssub_vv_i8m1(
            __riscv_vle8_v_i8m1((const int8_t *)&a, 16),
            __riscv_vle8_v_i8m1((const int8_t *)&b, 16), 16), 16);
    return out;
}
static inline u8x16 rvv_adds_u8x16(u8x16 a, u8x16 b)
{
    u8x16 out;
    __riscv_vse8_v_u8m1((uint8_t *)&out,
        __riscv_vsaddu_vv_u8m1(
            __riscv_vle8_v_u8m1((const uint8_t *)&a, 16),
            __riscv_vle8_v_u8m1((const uint8_t *)&b, 16), 16), 16);
    return out;
}
static inline u8x16 rvv_subs_u8x16(u8x16 a, u8x16 b)
{
    u8x16 out;
    __riscv_vse8_v_u8m1((uint8_t *)&out,
        __riscv_vssubu_vv_u8m1(
            __riscv_vle8_v_u8m1((const uint8_t *)&a, 16),
            __riscv_vle8_v_u8m1((const uint8_t *)&b, 16), 16), 16);
    return out;
}
static inline i16x8 rvv_adds_i16x8(i16x8 a, i16x8 b)
{
    i16x8 out;
    __riscv_vse16_v_i16m1((int16_t *)&out,
        __riscv_vsadd_vv_i16m1(
            __riscv_vle16_v_i16m1((const int16_t *)&a, 8),
            __riscv_vle16_v_i16m1((const int16_t *)&b, 8), 8), 8);
    return out;
}
static inline i16x8 rvv_subs_i16x8(i16x8 a, i16x8 b)
{
    i16x8 out;
    __riscv_vse16_v_i16m1((int16_t *)&out,
        __riscv_vssub_vv_i16m1(
            __riscv_vle16_v_i16m1((const int16_t *)&a, 8),
            __riscv_vle16_v_i16m1((const int16_t *)&b, 8), 8), 8);
    return out;
}
static inline u16x8 rvv_adds_u16x8(u16x8 a, u16x8 b)
{
    u16x8 out;
    __riscv_vse16_v_u16m1((uint16_t *)&out,
        __riscv_vsaddu_vv_u16m1(
            __riscv_vle16_v_u16m1((const uint16_t *)&a, 8),
            __riscv_vle16_v_u16m1((const uint16_t *)&b, 8), 8), 8);
    return out;
}
static inline u16x8 rvv_subs_u16x8(u16x8 a, u16x8 b)
{
    u16x8 out;
    __riscv_vse16_v_u16m1((uint16_t *)&out,
        __riscv_vssubu_vv_u16m1(
            __riscv_vle16_v_u16m1((const uint16_t *)&a, 8),
            __riscv_vle16_v_u16m1((const uint16_t *)&b, 8), 8), 8);
    return out;
}
#define rvv_adds(a,b) _Generic((a), i8x16: rvv_adds_i8x16, u8x16: rvv_adds_u8x16, i16x8: rvv_adds_i16x8, u16x8: rvv_adds_u16x8)(a,b)
#define rvv_subs(a,b) _Generic((a), i8x16: rvv_subs_i8x16, u8x16: rvv_subs_u8x16, i16x8: rvv_subs_i16x8, u16x8: rvv_subs_u16x8)(a,b)
static inline i16x8 rvv_sra_i16x8(i16x8 a, u16x8 count)
{
    i16x8 out;
    __riscv_vse16_v_i16m1((int16_t *)&out,
        __riscv_vsra_vv_i16m1(
            __riscv_vle16_v_i16m1((const int16_t *)&a, 8),
            __riscv_vle16_v_u16m1((const uint16_t *)&count, 8), 8), 8);
    return out;
}
static inline u16x8 rvv_sl_u16x8(u16x8 a, u16x8 count)
{
    u16x8 out;
    __riscv_vse16_v_u16m1((uint16_t *)&out,
        __riscv_vsll_vv_u16m1(
            __riscv_vle16_v_u16m1((const uint16_t *)&a, 8),
            __riscv_vle16_v_u16m1((const uint16_t *)&count, 8), 8), 8);
    return out;
}
static inline u16x8 rvv_sr_u16x8(u16x8 a, u16x8 count)
{
    u16x8 out;
    __riscv_vse16_v_u16m1((uint16_t *)&out,
        __riscv_vsrl_vv_u16m1(
            __riscv_vle16_v_u16m1((const uint16_t *)&a, 8),
            __riscv_vle16_v_u16m1((const uint16_t *)&count, 8), 8), 8);
    return out;
}
static inline i32x4 rvv_sra_i32x4(i32x4 a, u32x4 count)
{
    i32x4 out;
    __riscv_vse32_v_i32m1((int32_t *)&out,
        __riscv_vsra_vv_i32m1(
            __riscv_vle32_v_i32m1((const int32_t *)&a, 4),
            __riscv_vle32_v_u32m1((const uint32_t *)&count, 4), 4), 4);
    return out;
}
static inline u32x4 rvv_sl_u32x4(u32x4 a, u32x4 count)
{
    u32x4 out;
    __riscv_vse32_v_u32m1((uint32_t *)&out,
        __riscv_vsll_vv_u32m1(
            __riscv_vle32_v_u32m1((const uint32_t *)&a, 4),
            __riscv_vle32_v_u32m1((const uint32_t *)&count, 4), 4), 4);
    return out;
}
static inline u32x4 rvv_sr_u32x4(u32x4 a, u32x4 count)
{
    u32x4 out;
    __riscv_vse32_v_u32m1((uint32_t *)&out,
        __riscv_vsrl_vv_u32m1(
            __riscv_vle32_v_u32m1((const uint32_t *)&a, 4),
            __riscv_vle32_v_u32m1((const uint32_t *)&count, 4), 4), 4);
    return out;
}
static inline i64x2 rvv_sra_i64x2(i64x2 a, u64x2 count)
{
    i64x2 out;
    __riscv_vse64_v_i64m1((int64_t *)&out,
        __riscv_vsra_vv_i64m1(
            __riscv_vle64_v_i64m1((const int64_t *)&a, 2),
            __riscv_vle64_v_u64m1((const uint64_t *)&count, 2), 2), 2);
    return out;
}
static inline u64x2 rvv_sl_u64x2(u64x2 a, u64x2 count)
{
    u64x2 out;
    __riscv_vse64_v_u64m1((uint64_t *)&out,
        __riscv_vsll_vv_u64m1(
            __riscv_vle64_v_u64m1((const uint64_t *)&a, 2),
            __riscv_vle64_v_u64m1((const uint64_t *)&count, 2), 2), 2);
    return out;
}
static inline u64x2 rvv_sr_u64x2(u64x2 a, u64x2 count)
{
    u64x2 out;
    __riscv_vse64_v_u64m1((uint64_t *)&out,
        __riscv_vsrl_vv_u64m1(
            __riscv_vle64_v_u64m1((const uint64_t *)&a, 2),
            __riscv_vle64_v_u64m1((const uint64_t *)&count, 2), 2), 2);
    return out;
}
#define rvv_sl(a,b) _Generic((a), u16x8: rvv_sl_u16x8, u32x4: rvv_sl_u32x4, u64x2: rvv_sl_u64x2)(a,b)
#define rvv_sr(a,b) _Generic((a), u16x8: rvv_sr_u16x8, u32x4: rvv_sr_u32x4, u64x2: rvv_sr_u64x2)(a,b)
#define rvv_sra(a,b) _Generic((a), i16x8: rvv_sra_i16x8, i32x4: rvv_sra_i32x4, i64x2: rvv_sra_i64x2)(a,b)
/* Native fixed-vector comparisons lower to RVV mask/merge instructions. */
#define rvv_cmpeq(a,b) ((a) == (b))
#define rvv_cmpgt(a,b) ((a) > (b))
#define rvv_min(a,b) (((a) & (__typeof__(a))((a) < (b))) | ((b) & ~(__typeof__(a))((a) < (b))))
#define rvv_max(a,b) (((a) & (__typeof__(a))((a) > (b))) | ((b) & ~(__typeof__(a))((a) > (b))))
/* Exact unsigned ceil((a+b)/2), without overflowing the element width. */
#define rvv_avg(a,b) (((a) & (b)) + (((a) ^ (b)) >> 1) + (((a) ^ (b)) & 1))
static inline i32x4 rvv_msum(i16x8 a, i16x8 b, i32x4 initial)
{
    u32x4 out = (u32x4)initial;
    for (unsigned lane = 0; lane < 4; ++lane) {
        unsigned low = (unsigned)((int)a[2*lane] * (int)b[2*lane]);
        unsigned high = (unsigned)((int)a[2*lane+1] * (int)b[2*lane+1]);
        out[lane] += low + high;
    }
    return (i32x4)out;
}
static inline i8x16 rvv_packs_i16(i16x8 a, i16x8 b)
{
    i8x16 out;
    for (unsigned lane = 0; lane < 16; ++lane) {
        int x = lane < 8 ? a[lane] : b[lane-8];
        out[lane] = (signed char)(x < -128 ? -128 : x > 127 ? 127 : x);
    }
    return out;
}
static inline u8x16 rvv_packsu(i16x8 a, i16x8 b)
{
    u8x16 out;
    for (unsigned lane = 0; lane < 16; ++lane) {
        int x = lane < 8 ? a[lane] : b[lane-8];
        out[lane] = (unsigned char)(x < 0 ? 0 : x > 255 ? 255 : x);
    }
    return out;
}
static inline i16x8 rvv_packs_i32(i32x4 a, i32x4 b)
{
    i16x8 out;
    for (unsigned lane = 0; lane < 8; ++lane) {
        int x = lane < 4 ? a[lane] : b[lane-4];
        out[lane] = (short)(x < -32768 ? -32768 : x > 32767 ? 32767 : x);
    }
    return out;
}
#define rvv_packs(a,b) _Generic((a), i16x8: rvv_packs_i16, i32x4: rvv_packs_i32)(a,b)
#endif
