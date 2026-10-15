#!/bin/sh
set -eu
project_dir=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
output_dir=${1:-"$project_dir/build/riscv-smoke"}
if [ -n "${EQUILANE_RISCV_CLANG:-}" ]; then
    compiler=$EQUILANE_RISCV_CLANG
elif [ -x /opt/homebrew/opt/llvm/bin/clang ]; then
    compiler=/opt/homebrew/opt/llvm/bin/clang
else
    compiler=$(command -v clang)
fi
emulator=${EQUILANE_RISCV_QEMU:-qemu-system-riscv64}
mkdir -p "$output_dir"
for block in f32x4 f64x2 i8x16 i16x8 i32x4 i64x2; do
    "$compiler" --target=riscv64-unknown-linux-gnu -ffreestanding \
        -march=rv64gcv -mabi=lp64d -mrvv-vector-bits=scalable -mcmodel=medany \
        -O2 -ffp-model=strict -ffunction-sections -fdata-sections \
        -Wall -Wextra -Wpedantic -Werror \
        -c "$project_dir/10_official_suite/riscv/$block.c" -o "$output_dir/$block.o"
done
"$compiler" --target=riscv64-unknown-linux-gnu -ffreestanding \
    -march=rv64gcv -mabi=lp64d -mrvv-vector-bits=scalable -mcmodel=medany \
    -O2 -ffp-model=strict -ffunction-sections -fdata-sections \
    -Wall -Wextra -Wpedantic -Werror -fno-pie -static -nostdlib -fuse-ld=lld \
    -Wl,-no-pie -Wl,--no-dynamic-linker \
    -Wl,--gc-sections -Wl,-T,"$project_dir/tests/native/test_riscv_virt.ld" \
    "$project_dir/tests/native/test_riscv_start.S" \
    "$project_dir/tests/native/test_riscv_smoke.c" "$output_dir/"*.o \
    -o "$output_dir/smoke.elf"
for vlen in 128 256; do
    python3 - "$emulator" "$output_dir/smoke.elf" "$vlen" <<'PY'
import subprocess, sys
subprocess.run([sys.argv[1], "-machine", "virt", "-cpu",
                f"rv64,v=true,vlen={sys.argv[3]},elen=64", "-m", "128M", "-smp", "1",
                "-bios", "none", "-kernel", sys.argv[2], "-display", "none",
                "-serial", "stdio", "-monitor", "none"], check=True, timeout=20)
PY
done
