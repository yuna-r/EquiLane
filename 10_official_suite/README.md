# Official equivalence suite

<p>
  <img alt="Intel SSE2" src="https://img.shields.io/badge/Intel-SSE2-0071c5?style=flat&amp;logo=intel&amp;logoColor=white">
  <img alt="OpenPOWER" src="https://img.shields.io/badge/OpenPOWER-POWER8-cb2027?style=flat">
  <img alt="VSX" src="https://img.shields.io/badge/SIMD-VSX-f7b500?style=flat">
  <img alt="254 cases" src="https://img.shields.io/badge/cases-254-7c4dff?style=flat">
  <img alt="254,000 standard vectors" src="https://img.shields.io/badge/standard_vectors-254k-00bfa5?style=flat">
  <img alt="Cross build passing" src="https://img.shields.io/badge/Clang_cross_build-passing-2ea44f?style=flat&amp;logo=llvm">
</p>

このrepositoryの先頭に置く、公式のIntel ↔ OpenPOWER / ARM64 / RISC-V64 equivalence suiteです。
254個のcontractと、各architectureの全操作を一覧できるコードをまとめています。

```text
10_official_suite/
├── cases/       254個の1-file case pack
├── intel/       SSE2による全254操作
├── openpower/   VSXによる全254操作
├── arm/         NEONによる全254操作
├── riscv/       RVV＋C helperによる全254操作
├── new-case     case packを1ファイル作るscaffold
└── shortcuts.h  単純なnative wrapperを1行にするmacro
```

| Block | Cases | Intel | OpenPOWER | ARM64 | RISC-V64 |
|---|---:|---|---|---|---|
| f32 arithmetic / compare / lane / mask and f32/f64/i32 cast / conversion | 83 | [`intel/f32x4.c`](intel/f32x4.c) | [`openpower/f32x4.c`](openpower/f32x4.c) | [`arm/f32x4.c`](arm/f32x4.c) | [`riscv/f32x4.c`](riscv/f32x4.c) |
| f64 arithmetic / bit / construct / lane / compare / conversion / memory | 75 | [`intel/f64x2.c`](intel/f64x2.c) | [`openpower/f64x2.c`](openpower/f64x2.c) | [`arm/f64x2.c`](arm/f64x2.c) | [`riscv/f64x2.c`](riscv/f64x2.c) |
| i8 arithmetic / saturation / average / SAD / minmax / shift / lane / mask | 21 | [`intel/i8x16.c`](intel/i8x16.c) | [`openpower/i8x16.c`](openpower/i8x16.c) | [`arm/i8x16.c`](arm/i8x16.c) | [`riscv/i8x16.c`](riscv/i8x16.c) |
| i16 arithmetic / multiply / saturation / average / minmax / compare / shift / pack / lane | 33 | [`intel/i16x8.c`](intel/i16x8.c) | [`openpower/i16x8.c`](openpower/i16x8.c) | [`arm/i16x8.c`](arm/i16x8.c) | [`riscv/i16x8.c`](riscv/i16x8.c) |
| i32 arithmetic / multiply / logic / compare / shift / construct / pack / lane | 27 | [`intel/i32x4.c`](intel/i32x4.c) | [`openpower/i32x4.c`](openpower/i32x4.c) | [`arm/i32x4.c`](arm/i32x4.c) | [`riscv/i32x4.c`](riscv/i32x4.c) |
| i64 arithmetic / shift / construct / lane | 15 | [`intel/i64x2.c`](intel/i64x2.c) | [`openpower/i64x2.c`](openpower/i64x2.c) | [`arm/i64x2.c`](arm/i64x2.c) | [`riscv/i64x2.c`](riscv/i64x2.c) |
| **Total** | **254** | **254 operations** | **254 operations** | **254 operations** | **254 operations** |

- [`cases/`](cases/)は入力生成、比較方法、各architectureのsymbolをまとめたcase packです。
- [`intel/`](intel/)と[`openpower/`](openpower/)は、両側の中心部分を同じ粒度で見せます。
- 全254ケースが`cases/*.py`へ平らに並びます。YAML contractと短いfamily宣言で1ケースです。
- `./10_official_suite/new-case demo-add-i32x4 +`でユーザーcaseの雛形を作れます。
- 実際に証拠を生成するadapterは[`../adapters/`](../adapters/)にあります。

## Clang cross build

Homebrew LLVMなど、x86_64・ppc64le・aarch64・riscv64 backendを持つClangなら1コマンドです。

```sh
./10_official_suite/cross-compile.sh
```

```text
intel/f64x2.o:     ELF 64-bit LSB relocatable, x86-64
openpower/f64x2.o: ELF 64-bit LSB relocatable, OpenPOWER ELF V2 ABI
```

4アーキテクチャ × 6ブロック = 24個のELF objectを生成します。
ARMの実機テストとRISC-VのQEMUテストは [Native status](../NATIVE_STATUS.md) を参照してください。
通常の`check`は共通Pythonモデルによるfixtureで、native runnerによる成果物収集は開発中です。

```sh
equilane check --target all --profile standard --count-per-case 1000 --showcase-report
```

この1コマンドで254ケース・254,000 vectorsの比較とかわいいレポート生成まで進みます。
