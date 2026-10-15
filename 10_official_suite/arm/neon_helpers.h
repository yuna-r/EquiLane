#ifndef EQUILANE_ARM_NEON_HELPERS_H
#define EQUILANE_ARM_NEON_HELPERS_H

#include <arm_neon.h>
#if !defined(__aarch64__) && !defined(__arm64__)
#error "EquiLane ARM wrappers require native AArch64 Advanced SIMD"

#endif

typedef float32x4_t f32x4;
typedef float64x2_t f64x2;
typedef int8x16_t i8x16;
typedef uint8x16_t u8x16;
typedef int16x8_t i16x8;
typedef uint16x8_t u16x8;
typedef int32x4_t i32x4;
typedef uint32x4_t u32x4;
typedef int64x2_t i64x2;
typedef uint64x2_t u64x2;

#define arm_splats(value) _Generic((value), \
    signed char: vdupq_n_s8, unsigned char: vdupq_n_u8, \
    short: vdupq_n_s16, unsigned short: vdupq_n_u16, \
    int: vdupq_n_s32, unsigned: vdupq_n_u32, \
    long long: vdupq_n_s64, unsigned long long: vdupq_n_u64, \
    float: vdupq_n_f32, double: vdupq_n_f64)(value)
#define arm_adds(a,b) _Generic((a), i8x16:vqaddq_s8, u8x16:vqaddq_u8, i16x8:vqaddq_s16, u16x8:vqaddq_u16)(a,b)
#define arm_subs(a,b) _Generic((a), i8x16:vqsubq_s8, u8x16:vqsubq_u8, i16x8:vqsubq_s16, u16x8:vqsubq_u16)(a,b)
#define arm_cmpeq(a,b) _Generic((a), u8x16:vceqq_u8, u16x8:vceqq_u16, u32x4:vceqq_u32)(a,b)
#define arm_cmpgt(a,b) _Generic((a), i8x16:vcgtq_s8, i16x8:vcgtq_s16, i32x4:vcgtq_s32)(a,b)
#define arm_avg(a,b) _Generic((a), u8x16:vrhaddq_u8, u16x8:vrhaddq_u16)(a,b)
#define arm_min(a,b) _Generic((a), u8x16:vminq_u8, i16x8:vminq_s16)(a,b)
#define arm_max(a,b) _Generic((a), u8x16:vmaxq_u8, i16x8:vmaxq_s16)(a,b)

static inline u16x8 arm_sl_u16(u16x8 a, u16x8 n) { return vshlq_u16(a, vreinterpretq_s16_u16(n)); }
static inline u32x4 arm_sl_u32(u32x4 a, u32x4 n) { return vshlq_u32(a, vreinterpretq_s32_u32(n)); }
static inline u64x2 arm_sl_u64(u64x2 a, u64x2 n) { return vshlq_u64(a, vreinterpretq_s64_u64(n)); }
static inline u16x8 arm_sr_u16(u16x8 a, u16x8 n) { return vshlq_u16(a, vnegq_s16(vreinterpretq_s16_u16(n))); }
static inline u32x4 arm_sr_u32(u32x4 a, u32x4 n) { return vshlq_u32(a, vnegq_s32(vreinterpretq_s32_u32(n))); }
static inline u64x2 arm_sr_u64(u64x2 a, u64x2 n) { return vshlq_u64(a, vnegq_s64(vreinterpretq_s64_u64(n))); }
static inline i16x8 arm_sra_s16(i16x8 a, u16x8 n) { return vshlq_s16(a, vnegq_s16(vreinterpretq_s16_u16(n))); }
static inline i32x4 arm_sra_s32(i32x4 a, u32x4 n) { return vshlq_s32(a, vnegq_s32(vreinterpretq_s32_u32(n))); }
#define arm_sl(a,b) _Generic((a), u16x8:arm_sl_u16, u32x4:arm_sl_u32, u64x2:arm_sl_u64)(a,b)
#define arm_sr(a,b) _Generic((a), u16x8:arm_sr_u16, u32x4:arm_sr_u32, u64x2:arm_sr_u64)(a,b)
#define arm_sra(a,b) _Generic((a), i16x8:arm_sra_s16, i32x4:arm_sra_s32)(a,b)

static inline i8x16 arm_packs_s16(i16x8 a, i16x8 b) { return vcombine_s8(vqmovn_s16(a), vqmovn_s16(b)); }
static inline i16x8 arm_packs_s32(i32x4 a, i32x4 b) { return vcombine_s16(vqmovn_s32(a), vqmovn_s32(b)); }
static inline u8x16 arm_packsu(i16x8 a, i16x8 b) { return vcombine_u8(vqmovun_s16(a), vqmovun_s16(b)); }
#define arm_packs(a,b) _Generic((a), i16x8:arm_packs_s16, i32x4:arm_packs_s32)(a,b)
static inline i32x4 arm_msum(i16x8 a, i16x8 b, i32x4 c) {
    i32x4 low = vmull_s16(vget_low_s16(a), vget_low_s16(b));
    i32x4 high = vmull_s16(vget_high_s16(a), vget_high_s16(b));
    return vaddq_s32(vpaddq_s32(low, high), c);
}
/* Base A64 scalar conversions. These ACLE spellings are absent from Apple Clang21,
 * so execute the same native FCVT instructions directly, retaining ARM saturation. */
static inline int32_t arm_nearest_f64_i32(double value) {
    int32_t result;
    __asm__ volatile("fcvtns %w0, %d1" : "=r"(result) : "w"(value));
    return result;
}
static inline int32_t arm_truncate_f64_i32(double value) {
    int32_t result;
    __asm__ volatile("fcvtzs %w0, %d1" : "=r"(result) : "w"(value));
    return result;
}

#endif
