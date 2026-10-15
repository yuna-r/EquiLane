# 不一致の詳細レポート — 2026-10-16

この記録では、**Python portable model の出力不一致2,159件**と、ARM / RISC-V の
命令実行で観測した差を区別します。Pythonの2,159件は2,157個の異なる入力ID、
30個のcase IDに対応します。2,159個の独立した根本原因がある、という意味ではありません。
モデル本体の該当処理は未修正です。

モデル本体の参照バージョンはコミット `b61b60aced1e808259eb960a291950fea6751abc` です。
集計は追加テストを含む作業ツリーで取得し、実際に参照したコードとoracleはSHA-256で識別します。
集計元は `.equilane/test-expansion-20261016-final/summary.json`、公開可能な相対パスと
代表入力を残した固定スナップショットは
[`tests/data/mismatch-report-2026-10-16.json`](tests/data/mismatch-report-2026-10-16.json) です。
元のsummaryやbuild成果物は別のcheckoutに存在しない場合があります。

## 1. 何を比較したか

Python検証では、公式または明示した拡張契約に入力が適合することを検証し、
`load_development_case(...).execute(record)` の返り値を独立した期待値と直接比較しました。
[`equilane.compare`](src/equilane/compare.py) を経由した判定ではありません。
実行経路は [`VerifiedModelMixin.assert_model`](tests/model_test_support.py#L43) と
[`AdversarialModelTests.assert_model`](tests/test_adversarial_models.py#L61) にあります。

期待値には、Pythonモデルを呼ばないx86_64 SSE2 probeの記録を使っています。
取得環境は **arm64 Darwin上のRosettaによるx86_64実行**、strict FP、例外mask、FTZ/DAZ無効です。
Intel実機とOpenPOWER実機の比較記録ではありません。FPの固定corpusは
[`rounding-oracles.json`](tests/data/rounding-oracles.json) の504行と
[`fp-boundary-oracles.json`](tests/data/fp-boundary-oracles.json) の10,708行です。
初期の36件は [`BUG_HUNT.md`](tests/BUG_HUNT.md) にあるSSE2確認値を使います。
corpusのprobe、生成スクリプト、保存行のdigestはテストでも検証しています。

通常のdevelopment fixtureは、対象名がIntel / OpenPOWER / ARM / RISC-Vであっても
同じPythonモデルを呼びます。両側で同じ間違った値を返せばfixture比較は一致します。
その一致は、以下のモデル不一致が解消した証拠にも、各CPU命令の一致の証拠にもなりません。
実装は [`fixture._result_record`](src/equilane/fixture.py#L88) を参照してください。

## 2. Python検証の集計

| 指標 | 値 |
|---|---:|
| 実行環境 | arm64 / Darwin / Python 3.14.7 |
| テストメソッドの実行数 | 1,186 |
| 独立したモデル出力照合数 | 74,722 |
| 照合で記録した異なる入力ID数 | 74,700 |
| モデル出力の不一致記録数 | 2,159 |
| 不一致の異なる入力ID数 | 2,157 |
| 不一致のcase ID数 | 30 |
| モデル不一致以外のassertion失敗 | 0 |
| 実行エラー / skip | 0 / 0 |
| 行カバレッジ | 未計測（`coverage_collected: false`、割合は`null`） |

照合回数には同じ入力を異なるテストで再照合した分を含みます。不一致でも2個の入力IDが
別familyで2回ずつ記録されているため、記録数と異なる入力数に2件の差があります。
テストメソッド数、入力数、命令ケース数、根本原因数を混同しないでください。

| 契約の範囲 | 不一致記録 | 異なる入力ID | case ID | 指定丸め |
|---|---:|---:|---:|---|
| `official_nearest_even` | 106 | 105 | 12 | `nearest_even` |
| `validated_rounding_extension` | 2,053 | 2,052 | 30 | 下記3モード |

拡張契約は元の公式caseを複製し、対象の丸めモードと、入力IDに対応する回帰期待値を
付けて検証したものです。公式case packのnearest-even宣言を変更していません。
したがって2,053件を「公式nearest-even契約の失敗」として数えることはできません。

| 入力の `environment.rounding` | 不一致記録 |
|---|---:|
| `nearest_even` | 106 |
| `toward_negative` | 778 |
| `toward_positive` | 698 |
| `toward_zero` | 577 |

### 観測を実装経路ごとにまとめた7グループ

| グループ | 不一致記録 | 異なる入力ID | case ID | 範囲 |
|---|---:|---:|---:|---|
| 無効演算で生成するNaNの符号 | 28 | 27 | 12 | 公式nearest-even |
| NaN payloadの選択 | 78 | 78 | 12 | 公式nearest-even |
| 算術の指定丸め未反映 | 744 | 743 | 16 | 丸め拡張 |
| 平方根の指定丸め未反映 | 214 | 214 | 4 | 丸め拡張 |
| 浮動小数点→整数の指定丸め未反映 | 372 | 372 | 5 | 丸め拡張 |
| f64→f32の指定丸め未反映 | 610 | 610 | 2 | 丸め拡張 |
| 整数→浮動小数点の指定丸め未反映 | 113 | 113 | 3 | 丸め拡張 |

この7分類は、保存された出力差と現行コードを対応づけた分析です。
family名そのものを根本原因とみなしてはいません。特に
`fp_boundary_signed_zero_infinity` の84件には、公式のNaN符号16件と、
拡張契約の丸め68件が混在するため、契約範囲を見て分類しています。

## 3. 代表入力・期待値・実測値

以下の`0x...`は各レーンまたはscalarのビットパターンです。
`×4`は同じ値を4レーンに設定することを示します。期待値は保存したSSE2基準値、
実測値はこのarm64環境のPythonモデルの出力です。
全文の入力レコード、入力ID、期待値、実測値、テストID、oracle参照はJSONの
`representative_findings` に16例を保存しています。

### 公式契約: NaN符号とpayload

| case / 指定 | 入力 | 期待値 | モデル実測 |
|---|---|---|---|
| `sse2.add.f32x4.default` / nearest-even | a=`0x7f800000`×4、b=`0xff800000`×4 | `0xffc00000`×4 | `0x7fc00000`×4 |
| `sse2.add.f32x4.default` / nearest-even | a=`0x7fc00042`×4、b=`0x7f800001`×4 | `0x7fc00042`×4 | `0x7fc00001`×4 |

第1例は`+inf + -inf`で、期待値と実測値の違いはNaNの符号bitです。
第2例は第一引数のquiet NaNと第二引数のsignaling NaNです。quiet化した後に
残るpayloadが、期待値では第一引数、モデルでは第二引数になっています。
この入力を含む公式ケースは`bit_exact`で、入力domainからNaNを除外していません。
両方がNaNであることだけでは一致になりません。

観測箇所は [`float_binary_case`](src/equilane/casepack_families.py#L463) です。
add / sub / mulは[`_float_from_bits`](src/equilane/casepack_families.py#L179)でPython floatへ
変換し、ホストの算術で演算後に[`_float_to_bits`](src/equilane/casepack_families.py#L184)で戻します。
この経路とホスト依存のNaN伝播が、観測した差と対応します。
divは別のビット演算経路を使い、この公式NaN符号・payload分類には入っていません。

### 検証用拡張契約: 指定丸め

| case / 指定 | 入力 | 期待値 | モデル実測 |
|---|---|---|---|
| `sse2.add.f32x4.default` / toward-positive | a=`0x3f800000`×4、b=`0x33000000`×4 | `0x3f800001`×4 | `0x3f800000`×4 |
| `sse2.sqrt.f32x4.default` / toward-positive | a=`0x40000000`×4 | `0x3fb504f4`×4 | `0x3fb504f3`×4 |
| `sse2.cvt.f32x4.i32.scalar` / toward-negative | a=`0x3fc00000`×4 | i32=`0x00000001` | i32=`0x00000002` |
| `sse2.cvt.f64x2.f32x4` / toward-positive | a=`0x3ff0000010000000`×2 | `[0x3f800001, 0x3f800001, 0x00000000, 0x00000000]` | `[0x3f800000, 0x3f800000, 0x00000000, 0x00000000]` |
| `sse2.cvt.i32x4.f32x4` / toward-positive | a=`0x01000001`×4 | `0x4b800001`×4 | `0x4b800000`×4 |

算術の第1例は`1 + 2^-25`、整数変換の例は`1.5`、整数→f32の例は`16,777,217`です。
無限小の違いだけに見えても、整数変換では返る整数そのものが変わります。
さらに次の入力では、ゼロの符号や最小非正規数への丸めにも影響しています。

| case / 指定 | 入力 | 期待値 | モデル実測 |
|---|---|---|---|
| `sse2.add.f32x4.default` / toward-negative | a=`[-0,+0,+0,-0]`、b=`[+0,-0,-0,+0]` | `0x80000000`×4 | `0x00000000`×4 |
| `sse2.cvt.f64x2.f32x4` / toward-negative | a=`[0x0000000000000003, 0x8000000000000003]` | `[0x00000000, 0x80000001, 0x00000000, 0x00000000]` | `[0x00000000, 0x80000000, 0x00000000, 0x00000000]` |

指定丸めに対応する値は入力に存在しますが、次の実行経路にはその値が渡っていません。
すべてを「ホストfloatの誤差」で説明することはできません。

| 分類 | 主な実装経路 | コードから読み取れること |
|---|---|---|
| 算術 | [`float_binary_case`](src/equilane/casepack_families.py#L463)、[`_divide_float_bits`](src/equilane/casepack_families.py#L206)、[`_round_ratio_even`](src/equilane/casepack_families.py#L194) | add/sub/mulはホスト演算。divは整数比のnearest-even計算。いずれも入力のroundingで分岐しない |
| 平方根 | [`sqrt_case`](src/equilane/casepack_families.py#L1162) | 有限の正数に`math.sqrt`とbits変換を使用し、入力roundingを使用しない |
| 浮動小数点→整数 | [`conversion_case`](src/equilane/casepack_families.py#L884)、[`float_to_scalar_case`](src/equilane/casepack_families.py#L1005)、[`_float_to_integer`](src/equilane/casepack_families.py#L830) | `cvt`は`round()`、`cvtt`は`math.trunc()`。指定した方向への丸めは渡らない |
| f64→f32 | [`conversion_case`](src/equilane/casepack_families.py#L884)、[`scalar_conversion_case`](src/equilane/casepack_families.py#L919)、[`_f64_to_f32`](src/equilane/casepack_families.py#L775) | ビット演算の変換だがnearest-even固定の補助処理を使う |
| 整数→浮動小数点 | 同上のconversion factories、[`_i32_to_f32`](src/equilane/casepack_families.py#L813) | i32→f32の固定丸め、または`float()`とbits変換を使用し、入力roundingを使用しない |

これは指定丸めを伝播していないという実装上の説明と整合します。
このスナップショットは、各行の差を完全なIEEE演算の証明や、すべての未検査入力に
一般化した結果として扱うものではありません。

### 数値として何を計算しているか

ビット列は、値が非常に近い場合や、NaN・符号付きゼロを区別するための記録です。
主な再現例を通常の計算へ読み替えると、次のようになります。

| 操作 | 実際の計算・変換 | 指定した結果と、観測した違い |
|---|---|---|
| f32加算 | `1 + 2^-25` | 1付近のf32の間隔は`2^-23`。上方向への丸めなら次の表現可能な値`1 + 2^-23`へ進むが、モデルは1を返す |
| f32平方根 | `sqrt(2)` | 無理数の約1.414…をf32へ格納する。上方向への丸めで必要な上側の値より、モデルの結果が1段小さい |
| f32→i32 | `1.5`を整数へ変換 | 下方向なら1。モデルはnearest-evenの2を返す |
| f64→f32 | `1 + 2^-24`をf32へ縮小 | 1と`1 + 2^-23`のちょうど中間。上方向なら後者だが、モデルは1を返す |
| i32→f32 | 整数`16,777,217`をf32へ変換 | 近隣のf32は16,777,216と16,777,218。上方向なら後者だが、モデルは前者を返す |

丸め方向は、表現できない真の値をどちら側へ寄せるかの指定です。

| モード | 意味 | `+1.5`を整数へ | `-1.5`を整数へ |
|---|---|---:|---:|
| `nearest_even` | 最も近い値。ちょうど中間なら偶数側 | 2 | -2 |
| `toward_negative` | 負の無限大側へ | 1 | -2 |
| `toward_positive` | 正の無限大側へ | 2 | -1 |
| `toward_zero` | ゼロへ近づく側へ | 1 | -1 |

packed操作は全レーンを計算します。scalar操作では通常は下位1レーンだけを計算し、
残りは保持します。たとえば`_mm_add_ss(a, b)`は下位f32同士を足し、上位3レーンを
`a`からそのまま返します。`_mm_cvtsd_ss(a, b)`は`b`の下位f64をf32へ変換し、
`a`の上位3レーンを保持します。`_mm_sqrt_sd(a, b)`も計算元の下位レーンは`b`、
保持する上位レーンは`a`です。このフレームワークのunary wrapperは`_mm_sqrt_sd(a, a)`と
呼ぶため、1本の入力から平方根と保持レーンを得ます。
一方、`_mm_cvtss_si32`など整数scalarを返す変換では、入力の下位レーンを使い、
上位レーンを結果へコピーする処理はありません。

NaNは「数値でない」状態の表現で、payloadはその仮数部に残る識別用のbit列です。
quiet / signalingの区別もbitで表します。NaNの符号bitは通常の正負の大小を意味しませんが、
`bit_exact`比較では符号もpayloadも比較対象です。
今回のNaN分類は、そのbit列をどの入力から残すか、無効演算でどの符号を生成するかの差です。
このレポートの出力不一致数だけからFP例外フラグの不一致まで主張してはいません。

`+0`と`-0`は通常の数値比較では等しくてもビット表現が異なります。
丸めによるゼロの符号や、min/maxの同値時にどちらの入力を返すかを検査するため、
ここでは区別して記録しています。

### 一致側で検査した操作の例

検査はFPの不一致だけではありません。次は整数・レーン・メモリ・bitcastの計算を説明する
代表例です。この7入力も契約を検証してモデルへ渡し、期待値との一致を確認しました。
**2,159件の不一致集計には含めません。** 期待値を含む全文はJSONの
`matching_operation_examples`に保存しています。全254操作の全入力が一致するという意味ではありません。

配列は左から**lane 0（最下位）、lane 1、…**です。
メモリのbyte列はアドレスの小さい順に記載し、複数byteの整数はlittle endianで読みます。

| 操作 / Guide | 説明する入力と計算 | 確認する結果 | 対応する既存テスト |
|---|---|---|---|
| 8bit整数のwrapと飽和 / [`_mm_add_epi8`](https://www.intel.com/content/www/us/en/docs/intrinsics-guide/index.html#text=_mm_add_epi8)、[`_mm_adds_epi8`](https://www.intel.com/content/www/us/en/docs/intrinsics-guide/index.html#text=_mm_adds_epi8) | 各i8レーンで`127 + 1` | wrapは8bitだけ残り`0x80`（符号付きでは-128）、signed飽和は上限127で止まる。同じ加算でも処理が違う | [wrap](tests/test_integer_boundary_models.py#L135)、[signed / unsigned飽和](tests/test_integer_boundary_models.py#L151) |
| レーン並べ替え / [`_mm_shuffle_epi32`](https://www.intel.com/content/www/us/en/docs/intrinsics-guide/index.html#text=_mm_shuffle_epi32) | 4レーン`[a0,a1,a2,a3]`に`imm8=27`（`0x1b`） | 各2bitの選択値が下位から`3,2,1,0`となり、`[a3,a2,a1,a0]`を返す | [全imm8と保持レーン](tests/test_integer_boundary_models.py#L393) |
| 幅の大きなshift count / [`_mm_sll_epi32`](https://www.intel.com/content/www/us/en/docs/intrinsics-guide/index.html#text=_mm_sll_epi32) | i32レーン`[1,2,-1,-2147483648]`を、countの下位64bit=`2^32+1`だけ左shift | 全レーン0。countを下位32bitの1や、幅に対する剰余へ丸めない | [countの下位64bit](tests/test_integer_boundary_models.py#L342)、[上位countレーン無視](tests/test_integer_boundary_models.py#L361) |
| unaligned load / [`_mm_loadu_si128`](https://www.intel.com/content/www/us/en/docs/intrinsics-guide/index.html#text=_mm_loadu_si128) | bufferのoffset 3から16byteの`00 01 … 0f`を読む | i32のbitsは`[0x03020100,0x07060504,0x0b0a0908,0x0f0e0d0c]`。bufferを変更せず、lane順序とbyte順序を維持する | [offset・guard・別allocation](tests/test_integer_boundary_models.py#L495) |
| unaligned store / [`_mm_storeu_si128`](https://www.intel.com/content/www/us/en/docs/intrinsics-guide/index.html#text=_mm_storeu_si128) | 16byteの`80 81 … 8f`をbufferのoffset 3へ書く | byte位置3〜18だけを置換する。前後のguardと、別の`unrelated` bufferは不変 | [load / storeの全buffer比較](tests/test_integer_boundary_models.py#L495) |
| bitcast / [`_mm_castps_si128`](https://www.intel.com/content/www/us/en/docs/intrinsics-guide/index.html#text=_mm_castps_si128) | f32の`-0`を含む`[0x80000000,0,0,0]`をi32として読み替える | bitsは一切変えない。最初のi32の符号付き解釈は-2147483648で、数値を0へ変換する演算とは異なる | [全128bitを1bitずつ確認](tests/test_adversarial_models.py#L263) |

これらのテストでは、整数の固定幅計算、範囲への明示的なclamp、独立したbyte操作から
期待値を作ります。shuffle / shiftの全imm8を検査する際、公式allowlistを超える値は
明示した検証用拡張として区別します。上のshuffle例`27`自体は公式allowlistの値です。

## 4. 生のfamily集計と影響するcase

| finding family | 記録数 | 公式nearest-even | 丸め拡張 |
|---|---:|---:|---:|
| `fp_boundary_float_to_integer` | 335 | 0 | 335 |
| `fp_boundary_halfway_arithmetic` | 513 | 0 | 513 |
| `fp_boundary_integer_to_float` | 95 | 0 | 95 |
| `fp_boundary_nan_priority` | 54 | 54 | 0 |
| `fp_boundary_narrowing` | 594 | 0 | 594 |
| `fp_boundary_signed_zero_infinity` | 84 | 16 | 68 |
| `fp_boundary_sqrt` | 206 | 0 | 206 |
| `fp_boundary_subnormal_arithmetic` | 107 | 0 | 107 |
| `nan_indefinite_sign` | 12 | 12 | 0 |
| `nan_payload_priority` | 24 | 24 | 0 |
| `rounding_arithmetic` | 56 | 0 | 56 |
| `rounding_float_to_integer` | 37 | 0 | 37 |
| `rounding_integer_to_float` | 18 | 0 | 18 |
| `rounding_narrowing` | 16 | 0 | 16 |
| `rounding_sqrt` | 8 | 0 | 8 |

### Intel Intrinsics Guideとの対応

対応元は[Intel Intrinsics Guide](https://www.intel.com/content/www/us/en/docs/intrinsics-guide/index.html)です。2026-10-16に[公式offline配布](https://cdrdv2.intel.com/v1/dl/getContent/671338)を取得し、version `3.6.9`、データのdate属性`07/12/2024`を確認しました。
次の30行は公式caseのIntel bindingとGuide内の同名intrinsicを照合したものです。prototype全文、実際のwrapper呼び出し、Guideへの検索リンクはJSONの`intrinsic_mappings`にも保存しています。

**case IDの`sse2`名前空間と、個別intrinsicのISAは別です。** 各caseのIntel最低profileは`sse2`ですが、Guide上では`_mm_add_ps`など12行がSSE、残る18行がSSE2です。この接頭辞を個別命令の初出ISAと読み替えないでください。

| case ID | 不一致記録 | Guide intrinsic | ISA | 命令 / header | 計算する内容 |
|---|---:|---|---|---|---|
| `sse2.add.f32x4.default` | 50 | [`_mm_add_ps`](https://www.intel.com/content/www/us/en/docs/intrinsics-guide/index.html#text=_mm_add_ps) | SSE | `ADDPS` / `<xmmintrin.h>` | 4個のf32の加算をレーンごとに行う。 |
| `sse2.add.f32x4.scalar` | 35 | [`_mm_add_ss`](https://www.intel.com/content/www/us/en/docs/intrinsics-guide/index.html#text=_mm_add_ss) | SSE | `ADDSS` / `<xmmintrin.h>` | 下位f32の加算を行い、aの上位3レーンを保持する。 |
| `sse2.add.f64x2.default` | 38 | [`_mm_add_pd`](https://www.intel.com/content/www/us/en/docs/intrinsics-guide/index.html#text=_mm_add_pd) | SSE2 | `ADDPD` / `<emmintrin.h>` | 2個のf64の加算をレーンごとに行う。 |
| `sse2.add.f64x2.scalar` | 35 | [`_mm_add_sd`](https://www.intel.com/content/www/us/en/docs/intrinsics-guide/index.html#text=_mm_add_sd) | SSE2 | `ADDSD` / `<emmintrin.h>` | 下位f64の加算を行い、aの上位1レーンを保持する。 |
| `sse2.cvt.f32x4.i32.scalar` | 52 | [`_mm_cvtss_si32`](https://www.intel.com/content/www/us/en/docs/intrinsics-guide/index.html#text=_mm_cvtss_si32) | SSE | `CVTSS2SI` / `<xmmintrin.h>` | 下位f32を符号付きi32へ変換する。 |
| `sse2.cvt.f32x4.i32x4` | 86 | [`_mm_cvtps_epi32`](https://www.intel.com/content/www/us/en/docs/intrinsics-guide/index.html#text=_mm_cvtps_epi32) | SSE2 | `CVTPS2DQ` / `<emmintrin.h>` | 4個のf32をレーンごとに符号付きi32へ変換する。 |
| `sse2.cvt.f64x2.f32x4` | 378 | [`_mm_cvtpd_ps`](https://www.intel.com/content/www/us/en/docs/intrinsics-guide/index.html#text=_mm_cvtpd_ps) | SSE2 | `CVTPD2PS` / `<emmintrin.h>` | 2個のf64を下位2個のf32へ変換し、上位2レーンを0にする。 |
| `sse2.cvt.f64x2.f32x4.scalar` | 232 | [`_mm_cvtsd_ss`](https://www.intel.com/content/www/us/en/docs/intrinsics-guide/index.html#text=_mm_cvtsd_ss) | SSE2 | `CVTSD2SS` / `<emmintrin.h>` | bの下位f64をaの下位f32へ変換し、aの上位3レーンを保持する。 |
| `sse2.cvt.f64x2.i32.scalar` | 64 | [`_mm_cvtsd_si32`](https://www.intel.com/content/www/us/en/docs/intrinsics-guide/index.html#text=_mm_cvtsd_si32) | SSE2 | `CVTSD2SI` / `<emmintrin.h>` | 下位f64を符号付きi32へ変換する。 |
| `sse2.cvt.f64x2.i32x4` | 102 | [`_mm_cvtpd_epi32`](https://www.intel.com/content/www/us/en/docs/intrinsics-guide/index.html#text=_mm_cvtpd_epi32) | SSE2 | `CVTPD2DQ` / `<emmintrin.h>` | 2個のf64を下位2個のi32へ変換し、上位2レーンを0にする。 |
| `sse2.cvt.f64x2.i64.scalar` | 68 | [`_mm_cvtsd_si64`](https://www.intel.com/content/www/us/en/docs/intrinsics-guide/index.html#text=_mm_cvtsd_si64) | SSE2 | `CVTSD2SI` / `<emmintrin.h>` | 下位f64を符号付きi64へ変換する。 |
| `sse2.cvt.i32.f32x4.scalar` | 22 | [`_mm_cvtsi32_ss`](https://www.intel.com/content/www/us/en/docs/intrinsics-guide/index.html#text=_mm_cvtsi32_ss) | SSE | `CVTSI2SS` / `<xmmintrin.h>` | 符号付きi32をaの下位f32へ変換し、aの上位3レーンを保持する。 |
| `sse2.cvt.i32x4.f32x4` | 69 | [`_mm_cvtepi32_ps`](https://www.intel.com/content/www/us/en/docs/intrinsics-guide/index.html#text=_mm_cvtepi32_ps) | SSE2 | `CVTDQ2PS` / `<emmintrin.h>` | 4個の符号付きi32をレーンごとにf32へ変換する。 |
| `sse2.cvt.i64.f64x2.scalar` | 22 | [`_mm_cvtsi64_sd`](https://www.intel.com/content/www/us/en/docs/intrinsics-guide/index.html#text=_mm_cvtsi64_sd) | SSE2 | `CVTSI2SD` / `<emmintrin.h>` | 符号付きi64をaの下位f64へ変換し、aの上位レーンを保持する。 |
| `sse2.div.f32x4.default` | 73 | [`_mm_div_ps`](https://www.intel.com/content/www/us/en/docs/intrinsics-guide/index.html#text=_mm_div_ps) | SSE | `DIVPS` / `<xmmintrin.h>` | 4個のf32の除算をレーンごとに行う。 |
| `sse2.div.f32x4.scalar` | 53 | [`_mm_div_ss`](https://www.intel.com/content/www/us/en/docs/intrinsics-guide/index.html#text=_mm_div_ss) | SSE | `DIVSS` / `<xmmintrin.h>` | 下位f32の除算を行い、aの上位3レーンを保持する。 |
| `sse2.div.f64x2.default` | 67 | [`_mm_div_pd`](https://www.intel.com/content/www/us/en/docs/intrinsics-guide/index.html#text=_mm_div_pd) | SSE2 | `DIVPD` / `<emmintrin.h>` | 2個のf64の除算をレーンごとに行う。 |
| `sse2.div.f64x2.scalar` | 47 | [`_mm_div_sd`](https://www.intel.com/content/www/us/en/docs/intrinsics-guide/index.html#text=_mm_div_sd) | SSE2 | `DIVSD` / `<emmintrin.h>` | 下位f64の除算を行い、aの上位1レーンを保持する。 |
| `sse2.mul.f32x4.default` | 74 | [`_mm_mul_ps`](https://www.intel.com/content/www/us/en/docs/intrinsics-guide/index.html#text=_mm_mul_ps) | SSE | `MULPS` / `<xmmintrin.h>` | 4個のf32の乗算をレーンごとに行う。 |
| `sse2.mul.f32x4.scalar` | 71 | [`_mm_mul_ss`](https://www.intel.com/content/www/us/en/docs/intrinsics-guide/index.html#text=_mm_mul_ss) | SSE | `MULSS` / `<xmmintrin.h>` | 下位f32の乗算を行い、aの上位3レーンを保持する。 |
| `sse2.mul.f64x2.default` | 74 | [`_mm_mul_pd`](https://www.intel.com/content/www/us/en/docs/intrinsics-guide/index.html#text=_mm_mul_pd) | SSE2 | `MULPD` / `<emmintrin.h>` | 2個のf64の乗算をレーンごとに行う。 |
| `sse2.mul.f64x2.scalar` | 71 | [`_mm_mul_sd`](https://www.intel.com/content/www/us/en/docs/intrinsics-guide/index.html#text=_mm_mul_sd) | SSE2 | `MULSD` / `<emmintrin.h>` | 下位f64の乗算を行い、aの上位1レーンを保持する。 |
| `sse2.sqrt.f32x4.default` | 118 | [`_mm_sqrt_ps`](https://www.intel.com/content/www/us/en/docs/intrinsics-guide/index.html#text=_mm_sqrt_ps) | SSE | `SQRTPS` / `<xmmintrin.h>` | 4個のf32の平方根をレーンごとに求める。 |
| `sse2.sqrt.f32x4.scalar` | 26 | [`_mm_sqrt_ss`](https://www.intel.com/content/www/us/en/docs/intrinsics-guide/index.html#text=_mm_sqrt_ss) | SSE | `SQRTSS` / `<xmmintrin.h>` | 下位f32の平方根を求め、aの上位3レーンを保持する。 |
| `sse2.sqrt.f64x2.default` | 46 | [`_mm_sqrt_pd`](https://www.intel.com/content/www/us/en/docs/intrinsics-guide/index.html#text=_mm_sqrt_pd) | SSE2 | `SQRTPD` / `<emmintrin.h>` | 2個のf64の平方根をレーンごとに求める。 |
| `sse2.sqrt.f64x2.scalar` | 24 | [`_mm_sqrt_sd`](https://www.intel.com/content/www/us/en/docs/intrinsics-guide/index.html#text=_mm_sqrt_sd) | SSE2 | `SQRTSD` / `<emmintrin.h>` | 下位f64の平方根を求め、aの上位1レーンを保持する。 |
| `sse2.sub.f32x4.default` | 48 | [`_mm_sub_ps`](https://www.intel.com/content/www/us/en/docs/intrinsics-guide/index.html#text=_mm_sub_ps) | SSE | `SUBPS` / `<xmmintrin.h>` | 4個のf32の減算をレーンごとに行う。 |
| `sse2.sub.f32x4.scalar` | 33 | [`_mm_sub_ss`](https://www.intel.com/content/www/us/en/docs/intrinsics-guide/index.html#text=_mm_sub_ss) | SSE | `SUBSS` / `<xmmintrin.h>` | 下位f32の減算を行い、aの上位3レーンを保持する。 |
| `sse2.sub.f64x2.default` | 48 | [`_mm_sub_pd`](https://www.intel.com/content/www/us/en/docs/intrinsics-guide/index.html#text=_mm_sub_pd) | SSE2 | `SUBPD` / `<emmintrin.h>` | 2個のf64の減算をレーンごとに行う。 |
| `sse2.sub.f64x2.scalar` | 33 | [`_mm_sub_sd`](https://www.intel.com/content/www/us/en/docs/intrinsics-guide/index.html#text=_mm_sub_sd) | SSE2 | `SUBSD` / `<emmintrin.h>` | 下位f64の減算を行い、aの上位1レーンを保持する。 |

**引数を制限している対応があります。** Guideの`_mm_sqrt_sd(a, b)`は2引数ですが、
[unary wrapper](10_official_suite/intel/f64x2.c#L12)は`_mm_sqrt_sd(a, a)`を呼びます。
下位の平方根も保持する上位レーンも同じ入力`a`に由来します。Guideのprototypeとframework caseのsignatureが、そのまま一致するという意味ではありません。

ARM nativeの追加診断には、上記Python不一致30caseへ含まれない次の2操作もあります。

| ARM診断に対応するcase | Guide intrinsic | ISA | 命令 / header | 計算する内容 |
|---|---|---|---|---|
| `sse2.min.f64x2.default` | [`_mm_min_pd`](https://www.intel.com/content/www/us/en/docs/intrinsics-guide/index.html#text=_mm_min_pd) | SSE2 | `MINPD` / `<emmintrin.h>` | 2個のf64の最小値をレーンごとに選ぶ。 |
| `sse2.max.f64x2.default` | [`_mm_max_pd`](https://www.intel.com/content/www/us/en/docs/intrinsics-guide/index.html#text=_mm_max_pd) | SSE2 | `MAXPD` / `<emmintrin.h>` | 2個のf64の最大値をレーンごとに選ぶ。 |

Guideは演算・型・命令の対応を確認する資料です。今回のNaN符号・payloadの全期待bitを、
Guideだけから導出したとは主張していません。具体的なbitの期待値は、strict SSE2/Rosetta実行で
取得した別の観測証拠に基づきます。Guideの対応表と、実行値の出典を分けて読んでください。

各caseの丸めモード別・契約範囲別の内訳はJSONの`case_counts`、family別は`family_counts`です。

不一致がないcaseを「全入力で正しい」と認定する一覧ではありません。
この記録に含まれる比較・min/max・bitwise・メモリの新規行列では不一致を観測していませんが、
指定した入力集合での結果に限られます。

## 5. ARM native wrapperで観測した6例

こちらはPythonモデルを呼ばず、このarm64 MacでARM C wrapperを実行した結果です。
FPCRは`0x0000000000000000`です。
[`test_arm_wrappers.c`](tests/native/test_arm_wrappers.c#L145) は4,596件の制御検査をassertし、
以下の6例をSSE2/Rosettaの基準値と並べて診断表示します。6例はassert失敗ではないため、
この差が表示されても実行ファイルとCTestの終了コードは0です。

| 入力・操作（f64の結果はlane 0） | SSE2/Rosetta基準 | ARM実測 |
|---|---|---|
| `+inf + -inf` | `0xfff8000000000000` | `0x7ff8000000000000` |
| `qNaN(0x41) + sNaN(0x03)` | `0x7ff8000000000041` | `0x7ff8000000000003` |
| `min(qNaN(0x41), 1)` | `0x3ff0000000000000` | `0x7ff8000000000041` |
| `max(+0, -0)` | `0x8000000000000000` | `0x0000000000000000` |
| qNaN→i32 | `0x80000000` | `0x00000000` |
| +inf→i32 | `0x80000000` | `0x7fffffff` |

ここでのhexはIEEEレーンまたはi32の整数ビット表現です。
ARMの[`vaddq_f64` / `vminq_f64` / `vmaxq_f64`](10_official_suite/arm/f64x2.c#L6)や
[`FCVTNS`の変換helper](10_official_suite/arm/neon_helpers.h)を使う経路は、
これらの差をSSE2の値へ補正していません。
観測した命令セマンティクスの差として扱い、Pythonモデルの不具合数へ加算しません。
全254ケースを網羅的に比較した6件、という意味でもありません。

## 6. RISC-V / RVVのQEMU比較

[`test_riscv_suite.py`](tests/native/test_riscv_suite.py) は254個のraw C wrapperに
各3入力を与え、x86_64 SSE2/Rosetta実行とRV64/RVV 1.0のQEMU実行を比較します。
同じRVV ELFをVLEN 128 / 256で実行しています。
取得元summaryのツール情報はClang 22.1.8、Apple Clang 21.0.0、QEMU 11.1.0です。

| VLEN | 比較入力数 | 一致 | 不一致 |
|---|---:|---:|---:|
| 128 | 762 | 750 | 12 |
| 256 | 762 | 750 | 12 |

両VLENで不一致のsample名と結果は同一です。**異なる不一致sampleは12個**で、
24種類の差を観測したわけではありません。
12個はadd / sub / mul × f32 / f64 × packed / scalarのNaNを含む入力`#2`です。
具体的なsample名、全operand、期待値、実測値はJSONの`native_observations.riscv.differences`に
12例すべて保存しています。

例として`add_f32x4#2`の入力は次のとおりです。

```text
a = [0x7fc00042, 0x7f800035, 0x7f800000, 0xff800000]
b = [0x7f800035, 0x7f800000, 0xff800000, 0x7fc00042]
SSE2基準lane = [0x7fc00042, 0x7fc00035, 0xffc00000, 0x7fc00042]
RVV実測lane  = [0x7fc00000, 0x7fc00000, 0x7fc00000, 0x7fc00000]
```

元summaryの同じ結果は、SSE2=`4200c07f3500c07f0000c0ff4200c07f`、
RVV=`0000c07f0000c07f0000c07f0000c07f`と記録されています。
これらは**メモリ順のbyte列**です。little endianの4byteごとにf32レーンとして読むもので、
文字列全体を一つの数値hexやULP距離として読んではいけません。

算術経路は [`riscv/f32x4.c`](10_official_suite/riscv/f32x4.c#L319)、
[`riscv/f64x2.c`](10_official_suite/riscv/f64x2.c#L6)、
[`rvv128.h`](10_official_suite/riscv/rvv128.h#L111) のRVV intrinsicです。
観測したNaNはcanonical NaNで、SSE2基準のpayload・符号と異なります。
他の入力、全丸め、全FP例外への一般化はできません。
これはQEMU上のRISC-V機械語実行で、RISC-V実機やLinux native runnerの検証ではありません。
比較ツールは不一致を残して終了コード1を返します。

## 7. OpenPOWERとencoded ABIの範囲

OpenPOWER / ppc64leはPOWER8 + AltiVec + VSX向けの6オブジェクトのcross buildを確認した範囲です。
OpenPOWER実機の出力をこの記録では測定していません。JSONのOpenPOWER `differences: []`は
未測定を表し、「不一致0件」や「すべて一致」を意味しません。

4対象に254個ずつあるraw wrapperと、canonical encoded ABIの実行範囲は異なります。
共通ABIに接続済みなのはadd / set1 / shuffleの3例です。
RISC-V encoded adapterはcross compile済みですが、Linux libc / fenvとのlink・実行は未検証です。
cross buildやraw wrapperの実行だけで、仕様に適合するnative結果manifest一式が完成したとは
主張しません。詳細は [`NATIVE_STATUS.md`](NATIVE_STATUS.md) を参照してください。

## 8. 再現手順

モデル不一致の小さい再現は、リポジトリルートで次のように実行できます。
これらはこのarm64環境の現行モデルでは不一致を表示して終了コード1になります。
Pythonのホスト依存処理があるため、別CPU・別Pythonで同じ件数になる保証はありません。

```sh
PYTHONPATH=src:tests .venv/bin/python -m unittest \
  test_adversarial_models.AdversarialModelTests.test_add_opposite_infinities_returns_negative_indefinite_nan
PYTHONPATH=src:tests .venv/bin/python -m unittest \
  test_adversarial_models.AdversarialModelTests.test_requested_rounding_in_arithmetic
.venv/bin/python -m unittest discover -s tests -p test_fp_boundary_models.py
```

全体を新しい出力先へ記録する場合：

```sh
mkdir -p build
report_run=$(mktemp -d build/mismatch-run.XXXXXX)
PYTHONPATH=src .venv/bin/python -m equilane.quality_runner \
  --project-root . --output "$report_run/quality" \
  > "$report_run/summary.stdout.json"
```

`quality_runner`は既存の出力ディレクトリを受け付けないため、毎回新しい親領域を確保し、
未作成の`quality`を出力先にしています。stdoutはその外へ保存します。
このコマンドは固定スナップショットを書き換えません。通常のquality実行ではSSE2 probeを
再実行せず、保存した期待値を使います。NaN・丸めの期待値を独立に再取得して検証する
strict FPのprobeビルドと`--verify`の手順は [`BUG_HUNT.md`](tests/BUG_HUNT.md) にあります。

固定記録から代表入力の全文を取り出す例：

```sh
.venv/bin/python - <<'PY'
import json
from pathlib import Path
snapshot = json.loads(Path('tests/data/mismatch-report-2026-10-16.json').read_text())
finding = next(row for row in snapshot['representative_findings']
               if row['report_example_id'] == 'rounding_arithmetic')
print(json.dumps(finding, ensure_ascii=False, indent=2))
PY
```

ARM raw wrapperの再実行、RVVの再取得・比較、全対象のcross build：

```sh
cmake -S . -B build/native -DBUILD_TESTING=ON -DCMAKE_BUILD_TYPE=Debug
cmake --build build/native --parallel
./build/native/ioitf_arm_wrappers_selftest
.venv/bin/python tests/native/test_riscv_suite.py
./10_official_suite/cross-compile.sh
```

ARMの実行はarm64環境、RVVの取得はLLVM ClangとQEMU、SSE2参照実行はx86_64実行環境を要します。
Apple Siliconで参照を実行する場合はRosettaを使います。
RVV比較が終了コード1になっても、不一致を記録したsummaryは生成されます。

## 9. 証拠の同一性と未確認事項

| 原資料 | SHA-256 |
|---|---|
| `.equilane/test-expansion-20261016-final/summary.json` | `da72bfb0ec9ab62eff65c9b4f5ad24ff22b60d28f5fcf44b90872ff1ba3cce34` |
| `build/riscv-native-suite/summary.json` | `2fbb6f6d854628047c3df69b054c97a753effd1f64d6cec0e45b937cc3fb8926` |
| モデル本体 `src/equilane/casepack_families.py` | `091a303d171d74cb66e847a8af0d42ddb3505f8402a8fbf849e003098b7ee9b0` |
| Intel公式offline Guide ZIP | `4de3a0c59a944f153f6dc47cfb2d0e455ef16e54a8878f8882db82237947f5e7` |
| 同ZIP内のGuideデータ `data.js` | `67307df98241728ff14ea2ad4102a3316b4674d7e4629a7293e39952eec59d69` |

JSONの`evidence`には、原summaryのhash、検証元summaryが記録したモデル・oracle sourceのhash、
本レポートで参照したソースのhash、RVV生成コード・wrapperの元hashを保存しています。
元のRVV summaryにあった絶対パスはリポジトリ相対パスへ変換しました。
hashはファイル内容の同一性を示し、実行の成功や測定時刻を単独で証明するものではありません。

この報告が確認していないものは、OpenPOWER実機の結果、RISC-V実機の結果、
物理Intel CPUを基準にしたcross-host conformance、254操作すべてのencoded ABI実行、
全入力・全CPU設定での等価性です。ARMの6診断とRVVの12差分は異なる入力集合なので、
件数を比較してアーキテクチャの優劣や不一致率を出すこともできません。
固定記録と、将来のモデル修正後の再実行結果は別の証拠として扱ってください。
