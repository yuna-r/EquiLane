# Native implementation status

EquiLane keeps the Intel/OpenPOWER ABI v1 boundary and adds ARM64 NEON and
RISC-V64 RVV 1.0 targets. The normal `check` command uses development fixtures;
its comparisons share a Python model and do not measure CPU differences.

For exact observed inputs, output bits, model failure attribution, and reproduction
commands, see the [detailed mismatch report](MISMATCH_REPORT.md). Native observations
are listed separately from shared Python-model findings.

| Target | Official C wrappers | Example ABI callbacks | Execution checked here |
|---|---:|---:|---|
| Intel / SSE2 | 254 | 3 | x86_64 probes under Rosetta |
| OpenPOWER / POWER8 VSX | 254 | 3 | Cross-build only |
| ARM64 / NEON | 254 | 3 | Native arm64 Mac |
| RISC-V64 / RVV 1.0 | 254 | 3 | QEMU RV64, VLEN 128 and 256 |

Every target has six C blocks (`f32x4`, `f64x2`, `i8x16`, `i16x8`, `i32x4`,
`i64x2`). Their 24 standalone objects cross-compile without target libc sysroots:

```sh
./10_official_suite/cross-compile.sh
```

The ARM and RISC-V bindings are project extensions. Old case packs containing
only Intel and OpenPOWER remain valid; missing extra bindings yield `unsupported`.
The new targets use little-endian LP64 architecture profiles (`aarch64`, `riscv64`).

## Native checks

On ARM64 macOS or Linux, CMake builds the NEON adapter and wrapper self-test.
The latter performs 4,596 checks even in Release builds: integer boundaries,
saturating packs, complete shuffle immediates, full-width shifts, scalar inactive
lanes, subnormal widening and unaligned memory guards. Native NaN, zero-tie and
float-to-integer differences are printed without normalization.

```sh
cmake -S . -B build/native -DBUILD_TESTING=ON -DCMAKE_BUILD_TYPE=Release
cmake --build build/native --parallel
ctest --test-dir build/native --output-on-failure
```

With LLVM Clang and `qemu-system-riscv64` available:

```sh
./tests/native/test_riscv_smoke.sh
python3 tests/native/test_riscv_suite.py
```

The smoke performs 18 checks at each VLEN, including memory guards, vector widths,
saturation, large shifts, dynamic rounding and exception flags. The full suite
executes all 254 wrappers with three deterministic inputs each. At both VLENs,
750 of 762 byte results matched the strict SSE2/Rosetta reference; 12 floating
add/subtract/multiply samples differed because RVV retains its canonical NaN.
The command records every difference in `build/riscv-native-suite/summary.json`
and exits with status 1. These are CPU semantic differences, separate from the
portable model's known bugs. This is emulated execution, with a Rosetta reference.

RVV uses explicit element counts and `-mrvv-vector-bits=scalable`. A larger
physical register must not overwrite the fixed 16-byte logical vector slots.
Detailed implementation mappings and observed bits are in [ARM](10_official_suite/arm/README.md)
and [RISC-V](10_official_suite/riscv/README.md).

## C ABI boundary

All four architecture adapters expose `_mm_add_pd`, `_mm_set1_pd` and
`_mm_shuffle_epi32` through the canonical ABI. Other wrappers are raw C functions;
they are not yet wired into the encoded ABI. The RISC-V adapter was cross-compiled,
but its Linux libc/fenv link and runtime remain unverified.

The common boundary implements LP64 size/offset assertions, strict case-specific
RFC 8785 decoding, output-capacity negotiation, logical lane encoding, requested
rounding and FP exception capture. Input preparation precedes flag clearing;
extraction follows observation. The three public example contracts disable FP
exception observation, while the internal capture path is independently tested.
Shuffle accepts only the registered literals `0`, `1`, `27` and `255`.
The portable adapter remains a development fixture.

Still under development: the native runner and process isolation, dynamic symbol
registry, CPU/FP-control preflight, build-evidence collector, arbitrary-signature
JSON decoding, and encoded pointer/memory cases. Official wrapper execution and
cross-builds do not by themselves produce conformant native result manifests.
