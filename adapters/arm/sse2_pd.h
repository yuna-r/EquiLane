#ifndef EQUILANE_ADAPTER_ARM_SSE2_PD_H
#define EQUILANE_ADAPTER_ARM_SSE2_PD_H
#include "framework/case_abi.h"
#if defined(__GNUC__) || defined(__clang__)
#define EQUILANE_ARM_API __attribute__((visibility("default")))
#else
#define EQUILANE_ARM_API
#endif
EQUILANE_ARM_API int arm_mm_add_pd(const ioitf_input*, ioitf_output*);
EQUILANE_ARM_API int arm_mm_set1_pd(const ioitf_input*, ioitf_output*);
EQUILANE_ARM_API int arm_mm_shuffle_epi32(const ioitf_input*, ioitf_output*);
#undef EQUILANE_ARM_API
#endif
