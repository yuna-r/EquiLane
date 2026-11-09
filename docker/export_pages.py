#!/usr/bin/env python3
"""Export a small public report site without publishing local build evidence.

Only fixed canonical showcase paths and explicitly selected scalar summary
fields cross the public boundary. Raw manifests, logs, environments, binaries,
failure bundles and arbitrary paths from report JSON are never copied.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
from html import escape
from html.parser import HTMLParser
import json
from pathlib import Path
import re
from urllib.parse import urlsplit

TARGETS = ("openpower", "arm", "riscv", "riscv-vlen256")
TARGET_LABELS = {
    "intel": "Intel / SSE2", "openpower": "OpenPOWER / VSX", "arm": "ARM64 / NEON",
    "riscv": "RISC-V / RVV（VLEN 128）", "riscv-vlen256": "RISC-V / RVV（VLEN 256）",
}
STATUS_LABELS = {
    "pass": "今回の入力では適合", "match": "今回の入力では適合", "mismatch": "未適合あり",
    "findings": "未適合・検証指摘あり", "not_comparable": "判定できない入力あり",
    "error": "工程未完了・実行失敗", "unknown": "未記録",
}
STATUSES = {"pass", "match", "mismatch", "findings", "not_comparable", "error", "unknown"}
COUNTS = ("record_count", "matched_inputs", "mismatched_inputs", "not_comparable_inputs", "mismatch_atoms",
          "samples", "matched", "mismatch_count", "tests_run", "skipped", "portable_model_mismatches", "execution_and_assertion_errors")
PRIVATE_PATH = re.compile(r"(?:file://)?(?:/(?:Users|home|workspace|opt/equilane|private/(?:var|tmp)|tmp|build|results)/[^\s<>\"'`]+|[A-Za-z]:[\\/][^\s<>\"'`]+)")
SECRET = re.compile(r"(?:gh[pousr]_[A-Za-z0-9_]{12,}|github_pat_[A-Za-z0-9_]{12,}|Bearer\s+[A-Za-z0-9._~+/=-]{12,}|AKIA[A-Z0-9]{16})")


def public_text(value):
    return SECRET.sub("[redacted]", PRIVATE_PATH.sub("[local path]", str(value)))


def _safe_source(root, relative):
    candidate = root / relative
    # A symlink in an allowlisted directory must not turn into an arbitrary read.
    cursor = candidate
    while cursor != root:
        if cursor.is_symlink():
            raise ValueError("public report source contains a symlink")
        cursor = cursor.parent
    if not candidate.resolve().is_relative_to(root):
        raise ValueError("public report source escapes its input directory")
    return candidate


def _object(path):
    value = json.loads(path.read_bytes())
    if not isinstance(value, dict):
        raise ValueError("report must contain a JSON object")
    return value


def _row(value, target=None):
    if not isinstance(value, dict):
        return {"target": target, "status": "error"}
    result = {"status": value.get("status", value.get("outcome", "unknown"))}
    if not isinstance(result["status"], str) or result["status"] not in STATUSES:
        result["status"] = "error"
    if target is not None:
        result["target"] = target
    for key in COUNTS:
        count = value.get(key)
        if type(count) is int and count >= 0:
            result[key] = count
    return result


def public_summary(raw):
    summary = {"artifact_type": "equilane.public-report", "schema_version": 1,
               "status": "error", "wrapper_comparisons": [], "canonical_comparisons": [],
               "native_tests": [], "quality": {"status": "unknown"}, "physical_hardware_evidence": False}
    if not isinstance(raw, dict):
        return summary
    if isinstance(raw.get("status"), str) and raw["status"] in STATUSES:
        summary["status"] = raw["status"]
    created = raw.get("created_at")
    if isinstance(created, str):
        try:
            summary["created_at"] = datetime.fromisoformat(created).isoformat()
        except ValueError:
            pass
    for key in ("wrapper_comparisons", "canonical_comparisons", "native_tests"):
        values = raw.get(key)
        if not isinstance(values, list):
            continue
        for value in values:
            if isinstance(value, dict) and value.get("target") in (*TARGETS, "intel"):
                summary[key].append(_row(value, value["target"]))
    summary["quality"] = _row(raw.get("quality"))
    if isinstance(raw.get("rvv_width_check"), dict):
        summary["rvv_width_check"] = _row(raw["rvv_width_check"])
    return summary


class PublicHTML(HTMLParser):
    """Keep trusted report presentation while removing local navigation/data."""
    def __init__(self):
        super().__init__(convert_charrefs=False)
        self.parts = []

    def handle_decl(self, declaration):
        if declaration.lower() == "doctype html":
            self.parts.append("<!doctype html>")

    def handle_starttag(self, tag, attributes):
        cleaned = []
        for key, value in attributes:
            if value is None:
                cleaned.append(key)
                continue
            if key in ("href", "src", "action"):
                parsed = urlsplit(value)
                allowed = value.startswith("#") or parsed.scheme == "https" or (tag == "img" and value.startswith("data:image/"))
                if not allowed:
                    continue
            cleaned.append(f'{key}="{escape(public_text(value), quote=True)}"')
        self.parts.append("<" + tag + (" " + " ".join(cleaned) if cleaned else "") + ">")

    def handle_startendtag(self, tag, attributes):
        self.handle_starttag(tag, attributes)

    def handle_endtag(self, tag):
        self.parts.append("</" + tag + ">")

    def handle_data(self, data):
        self.parts.append(public_text(data))

    def handle_entityref(self, name):
        self.parts.append("&" + name + ";")

    def handle_charref(self, name):
        self.parts.append("&#" + name + ";")


def export_pages(source, destination, *, commit="", repository="", run_id=""):
    source = Path(source).resolve()
    destination = Path(destination).resolve()
    if destination == source or source.is_relative_to(destination):
        raise ValueError("public destination must not contain the private input directory")
    destination.mkdir(parents=True, exist_ok=True)
    # Never leave a raw file from an earlier export in the published directory.
    if any(destination.iterdir()):
        raise ValueError("public destination must be empty")
    try:
        summary = public_summary(_object(_safe_source(source, "pipeline-summary.json")))
    except (OSError, ValueError):
        summary = public_summary(None)
    if re.fullmatch(r"[0-9a-f]{40,64}", commit):
        summary["commit"] = commit
    if re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
        summary["repository"] = repository
        if re.fullmatch(r"[0-9]+", run_id):
            summary["actions_run"] = f"https://github.com/{repository}/actions/runs/{run_id}"
    pages = []
    for target in TARGETS:
        try:
            page = _safe_source(source, f"canonical/compare-{target}/showcase.html")
            if not page.is_file():
                continue
            text = page.read_text(encoding="utf-8")
            if "<title>EquiLane" not in text or "</html>" not in text:
                raise ValueError("not an EquiLane showcase report")
            sanitizer = PublicHTML()
            sanitizer.feed(text)
            sanitizer.close()
            output = destination / target / "index.html"
            output.parent.mkdir()
            output.write_text("".join(sanitizer.parts), encoding="utf-8")
            pages.append(target)
        except (OSError, ValueError):
            # The index explicitly records missing pages instead of copying a
            # different file or following a report-controlled path.
            continue
    summary["detail_pages"] = pages
    missing = [row["target"] for row in summary["canonical_comparisons"]
               if row.get("record_count", 0) > 0 and row["target"] not in pages]
    if missing:
        summary["unavailable_detail_pages"] = missing
        summary["status"] = "error"
    (destination / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    parts = ['<!doctype html><html lang="ja"><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>EquiLane — Intel Intrinsics 移植検証</title>',
             '<style>body{max-width:1100px;margin:3rem auto;padding:0 1rem;font:16px/1.7 system-ui;background:#faf7f0;color:#393244}h1{color:#70538f}table{width:100%;border-collapse:collapse;background:#fffdf9}th,td{padding:.7rem;border:1px solid #e6dfec;text-align:left}a{color:#70538f}.status{padding:1rem;background:#f8e5e9;border-radius:14px}code{overflow-wrap:anywhere}</style>',
             '<h1>Intel Intrinsics 移植検証</h1>', f'<p class="status">検証状態: <strong>{STATUS_LABELS[summary["status"]]}</strong></p>',
             '<p>Intel Intrinsics の動作を各アーキテクチャへ正しく移植できているか、Intel の実行結果を基準としてケース契約に沿って検証します。Guide の関数をケースへ対応付け、共通入力を各実装の C ABI に渡し、戻り値とメモリ出力を照合します。</p>',
             '<p>共通環境は Ubuntu 26.04 LTS。対象は Intel / SSE2（linux/amd64）、OpenPOWER / VSX（linux/ppc64le）、ARM64 / NEON（linux/arm64）、RISC-V64 / RVV（linux/riscv64）。異種 CPU の実行はエミュレータの観測として扱います。</p>',
             '<p>実行・テスト工程の成否と、出力の意味上の一致を分けて表示します。各対象の詳細には <code>_mm_*</code> 関数、シグネチャ、操作、アーキテクチャ間の対応、期待値と実測値をまとめます。</p>',
             '<p>ケース契約で許容されない不一致は移植の不適合として扱い、原因の調査が必要です。契約が明示的に許容する差は比較ルールに従います。補助的な wrapper のビット差だけから個別の不具合を断定せず、契約に基づく判定と入力・実測値を確認してください。</p>',
             '<p><a href="summary.json">公開用の集計 JSON</a></p>']
    if "actions_run" in summary:
        parts.append(f'<p><a href="{escape(summary["actions_run"], quote=True)}">GitHub Actions の実行記録</a></p>')
    if "commit" in summary:
        parts.append(f'<p>Source commit: <code>{summary["commit"]}</code></p>')
    if missing:
        parts.append('<p class="status">詳細ページを公開できなかった対象: ' + escape(", ".join(TARGET_LABELS[target] for target in missing)) + '。この公開結果は未完了です。</p>')
    for key, label in (("canonical_comparisons", "共通 C ABI の比較"), ("wrapper_comparisons", "Wrapper のバイト比較")):
        parts.append(f'<h2>{label}</h2><table><tr><th>対象</th><th>状態</th><th>一致</th><th>不一致</th><th>合計</th><th>詳細</th></tr>')
        for row in summary[key]:
            target = row["target"]
            detail = f'<a href="{target}/">ケースと検証範囲</a>' if key == "canonical_comparisons" and target in pages else "—"
            status_label = STATUS_LABELS[row["status"]]
            if key == "wrapper_comparisons":
                status_label = {"pass": "ビット差なし", "match": "ビット差なし", "mismatch": "ビット差あり（補助診断）"}.get(row["status"], status_label)
            fields = (TARGET_LABELS[target], status_label, row.get("matched_inputs", row.get("matched", "—")), row.get("mismatched_inputs", row.get("mismatch_count", "—")), row.get("record_count", row.get("samples", "—")))
            parts.append('<tr>' + ''.join('<td>' + escape(str(value)) + '</td>' for value in fields) + f'<td>{detail}</td></tr>')
        parts.append('</table>')
    parts.append('<h2>検証工程の実行状態</h2>')
    for row in summary["native_tests"]:
        label = "成功" if row["status"] == "pass" else STATUS_LABELS[row["status"]]
        parts.append(f'<p>{TARGET_LABELS[row["target"]]} CMake / CTest: {label}</p>')
    quality = summary["quality"]
    quality_label = {"pass": "工程完了", "findings": "工程完了（モデルの検証指摘あり）"}.get(quality["status"], STATUS_LABELS[quality["status"]])
    parts.append(f'<p>回帰テスト・画面 JS・sanitizer・cross build: {quality_label}</p>')
    for key, label in (("tests_run", "実行した回帰テスト"), ("skipped", "省略したテスト"),
                       ("execution_and_assertion_errors", "テスト実行エラー・その他の assertion 失敗")):
        if key in quality:
            parts.append(f'<p>{label}: {quality[key]:,}件</p>')
    if "portable_model_mismatches" in quality:
        parts.append(f'<h3>Python portable model の既知の不一致</h3><p>{quality["portable_model_mismatches"]:,}件。開発用モデルの出力を、独立した期待値と照合して検出した指摘です。移植先 C 実装の適合判定は、上の「共通 C ABI の比較」で確認できます。</p>')
    parts.append('<p>適合の表示は、今回のケース契約と入力範囲についての結果です。未適合の入力は、詳細ページの関数・丸め条件・期待値・実測値から調査できます。実行工程の完了と Intel 互換要求への適合は、それぞれの欄で確認してください。</p></html>')
    (destination / "index.html").write_text("\n".join(parts), encoding="utf-8")
    (destination / ".nojekyll").write_text("")
    files = sorted(path for path in destination.rglob("*") if path.is_file())
    deployment = {"artifact_type": "equilane.pages-export", "schema_version": 1,
                  "exported_at": datetime.now(timezone.utc).isoformat(), "status": summary["status"],
                  "files": [{"file": str(path.relative_to(destination)), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()} for path in files]}
    (destination / "deployment.json").write_text(json.dumps(deployment, indent=2) + "\n")
    return summary


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--commit", default="")
    parser.add_argument("--repository", default="")
    parser.add_argument("--run-id", default="")
    args = parser.parse_args(argv)
    try:
        summary = export_pages(args.input, args.output, commit=args.commit, repository=args.repository, run_id=args.run_id)
    except (OSError, ValueError) as exc:
        print(f"Pages export failed: {public_text(exc)}")
        return 2
    print(json.dumps({"status": summary["status"], "pages": len(summary["detail_pages"])}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
