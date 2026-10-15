"""Detailed, self-contained view of recorded model and native differences.

Rendering consumes recorded evidence. It does not rerun a model, CPU probe,
quality gate or emulator, and never infers native results from fixture labels.
"""

from __future__ import annotations

from datetime import datetime, timezone
from html import escape
from pathlib import Path
from typing import Mapping

from .canonical import JSONValue, atomic_write, dumps, require_object, sha256_bytes
from .errors import ValidationError


_STYLE = """
:root { color-scheme: light; --paper:#faf7f0; --panel:#fffdf9; --line:#e6dfec;
  --text:#393244; --muted:#696273; --violet:#70538f; --red:#a83049; }
* { box-sizing:border-box; }
body { margin:0; background:var(--paper); color:var(--text);
  font:15px/1.8 system-ui, 'Hiragino Sans', 'Yu Gothic', sans-serif; }
main { width:min(1120px, calc(100% - 40px)); margin:32px auto 60px; }
header, section.native-section { padding:28px; background:var(--panel);
  border:1px solid var(--line); border-radius:22px; margin-bottom:24px; }
h1 { font-size:clamp(26px, 4vw, 42px); line-height:1.3; margin:12px 0; }
h2 { line-height:1.5; } h3 { color:var(--violet); }
.eyebrow { font-size:12px; color:var(--violet); letter-spacing:.08em; }
.muted, small { color:var(--muted); }
nav { display:flex; flex-wrap:wrap; gap:12px; margin:20px 0; }
nav a { color:var(--violet); background:#f0eaf7; border-radius:10px; padding:6px 12px; }
a:focus-visible, summary:focus-visible { outline:3px solid var(--violet); outline-offset:3px; }
code, pre { font-family:ui-monospace, 'SFMono-Regular', Consolas, monospace; }
pre { white-space:pre-wrap; overflow-wrap:anywhere; background:#f4f0f7; padding:12px;
  border-radius:8px; max-width:100%; font-size:12px; }
.table-scroll { overflow:auto; }
table { border-collapse:collapse; width:100%; font-size:13px; }
th, td { border-bottom:1px solid var(--line); padding:10px; text-align:left; vertical-align:top; }
th { background:#f0eaf7; } td pre { margin:0; min-width:135px; }
details { border:1px solid var(--line); border-radius:10px; padding:12px; margin:12px 0; }
summary { cursor:pointer; font-weight:650; overflow-wrap:anywhere; }
.unknown { padding:12px; background:#fcf2df; border-radius:10px; }
.provenance { font-size:12px; overflow-wrap:anywhere; }
@media (max-width:600px) { main { width:calc(100% - 20px); } header, section.native-section { padding:16px; } }
@media print { body { background:white; } nav { display:none; } main { width:100%; }
  header, section.native-section { break-inside:avoid; } }
"""


def _text(value: object) -> str:
    return escape(str(value), quote=True)


def _encoded(value: JSONValue) -> str:
    return _text(value if isinstance(value, str) else dumps(value))


def python_summary(summary: Mapping[str, JSONValue]) -> dict[str, JSONValue]:
    """Read a quality-runner summary or its enclosing quality gate report."""

    root = require_object(summary, "quality summary")
    if "gates" in root:
        gates = require_object(root["gates"], "quality summary.gates")
        root = require_object(gates.get("python_coverage"), "quality summary.gates.python_coverage")
    rows = root.get("model_output_mismatches")
    if not isinstance(rows, list):
        raise ValidationError("quality summary requires a model_output_mismatches array")
    if any(not isinstance(row, dict) for row in rows):
        raise ValidationError("model_output_mismatches must contain objects")
    classification = root.get("failure_classification")
    if isinstance(classification, dict) and "portable_model_output_mismatches" in classification:
        count = classification["portable_model_output_mismatches"]
        if type(count) is not int or count != len(rows):
            raise ValidationError("model mismatch count does not match recorded findings")
    return root


def _native_section(observations: Mapping[str, JSONValue] | None) -> str:
    from .intrinsic_guide import intrinsic_for_case

    if observations is None:
        return '''<section class="native-section" id="native-observations">
          <h2>CPU向け実装で観測した差</h2><p class="unknown">この出力にはnative観測の記録を添付していません。
          Pythonモデルの結果から各CPUの一致・不一致は判定しません。</p></section>'''
    root = require_object(observations, "observation snapshot")
    native = require_object(root.get("native_observations"), "observation snapshot.native_observations")
    cards = []
    labels = {"arm": "ARM64 NEON", "riscv": "RISC-V64 RVV", "openpower": "ppc64le OpenPOWER"}
    kinds = {"native": "実機で実行", "emulated": "エミュレーターで実行",
             "cross_build_only": "クロスビルドのみ"}
    for role, value in native.items():
        item = require_object(value, f"native observations.{role}")
        differences = item.get("differences", [])
        if not isinstance(differences, list) or any(not isinstance(row, dict) for row in differences):
            raise ValidationError(f"native observations.{role}.differences must be an array of objects")
        def operation_label(row):
            case_id = row.get("case_id", row.get("label", "未記録"))
            entry = intrinsic_for_case(case_id)
            guide = (f'<br><a href="{_text(entry["guide_url"])}" target="_blank" rel="noopener noreferrer">'
                     f'{_text(entry["name"])}</a> / {_text(entry["isa"])}' if entry else "")
            label = f'<br>{_text(row["label"])}' if "label" in row and row["label"] != case_id else ""
            return _text(case_id) + guide + label
        rows = "".join(
            f'<tr><td>{operation_label(row)}</td>'
            f'<td><pre>{_encoded(row.get("input"))}</pre></td>'
            f'<td><pre>{_encoded(row.get("expected"))}</pre></td>'
            f'<td><pre>{_encoded(row.get("actual"))}</pre></td></tr>'
            for row in differences
        )
        table = (f'''<div class="table-scroll"><table><thead><tr><th>操作・入力例</th>
          <th>入力</th><th>参照値</th><th>観測値</th></tr></thead><tbody>{rows}</tbody></table></div>'''
                 if differences else '<p class="unknown">差の観測記録はありません。未実行の対象の一致を意味しません。</p>')
        cards.append(f'''<article data-observation-target="{_text(role)}">
          <h3>{_text(labels.get(role, role))}</h3>
          <p>{_text(kinds.get(str(item.get("execution_kind")), "実行種別未記録"))}。
          {_text(item.get("coverage", "検証範囲未記録"))}</p>
          <p>比較基準：{_text(item.get("reference", "未記録"))}</p>
          <p>表記：{_text(item.get("representation", "記録の表記に従います"))}</p>
          <details><summary>実行環境と検査件数</summary>
            <pre>{_encoded(item.get("environment", {}))}</pre>
            <pre>{_encoded(item.get("sample_counts", {}))}</pre></details>{table}
          <details><summary>観測の出典と再現方法</summary>
            <p>観測日：{_text(item.get("capture_date", "未記録"))}</p>
            <pre>{_encoded(item.get("sources", []))}</pre>
            <pre>{_encoded(item.get("reproduce", []))}</pre></details></article>''')
    date = root.get("capture_date", root.get("recorded_at", root.get("date", "未記録")))
    return f'''<section class="native-section" id="native-observations">
      <h2>CPU向け実装で観測した差</h2>
      <p>添付した観測スナップショットの記録日：{_text(date)}。
      以下はPythonモデル検証とは別の実行記録です。このHTMLの生成時にCPU検証を再実行していません。
      観測した差だけでは、ISAの仕様差と移植実装の修正要否を確定しません。</p>
      {''.join(cards)}</section>'''


def render_mismatch_report(
    summary: Mapping[str, JSONValue],
    *,
    observations: Mapping[str, JSONValue] | None = None,
    generated_at: datetime | None = None,
) -> str:
    """Render a report without attributing portable-model findings to an ISA."""

    from .mismatch_details import render_mismatch_details
    from .intrinsic_guide import GUIDE_URL, catalog_provenance

    python = python_summary(summary)
    generated_at = generated_at or datetime.now(timezone.utc)
    stamp = (generated_at.astimezone(timezone.utc) if generated_at.tzinfo else
             generated_at.replace(tzinfo=timezone.utc)).strftime("%Y-%m-%d %H:%M:%S UTC")
    digest = sha256_bytes(dumps(dict(summary)).encode("utf-8"))
    observation_digest = (sha256_bytes(dumps(dict(observations)).encode("utf-8"))
                          if observations is not None else None)
    rows = python["model_output_mismatches"]
    assert isinstance(rows, list)
    guide = catalog_provenance()
    return f'''<!doctype html>
<html lang="ja"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>EquiLane 不一致の詳細レポート</title><style>{_STYLE}</style></head><body><main>
<header><div class="eyebrow">EquiLane / Mismatch details</div><h1>不一致の詳細レポート</h1>
<p>記録されたPythonモデルの出力不一致：<strong>{len(rows):,}件</strong>。
件数は不一致の記録行数で、バグの個数ではありません。</p>
<p>共通Pythonモデルと独立した期待値の照合を、CPU向け実装の観測記録と分けて表示します。
通常のfixture同士の一致からCPU実機の一致は推定しません。</p>
<nav aria-label="レポート内の移動"><a href="#model-mismatch-details">Pythonモデルの詳細</a>
<a href="#native-observations">CPU向け実装の観測</a><a href="#report-provenance">記録の出典</a></nav>
<small>HTML生成：{_text(stamp)}。検証実行日時を表すものではありません。</small></header>
<h2>Pythonモデルで行った計算と不一致</h2>
{render_mismatch_details(summary)}
{_native_section(observations)}
<section class="native-section provenance" id="report-provenance"><h2>記録の出典</h2>
<p>入力した品質検証JSONのcanonical内容SHA-256：<code>{digest}</code></p>
<p>添付観測JSONのcanonical内容SHA-256：<code>{_text(observation_digest or "未添付")}</code></p>
<details><summary>添付スナップショットの証拠ファイルと採取条件</summary>
<pre>{_encoded(observations.get("evidence", {}) if observations is not None else {})}</pre></details>
<h3>Intel Intrinsics Guideとの対応</h3>
<p>各ケースの関数名・prototype・命令・ISAを<a href="{GUIDE_URL}" target="_blank" rel="noopener noreferrer">Intel公式ガイド</a>へ対応づけています。
Guideの必要ISA（SSE/SSE2）と、このプロジェクトが全ケースに使うsse2プロファイル名は別の情報です。
丸め・NaNの完全なビット一致判定には、記録したSSE2期待値も使っています。</p>
<details><summary>対応表の取得版と出典</summary><pre>{_encoded(guide)}</pre></details>
<p>記録に含まれる期待値の出典・テストID・入力IDは各不一致で確認できます。
canonical内容のハッシュはファイル末尾改行や空白に依存しません。</p></section>
</main></body></html>'''


def write_mismatch_report(
    output: str | Path,
    *,
    summary: Mapping[str, JSONValue],
    observations: Mapping[str, JSONValue] | None = None,
    generated_at: datetime | None = None,
) -> Path:
    path = Path(output)
    html = render_mismatch_report(summary, observations=observations, generated_at=generated_at)
    atomic_write(path, html.encode("utf-8"))
    return path
