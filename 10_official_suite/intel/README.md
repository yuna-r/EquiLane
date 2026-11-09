# Intel examples

[日本語](README.md) | [English](README.en.md)

<p>
  <img alt="Intel" src="https://img.shields.io/badge/Intel-x86__64-0071c5?style=flat&amp;logo=intel&amp;logoColor=white">
  <img alt="SSE2" src="https://img.shields.io/badge/vector-SSE2-00a4ef?style=flat">
  <img alt="254 operations" src="https://img.shields.io/badge/operations-254-7c4dff?style=flat">
</p>

OpenPOWER側と同じ全254ケースを、Intel SSE2 Intrinsicsで型ごとの塊にまとめています。

```text
intel/
├── f32x4.c  83 operations // arithmetic, compare, bit casts, conversion, lane, mask
├── f64x2.c  75 operations // arithmetic, bit, construct, lane, compare, conversion, memory
├── i8x16.c  21 operations // arithmetic, saturation, average, SAD, shift, mask
├── i16x8.c  33 operations // arithmetic, multiply, average, shift, pack, lane
├── i32x4.c  27 operations // arithmetic, logic, compare, shift, construct, lane
└── i64x2.c  15 operations // arithmetic, shift, construct, lane
```

| Block | Cases | Source |
|---|---:|---|
| f32 arithmetic / compare / lane / mask and f32/f64/i32 cast / conversion | 83 | [`f32x4.c`](f32x4.c) |
| f64 arithmetic / bit / construct / lane / compare / conversion / memory | 75 | [`f64x2.c`](f64x2.c) |
| i8 arithmetic / saturation / average / SAD / minmax / shift / lane / mask | 21 | [`i8x16.c`](i8x16.c) |
| i16 arithmetic / multiply / saturation / average / minmax / compare / shift / pack / lane | 33 | [`i16x8.c`](i16x8.c) |
| i32 arithmetic / multiply / logic / compare / shift / construct / pack / lane | 27 | [`i32x4.c`](i32x4.c) |
| i64 arithmetic / shift / construct / lane | 15 | [`i64x2.c`](i64x2.c) |

入力decodeや共通ABIを外し、比較対象になるIntrinsic呼び出しを見やすくしたofficial exampleです。
単純wrapperは[`../shortcuts.h`](../shortcuts.h)の`IOITF_BINARY` / `IOITF_UNARY`で
1操作1行にし、switchやlane操作だけを普通のC関数として目立たせています。
実際のevidence adapterは[`../../adapters/intel/sse2_pd.c`](../../adapters/intel/sse2_pd.c)にあります。
