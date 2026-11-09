"""Case pack for _mm_load_si128."""

CASE_YAML = """
schema_version: 1
id: sse2.load.i32x4.aligned
description: load all i32 lanes with 16-byte required alignment

intel:
  symbol: intel_mm_load_si128
  required_isa: [sse2]

openpower:
  symbol: power_mm_load_si128
  required_isa: [power8, vsx]

arm:
  symbol: arm_mm_load_si128
  required_isa: [neon]

riscv:
  symbol: riscv_mm_load_si128
  required_isa: [rvv]

signature:
  arguments:
    - {name: source, type: pointer}
  return: {type: vector, element: i32, lanes: 4}

memory_contract:
  source:
    access: read
    read_ranges: [{offset: 0, byte_length: 16}]
    required_alignment: 16
    write_ranges: []

input_domain:
  exclude: []

comparison:
  mode: bit_exact

environment:
  fp_rounding_modes: [nearest_even]
  observe_fp_exceptions: false

tags: [endianness-sensitive]
"""

from equilane.casepack_families import memory_load_case


CASE_ID, MINIMUM_COUNTS, candidates, execute = memory_load_case("sse2.load.i32x4.aligned", "i32x4", "all", required_alignment=16)
