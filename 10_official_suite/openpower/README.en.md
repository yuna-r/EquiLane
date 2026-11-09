# OpenPOWER examples

[日本語](README.md) | [English](README.en.md)

<p>
  <img alt="OpenPOWER" src="https://img.shields.io/badge/OpenPOWER-POWER8-cb2027?style=flat">
  <img alt="VSX" src="https://img.shields.io/badge/vector-VSX-f7b500?style=flat">
  <img alt="ppc64le" src="https://img.shields.io/badge/arch-ppc64le-5c6bc0?style=flat">
</p>

The core of each Intel-intrinsic port to OpenPOWER is shown for all 254 cases.
Code is grouped by data type so related operations can be read together.

```text
openpower/
├── f32x4.c  83 operations // arithmetic, compare, bit casts, conversion, lane, mask
├── f64x2.c  75 operations // arithmetic, bit, construct, lane, compare, conversion, memory
├── i8x16.c  21 operations // arithmetic, saturation, average, SAD, shift, mask
├── i16x8.c  33 operations // arithmetic, multiply, average, shift, pack, lane
├── i32x4.c  27 operations // arithmetic, logic, compare, shift, construct, lane
└── i64x2.c  15 operations // arithmetic, shift, construct, lane
```

| Family | Intel Intrinsics | OpenPOWER expression |
|---|---|---|
| cross-width bit cast | pd / ps / si128 casts | explicit 32/64-bit lane split and join |
| numeric conversion | ps / pd / epi32 conversions | explicit widening, rounding, truncation, and invalid sentinel |
| f32 arithmetic / compare | `_mm_add_ps` / `_mm_sqrt_ps` / `_mm_min_ps` / `_mm_cmpunord_ps` | VSX arithmetic + exact SSE special-value rules |
| f32 lane / mask | `_mm_unpacklo_ps` / `_mm_movemask_ps` | lane initializer / sign-bit pack |
| f64 arithmetic | `_mm_add_pd` / `_mm_sub_pd` / `_mm_mul_pd` | `vec_add` / `vec_sub` / `vec_mul` |
| f64 bit | `_mm_and_pd` / `_mm_or_pd` / `_mm_xor_pd` | vector `&` / `\|` / `^` |
| f64 construct / lane | set / set1 / move / shuffle / unpack / cast | splat / lane initializer / bit cast |
| f64 compare | packed predicates, min/max, scalar COMI | lane comparison + exact SSE2 selection rules |
| f64 scalar / memory | `_mm_add_sd` / `_mm_loadu_pd` | low-lane add with preservation / byte-exact unaligned load |
| i8 arithmetic / reduction | add / sub / adds / subs / avg / SAD / min / max | vector operations + half reduction |
| i8 lane / mask | byte shifts / unpack / movemask | semantic lane initializer + sign-bit pack |
| i8 compare / construct | eq / gt / lt / set | vector comparison + lane initializer |
| i16 arithmetic / multiply | add / sub / saturating / mul / madd / avg / min / max | vector operations + widened products |
| i16 compare | eq / gt / lt | `vec_cmpeq` / reversed `vec_cmpgt` |
| i16 shift / lane / pack | immediate/vector shift, shuffle, unpack, extract/insert, packs | guarded shift / lane initializer / saturating pack |
| i32 arithmetic / construct | add / sub / even-lane mul / scalar insert / broadcast | vector operations + lane initializer |
| i32 logic | and / or / xor / andnot | vector bit operators |
| i32 compare | eq / gt / lt | `vec_cmpeq` / `vec_cmpgt` |
| i32 shift | immediate and vector-count variants | `vec_sl` / `vec_sr` / `vec_sra` |
| i32 lane / pack | shuffle / unpack / signed pack | lane initializer / saturating pack |
| i64 arithmetic / shift / lane | add / sub / immediate/vector shift / unpack / move / construct / extract | vector operations + lane initializer |

On Linux ppc64le, these are included in the normal native build.

```sh
cmake -S . -B build/native -DCMAKE_BUILD_TYPE=Release
cmake --build build/native --parallel
```

These official examples emphasize readability. Simple vector expressions are reduced to one line per operation
using [`../shortcuts.h`](../shortcuts.h), while explicit rounding and special-value handling remain
ordinary functions so their logic is visible. The implementation including the shared ABI,
input decoding, and exception observation is in
[`../../adapters/openpower/sse2_pd.c`](../../adapters/openpower/sse2_pd.c).
