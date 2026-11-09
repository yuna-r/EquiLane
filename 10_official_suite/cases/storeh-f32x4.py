"""Case pack for _mm_storeh_pi."""

CASE_YAML = """
schema_version: 1
id: sse2.storeh.f32x4.high
description: store high_half f32 lanes with 1-byte required alignment

intel:
  symbol: intel_mm_storeh_pi
  required_isa: [sse2]

openpower:
  symbol: power_mm_storeh_pi
  required_isa: [power8, vsx]

arm:
  symbol: arm_mm_storeh_pi
  required_isa: [neon]

riscv:
  symbol: riscv_mm_storeh_pi
  required_isa: [rvv]

signature:
  arguments:
    - {name: destination, type: pointer}
    - {name: a, type: vector, element: f32, lanes: 4}
  return: {type: void}

memory_contract:
  destination:
    access: write
    read_ranges: []
    required_alignment: 1
    write_ranges: [{offset: 0, byte_length: 8}]

input_domain:
  exclude: []

comparison:
  mode: bit_exact

environment:
  fp_rounding_modes: [nearest_even]
  observe_fp_exceptions: false

tags: [endianness-sensitive, nan-sensitive, signed-zero-sensitive]
"""

from equilane.casepack_families import memory_store_case


CASE_ID, MINIMUM_COUNTS, candidates, execute = memory_store_case("sse2.storeh.f32x4.high", "f32x4", "high_half")
