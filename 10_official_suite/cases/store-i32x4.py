"""Case pack for _mm_store_si128."""

CASE_YAML = """
schema_version: 1
id: sse2.store.i32x4.aligned
description: store all i32 lanes with 16-byte required alignment

intel:
  symbol: intel_mm_store_si128
  required_isa: [sse2]

openpower:
  symbol: power_mm_store_si128
  required_isa: [power8, vsx]

arm:
  symbol: arm_mm_store_si128
  required_isa: [neon]

riscv:
  symbol: riscv_mm_store_si128
  required_isa: [rvv]

signature:
  arguments:
    - {name: destination, type: pointer}
    - {name: a, type: vector, element: i32, lanes: 4}
  return: {type: void}

memory_contract:
  destination:
    access: write
    read_ranges: []
    required_alignment: 16
    write_ranges: [{offset: 0, byte_length: 16}]

input_domain:
  exclude: []

comparison:
  mode: bit_exact

environment:
  fp_rounding_modes: [nearest_even]
  observe_fp_exceptions: false

tags: [endianness-sensitive]
"""

from equilane.casepack_families import memory_store_case


CASE_ID, MINIMUM_COUNTS, candidates, execute = memory_store_case("sse2.store.i32x4.aligned", "i32x4", "all", required_alignment=16)
