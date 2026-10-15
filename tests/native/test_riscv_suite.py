#!/usr/bin/env python3
"""Compare every RVV wrapper against runtime Intel/SSE2 outputs, byte for byte.

The expected values come from an x86_64 C executable, never a Python model.
On Apple Silicon that reference runs under Rosetta. The observed RISC-V C
executable runs in QEMU virt at VLEN=128 and 256. This is emulated execution,
not evidence from physical Intel/RISC-V hardware or a native Linux runner.
All mismatches are retained and cause a nonzero exit status.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
BLOCKS = ("f32x4", "f64x2", "i8x16", "i16x8", "i32x4", "i64x2")
CLANG = "/opt/homebrew/opt/llvm/bin/clang" if Path("/opt/homebrew/opt/llvm/bin/clang").is_file() else "clang"
RV_FLAGS = ["--target=riscv64-unknown-linux-gnu", "-ffreestanding", "-march=rv64gcv",
            "-mabi=lp64d", "-mrvv-vector-bits=scalable", "-mcmodel=medany", "-O2", "-ffp-model=strict"]
TYPES = {"i8x16": "signed char", "u8x16": "unsigned char", "i16x8": "short",
         "u16x8": "unsigned short", "i32x4": "int", "u32x4": "unsigned int",
         "i64x2": "long long", "u64x2": "unsigned long long", "f32x4": "float", "f64x2": "double"}


def run(command, **kwargs):
    result = subprocess.run(command, capture_output=True, text=True, timeout=60, **kwargs)
    if result.returncode:
        raise RuntimeError(f"command failed ({result.returncode}): {command}\n{result.stderr}")
    return result


def declarations(compiler):
    functions = []
    for block in BLOCKS:
        ast = json.loads(run([compiler, *RV_FLAGS, "-Xclang", "-ast-dump=json", "-fsyntax-only",
                              str(ROOT / f"10_official_suite/riscv/{block}.c")]).stdout)
        for node in ast["inner"]:
            if node.get("kind") != "FunctionDecl" or not node.get("name", "").startswith("riscv_"):
                continue
            result = node["type"]["qualType"].split(" (")[0]
            parameters = [(p.get("name", "arg"), p["type"]["qualType"])
                          for p in node.get("inner", []) if p["kind"] == "ParmVarDecl"]
            functions.append((node["name"][len("riscv_"):], result, parameters))
    if len(functions) != 254 or len({name for name, _, _ in functions}) != 254:
        raise ValueError("the six official blocks must expose all 254 unique operations")
    return sorted(functions)


def float_bits(width, variant, position):
    rows = {
        32: [(0, 0x80000000, 0, 0x80000000), (0x3FC00000, 0xC0200000, 0x3F800000, 0x40000000),
             (0x7FC00042, 0x7F800035, 0x7F800000, 0xFF800000)],
        64: [(0, 0x8000000000000000), (0x3FF8000000000000, 0xC004000000000000),
             (0x7FF8000000000042, 0x7FF0000000000035)],
    }
    return rows[width][variant][position % len(rows[width][variant])]


def integer_bits(width, variant, position):
    maximum = (1 << width) - 1
    rows = [(0, 1, maximum, 1 << (width - 1)), (3, maximum - 1, 1 << (width - 1), (1 << (width - 1)) - 1),
            (maximum, 1 << (width - 1), 0x55 & maximum, 0xAA & maximum)]
    return rows[variant][position % 4]


def immediate(name, variant):
    if "extract" in name or "insert" in name:
        return (0, 3, 7)[variant]
    if "shuffle_f64" in name:
        return (0, 1, 3)[variant]
    if "shuffle" in name:
        return (0, 27, 255)[variant]
    width = 16 if "bytes" in name else 16 if "i16" in name else 32 if "i32" in name else 64
    return (0, width - 1, 255)[variant]


def source(functions):
    lines = ["/* Generated native calls; expected output is captured from Intel SSE2. */\n#include <stddef.h>\n#include <stdint.h>\n"]
    lines.append("#if defined(__riscv)\n#include \"rvv128.h\"\n#define TARGET(name) riscv_##name\n#else\n#include <stdio.h>\n#include <emmintrin.h>\n#define TARGET(name) intel_##name\n")
    for vector, scalar in TYPES.items():
        lines.append(f"typedef {scalar} {vector} __attribute__((vector_size(16)));\n")
    lines.append("#endif\n")
    for name, result, parameters in functions:
        lines.append(f"extern {result} TARGET({name})({', '.join(t for _, t in parameters)});\n")
    lines.append('''#if defined(__riscv)
static void text(const char *s) {
    volatile unsigned char *uart = (volatile unsigned char *)0x10000000UL;
    while (*s) { while (!(uart[5]&0x20)) {} uart[0]=(unsigned char)*s++; }
}
void *memcpy(void *destination, const void *source, size_t length) {
    unsigned char *d=destination; const unsigned char *s=source;
    for (size_t i=0;i<length;++i) d[i]=s[i]; return destination;
}
void *memset(void *destination, int value, size_t length) {
    unsigned char *d=destination; for (size_t i=0;i<length;++i) d[i]=(unsigned char)value;
    return destination;
}
static void emit(const char *name,const unsigned char *bytes,size_t count) {
    const char hex[]="0123456789abcdef"; text(name); text(" ");
    for(size_t i=0;i<count;++i) { char b[3]={hex[bytes[i]>>4],hex[bytes[i]&15],0}; text(b); }
    text("\\n");
}
#else
static void emit(const char *name,const unsigned char *bytes,size_t count) {
    printf("%s ",name); for(size_t i=0;i<count;++i) printf("%02x",bytes[i]); putchar('\\n');
}
#endif
int riscv_smoke_main(void) {
''')
    for name, result, parameters in functions:
        for variant in range(3):
            lines.append("{\n")
            arguments, memory = [], []
            for position, (parameter, type_name) in enumerate(parameters):
                variable = f"p{position}"
                arguments.append(variable)
                if "*" in type_name:
                    initial = [(i * 29 + variant * 53) % 256 for i in range(64)]
                    lines.append(f"unsigned char buffer{position}[64] __attribute__((aligned(16))) = {{{','.join(map(str,initial))}}};\n")
                    offset = 16 if variant else 0
                    lines.append(f"{type_name} {variable}=buffer{position}+{offset};\n")
                    memory.append(f"buffer{position}")
                elif type_name in TYPES:
                    match = re.fullmatch(r"([iuf])(\d+)x(\d+)", type_name)
                    kind, width, count = match[1], int(match[2]), int(match[3])
                    unsigned = f"u{width}x{count}"
                    bits = [(float_bits if kind == "f" else integer_bits)(width, variant, position + lane) for lane in range(count)]
                    lines.append(f"{type_name} {variable}=({type_name})({unsigned}){{{','.join(hex(x)+'ULL' for x in bits)}}};\n")
                elif type_name in ("float", "double"):
                    width = 32 if type_name == "float" else 64
                    integer = "unsigned int" if width == 32 else "unsigned long long"
                    lines.append(f"union {{ {integer} bits; {type_name} value; }} scalar{position}={{.bits={hex(float_bits(width,variant,position))}ULL}};\n")
                    arguments[-1] = f"scalar{position}.value"
                else:
                    value = immediate(name, variant) if parameter == "imm" else integer_bits(64 if "long long" in type_name else 32, variant, position)
                    lines.append(f"{type_name} {variable}=({type_name}){hex(value)}ULL;\n")
            expression = f"TARGET({name})({','.join(arguments)})"
            if result == "void":
                lines.append(expression + ";\n")
                return_size = 0
            else:
                lines.append(f"{result} returned={expression};\n")
                return_size = 16 if result in TYPES else 8 if "long long" in result else 4
            total = return_size + 64 * len(memory)
            lines.append(f"unsigned char actual[{total}];\n")
            if return_size:
                lines.append(f"__builtin_memcpy(actual,&returned,{return_size});\n")
            for index, allocation in enumerate(memory):
                lines.append(f"__builtin_memcpy(actual+{return_size+64*index},{allocation},64);\n")
            lines.append(f'emit("{name}#{variant}",actual,sizeof(actual));\n}}\n')
    lines.append("return 0;\n}\n#if !defined(__riscv)\nint main(void) { _mm_setcsr(0x1f80); return riscv_smoke_main(); }\n#endif\n")
    return "".join(lines)


def rows(output):
    parsed = {}
    for line in output.splitlines():
        name, bits = line.split()
        if name in parsed or not re.fullmatch(r"[0-9a-f]+", bits):
            raise ValueError("duplicate or malformed native output")
        parsed[name] = bits
    if len(parsed) != 762:
        raise ValueError(f"native executable emitted {len(parsed)} outputs, expected 762")
    return parsed


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "build/riscv-native-suite")
    parser.add_argument("--clang", default=CLANG)
    parser.add_argument("--reference-clang", default="/usr/bin/clang")
    parser.add_argument("--qemu", default="qemu-system-riscv64")
    args = parser.parse_args()
    output = args.output.resolve(); output.mkdir(parents=True, exist_ok=True)
    generated = output / "calls.c"; generated.write_text(source(declarations(args.clang)))
    intel_objects, riscv_objects = [], []
    for block in BLOCKS:
        target = output / (block + "-intel.o"); intel_objects.append(str(target))
        run([args.reference_clang, "-arch", "x86_64", "-O2", "-msse2", "-ffp-model=strict", "-c",
             str(ROOT / f"10_official_suite/intel/{block}.c"), "-o", str(target)])
        target = output / (block + "-riscv.o"); riscv_objects.append(str(target))
        run([args.clang, *RV_FLAGS, "-c", str(ROOT / f"10_official_suite/riscv/{block}.c"), "-o", str(target)])
    reference = output / "intel-reference"
    run([args.reference_clang, "-arch", "x86_64", "-O2", "-msse2", "-ffp-model=strict", str(generated), *intel_objects, "-o", str(reference)])
    expected_output = run([str(reference)]).stdout
    (output / "intel-outputs.txt").write_text(expected_output)
    expected = rows(expected_output)
    guest = output / "riscv-suite.elf"
    run([args.clang, *RV_FLAGS, "-fno-builtin", "-fno-pie", "-static", "-nostdlib", "-fuse-ld=lld",
         "-Wl,-no-pie", "-Wl,--no-dynamic-linker", "-Wl,-T,"+str(ROOT / "tests/native/test_riscv_virt.ld"),
         "-I"+str(ROOT / "10_official_suite/riscv"), str(ROOT / "tests/native/test_riscv_start.S"),
         str(generated), *riscv_objects, "-o", str(guest)])
    results = {}
    for vlen in (128, 256):
        observed_output = run([args.qemu, "-machine", "virt", "-cpu", f"rv64,v=true,vlen={vlen},elen=64",
                               "-m", "128M", "-smp", "1", "-bios", "none", "-kernel", str(guest),
                               "-display", "none", "-serial", "stdio", "-monitor", "none"]).stdout
        (output / f"riscv-vlen{vlen}-outputs.txt").write_text(observed_output)
        observed = rows(observed_output)
        if set(expected) != set(observed):
            raise ValueError("native result identities differ")
        differences = [{"sample": name, "expected_intel": expected[name], "actual_riscv": observed[name]}
                       for name in sorted(expected) if expected[name] != observed[name]]
        results[str(vlen)] = {"samples": 762, "matched": 762-len(differences), "mismatches": differences}
    sources = [generated, ROOT / "10_official_suite/riscv/rvv128.h",
               *[ROOT / f"10_official_suite/{role}/{block}.c" for role in ("intel", "riscv") for block in BLOCKS]]
    report = {"artifact_type": "equilane.rvv-native-suite", "schema_version": 1, "operations": 254,
              "status": "mismatch" if any(r["mismatches"] for r in results.values()) else "pass",
              "emulated_execution": True, "native_cross_host_evidence": False,
              "reference": "x86_64 Intel/SSE2 C executable under Rosetta on arm64 Darwin",
              "observed": "RV64/RVV1.0 QEMU virt bare-metal C executable",
              "rvv_compile_flags": RV_FLAGS, "compiler": run([args.clang,"--version"]).stdout.splitlines()[0],
              "reference_compiler": run([args.reference_clang,"--version"]).stdout.splitlines()[0],
              "qemu": run([args.qemu,"--version"]).stdout.splitlines()[0], "vlen_results": results,
              "source_sha256": {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in sources}}
    marker = output / "summary.json"; marker.write_text(json.dumps(report, indent=2)+"\n")
    print(json.dumps({"operations":254,"summary":str(marker),"status":report["status"],
                      "vlen":{v:{"samples":r["samples"],"matched":r["matched"],"mismatch":len(r["mismatches"])} for v,r in results.items()}}))
    return 1 if report["status"] == "mismatch" else 0


if __name__ == "__main__":
    sys.exit(main())
