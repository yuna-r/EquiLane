/* Independent SSE2 floating-point boundary oracle.
 *
 * Build on macOS: clang -arch x86_64 -O0 -msse2 -ffp-model=strict \
 *   tests/native/probe_fp_boundary.c -o /tmp/equilane-fp-boundary-probe
 * Apple Silicon execution is explicitly Rosetta evidence, not POWER or
 * cross-host native conformance. Inputs are runtime hexadecimal bit patterns;
 * no host-language float oracle or portable case model is involved.
 *
 * Each stdin row has thirteen whitespace-separated fields:
 * ELEMENT OP VARIANT ROUNDING A0 A1 A2 A3 B0 B1 B2 B3 VALUE
 * ELEMENT=f32|f64|i32|i64; VARIANT=packed|scalar. Output is one line of raw
 * hexadecimal return lanes (or one scalar). Unused request lanes are zero.
 */
#include <emmintrin.h>
#include <errno.h>
#include <inttypes.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#if !defined(__x86_64__)
#error "This independent oracle must be compiled for x86_64 SSE2"
#endif
#pragma STDC FENV_ACCESS ON

static int bits(const char *text, uint64_t *value)
{
    char *end;
    errno = 0;
    if (!text[0] || text[0] == '-' || text[0] == '+') return 0;
    *value = strtoull(text, &end, 16);
    return !errno && end != text && !*end;
}

static void out32(const void *result, int lanes)
{
    uint32_t values[4] = {0};
    memcpy(values, result, (size_t)lanes * sizeof(values[0]));
    for (int i = 0; i < lanes; ++i)
        printf("%s0x%08" PRIx32, i ? " " : "", values[i]);
    putchar('\n');
}

static void out64(const void *result, int lanes)
{
    uint64_t values[2] = {0};
    memcpy(values, result, (size_t)lanes * sizeof(values[0]));
    for (int i = 0; i < lanes; ++i)
        printf("%s0x%016" PRIx64, i ? " " : "", values[i]);
    putchar('\n');
}

/* Both variants execute the actual intrinsic, including inactive scalar lanes. */
#define FLOAT_OPERATION(NAME) \
    if (!strcmp(op, #NAME)) { \
        result = scalar ? _mm_##NAME##_ss(a, b) : _mm_##NAME##_ps(a, b); \
    }
#define DOUBLE_OPERATION(NAME) \
    if (!strcmp(op, #NAME)) { \
        result = scalar ? _mm_##NAME##_sd(a, b) : _mm_##NAME##_pd(a, b); \
    }
#define COMI_FLOAT(NAME) \
    if (!strcmp(op, #NAME)) { \
        int value = _mm_##NAME##_ss(a, b); out32(&value, 1); return 1; \
    }
#define COMI_DOUBLE(NAME) \
    if (!strcmp(op, #NAME)) { \
        int value = _mm_##NAME##_sd(a, b); out32(&value, 1); return 1; \
    }

static int evaluate(char **fields)
{
    const char *element = fields[0], *op = fields[1];
    int scalar;
    unsigned rounding;
    uint64_t av[4], bv[4], scalar_bits;
    uint32_t a32[4], b32[4];
    __m128 af, bf;
    __m128d ad, bd;
    __m128i ai;
    if (!strcmp(fields[2], "scalar")) scalar = 1;
    else if (!strcmp(fields[2], "packed")) scalar = 0;
    else return 0;
    if (!strcmp(fields[3], "nearest_even")) rounding = 0;
    else if (!strcmp(fields[3], "toward_negative")) rounding = 0x2000;
    else if (!strcmp(fields[3], "toward_positive")) rounding = 0x4000;
    else if (!strcmp(fields[3], "toward_zero")) rounding = 0x6000;
    else return 0;
    for (int i = 0; i < 4; ++i) {
        if (!bits(fields[4 + i], &av[i]) || !bits(fields[8 + i], &bv[i])) return 0;
        a32[i] = (uint32_t)av[i];
        b32[i] = (uint32_t)bv[i];
    }
    if (!bits(fields[12], &scalar_bits)) return 0;
    memcpy(&af, a32, sizeof(af)); memcpy(&bf, b32, sizeof(bf));
    memcpy(&ad, av, sizeof(ad)); memcpy(&bd, bv, sizeof(bd));
    memcpy(&ai, a32, sizeof(ai));
    /* All exceptions masked; FTZ and DAZ off; reset before every request. */
    _mm_setcsr(0x1F80 | rounding);

    if (!strcmp(op, "narrow")) {
        __m128 result = scalar ? _mm_cvtsd_ss(af, bd) : _mm_cvtpd_ps(ad);
        out32(&result, 4); return 1;
    }
    if (!strcmp(op, "widen")) {
        __m128d result = scalar ? _mm_cvtss_sd(ad, bf) : _mm_cvtps_pd(af);
        out64(&result, 2); return 1;
    }
    if (!strcmp(op, "from_i32_f32")) {
        int value;
        uint32_t low = (uint32_t)scalar_bits;
        memcpy(&value, &low, sizeof(value));
        __m128 result = scalar ? _mm_cvtsi32_ss(af, value) : _mm_cvtepi32_ps(ai);
        out32(&result, 4); return 1;
    }
    if (!strcmp(op, "from_i32_f64")) {
        int value;
        uint32_t low = (uint32_t)scalar_bits;
        memcpy(&value, &low, sizeof(value));
        __m128d result = scalar ? _mm_cvtsi32_sd(ad, value) : _mm_cvtepi32_pd(ai);
        out64(&result, 2); return 1;
    }
    if (!strcmp(op, "from_i64_f64") && scalar) {
        long long value;
        memcpy(&value, &scalar_bits, sizeof(value));
        __m128d result = _mm_cvtsi64_sd(ad, value);
        out64(&result, 2); return 1;
    }

    if (!strcmp(element, "f32")) {
        __m128 a = af, b = bf, result;
        if (!strcmp(op, "cvt_i32") || !strcmp(op, "cvtt_i32")) {
            int truncate = !strcmp(op, "cvtt_i32");
            if (scalar) {
                int value = truncate ? _mm_cvttss_si32(a) : _mm_cvtss_si32(a);
                out32(&value, 1);
            } else {
                __m128i value = truncate ? _mm_cvttps_epi32(a) : _mm_cvtps_epi32(a);
                out32(&value, 4);
            }
            return 1;
        }
        COMI_FLOAT(comieq) COMI_FLOAT(comineq) COMI_FLOAT(comilt)
        COMI_FLOAT(comile) COMI_FLOAT(comigt) COMI_FLOAT(comige)
        if (!strcmp(op, "sqrt")) result = scalar ? _mm_sqrt_ss(a) : _mm_sqrt_ps(a);
        else FLOAT_OPERATION(add) else FLOAT_OPERATION(sub)
        else FLOAT_OPERATION(mul) else FLOAT_OPERATION(div)
        else FLOAT_OPERATION(min) else FLOAT_OPERATION(max)
        else FLOAT_OPERATION(cmpeq) else FLOAT_OPERATION(cmpneq)
        else FLOAT_OPERATION(cmplt) else FLOAT_OPERATION(cmple)
        else FLOAT_OPERATION(cmpgt) else FLOAT_OPERATION(cmpge)
        else FLOAT_OPERATION(cmpnlt) else FLOAT_OPERATION(cmpnle)
        else FLOAT_OPERATION(cmpngt) else FLOAT_OPERATION(cmpnge)
        else FLOAT_OPERATION(cmpord) else FLOAT_OPERATION(cmpunord)
        else return 0;
        out32(&result, 4); return 1;
    }
    if (!strcmp(element, "f64")) {
        __m128d a = ad, b = bd, result;
        if (!strcmp(op, "cvt_i32") || !strcmp(op, "cvtt_i32")) {
            int truncate = !strcmp(op, "cvtt_i32");
            if (scalar) {
                int value = truncate ? _mm_cvttsd_si32(a) : _mm_cvtsd_si32(a);
                out32(&value, 1);
            } else {
                __m128i value = truncate ? _mm_cvttpd_epi32(a) : _mm_cvtpd_epi32(a);
                out32(&value, 4);
            }
            return 1;
        }
        if ((!strcmp(op, "cvt_i64") || !strcmp(op, "cvtt_i64")) && scalar) {
            long long value = !strcmp(op, "cvtt_i64") ? _mm_cvttsd_si64(a) : _mm_cvtsd_si64(a);
            out64(&value, 1); return 1;
        }
        COMI_DOUBLE(comieq) COMI_DOUBLE(comineq) COMI_DOUBLE(comilt)
        COMI_DOUBLE(comile) COMI_DOUBLE(comigt) COMI_DOUBLE(comige)
        if (!strcmp(op, "sqrt")) result = scalar ? _mm_sqrt_sd(a, a) : _mm_sqrt_pd(a);
        else DOUBLE_OPERATION(add) else DOUBLE_OPERATION(sub)
        else DOUBLE_OPERATION(mul) else DOUBLE_OPERATION(div)
        else DOUBLE_OPERATION(min) else DOUBLE_OPERATION(max)
        else DOUBLE_OPERATION(cmpeq) else DOUBLE_OPERATION(cmpneq)
        else DOUBLE_OPERATION(cmplt) else DOUBLE_OPERATION(cmple)
        else DOUBLE_OPERATION(cmpgt) else DOUBLE_OPERATION(cmpge)
        else DOUBLE_OPERATION(cmpnlt) else DOUBLE_OPERATION(cmpnle)
        else DOUBLE_OPERATION(cmpngt) else DOUBLE_OPERATION(cmpnge)
        else DOUBLE_OPERATION(cmpord) else DOUBLE_OPERATION(cmpunord)
        else return 0;
        out64(&result, 2); return 1;
    }
    return 0;
}

int main(void)
{
    char line[4096];
    unsigned row = 0;
    while (fgets(line, sizeof(line), stdin)) {
        char *fields[13], *next = NULL;
        int count = 0;
        ++row;
        char *word = strtok_r(line, " \t\r\n", &next);
        while (word && count < 13) {
            fields[count++] = word;
            word = strtok_r(NULL, " \t\r\n", &next);
        }
        if (count != 13 || word || !evaluate(fields)) {
            fprintf(stderr, "invalid oracle request at row %u\n", row);
            return 2;
        }
    }
    if (ferror(stdin) || ferror(stdout)) return 3;
    return 0;
}
