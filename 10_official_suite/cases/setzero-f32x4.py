"""Case pack for _mm_setzero_ps."""

CASE_YAML = """
schema_version: 1
id: sse2.setzero.f32x4.default
description: construct 4 all-zero f32 lanes without operands

intel:
  symbol: intel_mm_setzero_ps
  required_isa: [sse2]

openpower:
  symbol: power_mm_setzero_ps
  required_isa: [power8, vsx]

arm:
  symbol: arm_mm_setzero_ps
  required_isa: [neon]

riscv:
  symbol: riscv_mm_setzero_ps
  required_isa: [rvv]

signature:
  arguments: []
  return: {type: vector, element: f32, lanes: 4}

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


CASE_ID, MINIMUM_COUNTS, candidates, execute = zero_vector_case("sse2.setzero.f32x4.default", "f32x4")
