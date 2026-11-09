# Official equivalence suite

[日本語](README.md) | [English](README.en.md)

<p>
  <img alt="Intel SSE2" src="https://img.shields.io/badge/Intel-SSE2-0071c5?style=flat&amp;logo=intel&amp;logoColor=white">
  <img alt="OpenPOWER" src="https://img.shields.io/badge/OpenPOWER-POWER8-cb2027?style=flat">
  <img alt="VSX" src="https://img.shields.io/badge/SIMD-VSX-f7b500?style=flat">
  <img alt="254 cases" src="https://img.shields.io/badge/cases-254-7c4dff?style=flat">
  <img alt="254,000 standard vectors" src="https://img.shields.io/badge/standard_vectors-254k-00bfa5?style=flat">
  <img alt="Cross build passing" src="https://img.shields.io/badge/Clang_cross_build-passing-2ea44f?style=flat&amp;logo=llvm">
</p>

This is the repository's official Intel ↔ OpenPOWER / ARM64 / RISC-V64 equivalence suite.
It brings together 254 contracts and source files showing every operation for each architecture.

```text
10_official_suite/
├── cases/       254 single-file case packs
├── intel/       All 254 operations using SSE2
├── openpower/   All 254 operations using VSX
├── arm/         All 254 operations using NEON
├── riscv/       All 254 operations using RVV and C helpers
├── new-case     Scaffold for one case-pack file
└── shortcuts.h  Macros for single-line native wrappers
```

| Block | Cases | Intel | OpenPOWER | ARM64 | RISC-V64 |
|---|---:|---|---|---|---|
| f32 arithmetic / compare / lane / mask and f32/f64/i32 cast / conversion | 83 | [`intel/f32x4.c`](intel/f32x4.c) | [`openpower/f32x4.c`](openpower/f32x4.c) | [`arm/f32x4.c`](arm/f32x4.c) | [`riscv/f32x4.c`](riscv/f32x4.c) |
| f64 arithmetic / bit / construct / lane / compare / conversion / memory | 75 | [`intel/f64x2.c`](intel/f64x2.c) | [`openpower/f64x2.c`](openpower/f64x2.c) | [`arm/f64x2.c`](arm/f64x2.c) | [`riscv/f64x2.c`](riscv/f64x2.c) |
| i8 arithmetic / saturation / average / SAD / minmax / shift / lane / mask | 21 | [`intel/i8x16.c`](intel/i8x16.c) | [`openpower/i8x16.c`](openpower/i8x16.c) | [`arm/i8x16.c`](arm/i8x16.c) | [`riscv/i8x16.c`](riscv/i8x16.c) |
| i16 arithmetic / multiply / saturation / average / minmax / compare / shift / pack / lane | 33 | [`intel/i16x8.c`](intel/i16x8.c) | [`openpower/i16x8.c`](openpower/i16x8.c) | [`arm/i16x8.c`](arm/i16x8.c) | [`riscv/i16x8.c`](riscv/i16x8.c) |
| i32 arithmetic / multiply / logic / compare / shift / construct / pack / lane | 27 | [`intel/i32x4.c`](intel/i32x4.c) | [`openpower/i32x4.c`](openpower/i32x4.c) | [`arm/i32x4.c`](arm/i32x4.c) | [`riscv/i32x4.c`](riscv/i32x4.c) |
| i64 arithmetic / shift / construct / lane | 15 | [`intel/i64x2.c`](intel/i64x2.c) | [`openpower/i64x2.c`](openpower/i64x2.c) | [`arm/i64x2.c`](arm/i64x2.c) | [`riscv/i64x2.c`](riscv/i64x2.c) |
| **Total** | **254** | **254 operations** | **254 operations** | **254 operations** | **254 operations** |

- [`cases/`](cases/) contains case packs defining input generation, comparison rules, and symbols for each architecture.
- [`intel/`](intel/) and [`openpower/`](openpower/) show the core implementations at matching granularity.
- All 254 cases are stored directly in `cases/*.py`. Each file contains a YAML contract and a short family declaration.
- `./10_official_suite/new-case demo-add-i32x4 +` creates a starting point for a user-defined case.
- The adapters that produce execution evidence are in [`../adapters/`](../adapters/).

## Clang cross build

Use a Clang installation with x86_64, ppc64le, aarch64, and riscv64 backends, such as Homebrew LLVM:

```sh
./10_official_suite/cross-compile.sh
```

```text
intel/f64x2.o:     ELF 64-bit LSB relocatable, x86-64
openpower/f64x2.o: ELF 64-bit LSB relocatable, OpenPOWER ELF V2 ABI
```

This produces four architectures × six blocks = 24 ELF objects.
See [Native status](../documentation/native-status.json) for ARM hardware and RISC-V QEMU checks.
Ordinary `check` uses a shared Python-model fixture. The native runner collects artifacts by executing all 254 operations through the shared C ABI.
Use `./docker/run.sh all` to execute and compare the four environments.

```sh
equilane check --target all --profile standard --count-per-case 1000 --showcase-report
```

This command compares 254 cases and 254,000 vectors and generates an HTML report.
