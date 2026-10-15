/* Native RV64/RVV adaptation of the project SIMD wrappers. */
#include "../shortcuts.h"
#include "rvv128.h"


IOITF_BINARY(i64x2, i64x2, riscv_add_i64x2, (i64x2)((u64x2)a + (u64x2)b))
IOITF_BINARY(i64x2, i64x2, riscv_sub_i64x2, (i64x2)((u64x2)a - (u64x2)b))

i64x2 riscv_slli_i64x2(i64x2 v, unsigned imm)
{
    if (imm > 63U) {
        return (i64x2)rvv_splats(0ULL);
    }
    return (i64x2)rvv_sl((u64x2)v, rvv_splats((unsigned long long)imm));
}

i64x2 riscv_srli_i64x2(i64x2 v, unsigned imm)
{
    if (imm > 63U) {
        return (i64x2)rvv_splats(0ULL);
    }
    return (i64x2)rvv_sr((u64x2)v, rvv_splats((unsigned long long)imm));
}

IOITF_BINARY(i64x2, i64x2, riscv_unpacklo_i64x2, (i64x2){a[0], b[0]})
IOITF_BINARY(i64x2, i64x2, riscv_unpackhi_i64x2, (i64x2){a[1], b[1]})

i64x2 riscv_move_i64x2(i64x2 value)
{
    return (i64x2){value[0], 0};
}

i64x2 riscv_cvtsi64_i64x2(long long value)
{
    return (i64x2){value, 0};
}

i64x2 riscv_set1_i64x2(long long value)
{
    return rvv_splats(value);
}

i64x2 riscv_sll_i64x2(i64x2 a, u64x2 count)
{
    unsigned long long n = count[0];
    if (n > 63U) {
        return (i64x2)rvv_splats(0ULL);
    }
    return (i64x2)rvv_sl((u64x2)a, rvv_splats(n));
}

i64x2 riscv_srl_i64x2(i64x2 a, u64x2 count)
{
    unsigned long long n = count[0];
    if (n > 63U) {
        return (i64x2)rvv_splats(0ULL);
    }
    return (i64x2)rvv_sr((u64x2)a, rvv_splats(n));
}

IOITF_UNARY(long long, i64x2, riscv_cvtsi128_i64x2, a[0])

i64x2 riscv_set_i64x2(long long high, long long low)
{
    return (i64x2){low, high};
}

i64x2 riscv_loadl_i64x2(const void *source)
{
    unsigned long long value;
    __builtin_memcpy(&value, source, sizeof(value));
    return (i64x2)(u64x2){value, 0};
}

void riscv_storel_i64x2(void *destination, i64x2 a)
{
    unsigned long long value = ((u64x2)a)[0];
    __builtin_memcpy(destination, &value, sizeof(value));
}
