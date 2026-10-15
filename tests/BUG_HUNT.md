# バグ探索用テスト

`test_adversarial_models.py` は、公式 case pack を読み込み、contract に適合する
入力を検証してから、モデルの結果を独立したビット列の期待値と比較します。
丸めモードの検証では、公式ケースを元に回帰期待値付きの検証用契約を明示的に作成します。
両 fixture が同じモデルを使うために見逃すバグを対象にしています。
期待値の計算には、対象モデルもホストの浮動小数点演算も使いません。

プロジェクトのルートで実行します。追加の Python 依存関係はありません。

```sh
.venv/bin/python -m unittest discover -s tests -p test_adversarial_models.py -v
```

## 今回追加した探索ケース

公式254 case packの命令契約は維持し、独立した期待値を持つ入力パターンと、
フレームワークの拒否・比較テストを追加しました。命令の種類数と入力パターン数は別です。

| 追加した検証 | 件数 | 内容 |
|---|---:|---|
| 整数モデルの入力・期待値照合 | 17,090 | wrap、飽和、符号、乗算、全imm8、64bit shift count、lane順序、メモリ境界 |
| 浮動小数点モデルの入力・期待値照合 | 10,708 | 107命令ケース、12系統、4丸めモード、NaN、非正規数、丸め中間値、変換境界 |
| schema・artifact・replay・比較テスト | 291メソッド | 閉じたschema、改ざん、symlink、全status組合せ、NaN方針、ULP、厳密な許容誤差 |
| CLI設定選択の回帰テスト | 4メソッド | 新旧設定ファイルの自動選択、quality有無、設定ファイル明示の対照 |

ARM/RISC-V追加前の全体検証は495テストメソッド、独立したモデル出力照合29,314件、
記録した異なる入力ID29,292件でした。モデル不一致2,159件、その他のassertion失敗13件、
実行エラー0件、skip 0件です。モデル不一致のうち106件は公式nearest-even契約、
2,053件は検証用の丸め拡張です。C sanitizerと12オブジェクトのcross buildは成功しています。

整数の期待値は、無限精度の整数演算、固定幅への明示的な符号化、独立したbyte操作で
計算します。モデルの演算helperや比較エンジンから期待値を作りません。
shuffleとshiftの全imm8を検査する際は、元の許可値を超える入力だけを
`validated_immediate_extension` として契約検証します。公式の即値allowlistは変更していません。

浮動小数点の期待値は、モデルをimportしない別のCプログラムから取得しました。
各packed命令にはレーンごとに異なる値、scalar命令には左右で異なる上位poisonレーンを渡します。
新corpus内の10,708入力はすべて異なり、そのうち10,704入力は以前の504行corpusにありません。
4入力は以前の対照ケースと共有しています。`-O0` と `-O2` のstrict FPビルドで全行を再取得し、
保存値との完全一致を確認しています。

追加FPテストは、arm64 Darwin / Python 3.14.7で1,988パターンのモデル不一致を再現しました。
このうち公式nearest-even契約での70件は、NaN payload選択54件と無効演算のNaN符号16件です。
残る1,918件は、対応丸めモードを追加した検証用契約での不一致です。
既存のNaNと丸め未反映の不具合を、より多くの入力で再現したもので、
不一致の件数を根本原因の数とは扱いません。整数17,090照合は全件成功しています。

### 新たに再現した不備と検証強化用ケース

- NaN比較方針の4項目と `signed_zero` に配列・辞書を渡すと、文字列型の検査より先に
  set membershipを実行し、`ValidationError` の代わりに `TypeError` が漏れます。10テストで再現します。
- `validate_result_record(..., input_record=...)` 単独では、指定した入力と異なる、形式上正しい
  `input_id` を受理します。1テストで再現します。artifact検証と比較の上位経路はこの不一致を拒否するため、
  通常の比較pipelineが偽の一致を報告するという意味ではありません。
  仕様が要求する上位経路の検証とは区別し、下位APIの検証強化を求めるテストとして残しています。
- `check --quality` の設定自動選択後の `Path(None)` は、ARM/RISC-V対応と合わせて修正しました。
  新旧設定ファイルの省略・明示経路を回帰テストで確認しています。残るその他のassertion失敗は11件です。

### ARM/RISC-V拡張で見つけて修正した問題

- ARM/RVの浮動小数点差分に、nested diagnosticの`openpower`ラベルが残っていました。
  実際の対象名へ揃え、両対象の不一致を再現するテストを追加しました。
- 空の比較集合で対象名の検証が抜けていました。Intel自身・不明な名前・`None`を拒否します。
- 複数対象のHTMLで、重複した対象・summaryと異なる対象・異なる入力件数を受け付け、
  Intel共通経路の実行数が矛盾する場合がありました。これらを拒否する回帰テストを追加しました。
- PPC旧bundleと旧`ioitf`コマンドの互換性、ARM/RV bundleの再読み込み・再照合、
  ロード後のファイル改ざんからのsnapshot独立性も確認しています。

Native側のテストは [Native status](../NATIVE_STATUS.md) を参照してください。
ARMでは4596検査、RVVでは同じELFのVLEN 128/256実行と、254操作×3入力の
実測SSE2比較を行います。RVVのNaN差異12件はアーキテクチャの観測結果として記録し、
portableモデルの不具合とは区別しています。

失敗は `expectedFailure`、skip、期待値の差し替えで抑制していません。
schema拒否の例外漏れは、実際の例外を捕捉したうえで `ValidationError` であることをassertし、
テスト自体の偶発的な実行エラーと区別しています。

```sh
.venv/bin/python -m unittest discover -s tests -p test_integer_boundary_models.py -v
.venv/bin/python -m unittest discover -s tests -p test_fp_boundary_models.py -v
.venv/bin/python -m unittest discover -s tests -p test_protocol_adversarial.py -v
.venv/bin/python -m unittest discover -s tests -p test_comparison_adversarial.py -v
.venv/bin/python -m unittest discover -s tests -p test_cli_adversarial.py -v
```

FP期待値を独立に再取得する手順：

```sh
clang -arch x86_64 -O0 -msse2 -ffp-model=strict -Wall -Wextra -Werror \
  tests/native/probe_fp_boundary.c -o /tmp/equilane-fp-boundary-probe
.venv/bin/python tests/build_fp_boundary_oracles.py \
  --probe /tmp/equilane-fp-boundary-probe \
  --execution-context 'x86_64 SSE2 under Rosetta on arm64 Darwin' \
  --build-command 'clang -arch x86_64 -O0 -msse2 -ffp-model=strict -Wall -Wextra -Werror tests/native/probe_fp_boundary.c -o /tmp/equilane-fp-boundary-probe' \
  --verify
```

`-O2` に変えたprobeでも同じ `--verify` が成功します。通常のPythonテストにコンパイラやRosettaは不要です。
corpusは取得環境、コンパイラ、build command、probe・ソース・全期待値のSHA-256を保存します。
これはRosetta上のSSE2記録であり、Intel / OpenPOWER実機間のconformance evidenceではありません。
仕様の参照元は [Intel Intrinsics Guide](https://www.intel.com/content/www/us/en/docs/intrinsics-guide/index.html)、
[Intel SDM](https://www.intel.com/content/www/us/en/developer/articles/technical/intel-sdm.html)、
[Floating-Point Reference Sheet](https://www.intel.com/content/www/us/en/developer/articles/technical/floating-point-reference-sheet-for-intel-architecture.html) です。

## 今回の追加前に確認していた結果

arm64 Darwin / Python 3.14.7 で **171パターンの出力不一致**を再現しました。
初回の36件に、丸めモード未反映の135件が追加されています。
出力照合は1,516件、テストは出力検証17メソッドとoracle出典の整合性検証1メソッドです。
不一致件数はバグの個数ではありません。根本原因はNaNの符号・NaN payload選択・
丸めモード未反映の3系統で、最後の系統が5つの実装経路に現れています。

| 検出した差 | 不一致パターン | 契約の範囲 |
|---|---:|---|
| 無効演算のNaN符号 | 12 | 公式 nearest-even |
| NaN payload の選択 | 24 | 公式 nearest-even |
| 算術で指定した丸めが反映されない | 56 | 検証用の丸め拡張 |
| 平方根で指定した丸めが反映されない | 8 | 検証用の丸め拡張 |
| 浮動小数点→整数で指定した丸めが反映されない | 37 | 検証用の丸め拡張 |
| f64→f32で指定した丸めが反映されない | 16 | 検証用の丸め拡張 |
| 整数→浮動小数点で指定した丸めが反映されない | 18 | 検証用の丸め拡張 |

丸めを変えた場合の具体例：

| 操作・指定 | 入力 | SSE2期待値 | 現モデル |
|---|---|---|---|
| f32 add / toward_positive | `0x3f800000 + 0x33000000` | `0x3f800001` | `0x3f800000` |
| f32 sqrt / toward_positive | `0x40000000` (=2) | `0x3fb504f4` | `0x3fb504f3` |
| f32→i32 / toward_negative | `0x3fc00000` (=1.5) | `0x00000001` | `0x00000002` |
| f64→f32 / toward_positive | `0x3ff0000010000000` | `0x3f800001` | `0x3f800000` |
| i32→f32 / toward_positive | `0x01000001` (=16,777,217) | `0x4b800001` | `0x4b800000` |

原因箇所は `casepack_families.py` の `float_binary_case`、`sqrt_case`、
`conversion_case`、`scalar_conversion_case`、`float_to_scalar_case` の実行経路です。
入力の `environment.rounding` を使用せず、ホストの演算やnearest-even固定の補助関数を使います。
公式ケースの宣言はnearest-evenのまま変更していません。この135件は、対応モードを
拡張する際の再現ケースであり、公式nearest-evenで同じ差が出るという主張ではありません。

504組の丸め基準値は `tests/data/rounding-oracles.json` に保存しています。
Pythonモデルを一切呼ばない `tests/build_rounding_oracles.py` がSSE2 probeから取得しました。
4モードの正負、halfway、underflow、overflow、整数境界、scalar上位レーンを検証します。
nearest-evenと、丸めモードに影響されないcvttは対照ケースです。
同じ504組はstrict FPの `-O0` / `-O2` ビルドで再取得し、一致を確認しました。

さらに6種類のbitcastで全128ビットを1ビットずつ移動する768照合と、
3種類のunaligned storeでoffset 0〜15と未使用buffer・前後guardを確認する48照合も追加しました。
これら816照合では不一致は出ていません。

## 初回に確認したNaNの不一致

arm64 macOS の Python で、10テストメソッド中4つが失敗し、6つが成功しました。
失敗した subtest は36件で、原因は次の2種類です。実装の修正は含めていません。

| 入力例（f64） | SSE2の期待値 | 現在のarm64モデル |
|---|---|---|
| `+inf + -inf` / `inf - inf` / `0 * inf` | `0xfff8000000000000` | `0x7ff8000000000000` |
| `0x7ff8000000000042 + 0x7ff0000000000001` | `0x7ff8000000000042` | `0x7ff8000000000001` |

1. 無効演算で生成する NaN の符号が違います。add / sub / mul、f32 / f64、
   packed / scalar の12組で再現します。
2. 第一引数が quiet NaN、第二引数が signaling NaN のとき、第二引数の payload が
   残ります。add / sub / mul、f32 / f64、packed / scalar と第一引数の正負を
   組み合わせた24組で再現します。div と、NaN の順序を逆にした対照ケースは成功します。

原因箇所は `src/equilane/casepack_families.py` の `float_binary_case.execute` です。
add / sub / mul はビット列を Python の float に変換してホストで演算します。
これらの公式 contract は入力を除外せず `bit_exact` を要求するため、NaN 同士でも
この差は不一致です。両側が同じモデルを実行する通常の fixture 比較では見逃します。

`expectedFailure` や skip で失敗を隠していません。全体の unittest discover にも
含まれるため、未修正の arm64 環境では全体テストも非0で終了します。
ホスト依存のバグなので、x86_64 の Python では同じ失敗数になるとは限りません。

## レポートへの反映

```sh
.venv/bin/python -m equilane check --project equilane.toml --quality --showcase-report
```

`showcase.html` の「独立したモデル検証と失敗箇所」に、モデル出力の不一致、その他の
assertion 失敗、テスト実行エラーを分けて表示します。fixture 比較が PASS でも、
quality 検証に失敗した場合は冒頭を `QUALITY CHECK FAILED` と表示します。
今回の未修正環境ではコマンドは非0で終了しますが、レポートは生成されます。
`--quality` を付けなかったレポートでは、独立したモデル検証は「未実施」です。

出力不一致は、公式または明示した検証用 contract に適合した入力でモデル実行が成功した後、
`equilane.compare` を通さず固定期待値と直接照合した出力の差です。
**比較・集計処理の誤判定ではなく、Python portable model の出力不一致**として分類します。
このモデルもリポジトリ内の開発用実装であり、OpenPOWER 実機のバグが見つかったという
意味ではありません。また、今回の結果だけでフレームワーク全体の無欠陥を主張しません。

機械可読の記録は `quality/summary.json` の `gates.python_coverage` にあります。
`--quality` では独立テストを実行しますが、カバレッジの計測は既定でオフです。
明示的に `--coverage` を加えた場合だけソースをtraceします。未計測時の割合は `null` で、
`coverage_collected: false` と記録します。`python_coverage` キーは既存成果物との互換性のため維持しています。

- `failure_classification`: モデル不一致・その他の assertion 失敗・実行エラーの件数。
- `model_output_mismatches`: 各入力、入力ID、テストID、期待値、実測値、照合方法。
- `execution_environment`: 実際に検証した OS、CPU アーキテクチャ、Python バージョン。
- `evidence_sources`: oracle テストとモデル等のソースの SHA-256。
- `model_findings_by_family` / `model_findings_by_contract_scope`: 原因別と契約範囲別の件数。
- `model_oracle_checks_run`: 実施した出力照合数（テストメソッド数とは別）。
- `model_oracle_reference`: SSE2記録数、取得環境、probeと生成スクリプトのSHA-256。
- `model_oracle_references`: 新旧corpusと独立した整数期待値の出典一覧。
- `model_oracle_tracked_unique_inputs`: 入力IDを記録した、重複のないモデル検証入力数。
- `test_assertion_failures` / `test_execution_errors`: モデル不一致以外のテストID、例外名、メッセージ。

NaN oracle の出典はこの文書の SSE2 probe 確認記録です。
quality 検証時には probe を再実行せず、固定した期待値を使用します。

保存した丸め基準値を独立に再確認するには、上記probeをビルドした後に実行します。

```sh
.venv/bin/python tests/build_rounding_oracles.py \
  --probe /tmp/equilane-probe-sse2-nan \
  --execution-context 'x86_64 under Rosetta on arm64 Darwin' --verify
```

`--verify` は保存済み504組と再取得した結果の一致を確認します。
基準値の更新が必要な場合だけ `--verify` を外します。ソース変更後に古いoracleを
無言で使わないよう、通常テストでも取得元ソースのSHA-256を確認しています。

端末の品質メトリクスも、`tests_run` を `passed` と表示していた表記を
`executed` に修正しました。これは表示の問題で、上記36件のモデル出力の差とは
別の問題です。実行数と成功数を混同しないための修正です。

## 追加した境界条件

- min / max：±0 の入力順序、NaN の入力順序、第二引数の signaling NaN をそのまま返すこと。
- scalar 演算：上位レーンの signaling NaN、負のゼロ、payload をビット単位で保持すること。
- f64 → f32：丸め中間点の直前・一致・直後。偶数丸め、ゼロへの underflow、
  subnormal / normal 境界、overflow、負数、未使用レーンのゼロ化。
- 可変シフト：幅以上の count、`2^32`、`2^32 + 1`、`2^63`、`2^64 - 1`、
  count の上位64ビットを無視すること。
- `_mm_madd_epi16`：`(-32768 * -32768) * 2` が `0x80000000` に wrap することと、
  隣接するレーン対が混ざらないこと。

## NaN の期待値を SSE2 で確認する

`native/probe_sse2_nan.c` は、実行時に渡した入力で SSE2 intrinsic を直接呼びます。
FP例外は mask、丸めは nearest-even、FTZ / DAZ は無効に設定します。
Python モデルには依存しません。

Apple Silicon + Rosetta の場合：

```sh
clang -arch x86_64 -std=c11 -O0 -msse2 -ffp-model=strict \
  tests/native/probe_sse2_nan.c -o /tmp/equilane-probe-sse2-nan
```

x86_64 上の Clang では `-arch x86_64` を外します。

```sh
/tmp/equilane-probe-sse2-nan f64 add 0x7ff0000000000000 0xfff0000000000000
# 0xfff8000000000000
/tmp/equilane-probe-sse2-nan f64 add 0x7ff8000000000042 0x7ff0000000000001
# 0x7ff8000000000042
/tmp/equilane-probe-sse2-nan f32 mul 0x00000000 0x7f800000
# 0xffc00000
```

今回の期待値確認は Rosetta 上の x86_64 バイナリ実行です。
Intel / OpenPOWER 実機間の native conformance evidence ではありません。
通常の Python テスト実行にコンパイラや Rosetta は不要です。
