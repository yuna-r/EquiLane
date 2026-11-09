#!/usr/bin/env python3
"""Capture real SIMD wrapper outputs in a target container and compare them.

This deliberately has its own small artifact schema: it tests returned bytes
and memory, not the encoded adapter ABI, FP exception flags or all rounding
modes. Expected results always come from a captured Intel/SSE2 C executable.
CPU emulation is provided by Docker/binfmt outside this process.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import html
import json
import os
from pathlib import Path
import platform
import re
import subprocess
import sys
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
BLOCKS = ("f32x4", "f64x2", "i8x16", "i16x8", "i32x4", "i64x2")
TARGETS = {
    "intel": ("intel", ("x86_64", "amd64"), ["-march=x86-64", "-msse2", "-mno-avx"]),
    "openpower": ("power", ("ppc64le", "powerpc64le"), ["-mcpu=power8", "-maltivec", "-mvsx"]),
    "arm": ("arm", ("aarch64", "arm64"), ["-march=armv8-a+simd"]),
    "riscv": ("riscv", ("riscv64",), ["-march=rv64gcv", "-mabi=lp64d", "-mrvv-vector-bits=scalable"]),
}
TYPES = {
    "i8x16": "signed char", "u8x16": "unsigned char",
    "i16x8": "short", "u16x8": "unsigned short",
    "i32x4": "int", "u32x4": "unsigned int",
    "i64x2": "long long", "u64x2": "unsigned long long",
    "f32x4": "float", "f64x2": "double",
}
SCALAR_SIZES = {"signed char": 1, "short": 2, "int": 4, "unsigned": 4,
                "long long": 8, "float": 4, "double": 8}
VARIANTS = 3


def encoded(value):
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_bytes(encoded(value))
    temporary.replace(path)


def timestamp():
    return datetime.now(timezone.utc).isoformat()


def digest(data):
    return hashlib.sha256(data).hexdigest()


def declarations(root=ROOT):
    """Read the shared call signatures from the project-owned RVV wrappers.

    These wrappers use the same fixed 128-bit vector aliases as the other
    implementations. Parsing this restricted declaration syntax avoids needing
    a cross sysroot merely to generate identical inputs in each container.
    An incomplete/new declaration is a hard error, never silently skipped.
    """
    functions = []
    for block in BLOCKS:
        source = (root / "10_official_suite/riscv" / f"{block}.c").read_text()
        for match in re.finditer(
            r"IOITF_(UNARY|BINARY)\(\s*([\w ]+?)\s*,\s*(\w+)\s*,\s*riscv_(\w+)\s*,", source
        ):
            arity, result, argument, name = match.groups()
            functions.append((name, result.strip(), [("a", argument)] +
                              ([("b", argument)] if arity == "BINARY" else [])))
        for match in re.finditer(
            r"^([\w ]+\*?)\s+riscv_(\w+)\(([^)]*)\)\s*\{", source, re.MULTILINE
        ):
            result, name, arguments = match.groups()
            parameters = []
            if arguments.strip() not in ("", "void"):
                for argument in arguments.split(","):
                    parameter = re.fullmatch(r"\s*(.*?)\s*\b(\w+)\s*", argument, re.DOTALL)
                    if parameter is None:
                        raise ValueError(f"cannot parse parameter: {name}: {argument}")
                    parameters.append((parameter[2], " ".join(parameter[1].split())))
            functions.append((name, result.strip(), parameters))
    names = [name for name, _, _ in functions]
    if len(names) != 254 or len(set(names)) != 254:
        raise ValueError(f"expected 254 unique wrapper operations, found {len(names)}")
    for name, result, parameters in functions:
        for type_name in [result, *(t for _, t in parameters)]:
            if type_name not in TYPES and type_name not in SCALAR_SIZES and type_name not in (
                "void", "void *", "const void *"
            ):
                raise ValueError(f"unsupported wrapper type: {name}: {type_name}")
    return sorted(functions)


def float_bits(width, variant, position):
    rows = {
        32: [(0, 0x80000000, 0, 0x80000000),
             (0x3FC00000, 0xC0200000, 0x3F800000, 0x40000000),
             (0x7FC00042, 0x7F800035, 0x7F800000, 0xFF800000)],
        64: [(0, 0x8000000000000000), (0x3FF8000000000000, 0xC004000000000000),
             (0x7FF8000000000042, 0x7FF0000000000035)],
    }
    return rows[width][variant][position % len(rows[width][variant])]


def integer_bits(width, variant, position):
    maximum = (1 << width) - 1
    rows = [(0, 1, maximum, 1 << (width - 1)),
            (3, maximum - 1, 1 << (width - 1), (1 << (width - 1)) - 1),
            (maximum, 1 << (width - 1), 0x55 & maximum, 0xAA & maximum)]
    return rows[variant][position % 4]


def immediate(name, variant):
    if "extract" in name or "insert" in name:
        return (0, 3, 7)[variant]
    if "shuffle_f64" in name:
        return (0, 1, 3)[variant]
    if "shuffle" in name:
        return (0, 27, 255)[variant]
    width = 16 if "bytes" in name or "i16" in name else 32 if "i32" in name else 64
    return (0, width - 1, 255)[variant]


def corpus(functions):
    samples = []
    for name, result, parameters in functions:
        for variant in range(VARIANTS):
            arguments = []
            for position, (parameter, type_name) in enumerate(parameters):
                if "*" in type_name:
                    buffer = bytes((i * 29 + variant * 53) % 256 for i in range(64))
                    offset = (17 if name.startswith(("loadu_", "storeu_")) else 16) if variant else 0
                    arguments.append({"type": type_name, "bytes": buffer.hex(), "offset": offset})
                elif type_name in TYPES:
                    kind, width, count = re.fullmatch(r"([iuf])(\d+)x(\d+)", type_name).groups()
                    width, count = int(width), int(count)
                    bits = [(float_bits if kind == "f" else integer_bits)(width, variant, position + lane)
                            for lane in range(count)]
                    arguments.append({"type": type_name, "bytes": b"".join(
                        value.to_bytes(width // 8, "little") for value in bits).hex()})
                elif type_name in ("float", "double"):
                    width = 8 * SCALAR_SIZES[type_name]
                    value = float_bits(width, variant, position)
                    arguments.append({"type": type_name, "bytes": value.to_bytes(width // 8, "little").hex()})
                else:
                    width = 8 * SCALAR_SIZES[type_name]
                    value = immediate(name, variant) if parameter == "imm" else integer_bits(width, variant, position)
                    arguments.append({"type": type_name, "bytes": value.to_bytes(width // 8, "little").hex()})
            size = 0 if result == "void" else 16 if result in TYPES else SCALAR_SIZES[result]
            size += 64 * sum("*" in t for _, t in parameters)
            samples.append({"sample": f"{name}#{variant}", "operation": name,
                            "return_type": result, "arguments": arguments, "output_bytes": size})
    return {"schema_version": 1, "artifact_type": "equilane.native-wrapper-corpus",
            "rounding": "nearest_even", "byte_order": "little", "samples": samples}


def source(functions, inputs, target):
    prefix = TARGETS[target][0]
    lines = ["/* Generated native calls: inputs are specified in corpus.json. */\n",
             "#include <stdint.h>\n#include <stdio.h>\n#include <string.h>\n#include <fenv.h>\n"]
    if target == "intel":
        lines.append("#include <emmintrin.h>\n")
    for vector, scalar in TYPES.items():
        if target == "intel":
            # Match the actual wrapper prototypes, not just their SSE register
            # calling convention: every Intel integer vector is __m128i.
            native = "__m128" if vector == "f32x4" else "__m128d" if vector == "f64x2" else "__m128i"
            lines.append(f"typedef {native} {vector};\n")
        else:
            lines.append(f"typedef {scalar} {vector} __attribute__((vector_size(16)));\n")
    for name, result, parameters in functions:
        lines.append(f"extern {result} {prefix}_{name}({', '.join(t for _, t in parameters) or 'void'});\n")
    lines.append('''static void emit(const char *sample, const unsigned char *data, size_t size) {
    printf("%s ", sample);
    for (size_t i=0; i<size; ++i) printf("%02x", data[i]);
    putchar('\\n');
}
int main(void) {
    if (fesetenv(FE_DFL_ENV) != 0 || fesetround(FE_TONEAREST) != 0) return 2;
''')
    if target == "intel":
        lines.append("_mm_setcsr(0x1f80);\n")
    for sample in inputs["samples"]:
        lines.append("{\n")
        arguments, memory = [], []
        for index, argument in enumerate(sample["arguments"]):
            type_name = argument["type"]
            raw = bytes.fromhex(argument["bytes"])
            initial = ",".join(str(value) for value in raw)
            lines.append(f"unsigned char buffer{index}[{len(raw)}] __attribute__((aligned(16))) = {{{initial}}};\n")
            if "*" in type_name:
                lines.append(f"{type_name} p{index} = buffer{index}+{argument['offset']};\n")
                memory.append(f"buffer{index}")
            else:
                lines.append(f"{type_name} p{index}; memcpy(&p{index},buffer{index},sizeof(p{index}));\n")
            arguments.append(f"p{index}")
        expression = f"{prefix}_{sample['operation']}({','.join(arguments)})"
        if sample["return_type"] == "void":
            lines.append(expression + ";\n")
            return_size = 0
        else:
            lines.append(f"{sample['return_type']} returned = {expression};\n")
            return_size = 16 if sample["return_type"] in TYPES else SCALAR_SIZES[sample["return_type"]]
        lines.append(f"unsigned char actual[{sample['output_bytes']}];\n")
        if return_size:
            lines.append("memcpy(actual,&returned,sizeof(returned));\n")
        for index, allocation in enumerate(memory):
            lines.append(f"memcpy(actual+{return_size+64*index},{allocation},64);\n")
        lines.append(f'emit("{sample["sample"]}",actual,sizeof(actual));\n}}\n')
    lines.append("return ferror(stdout) ? 3 : 0;\n}\n")
    return "".join(lines)


def parse_output(output, inputs):
    expected = {sample["sample"]: sample["output_bytes"] for sample in inputs["samples"]}
    rows = {}
    for line in output.splitlines():
        fields = line.split()
        if len(fields) != 2:
            raise ValueError("malformed native executable output")
        name, bits = fields
        if name in rows or name not in expected or not re.fullmatch(r"[0-9a-f]+", bits):
            raise ValueError(f"unexpected, duplicate or malformed native output: {name}")
        if len(bits) != expected[name] * 2:
            raise ValueError(f"incorrect native output byte count: {name}")
        rows[name] = bits
    if set(rows) != set(expected):
        raise ValueError(f"native executable emitted {len(rows)} outputs, expected {len(expected)}")
    return rows


def source_hashes():
    paths = [ROOT / "10_official_suite/shortcuts.h", Path(__file__).resolve()]
    for target in TARGETS:
        directory = ROOT / "10_official_suite" / target
        paths.extend(sorted(directory.glob("*.c")))
        paths.extend(sorted(directory.glob("*.h")))
    return {str(path.relative_to(ROOT)): digest(path.read_bytes()) for path in paths}


def command_run(command, log):
    with log.open("a") as stream:
        stream.write(json.dumps(command) + "\n")
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=300)
    except subprocess.TimeoutExpired as exc:
        with log.open("a") as stream:
            for part in (exc.stdout, exc.stderr):
                if part:
                    stream.write(part.decode(errors="replace") if isinstance(part, bytes) else part)
            stream.write("\nCommand timed out\n")
        raise
    with log.open("a") as stream:
        stream.write(result.stdout + result.stderr)
    if result.returncode:
        raise RuntimeError(f"command failed ({result.returncode}): {command!r}\n{result.stderr[-8000:]}")
    return result.stdout


def capture(args):
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    # Invalidate stale success metadata before validation, build or execution.
    (output / "manifest.json").unlink(missing_ok=True)
    architecture = platform.machine().lower()
    if architecture not in TARGETS[args.target][1] or sys.byteorder != "little":
        raise ValueError(f"{args.target} requires {TARGETS[args.target][1]} little-endian, found {architecture}/{sys.byteorder}")
    build = (args.build_dir or output / "build").resolve()
    build.mkdir(parents=True, exist_ok=True)
    log = output / "build.log"
    log.write_text("")
    functions = declarations()
    inputs = corpus(functions)
    corpus_bytes = encoded(inputs)
    (output / "corpus.json").write_bytes(corpus_bytes)
    generated = build / "calls.c"
    generated.write_text(source(functions, inputs, args.target))
    compiler = command_run([args.compiler, "--version"], log).splitlines()[0]
    if "clang" not in compiler.lower():
        raise ValueError("native wrapper capture currently requires Clang and its strict FP mode")
    flags = ["-O2", "-std=gnu11", "-ffp-model=strict", *TARGETS[args.target][2]]
    objects = []
    for block in BLOCKS:
        destination = build / f"{block}.o"
        command_run([args.compiler, *flags, "-c", str(ROOT / "10_official_suite" / args.target / f"{block}.c"),
                     "-o", str(destination)], log)
        objects.append(str(destination))
    executable = build / "native-wrapper-suite"
    command_run([args.compiler, *flags, str(generated), *objects, "-lm", "-o", str(executable)], log)
    raw = command_run([str(executable)], log)
    (output / "stdout.txt").write_text(raw)
    rows = parse_output(raw, inputs)
    results = b"".join(encoded({"sample": name, "bytes": rows[name]}) for name in sorted(rows))
    (output / "outputs.jsonl").write_bytes(results)
    manifest = {
        "schema_version": 1, "artifact_type": "equilane.native-wrapper-capture",
        "status": "captured", "target": args.target, "architecture": architecture,
        "captured_at": timestamp(),
        "operations": len(functions), "samples": len(rows), "compiler": compiler,
        "compile_flags": flags, "source_sha256": source_hashes(),
        "corpus_sha256": digest(corpus_bytes), "outputs_sha256": digest(results),
        "executable_sha256": digest(executable.read_bytes()),
        "environment": {"system": platform.system(), "release": platform.release(),
                        "execution_mode": os.environ.get("EQUILANE_EXECUTION_MODE", "unknown"),
                        "docker_image_id": os.environ.get("EQUILANE_IMAGE_ID", "unknown"),
                        "host_architecture": os.environ.get("EQUILANE_HOST_ARCH", "unknown"),
                        "docker_platform": os.environ.get("EQUILANE_DOCKER_PLATFORM", "unknown"),
                        "qemu_cpu": os.environ.get("QEMU_CPU", "")},
        "scope": {"returned_bytes": True, "pointer_buffer_bytes": 64,
                  "rounding": "nearest_even", "fp_exceptions": False,
                  "encoded_adapter_abi": False, "physical_hardware_evidence": False},
    }
    write_json(output / "manifest.json", manifest)
    print(json.dumps({"target": args.target, "status": "captured", "operations": len(functions), "samples": len(rows)}))
    return 0


def load_capture(directory):
    manifest = json.loads((directory / "manifest.json").read_bytes())
    if not isinstance(manifest, dict) or type(manifest.get("schema_version")) is not int or manifest.get("schema_version") != 1 or manifest.get("artifact_type") != "equilane.native-wrapper-capture":
        raise ValueError(f"invalid native capture manifest: {directory}")
    if manifest.get("status") != "captured" or manifest.get("target") not in TARGETS:
        raise ValueError(f"incomplete native capture: {directory}")
    hashes = manifest.get("source_sha256")
    if not isinstance(hashes, dict) or set(hashes) != set(source_hashes()) or any(
        not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None
        for value in hashes.values()
    ):
        raise ValueError(f"missing or invalid source provenance: {directory}")
    if manifest.get("architecture") not in TARGETS[manifest["target"]][1]:
        raise ValueError(f"capture architecture does not match target: {directory}")
    for key in ("corpus_sha256", "outputs_sha256", "executable_sha256"):
        if not isinstance(manifest.get(key), str) or re.fullmatch(r"[0-9a-f]{64}", manifest[key]) is None:
            raise ValueError(f"invalid {key}: {directory}")
    environment = manifest.get("environment")
    if not isinstance(environment, dict) or not isinstance(manifest.get("scope"), dict):
        raise ValueError(f"missing execution provenance: {directory}")
    if manifest["scope"].get("encoded_adapter_abi") is not False or manifest["scope"].get("fp_exceptions") is not False:
        raise ValueError(f"invalid wrapper evidence scope: {directory}")
    inputs_raw = (directory / "corpus.json").read_bytes()
    outputs_raw = (directory / "outputs.jsonl").read_bytes()
    if digest(inputs_raw) != manifest.get("corpus_sha256") or digest(outputs_raw) != manifest.get("outputs_sha256"):
        raise ValueError(f"native capture file hash mismatch: {directory}")
    inputs = json.loads(inputs_raw)
    expected = corpus(declarations())
    if inputs != expected or type(manifest.get("operations")) is not int or manifest.get("operations") != 254 or type(manifest.get("samples")) is not int or manifest.get("samples") != 762:
        raise ValueError(f"native capture does not contain the complete current corpus: {directory}")
    lines = []
    for raw in outputs_raw.splitlines():
        row = json.loads(raw)
        if not isinstance(row, dict) or set(row) != {"sample", "bytes"}:
            raise ValueError(f"malformed native capture row: {directory}")
        if not isinstance(row["sample"], str) or not isinstance(row["bytes"], str):
            raise ValueError(f"invalid native capture row types: {directory}")
        lines.append(row["sample"] + " " + row["bytes"])
    return manifest, parse_output("\n".join(lines), inputs)


def comparison_data(reference_directory, observed_directory):
    reference, expected = load_capture(reference_directory)
    observed, actual = load_capture(observed_directory)
    if reference["target"] != "intel" or observed["target"] == "intel":
        raise ValueError("comparison requires an Intel reference and a different observed target")
    if reference["corpus_sha256"] != observed["corpus_sha256"]:
        raise ValueError("native captures use different input corpora")
    if reference.get("source_sha256") != observed.get("source_sha256"):
        raise ValueError("native captures were produced from different source checkouts")
    differences = [{"sample": name, "expected_intel": expected[name], "actual": actual[name]}
                   for name in sorted(expected) if expected[name] != actual[name]]
    return {
        "schema_version": 1, "artifact_type": "equilane.native-wrapper-comparison",
        "status": "mismatch" if differences else "pass", "target": observed["target"],
        "operations": 254, "samples": len(expected), "matched": len(expected) - len(differences),
        "mismatch_count": len(differences), "mismatches": differences,
        "reference_manifest": reference, "observed_manifest": observed,
        "corpus_sha256": reference["corpus_sha256"],
        "physical_hardware_evidence": False,
    }


def compare(args):
    # Failed recaptures must never leave an older successful report visible.
    args.output.unlink(missing_ok=True)
    report = comparison_data(args.reference, args.observed)
    write_json(args.output, report)
    print(json.dumps({key: report[key] for key in ("target", "status", "samples", "matched", "mismatch_count")}))
    return 1 if report["mismatch_count"] else 0


def verify_native(args):
    """Build and test the architecture's encoded ABI as well as its wrappers."""
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    marker = output / "manifest.json"
    marker.unlink(missing_ok=True)
    log = output / "build.log"
    log.write_text("")
    build = output / "build"
    commands = [
        ["cmake", "-S", str(ROOT), "-B", str(build), "-G", "Ninja",
         "-DCMAKE_C_COMPILER=clang", "-DBUILD_TESTING=ON", "-DCMAKE_BUILD_TYPE=Release"],
        ["cmake", "--build", str(build), "--parallel", str(args.jobs)],
        ["ctest", "--test-dir", str(build), "--output-on-failure", "--output-junit", str(output / "ctest.xml")],
        [sys.executable, "-m", "unittest", "discover", "-s", str(ROOT / "tests"),
         "-p", "test_native_codegen.py", "-v"],
    ]
    report = {"artifact_type": "equilane.docker-native-tests", "schema_version": 1,
              "target": args.target, "created_at": timestamp(), "status": "error", "commands": commands}
    try:
        if platform.machine().lower() not in TARGETS[args.target][1]:
            raise ValueError("native tests are running on the wrong architecture")
        for command in commands:
            command_run(command, log)
        report["status"] = "pass"
    except (ValueError, RuntimeError, OSError, subprocess.TimeoutExpired) as exc:
        report["error"] = str(exc)
    write_json(marker, report)
    print(json.dumps({key: report[key] for key in ("target", "status")}))
    return 0 if report["status"] == "pass" else 2


def quality(args):
    """Keep known model findings distinct from execution/test failures."""
    from equilane.quality import run_quality_gates

    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    marker = output / "manifest.json"
    marker.unlink(missing_ok=True)
    run_directory = output / "runs" / uuid4().hex
    report = {"artifact_type": "equilane.docker-quality", "schema_version": 1,
              "created_at": timestamp(), "status": "error", "run_directory": str(run_directory)}
    try:
        result = run_quality_gates(ROOT, run_directory, jobs=args.jobs)
        summary = json.loads(result.report_path.read_bytes())
        gates = summary["gates"]
        python = gates["python_coverage"]
        findings = python["failure_classification"]
        errors = findings["test_execution_errors"] + findings["other_assertion_failures"]
        complete = python["tests_run"] > 0 and python["skipped"] == 0
        infrastructure_passed = all(gates[key]["status"] == "pass" for key in ("c_sanitizers", "cross_build"))
        report.update({"gates": gates, "tests_run": python["tests_run"], "skipped": python["skipped"],
                       "portable_model_mismatches": findings["portable_model_output_mismatches"],
                       "execution_and_assertion_errors": errors})
        if complete and infrastructure_passed and not errors:
            report["status"] = "findings" if report["portable_model_mismatches"] else "pass"
    except (ValueError, RuntimeError, OSError, KeyError, TypeError) as exc:
        report["error"] = str(exc)
    write_json(marker, report)
    print(json.dumps({key: value for key, value in report.items() if key != "gates"}))
    return 0 if report["status"] == "pass" else 1 if report["status"] == "findings" else 2


def _read_object(path):
    value = json.loads(path.read_bytes())
    if not isinstance(value, dict):
        raise ValueError(f"expected a JSON object: {path}")
    return value


def authenticate_canonical_comparison(directory, target, cache):
    """Authenticate native artifacts and recompute summary counts without C execution."""
    from equilane.artifacts import validate_input_artifact, validate_result_artifact
    from equilane.cases import load_case_definitions
    from equilane.compare import compare_result_record_set
    from equilane.errors import EquiLaneError
    from equilane.isa import load_isa_registry
    from equilane.native_traceability import validate_traceability
    from equilane.project import load_project

    try:
        if "context" not in cache:
            project = load_project(ROOT / "equilane.toml")
            isa = load_isa_registry(project.isa_registry)
            cases = load_case_definitions(project.suite, isa_registry=isa)
            inputs = validate_input_artifact(directory / "vectors", cases, isa)
            cache["context"] = (cases, isa, inputs)
        cases, isa, inputs = cache["context"]
        for role_directory, role in (("intel", "intel"), (target, "riscv" if target == "riscv-vlen256" else target)):
            if role_directory not in cache:
                output = directory / "native" / role_directory
                validate_traceability(output, input_artifact=inputs, cases=cases, isa_registry=isa, role=role)
                cache[role_directory] = validate_result_artifact(output, cases, isa, input_artifact=inputs)
                if cache[role_directory].development_fixture or cache[role_directory].role != role:
                    raise ValueError("canonical artifact is not the requested native execution")
        comparisons = compare_result_record_set(
            cases=cases, input_records=inputs.records,
            intel_records=cache["intel"].records, openpower_records=cache[target].records,
            target_role="riscv" if target == "riscv-vlen256" else target,
        )
        return {
            "record_count": len(inputs.records),
            "matched_inputs": sum(result.outcome == "match" for result in comparisons),
            "mismatched_inputs": sum(result.outcome == "mismatch" for result in comparisons),
            "not_comparable_inputs": sum(result.outcome == "not_comparable" for result in comparisons),
            "mismatch_atoms": sum(result.mismatch_count for result in comparisons if result.outcome == "mismatch"),
        }
    except EquiLaneError as exc:
        raise ValueError(f"canonical native evidence authentication failed: {exc}") from exc


def report_pipeline(args):
    """Revalidate captures and render the complete run, including failed stages."""
    directory = args.directory.resolve()
    report = {"artifact_type": "equilane.docker-pipeline", "schema_version": 1,
              "created_at": timestamp(), "status": "pass", "wrapper_comparisons": [],
              "canonical_comparisons": [], "native_tests": [], "launcher_errors": [],
              "physical_hardware_evidence": False}
    code = 0
    for target in ("openpower", "arm", "riscv"):
        try:
            comparison = comparison_data(directory / "intel", directory / target)
            if comparison["target"] != target:
                raise ValueError("capture role does not match directory")
            write_json(directory / f"compare-{target}.json", comparison)
            report["wrapper_comparisons"].append(comparison)
            code = max(code, 1 if comparison["mismatch_count"] else 0)
        except (ValueError, OSError, KeyError, TypeError) as exc:
            report["wrapper_comparisons"].append({"target": target, "status": "error", "error": str(exc)})
            code = 2
    if args.canonical:
        canonical_cache = {}
        canonical_targets = ["openpower", "arm", "riscv"]
        if getattr(args, "vlen256", False):
            canonical_targets.append("riscv-vlen256")
        for target in canonical_targets:
            try:
                comparison = _read_object(directory / "canonical" / f"compare-{target}" / "summary.json")
                if comparison.get("artifact_type") != "ioitf.comparison-summary":
                    raise ValueError("invalid canonical comparison summary")
                counts = [comparison.get(key) for key in ("record_count", "matched_inputs", "mismatched_inputs", "not_comparable_inputs")]
                if any(type(value) is not int or value < 0 for value in counts) or counts[0] == 0 or sum(counts[1:]) != counts[0]:
                    raise ValueError("invalid canonical comparison counts")
                authenticated = authenticate_canonical_comparison(directory, target, canonical_cache)
                if any(type(comparison.get(key)) is not int or comparison[key] != value for key, value in authenticated.items()):
                    raise ValueError("canonical summary counts differ from authenticated native results")
                outcome = "not_comparable" if counts[3] else "mismatch" if counts[2] else "pass"
                if comparison.get("outcome") != outcome:
                    raise ValueError("canonical summary verdict differs from authenticated results")
                report["canonical_comparisons"].append({**comparison, "target": target})
                code = max(code, 2 if counts[3] else 1 if counts[2] else 0)
            except (ValueError, OSError, KeyError, TypeError, AttributeError, ImportError) as exc:
                report["canonical_comparisons"].append({"target": target, "status": "error", "error": str(exc)})
                code = 2
    if getattr(args, "vlen256", False):
        try:
            wide_manifest, wide_rows = load_capture(directory / "riscv-vlen256")
            narrow_manifest, narrow_rows = load_capture(directory / "riscv")
            if "vlen=256" not in wide_manifest["environment"].get("qemu_cpu", ""):
                raise ValueError("VLEN 256 was not configured for the wide-register run")
            if wide_manifest["corpus_sha256"] != narrow_manifest["corpus_sha256"] or wide_manifest["source_sha256"] != narrow_manifest["source_sha256"]:
                raise ValueError("RVV width runs use different sources or corpora")
            differences = [{"sample": name, "vlen128": narrow_rows[name], "vlen256": wide_rows[name]}
                           for name in sorted(narrow_rows) if narrow_rows[name] != wide_rows[name]]
            report["rvv_width_check"] = {"status": "mismatch" if differences else "pass", "samples": len(wide_rows),
                                         "mismatches": differences, "vlen256_manifest": wide_manifest}
            code = max(code, 1 if differences else 0)
        except (ValueError, OSError, KeyError, TypeError) as exc:
            report["rvv_width_check"] = {"status": "error", "error": str(exc)}
            code = 2
    if args.tests:
        for target in TARGETS:
            try:
                gate = _read_object(directory / "native-tests" / target / "manifest.json")
                if gate.get("artifact_type") != "equilane.docker-native-tests" or gate.get("target") != target or gate.get("status") != "pass":
                    code = 2
                report["native_tests"].append(gate)
            except (ValueError, OSError) as exc:
                report["native_tests"].append({"target": target, "status": "error", "error": str(exc)})
                code = 2
        try:
            gate = _read_object(directory / "quality" / "manifest.json")
            report["quality"] = gate
            if gate.get("artifact_type") != "equilane.docker-quality" or gate.get("status") not in ("pass", "findings"):
                code = 2
            else:
                code = max(code, 1 if gate["status"] == "findings" else 0)
        except (ValueError, OSError) as exc:
            report["quality"] = {"status": "error", "error": str(exc)}
            code = 2
    for marker in sorted((directory / "launcher-errors").glob("*.json")):
        try:
            report["launcher_errors"].append(_read_object(marker))
        except (ValueError, OSError) as exc:
            report["launcher_errors"].append({"status": "error", "error": str(exc)})
        code = 2
    report["exit_code"] = code
    report["status"] = "error" if code == 2 else "findings" if code else "pass"
    write_json(directory / "pipeline-summary.json", report)
    escape = lambda value: html.escape(str(value), quote=True)
    parts = ['<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>EquiLane Intel Intrinsics port conformance</title>',
             '<style>body{font:16px system-ui;margin:2rem auto;max-width:1200px;padding:0 1rem;background:#f8f6fb;color:#2e2938}table{border-collapse:collapse;width:100%;background:white}th,td{padding:.55rem;border:1px solid #ddd;text-align:left}code{word-break:break-all}details{margin:1rem 0}pre{white-space:pre-wrap;overflow-wrap:anywhere}.error{color:#a11933}</style>',
             '<h1>Intel Intrinsics port conformance</h1>', f'<p>Status: <strong>{escape(report["status"])}</strong> · {escape(report["created_at"])}</p>',
             '<p>Ubuntu 26.04 LTS. Actual C implementations are checked against Intel execution to establish whether each port meets its declared case contract. A disallowed mismatch is a conformance failure requiring investigation. Explicitly allowed differences follow the case comparison rules. Emulated runs remain emulator observations.</p>',
             '<p><a href="pipeline-summary.json">Machine-readable run summary</a></p><h2>Wrapper byte comparisons</h2>']
    for comparison in report["wrapper_comparisons"]:
        target = comparison["target"]
        parts.append(f'<h3>{escape(target)}</h3>')
        if comparison["status"] == "error":
            parts.append(f'<p class="error">{escape(comparison["error"])}</p>')
            continue
        observed = comparison["observed_manifest"]
        parts.append(f'<p>{comparison["matched"]} matched / {comparison["samples"]} samples; {comparison["mismatch_count"]} differences. Mode: {escape(observed["environment"].get("execution_mode", "unknown"))}. CPU: {escape(observed["environment"].get("qemu_cpu", ""))}.</p>')
        parts.append(f'<p><a href="compare-{target}.json">All comparison evidence</a> · <a href="{target}/corpus.json">Exact inputs</a> · <a href="{target}/manifest.json">Build and execution provenance</a></p>')
        parts.append('<details><summary>Bit differences</summary><table><tr><th>Sample</th><th>Intel bytes</th><th>Observed bytes</th></tr>')
        for difference in comparison["mismatches"]:
            parts.append('<tr>' + ''.join(f'<td><code>{escape(difference[key])}</code></td>' for key in ("sample", "expected_intel", "actual")) + '</tr>')
        parts.append('</table></details>')
    if args.canonical:
        parts.append('<h2>Encoded C ABI comparisons</h2>')
        for comparison in report["canonical_comparisons"]:
            target = comparison["target"]
            if comparison.get("status") == "error":
                parts.append(f'<p class="error">{escape(target)}: {escape(comparison["error"])}</p>')
            else:
                parts.append(f'<p>{escape(target)}: {comparison["matched_inputs"]} matched / {comparison["record_count"]}; {comparison["mismatched_inputs"]} mismatched; {comparison["not_comparable_inputs"]} not comparable. <a href="canonical/compare-{target}/showcase.html">Searchable case report</a> · <a href="canonical/compare-{target}/summary.json">Summary and failure bundle paths</a> · <a href="canonical/compare-{target}/junit.xml">JUnit</a></p>')
    if args.tests:
        parts.append('<h2>Regression and native ABI gates</h2>')
        for gate in [*report["native_tests"], report.get("quality", {})]:
            parts.append(f'<details><summary>{escape(gate.get("target", "Python / sanitizer / cross-build"))}: {escape(gate.get("status", "error"))}</summary><pre>{escape(json.dumps(gate, indent=2))}</pre></details>')
    if "rvv_width_check" in report:
        parts.append(f'<h2>RVV register width check</h2><pre>{escape(json.dumps(report["rvv_width_check"], indent=2))}</pre>')
    for error in report["launcher_errors"]:
        parts.append(f'<pre class="error">{escape(json.dumps(error, indent=2))}</pre>')
    parts.append('</html>')
    html_path = args.output or directory / "pipeline-report.html"
    html_path.parent.mkdir(parents=True, exist_ok=True)
    html_path.write_text("\n".join(parts), encoding="utf-8")
    print(json.dumps({"status": report["status"], "report": str(html_path), "exit_code": code}))
    return code


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    capture_parser = subparsers.add_parser("capture", help="compile and execute the actual target C wrappers")
    capture_parser.add_argument("--target", choices=tuple(TARGETS), required=True)
    capture_parser.add_argument("--output", type=Path, required=True)
    capture_parser.add_argument("--compiler", default="clang")
    capture_parser.add_argument("--build-dir", type=Path)
    capture_parser.set_defaults(handler=capture)
    compare_parser = subparsers.add_parser("compare", help="compare a captured target with captured Intel bytes")
    compare_parser.add_argument("--reference", type=Path, required=True)
    compare_parser.add_argument("--observed", type=Path, required=True)
    compare_parser.add_argument("--output", type=Path, required=True)
    compare_parser.set_defaults(handler=compare)
    verify_parser = subparsers.add_parser("verify-native", help="run CMake and native CTest gates")
    verify_parser.add_argument("--target", choices=tuple(TARGETS), required=True)
    verify_parser.add_argument("--output", type=Path, required=True)
    verify_parser.add_argument("--jobs", type=int, default=2)
    verify_parser.set_defaults(handler=verify_native)
    quality_parser = subparsers.add_parser("quality", help="run Python, browser, sanitizer and cross-build gates")
    quality_parser.add_argument("--output", type=Path, required=True)
    quality_parser.add_argument("--jobs", type=int, default=2)
    quality_parser.set_defaults(handler=quality)
    report_parser = subparsers.add_parser("report", help="render a complete pipeline report, retaining failed stages")
    report_parser.add_argument("--directory", type=Path, required=True)
    report_parser.add_argument("--output", type=Path)
    report_parser.add_argument("--canonical", action="store_true")
    report_parser.add_argument("--tests", action="store_true")
    report_parser.add_argument("--vlen256", action="store_true")
    report_parser.set_defaults(handler=report_pipeline)
    args = parser.parse_args(argv)
    try:
        return args.handler(args)
    except (ValueError, RuntimeError, OSError, subprocess.TimeoutExpired, KeyError, TypeError) as exc:
        print(f"native suite error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
