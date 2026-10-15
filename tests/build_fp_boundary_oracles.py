"""Capture a deterministic boundary corpus from strict x86_64 SSE2 instructions.

This file imports neither EquiLane nor its portable models and never computes
expected results with Python float. Integer encodings below only build inputs.

Build the probe with the command recorded in native/probe_fp_boundary.c, then:
  python tests/build_fp_boundary_oracles.py --probe /tmp/equilane-fp-boundary-probe \
    --execution-context 'x86_64 SSE2 under Rosetta on arm64 Darwin' \
    --build-command 'clang -arch x86_64 -O0 -msse2 -ffp-model=strict -Wall -Wextra -Werror tests/native/probe_fp_boundary.c -o /tmp/equilane-fp-boundary-probe'
Repeat with --verify to check both captured rows and their source provenance.
"""

from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]
MODES = ("nearest_even", "toward_negative", "toward_positive", "toward_zero")
COMPARE = ("cmpeq", "cmpneq", "cmplt", "cmple", "cmpgt", "cmpge", "cmpnlt", "cmpnle", "cmpngt", "cmpnge", "cmpord", "cmpunord")
COMI = ("comieq", "comineq", "comilt", "comile", "comigt", "comige")
CORPUS = ROOT / "tests/data/fp-boundary-oracles.json"


def stable(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def digest(value):
    return hashlib.sha256(stable(value).encode()).hexdigest()


def vector(element, values):
    width = int(element[1:])
    mask = (1 << width) - 1
    return {"element": element, "lanes": [f"0x{value & mask:0{width // 4}x}" for value in values]}


def scalar(element, value):
    return {"element": element, "bits": vector(element, [value])["lanes"][0]}


def format_bits(element):
    width = int(element[1:])
    fraction = 23 if width == 32 else 52
    bias = 127 if width == 32 else 1023
    return {
        "width": width, "lanes": 128 // width, "fraction": fraction,
        "sign": 1 << (width - 1), "normal": 1 << fraction,
        "one": bias << fraction, "half": (bias - 1) << fraction,
        "two": (bias + 1) << fraction,
        "three": ((bias + 1) << fraction) | (1 << (fraction - 1)),
        "inf": ((1 << (width - fraction - 1)) - 1) << fraction,
        "quiet": 1 << (fraction - 1),
        "half_ulp": (bias - fraction - 1) << fraction,
    }


def edge_values(element):
    f = format_bits(element)
    positive = [0, 1, 2, 3, f["normal"] - 2, f["normal"] - 1, f["normal"], f["normal"] + 1,
                f["half"] - 1, f["half"], f["half"] + 1,
                f["one"] - 1, f["one"], f["one"] + 1,
                f["two"] - 1, f["two"], f["two"] + 1, f["three"],
                f["inf"] - 2, f["inf"] - 1, f["inf"]]
    return positive + [value | f["sign"] for value in positive]


def nan_values(element):
    f = format_bits(element)
    return [f["inf"] | payload | sign for payload, sign in (
        (f["quiet"] | 0x41, 0), (f["quiet"] | 0x12345, 0),
        (f["quiet"] | 7, f["sign"]), ((1 << f["fraction"]) - 1, f["sign"]),
        (3, 0), (0x12345, 0), (0x11, f["sign"]), (f["quiet"] - 1, f["sign"]),
    )]


def poison(element):
    f = format_bits(element)
    return (f["inf"] | 0x35, f["sign"], f["inf"] | f["quiet"] | 0xCAFE)[:f["lanes"] - 1]


def right_poison(element):
    f = format_bits(element)
    return (f["sign"] | f["inf"] | f["quiet"] | 0xD00D,
            1, f["sign"] | f["inf"] | 0x35)[:f["lanes"] - 1]


def exact_f64_scaled_integer(value, exponent):
    """Encode an exact positive integer * 2**exponent input, without float."""
    leading = value.bit_length() - 1
    if leading > 52 or not 0 < leading + exponent + 1023 < 2047:
        raise ValueError("input must fit an exact normal binary64 encoding")
    return ((leading + exponent + 1023) << 52) | ((value << (52 - leading)) - (1 << 52))


def request_line(element, operation, variant, mode, left, right=(), value=0):
    left = tuple(left) + (0,) * (4 - len(left))
    right = tuple(right) + (0,) * (4 - len(right))
    if len(left) != 4 or len(right) != 4:
        raise ValueError("SSE2 requests have four storage slots per operand")
    return " ".join((element, operation, variant, mode, *(hex(x) for x in left), *(hex(x) for x in right), hex(value)))


def plans():
    rows, requests, seen = [], [], set()

    def add(case_id, mode, operands, family, label, element, operation, variant, left, right=(), value=0,
            return_element=None, return_lanes=None):
        identity = {"case_id": case_id, "rounding": mode, "operands": operands}
        key = stable(identity)
        if key in seen:
            return
        seen.add(key)
        rows.append({**identity, "row_id": digest(identity), "family": family, "label": label,
                     "_return_element": return_element or element,
                     "_return_lanes": return_lanes})
        requests.append(request_line(element, operation, variant, mode, left, right, value))

    def binary(element, op, pairs, family, modes):
        f = format_bits(element)
        for a, b in pairs:
            for variant in ("packed", "scalar"):
                if variant == "scalar":
                    left = (a,) + poison(element)
                    right = (b,) + right_poison(element)
                    suffix = "scalar"
                else:
                    left = (a, b, a ^ f["sign"], b ^ f["sign"])[:f["lanes"]]
                    right = (b, a, b ^ f["sign"], a ^ f["sign"])[:f["lanes"]]
                    suffix = "default"
                for mode in modes:
                    add(f"sse2.{op}.{element}x{f['lanes']}.{suffix}", mode,
                        {"a": vector(element, left), "b": vector(element, right)}, family,
                        f"{op}:{a:x}/{b:x}:{variant}", element, op, variant, left, right,
                        return_lanes=f["lanes"])

    for element in ("f32", "f64"):
        f = format_bits(element)
        sub = [(f["normal"], f["normal"] - 1), (f["normal"] - 1, 1), (1, 1), (2, 3),
               (f["normal"] - 2, 2), (f["normal"] + 1, f["normal"]), (f["normal"], 1)]
        half = [(f["one"], f["half_ulp"] + delta) for delta in (-1, 0, 1)]
        half += [(f["one"] + 1, f["half_ulp"] + delta) for delta in (-1, 0, 1)]
        mul = [(value, f["half"] + delta) for value in (1, 3, f["normal"] - 1, f["normal"])
               for delta in (-1, 0, 1)]
        mul += [(f["one"] + 1, f["one"] + 1), (f["inf"] - 1, f["one"] + 1)]
        div = [(value, divisor) for value in (1, 3, f["normal"] - 1, f["normal"])
               for divisor in (f["two"] - 1, f["two"], f["two"] + 1)]
        div += [(f["one"], f["three"]), (f["inf"] - 1, f["one"] - 1)]
        for op in ("add", "sub", "mul", "div"):
            for family, pairs in (("fp_boundary_subnormal_arithmetic", sub),
                                  ("fp_boundary_halfway_arithmetic", half if op in ("add", "sub") else mul if op == "mul" else div)):
                signed = pairs + [(a ^ f["sign"], b) for a, b in pairs]
                binary(element, op, signed, family, MODES)
            zero_inf = [(0, 0), (f["sign"], 0), (0, f["sign"]), (f["sign"], f["sign"]),
                        (f["inf"], f["inf"]), (f["inf"], f["inf"] | f["sign"]),
                        (0, f["inf"]), (f["inf"], 0), (f["one"], f["inf"]),
                        (f["inf"] - 1, 0), (f["one"], f["one"] | f["sign"])]
            binary(element, op, zero_inf, "fp_boundary_signed_zero_infinity", MODES)
            nans = nan_values(element)
            pairs = [(value, nans[(index + step) % len(nans)]) for index, value in enumerate(nans) for step in (1, 4)]
            pairs += [(value, f["one"]) for value in nans] + [(f["one"], value) for value in nans]
            binary(element, op, pairs, "fp_boundary_nan_priority", ("nearest_even",))

        sqrt_values = edge_values(element) + nan_values(element)
        for x in sqrt_values:
            for variant in ("packed", "scalar"):
                left = (x,) + poison(element) if variant == "scalar" else (x, x ^ f["sign"], 3, f["normal"] - 1)[:f["lanes"]]
                for mode in MODES:
                    add(f"sse2.sqrt.{element}x{f['lanes']}.{'scalar' if variant == 'scalar' else 'default'}", mode,
                        {"a": vector(element, left)}, "fp_boundary_sqrt", f"sqrt:{x:x}:{variant}",
                        element, "sqrt", variant, left, return_lanes=f["lanes"])

        values = edge_values(element)
        pairs = [(value, values[(index + 1) % len(values)]) for index, value in enumerate(values)]
        pairs += [(value, value) for value in (0, f["sign"], 1, f["normal"], f["one"], f["inf"])]
        nans = nan_values(element)
        pairs += [(value, nans[(index + 1) % len(nans)]) for index, value in enumerate(nans)]
        pairs += [(value, f["one"]) for value in nans] + [(f["one"], value) for value in nans]
        for op in ("min", "max"):
            binary(element, op, pairs, "fp_boundary_minmax", ("nearest_even",))
        compare_pairs = pairs[::3] + [(0, f["sign"]), (f["sign"], 0)]
        for op in COMPARE:
            binary(element, op, compare_pairs, "fp_boundary_compare", ("nearest_even",))
        for op in COMI:
            for a, b in compare_pairs:
                left, right = (a,) + poison(element), (b,) + right_poison(element)
                add(f"sse2.{op}.{element}x{f['lanes']}.scalar", "nearest_even",
                    {"a": vector(element, left), "b": vector(element, right)}, "fp_boundary_comi",
                    f"{op}:{a:x}/{b:x}", element, op, "scalar", left, right,
                    return_element="i32", return_lanes=None)

        fraction = f["fraction"]
        bias = 127 if element == "f32" else 1023
        integer_limits = [(bias + 31) << fraction]
        if element == "f64":
            integer_limits.append((bias + 63) << fraction)
        ties = [(bias << fraction) | (1 << (fraction - 1)),
                ((bias + 1) << fraction) | (1 << (fraction - 2))]
        conversion_values = edge_values(element)[::2] + nan_values(element)
        for center in integer_limits + ties:
            for delta in (-2, -1, 0, 1, 2):
                conversion_values.extend((center + delta, (center + delta) | f["sign"]))
        for x in sorted(set(conversion_values)):
            for op in ("cvt", "cvtt"):
                for variant in ("packed", "scalar"):
                    left = (x,) + poison(element) if variant == "scalar" else (x, x ^ f["sign"], 0, f["one"])[:f["lanes"]]
                    for mode in MODES:
                        add(f"sse2.{op}.{element}x{f['lanes']}.{'i32.scalar' if variant == 'scalar' else 'i32x4'}", mode,
                            {"a": vector(element, left)}, "fp_boundary_float_to_integer",
                            f"{op}:{x:x}:{variant}", element, op + "_i32", variant, left,
                            return_element="i32", return_lanes=None if variant == "scalar" else 4)
                if element == "f64":
                    left = (x,) + poison(element)
                    for mode in MODES:
                        add(f"sse2.{op}.f64x2.i64.scalar", mode, {"a": vector(element, left)},
                            "fp_boundary_float_to_integer", f"{op}_i64:{x:x}", element, op + "_i64", "scalar", left,
                            return_element="i64", return_lanes=None)

    # f64 nextafter neighbors around several f32 ties, underflow and overflow.
    midpoints = [0x3FF0000000000000 + (index << 29) + (1 << 28) for index in (0, 1, 2, 3, 7, 15, 127, 255)]
    midpoints += [exact_f64_scaled_integer(2 * index + 1, -150) for index in range(4)]
    midpoints += [exact_f64_scaled_integer((1 << 24) - 1, -150), exact_f64_scaled_integer((1 << 25) - 1, 103)]
    narrowing = [(midpoint + delta) | sign for midpoint in midpoints for delta in (-2, -1, 0, 1, 2)
                 for sign in (0, 1 << 63)] + edge_values("f64")[::3] + nan_values("f64")
    for x in sorted(set(narrowing)):
        for variant in ("packed", "scalar"):
            left = (0x7FC12345,) + poison("f32") if variant == "scalar" else (x, x ^ (1 << 63))
            right = (x, 0xFFF0000000000035) if variant == "scalar" else ()
            operands = {"a": vector("f32", left), "b": vector("f64", right)} if variant == "scalar" else {"a": vector("f64", left)}
            for mode in MODES:
                add(f"sse2.cvt.f64x2.f32x4{'.scalar' if variant == 'scalar' else ''}", mode, operands,
                    "fp_boundary_narrowing", f"narrow:{x:x}:{variant}", "f64", "narrow", variant, left, right,
                    return_element="f32", return_lanes=4)

    for x in edge_values("f32") + nan_values("f32"):
        for variant in ("packed", "scalar"):
            left = (0x7FF800000000CAFE,) + poison("f64") if variant == "scalar" else (x, x ^ (1 << 31), 0x7F800035, 0xFFC0CAFE)
            right = (x, 0x7F800035, 0x80000000, 0x7FC0CAFE) if variant == "scalar" else ()
            operands = {"a": vector("f64", left), "b": vector("f32", right)} if variant == "scalar" else {"a": vector("f32", left)}
            add(f"sse2.cvt.f32x4.f64x2{'.scalar' if variant == 'scalar' else ''}", "nearest_even", operands,
                "fp_boundary_widening", f"widen:{x:x}:{variant}", "f32", "widen", variant, left, right,
                return_element="f64", return_lanes=2)

    integers = [0, 1, 2, 3, (1 << 31) - 1, 1 << 31]
    integers += [(1 << 24) + offset for offset in (-3, -2, -1, 0, 1, 2, 3, 5, 7)]
    integers += [(-value) & 0xFFFFFFFF for value in integers]
    for value in sorted(set(integers)):
        for target in ("f32", "f64"):
            f = format_bits(target)
            for variant in ("packed", "scalar"):
                left = (0x7FC12345 if target == "f32" else 0x7FF800000000CAFE,) + poison(target) if variant == "scalar" else (value, value ^ 0xFFFFFFFF, 0x80000000, 0x7FFFFFFF)
                operands = {"a": vector(target, left), "value": scalar("i32", value)} if variant == "scalar" else {"a": vector("i32", left)}
                for mode in MODES if target == "f32" else ("nearest_even",):
                    case_id = f"sse2.cvt.i32.{target}x{f['lanes']}.scalar" if variant == "scalar" else f"sse2.cvt.i32x4.{target}x{f['lanes']}"
                    add(case_id, mode, operands, "fp_boundary_integer_to_float", f"from_i32:{value:x}:{target}:{variant}",
                        "i32", "from_i32_" + target, variant, left, value=value,
                        return_element=target, return_lanes=f["lanes"])
    integers64 = [(1 << 53) + offset for offset in (-3, -2, -1, 0, 1, 2, 3, 5, 7)]
    integers64 += [0, 1, 3, (1 << 63) - 1, 1 << 63]
    integers64 += [(-value) & ((1 << 64) - 1) for value in integers64]
    for value in sorted(set(integers64)):
        left = (0x7FF800000000CAFE,) + poison("f64")
        for mode in MODES:
            add("sse2.cvt.i64.f64x2.scalar", mode, {"a": vector("f64", left), "value": scalar("i64", value)},
                "fp_boundary_integer_to_float", f"from_i64:{value:x}", "i64", "from_i64_f64", "scalar", left,
                value=value, return_element="f64", return_lanes=2)
    return rows, requests


def capture(probe):
    rows, requests = plans()
    result = subprocess.run([str(probe)], input="\n".join(requests) + "\n", text=True,
                            capture_output=True, check=True, timeout=120)
    lines = result.stdout.splitlines()
    if len(lines) != len(rows):
        raise ValueError(f"probe returned {len(lines)} rows for {len(rows)} requests")
    for row, line in zip(rows, lines):
        element = row.pop("_return_element")
        lanes = row.pop("_return_lanes")
        values = [int(token, 16) for token in line.split()]
        if len(values) != (1 if lanes is None else lanes):
            raise ValueError(f"unexpected result shape for {row['case_id']}: {line}")
        row["expected"] = {"return": scalar(element, values[0]) if lanes is None else vector(element, values)}
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--probe", required=True, type=Path)
    parser.add_argument("--execution-context", required=True)
    parser.add_argument("--build-command", required=True)
    parser.add_argument("--output", type=Path, default=CORPUS)
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    probe = args.probe.resolve()
    rows = capture(probe)
    source_hashes = {str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
                     for path in (Path(__file__).resolve(), ROOT / "tests/native/probe_fp_boundary.c")}
    if args.verify:
        stored = json.loads(args.output.read_text())
        if stored["rows"] != rows or stored["source_sha256"] != source_hashes:
            raise ValueError("independent SSE2 capture or pinned source provenance changed")
        if stored["rows_sha256"] != digest(rows):
            raise ValueError("saved corpus digest does not match its captured rows")
    else:
        compiler_version = subprocess.check_output(["clang", "--version"], text=True).strip()
        artifact = {
            "schema_version": 1,
            "oracle": "Runtime SSE2 intrinsics; all MXCSR exceptions masked; FTZ and DAZ off",
            "oracle_method": "captured_sse2_expected_bits",
            "execution_context": args.execution_context,
            "native_cross_host_evidence": False,
            "build_command": args.build_command, "compiler_version": compiler_version,
            "probe_sha256": hashlib.sha256(probe.read_bytes()).hexdigest(),
            "source_sha256": source_hashes,
            "rounding_modes": list(MODES), "rows_sha256": digest(rows), "row_count": len(rows),
            "families": dict(sorted(Counter(row["family"] for row in rows).items())),
            "documentation": [
                "https://www.intel.com/content/www/us/en/docs/intrinsics-guide/index.html",
                "https://www.intel.com/content/www/us/en/developer/articles/technical/floating-point-reference-sheet-for-intel-architecture.html",
            ],
            "rows": rows,
        }
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(artifact, ensure_ascii=False, sort_keys=True, indent=2) + "\n")
    print(f"{'Verified' if args.verify else 'Captured'} {len(rows)} distinct independent SSE2 rows.")
    for name, count in sorted(Counter(row["family"] for row in rows).items()):
        print(f"  {name}: {count}")


if __name__ == "__main__":
    main()
