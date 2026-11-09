"""Lane-independent packed mask adapters over audited SIMDe base kernels."""

from __future__ import annotations

import argparse
from collections import defaultdict
import json
from pathlib import Path
import re
from typing import Any

from .simde_codegen import (ABI_VERSION, ROOT, STATUS, VENDOR, _digest, _intel_features,
                            _type, alias_index, fp_wrapper_source, read_guide, verify_vendor)


OPERATIONS = {
    "add", "adds", "sub", "subs", "mul", "mullo", "mulhi", "div", "sqrt",
    "min", "max", "and", "or", "xor", "andnot", "abs", "avg", "fmadd",
    "fmsub", "fnmadd", "fnmsub", "fmaddsub", "fmsubadd", "sllv", "srlv",
    "srav", "rolv", "rorv", "rcp14", "rsqrt14", "getexp", "scalef",
}
ELEMENTS = {"FP32": 32, "FP64": 64, **{f"{sign}I{bits}": bits
            for sign in ("S", "U") for bits in (8, 16, 32, 64)}}


def build_catalog(archive: str | Path, *, vendor: str | Path = VENDOR) -> dict[str, Any]:
    entries, guide = read_guide(archive)
    aliases = alias_index(vendor)
    by_name = {entry.attrib["name"]: entry for entry in entries}
    official = json.loads((ROOT / "src/equilane/data/intel-intrinsics.json").read_text())
    existing = {row["name"] for row in official["intrinsics"].values()}
    pure = json.loads((ROOT / "contracts/simde-pure-values.json").read_text())
    existing.update(row["name"] for row in pure["intrinsics"])
    accepted = []
    excluded: dict[str, list[str]] = defaultdict(list)
    for name, entry in sorted(by_name.items()):
        if name in aliases or name in existing or not re.search(r"_maskz?_", name):
            continue
        base_name = re.sub(r"_maskz?_", "_", name)
        if base_name not in aliases or base_name not in by_name:
            continue
        base = by_name[base_name]
        operation_match = re.match(r"_mm(?:256|512)?_([^_]+)_", base_name)
        if not operation_match or operation_match.group(1) not in OPERATIONS:
            excluded["not_a_whitelisted_lane_independent_operation"].append(name)
            continue
        operation = operation_match.group(1)
        parameters = entry.findall("parameter")
        base_parameters = base.findall("parameter")
        result = entry.find("return")
        assert result is not None
        output = _type(result.attrib["type"])
        element = result.get("etype")
        if output is None or output["kind"] != "vector" or element not in ELEMENTS:
            excluded["unsupported_result_type"].append(name)
            continue
        lane_bits = ELEMENTS[element]
        if entry.get("tech") == "SVML" or any(p.get("etype") == "IMM" or "*" in p.get("type", "") for p in parameters):
            excluded["pointer_immediate_or_svml"].append(name)
            continue
        parameter_types = [_type(p.attrib["type"]) for p in parameters]
        if any(value is None for value in parameter_types):
            excluded["unsupported_parameter_type"].append(name)
            continue
        if any(_type(p.attrib["type"]) is None
               or _type(p.attrib["type"])["kind"] != "vector"
               or _type(p.attrib["type"])["byte_length"] != output["byte_length"]
               or ELEMENTS.get(p.get("etype")) != lane_bits for p in base_parameters):
            excluded["nonuniform_or_cross_lane_base_signature"].append(name)
            continue
        indices = {p.get("varname"): i for i, p in enumerate(parameters)}
        masks = [i for i, value in enumerate(parameter_types) if value["kind"] == "mask"]
        if len(masks) != 1 or any(p.get("varname") not in indices for p in base_parameters):
            excluded["unsupported_mask_or_operand_mapping"].append(name)
            continue
        lane_count = output["byte_length"] * 8 // lane_bits
        if parameter_types[masks[0]]["byte_length"] * 8 < lane_count:
            excluded["mask_too_narrow"].append(name)
            continue
        operation_text = (entry.findtext("operation") or "").strip()
        else_assignments = re.findall(r"ELSE\s+dst\[i\+\d+:i\]\s*:=\s*([^\n]+)", operation_text)
        zero_mask = "_maskz_" in name
        merge_index = None
        if zero_mask:
            if not else_assignments or any(rhs.strip() != "0" for rhs in else_assignments):
                excluded["unrecognized_zero_mask_operation"].append(name)
                continue
        else:
            matches = [re.fullmatch(r"([A-Za-z_]\w*)\[i\+\d+:i\]", rhs.strip()) for rhs in else_assignments]
            sources = {match.group(1) for match in matches if match is not None}
            if not matches or any(match is None for match in matches) or len(sources) != 1:
                excluded["unrecognized_merge_operation"].append(name)
                continue
            source = next(iter(sources))
            if source not in indices or parameter_types[indices[source]] != output:
                excluded["merge_source_type_mismatch"].append(name)
                continue
            merge_index = indices[source]
        cpuid = sorted({value.text for value in entry.findall("CPUID")})
        try:
            features = _intel_features(name, cpuid)
        except KeyError:
            excluded["unmapped_intel_feature"].append(name)
            continue
        alias = sorted(aliases[base_name], key=lambda row: (len(row["header"]), row["header"]))[0]
        if len(alias["parameters"]) != len(base_parameters):
            excluded["base_alias_arity_mismatch"].append(name)
            continue
        arguments = []
        offset = 0
        for parameter, value in zip(parameters, parameter_types):
            arguments.append({**value, "name": parameter.get("varname"),
                              "element_type": parameter.get("etype"), "offset": offset})
            offset += value["byte_length"]
        base_indices = [indices[p.get("varname")] for p in base_parameters]
        neutral = [0] * len(base_indices)
        if element.startswith("FP"):
            one = 0x3F800000 if lane_bits == 32 else 0x3FF0000000000000
            if operation == "div":
                neutral[1] = one
            elif operation in {"rcp14", "rsqrt14", "getexp"}:
                neutral[0] = one
        accepted.append({
            "ordinal": len(accepted), "name": name, "base_name": base_name,
            "operation": operation, "simde_header": alias["header"],
            "base_alias_parameters": alias["parameters"], "base_alias_body": alias["body"],
            "cpuid": cpuid, "intel_target_features": features,
            "arguments": arguments, "return": output, "input_byte_length": offset,
            "output_byte_length": output["byte_length"], "lane_bits": lane_bits,
            "lane_count": lane_count, "mask_argument_index": masks[0],
            "base_argument_indices": base_indices, "merge_argument_index": merge_index,
            "mask_mode": "zero" if zero_mask else "merge",
            "inactive_operand_bits": [f"0x{value:0{lane_bits // 4}x}" for value in neutral],
            "guide_operation_sha256": _digest(operation_text.encode()),
            "status": "generated_adapter_not_conformance_evidence",
        })
    return {
        "schema_version": 1, "artifact_type": "equilane.simde-masked-candidates",
        "language": "en", "abi_version": ABI_VERSION, "candidate_count": len(accepted),
        "scope": "Packed lane-independent merge/zero masks over a real SIMDe base kernel. Uniform vector lane widths only; no pointer/immediate, gather/compress, scalar, cross-lane, half/BF16, or explicit-rounding form.",
        "semantics": "The Guide ELSE assignment identifies merge or zero output. Inactive operands are neutralized before calling the base kernel, then inactive result bytes are copied from the original merge source or set to zero.",
        "execution_status": "Generated C adapters require native compilation and execution evidence. A catalog row alone is not implementation completion or conformance.",
        "byte_order": "little", "statuses": STATUS,
        "guide": guide, "simde": verify_vendor(vendor),
        "excluded": {reason: {"count": len(names), "names": names} for reason, names in sorted(excluded.items())},
        "intrinsics": accepted,
    }


def validate_catalog(catalog: dict[str, Any], *, vendor: str | Path = VENDOR) -> dict[str, str]:
    if catalog.get("artifact_type") != "equilane.simde-masked-candidates" or catalog.get("abi_version") != ABI_VERSION:
        raise ValueError("invalid masked catalog type or ABI")
    identity = verify_vendor(vendor)
    if catalog.get("simde") != identity:
        raise ValueError("masked catalog source identity changed")
    pinned = json.loads((Path(__file__).parent / "data/intel-intrinsics.json").read_text())["provenance"]
    for field in ("offline_archive_sha256", "data_js_sha256", "decoded_xml_sha256"):
        if catalog.get("guide", {}).get(field) != pinned[field]:
            raise ValueError("masked catalog Guide identity changed")
    aliases = alias_index(vendor)
    records = catalog["intrinsics"]
    if len(records) != catalog["candidate_count"]:
        raise ValueError("masked candidate count changed")
    names = set()
    for ordinal, record in enumerate(records):
        if type(record["ordinal"]) is not int or record["ordinal"] != ordinal or record["name"] in names:
            raise ValueError("invalid masked ordinal or duplicate name")
        names.add(record["name"])
        if not re.fullmatch(r"_mm(?:256|512)?_maskz?_[a-z0-9_]+", record["name"]):
            raise ValueError("invalid masked name")
        if record["operation"] not in OPERATIONS or record["base_name"] != re.sub(r"_maskz?_", "_", record["name"]):
            raise ValueError("invalid masked base operation")
        alias = {"header": record["simde_header"], "parameters": record["base_alias_parameters"],
                 "body": record["base_alias_body"]}
        if not any(all(value.get(k) == v for k, v in alias.items()) for value in aliases.get(record["base_name"], [])):
            raise ValueError("masked base alias differs from vendor source")
        offset = 0
        for argument in record["arguments"]:
            expected = _type(argument["guide_type"])
            if expected is None or any(argument.get(k) != v for k, v in expected.items()) or argument["offset"] != offset:
                raise ValueError("invalid masked argument type or offset")
            offset += expected["byte_length"]
        if offset != record["input_byte_length"] or record["return"] != _type(record["return"]["guide_type"]):
            raise ValueError("invalid masked input or return layout")
        if record["return"]["byte_length"] != record["output_byte_length"] or record["lane_bits"] * record["lane_count"] != 8 * record["output_byte_length"]:
            raise ValueError("invalid masked lane shape")
        if record["intel_target_features"] != _intel_features(record["name"], record["cpuid"]):
            raise ValueError("masked Intel features changed")
        if record["mask_mode"] not in {"merge", "zero"} or len(record["inactive_operand_bits"]) != len(record["base_argument_indices"]):
            raise ValueError("invalid mask or neutralization policy")
        if (record["mask_mode"] == "zero") != ("_maskz_" in record["name"]):
            raise ValueError("mask mode does not match intrinsic")
        if record["lane_bits"] not in (8, 16, 32, 64) or not 1 <= record["lane_count"] <= 64:
            raise ValueError("invalid packed lane dimensions")
        for index in record["base_argument_indices"]:
            if type(index) is not int or not 0 <= index < len(record["arguments"]):
                raise ValueError("invalid masked argument mapping")
            argument = record["arguments"][index]
            if argument["kind"] != "vector" or argument["byte_length"] != record["output_byte_length"] or ELEMENTS.get(argument["element_type"]) != record["lane_bits"]:
                raise ValueError("nonuniform masked base input")
        neutral = [0] * len(record["base_argument_indices"])
        floating = not record["return"]["guide_type"].endswith("i")
        if floating:
            one = 0x3F800000 if record["lane_bits"] == 32 else 0x3FF0000000000000
            if record["operation"] == "div":
                neutral[1] = one
            elif record["operation"] in {"rcp14", "rsqrt14", "getexp"}:
                neutral[0] = one
        if record["inactive_operand_bits"] != [f"0x{value:0{record['lane_bits'] // 4}x}" for value in neutral]:
            raise ValueError("unsafe inactive-lane neutralization")
        if type(record["mask_argument_index"]) is not int or not 0 <= record["mask_argument_index"] < len(record["arguments"]):
            raise ValueError("invalid mask argument")
        mask = record["arguments"][record["mask_argument_index"]]
        if mask["kind"] != "mask" or 8 * mask["byte_length"] < record["lane_count"]:
            raise ValueError("invalid mask width")
        if record["mask_mode"] == "merge":
            if type(record["merge_argument_index"]) is not int or not 0 <= record["merge_argument_index"] < len(record["arguments"]):
                raise ValueError("invalid merge source index")
            if record["arguments"][record["merge_argument_index"]]["guide_type"] != record["return"]["guide_type"]:
                raise ValueError("invalid merge source")
    return {"catalog_sha256": _digest(json.dumps(catalog, sort_keys=True, separators=(",", ":")).encode()),
            "vendor_header_tree_sha256": identity["header_tree_sha256"],
            "vendor_provenance_sha256": identity["provenance_sha256"]}


def generate_source(catalog: dict[str, Any], names: list[str], *, role: str,
                    vendor: str | Path = VENDOR) -> str:
    validate_catalog(catalog, vendor=vendor)
    if role not in {"intel", "portable"}:
        raise ValueError("invalid masked role")
    indexed = {record["name"]: record for record in catalog["intrinsics"]}
    if not names or len(set(names)) != len(names) or any(name not in indexed for name in names):
        raise ValueError("unknown or duplicate masked name")
    records = [indexed[name] for name in names]
    lines = ["/* Generated lane-independent masked adapters; not conformance evidence. */",
             "#include <stdint.h>", "#include <stddef.h>", "#include <string.h>", "#include <fenv.h>",
             "#pragma STDC FENV_ACCESS ON", "#if defined(__x86_64__) || defined(__i386__)",
             "#include <xmmintrin.h>", "#elif defined(__powerpc64__)", "#include <altivec.h>", "#endif",
             "#if defined(__BYTE_ORDER__) && __BYTE_ORDER__ != __ORDER_LITTLE_ENDIAN__",
             '#error "Masked byte ABI requires little endian"', "#endif"]
    if role == "portable":
        lines += ["#define SIMDE_NO_NATIVE", "#define SIMDE_NO_FAST_MATH", "#define SIMDE_NO_FAST_NANS",
                  "#define SIMDE_NO_FAST_CONVERSION_RANGE", "#define SIMDE_NO_FAST_ROUND_MODE",
                  '#include "simde/x86/avx512/types.h"']
        lines += [f'#include "{header}"' for header in sorted({record["simde_header"] for record in records})]
    else:
        lines += ["#if !defined(__x86_64__) && !defined(__i386__)", '#error "Intel reference requires x86"',
                  "#endif", "#include <immintrin.h>"]
    for record in records:
        ordinal = record["ordinal"]
        if role == "intel":
            features = ",".join(record["intel_target_features"])
            lines.append(f'__attribute__((target("{features}"), noinline))')
        else:
            parameters = ", ".join(record["base_alias_parameters"])
            lines.append(f'#define EQUILANE_MASK_BASE_{ordinal}({parameters}) {record["base_alias_body"]}')
        lines.append(f"static int equilane_mask_{ordinal}(const uint8_t *input, uint8_t *output) {{")
        for index, argument in enumerate(record["arguments"]):
            ctype = argument["intel_type" if role == "intel" else "portable_type"]
            lines += [f"  {ctype} a{index};", f"  memcpy(&a{index}, input + {argument['offset']}, {argument['byte_length']});"]
        ctype = record["return"]["intel_type" if role == "intel" else "portable_type"]
        if role == "intel":
            args = ", ".join(f"a{i}" for i in range(len(record["arguments"])))
            lines.append(f"  {ctype} result = {record['name']}({args});")
        else:
            size = record["output_byte_length"]
            lane_bytes = record["lane_bits"] // 8
            lines.append(f"  uint64_t mask = (uint64_t)a{record['mask_argument_index']};")
            for number, (index, neutral) in enumerate(zip(record["base_argument_indices"], record["inactive_operand_bits"])):
                neutral = int(neutral, 16)
                argtype = record["arguments"][index]["portable_type"]
                lines += [f"  {argtype} safe{number};", f"  uint8_t bytes{number}[{size}];",
                          f"  memcpy(bytes{number}, &a{index}, {size});",
                          f"  uint{record['lane_bits']}_t neutral{number} = UINT{record['lane_bits']}_C({neutral});",
                          f"  for (unsigned lane = 0; lane < {record['lane_count']}; ++lane)",
                          f"    if (!(mask & (UINT64_C(1) << lane))) memcpy(bytes{number} + lane * {lane_bytes}, &neutral{number}, {lane_bytes});",
                          f"  memcpy(&safe{number}, bytes{number}, {size});"]
            args = ", ".join(f"safe{i}" for i in range(len(record["base_argument_indices"])))
            lines += [f"  {ctype} result = EQUILANE_MASK_BASE_{ordinal}({args});",
                      f"  uint8_t result_bytes[{size}];", f"  memcpy(result_bytes, &result, {size});"]
            if record["mask_mode"] == "merge":
                lines += [f"  uint8_t merge_bytes[{size}];",
                          f"  memcpy(merge_bytes, &a{record['merge_argument_index']}, {size});"]
                update = f"memcpy(result_bytes + lane * {lane_bytes}, merge_bytes + lane * {lane_bytes}, {lane_bytes})"
            else:
                update = f"memset(result_bytes + lane * {lane_bytes}, 0, {lane_bytes})"
            lines += [f"  for (unsigned lane = 0; lane < {record['lane_count']}; ++lane)",
                      f"    if (!(mask & (UINT64_C(1) << lane))) {update};",
                      f"  memcpy(&result, result_bytes, {size});"]
        lines += [f'  _Static_assert(sizeof(result) == {record["output_byte_length"]}, "masked result size");',
                  f"  memcpy(output, &result, {record['output_byte_length']});", "  return 0;", "}"]
    lines += ["static int equilane_ext_invoke(uint32_t operation, const uint8_t *input, uint64_t input_size,",
              " uint8_t *output, uint64_t output_size) {", " switch(operation) {"]
    for record in records:
        lines += [f" case {record['ordinal']}:",
                  f"  if (input_size != {record['input_byte_length']} || output_size != {record['output_byte_length']} || !input || !output) return 2;"]
        if role == "intel":
            lines += ["  __builtin_cpu_init();", "  if (!(" + " && ".join(
                f'__builtin_cpu_supports("{feature}")' for feature in record["intel_target_features"]) + ")) return 3;"]
        lines.append(f"  return equilane_mask_{record['ordinal']}(input, output);")
    lines += [" default: return 1;", " }", "}", fp_wrapper_source(), ""]
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--guide-archive", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(build_catalog(args.guide_archive), indent=2) + "\n")


if __name__ == "__main__":
    main()
