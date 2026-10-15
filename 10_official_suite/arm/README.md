# ARM64 / NEON wrappers

Six native AArch64 C blocks expose all **254 `arm_*` counterparts** of the
Intel / OpenPOWER official wrapper suite. `wrappers.h` declares the raw vector
API; `neon_helpers.h` supplies native NEON saturation, packing, widening and
shift operations. Fixed lane arrangements and the full 64-bit shift-count
checks implement operations without a direct one-instruction equivalent.
These files neither import nor invoke the Python portable fixture.

Floating arithmetic, square root, min/max and numeric conversions execute
native ARM instructions. Their NaN selection, zero ties and invalid integer
conversion results remain visible; the wrappers do not replace them with
SSE2 or portable-model results. `cvt` float-to-integer uses native nearest-even
conversion, while `cvtt` uses native truncation. Other floating operations use
the caller's FPCR. Callers must supply the intended rounding/FTZ/default-NaN
configuration; these wrappers do not modify it.

## Build

The Linux AArch64 object build needs only LLVM Clang's builtin headers:

```sh
/opt/homebrew/opt/llvm/bin/clang \
  --target=aarch64-linux-gnu -ffreestanding -march=armv8-a+simd \
  -std=c11 -O2 -ffp-model=strict -Wall -Wextra -Werror \
  -c 10_official_suite/arm/f64x2.c -o /tmp/equilane-arm-f64x2.o
```

Repeat for `f32x4`, `i8x16`, `i16x8`, `i32x4` and `i64x2`. Object compilation
proves source/symbol coverage; it is not runtime conformance evidence.
Apple Clang21 lacks the newer ACLE names for scalar f64-to-i32 conversion, so
those two helpers execute the base-A64 `FCVTNS` / `FCVTZS` instructions directly.

On an actual arm64 Mac, compile and run the wrapper smoke:

```sh
clang -arch arm64 -std=c11 -O2 -ffp-model=strict -Wall -Wextra -Werror \
  tests/native/test_arm_wrappers.c \
  10_official_suite/arm/f32x4.c 10_official_suite/arm/f64x2.c \
  10_official_suite/arm/i8x16.c 10_official_suite/arm/i16x8.c \
  10_official_suite/arm/i32x4.c 10_official_suite/arm/i64x2.c \
  -o /tmp/equilane-arm-wrapper-smoke
/tmp/equilane-arm-wrapper-smoke
```

The smoke checks integer boundaries, saturating packs, overflow, full-width
shift counts, all 256 i32 shuffle selectors, inactive scalar signaling-NaN
lanes, exact finite FP controls, subnormal widening and unaligned memory guards.
It prints native FP results alongside fixed strict-SSE2/Rosetta references.
It does **not** establish native equivalence for every one of the 254 cases.

## Observed native differences

On the current arm64 Mac with FPCR `0`, the native smoke retained these results:

| Operation | ARM native bits | SSE2 captured reference bits |
|---|---|---|
| `+inf + -inf`, f64 | `0x7ff8000000000000` | `0xfff8000000000000` |
| `qNaN(0x41) + sNaN(0x03)`, f64 | `0x7ff8000000000003` | `0x7ff8000000000041` |
| `min(qNaN(0x41), 1)`, f64 | `0x7ff8000000000041` | `0x3ff0000000000000` |
| `max(+0, -0)`, f64 | `0x0000000000000000` | `0x8000000000000000` |
| `qNaN` to i32 | `0x00000000` | `0x80000000` |
| `+inf` to i32 | `0x7fffffff` | `0x80000000` |

The SSE2 values come from the independent strict x86_64 probes under Rosetta;
this is not an Intel-CPU/OpenPOWER-CPU cross-host capture. Keep those distinctions
when interpreting a bit-exact comparison.

The framework's three example-case C ABI callbacks live in
[`adapters/arm`](../../adapters/arm/README.md). The raw wrapper suite and that
three-case adapter have different coverage boundaries.

Primary instruction mapping:
[Arm Neon Intrinsics Reference](https://arm-software.github.io/acle/neon_intrinsics/),
[Arm C Language Extensions](https://arm-software.github.io/acle/main/acle.html).
