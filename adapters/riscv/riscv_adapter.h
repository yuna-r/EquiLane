#ifndef EQUILANE_RISCV_ADAPTER_H
#define EQUILANE_RISCV_ADAPTER_H
#include "framework/case_abi.h"

/* Common encoded ABI v1. Raw fixed-vector wrappers live in official/riscv. */
int riscv_mm_add_pd(const ioitf_input *, ioitf_output *);
int riscv_mm_set1_pd(const ioitf_input *, ioitf_output *);
int riscv_mm_shuffle_epi32(const ioitf_input *, ioitf_output *);
#endif
