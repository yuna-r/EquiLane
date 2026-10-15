# RISC-V RV64 / RVV 1.0

Intel側と同じ254操作を、RISC-V向けのC wrapperで実装しています。
`f32x4.c`、`f64x2.c`、`i8x16.c`、`i16x8.c`、`i32x4.c`、`i64x2.c` の6ブロックです。

公開vector型は16バイトのC vectorです。内部では `rvv128.h` のRVV intrinsicを使い、
各命令へ `128 / SEW` の有効レーン数を渡します。浮動小数点の算術、整数の飽和演算、
shift、broadcastには明示的なRVV load / operation / storeがあります。
pack、shuffle、レーン配置、SSE2の特殊値・変換規則にはRISC-Vで実行するCの補助処理を使います。
Python fixtureを呼ぶ実装ではありません。

必要条件はRV64、RVV 1.0、F/D、little endian、VLEN 128以上です。
Clang 22で次の形式のcross buildを確認しています。

```sh
clang --target=riscv64-unknown-linux-gnu -ffreestanding \
  -march=rv64gcv -mabi=lp64d -mrvv-vector-bits=scalable \
  -O2 -ffp-model=strict -Wall -Wextra -Wpedantic -Werror \
  -c 10_official_suite/riscv/f64x2.c -o build/riscv-f64x2.o
```

`-mrvv-vector-bits=128` は使いません。この指定はコンパイラが実際のVLENを128と仮定し、
whole-register load/storeへ置き換えることを許します。そのbinaryをVLEN 256で使うと、
16バイトのslotを超えてアクセスする可能性があります。headerでも固定VLEN指定を拒否します。

## QEMUで実行する

```sh
tests/native/test_riscv_smoke.sh
```

同じbare-metal ELFをQEMU `virt` のVLEN 128 / 256で実行します。
M-modeのstartupでFS/VSを有効にし、UARTで結果を表示します。18検査には6つの要素型、
飽和・shift境界、unaligned memoryのguard、scalarの未使用NaNレーン、動的丸め、
`fflags` のinexact / invalidを含みます。両VLENで18件成功、0件失敗を確認しています。

全wrapperをIntel/SSE2の実行結果と直接比べる場合：

```sh
.venv/bin/python tests/native/test_riscv_suite.py
```

この取得・比較ツールはmacOSのx86_64 C executableを参照経路に使います。
Apple Siliconでは参照がRosetta、RISC-V側がQEMUです。
254操作に3入力ずつ、VLENごとに762照合を実行します。入力には混在レーン、符号付きゼロ、
整数境界、NaN、合法な即値、メモリの前後guardを含め、戻り値と全bufferをbyte単位で比較します。
期待値の計算にPythonモデルを使いません。成果物とsource digestは
`build/riscv-native-suite/summary.json` に残ります。

現在はVLEN 128 / 256とも750照合が一致し、12照合が不一致です。
packed / scalarのf32 / f64 add、sub、mulで、RVVのcanonical NaNとSSE2のNaN payload・符号が異なります。
これらを隠さず記録し、比較ツールは終了コード1を返します。両VLENの結果全体は一致しています。
他の入力での一致、全丸めモード、全native FP例外の適合性まで証明した結果ではありません。

これは**エミュレーター上でのRISC-V機械語実行**です。RISC-V実機やLinux runnerの
native conformance成果物ではありません。共通encoded ABI v1のadd / set1 / shuffle adapterは
`adapters/riscv/` にありますが、そのLinux libc / fenvとのlink・実行は別途検証が必要です。

仕様資料：
[RVV 1.0](https://docs.riscv.org/reference/isa/unpriv/v-st-ext)、
[RVV C intrinsic](https://docs.riscv.org/reference/vector-c-intrinsics/rvv-intrinsic-examples.html)、
[LLVM RVV lowering](https://llvm.org/docs/RISCV/RISCVVectorExtension.html)、
[QEMU virt](https://www.qemu.org/docs/master/system/riscv/virt.html)。
