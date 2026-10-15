/* RV64/RVV implementation behind the same three encoded ABI v1 examples. */
#include "f64x2_adapter.h"
#include "riscv_adapter.h"
#include "../../10_official_suite/riscv/rvv128.h"

typedef struct { f64x2 a, b, result; } riscv_add_context;
typedef struct { double value; f64x2 result; } riscv_set1_context;

static void prepare_add(void *opaque, const double a[2], const double b[2])
{
    riscv_add_context *context = opaque;
    __builtin_memcpy(&context->a, a, 16);
    __builtin_memcpy(&context->b, b, 16);
}
static void invoke_add(void *opaque)
{
    riscv_add_context *context = opaque;
    context->result = rvv_add_f64x2(context->a, context->b);
}
static void extract_add(const void *opaque, double result[2])
{
    const riscv_add_context *context = opaque;
    __builtin_memcpy(result, &context->result, 16);
}
static void prepare_set1(void *opaque, double value)
{
    riscv_set1_context *context = opaque;
    __builtin_memcpy(&context->value, &value, sizeof(value));
}
static void invoke_set1(void *opaque)
{
    riscv_set1_context *context = opaque;
    context->result = rvv_splat_f64x2(context->value);
}
static void extract_set1(const void *opaque, double result[2])
{
    const riscv_set1_context *context = opaque;
    __builtin_memcpy(result, &context->result, 16);
}
static void invoke_shuffle(const uint32_t lanes[4], uint8_t immediate, uint32_t result[4])
{
    uint32_t indices[4] = {
        immediate & 3U, (immediate >> 2) & 3U,
        (immediate >> 4) & 3U, (immediate >> 6) & 3U,
    };
    vuint32m1_t input = __riscv_vle32_v_u32m1(lanes, 4);
    vuint32m1_t selectors = __riscv_vle32_v_u32m1(indices, 4);
    __riscv_vse32_v_u32m1(result, __riscv_vrgather_vv_u32m1(input, selectors, 4), 4);
}
static const ioitf_add_pd_callbacks ADD_CALLBACKS = {prepare_add, invoke_add, extract_add};
static const ioitf_set1_pd_callbacks SET1_CALLBACKS = {prepare_set1, invoke_set1, extract_set1};

int riscv_mm_add_pd(const ioitf_input *input, ioitf_output *output)
{
    riscv_add_context context;
    return ioitf_execute_add_pd(input, output, &context, &ADD_CALLBACKS, NULL);
}
int riscv_mm_set1_pd(const ioitf_input *input, ioitf_output *output)
{
    riscv_set1_context context;
    return ioitf_execute_set1_pd(input, output, &context, &SET1_CALLBACKS, NULL);
}
int riscv_mm_shuffle_epi32(const ioitf_input *input, ioitf_output *output)
{
    return ioitf_execute_shuffle_epi32(input, output, invoke_shuffle);
}
