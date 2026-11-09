"""Isolated execution of separately identified portable intrinsic backends.

This backend does not extend or impersonate the canonical case ABI. Candidates,
compilation, execution, and exact-byte agreement are distinct observations.
Intel references always call actual Intel intrinsics behind CPU-feature guards.
"""
from __future__ import annotations

from collections import Counter, defaultdict
import ctypes
from html import escape
from importlib import import_module
import json
import math
import os
from pathlib import Path
import platform
import re
import signal
import shutil
import subprocess
import sys
import time
from typing import Any

from .canonical import atomic_write, dump_bytes, loads, sha256_bytes, sha256_file, write_jsonl
from .simde_codegen import VENDOR


ROLES = {"intel": {"x86_64", "amd64"}, "arm": {"aarch64", "arm64"},
         "openpower": {"ppc64le", "ppc64el"}, "riscv": {"riscv64"}}
RESULT_STATUSES = {"ok", "unknown_operation", "invalid_buffer", "unsupported_cpu", "fp_environment_error",
                   "compile_error", "runtime_error", "signal", "timeout"}
ABI_STATUSES = {0: "ok", 1: "unknown_operation", 2: "invalid_buffer", 3: "unsupported_cpu", 4: "fp_environment_error"}
_SCOPE = ("Separate SIMDe pure-value backend. Exact return bytes are compared with actual Intel intrinsics "
          "only when both executions succeed. Floating-point flags are recorded as diagnostics. "
          "Catalog membership, compilation, and unsupported CPU features are not proof of conformance.")
_HEX = re.compile(r"0x(?:[0-9a-f]{2})*\Z")
_SHA = re.compile(r"[0-9a-f]{64}\Z")
_BACKENDS = {
    "equilane.simde-pure-value-candidates": ("simde-pure-value", "equilane.simde_codegen"),
    "equilane.softfloat-fp16-candidates": ("softfloat-fp16", "equilane.half_codegen"),
    "equilane.simde-masked-candidates": ("simde-masked", "equilane.simde_masked"),
}


def _backend(catalog):
    definition = _BACKENDS.get(catalog.get("artifact_type"))
    if definition is None:
        raise ValueError("unsupported extended implementation backend")
    return definition[0], import_module(definition[1])


def _scope(catalog):
    backend, _module = _backend(catalog)
    return (f"Separate {backend} backend. Exact return bytes are compared with actual Intel intrinsics "
            "only when both executions succeed. Floating-point flags are recorded as diagnostics. "
            "Catalog membership, compilation, and unsupported CPU features are not proof of conformance.")


def _generated_source(catalog, names, *, role):
    _name, module = _backend(catalog)
    return module.generate_source(catalog, names, role=role)


def _catalog_identity(catalog):
    try:
        return _backend(catalog)[1].validate_catalog(catalog)
    except (KeyError, TypeError) as exc:
        raise ValueError("invalid or outdated extended catalog schema") from exc


def _fresh(directory):
    directory = Path(directory).resolve()
    if directory.exists() and any(directory.iterdir()):
        raise ValueError("extended output directory must be empty")
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def _load(path):
    value = loads(Path(path).read_text(encoding="utf-8"), source=str(path))
    if not isinstance(value, dict):
        raise ValueError("extended artifact requires an object")
    return value


def _integer(value, *, minimum=0):
    if type(value) is not int or value < minimum:
        raise ValueError("invalid extended integer")
    return value


def _hash(value):
    if not isinstance(value, str) or not _SHA.fullmatch(value):
        raise ValueError("invalid extended hash")
    return value


def _fixed(root, name):
    if name not in {"inputs.jsonl", "results.jsonl", "catalog.json"}:
        raise ValueError("extended artifact path is not allowlisted")
    path = root / name
    if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
        raise ValueError("extended artifact must not follow symlinks")
    return path


def _manifest_path(value, kind):
    path = Path(value).resolve()
    return path / f"{kind}.manifest.json" if path.is_dir() else path


def _stream(path):
    with Path(path).open("rb") as stream:
        for raw in stream:
            if len(raw) > 8 * 1024 * 1024 or not raw.endswith(b"\n"):
                raise ValueError("invalid extended JSONL line")
            value = loads(raw.decode("utf-8"), source=str(path))
            if not isinstance(value, dict) or dump_bytes(value, newline=True) != raw:
                raise ValueError("extended records must use canonical JSONL")
            yield value


def _payload_id(name, encoded):
    return sha256_bytes(dump_bytes({"name": name, "input": encoded, "rounding": "nearest_even"}))


def _record_check(row, record, sequence):
    if set(row) != {"schema_version", "name", "ordinal", "sequence", "generation", "input_id", "input", "rounding"}:
        raise ValueError("invalid extended input fields")
    if (row["schema_version"] != 1 or type(row["schema_version"]) is not int
            or type(row["sequence"]) is not int or row["sequence"] != sequence
            or type(row["ordinal"]) is not int):
        raise ValueError("invalid extended input version or order")
    if row["name"] != record["name"] or row["ordinal"] != record["ordinal"] or row["rounding"] != "nearest_even":
        raise ValueError("extended input does not match its catalog entry")
    if row["generation"] not in {"boundary", "random"}:
        raise ValueError("invalid extended generation class")
    value = row["input"]
    if not isinstance(value, str) or not _HEX.fullmatch(value) or len(value) != record["input_byte_length"] * 2 + 2:
        raise ValueError("invalid extended input byte length")
    if row["input_id"] != _payload_id(row["name"], value):
        raise ValueError("extended input identity mismatch")


def _verified_input(value):
    path = _manifest_path(value, "inputs")
    manifest = _load(path)
    if manifest.get("artifact_type") != "equilane.extended-inputs" or manifest.get("schema_version") != 1 or manifest.get("complete") is not True:
        raise ValueError("not a completed extended input artifact")
    root = path.parent
    catalog_file = _fixed(root, "catalog.json")
    if sha256_file(catalog_file) != manifest["catalog"]["sha256"]:
        raise ValueError("extended catalog file hash changed")
    catalog = _load(catalog_file)
    identity = _catalog_identity(catalog)
    if identity != manifest["source_identity"] or manifest.get("backend") != _backend(catalog)[0]:
        raise ValueError("extended catalog or vendored source identity changed")
    records = {record["name"]: record for record in catalog["intrinsics"]}
    names = manifest["names"]
    if not isinstance(names, list) or not names or len(names) != len(set(names)) or any(name not in records for name in names):
        raise ValueError("invalid extended selection")
    metadata = manifest["inputs"]
    if metadata.get("file") != "inputs.jsonl":
        raise ValueError("invalid extended input path")
    stream = _fixed(root, metadata["file"])
    if stream.stat().st_size != metadata["byte_length"] or sha256_file(stream) != metadata["sha256"]:
        raise ValueError("extended input stream hash changed")
    grouped = {name: [] for name in names}
    seen = set()
    count = 0
    for count, row in enumerate(_stream(stream), 1):
        name = row.get("name")
        if name not in grouped:
            raise ValueError("input contains an unselected candidate")
        _record_check(row, records[name], count)
        if row["input_id"] in seen:
            raise ValueError("duplicate extended input identity")
        seen.add(row["input_id"])
        grouped[name].append(row)
    if (count != metadata["record_count"] or any(not rows for rows in grouped.values())
            or manifest["candidate_count"] != len(grouped)
            or manifest["input_counts"] != {name: len(rows) for name, rows in grouped.items()}):
        raise ValueError("extended input count mismatch")
    return path, manifest, catalog, grouped


def _splitmix(state):
    mask = (1 << 64) - 1
    state = (state + 0x9E3779B97F4A7C15) & mask
    value = state
    value = ((value ^ (value >> 30)) * 0xBF58476D1CE4E5B9) & mask
    value = ((value ^ (value >> 27)) * 0x94D049BB133111EB) & mask
    return state, value ^ (value >> 31)


def _boundaries(argument):
    element = str(argument.get("element_type") or "")
    match = re.search(r"(8|16|32|64)$", element)
    width = int(match[1]) if match else 8
    width = min(width, argument["byte_length"] * 8)
    if element.startswith("FP") and width in {16, 32, 64}:
        fraction, exponent = {16: (10, 5), 32: (23, 8), 64: (52, 11)}[width]
        infinity = ((1 << exponent) - 1) << fraction
        one = ((1 << (exponent - 1)) - 1) << fraction
        values = (0, 1, 2, (1 << fraction) - 1, 1 << fraction, one - 1, one, one + 1,
                  infinity - 1, infinity, infinity | 1, infinity | (1 << (fraction - 1)) | 0x15)
        return width, tuple(word | sign for word in values for sign in (0, 1 << (width - 1)))
    maximum, sign = (1 << width) - 1, 1 << (width - 1)
    return width, (0, 1, 2, sign - 1, sign, sign + 1, maximum - 1, maximum,
                   int.from_bytes(b"\x55" * (width // 8), "little"), int.from_bytes(b"\xaa" * (width // 8), "little"))


def _inputs(record, count, seed):
    size = record["input_byte_length"]
    if size == 0:
        yield "boundary", b""
        return
    seen = set()
    candidates = [("boundary", bytes([value]) * size) for value in (0, 255, 85, 170)]
    for rotation in range(24):
        buffer = bytearray(size)
        for argument_index, argument in enumerate(record["arguments"]):
            width, values = _boundaries(argument)
            step = width // 8
            for lane in range(argument["byte_length"] // step):
                word = values[(rotation + lane * 3 + argument_index * 7) % len(values)]
                start = argument["offset"] + lane * step
                buffer[start:start + step] = word.to_bytes(step, "little")
        candidates.append(("boundary", bytes(buffer)))
    for bit in range(min(size * 8, 128)):
        buffer = bytearray(size)
        buffer[bit // 8] = 1 << (bit % 8)
        candidates.append(("boundary", bytes(buffer)))
    for kind, raw in candidates:
        if raw not in seen:
            seen.add(raw)
            yield kind, raw
            if len(seen) == count:
                return
    state = int(sha256_bytes((seed + record["name"]).encode())[:16], 16)
    domain_size = 1 << min(size * 8, 20)
    while len(seen) < min(count, domain_size):
        chunks = []
        for _ in range(math.ceil(size / 8)):
            state, value = _splitmix(state)
            chunks.append(value.to_bytes(8, "little"))
        raw = b"".join(chunks)[:size]
        if raw not in seen:
            seen.add(raw)
            yield "random", raw


def generate_extended_artifact(catalog_path, output, *, names=None, count_per_case=32, seed="0x6a09e667f3bcc909"):
    _integer(count_per_case, minimum=1)
    if count_per_case > 100000 or not isinstance(seed, str) or not re.fullmatch(r"0x[0-9a-f]{16}", seed):
        raise ValueError("invalid extended count or seed")
    catalog = _load(catalog_path)
    identity = _catalog_identity(catalog)
    indexed = {record["name"]: record for record in catalog["intrinsics"]}
    selected = sorted(indexed) if names is None else list(names)
    if not selected or len(selected) != len(set(selected)) or any(name not in indexed for name in selected):
        raise ValueError("extended names must be nonempty, unique catalog candidates")
    directory = _fresh(output)
    atomic_write(directory / "catalog.json", dump_bytes(catalog, newline=True))
    counts = Counter()
    def rows():
        sequence = 0
        for name in selected:
            record = indexed[name]
            for generation, raw in _inputs(record, count_per_case, seed):
                sequence += 1
                counts[name] += 1
                encoded = "0x" + raw.hex()
                yield {"schema_version": 1, "sequence": sequence, "name": name, "ordinal": record["ordinal"],
                       "generation": generation, "rounding": "nearest_even", "input": encoded,
                       "input_id": _payload_id(name, encoded)}
    count, length, digest = write_jsonl(directory / "inputs.jsonl", rows())
    manifest = {"schema_version": 1, "artifact_type": "equilane.extended-inputs", "complete": True,
                "backend": _backend(catalog)[0], "scope": _scope(catalog), "seed": seed, "requested_inputs_per_case": count_per_case,
                "candidate_count": len(selected), "names": selected, "input_counts": dict(counts),
                "source_identity": identity, "catalog": {"file": "catalog.json", "sha256": sha256_file(directory / "catalog.json")},
                "inputs": {"file": "inputs.jsonl", "record_count": count, "byte_length": length, "sha256": digest}}
    path = directory / "inputs.manifest.json"
    atomic_write(path, dump_bytes(manifest, newline=True))
    return {"manifest": str(path), "candidate_count": len(selected), "record_count": count, "status": "pass", "exit_code": 0}


def _library_architecture(path, role):
    with Path(path).open("rb") as stream:
        header = stream.read(20)
    expected = {"intel": 62, "arm": 183, "openpower": 21, "riscv": 243}[role]
    if (len(header) != 20 or header[:4] != b"\x7fELF" or header[4:6] != b"\x02\x01"
            or int.from_bytes(header[18:20], "little") != expected):
        raise ValueError("extended library is not little-endian ELF64 for its declared CPU")


def _compile(catalog, names, directory, role, compiler, attempts, extra_flags=()):
    unit_id = f"unit-{len(attempts):05d}"
    source = directory / (unit_id + ".c")
    library = directory / (unit_id + ".so")
    code = _generated_source(catalog, names, role="intel" if role == "intel" else "portable")
    atomic_write(source, code.encode())
    command = [compiler, "-std=c11", "-O2", "-shared", "-fPIC", "-fno-fast-math", "-frounding-math", "-fno-strict-aliasing",
               "-I", str(VENDOR), str(source), "-o", str(library), *extra_flags, "-lm"]
    if role == "openpower":
        command[1:1] = ["-maltivec", "-mvsx", "-mcpu=power8"]
    try:
        result = subprocess.run(command, text=True, capture_output=True, timeout=180)
        log, passed = result.stdout + result.stderr, result.returncode == 0
        if passed:
            try:
                _library_architecture(library, role)
            except (OSError, ValueError) as exc:
                log, passed = log + "\n" + str(exc), False
    except (OSError, subprocess.TimeoutExpired) as exc:
        log, passed = str(exc), False
    atomic_write(directory / (unit_id + ".log"), log.encode())
    evidence = {"unit": unit_id, "names": names, "source": source.name, "source_sha256": sha256_file(source),
                "status": "compiled" if passed else "compile_error", "command": command, "log": unit_id + ".log"}
    if passed:
        evidence.update(library=library.name, library_sha256=sha256_file(library))
    attempts.append(evidence)
    if passed or len(names) == 1:
        return {name: evidence for name in names}
    midpoint = len(names) // 2
    return {**_compile(catalog, names[:midpoint], directory, role, compiler, attempts, extra_flags),
            **_compile(catalog, names[midpoint:], directory, role, compiler, attempts, extra_flags)}


def _worker_main():
    import resource
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    signal.signal(signal.SIGALRM, signal.SIG_DFL)
    request = json.load(sys.stdin)
    library = ctypes.CDLL(request["library"])
    call = library.equilane_ext_call_with_flags
    call.argtypes = (ctypes.c_uint32, ctypes.POINTER(ctypes.c_ubyte), ctypes.c_uint64,
                     ctypes.POINTER(ctypes.c_ubyte), ctypes.c_uint64, ctypes.POINTER(ctypes.c_uint32))
    call.restype = ctypes.c_int
    for item in request["rows"]:
        raw = bytes.fromhex(item["input"][2:])
        source = (ctypes.c_ubyte * max(1, len(raw)))()
        if raw:
            ctypes.memmove(source, raw, len(raw))
        output = (ctypes.c_ubyte * request["output_size"])()
        flags = ctypes.c_uint32()
        signal.setitimer(signal.ITIMER_REAL, request["timeout"])
        started = time.monotonic_ns()
        status = call(request["ordinal"], source, len(raw), output, len(output), ctypes.byref(flags))
        duration = time.monotonic_ns() - started
        signal.setitimer(signal.ITIMER_REAL, 0)
        record = {"input_id": item["input_id"], "status": ABI_STATUSES.get(status, "runtime_error"), "duration_ns": duration}
        if status == 0:
            record.update(output="0x" + bytes(output).hex(), fp_flags=flags.value)
        else:
            record["error_code"] = f"abi_status_{status}"
        print(json.dumps(record, separators=(",", ":")), flush=True)


def _execute_case(record, rows, evidence, directory, timeout):
    if evidence["status"] != "compiled":
        return [{"input_id": row["input_id"], "status": "compile_error", "duration_ns": 0,
                 "error_code": "compiler_failed"} for row in rows]
    library = directory / evidence["library"]
    if sha256_file(library) != evidence["library_sha256"]:
        raise ValueError("extended library changed before execution")
    request = {"library": str(library), "ordinal": record["ordinal"], "output_size": record["output_byte_length"],
               "rows": rows, "timeout": timeout}
    try:
        process = subprocess.run([sys.executable, "-m", "equilane.extended", "_worker"], input=json.dumps(request),
                                 text=True, capture_output=True, timeout=max(5.0, timeout * (len(rows) + 1)))
        output, code = process.stdout, process.returncode
        stderr = process.stderr
    except subprocess.TimeoutExpired as exc:
        output = exc.stdout or ""
        output = output.decode() if isinstance(output, bytes) else output
        code, stderr = -signal.SIGALRM, "worker deadline exceeded"
    atomic_write(directory / (f"case-{record['ordinal']:05d}.log"), stderr.encode())
    results = []
    for line in output.splitlines():
        try:
            result = json.loads(line)
        except ValueError:
            break
        if len(results) >= len(rows) or result.get("input_id") != rows[len(results)]["input_id"]:
            raise ValueError("extended worker returned an unexpected input identity")
        results.append(result)
    if len(results) == len(rows) and code != 0:
        results[-1] = {"input_id": rows[-1]["input_id"], "status": "signal" if code < 0 else "runtime_error",
                       "duration_ns": 0, "error_code": f"worker_exit_{code}"}
    if len(results) < len(rows):
        status = "timeout" if code == -signal.SIGALRM else "signal" if code < 0 else "runtime_error"
        error = f"worker_signal_{-code}" if code < 0 else f"worker_exit_{code}"
        results.extend({"input_id": row["input_id"], "status": status, "duration_ns": 0, "error_code": error}
                       for row in rows[len(results):])
    if len(results) != len(rows):
        raise ValueError("extended worker result count mismatch")
    return results


def _build_dependencies(catalog, build, role, compiler):
    backend, _module = _backend(catalog)
    if backend != "softfloat-fp16" or role == "intel":
        return [], []
    from .softfloat import VENDOR as softfloat_vendor, build_softfloat
    library = build_softfloat(build / "softfloat", compiler=compiler)
    _library_architecture(library, role)
    evidence = library.parent / "build-evidence.json"
    dependency = {"file": str(library.relative_to(build)), "sha256": sha256_file(library),
                  "evidence": str(evidence.relative_to(build)), "evidence_sha256": sha256_file(evidence)}
    flags = ["-I", str(softfloat_vendor / "source/include"), "-DTHREAD_LOCAL=_Thread_local",
             "-DSOFTFLOAT_FAST_INT64", "-L", str(library.parent), "-lequilane-softfloat",
             "-Wl,-rpath,$ORIGIN/softfloat"]
    return flags, [dependency]


def run_extended_native(input_manifest, output, *, role, compiler="clang", timeout=5.0):
    if role not in ROLES or platform.system() != "Linux" or platform.machine().lower() not in ROLES[role]:
        raise ValueError("extended native execution requires Linux on the requested architecture")
    if not isinstance(timeout, (int, float)) or isinstance(timeout, bool) or not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("extended timeout must be positive and finite")
    input_path, inputs, catalog, grouped = _verified_input(input_manifest)
    directory = _fresh(output)
    build = directory / "build"
    build.mkdir()
    shutil.copyfile(input_path.parent / "catalog.json", directory / "catalog.json")
    shutil.copyfile(input_path, directory / "inputs.manifest.json")
    shutil.copyfile(input_path.parent / "inputs.jsonl", directory / "inputs.jsonl")
    try:
        compiler_version = subprocess.run([compiler, "--version"], text=True, capture_output=True, check=True, timeout=10).stdout.splitlines()[0]
    except (OSError, subprocess.SubprocessError, IndexError) as exc:
        raise ValueError("extended compiler is unavailable") from exc
    extra_flags, dependencies = _build_dependencies(catalog, build, role, compiler)
    indexed = {record["name"]: record for record in catalog["intrinsics"]}
    groups = defaultdict(list)
    for name in inputs["names"]:
        record = indexed[name]
        groups[(tuple(record["intel_target_features"]), record.get("simde_header", "portable"))].append(name)
    attempts, compiled = [], {}
    for names in groups.values():
        for start in range(0, len(names), 64):
            compiled.update(_compile(catalog, names[start:start + 64], build, role, compiler, attempts, extra_flags))
    counts = Counter()
    executed_names, successful_names = set(), set()
    def result_rows():
        for name in inputs["names"]:
            evidence = compiled[name]
            record = indexed[name]
            for input_row, row in zip(grouped[name], _execute_case(record, grouped[name], evidence, build, timeout)):
                counts[row["status"]] += 1
                if row["status"] != "compile_error":
                    executed_names.add(name)
                if row["status"] == "ok":
                    successful_names.add(name)
                yield {"schema_version": 1, "name": name, "ordinal": record["ordinal"], "runner": role,
                       "build_unit": evidence["unit"], "input": input_row["input"], "rounding": input_row["rounding"], **row}
    count, length, digest = write_jsonl(directory / "results.jsonl", result_rows())
    build_evidence = {"artifact_type": "equilane.extended-build", "schema_version": 1,
                      "source_identity": inputs["source_identity"], "compiler_version": compiler_version,
                      "role": role, "units": attempts, "dependencies": dependencies}
    atomic_write(directory / "build-evidence.json", dump_bytes(build_evidence, newline=True))
    status = "pass" if counts["ok"] == count else "findings"
    manifest = {"artifact_type": "equilane.extended-results", "schema_version": 1, "complete": True,
                "status": status, "backend": "intel-intrinsics" if role == "intel" else inputs["backend"],
                "implementation_backend": inputs["backend"], "scope": _scope(catalog), "runner": role, "physical_hardware_evidence": False,
                "execution_environment": {"system": platform.system(), "machine": platform.machine(),
                                          "kind": "linux_process_observation"},
                "input_manifest_sha256": sha256_file(input_path), "input_sha256": inputs["inputs"]["sha256"],
                "source_identity": inputs["source_identity"], "names": inputs["names"],
                "catalog": inputs["catalog"], "build_evidence": {"file": "build-evidence.json", "sha256": sha256_file(directory / "build-evidence.json")},
                "candidate_count": len(inputs["names"]),
                "compiled_candidates": sum(e["status"] == "compiled" for e in compiled.values()),
                "invoked_candidates": len(executed_names), "executed_candidates": len(successful_names),
                "status_counts": dict(counts), "results": {"file": "results.jsonl", "record_count": count, "byte_length": length, "sha256": digest}}
    path = directory / "results.manifest.json"
    atomic_write(path, dump_bytes(manifest, newline=True))
    return {"manifest": str(path), "record_count": count, "candidate_count": len(inputs["names"]),
            "compiled_candidates": manifest["compiled_candidates"], "executed_candidates": len(successful_names),
            "status_counts": dict(counts), "status": status, "exit_code": 0 if status == "pass" else 1}


def _verified_result(value):
    path = _manifest_path(value, "results")
    manifest = _load(path)
    if manifest.get("artifact_type") != "equilane.extended-results" or manifest.get("schema_version") != 1 or manifest.get("complete") is not True:
        raise ValueError("not a completed extended result artifact")
    role = manifest.get("runner")
    allowed_backends = {value[0] for value in _BACKENDS.values()}
    if (role not in ROLES or (role == "intel" and manifest.get("backend") != "intel-intrinsics")
            or (role != "intel" and manifest.get("backend") not in allowed_backends)):
        raise ValueError("extended result backend or role is invalid")
    root = path.parent
    input_path, input_manifest, _input_catalog, input_groups = _verified_input(root / "inputs.manifest.json")
    manifest.setdefault("implementation_backend", input_manifest["backend"])
    if (sha256_file(input_path) != manifest["input_manifest_sha256"]
            or input_manifest["inputs"]["sha256"] != manifest["input_sha256"]
            or input_manifest["names"] != manifest["names"]
            or manifest.get("implementation_backend") != input_manifest["backend"]):
        raise ValueError("extended result input evidence changed")
    input_rows = {row["input_id"]: row for group in input_groups.values() for row in group}
    catalog_file = _fixed(root, "catalog.json")
    if sha256_file(catalog_file) != manifest["catalog"]["sha256"]:
        raise ValueError("extended result catalog hash changed")
    catalog = _load(catalog_file)
    if _catalog_identity(catalog) != manifest["source_identity"]:
        raise ValueError("extended result source identity changed")
    evidence = manifest["build_evidence"]
    if evidence.get("file") != "build-evidence.json" or sha256_file(root / evidence["file"]) != evidence["sha256"]:
        raise ValueError("extended build evidence hash changed")
    build = _load(root / "build-evidence.json")
    if (build.get("artifact_type") != "equilane.extended-build" or build.get("role") != role
            or build.get("source_identity") != manifest["source_identity"]):
        raise ValueError("extended build identity mismatch")
    dependencies = build.get("dependencies", [])
    expected_dependencies = 1 if input_manifest["backend"] == "softfloat-fp16" and role != "intel" else 0
    if len(dependencies) != expected_dependencies:
        raise ValueError("extended dependency inventory mismatch")
    for dependency in dependencies:
        if dependency.get("file") != "softfloat/libequilane-softfloat.so" or dependency.get("evidence") != "softfloat/build-evidence.json":
            raise ValueError("invalid extended dependency path")
        library = root / "build" / dependency["file"]
        evidence_path = root / "build" / dependency["evidence"]
        if library.is_symlink() or evidence_path.is_symlink() or sha256_file(library) != dependency["sha256"] or sha256_file(evidence_path) != dependency["evidence_sha256"]:
            raise ValueError("extended dependency identity changed")
        _library_architecture(library, role)
    units = {}
    for unit in build["units"]:
        if unit.get("status") not in {"compiled", "compile_error"}:
            raise ValueError("invalid extended build status")
        if not re.fullmatch(r"unit-[0-9]{5}", unit["unit"]) or unit["source"] != unit["unit"] + ".c":
            raise ValueError("invalid extended build unit")
        if unit["unit"] in units:
            raise ValueError("duplicate extended build unit")
        source = root / "build" / unit["source"]
        if source.is_symlink() or (root / "build" / unit.get("library", "unused")).is_symlink():
            raise ValueError("extended build artifact must not be a symlink")
        expected_source = _generated_source(catalog, unit["names"], role="intel" if role == "intel" else "portable")
        if (sha256_file(source) != unit["source_sha256"]
                or sha256_bytes(expected_source.encode()) != unit["source_sha256"]):
            raise ValueError("extended generated source hash changed")
        if unit["status"] == "compiled":
            if unit.get("library") != unit["unit"] + ".so" or sha256_file(root / "build" / unit["library"]) != unit["library_sha256"]:
                raise ValueError("extended library hash changed")
            _library_architecture(root / "build" / unit["library"], role)
        units[unit["unit"]] = unit
    records = {record["name"]: record for record in catalog["intrinsics"]}
    metadata = manifest["results"]
    if metadata.get("file") != "results.jsonl":
        raise ValueError("invalid extended result path")
    stream = _fixed(root, metadata["file"])
    if stream.stat().st_size != metadata["byte_length"] or sha256_file(stream) != metadata["sha256"]:
        raise ValueError("extended result stream hash changed")
    seen, counts, rows = set(), Counter(), []
    for row in _stream(stream):
        expected = {"schema_version", "name", "ordinal", "runner", "build_unit", "input_id", "input", "rounding", "status", "duration_ns"}
        expected |= {"output", "fp_flags"} if row.get("status") == "ok" else {"error_code"}
        if set(row) != expected or row["schema_version"] != 1 or row["runner"] != role or row["name"] not in manifest["names"]:
            raise ValueError("invalid extended result fields")
        record = records[row["name"]]
        if row["ordinal"] != record["ordinal"] or row["status"] not in RESULT_STATUSES or row["input_id"] in seen:
            raise ValueError("invalid extended result identity or status")
        _hash(row["input_id"])
        original = input_rows.get(row["input_id"])
        if original is None or any(row[key] != original[key] for key in ("name", "ordinal", "input", "rounding")):
            raise ValueError("extended result input identity differs from the source stream")
        _integer(row["duration_ns"])
        unit = units.get(row["build_unit"])
        if unit is None or row["name"] not in unit["names"] or (row["status"] == "compile_error") != (unit["status"] == "compile_error"):
            raise ValueError("extended result build mapping mismatch")
        if row["status"] == "ok":
            if not isinstance(row["output"], str) or not _HEX.fullmatch(row["output"]) or len(row["output"]) != 2 + record["output_byte_length"] * 2:
                raise ValueError("extended result output size mismatch")
            if _integer(row["fp_flags"]) > 31:
                raise ValueError("invalid extended floating-point flags")
        elif not isinstance(row["error_code"], str) or not re.fullmatch(r"[a-z][a-z0-9_.-]{0,127}", row["error_code"]):
            raise ValueError("invalid extended error code")
        seen.add(row["input_id"])
        counts[row["status"]] += 1
        rows.append(row)
    if (len(rows) != metadata["record_count"] or len(rows) != len(input_rows)
            or dict(counts) != manifest["status_counts"]):
        raise ValueError("extended result counts changed")
    compiled = {row["name"] for row in rows if units[row["build_unit"]]["status"] == "compiled"}
    invoked = {row["name"] for row in rows if row["status"] != "compile_error"}
    executed = {row["name"] for row in rows if row["status"] == "ok"}
    expected_status = "pass" if counts["ok"] == len(rows) else "findings"
    if (manifest["candidate_count"] != len(input_groups) or manifest["compiled_candidates"] != len(compiled)
            or manifest["invoked_candidates"] != len(invoked) or manifest["executed_candidates"] != len(executed)
            or manifest["status"] != expected_status):
        raise ValueError("extended execution counts or status changed")
    return path, manifest, rows


def compare_extended_artifacts(intel_manifest, target_manifest, output):
    intel_path, intel, left = _verified_result(intel_manifest)
    target_path, target, right = _verified_result(target_manifest)
    if intel["runner"] != "intel" or target["runner"] == "intel":
        raise ValueError("extended comparison requires an actual Intel reference and a portable target")
    for key in ("input_manifest_sha256", "input_sha256", "source_identity", "names", "implementation_backend"):
        if intel[key] != target[key]:
            raise ValueError("extended comparison inputs or sources differ")
    if [row["input_id"] for row in left] != [row["input_id"] for row in right]:
        raise ValueError("extended result input ordering differs")
    directory = _fresh(output)
    counts, cases = Counter(), defaultdict(Counter)
    def differences():
        for expected, actual in zip(left, right):
            if expected["name"] != actual["name"]:
                raise ValueError("extended result operation identity differs")
            if expected["status"] != "ok" or actual["status"] != "ok":
                outcome = "not_comparable"
            else:
                outcome = "match" if expected["output"] == actual["output"] else "mismatch"
            counts[outcome] += 1
            cases[expected["name"]][outcome] += 1
            if outcome != "match":
                yield {"schema_version": 1, "name": expected["name"], "input_id": expected["input_id"],
                       "outcome": outcome, "input": {"name": expected["name"], "input": expected["input"], "rounding": expected["rounding"]},
                       "intel": expected, "target": actual}
    count, length, digest = write_jsonl(directory / "differences.jsonl", differences())
    outcome = "not_comparable" if counts["not_comparable"] else "mismatch" if counts["mismatch"] else "pass"
    summary = {"schema_version": 1, "artifact_type": "equilane.extended-comparison", "complete": True,
               "backend": intel["implementation_backend"], "scope": intel.get("scope", _SCOPE), "comparison": "bit_exact_return_bytes",
               "fp_flags": "recorded_diagnostics_not_verdict", "status": outcome, "outcome": outcome, "target": target["runner"],
               "physical_hardware_evidence": False, "record_count": len(left), "matched_inputs": counts["match"],
               "mismatched_inputs": counts["mismatch"], "not_comparable_inputs": counts["not_comparable"],
               "candidate_count": len(cases), "intel_executed_candidates": intel["executed_candidates"],
               "target_executed_candidates": target["executed_candidates"],
               "source_identity": intel["source_identity"], "input_sha256": intel["input_sha256"],
               "intel_manifest_sha256": sha256_file(intel_path), "target_manifest_sha256": sha256_file(target_path),
               "differences": {"file": "differences.jsonl", "record_count": count, "byte_length": length, "sha256": digest},
               "cases": [{"name": name, "matched": values["match"], "mismatched": values["mismatch"], "not_comparable": values["not_comparable"]}
                         for name, values in sorted(cases.items())]}
    _comparison_html(directory, summary)
    atomic_write(directory / "summary.json", dump_bytes(summary, newline=True))
    return {"summary": str(directory / "summary.json"), "showcase_report": str(directory / "showcase.html"),
            "record_count": len(left), "matched_inputs": counts["match"], "mismatched_inputs": counts["mismatch"],
            "not_comparable_inputs": counts["not_comparable"], "status": outcome,
            "exit_code": 2 if counts["not_comparable"] else 1 if counts["mismatch"] else 0}


def _comparison_html(directory, summary):
    rows = "".join("<tr>" + "".join(f"<td>{escape(str(row[key]))}</td>" for key in ("name", "matched", "mismatched", "not_comparable")) + "</tr>"
                   for row in summary["cases"])
    for language in ("ja", "en"):
        label = summary["backend"]
        title = f"EquiLane · {label} 拡張バックエンドの比較" if language == "ja" else f"EquiLane · {label} extended backend comparison"
        intro = ("Intel の実命令と移植実装の戻り値をビット単位で比較します。未対応 CPU・コンパイル失敗・実行失敗は比較不能です。登録件数は検証済み件数を意味しません。"
                 if language == "ja" else summary.get("scope", _SCOPE))
        labels = ("一致", "不一致", "比較不能") if language == "ja" else ("Matched", "Mismatched", "Not comparable")
        html = f'''<!doctype html><html lang="{language}"><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>{title}</title>
<style>body{{max-width:1100px;margin:2rem auto;padding:1rem;font:16px/1.7 system-ui}}table{{border-collapse:collapse;width:100%}}td,th{{border:1px solid #ddd;padding:.5rem;overflow-wrap:anywhere}}a{{color:#70538f}}</style>
<nav><a href="showcase.html">Japanese</a> / <a href="showcase.en.html">English</a></nav><h1>{title}</h1><p>{intro}</p>
<p>Status: {escape(summary['status'])} / {escape(summary['target'])}</p><p>{labels[0]}: {summary['matched_inputs']:,} / {labels[1]}: {summary['mismatched_inputs']:,} / {labels[2]}: {summary['not_comparable_inputs']:,}</p>
<p><a href="differences.jsonl">All differing or noncomparable records (JSONL)</a> · <a href="summary.json">Summary JSON</a></p>
<p>Comparison: exact return bytes. FP flags are recorded separately. Physical hardware evidence is not implied.</p>
<table><thead><tr><th>Intrinsic</th><th>{labels[0]}</th><th>{labels[1]}</th><th>{labels[2]}</th></tr></thead><tbody>{rows}</tbody></table></html>'''
        name = "showcase.html" if language == "ja" else "showcase.en.html"
        atomic_write(directory / name, html.encode())


if __name__ == "__main__":
    if sys.argv[1:] != ["_worker"]:
        raise SystemExit("internal extended worker only")
    _worker_main()
