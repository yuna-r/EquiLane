# Native AArch64 example-case adapter

`sse2_pd.c` uses actual AArch64 NEON instructions and the existing common
`ioitf_input` / `ioitf_output` ABI. It exports:

- `arm_mm_add_pd`: two native binary64 additions;
- `arm_mm_set1_pd`: native scalar broadcast;
- `arm_mm_shuffle_epi32`: exact i32 lane selection for the example contract.

Compile with `include/`, `adapters/common/` and `adapters/arm/` include paths,
and link `adapters/common/f64x2_adapter.c`. On an arm64 Mac, use
`-arch arm64 -ffp-model=strict`; Linux cross-compilation uses
`--target=aarch64-linux-gnu -ffreestanding -march=armv8-a+simd`.

The adapter has **three example cases**, while the independent
[`10_official_suite/arm`](../../10_official_suite/arm/README.md) vector-wrapper
sources expose all 254 official counterparts. Neither count demonstrates
that every case has completed native equivalence testing.

Native NaN payload/priority and invalid-operation results are not rewritten to
Intel or Python model bits. The common decoder, environment/error handling and
buffer ownership are shared with the existing native adapters.
