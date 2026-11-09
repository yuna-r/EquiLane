"""Generate byte-ABI candidates from real SIMDe and Intel implementations.

Catalog membership is a source-audit result, not execution or conformance
evidence.  Intel code always calls the actual intrinsic behind a CPU guard;
portable code calls SIMDe.  Neither path computes results in Python.
"""

from __future__ import annotations

import argparse
import ast
from collections import defaultdict
import hashlib
import json
from pathlib import Path
import re
from typing import Any
import xml.etree.ElementTree as ET
from zipfile import ZipFile


def _source_root() -> Path:
    # Wheels contain Python code; the Docker/source checkout retains contracts
    # and the licensed vendor tree alongside the working project.
    module_root = Path(__file__).resolve().parents[2]
    candidates = [module_root, Path.cwd(), *Path.cwd().parents]
    for candidate in candidates:
        if (candidate / "vendor/simde/provenance.json").is_file() and (candidate / "contracts").is_dir():
            return candidate
    return module_root


ROOT = _source_root()
VENDOR = ROOT / "vendor" / "simde"
GUIDE_MEMBER = "Intel Intrinsics Guide/files/data.js"
ABI_VERSION = 1
STATUS = {"ok": 0, "unknown_operation": 1, "invalid_buffer": 2, "unsupported_cpu": 3,
          "fp_environment_error": 4}

SCALAR_TYPES = {
    "char": ("int8_t", 1), "signed char": ("int8_t", 1),
    "unsigned char": ("uint8_t", 1), "short": ("int16_t", 2),
    "unsigned short": ("uint16_t", 2), "int": ("int32_t", 4),
    "unsigned int": ("uint32_t", 4), "long long": ("int64_t", 8),
    "unsigned long long": ("uint64_t", 8), "__int64": ("int64_t", 8),
    "unsigned __int64": ("uint64_t", 8), "float": ("float", 4),
    "double": ("double", 8),
}
VECTOR_BYTES = {"__m64": 8, **{f"__m{bits}{suffix}": bits // 8
    for bits in (128, 256, 512) for suffix in ("", "d", "i")}}
MASK_BYTES = {f"__mmask{bits}": bits // 8 for bits in (8, 16, 32, 64)}
FEATURES = {
    "MMX": "mmx", "SSE": "sse", "SSE2": "sse2", "SSE3": "sse3",
    "SSSE3": "ssse3", "SSE4.1": "sse4.1", "SSE4.2": "sse4.2",
    "CRC32": "sse4.2", "AVX": "avx", "AVX2": "avx2", "FMA": "fma",
    "F16C": "f16c", "AES": "aes", "GFNI": "gfni", "BMI1": "bmi",
    "BMI2": "bmi2", "PCLMULQDQ": "pclmul", "VPCLMULQDQ": "vpclmulqdq",
    "AVX512F": "avx512f", "AVX512VL": "avx512vl", "AVX512BW": "avx512bw",
    "AVX512DQ": "avx512dq", "AVX512CD": "avx512cd", "AVX512_VBMI": "avx512vbmi",
    "AVX512_VBMI2": "avx512vbmi2", "AVX512_VNNI": "avx512vnni",
    "AVX512_BITALG": "avx512bitalg", "AVX512VPOPCNTDQ": "avx512vpopcntdq",
    "AVX_VNNI": "avxvnni", "AVX512IFMA52": "avx512ifma",
}
INTEL_CALL_ALIASES = {
    "_mm_cvtsd_si64x": "_mm_cvtsd_si64", "_mm_cvtsi128_si64x": "_mm_cvtsi128_si64",
    "_mm_cvtsi64x_sd": "_mm_cvtsi64_sd", "_mm_cvtsi64x_si128": "_mm_cvtsi64_si128",
    "_mm_cvttsd_si64x": "_mm_cvttsd_si64",
}


def _intel_features(name: str, cpuid: list[str]) -> list[str]:
    features = {FEATURES[value] for value in cpuid}
    # Clang implements these masked GFNI APIs with AVX512BW mask-move helpers.
    # Keep the compiler dependency in the runtime guard as well as the target.
    if re.fullmatch(r"_mm(?:256|512)?_maskz?_gf2p8mul_epi8", name):
        features.add("avx512bw")
    return sorted(features)


def _digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def verify_vendor(vendor: str | Path = VENDOR) -> dict[str, Any]:
    """Authenticate every vendored header and the retained upstream license."""

    vendor = Path(vendor)
    manifest_bytes = (vendor / "provenance.json").read_bytes()
    manifest = json.loads(manifest_bytes)
    expected = manifest["files"]
    actual = sorted(str(p.relative_to(vendor)) for p in (vendor / "simde").rglob("*") if p.is_file())
    if sorted(row["file"] for row in expected) != actual or len(expected) != manifest["file_count"]:
        raise ValueError("vendored SIMDe file inventory changed")
    for row in expected:
        relative = Path(row["file"])
        if relative.is_absolute() or ".." in relative.parts or relative.parts[0] != "simde":
            raise ValueError("invalid vendored SIMDe path")
        path = vendor / relative
        if path.is_symlink() or _digest(path.read_bytes()) != row["sha256"]:
            raise ValueError(f"vendored SIMDe source hash changed: {relative}")
    license_file = vendor / manifest["license_file"]
    if license_file.is_symlink() or _digest(license_file.read_bytes()) != manifest["license_sha256"]:
        raise ValueError("vendored SIMDe license hash changed")
    return {key: value for key, value in manifest.items() if key != "files"} | {
        "provenance_sha256": _digest(manifest_bytes),
        "header_tree_sha256": _digest(json.dumps(expected, sort_keys=True, separators=(",", ":")).encode()),
    }


def _type(value: str) -> dict[str, Any] | None:
    value = re.sub(r"\s+", " ", value.strip()).removeprefix("const ")
    if value in SCALAR_TYPES:
        c_type, size = SCALAR_TYPES[value]
        return {"guide_type": value, "intel_type": c_type, "portable_type": c_type,
                "byte_length": size, "kind": "scalar"}
    if value in VECTOR_BYTES or value in MASK_BYTES:
        return {"guide_type": value, "intel_type": value,
                "portable_type": "simde" + value,
                "byte_length": (VECTOR_BYTES | MASK_BYTES)[value],
                "kind": "mask" if value in MASK_BYTES else "vector"}
    return None


def read_guide(archive: str | Path) -> tuple[list[ET.Element], dict[str, Any]]:
    archive_bytes = Path(archive).read_bytes()
    with ZipFile(archive) as source:
        data = source.read(GUIDE_MEMBER)
    script = data.decode("utf-8")
    if not script.startswith("var data_js = ") or not script.rstrip().endswith(";"):
        raise ValueError("unexpected Guide data wrapper")
    xml = ast.literal_eval(script.removeprefix("var data_js = ").strip().removesuffix(";"))
    if not isinstance(xml, str):
        raise ValueError("Guide data is not a string literal")
    root = ET.fromstring(xml)
    if root.tag != "intrinsics_list":
        raise ValueError("unexpected Guide XML root")
    return root.findall("intrinsic"), {
        "version": root.attrib["version"], "date": root.attrib["date"],
        "offline_archive_sha256": _digest(archive_bytes), "data_js_sha256": _digest(data),
        "decoded_xml_sha256": _digest(xml.encode()),
    }


def alias_index(vendor: str | Path = VENDOR) -> dict[str, list[dict[str, Any]]]:
    vendor = Path(vendor)
    index: dict[str, list[dict[str, Any]]] = defaultdict(list)
    pattern = re.compile(r"^\s*#\s*define\s+([A-Za-z_]\w*)\b([^\n]*)", re.MULTILINE)
    for header in sorted((vendor / "simde" / "x86").rglob("*.h")):
        content = header.read_text().replace("\\\n", "")
        for match in pattern.finditer(content):
            name, body = match.groups()
            parameters = re.match(r"^\(([^)]*)\)\s*(.*)$", body)
            if parameters is None:
                continue
            parameter_text, expression = parameters.groups()
            target = re.search(r"\b(simde_[A-Za-z_0-9]+)\s*\(", body)
            if target is not None:
                index[name].append({"header": str(header.relative_to(vendor)),
                                    "symbol": target.group(1),
                                    "parameters": [p.strip() for p in parameter_text.split(",") if p.strip()],
                                    "body": expression.strip()})
    return dict(index)


def build_catalog(archive: str | Path, *, vendor: str | Path = VENDOR,
                  registered_catalog: str | Path = ROOT / "src/equilane/data/intel-intrinsics.json") -> dict[str, Any]:
    entries, provenance = read_guide(archive)
    vendor = Path(vendor)
    aliases = alias_index(vendor)
    registered = json.loads(Path(registered_catalog).read_text())
    pinned = registered["provenance"]
    for field in ("offline_archive_sha256", "data_js_sha256", "decoded_xml_sha256"):
        if pinned.get(field) != provenance[field]:
            raise ValueError(f"Guide source does not match the registered source: {field}")
    existing = {entry["name"] for entry in registered["intrinsics"].values()}
    guide_by_name = {entry.attrib["name"]: entry for entry in entries}
    accepted: dict[str, dict[str, Any]] = {}
    excluded: dict[str, set[str]] = defaultdict(set)
    for entry in entries:
        name = entry.attrib["name"]
        if name in existing:
            excluded["existing_official_case"].add(name)
            continue
        if name not in aliases:
            excluded["no_direct_simde_alias"].add(name)
            continue
        parameters = [p for p in entry.findall("parameter") if p.get("type") != "void"]
        result = entry.find("return")
        assert result is not None
        all_types = [result.attrib["type"], *[p.attrib["type"] for p in parameters]]
        categories = sorted({str(c.text) for c in entry.findall("category")})
        cpuid = sorted({str(c.text) for c in entry.findall("CPUID")})
        reason = None
        if result.attrib["type"] == "void":
            reason = "void_result"
        elif any("*" in value or "[" in value for value in all_types):
            reason = "pointer_or_array"
        elif any(p.get("etype") == "IMM" or p.get("immwidth") for p in parameters):
            reason = "compile_time_immediate"
        elif set(categories) & {"General Support", "OS-Targeted", "Random"}:
            reason = "stateful_or_system_category"
        elif entry.get("tech") == "SVML":
            reason = "svml_requires_separate_intel_reference_toolchain"
        elif any(_type(value) is None for value in all_types):
            reason = "unsupported_type_in_initial_abi"
        elif not cpuid or any(value not in FEATURES for value in cpuid):
            reason = "unmapped_intel_cpu_feature"
        if reason:
            excluded[reason].add(name)
            continue
        offset = 0
        arguments = []
        for position, parameter in enumerate(parameters):
            value = _type(parameter.attrib["type"])
            assert value is not None
            arguments.append({**value, "name": parameter.get("varname", f"argument_{position}"),
                              "element_type": parameter.get("etype"), "offset": offset})
            offset += value["byte_length"]
        output = _type(result.attrib["type"])
        assert output is not None
        choices = sorted(aliases[name], key=lambda x: (len(x["header"]), x["header"], x["symbol"]))
        alias = choices[0]
        if len(alias["parameters"]) != len(parameters):
            excluded["simde_alias_parameter_count"].add(name)
            continue
        intel_symbol = INTEL_CALL_ALIASES.get(name, name)
        alias_evidence = None
        if intel_symbol != name:
            original = (entry.findtext("operation") or "").strip()
            canonical = (guide_by_name[intel_symbol].findtext("operation") or "").strip()
            if not original or original != canonical:
                raise ValueError(f"Intel synonym operation differs: {name}")
            alias_evidence = {"source_name": name, "call_name": intel_symbol,
                              "identical_guide_operation_sha256": _digest(original.encode()),
                              "reason": "The legacy si64x spelling is absent from Clang; the Guide canonical si64 spelling has identical Operation text."}
        record = {
            "name": name, "simde_symbol": alias["symbol"],
            "simde_header": alias["header"], "simde_alias_parameters": alias["parameters"],
            "simde_alias_body": alias["body"], "intel_call_symbol": intel_symbol,
            "intel_synonym_evidence": alias_evidence, "technology": entry.get("tech"),
            "cpuid": cpuid, "intel_target_features": _intel_features(name, cpuid),
            "compiler_feature_note": "Masked GFNI uses Clang AVX512BW mask-move helpers." if "avx512bw" in _intel_features(name, cpuid) and "AVX512BW" not in cpuid else None,
            "categories": categories, "arguments": arguments, "return": output,
            "input_byte_length": offset, "output_byte_length": output["byte_length"],
            "status": "source_candidate_not_execution_evidence",
        }
        if name not in accepted or len(record["intel_target_features"]) < len(accepted[name]["intel_target_features"]):
            accepted[name] = record
    records = [dict(accepted[name], ordinal=index) for index, name in enumerate(sorted(accepted))]
    return {
        "schema_version": 1, "artifact_type": "equilane.simde-pure-value-candidates",
        "language": "en", "abi_version": ABI_VERSION,
        "scope": "Unregistered pure-value API names with a real SIMDe alias, fixed-size scalar/vector/mask arguments, and an Intel intrinsic reference available to Clang. No pointer, void-result, immediate, system, half/bfloat, or SVML API is claimed by this initial generator.",
        "execution_status": "No catalog entry is proof of compilation, execution, or conformance. Each generated unit requires independent native validation.",
        "byte_order": "little", "input_layout": "Arguments concatenated in signature order without padding.",
        "intel_reference": "Actual Intel intrinsic with function target attributes and a runtime CPU-feature guard; never SIMDe.",
        "portable_backend": "Actual SIMDe C implementation with SIMDE_NO_NATIVE and without fast-math shortcuts.",
        "fp_environment": "Each call saves caller controls, uses nearest-even with exception traps masked and FTZ disabled, captures normalized exception flags, then restores caller controls. The initial comparison scope is return bytes only.",
        "normalized_fp_flags": {"invalid": 1, "divide_by_zero": 2, "overflow": 4,
                                "underflow": 8, "inexact": 16},
        "statuses": STATUS, "guide": provenance,
        "simde": verify_vendor(vendor),
        "registered_name_count": len(existing), "candidate_count": len(records),
        "excluded": {reason: {"count": len(names), "names": sorted(names)} for reason, names in sorted(excluded.items())},
        "intrinsics": records,
    }


def validate_catalog(catalog: dict[str, Any], *, vendor: str | Path = VENDOR) -> dict[str, str]:
    """Validate source identity, types, aliases, ordinals, and ABI layouts."""

    if catalog.get("schema_version") != 1 or catalog.get("abi_version") != ABI_VERSION:
        raise ValueError("unsupported SIMDe catalog schema or ABI")
    pinned = json.loads((Path(__file__).parent / "data/intel-intrinsics.json").read_text())["provenance"]
    for field in ("offline_archive_sha256", "data_js_sha256", "decoded_xml_sha256"):
        if catalog.get("guide", {}).get(field) != pinned[field]:
            raise ValueError(f"SIMDe catalog Guide identity changed: {field}")
    current_vendor = verify_vendor(vendor)
    if catalog.get("simde") != current_vendor:
        raise ValueError("SIMDe catalog and current vendored source do not match")
    aliases = alias_index(vendor)
    records = catalog["intrinsics"]
    if catalog.get("candidate_count") != len(records):
        raise ValueError("SIMDe candidate count changed")
    names: set[str] = set()
    for ordinal, record in enumerate(records):
        if type(record["ordinal"]) is not int or record["ordinal"] != ordinal:
            raise ValueError("SIMDe catalog ordinals are not contiguous")
        if record["name"] in names:
            raise ValueError("duplicate SIMDe catalog name")
        names.add(record["name"])
        if not re.fullmatch(r"[A-Za-z_]\w*", record["name"]) or not re.fullmatch(r"simde_\w+", record["simde_symbol"]):
            raise ValueError("invalid intrinsic identifier")
        if {"header": record["simde_header"], "symbol": record["simde_symbol"],
            "parameters": record["simde_alias_parameters"], "body": record["simde_alias_body"]} not in aliases.get(record["name"], []):
            raise ValueError("SIMDe alias or header does not match source")
        if len(record["simde_alias_parameters"]) != len(record["arguments"]):
            raise ValueError("SIMDe alias arity does not match Guide")
        if record["intel_call_symbol"] != INTEL_CALL_ALIASES.get(record["name"], record["name"]):
            raise ValueError("unrecognized Intel synonym")
        if any(feature not in FEATURES.values() for feature in record["intel_target_features"]):
            raise ValueError("invalid Intel target feature")
        if not record["cpuid"] or any(label not in FEATURES for label in record["cpuid"]) or _intel_features(record["name"], record["cpuid"]) != record["intel_target_features"]:
            raise ValueError("Intel feature guard does not match CPUID metadata")
        offset = 0
        for argument in record["arguments"]:
            type_info = _type(argument["guide_type"])
            if type_info is None or any(argument.get(k) != v for k, v in type_info.items()) or argument["offset"] != offset:
                raise ValueError("invalid argument layout")
            offset += argument["byte_length"]
        result_info = _type(record["return"]["guide_type"])
        if result_info != record["return"] or offset != record["input_byte_length"]:
            raise ValueError("invalid result or input layout")
        if result_info["byte_length"] != record["output_byte_length"]:
            raise ValueError("invalid result size")
    return {"catalog_sha256": _digest(json.dumps(catalog, sort_keys=True, separators=(",", ":")).encode()),
            "vendor_header_tree_sha256": current_vendor["header_tree_sha256"],
            "vendor_provenance_sha256": current_vendor["provenance_sha256"]}



def fp_wrapper_source() -> str:
    """Shared C dispatch wrapper preserving IEEE controls and FP flags."""
    return r'''
/* Same IEEE controls as adapters/common/native_preflight.c. */
static int equilane_ext_ieee_setup(void) {
  if (fesetenv(FE_DFL_ENV) || fesetround(FE_TONEAREST)) return 0;
#if defined(__x86_64__) || defined(__i386__)
  _mm_setcsr(0x1f80);
#elif defined(__aarch64__)
  uint64_t control;
  __asm__ volatile("mrs %0, fpcr" : "=r"(control));
  control &= ~((UINT64_C(1)<<24)|(UINT64_C(1)<<25)|(UINT64_C(31)<<8));
  __asm__ volatile("msr fpcr, %0" :: "r"(control));
#elif defined(__powerpc64__)
  __asm__ volatile("mtfsb0 29");
  __vector unsigned int vscr = vec_mfvscr();
  vscr = vec_and(vscr, (__vector unsigned int){~0x10000u,~0x10000u,~0x10000u,~0x10000u});
  vec_mtvscr(vscr);
#endif
  return feclearexcept(FE_ALL_EXCEPT) == 0;
}
int equilane_ext_call_with_flags(uint32_t operation, const uint8_t *input, uint64_t input_size,
                                 uint8_t *output, uint64_t output_size, uint32_t *flags) {
  fenv_t saved;
  if (flags) *flags = 0;
#if defined(__powerpc64__)
  __vector unsigned int saved_vscr = vec_mfvscr();
#endif
  if (fegetenv(&saved)) return 4;
  int status;
  if (!equilane_ext_ieee_setup()) status = 4;
  else {
    status = equilane_ext_invoke(operation, input, input_size, output, output_size);
    int raised = fetestexcept(FE_ALL_EXCEPT);
    if (flags && status == 0) *flags =
      ((raised & FE_INVALID) ? 1u : 0u) |
      ((raised & FE_DIVBYZERO) ? 2u : 0u) |
      ((raised & FE_OVERFLOW) ? 4u : 0u) |
      ((raised & FE_UNDERFLOW) ? 8u : 0u) |
      ((raised & FE_INEXACT) ? 16u : 0u);
  }
  if (fesetenv(&saved)) status = 4;
#if defined(__powerpc64__)
  vec_mtvscr(saved_vscr);
#endif
  return status;
}
int equilane_ext_call(uint32_t operation, const uint8_t *input, uint64_t input_size,
                      uint8_t *output, uint64_t output_size) {
  return equilane_ext_call_with_flags(operation, input, input_size, output, output_size, NULL);
}
'''

def generate_source(catalog: dict[str, Any], names: list[str], *, role: str,
                    vendor: str | Path = VENDOR) -> str:
    if role not in {"intel", "portable"}:
        raise ValueError("role must be intel or portable")
    validate_catalog(catalog, vendor=vendor)
    indexed = {record["name"]: record for record in catalog["intrinsics"]}
    if not names or len(names) != len(set(names)) or any(name not in indexed for name in names):
        raise ValueError("names must be nonempty, unique catalog candidates")
    records = [indexed[name] for name in names]
    lines = ["/* Generated SIMDe/Intel byte ABI. Catalog membership is not verification. */",
             "#include <stdint.h>", "#include <stddef.h>", "#include <string.h>", "#include <fenv.h>",
             "#pragma STDC FENV_ACCESS ON",
             "#if defined(__x86_64__) || defined(__i386__)", "#include <xmmintrin.h>",
             "#elif defined(__powerpc64__)", "#include <altivec.h>", "#endif",
             "#if defined(__BYTE_ORDER__) && __BYTE_ORDER__ != __ORDER_LITTLE_ENDIAN__",
             '#error "EquiLane extended byte ABI currently requires little endian"', "#endif"]
    if role == "portable":
        lines += ["#define SIMDE_NO_NATIVE", "#define SIMDE_NO_FAST_MATH",
                  "#define SIMDE_NO_FAST_NANS", "#define SIMDE_NO_FAST_CONVERSION_RANGE",
                  "#define SIMDE_NO_FAST_ROUND_MODE", "#define SIMDE_ACCURACY_PREFERENCE 2"]
        lines += [f'#include "{header}"' for header in sorted({r["simde_header"] for r in records})]
    else:
        lines += ['#if !defined(__x86_64__) && !defined(__i386__)',
                  '#error "Intel reference requires an x86 CPU"', "#endif", "#include <immintrin.h>"]
    for record in records:
        ordinal = record["ordinal"]
        if role == "portable":
            parameters = ", ".join(record["simde_alias_parameters"])
            lines.append(f'#define EQUILANE_EXT_ALIAS_{ordinal}({parameters}) {record["simde_alias_body"]}')
        if role == "intel":
            features = ",".join(record["intel_target_features"])
            lines.append(f'__attribute__((target("{features}"), noinline))')
        lines.append(f"static int equilane_ext_{ordinal}(const uint8_t *input, uint8_t *output) {{")
        for index, argument in enumerate(record["arguments"]):
            c_type = argument["intel_type" if role == "intel" else "portable_type"]
            size, offset = argument["byte_length"], argument["offset"]
            lines += [f"  {c_type} a{index};", f'  _Static_assert(sizeof(a{index}) == {size}, "argument width");',
                      f"  memcpy(&a{index}, input + {offset}, {size});"]
        c_type = record["return"]["intel_type" if role == "intel" else "portable_type"]
        symbol = record["intel_call_symbol"] if role == "intel" else f"EQUILANE_EXT_ALIAS_{ordinal}"
        arguments = ", ".join(f"a{index}" for index in range(len(record["arguments"])))
        lines += [f"  {c_type} result = {symbol}({arguments});",
                  f'  _Static_assert(sizeof(result) == {record["output_byte_length"]}, "return width");',
                  f"  memcpy(output, &result, {record['output_byte_length']});"]
        if role == "intel" and "mmx" in record["intel_target_features"]:
            lines.append("  _mm_empty();")
        lines += ["  return 0;", "}"]
    lines += ["static int equilane_ext_invoke(uint32_t operation, const uint8_t *input, uint64_t input_size,",
              "                      uint8_t *output, uint64_t output_size) {",
              "  switch (operation) {"]
    for record in records:
        lines += [f"    case {record['ordinal']}:",
                  f"      if (input_size != {record['input_byte_length']} || output_size != {record['output_byte_length']} ||",
                  "          (!input && input_size) || !output) return 2;"]
        if role == "intel":
            lines += ["      __builtin_cpu_init();", "      if (!(" + " && ".join(
                f'__builtin_cpu_supports("{feature}")' for feature in record["intel_target_features"]) + ")) return 3;"]
        lines.append(f"      return equilane_ext_{record['ordinal']}(input, output);")
    lines += ["    default: return 1;", "  }", "}", fp_wrapper_source(), ""]
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    capture = commands.add_parser("catalog")
    capture.add_argument("--guide-archive", required=True, type=Path)
    capture.add_argument("--output", required=True, type=Path)
    generate = commands.add_parser("generate")
    generate.add_argument("--catalog", required=True, type=Path)
    generate.add_argument("--name", action="append", required=True)
    generate.add_argument("--role", choices=("intel", "portable"), required=True)
    generate.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if args.command == "catalog":
        value = build_catalog(args.guide_archive)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
    else:
        catalog = json.loads(args.catalog.read_text())
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(generate_source(catalog, args.name, role=args.role))


if __name__ == "__main__":
    main()
