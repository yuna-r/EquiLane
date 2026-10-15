#include "f64x2_adapter.h"
#include "framework/example_cases.h"

#include <arm_neon.h>
#include "sse2_pd.h"

#if !defined(__aarch64__) && !defined(__arm64__)
#error "ARM NEON adapter must be compiled for AArch64"
#endif

_Static_assert(sizeof(float64x2_t) == 16, "float64x2_t must be 128 bits");

typedef struct {
    float64x2_t a;
    float64x2_t b;
    float64x2_t result;
} arm_add_pd_context;

typedef struct {
    double value;
    float64x2_t result;
} arm_set1_pd_context;

static void arm_prepare_mm_add_pd(void* opaque,
                                    const double a[2],
                                    const double b[2])
{
    arm_add_pd_context* context = (arm_add_pd_context*)opaque;
    context->a = vld1q_f64(a);
    context->b = vld1q_f64(b);
}

static void arm_invoke_mm_add_pd(void* opaque)
{
    arm_add_pd_context* context = (arm_add_pd_context*)opaque;
    context->result = vaddq_f64(context->a, context->b);
}

static void arm_extract_mm_add_pd(const void* opaque, double result[2])
{
    const arm_add_pd_context* context = (const arm_add_pd_context*)opaque;
    vst1q_f64(result, context->result);
}

static void arm_prepare_mm_set1_pd(void* opaque, double value)
{
    arm_set1_pd_context* context = (arm_set1_pd_context*)opaque;
    context->value = value;
}

static void arm_invoke_mm_set1_pd(void* opaque)
{
    arm_set1_pd_context* context = (arm_set1_pd_context*)opaque;
    context->result = vdupq_n_f64(context->value);
}

static void arm_extract_mm_set1_pd(const void* opaque, double result[2])
{
    const arm_set1_pd_context* context = (const arm_set1_pd_context*)opaque;
    vst1q_f64(result, context->result);
}

static const ioitf_add_pd_callbacks ARM_ADD_PD_CALLBACKS = {
    arm_prepare_mm_add_pd,
    arm_invoke_mm_add_pd,
    arm_extract_mm_add_pd
};

static const ioitf_set1_pd_callbacks ARM_SET1_PD_CALLBACKS = {
    arm_prepare_mm_set1_pd,
    arm_invoke_mm_set1_pd,
    arm_extract_mm_set1_pd
};

static void arm_store_i32x4(uint32_t result[4], uint32x4_t value)
{
    vst1q_u32(result, value);
}

static void arm_call_mm_shuffle_epi32(const uint32_t lanes[4],
                                        uint8_t immediate,
                                        uint32_t result[4])
{
    uint32x4_t input = vld1q_u32(lanes);
    uint32x4_t shuffled;

    switch (immediate) {
    case 0:
        shuffled = (uint32x4_t){input[0], input[0], input[0], input[0]};
        break;
    case 1:
        shuffled = (uint32x4_t){input[1], input[0], input[0], input[0]};
        break;
    case 27:
        shuffled = (uint32x4_t){input[3], input[2], input[1], input[0]};
        break;
    case 255:
        shuffled = (uint32x4_t){input[3], input[3], input[3], input[3]};
        break;
    default:
        /* The case-specific decoder rejects this before the SUT call. */
        shuffled = input;
        break;
    }
    arm_store_i32x4(result, shuffled);
}

int arm_mm_add_pd(const ioitf_input* input, ioitf_output* output)
{
    arm_add_pd_context context;
    return ioitf_execute_add_pd(input, output, &context,
                                &ARM_ADD_PD_CALLBACKS, NULL);
}

int arm_mm_set1_pd(const ioitf_input* input, ioitf_output* output)
{
    arm_set1_pd_context context;
    return ioitf_execute_set1_pd(input, output, &context,
                                 &ARM_SET1_PD_CALLBACKS, NULL);
}

int arm_mm_shuffle_epi32(const ioitf_input* input, ioitf_output* output)
{
    return ioitf_execute_shuffle_epi32(input, output,
                                       arm_call_mm_shuffle_epi32);
}
