# RISC-V encoded ABI examples

`sse2_pd.c` implements the common ABI v1 entry points `riscv_mm_add_pd`,
`riscv_mm_set1_pd`, and `riscv_mm_shuffle_epi32` using real RVV instructions.
The common layer handles canonical JSON, capacity queries, validation and
floating-point environment save/restore. The callbacks execute RVV add,
broadcast and gather with explicit two/four-lane counts.

Build for little-endian RV64 Linux with RVV 1.0, F/D and VLEN >= 128.
Use `-march=rv64gcv -mabi=lp64d -mrvv-vector-bits=scalable` and strict FP flags.
The adapter object cross-compiles with Clang 22. Linking and running the common
libc/fenv layer on RV64 Linux has not yet been verified in this environment.
The independently executed bare-metal tests exercise the 254 raw wrappers in
`10_official_suite/riscv/`; they do not establish this encoded ABI's runtime
conformance.
