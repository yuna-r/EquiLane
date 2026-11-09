"""Generate the encoded ABI for every registered official C wrapper.

The generated adapters decode typed input bits in C and call architecture C
implementations. No portable model participates in producing native results.
"""
from __future__ import annotations

from pathlib import Path
import re

from .cases import CaseDefinition
from .errors import ValidationError

BLOCKS = ("f32x4", "f64x2", "i8x16", "i16x8", "i32x4", "i64x2")
PREFIXES = {"intel": "intel", "openpower": "power", "arm": "arm", "riscv": "riscv"}
VECTOR_TYPES = {
    "i8x16": "signed char", "u8x16": "unsigned char", "i16x8": "short",
    "u16x8": "unsigned short", "i32x4": "int", "u32x4": "unsigned int",
    "i64x2": "long long", "u64x2": "unsigned long long", "f32x4": "float", "f64x2": "double",
}


def native_compile_flags(role: str) -> tuple[str, ...]:
    flags = {
        "intel": ("-march=x86-64", "-msse2", "-mno-avx"),
        "openpower": ("-mcpu=power8", "-maltivec", "-mvsx"),
        "arm": ("-march=armv8-a+simd",),
        "riscv": ("-march=rv64gcv", "-mabi=lp64d", "-mrvv-vector-bits=scalable"),
    }
    if role not in flags:
        raise ValidationError(f"unsupported native target: {role!r}")
    return ("-O2", "-std=gnu11", "-ffp-model=strict", "-fno-fast-math", "-ffp-contract=off", *flags[role])


def raw_declarations(project_root: Path) -> dict[str, tuple[str, list[tuple[str, str]]]]:
    """Read the restricted shared declaration grammar; incomplete catalogs fail."""
    functions: dict[str, tuple[str, list[tuple[str, str]]]] = {}
    for block in BLOCKS:
        source = (project_root / "10_official_suite/riscv" / f"{block}.c").read_text()
        declarations = []
        for match in re.finditer(r"IOITF_(UNARY|BINARY)\(\s*([\w ]+?)\s*,\s*(\w+)\s*,\s*riscv_(\w+)\s*,", source):
            arity, result, argument, name = match.groups()
            declarations.append((name, result.strip(), [("a", argument)] + ([("b", argument)] if arity == "BINARY" else [])))
        for match in re.finditer(r"^([\w ]+\*?)\s+riscv_(\w+)\(([^)]*)\)\s*\{", source, re.MULTILINE):
            result, name, raw_arguments = match.groups()
            arguments = []
            for raw in raw_arguments.split(",") if raw_arguments.strip() not in ("", "void") else []:
                item = re.fullmatch(r"\s*(.*?)\s*\b(\w+)\s*", raw, re.DOTALL)
                if item is None:
                    raise ValidationError(f"cannot parse native wrapper parameter: {name}: {raw}")
                arguments.append((item[2], " ".join(item[1].split())))
            declarations.append((name, result.strip(), arguments))
        for name, result, arguments in declarations:
            if name in functions:
                raise ValidationError(f"duplicate native wrapper: {name}")
            functions[name] = (result, arguments)
    if not functions:
        raise ValidationError("native wrapper registry is empty")
    return functions


def _return_parameters(contract: dict) -> tuple[str, int, int, int]:
    if contract["type"] == "void":
        return "NULL", 0, 0, 0
    element = str(contract["element"])
    width = int(element[1:]) // 8
    scalar = int(contract["type"] == "scalar")
    return f'"{element}"', width, int(contract.get("lanes", 1)), scalar


def generate_native_suite(cases, role: str, output_directory: str | Path, *, project_root: str | Path | None = None) -> Path:
    """Write generated ABI C; compile with runtime C, six C blocks and root includes.

    Each exported case symbol has ``ioitf_case_fn``'s public ABI. The three
    ``equilane_symbol_*``/``equilane_lookup`` exports audit the dynamic registry.
    """
    native_compile_flags(role)
    root = Path(project_root) if project_root else Path(__file__).resolve().parents[2]
    functions = raw_declarations(root)
    prefix = PREFIXES[role]
    lines = ['/* Generated from validated case signatures; do not edit. */\n',
             '#include "adapters/common/native_suite_runtime.h"\n#include <string.h>\n']
    if role == "arm":
        lines.append('#include "10_official_suite/arm/neon_helpers.h"\n')
    elif role == "riscv":
        lines.append('#include "10_official_suite/riscv/rvv128.h"\n')
    else:
        if role == "intel":
            lines.append('#include <emmintrin.h>\n')
        else:
            lines.append('#include <altivec.h>\n')
        for name, scalar in VECTOR_TYPES.items():
            actual = "__m128" if name == "f32x4" else "__m128d" if name == "f64x2" else "__m128i"
            lines.append(f'typedef {actual if role == "intel" else "__vector " + scalar} {name};\n')
    for name, (result, arguments) in sorted(functions.items()):
        lines.append(f"extern {result} {prefix}_{name}({','.join(t for _, t in arguments) or 'void'});\n")
    entries = []
    for case in sorted(cases, key=lambda item: item.id):
        if role not in case.target_roles:
            continue
        if case.source_path is None:
            raise ValidationError(f"case {case.id}: native generation requires its official source path")
        operation = case.source_path.stem.replace("-", "_")
        if operation not in functions:
            raise ValidationError(f"case {case.id}: no native wrapper for {operation!r}")
        result_type, arguments = functions[operation]
        contracts = case.signature["arguments"]
        if len(contracts) != len(arguments):
            raise ValidationError(f"case {case.id}: native and encoded signature arity differs")
        symbol = str(case.data[role]["symbol"])
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", symbol):
            raise ValidationError(f"case {case.id}: invalid ABI symbol")
        entries.append(symbol)
        normal_count = sum(item["type"] != "immediate" for item in contracts)
        immediate_count = len(contracts) - normal_count
        pointer_count = sum(item["type"] == "pointer" for item in contracts)
        return_parameters = _return_parameters(case.signature["return"])
        formatted_return = ",".join(map(str, return_parameters))
        lines.append(f'\nint {symbol}(const ioitf_input *input,ioitf_output *output) {{\n'
                     'if (!ns_valid_abi(input,output)) return IOITF_CALL_INVALID_ABI;\n'
                     f'ns_context context; if (!ns_init(&context,input,output,{normal_count},{immediate_count},{pointer_count})) {{ ns_destroy(&context); return IOITF_CALL_OK; }}\n')
        modes = {"nearest_even": 0, "toward_zero": 1, "toward_positive": 2, "toward_negative": 3}
        allowed_modes = " || ".join(f"input->rounding_mode=={modes[str(mode)]}U" for mode in case.environment["fp_rounding_modes"])
        lines.append(f'if (!({allowed_modes})) goto invalid;\n')
        for index, ((_, raw_type), contract) in enumerate(zip(arguments, contracts)):
            name = contract["name"]
            lines.append(f"{raw_type} p{index};\n")
            if contract["type"] == "immediate":
                values = case.data["immediates"][name]["values"]
                allowed = " || ".join(f"immediate{index}=={value}LL" for value in values)
                lines.append(f'int64_t immediate{index}; if (!ns_immediate(&context,"{name}",&immediate{index}) || !({allowed})) goto invalid;\n'
                             f'p{index}=({raw_type})immediate{index};\n')
            elif contract["type"] == "pointer":
                memory = case.data["memory_contract"][name]
                extent = max((int(item["offset"]) + int(item["byte_length"]) for kind in ("read_ranges", "write_ranges") for item in memory[kind]), default=0)
                lines.append(f'void *pointer{index}; if (!ns_pointer(&context,"{name}",{memory["required_alignment"]},{extent},&pointer{index})) goto invalid; p{index}=pointer{index};\n')
            else:
                element = contract["element"]
                width = int(str(element)[1:]) // 8
                lanes = int(contract.get("lanes", 1))
                scalar = int(contract["type"] == "scalar")
                lines.append(f'if (!ns_value(&context,"{name}","{element}",{width},{lanes},{scalar},&p{index},sizeof(p{index}))) goto invalid;\n')
        lines.append(f'if (!ns_capacity(&context,output,{formatted_return})) {{ ns_destroy(&context); return IOITF_CALL_OUTPUT_TOO_SMALL; }}\n'
                     'if (!ns_begin(&context,input->rounding_mode)) goto runtime;\n')
        expression = f"{prefix}_{operation}({','.join(f'p{i}' for i in range(len(arguments)))})"
        if result_type == "void":
            lines.append(expression + ";\n")
            returned = "NULL"
        else:
            lines.append(f"{result_type} returned={expression};\n")
            returned = "&returned"
        lines.append('if (!ns_end(&context,&output->normalized_fp_exceptions)) goto runtime;\n'
                     f'ns_encode(&context,output,{formatted_return},{returned}); ns_destroy(&context); return IOITF_CALL_OK;\n'
                     'invalid: output->status=IOITF_OUTPUT_INVALID_INPUT; output->return_size=output->effects_size=0; ns_destroy(&context); return IOITF_CALL_OK;\n'
                     'runtime: output->status=IOITF_OUTPUT_RUNTIME_ERROR; output->return_size=output->effects_size=0; ns_destroy(&context); return IOITF_CALL_OK;\n}\n')
    if not entries or len(set(entries)) != len(entries):
        raise ValidationError("native symbol registry is empty or contains duplicate symbols")
    lines.append('static const char *const registered_symbols[]={\n' + ",\n".join(f'"{name}"' for name in sorted(entries)) + '};\n')
    lines.append('size_t equilane_symbol_count(void) { return sizeof(registered_symbols)/sizeof(registered_symbols[0]); }\n'
                 'const char *equilane_symbol_at(size_t index) { return index<equilane_symbol_count()?registered_symbols[index]:NULL; }\n'
                 'ioitf_case_fn equilane_lookup(const char *name) { if (!name) return NULL;\n')
    for name in sorted(entries):
        lines.append(f'if (strcmp(name,"{name}")==0) return {name};\n')
    lines.append('return NULL; }\n')
    directory = Path(output_directory)
    directory.mkdir(parents=True, exist_ok=True)
    generated = directory / f"encoded-{role}.c"
    generated.write_text("".join(lines), encoding="utf-8")
    return generated
