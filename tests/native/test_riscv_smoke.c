/* Independent fixed-result checks executed on RV64/RVV instructions in QEMU. */
#include "../../10_official_suite/riscv/rvv128.h"

extern f64x2 riscv_add_f64x2(f64x2, f64x2);
extern f64x2 riscv_add_scalar_f64x2(f64x2, f64x2);
extern f32x4 riscv_add_f32x4(f32x4, f32x4);
extern u8x16 riscv_adds_i8x16(u8x16, u8x16);
extern u8x16 riscv_adds_u8x16(u8x16, u8x16);
extern u8x16 riscv_subs_u8x16(u8x16, u8x16);
extern u16x8 riscv_adds_i16x8(u16x8, u16x8);
extern u16x8 riscv_avg_u16x8(u16x8, u16x8);
extern u32x4 riscv_slli_i32x4(u32x4, unsigned);
extern u32x4 riscv_sll_i32x4(u32x4, u64x2);
extern i64x2 riscv_add_i64x2(i64x2, i64x2);
extern i16x8 riscv_packs_i32x4(i32x4, i32x4);
extern u32x4 riscv_shuffle_i32x4(u32x4, unsigned);
extern i32x4 riscv_loadu_i32x4(const void *);
extern void riscv_storeu_i32x4(void *, i32x4);

static unsigned checks, failures;
static void text(const char *message)
{
    volatile unsigned char *uart = (volatile unsigned char *)0x10000000UL;
    while (*message) {
        while (!(uart[5] & 0x20)) {}
        uart[0] = (unsigned char)*message++;
    }
}
static void number(unsigned value)
{
    char digits[12]; unsigned length = 0;
    do { digits[length++] = (char)('0' + value % 10); value /= 10; } while (value);
    while (length) { char one[2] = {digits[--length], 0}; text(one); }
}
static void check(int condition, const char *label)
{
    ++checks;
    if (!condition) { ++failures; text("FAIL "); text(label); text("\n"); }
}

int riscv_smoke_main(void)
{
    unsigned long vlenb;
    __asm__ volatile("csrr %0, vlenb" : "=r"(vlenb));
    check(vlenb >= 16, "VLEN >= 128");
    text("RVV emulated smoke VLEN="); number((unsigned)vlenb * 8); text("\n");
    f64x2 d = riscv_add_f64x2((f64x2){1.0, 10.0}, (f64x2){2.0, 20.0});
    check(d[0] == 3.0 && d[1] == 30.0, "RVV f64 add two distinct lanes");
    f32x4 f = riscv_add_f32x4((f32x4){1, 10, -2, 0}, (f32x4){2, 20, 3, -1});
    check(f[0] == 3 && f[1] == 30 && f[2] == 1 && f[3] == -1, "RVV f32 add four lanes");
    u8x16 bytes = riscv_adds_i8x16((u8x16){127,128,1,255}, (u8x16){1,255,1,1});
    check(bytes[0] == 127 && bytes[1] == 128 && bytes[2] == 2 && bytes[3] == 0, "signed i8 saturation");
    bytes = riscv_adds_u8x16((u8x16){255,254,0,1}, (u8x16){1,3,255,1});
    check(bytes[0] == 255 && bytes[1] == 255 && bytes[2] == 255 && bytes[3] == 2, "unsigned u8 saturation");
    bytes = riscv_subs_u8x16((u8x16){0,1,255,10}, (u8x16){1,2,1,3});
    check(bytes[0] == 0 && bytes[1] == 0 && bytes[2] == 254 && bytes[3] == 7, "unsigned u8 subtract floor");
    u16x8 words = riscv_adds_i16x8((u16x8){32767,32768}, (u16x8){1,65535});
    check(words[0] == 32767 && words[1] == 32768, "signed i16 saturation");
    words = riscv_avg_u16x8((u16x8){65535,1}, (u16x8){65534,0});
    check(words[0] == 65535 && words[1] == 1, "unsigned average ceil without overflow");
    u32x4 ints = riscv_slli_i32x4((u32x4){1,2,3,4}, 32);
    check(ints[0] == 0 && ints[3] == 0, "immediate shift not modulo-masked");
    ints = riscv_sll_i32x4((u32x4){1,2,3,4}, (u64x2){0x100000000ULL,~0ULL});
    check(ints[0] == 0 && ints[3] == 0, "all low64 shift count bits used");
    ints = riscv_sll_i32x4((u32x4){1,2,3,4}, (u64x2){1,~0ULL});
    check(ints[0] == 2 && ints[3] == 8, "upper shift count lane ignored");
    i64x2 longs = riscv_add_i64x2((i64x2)(u64x2){~0ULL,0x7fffffffffffffffULL}, (i64x2){1,1});
    check(((u64x2)longs)[0] == 0 && ((u64x2)longs)[1] == 0x8000000000000000ULL, "i64 modular add");
    i16x8 packed = riscv_packs_i32x4((i32x4){32768,-32769,3,-4}, (i32x4){0,1,2147483647,(-2147483647-1)});
    check(packed[0] == 32767 && packed[1] == -32768 && packed[6] == 32767 && packed[7] == -32768, "pack signed saturation and order");
    ints = riscv_shuffle_i32x4((u32x4){1,2,4,8}, 27);
    check(ints[0] == 8 && ints[1] == 4 && ints[2] == 2 && ints[3] == 1, "four-lane shuffle");
    unsigned char memory[32];
    for (unsigned i = 0; i < sizeof(memory); ++i) memory[i] = (unsigned char)i;
    i32x4 loaded = riscv_loadu_i32x4(memory+1);
    riscv_storeu_i32x4(memory+3, loaded);
    check(memory[2] == 2 && memory[3] == 1 && memory[18] == 16 && memory[19] == 19, "unaligned memory and guards");
    __asm__ volatile("csrw fflags, zero");
    u64x2 scalar = (u64x2)riscv_add_scalar_f64x2((f64x2)(u64x2){0x3ff0000000000000ULL,0x7ff0000000000035ULL}, (f64x2){2,4});
    unsigned long flags;
    __asm__ volatile("csrr %0, fflags" : "=r"(flags));
    check(scalar[0] == 0x4008000000000000ULL && scalar[1] == 0x7ff0000000000035ULL && flags == 0, "scalar inactive signaling NaN preserved");
    __asm__ volatile("csrwi frm, 3\ncsrw fflags, zero");
    d = riscv_add_f64x2((f64x2){1,1}, (f64x2)(u64x2){0x3c90000000000000ULL,0x3c90000000000000ULL});
    __asm__ volatile("csrr %0, fflags" : "=r"(flags));
    check(((u64x2)d)[0] == 0x3ff0000000000001ULL && (flags & 1), "dynamic upward rounding and inexact flag");
    __asm__ volatile("csrwi frm, 0\ncsrw fflags, zero");
    d = riscv_add_f64x2((f64x2)(u64x2){0x7ff0000000000000ULL,0}, (f64x2)(u64x2){0xfff0000000000000ULL,0});
    __asm__ volatile("csrr %0, fflags" : "=r"(flags));
    check(((u64x2)d)[0] == 0x7ff8000000000000ULL && (flags & 16), "RISC-V canonical NaN and invalid flag");
    text("FINDING raw RVV invalid-add NaN is +canonical; SSE2 indefinite NaN is negative\n");
    text("RESULT checks="); number(checks); text(" failures="); number(failures); text("\n");
    return failures ? 1 : 0;
}
