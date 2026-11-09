# Official SSE2 ↔ OpenPOWER cases

[日本語](README.md) | [English](README.en.md)

This is an independent example suite, separate from the framework itself.
Each case is a single file, similar in size to one Ruby class.

There are 254 portable-model case packs covering arithmetic, multiplication, saturation,
average/SAD, logic, comparison, shifts, packing, and lane operations.
The handwritten native-adapter examples cover `_mm_add_pd`, `_mm_set1_pd`, and `_mm_shuffle_epi32`.

```text
10_official_suite/cases/
├── add-f32x4.py
├── add-f64x2.py
├── add-i32x4.py
└── ...
```

Each file's `CASE_YAML` accepts only a safe subset of the YAML 1.2 JSON schema.
Anchors, aliases, merge keys, explicit tags, duplicate keys, and floating-point numbers are rejected.
Artifact hashes are calculated from the converted JSON data model, not from YAML formatting.

## Small, Ruby-like units

The declaration after the YAML specifies only the case ID, type, and operation.
Shared components handle random inputs, boundaries, rounding, lane decoding/encoding,
saturation, and signed conversion.

```python
CASE_YAML = """
schema_version: 1
id: sse2.add.i32x4.default
# The signature and comparison rules follow here
"""

from equilane.casepack_families import binary_case


CASE_ID, MINIMUM_COUNTS, candidates, execute = binary_case(
    "sse2.add.i32x4.default", "i32x4", "+", standard=8)
```

The compact operators are `+ - & | ^ ~& == > < sat+ sat- avg min max *lo *hi`.
Conversions use `conversion_case`, lane rearrangement uses `lanes_case`, comparisons use
`float_compare_case`, and shuffles use `shuffle_case`.
Shared components also handle bit-level conversion oracles, NaNs, immediates, and unaligned buffers,
leaving the essential declaration visible when a case pack is opened.

When an instruction does not fit an existing family, add a small family first.
Tests also check that large model implementations do not creep back into case packs.

The generator and portable fixtures discover `cases/*.py` automatically, so no central
`if case_id ==` dispatch needs changing. Intel/OpenPOWER native-evidence adapters remain in
`adapters/`, where each CPU is compiled separately as an execution boundary.

Contract loading extracts only the literal `CASE_YAML` through the AST without executing Python.
The file is executed only when its model is needed. The previous `case.yaml + model.py` layout
remains supported for compatibility.

`development_fixture: true` in result JSON is a compatibility artifact classification meaning
“not native evidence captured on an actual CPU.” It is unrelated to the case-pack filename.

## Create a new case with one command

For a typical binary integer-vector operation, run this from the suite root:

```sh
./10_official_suite/new-case demo-add-i32x4 +
```

It creates only `cases/demo-add-i32x4.py` and never overwrites an existing file.
Add the corresponding Intel/OpenPOWER native symbols and run `equilane check`.
See `./10_official_suite/new-case --help` for supported operators.

Simple native wrappers also fit on one line using [`../shortcuts.h`](../shortcuts.h):

```c
IOITF_BINARY(i32x4, i32x4, intel_add_i32x4, _mm_add_epi32(a, b))
IOITF_BINARY(i32x4, i32x4, power_add_i32x4, vec_add(a, b))
```

Complex shuffles and conversions remain ordinary C functions so their important logic is easy to read.

For short C examples showing the OpenPOWER / VSX core first, open the adjacent
[`../openpower/`](../openpower/) directory.
