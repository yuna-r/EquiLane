"""Case pack for _mm_setzero_si128."""

CASE_YAML = """
schema_version: 1
id: sse2.setzero.i32x4.default
description: construct 4 all-zero i32 lanes without operands

intel:
  symbol: intel_mm_setzero_si128
  required_isa: [sse2]

openpower:
  symbol: power_mm_setzero_si128
  required_isa: [power8, vsx]

arm:
  symbol: arm_mm_setzero_si128
  required_isa: [neon]

riscv:
  symbol: riscv_mm_setzero_si128
  required_isa: [rvv]

signature:
  arguments: []
  return: {type: vector, element: i32, lanes: 4}

input_domain:
  exclude: []

comparison:
  mode: bit_exact

environment:
  fp_rounding_modes: [nearest_even]
  observe_fp_exceptions: false

tags: []
"""

from equilane.casepack_families import zero_vector_case


CASE_ID, MINIMUM_COUNTS, candidates, execute = zero_vector_case("sse2.setzero.i32x4.default", "i32x4")
