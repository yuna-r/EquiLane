/* Actual AArch64 wrapper smoke, not a fixture/model test.
 * Link the six ARM official C blocks with strict FP. Integer and
 * exact finite FP controls assert literal/reference results. Native floating
 * differences from captured SSE2 results are printed, never normalized away.
 */
#include "../../10_official_suite/arm/wrappers.h"
#include <inttypes.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static unsigned checks;
#define CHECK(test) do { ++checks; if (!(test)) { fprintf(stderr,"%s:%d: %s failed\n",__FILE__,__LINE__,#test); exit(1); } } while (0)
static int signed8(unsigned value) { return value & 128U ? (int)value - 256 : (int)value; }
static int signed16(unsigned value) { return value & 32768U ? (int)value - 65536 : (int)value; }
static int clamp(int value, int low, int high) { return value < low ? low : value > high ? high : value; }

static void integers(void)
{
    const unsigned bytes[] = {0, 1, 127, 128, 255};
    for (unsigned i = 0; i < 5; ++i) for (unsigned j = 0; j < 5; ++j) {
        u8x16 a = vdupq_n_u8((uint8_t)bytes[i]), b = vdupq_n_u8((uint8_t)bytes[j]);
        u8x16 add = arm_add_i8x16(a,b), sat = arm_adds_i8x16(a,b);
        u8x16 usat = arm_subs_u8x16(a,b), avg = arm_avg_u8x16(a,b);
        for (unsigned lane = 0; lane < 16; ++lane) {
            CHECK(add[lane] == (uint8_t)(bytes[i] + bytes[j]));
            CHECK(sat[lane] == (uint8_t)clamp(signed8(bytes[i]) + signed8(bytes[j]),-128,127));
            CHECK(usat[lane] == (uint8_t)(bytes[i] < bytes[j] ? 0 : bytes[i] - bytes[j]));
            CHECK(avg[lane] == (bytes[i] + bytes[j] + 1) / 2);
        }
        u64x2 sad = arm_sad_u8x16(a,b);
        unsigned difference = bytes[i] > bytes[j] ? bytes[i] - bytes[j] : bytes[j] - bytes[i];
        CHECK(sad[0] == difference * 8U && sad[1] == difference * 8U);
    }
    const unsigned halves[] = {0,1,32767,32768,65535,12345,45678};
    for (unsigned i = 0; i < 7; ++i) for (unsigned j = 0; j < 7; ++j) {
        u16x8 a = vdupq_n_u16((uint16_t)halves[i]), b = vdupq_n_u16((uint16_t)halves[j]);
        u16x8 add = arm_add_i16x8(a,b), sat = arm_adds_i16x8(a,b);
        u16x8 high = arm_mulhi_u16x8(a,b);
        for (unsigned lane = 0; lane < 8; ++lane) {
            CHECK(add[lane] == (uint16_t)(halves[i] + halves[j]));
            CHECK(sat[lane] == (uint16_t)clamp(signed16(halves[i]) + signed16(halves[j]),-32768,32767));
            CHECK(high[lane] == (uint16_t)(((uint32_t)halves[i] * halves[j]) >> 16));
        }
    }
    i16x8 minimum = vdupq_n_s16(-32768);
    i32x4 madd = arm_madd_i16x8(minimum, minimum);
    for (unsigned lane = 0; lane < 4; ++lane) CHECK((uint32_t)madd[lane] == UINT32_C(0x80000000));
    i16x8 a16 = {-32768,-129,-128,-1,0,127,128,32767};
    i16x8 b16 = {32767,128,127,0,-1,-128,-129,-32768};
    i8x16 packed = arm_packs_i16x8(a16,b16);
    u8x16 unsigned_packed = arm_packus_i16x8(a16,b16);
    for (unsigned lane = 0; lane < 16; ++lane) {
        int value = lane < 8 ? a16[lane] : b16[lane-8];
        CHECK(packed[lane] == clamp(value,-128,127));
        CHECK(unsigned_packed[lane] == clamp(value,0,255));
    }
    i32x4 a32 = {INT32_MIN,-32769,-32768,-1}, b32 = {0,32767,32768,INT32_MAX};
    i16x8 packed16 = arm_packs_i32x4(a32,b32);
    for (unsigned lane = 0; lane < 8; ++lane) {
        int value = lane < 4 ? a32[lane] : b32[lane-4];
        CHECK(packed16[lane] == clamp(value,-32768,32767));
    }
    u32x4 multiplication_a = {UINT32_MAX,0xDEADBEEF,0x80000000,0x12345678};
    u32x4 multiplication_b = {UINT32_MAX,1,UINT32_MAX,7};
    u64x2 wide = arm_mul_u32x4(multiplication_a,multiplication_b);
    CHECK(wide[0] == UINT64_C(0xfffffffe00000001));
    CHECK(wide[1] == UINT64_C(0x7fffffff80000000));
}

static void shifts_and_lanes(void)
{
    const uint64_t counts[] = {0,1,15,16,31,32,63,64,255,256,UINT64_C(0x100000001),UINT64_C(0x8000000000000000),UINT64_MAX};
    u16x8 a16 = {0x8001,0x7fff,0xffff,1,2,3,4,5};
    u32x4 a32 = {0x80000001,0x7fffffff,0xffffffff,1};
    u64x2 a64 = {UINT64_C(0x8000000000000001),UINT64_MAX};
    for (unsigned i = 0; i < sizeof(counts)/sizeof(counts[0]); ++i) {
        uint64_t n = counts[i]; u64x2 count = {n, UINT64_MAX};
        u16x8 left16 = (u16x8)arm_sll_i16x8((i16x8)a16,count);
        u16x8 right16 = (u16x8)arm_srl_i16x8((i16x8)a16,count);
        u16x8 signed16bits = (u16x8)arm_sra_i16x8((i16x8)a16,count);
        for (unsigned lane = 0; lane < 8; ++lane) {
            unsigned k = n > 15 ? 15 : (unsigned)n;
            unsigned signfill = k && (a16[lane]&0x8000U) ? 0xffffU << (16-k) : 0;
            CHECK(left16[lane] == (n > 15 ? 0 : (uint16_t)((unsigned)a16[lane] << n)));
            CHECK(right16[lane] == (n > 15 ? 0 : (uint16_t)(a16[lane] >> n)));
            CHECK(signed16bits[lane] == (uint16_t)((a16[lane] >> k) | signfill));
        }
        u32x4 left32 = arm_sll_i32x4(a32,count), right32 = arm_srl_i32x4(a32,count), signed32bits = arm_sra_i32x4(a32,count);
        for (unsigned lane = 0; lane < 4; ++lane) {
            unsigned k = n > 31 ? 31 : (unsigned)n;
            uint32_t fill = k && (a32[lane]&0x80000000U) ? UINT32_MAX << (32-k) : 0;
            CHECK(left32[lane] == (n > 31 ? 0 : a32[lane] << n));
            CHECK(right32[lane] == (n > 31 ? 0 : a32[lane] >> n));
            CHECK(signed32bits[lane] == (a32[lane] >> k | fill));
        }
        u64x2 left64 = (u64x2)arm_sll_i64x2((i64x2)a64,count), right64 = (u64x2)arm_srl_i64x2((i64x2)a64,count);
        for (unsigned lane = 0; lane < 2; ++lane) {
            CHECK(left64[lane] == (n > 63 ? 0 : a64[lane] << n));
            CHECK(right64[lane] == (n > 63 ? 0 : a64[lane] >> n));
        }
    }
    u32x4 input = {0x01234567,0x89abcdef,0x76543210,0xfedcba98};
    for (unsigned immediate = 0; immediate < 256; ++immediate) {
        u32x4 result = arm_shuffle_i32x4(input,immediate);
        for (unsigned lane = 0; lane < 4; ++lane) CHECK(result[lane] == input[(immediate>>(2*lane))&3U]);
    }
    u8x16 bytes = {0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15};
    const unsigned byte_counts[] = {0,1,7,15,16,255};
    for (unsigned i = 0; i < 6; ++i) {
        unsigned n = byte_counts[i]; u8x16 left = arm_slli_bytes_u8x16(bytes,n), right = arm_srli_bytes_u8x16(bytes,n);
        for (unsigned lane = 0; lane < 16; ++lane) {
            CHECK(left[lane] == (n >= 16 || lane < n ? 0 : bytes[lane-n]));
            CHECK(right[lane] == (n >= 16 || lane+n >= 16 ? 0 : bytes[lane+n]));
        }
    }
}

static void floating_and_memory(void)
{
    u32x4 poison32 = {0x3f800000,0x7f800035,0x80000000,0x7fc0cafe};
    f32x4 a32 = (f32x4)poison32, b32 = vdupq_n_f32(2);
    u32x4 scalar32 = (u32x4)arm_add_scalar_f32x4(a32,b32);
    CHECK(scalar32[0] == 0x40400000U);
    for (unsigned lane = 1; lane < 4; ++lane) CHECK(scalar32[lane] == poison32[lane]);
    u64x2 poison64 = {UINT64_C(0x3ff0000000000000),UINT64_C(0x7ff0000000000035)};
    f64x2 a64 = (f64x2)poison64, b64 = vdupq_n_f64(2);
    u64x2 scalar64 = (u64x2)arm_add_scalar_f64x2(a64,b64);
    CHECK(scalar64[0] == UINT64_C(0x4008000000000000)); CHECK(scalar64[1] == poison64[1]);
    u32x4 root32 = (u32x4)arm_sqrt_f32x4(vdupq_n_f32(4));
    u64x2 root64 = (u64x2)arm_sqrt_f64x2(vdupq_n_f64(4));
    CHECK(root32[0] == 0x40000000U && root64[0] == UINT64_C(0x4000000000000000));
    f32x4 subnormal = (f32x4)(u32x4){1,0x80000001,0x7f800035,0x7fc0cafe};
    u64x2 widened = (u64x2)arm_cvt_f32x4_f64x2(subnormal);
    CHECK(widened[0] == UINT64_C(0x36a0000000000000)); CHECK(widened[1] == UINT64_C(0xb6a0000000000000));
    unsigned char source[24], output[24];
    memset(source,0xA5,sizeof(source)); memset(output,0x5A,sizeof(output));
    memcpy(source+3,&poison32,16);
    arm_storeu_f32x4(output+5,arm_loadu_f32x4(source+3));
    CHECK(!memcmp(source+3,output+5,16)); CHECK(output[4] == 0x5A && output[21] == 0x5A);
    memcpy(source+3,&poison64,16);
    arm_storeu_f64x2(output+5,arm_loadu_f64x2(source+3));
    CHECK(!memcmp(source+3,output+5,16)); CHECK(output[4] == 0x5A && output[21] == 0x5A);
}

static void floating_findings(void)
{
    f64x2 inf = (f64x2)(u64x2){UINT64_C(0x7ff0000000000000),0};
    f64x2 neginf = (f64x2)(u64x2){UINT64_C(0xfff0000000000000),0};
    f64x2 quiet = (f64x2)(u64x2){UINT64_C(0x7ff8000000000041),0};
    f64x2 signaling = (f64x2)(u64x2){UINT64_C(0x7ff0000000000003),0};
    f64x2 zero = (f64x2)(u64x2){0,0}, minuszero = (f64x2)(u64x2){UINT64_C(0x8000000000000000),0};
    uint64_t invalid = ((u64x2)arm_add_f64x2(inf,neginf))[0];
    uint64_t priority = ((u64x2)arm_add_f64x2(quiet,signaling))[0];
    uint64_t minimum = ((u64x2)arm_min_f64x2(quiet,vdupq_n_f64(1)))[0];
    uint64_t maximum_zero = ((u64x2)arm_max_f64x2(zero,minuszero))[0];
    uint32_t nan_integer = (uint32_t)arm_cvt_scalar_f64x2_i32(quiet);
    uint32_t inf_integer = (uint32_t)arm_cvt_scalar_f64x2_i32(inf);
    printf("ARM native FP findings (raw bits; SSE2 reference from strict Rosetta capture):\n");
    printf("  invalid add: ARM=0x%016" PRIx64 " SSE2=0xfff8000000000000\n",invalid);
    printf("  qNaN+sNaN priority: ARM=0x%016" PRIx64 " SSE2=0x7ff8000000000041\n",priority);
    printf("  min(qNaN,1): ARM=0x%016" PRIx64 " SSE2=0x3ff0000000000000\n",minimum);
    printf("  max(+0,-0): ARM=0x%016" PRIx64 " SSE2=0x8000000000000000\n",maximum_zero);
    printf("  NaN->i32: ARM=0x%08" PRIx32 " SSE2=0x80000000\n",nan_integer);
    printf("  +inf->i32: ARM=0x%08" PRIx32 " SSE2=0x80000000\n",inf_integer);
}

int main(void)
{
    uint64_t fpcr;
    __asm__ volatile("mrs %0, fpcr" : "=r"(fpcr));
    printf("AArch64 NEON wrapper smoke: FPCR=0x%016" PRIx64 "\n",fpcr);
    integers(); shifts_and_lanes(); floating_and_memory(); floating_findings();
    printf("ARM native controls: %u assertions passed (integer boundaries, scalar poison, FP and unaligned guards).\n",checks);
    return 0;
}
