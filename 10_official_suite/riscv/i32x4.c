/* Native RV64/RVV adaptation of the project SIMD wrappers. */
#include "../shortcuts.h"
#include "rvv128.h"


IOITF_BINARY(u32x4, u32x4, riscv_add_i32x4, a + b)
IOITF_BINARY(u32x4, u32x4, riscv_sub_i32x4, a - b)
IOITF_BINARY(u32x4, u32x4, riscv_and_i32x4, a & b)
IOITF_BINARY(u32x4, u32x4, riscv_or_i32x4, a | b)
IOITF_BINARY(u32x4, u32x4, riscv_xor_i32x4, a ^ b)
IOITF_BINARY(u32x4, u32x4, riscv_andnot_i32x4, (~a) & b)
IOITF_BINARY(u32x4, u32x4, riscv_cmpeq_i32x4, (u32x4)rvv_cmpeq(a, b))
IOITF_BINARY(u32x4, u32x4, riscv_cmpgt_i32x4, (u32x4)rvv_cmpgt((i32x4)a, (i32x4)b))

u32x4 riscv_slli_i32x4(u32x4 v, unsigned imm)
{
    if (imm > 31U) {
        return rvv_splats(0U);
    }
    return rvv_sl(v, rvv_splats(imm));
}

u32x4 riscv_srli_i32x4(u32x4 v, unsigned imm)
{
    if (imm > 31U) {
        return rvv_splats(0U);
    }
    return rvv_sr(v, rvv_splats(imm));
}

u32x4 riscv_srai_i32x4(u32x4 v, unsigned imm)
{
    unsigned n = imm > 31U ? 31U : imm;
    return (u32x4)rvv_sra((i32x4)v, rvv_splats(n));
}

u32x4 riscv_shuffle_i32x4(u32x4 v, unsigned imm)
{
    return (u32x4){v[(imm >> 0) & 3U], v[(imm >> 2) & 3U],
                   v[(imm >> 4) & 3U], v[(imm >> 6) & 3U]};
}

IOITF_BINARY(u32x4, u32x4, riscv_unpacklo_i32x4, (u32x4){a[0], b[0], a[1], b[1]})
IOITF_BINARY(u32x4, u32x4, riscv_unpackhi_i32x4, (u32x4){a[2], b[2], a[3], b[3]})

u64x2 riscv_mul_u32x4(u32x4 a, u32x4 b)
{
    return (u64x2){(unsigned long long)a[0] * b[0],
                    (unsigned long long)a[2] * b[2]};
}

IOITF_BINARY(i16x8, i32x4, riscv_packs_i32x4, rvv_packs(a, b))

i32x4 riscv_cvtsi32_i32x4(int value)
{
    return (i32x4){value, 0, 0, 0};
}

i32x4 riscv_set1_i32x4(int value)
{
    return rvv_splats(value);
}

u32x4 riscv_sll_i32x4(u32x4 a, u64x2 count)
{
    unsigned long long n = count[0];
    if (n > 31U) {
        return rvv_splats(0U);
    }
    return rvv_sl(a, rvv_splats((unsigned)n));
}

u32x4 riscv_srl_i32x4(u32x4 a, u64x2 count)
{
    unsigned long long n = count[0];
    if (n > 31U) {
        return rvv_splats(0U);
    }
    return rvv_sr(a, rvv_splats((unsigned)n));
}

u32x4 riscv_sra_i32x4(u32x4 a, u64x2 count)
{
    unsigned long long raw = count[0];
    unsigned n = (unsigned)(raw > 31U ? 31U : raw);
    return (u32x4)rvv_sra((i32x4)a, rvv_splats(n));
}

IOITF_BINARY(u32x4, u32x4, riscv_cmplt_i32x4, (u32x4)rvv_cmpgt((i32x4)b, (i32x4)a))
IOITF_UNARY(int, i32x4, riscv_cvtsi128_i32x4, a[0])

i32x4 riscv_set_i32x4(int lane3, int lane2, int lane1, int lane0)
{
    return (i32x4){lane0, lane1, lane2, lane3};
}

i32x4 riscv_setr_i32x4(int lane0, int lane1, int lane2, int lane3)
{
    return (i32x4){lane0, lane1, lane2, lane3};
}

i32x4 riscv_loadu_i32x4(const void *source)
{
    unsigned values[4];
    __builtin_memcpy(values, source, sizeof(values));
    return (i32x4)(u32x4){values[0], values[1], values[2], values[3]};
}

void riscv_storeu_i32x4(void *destination, i32x4 a)
{
    unsigned values[4] = {
        ((u32x4)a)[0], ((u32x4)a)[1], ((u32x4)a)[2], ((u32x4)a)[3]};
    __builtin_memcpy(destination, values, sizeof(values));
}
