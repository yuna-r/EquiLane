# EquiLane

**Intrinsics Equivalence Testing**

Intel Intrinsics と OpenPOWER の実装に同じ入力を渡し、結果の違いを調べるツールです。
254のケースと、結果をまとめるHTMLレポートを収録しています。

![EquiLane report](assets/showcase-report-preview.svg)

レポートには比較結果、ケース一覧、独立したモデル検証の不一致をまとめています。
ケースを検索して絞り込めます。外部の画像やフォントに依存しない、1ファイルのHTMLです。

## 使う

Python 3.11以上が必要です。

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e .

equilane serve
```

[localhost:8765](http://localhost:8765) を開くと、新しい検証を実行し、
過去のレポートも見返せます。出力先は `.equilane/checks/` です。

端末から直接レポートを作る場合：

```sh
equilane check --showcase-report
```

最後に表示される `showcase_report` のHTMLをブラウザで開きます。

独立したモデル検証、coverage、sanitizer、cross buildも含める場合：

```sh
equilane check --quality --showcase-report
```

現在のportable modelには、NaNと丸め処理の既知の不一致があります。
`--quality` はこれらをレポートに記録し、検証失敗として終了します。
詳細は [検出済みの不一致](tests/BUG_HUNT.md) にあります。

入力を増やして比較する場合：

```sh
equilane check --profile standard --count-per-case 1000 --jobs auto --showcase-report
```

## ケースを追加する

```sh
./10_official_suite/new-case my-add-i32x4 +
```

生成されるPythonファイル1つに、入力の契約とモデルの宣言をまとめています。
ケースの書き方や実機adapterについては [開発・検証ガイド](DEVELOPMENT.md) を参照してください。

## 検証の範囲

現在の比較はローカルの開発用fixtureです。Intel / OpenPOWER実機間の一致を示す証拠ではありません。
x86_64 / ppc64leのcross buildには対応していますが、ppc64le実機runnerは未実装です。

従来の `ioitf` コマンド、Python import、`ioitf.toml` も引き続き使えます。
成果物の `ioitf.*` 識別子とC ABIは、既存データとの互換性のため維持しています。

原案：[daisukeokaoss](https://github.com/daisukeokaoss) ·
[仕様書](https://github.com/daisukeokaoss/ToukaSpecification/blob/a4d888dfdca53d4335a6051bea9df8b9fda9bf35/README.md) · [Native status](NATIVE_STATUS.md) · [Apache-2.0](LICENSE)

仕様書submoduleには元のMITライセンスが適用されます。著作権表示は [NOTICE](NOTICE) を参照してください。
