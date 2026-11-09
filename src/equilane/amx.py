"""Explicit-state AMX implementations, separate from Intel execution evidence.

The Python path uses integer IEEE rounding; the C path uses pinned SoftFloat.
Both implement palette 1 (eight 16x64-byte tiles). Intel-native execution remains
unsupported until an actual CPU/OS backend is available; portable output is not
substituted for a hardware reference.
"""
from __future__ import annotations

import ctypes
import hashlib
import json
import platform
from pathlib import Path
import re
import subprocess
import sys
import time
from uuid import uuid4

from .softfloat import ROOT, VENDOR, build_softfloat

KINDS = ("loadconfig", "storeconfig", "release", "load", "store", "zero",
         "dpbssd", "dpbsud", "dpbusd", "dpbuud", "dpbf16ps", "dpfp16ps",
         "cmmrlfp16ps", "cmmimfp16ps")
OPERATIONS = {
    "_tile_loadconfig": "loadconfig", "_tile_storeconfig": "storeconfig", "_tile_release": "release",
    **{prefix + name: kind for prefix in ("_tile_", "__tile_") for name, kind in (
        ("loadd", "load"), ("stream_loadd", "load"), ("stored", "store"), ("zero", "zero"),
        ("dpbssd", "dpbssd"), ("dpbsud", "dpbsud"), ("dpbusd", "dpbusd"), ("dpbuud", "dpbuud"),
        ("dpbf16ps", "dpbf16ps"), ("dpfp16ps", "dpfp16ps"),
        ("cmmrlfp16ps", "cmmrlfp16ps"), ("cmmimfp16ps", "cmmimfp16ps"))},
}


class AMXValidationError(ValueError):
    """Malformed explicit state or an architecturally invalid operation."""


def make_state(shapes=None, *, fill=0):
    """Create JSON state; shapes maps tile index to (rows, column bytes)."""
    if type(fill) is not int or not 0 <= fill <= 255:
        raise AMXValidationError("fill must be one byte")
    config = bytearray(64)
    if shapes:
        if not isinstance(shapes, dict):
            raise AMXValidationError("shapes must map tile indices to row/column pairs")
        config[0] = 1
        for tile, (rows, columns) in shapes.items():
            _integer(tile, 0, 7, "tile")
            _integer(rows, 0, 16, "rows")
            _integer(columns, 0, 64, "columns")
            config[48 + tile] = rows
            config[16 + 2 * tile:18 + 2 * tile] = columns.to_bytes(2, "little")
    _configuration(config)
    return {"config": "0x" + config.hex(), "tiles": ["0x" + bytes([fill] * 1024).hex() for _ in range(8)]}


def _integer(value, low, high, label):
    if type(value) is not int or not low <= value <= high:
        raise AMXValidationError(f"{label} must be an integer in {low}..{high}")
    return value


def _hex(value, length, label):
    if not isinstance(value, str) or not re.fullmatch(r"0x(?:[0-9a-f]{2})*", value):
        raise AMXValidationError(f"{label} requires lowercase hexadecimal bytes")
    raw = bytearray.fromhex(value[2:])
    if length is not None and len(raw) != length:
        raise AMXValidationError(f"{label} requires {length} bytes")
    return raw


def _configuration(config, *, strict_init=True):
    palette = config[0]
    if palette not in (0, 1):
        raise AMXValidationError("only AMX palettes 0 and 1 are supported")
    if palette == 0:
        if strict_init and any(config[1:]):
            raise AMXValidationError("INIT configuration must be all zero")
        return [(0, 0)] * 8
    if any(config[2:16]) or any(config[32:48]) or any(config[56:64]):
        raise AMXValidationError("reserved tile-configuration bytes must be zero")
    shapes = []
    for tile in range(8):
        rows = config[48 + tile]
        columns = int.from_bytes(config[16 + 2 * tile:18 + 2 * tile], "little")
        if rows > 16 or columns > 64 or (rows == 0) != (columns == 0):
            raise AMXValidationError("invalid tile shape")
        shapes.append((rows, columns))
    return shapes


def _decode(name, payload):
    if not isinstance(name, str) or name not in OPERATIONS:
        raise AMXValidationError("unknown AMX intrinsic")
    if not isinstance(payload, dict) or set(payload) != {"state", "arguments", "buffers"}:
        raise AMXValidationError("expected state, arguments, and buffers")
    state = payload["state"]
    if not isinstance(state, dict) or set(state) != {"config", "tiles"}:
        raise AMXValidationError("invalid state object")
    config = _hex(state["config"], 64, "config")
    shapes = _configuration(config)
    if not isinstance(state["tiles"], list) or len(state["tiles"]) != 8:
        raise AMXValidationError("state requires eight tiles")
    tiles = [_hex(raw, 1024, "tile") for raw in state["tiles"]]
    raw_buffers = payload["buffers"]
    if not isinstance(raw_buffers, dict) or len(raw_buffers) > 16:
        raise AMXValidationError("buffers must be an object with at most 16 entries")
    buffers = {}
    for key, value in raw_buffers.items():
        if not isinstance(key, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
            raise AMXValidationError("invalid buffer name")
        if not isinstance(value, dict) or set(value) != {"bytes"}:
            raise AMXValidationError("an AMX buffer has exactly one bytes field")
        buffers[key] = _hex(value["bytes"], None, "buffer")
        if len(buffers[key]) > 1024 * 1024:
            raise AMXValidationError("buffer exceeds the 1 MiB limit")
    kind, args = OPERATIONS[name], payload["arguments"]
    if not isinstance(args, dict):
        raise AMXValidationError("arguments must be an object")
    typed = name.startswith("__")
    expected = ({"mem_addr"} if kind in ("loadconfig", "storeconfig") else set() if kind == "release"
                else {"dst" if typed else "tdest"} if kind == "zero"
                else {"dst", "base", "stride"} if kind == "load"
                else {"src", "base", "stride"} if kind == "store"
                else {"dst", "src0", "src1"} if typed else {"dst", "a", "b"})
    if set(args) != expected:
        raise AMXValidationError(f"arguments for {name} must be {sorted(expected)}")
    dst = a = b = offset = stride = 0
    buffer_name = None
    for key in expected - {"mem_addr", "base", "stride"}:
        _integer(args[key], 0, 7, key)
    if kind == "zero":
        dst = args["dst" if typed else "tdest"]
    elif kind in ("load", "store"):
        dst = args["dst" if kind == "load" else "src"]
        stride = _integer(args["stride"], 0, (1 << 64) - 1, "stride")
    elif kind.startswith(("dp", "cmm")):
        dst, a, b = args["dst"], args["src0" if typed else "a"], args["src1" if typed else "b"]
    if kind in ("loadconfig", "storeconfig", "load", "store"):
        pointer = args["mem_addr" if kind in ("loadconfig", "storeconfig") else "base"]
        if (not isinstance(pointer, dict) or set(pointer) != {"buffer", "offset"} or
                not isinstance(pointer["buffer"], str) or pointer["buffer"] not in buffers):
            raise AMXValidationError("invalid memory pointer")
        buffer_name = pointer["buffer"]
        offset = _integer(pointer["offset"], 0, (1 << 64) - 1, "offset")
    return kind, config, shapes, tiles, buffers, buffer_name, offset, stride, dst, a, b


def _validate_operation(kind, config, shapes, buffers, name, offset, stride, dst, a, b):
    if kind in ("loadconfig", "storeconfig"):
        if offset + 64 > len(buffers[name]):
            raise AMXValidationError("configuration memory is out of bounds")
        if kind == "loadconfig":
            _configuration(buffers[name][offset:offset + 64], strict_init=False)
    elif kind != "release":
        if config[0] != 1 or shapes[dst][0] == 0:
            raise AMXValidationError("destination tile is not configured")
        rows, cols = shapes[dst]
        if kind in ("load", "store"):
            if cols % 4 or config[1] >= rows:
                raise AMXValidationError("invalid memory tile shape or restart row")
            if offset + (rows - 1) * stride + cols > len(buffers[name]):
                raise AMXValidationError("strided memory access is out of bounds")
        elif kind != "zero":
            if len({dst, a, b}) != 3 or not shapes[a][0] or not shapes[b][0]:
                raise AMXValidationError("dot-product tiles must be valid and distinct")
            if (cols % 4 or shapes[a][1] % 4 or shapes[b][1] % 4 or
                    rows != shapes[a][0] or cols != shapes[b][1] or shapes[a][1] // 4 != shapes[b][0]):
                raise AMXValidationError("incompatible dot-product shapes")


def _flush32(bits):
    return bits if bits & 0x7f800000 else bits & 0x80000000


def _round_shift(value, shift):
    if shift <= 0:
        return value << -shift
    quotient, remainder = divmod(value, 1 << shift)
    half = 1 << (shift - 1)
    return quotient + int(remainder > half or (remainder == half and quotient & 1))


def _encode32(value, exponent, zero_sign=0):
    if not value:
        return zero_sign
    sign = 0x80000000 if value < 0 else 0
    value = abs(value)
    unbiased = value.bit_length() - 1 + exponent
    if unbiased > 127:
        return sign | 0x7f800000
    if unbiased < -126:
        significand = _round_shift(value, -149 - exponent)
        return sign | 0x00800000 if significand >= 0x800000 else sign
    significand = _round_shift(value, value.bit_length() - 24)
    if significand >= 1 << 24:
        significand >>= 1
        unbiased += 1
    if unbiased > 127:
        return sign | 0x7f800000
    return sign | ((unbiased + 127) << 23) | (significand & 0x7fffff)


def _term32(bits):
    exponent = (bits >> 23) & 255
    fraction = bits & 0x7fffff
    magnitude = ((1 << 23) | fraction) if exponent else fraction
    return (-magnitude if bits >> 31 else magnitude), (exponent - 150 if exponent else -149)


def _nan(bits):
    return (bits & 0x7fffffff) > 0x7f800000


def _add32(left, right):
    left, right = _flush32(left), _flush32(right)
    if _nan(left) or _nan(right):
        return (left if _nan(left) else right) | 0x400000
    if (left & 0x7fffffff) == 0x7f800000:
        return 0xffc00000 if (right & 0x7fffffff) == 0x7f800000 and (left ^ right) >> 31 else left
    if (right & 0x7fffffff) == 0x7f800000:
        return right
    a, ae = _term32(left)
    b, be = _term32(right)
    exponent = min(ae, be)
    value = (a << (ae - exponent)) + (b << (be - exponent))
    return _encode32(value, exponent, (left & right) & 0x80000000)


def _fma32(left, right, accumulator):
    left, right, accumulator = map(_flush32, (left, right, accumulator))
    if _nan(left) or _nan(right):
        return (left if _nan(left) else right) | 0x400000
    ainf, binf = (left & 0x7fffffff) == 0x7f800000, (right & 0x7fffffff) == 0x7f800000
    sign = (left ^ right) & 0x80000000
    if ainf or binf:
        if (ainf and not (right & 0x7fffffff)) or (binf and not (left & 0x7fffffff)):
            return 0xffc00000
        if _nan(accumulator):
            return accumulator | 0x400000
        if (accumulator & 0x7fffffff) == 0x7f800000 and (accumulator ^ sign) >> 31:
            return 0xffc00000
        return sign | 0x7f800000
    if _nan(accumulator):
        return accumulator | 0x400000
    if (accumulator & 0x7fffffff) == 0x7f800000:
        return accumulator
    a, ae = _term32(left)
    b, be = _term32(right)
    c, ce = _term32(accumulator)
    exponent = min(ae + be, ce)
    value = ((a * b) << (ae + be - exponent)) + (c << (ce - exponent))
    return _encode32(value, exponent, (sign & accumulator) & 0x80000000)


def _widen16(bits, bf16):
    if bf16:
        return bits << 16
    sign, exponent, fraction = (bits & 0x8000) << 16, (bits >> 10) & 31, bits & 1023
    if exponent == 31:
        return sign | 0x7f800000 | (fraction << 13) | (0x400000 if fraction else 0)
    if exponent:
        return sign | ((exponent + 112) << 23) | (fraction << 13)
    return _encode32(-fraction if sign else fraction, -24, sign)


def _result(config, tiles, buffers):
    return {"status": "ok", "state": {"config": "0x" + bytes(config).hex(),
            "tiles": ["0x" + bytes(tile).hex() for tile in tiles]},
            "buffers": {name: {"bytes": "0x" + bytes(raw).hex()} for name, raw in buffers.items()}}


def execute_python(name, payload):
    kind, config, shapes, tiles, buffers, memory, offset, stride, dst, a, b = _decode(name, payload)
    _validate_operation(kind, config, shapes, buffers, memory, offset, stride, dst, a, b)
    if kind == "release" or (kind == "loadconfig" and buffers[memory][offset] == 0):
        config[:] = bytes(64)
        tiles = [bytearray(1024) for _ in range(8)]
    elif kind == "loadconfig":
        config[:] = buffers[memory][offset:offset + 64]
        tiles = [bytearray(1024) for _ in range(8)]
    elif kind == "storeconfig":
        buffers[memory][offset:offset + 64] = config
    elif kind == "zero":
        tiles[dst][:] = bytes(1024)
        config[1] = 0
    elif kind in ("load", "store"):
        rows, columns = shapes[dst]
        start = config[1]
        if kind == "load":
            tiles[dst][start * 64:] = bytes(1024 - start * 64)
        for row in range(start, rows):
            address = offset + row * stride
            if kind == "load":
                tiles[dst][row * 64:row * 64 + columns] = buffers[memory][address:address + columns]
            else:
                buffers[memory][address:address + columns] = tiles[dst][row * 64:row * 64 + columns]
        config[1] = 0
    else:
        rows, columns = shapes[dst]
        result = bytearray(1024)
        for m in range(rows):
            for n in range(columns // 4):
                old = int.from_bytes(tiles[dst][m * 64 + n * 4:m * 64 + n * 4 + 4], "little")
                if kind in ("dpbssd", "dpbsud", "dpbusd", "dpbuud"):
                    value = old
                    for k in range(shapes[a][1] // 4):
                        for j in range(4):
                            x, y = tiles[a][m * 64 + k * 4 + j], tiles[b][k * 64 + n * 4 + j]
                            x = x - 256 if kind in ("dpbssd", "dpbsud") and x >= 128 else x
                            y = y - 256 if kind in ("dpbssd", "dpbusd") and y >= 128 else y
                            value += x * y
                    value &= 0xffffffff
                else:
                    even = odd = 0
                    for k in range(shapes[a][1] // 4):
                        av = [_widen16(int.from_bytes(tiles[a][m * 64 + 4 * k + 2 * j:m * 64 + 4 * k + 2 * j + 2], "little"), kind == "dpbf16ps") for j in (0, 1)]
                        bv = [_widen16(int.from_bytes(tiles[b][k * 64 + 4 * n + 2 * j:k * 64 + 4 * n + 2 * j + 2], "little"), kind == "dpbf16ps") for j in (0, 1)]
                        if kind == "cmmimfp16ps":
                            even, odd = _fma32(av[1], bv[0], even), _fma32(av[0], bv[1], odd)
                        else:
                            if kind == "cmmrlfp16ps":
                                av[1] ^= 0x80000000
                            even, odd = _fma32(av[0], bv[0], even), _fma32(av[1], bv[1], odd)
                    value = _add32(old, _add32(even, odd))
                result[m * 64 + 4 * n:m * 64 + 4 * n + 4] = value.to_bytes(4, "little")
        tiles[dst] = result
        config[1] = 0
    return _result(config, tiles, buffers)


class _State(ctypes.Structure):
    _fields_ = [("config", ctypes.c_uint8 * 64), ("tiles", (ctypes.c_uint8 * 1024) * 8)]


def build_amx(output, *, compiler="clang", jobs=2):
    """Build one portable C implementation for any supported host architecture."""
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    build_softfloat(output / "softfloat", compiler=compiler, jobs=jobs)
    library = output / "libequilane-amx.so"
    subprocess.run([compiler, "-O2", "-std=c11", "-fPIC", "-shared", "-DTHREAD_LOCAL=_Thread_local",
                    "-I" + str(VENDOR / "source/include"), str(ROOT / "adapters/amx/amx.c"),
                    str(output / "softfloat/softfloat.a"), "-o", str(library)],
                   check=True, capture_output=True, timeout=300)
    return library


def execute_c(name, payload, library):
    kind, config, shapes, tiles, buffers, memory, offset, stride, dst, a, b = _decode(name, payload)
    source = _State()
    ctypes.memmove(ctypes.addressof(source), bytes(config) + b"".join(tiles), ctypes.sizeof(source))
    output = _State()
    raw = buffers[memory] if memory is not None else bytearray()
    allocation = (ctypes.c_uint8 * max(1, len(raw)))()
    if raw:
        ctypes.memmove(allocation, bytes(raw), len(raw))
    loaded = library if isinstance(library, ctypes.CDLL) else ctypes.CDLL(str(library))
    function = loaded.equilane_amx_portable
    function.argtypes = [ctypes.c_uint32, ctypes.POINTER(_State), ctypes.POINTER(_State),
                         ctypes.POINTER(ctypes.c_uint8), ctypes.c_uint64, ctypes.c_uint64,
                         ctypes.c_uint64, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_uint32]
    function.restype = ctypes.c_int
    status = function(KINDS.index(kind), ctypes.byref(source), ctypes.byref(output), allocation,
                      len(raw), offset, stride, dst, a, b)
    if status:
        raise AMXValidationError("C AMX adapter rejected the operation")
    if memory is not None:
        buffers[memory] = bytearray(allocation[:len(raw)])
    return _result(output.config, output.tiles, buffers)


def execute(name, payload, *, backend="python", library=None):
    if not isinstance(name, str) or name not in OPERATIONS:
        raise AMXValidationError("unknown AMX intrinsic")
    if backend == "intel-native":
        return {"status": "unsupported", "backend": backend,
                "reason": "Intel AMX CPU/OS instruction execution is not available; portable results are not a reference substitute"}
    if backend == "python":
        return execute_python(name, payload)
    if backend == "portable-c" and library is not None:
        return execute_c(name, payload, library)
    raise AMXValidationError("choose python, portable-c with a library, or intel-native")


def check_inputs():
    """A deterministic cross-host corpus exercising all 27 public entry points."""
    for name, kind in OPERATIONS.items():
        state = make_state({0: (2, 8), 1: (2, 8), 2: (2, 8)}, fill=0xa5)
        memory = bytes((i * 29 + 17) & 255 for i in range(96))
        if kind in ("loadconfig", "storeconfig"):
            config = bytes.fromhex(make_state({0: (16, 64), 7: (1, 4)})["config"][2:])
            memory = b"\xa5" * 3 + config + b"\x5a" * 5
            args = {"mem_addr": {"buffer": "memory", "offset": 3}}
        elif kind == "release":
            args = {}
        elif kind == "zero":
            args = {"dst" if name.startswith("__") else "tdest": 0}
        elif kind in ("load", "store"):
            args = {"dst" if kind == "load" else "src": 0,
                    "base": {"buffer": "memory", "offset": 3}, "stride": 13}
        else:
            args = {"dst": 0, "src0" if name.startswith("__") else "a": 1,
                    "src1" if name.startswith("__") else "b": 2}
            floating = kind in ("dpbf16ps", "dpfp16ps", "cmmrlfp16ps", "cmmimfp16ps")
            for index in range(3):
                raw = bytearray.fromhex(state["tiles"][index][2:])
                for row in range(2):
                    for col in range(2):
                        word = (0x3f800000 if floating else 0xfffffff0) if index == 0 else (
                            0x3f803f80 if kind == "dpbf16ps" else 0x3c003c00) if floating else 0xff807f01
                        raw[row * 64 + col * 4:row * 64 + col * 4 + 4] = word.to_bytes(4, "little")
                state["tiles"][index] = "0x" + raw.hex()
        yield name, {"state": state, "arguments": args, "buffers": {
            "memory": {"bytes": "0x" + memory.hex()}, "untouched": {"bytes": "0x001122ff"}}}


def run_check(output, *, compiler="clang", jobs=2):
    """Persist portable Python/C parity and separate unsupported Intel results."""
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    summary_path = output / "summary.json"
    summary_path.unlink(missing_ok=True)
    report = {"schema_version": 1, "artifact_type": "equilane.amx-check", "status": "error",
              "comparison_scope": "portable Python/C implementation parity",
              "intel_native": {"status": "unsupported", "used_as_reference": False},
              "physical_hardware_conformance": False, "operations": len(OPERATIONS),
              "execution_environment": {"system": platform.system(), "machine": platform.machine(),
                                        "python": platform.python_version()},
              "checks": 0, "mismatches": [], "operation_coverage": []}
    try:
        build_directory = output / "build" / uuid4().hex
        library = build_amx(build_directory, compiler=compiler, jobs=jobs)
        loaded = ctypes.CDLL(str(library))
        inputs_path, results_path = output / "inputs.jsonl", output / "results.jsonl"
        with inputs_path.open("w") as inputs, results_path.open("w") as results:
            for name, payload in check_inputs():
                identity = {"intrinsic": name, "input": payload}
                encoded = json.dumps(identity, sort_keys=True, separators=(",", ":"))
                input_id = hashlib.sha256(encoded.encode()).hexdigest()
                inputs.write(json.dumps({"input_id": input_id, **identity}, sort_keys=True) + "\n")
                expected = execute_python(name, payload)
                start = time.perf_counter_ns()
                actual = execute_c(name, payload, loaded)
                duration = time.perf_counter_ns() - start
                equal = actual == expected
                record = {"input_id": input_id, "intrinsic": name, "duration_ns": duration,
                          "portable_c": actual, "python": expected,
                          "intel_native": {"status": "unsupported"}, "matches_python_model": equal}
                results.write(json.dumps(record, sort_keys=True) + "\n")
                report["checks"] += 1
                report["operation_coverage"].append({"intrinsic": name, "portable_check": "pass" if equal else "mismatch",
                                                       "intel_native": "unsupported"})
                if not equal:
                    report["mismatches"].append({"intrinsic": name, "input_id": input_id})
        report["status"] = "pass" if not report["mismatches"] else "mismatch"
        report["files"] = {"inputs": inputs_path.name, "results": results_path.name,
                           "library": str(library.relative_to(output))}
        report["source_sha256"] = {str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
                                   for path in (ROOT / "src/equilane/amx.py", ROOT / "adapters/amx/amx.c",
                                                ROOT / "adapters/amx/amx.h", ROOT / "adapters/amx/contract.json",
                                                VENDOR / "provenance.json")}
        report["library_sha256"] = hashlib.sha256(library.read_bytes()).hexdigest()
    except (OSError, ValueError, subprocess.SubprocessError, RuntimeError) as exc:
        report["error"] = f"{type(exc).__name__}: {exc}"
    temporary = summary_path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(report, sort_keys=True, indent=2) + "\n")
    temporary.replace(summary_path)
    return report


def main(argv=None):
    import argparse
    parser = argparse.ArgumentParser(description="Check portable AMX state semantics without substituting an Intel reference")
    parser.add_argument("command", choices=("check",))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--compiler", default="clang")
    parser.add_argument("--jobs", type=int, default=2)
    args = parser.parse_args(argv)
    report = run_check(args.output, compiler=args.compiler, jobs=args.jobs)
    print(json.dumps(report, sort_keys=True))
    return 0 if report["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
