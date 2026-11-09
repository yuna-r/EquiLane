# RISC-V RV64 / RVV 1.0

[日本語](README.md) | [English](README.en.md)

RISC-V C wrappers implement the same 254 operations as the Intel side.
They are grouped in six files: `f32x4.c`, `f64x2.c`, `i8x16.c`, `i16x8.c`, `i32x4.c`, and `i64x2.c`.

Public vector types are 16-byte C vectors. Internally, RVV intrinsics from `rvv128.h`
receive an active lane count of `128 / SEW`. Floating-point arithmetic, saturating integer operations,
shifts, and broadcasts use explicit RVV loads, operations, and stores.
C helpers running on RISC-V implement packing, shuffling, lane placement, and SSE2 special-value
and conversion rules. The implementation does not call Python fixtures.

Requirements are RV64, RVV 1.0, F/D, little-endian storage, and VLEN of at least 128.
This cross-build form was checked with Clang 22:

```sh
clang --target=riscv64-unknown-linux-gnu -ffreestanding \
  -march=rv64gcv -mabi=lp64d -mrvv-vector-bits=scalable \
  -O2 -ffp-model=strict -Wall -Wextra -Wpedantic -Werror \
  -c 10_official_suite/riscv/f64x2.c -o build/riscv-f64x2.o
```

Do not use `-mrvv-vector-bits=128`. It lets the compiler assume a physical VLEN of 128 and
substitute whole-register loads/stores. Running that binary with VLEN 256 could access beyond
a 16-byte slot. The header also rejects fixed-VLEN configuration.

## Run in QEMU

```sh
tests/native/test_riscv_smoke.sh
```

The same bare-metal ELF runs on QEMU `virt` with VLEN 128 and 256.
M-mode startup enables FS/VS and prints results over UART.
The 18 checks cover all six element types, saturation and shift boundaries, unaligned-memory guards,
unused scalar NaN lanes, dynamic rounding, and inexact/invalid `fflags`.
The recorded run passed all 18 checks at both VLENs with no failures.

To compare every wrapper directly with Intel/SSE2 execution results:

```sh
.venv/bin/python tests/native/test_riscv_suite.py
```

This capture/comparison tool uses a macOS x86_64 C executable as its reference path.
On Apple Silicon, the reference runs through Rosetta and RISC-V runs through QEMU.
Three inputs per operation produce 762 comparisons for each VLEN. Inputs include mixed lanes,
signed zeros, integer boundaries, NaNs, legal immediates, and prefix/suffix memory guards.
Return values and entire buffers are compared byte by byte. Expected values do not come from
the Python model. Artifacts and source digests remain in `build/riscv-native-suite/summary.json`.

That recorded corpus produced 750 matches and 12 mismatches at both VLEN 128 and 256.
Packed/scalar f32/f64 add, sub, and mul differ because RVV canonical NaNs differ from SSE2 NaN
payloads and signs. These differences remain recorded, and the comparison tool returns exit code 1.
The full result sets agree between both VLENs. This does not prove agreement for other inputs,
every rounding mode, or all native FP exception behavior.

The results above are bare-metal checks of **RISC-V machine code executing in an emulator**,
distinct from observations on physical RISC-V hardware. The current Linux runner supports
all 254 operations through the shared C ABI with libc/fenv execution.
Use `./docker/run.sh all` for the four-environment Docker comparison and see
[Native status](../../documentation/native-status.json) for current verification records.

Specification references:
[RVV 1.0](https://docs.riscv.org/reference/isa/unpriv/v-st-ext),
[RVV C intrinsics](https://docs.riscv.org/reference/vector-c-intrinsics/rvv-intrinsic-examples.html),
[LLVM RVV lowering](https://llvm.org/docs/RISCV/RISCVVectorExtension.html),
[QEMU virt](https://www.qemu.org/docs/master/system/riscv/virt.html).
