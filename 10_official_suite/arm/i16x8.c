#include "../shortcuts.h"
#include "neon_helpers.h"


i16x8 arm_set1_i16x8(short value)
{
    return arm_splats(value);
}

IOITF_BINARY(u16x8, u16x8, arm_add_i16x8, a + b)
IOITF_BINARY(u16x8, u16x8, arm_sub_i16x8, a - b)
IOITF_BINARY(u16x8, u16x8, arm_adds_i16x8, (u16x8)arm_adds((i16x8)a, (i16x8)b))
IOITF_BINARY(u16x8, u16x8, arm_adds_u16x8, arm_adds(a, b))
IOITF_BINARY(u16x8, u16x8, arm_subs_i16x8, (u16x8)arm_subs((i16x8)a, (i16x8)b))
IOITF_BINARY(u16x8, u16x8, arm_subs_u16x8, arm_subs(a, b))
IOITF_BINARY(u16x8, u16x8, arm_cmpeq_i16x8, (u16x8)arm_cmpeq(a, b))
IOITF_BINARY(u16x8, u16x8, arm_cmpgt_i16x8, (u16x8)arm_cmpgt((i16x8)a, (i16x8)b))
IOITF_BINARY(i16x8, i16x8, arm_mullo_i16x8, (i16x8)((u16x8)a * (u16x8)b))

static unsigned short signed_mul_high(short a, short b)
{
    unsigned product = (unsigned)((int)a * (int)b);
    return (unsigned short)(product >> 16);
}

i16x8 arm_mulhi_i16x8(i16x8 a, i16x8 b)
{
    return (i16x8)(u16x8){
        signed_mul_high(a[0], b[0]), signed_mul_high(a[1], b[1]),
        signed_mul_high(a[2], b[2]), signed_mul_high(a[3], b[3]),
        signed_mul_high(a[4], b[4]), signed_mul_high(a[5], b[5]),
        signed_mul_high(a[6], b[6]), signed_mul_high(a[7], b[7])};
}

IOITF_BINARY(static unsigned short, unsigned short, unsigned_mul_high, (unsigned short)(((unsigned)a * (unsigned)b) >> 16))

u16x8 arm_mulhi_u16x8(u16x8 a, u16x8 b)
{
    return (u16x8){
        unsigned_mul_high(a[0], b[0]), unsigned_mul_high(a[1], b[1]),
        unsigned_mul_high(a[2], b[2]), unsigned_mul_high(a[3], b[3]),
        unsigned_mul_high(a[4], b[4]), unsigned_mul_high(a[5], b[5]),
        unsigned_mul_high(a[6], b[6]), unsigned_mul_high(a[7], b[7])};
}

IOITF_BINARY(i32x4, i16x8, arm_madd_i16x8, arm_msum(a, b, arm_splats(0)))
IOITF_BINARY(u16x8, u16x8, arm_avg_u16x8, arm_avg(a, b))
IOITF_BINARY(i16x8, i16x8, arm_min_i16x8, arm_min(a, b))
IOITF_BINARY(i16x8, i16x8, arm_max_i16x8, arm_max(a, b))

i16x8 arm_slli_i16x8(i16x8 v, unsigned imm)
{
    if (imm > 15U) {
        return (i16x8)arm_splats((unsigned short)0);
    }
    return (i16x8)arm_sl((u16x8)v, arm_splats((unsigned short)imm));
}

i16x8 arm_srli_i16x8(i16x8 v, unsigned imm)
{
    if (imm > 15U) {
        return (i16x8)arm_splats((unsigned short)0);
    }
    return (i16x8)arm_sr((u16x8)v, arm_splats((unsigned short)imm));
}

i16x8 arm_srai_i16x8(i16x8 v, unsigned imm)
{
    unsigned count = imm > 15U ? 15U : imm;
    return arm_sra(v, arm_splats((unsigned short)count));
}

IOITF_BINARY(i16x8, i16x8, arm_unpacklo_i16x8, (i16x8){a[0], b[0], a[1], b[1], a[2], b[2], a[3], b[3]})
IOITF_BINARY(i16x8, i16x8, arm_unpackhi_i16x8, (i16x8){a[4], b[4], a[5], b[5], a[6], b[6], a[7], b[7]})
IOITF_BINARY(i8x16, i16x8, arm_packs_i16x8, arm_packs(a, b))
IOITF_BINARY(u8x16, i16x8, arm_packus_i16x8, arm_packsu(a, b))

i16x8 arm_shufflelo_i16x8(i16x8 v, unsigned imm)
{
    return (i16x8){v[(imm >> 0) & 3U], v[(imm >> 2) & 3U],
                    v[(imm >> 4) & 3U], v[(imm >> 6) & 3U],
                    v[4], v[5], v[6], v[7]};
}

i16x8 arm_shufflehi_i16x8(i16x8 v, unsigned imm)
{
    return (i16x8){v[0], v[1], v[2], v[3],
                    v[4 + ((imm >> 0) & 3U)], v[4 + ((imm >> 2) & 3U)],
                    v[4 + ((imm >> 4) & 3U)], v[4 + ((imm >> 6) & 3U)]};
}

i16x8 arm_sll_i16x8(i16x8 a, u64x2 count)
{
    unsigned long long n = count[0];
    if (n > 15U) {
        return (i16x8)arm_splats((unsigned short)0);
    }
    return (i16x8)arm_sl((u16x8)a, arm_splats((unsigned short)n));
}

i16x8 arm_srl_i16x8(i16x8 a, u64x2 count)
{
    unsigned long long n = count[0];
    if (n > 15U) {
        return (i16x8)arm_splats((unsigned short)0);
    }
    return (i16x8)arm_sr((u16x8)a, arm_splats((unsigned short)n));
}

i16x8 arm_sra_i16x8(i16x8 a, u64x2 count)
{
    unsigned long long raw = count[0];
    unsigned short n = (unsigned short)(raw > 15U ? 15U : raw);
    return arm_sra(a, arm_splats(n));
}

IOITF_BINARY(u16x8, u16x8, arm_cmplt_i16x8, (u16x8)arm_cmpgt((i16x8)b, (i16x8)a))

i16x8 arm_set_i16x8(short lane7, short lane6, short lane5, short lane4,
                       short lane3, short lane2, short lane1, short lane0)
{
    return (i16x8){lane0, lane1, lane2, lane3,
                    lane4, lane5, lane6, lane7};
}

i16x8 arm_setr_i16x8(short lane0, short lane1, short lane2, short lane3,
                        short lane4, short lane5, short lane6, short lane7)
{
    return (i16x8){lane0, lane1, lane2, lane3,
                    lane4, lane5, lane6, lane7};
}

unsigned arm_extract_i16x8(i16x8 a, unsigned imm)
{
    return (unsigned)(unsigned short)a[imm & 7U];
}

i16x8 arm_insert_i16x8(i16x8 a, int value, unsigned imm)
{
    a[imm & 7U] = (short)value;
    return a;
}
