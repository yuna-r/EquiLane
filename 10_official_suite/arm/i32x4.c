#include "../shortcuts.h"
#include "neon_helpers.h"


IOITF_BINARY(u32x4, u32x4, arm_add_i32x4, a + b)
IOITF_BINARY(u32x4, u32x4, arm_sub_i32x4, a - b)
IOITF_BINARY(u32x4, u32x4, arm_and_i32x4, a & b)
IOITF_BINARY(u32x4, u32x4, arm_or_i32x4, a | b)
IOITF_BINARY(u32x4, u32x4, arm_xor_i32x4, a ^ b)
IOITF_BINARY(u32x4, u32x4, arm_andnot_i32x4, (~a) & b)
IOITF_BINARY(u32x4, u32x4, arm_cmpeq_i32x4, (u32x4)arm_cmpeq(a, b))
IOITF_BINARY(u32x4, u32x4, arm_cmpgt_i32x4, (u32x4)arm_cmpgt((i32x4)a, (i32x4)b))

u32x4 arm_slli_i32x4(u32x4 v, unsigned imm)
{
    if (imm > 31U) {
        return arm_splats(0U);
    }
    return arm_sl(v, arm_splats(imm));
}

u32x4 arm_srli_i32x4(u32x4 v, unsigned imm)
{
    if (imm > 31U) {
        return arm_splats(0U);
    }
    return arm_sr(v, arm_splats(imm));
}

u32x4 arm_srai_i32x4(u32x4 v, unsigned imm)
{
    unsigned n = imm > 31U ? 31U : imm;
    return (u32x4)arm_sra((i32x4)v, arm_splats(n));
}

u32x4 arm_shuffle_i32x4(u32x4 v, unsigned imm)
{
    return (u32x4){v[(imm >> 0) & 3U], v[(imm >> 2) & 3U],
                   v[(imm >> 4) & 3U], v[(imm >> 6) & 3U]};
}

IOITF_BINARY(u32x4, u32x4, arm_unpacklo_i32x4, (u32x4){a[0], b[0], a[1], b[1]})
IOITF_BINARY(u32x4, u32x4, arm_unpackhi_i32x4, (u32x4){a[2], b[2], a[3], b[3]})

u64x2 arm_mul_u32x4(u32x4 a, u32x4 b)
{
    return (u64x2){(unsigned long long)a[0] * b[0],
                    (unsigned long long)a[2] * b[2]};
}

IOITF_BINARY(i16x8, i32x4, arm_packs_i32x4, arm_packs(a, b))

i32x4 arm_cvtsi32_i32x4(int value)
{
    return (i32x4){value, 0, 0, 0};
}

i32x4 arm_set1_i32x4(int value)
{
    return arm_splats(value);
}

u32x4 arm_sll_i32x4(u32x4 a, u64x2 count)
{
    unsigned long long n = count[0];
    if (n > 31U) {
        return arm_splats(0U);
    }
    return arm_sl(a, arm_splats((unsigned)n));
}

u32x4 arm_srl_i32x4(u32x4 a, u64x2 count)
{
    unsigned long long n = count[0];
    if (n > 31U) {
        return arm_splats(0U);
    }
    return arm_sr(a, arm_splats((unsigned)n));
}

u32x4 arm_sra_i32x4(u32x4 a, u64x2 count)
{
    unsigned long long raw = count[0];
    unsigned n = (unsigned)(raw > 31U ? 31U : raw);
    return (u32x4)arm_sra((i32x4)a, arm_splats(n));
}

IOITF_BINARY(u32x4, u32x4, arm_cmplt_i32x4, (u32x4)arm_cmpgt((i32x4)b, (i32x4)a))
IOITF_UNARY(int, i32x4, arm_cvtsi128_i32x4, a[0])

i32x4 arm_set_i32x4(int lane3, int lane2, int lane1, int lane0)
{
    return (i32x4){lane0, lane1, lane2, lane3};
}

i32x4 arm_setr_i32x4(int lane0, int lane1, int lane2, int lane3)
{
    return (i32x4){lane0, lane1, lane2, lane3};
}

i32x4 arm_loadu_i32x4(const void *source)
{
    unsigned values[4];
    __builtin_memcpy(values, source, sizeof(values));
    return (i32x4)(u32x4){values[0], values[1], values[2], values[3]};
}

void arm_storeu_i32x4(void *destination, i32x4 a)
{
    unsigned values[4] = {
        ((u32x4)a)[0], ((u32x4)a)[1], ((u32x4)a)[2], ((u32x4)a)[3]};
    __builtin_memcpy(destination, values, sizeof(values));
}
