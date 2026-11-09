#!/usr/bin/env python3
"""Export a small public report site without publishing local build evidence.

Only fixed canonical showcase paths and explicitly selected scalar summary
fields cross the public boundary. Raw manifests, logs, environments, binaries,
failure bundles and arbitrary paths from report JSON are never copied. A fixed
path differences.jsonl is exported only after its digest, count and closed typed
record schema have been verified while streaming.
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
import sys
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

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
    value = json.loads(path.read_bytes(), object_pairs_hook=_unique_object)
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


_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,127}\Z")
_CODE = re.compile(r"[a-z][a-z0-9_.-]{0,199}\Z")
_DIGEST = re.compile(r"[0-9a-f]{64}\Z")
_ROUNDING = {"nearest_even", "toward_zero", "toward_negative", "toward_positive"}
_FLAGS = ("invalid", "divide-by-zero", "overflow", "underflow", "inexact")
_ERROR_STAGES = {"infrastructure_error": "runner", "invalid_input": "input_validation",
                 "runtime_error": "execution", "signal": "signal", "unsupported": "capability"}
_MAX_LINE_BYTES = 8 * 1024 * 1024


def _keys(value, required, optional=()):
    if not isinstance(value, dict) or not set(required) <= value.keys() or value.keys() - set(required) - set(optional):
        raise ValueError("invalid public difference record fields")
    return value


def _integer(value, minimum=0, maximum=9007199254740991):
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError("invalid public difference integer")
    return value


def _token(value, pattern=_IDENTIFIER):
    if not isinstance(value, str) or not pattern.fullmatch(value) or SECRET.search(value):
        raise ValueError("invalid public difference identifier")
    return value


def _hex_bytes(value):
    if not isinstance(value, str) or not re.fullmatch(r"0x(?:[0-9a-f]{2})*", value):
        raise ValueError("invalid public difference bytes")
    return value


def _typed(value, *, allow_pointer=True):
    if allow_pointer and isinstance(value, dict) and "buffer" in value:
        _keys(value, ("buffer", "offset"))
        _token(value["buffer"])
        _integer(value["offset"])
        return
    _keys(value, ("element",), ("bits", "lanes"))
    if set(value) not in ({"element", "bits"}, {"element", "lanes"}):
        raise ValueError("invalid public typed value")
    element = value["element"]
    if element not in {"f32", "f64", "i8", "u8", "i16", "u16", "i32", "u32", "i64", "u64"}:
        raise ValueError("invalid public element")
    words = [value["bits"]] if "bits" in value else value["lanes"]
    expected_lanes = 1 if "bits" in value else 128 // int(element[1:])
    if not isinstance(words, list) or len(words) != expected_lanes:
        raise ValueError("invalid public lane count")
    for word in words:
        if not isinstance(word, str) or not re.fullmatch(rf"0x[0-9a-f]{{{int(element[1:]) // 4}}}", word):
            raise ValueError("invalid public lane bits")


def _buffers(values, *, observed=False):
    if not isinstance(values, dict) or len(values) > 64:
        raise ValueError("invalid public buffers")
    for name, item in values.items():
        _token(name)
        _keys(item, ("byte_offset", "bytes") if observed else ("alignment", "bytes"))
        _hex_bytes(item["bytes"])
        if observed:
            if _integer(item["byte_offset"]) != 0:
                raise ValueError("invalid public buffer offset")
        else:
            alignment = _integer(item["alignment"], 1)
            if alignment & (alignment - 1):
                raise ValueError("invalid public buffer alignment")


def _flags(value):
    if not isinstance(value, list) or value != [flag for flag in _FLAGS if flag in value]:
        raise ValueError("invalid public exception flags")


def _input(value, case_id, input_id):
    _keys(value, ("schema_version", "case_id", "input_id", "sequence", "environment", "generation", "operands"),
          ("buffers", "immediates"))
    if value["schema_version"] != 1 or type(value["schema_version"]) is not int or value["case_id"] != case_id or value["input_id"] != input_id:
        raise ValueError("public input identity mismatch")
    _integer(value["sequence"], 1)
    environment = _keys(value["environment"], ("fp_mode", "rounding"))
    if environment["fp_mode"] != "ieee" or environment["rounding"] not in _ROUNDING:
        raise ValueError("invalid public logical FP environment")
    generation = _keys(value["generation"], ("class",), ("algorithm", "seed", "regression_id"))
    generation_class = generation["class"]
    if generation_class == "random":
        _keys(generation, ("class", "algorithm", "seed"))
        _token(generation["algorithm"], _CODE)
        if not isinstance(generation["seed"], str) or not re.fullmatch(r"0x[0-9a-f]{16}", generation["seed"]):
            raise ValueError("invalid public random seed")
    elif generation_class == "regression":
        _keys(generation, ("class", "regression_id"))
        _token(generation["regression_id"], _CODE)
    elif generation_class in {"boundary", "exhaustive", "structured"}:
        _keys(generation, ("class",))
    else:
        raise ValueError("invalid public input generation")
    operands = value["operands"]
    if not isinstance(operands, dict) or len(operands) > 32:
        raise ValueError("invalid public operands")
    for name, typed in operands.items():
        _token(name)
        _typed(typed)
    if "buffers" in value:
        _buffers(value["buffers"])
    if "immediates" in value:
        if not isinstance(value["immediates"], dict) or len(value["immediates"]) > 16:
            raise ValueError("invalid public immediates")
        for name, immediate in value["immediates"].items():
            _token(name)
            _integer(immediate, -2147483648, 4294967295)
    identity = {key: value[key] for key in ("case_id", "environment", "operands", "buffers", "immediates") if key in value}
    encoded = json.dumps(identity, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    if hashlib.sha256(encoded).hexdigest() != input_id:
        raise ValueError("public input hash mismatch")


def _result(value, case_id, input_id, role):
    _keys(value, ("schema_version", "case_id", "input_id", "duration_ns", "runner", "status"), ("observed", "error"))
    if value["schema_version"] != 1 or type(value["schema_version"]) is not int or value["case_id"] != case_id or value["input_id"] != input_id or value["runner"] != role:
        raise ValueError("public result identity mismatch")
    _integer(value["duration_ns"])
    status = value["status"]
    if status == "ok":
        _keys(value, ("schema_version", "case_id", "input_id", "duration_ns", "runner", "status", "observed"))
        observed = _keys(value["observed"], (), ("return", "buffers", "fp_exceptions"))
        if "return" in observed:
            _typed(observed["return"], allow_pointer=False)
        if "buffers" in observed:
            _buffers(observed["buffers"], observed=True)
        if "fp_exceptions" in observed:
            _flags(observed["fp_exceptions"])
    elif isinstance(status, str) and status in _ERROR_STAGES:
        _keys(value, ("schema_version", "case_id", "input_id", "duration_ns", "runner", "status", "error"))
        error = _keys(value["error"], ("code", "stage"))
        _token(error["code"], _CODE)
        if error["stage"] != _ERROR_STAGES[status]:
            raise ValueError("invalid public error stage")
    else:
        raise ValueError("invalid public result status")


def _diagnostic(value, role):
    _keys(value, ("intel", role), ("ulp_distance",))
    for name in ("intel", role):
        detail = _keys(value[name], ("classification",), ("decimal",))
        if detail["classification"] not in {"quiet_nan", "signaling_nan", "negative_infinity", "positive_infinity", "negative_zero", "positive_zero", "subnormal", "normal"}:
            raise ValueError("invalid public FP classification")
        if "decimal" in detail and (not isinstance(detail["decimal"], str) or not re.fullmatch(r"-?[0-9]+(?:\.[0-9]+)?(?:e[+-]?[0-9]+)?", detail["decimal"])):
            raise ValueError("invalid public decimal")
    if "ulp_distance" in value and (not isinstance(value["ulp_distance"], str) or not re.fullmatch(r"[0-9]{1,64}", value["ulp_distance"])):
        raise ValueError("invalid public ULP distance")


def _difference(value, role):
    if not isinstance(value, dict):
        raise ValueError("invalid public first difference")
    kind = value.get("kind")
    if kind == "return":
        _keys(value, ("kind", "intel", role), ("lane", "diagnostic"))
        for name in ("intel", role):
            _hex_bytes(value[name])
        if "lane" in value:
            _integer(value["lane"], 0, 15)
        if "diagnostic" in value:
            _diagnostic(value["diagnostic"], role)
    elif kind in {"memory_contract", "buffer"}:
        required = ("kind", "buffer", "byte_offset", "before", "after", "runner") if kind == "memory_contract" else ("kind", "buffer", "byte_offset", "intel", role)
        _keys(value, required)
        _token(value["buffer"])
        _integer(value["byte_offset"])
        if kind == "memory_contract" and value["runner"] not in {"intel", role}:
            raise ValueError("invalid public memory runner")
        for name in (("before", "after") if kind == "memory_contract" else ("intel", role)):
            if len(_hex_bytes(value[name])) != 4:
                raise ValueError("invalid public difference byte")
    elif kind in {"status", "fp_exceptions"}:
        _keys(value, ("kind", "intel", role))
        for name in ("intel", role):
            if kind == "status":
                if value[name] not in {"ok", *_ERROR_STAGES}:
                    raise ValueError("invalid public compared status")
            else:
                _flags(value[name])
    else:
        raise ValueError("invalid public difference kind")


def _public_difference(value, role):
    _keys(value, ("case_id", "input_id", "target_role", "input", "intel", "target", "comparison"))
    case_id = _token(value["case_id"], _CODE)
    input_id = _token(value["input_id"], _DIGEST)
    if value["target_role"] != role:
        raise ValueError("public difference role mismatch")
    _input(value["input"], case_id, input_id)
    _result(value["intel"], case_id, input_id, "intel")
    _result(value["target"], case_id, input_id, role)
    comparison = _keys(value["comparison"], ("outcome", "mismatch_count", "first_difference", "reason"))
    count = _integer(comparison["mismatch_count"])
    if comparison["outcome"] == "mismatch" and count > 0 and comparison["reason"] in {"status_mismatch", "value_mismatch"}:
        _difference(comparison["first_difference"], role)
    elif comparison["outcome"] == "not_comparable" and count == 0 and comparison["first_difference"] is None:
        if comparison["reason"] not in {"both_" + status for status in _ERROR_STAGES}:
            raise ValueError("invalid public comparison reason")
    else:
        raise ValueError("public archive must contain only nonmatching records")
    return comparison["outcome"]


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key in public archive")
        result[key] = value
    return result


def _file_sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _export_differences(source, output, target, report):
    metadata = _keys(report.get("differences"), ("file", "record_count", "byte_length", "sha256"))
    if metadata["file"] != "differences.jsonl":
        raise ValueError("public archive file must use its fixed name")
    expected_count = _integer(metadata["record_count"])
    expected_length = _integer(metadata["byte_length"])
    _token(metadata["sha256"], _DIGEST)
    archive = _safe_source(source, f"canonical/compare-{target}/differences.jsonl")
    temporary = output / ".differences.jsonl.tmp"
    digest = hashlib.sha256()
    count = length = 0
    outcomes = {"mismatch": 0, "not_comparable": 0}
    role = "riscv" if target == "riscv-vlen256" else target
    try:
        with archive.open("rb") as reader, temporary.open("wb") as writer:
            while True:
                line = reader.readline(_MAX_LINE_BYTES + 1)
                if not line:
                    break
                if len(line) > _MAX_LINE_BYTES or not line.endswith(b"\n"):
                    raise ValueError("public archive has an oversized or incomplete record")
                value = json.loads(line, object_pairs_hook=_unique_object)
                outcomes[_public_difference(value, role)] += 1
                digest.update(line)
                length += len(line)
                count += 1
                writer.write(line)
        if (count, length, digest.hexdigest()) != (expected_count, expected_length, metadata["sha256"]):
            raise ValueError("public archive integrity mismatch")
        for key, outcome in (("mismatched_inputs", "mismatch"), ("not_comparable_inputs", "not_comparable")):
            if key in report and _integer(report[key]) != outcomes[outcome]:
                raise ValueError("public archive outcome counts mismatch")
        temporary.replace(output / "differences.jsonl")
    except (OSError, ValueError, TypeError, UnicodeError, RecursionError):
        temporary.unlink(missing_ok=True)
        raise ValueError("public differences archive could not be verified") from None
    return dict(metadata)


class PublicHTML(HTMLParser):
    """Keep trusted report presentation while removing local navigation/data."""
    def __init__(self, *, available_files=(), link_mapping=None):
        super().__init__(convert_charrefs=False)
        self.parts = []
        self.available_files = frozenset(available_files)
        self.requests_differences = False
        self.link_mapping = link_mapping or {}

    def handle_decl(self, declaration):
        if declaration.lower() == "doctype html":
            self.parts.append("<!doctype html>")

    def handle_starttag(self, tag, attributes):
        cleaned = []
        for key, value in attributes:
            if value is None:
                cleaned.append(key)
                continue
            if tag == "a" and key == "href" and value == "differences.jsonl":
                self.requests_differences = True
            if key in ("href", "src", "action"):
                parsed = urlsplit(value)
                allowed = (value.startswith("#") or parsed.scheme == "https"
                           or (tag == "img" and value.startswith("data:image/"))
                           or (tag == "a" and key == "href" and value in self.available_files))
                if not allowed:
                    continue
            if key == "href":
                value = self.link_mapping.get(value, value)
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
    from equilane.i18n import translate_html
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
    archives = {}
    archive_failures = []
    for target in TARGETS:
        try:
            page = _safe_source(source, f"canonical/compare-{target}/showcase.html")
            if not page.is_file():
                continue
            text = page.read_text(encoding="utf-8")
            if "<title>EquiLane" not in text or "</html>" not in text:
                raise ValueError("not an EquiLane showcase report")
            output = destination / target / "index.html"
            output.parent.mkdir()
            try:
                comparison = _object(_safe_source(source, f"canonical/compare-{target}/summary.json"))
            except (OSError, ValueError):
                comparison = {}
            wants_archive = isinstance(comparison, dict) and "differences" in comparison
            language_files = ("showcase.html", "showcase.en.html", "index.html", "index.en.html")
            link_mapping = {"showcase.html": "index.html", "showcase.en.html": "index.en.html"}
            sanitizer = PublicHTML(available_files=language_files, link_mapping=link_mapping)
            sanitizer.feed(text)
            sanitizer.close()
            # Parse attributes so unquoted or entity-encoded links get the same check.
            links_archive = sanitizer.requests_differences
            if wants_archive or links_archive:
                try:
                    archives[target] = _export_differences(source, output.parent, target, comparison)
                except (OSError, ValueError, TypeError):
                    archive_failures.append(target)
            if target in archives:
                sanitizer = PublicHTML(available_files=(*language_files, "differences.jsonl"), link_mapping=link_mapping)
                sanitizer.feed(text)
                sanitizer.close()
            rendered = "".join(sanitizer.parts)
            if target in archive_failures:
                notice = '<p role="alert">差分 JSONL を検証できなかったため、この公開版のダウンロードは無効です。</p>'
                rendered = re.sub(r"(<body(?:\s[^>]*)?>)", lambda match: match[0] + notice, rendered, count=1)
            output.write_text(rendered, encoding="utf-8")
            english = translate_html(rendered, language="en", strict=True)
            (output.parent / "index.en.html").write_text(english, encoding="utf-8")
            pages.append(target)
        except (OSError, ValueError):
            # The index explicitly records missing pages instead of copying a
            # different file or following a report-controlled path.
            continue
    summary["detail_pages"] = pages
    if archives:
        summary["difference_archives"] = archives
    if archive_failures:
        summary["unavailable_difference_archives"] = archive_failures
        summary["status"] = "error"
    missing = [row["target"] for row in summary["canonical_comparisons"]
               if row.get("record_count", 0) > 0 and row["target"] not in pages]
    if missing:
        summary["unavailable_detail_pages"] = missing
        summary["status"] = "error"
    (destination / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    parts = ['<!doctype html><html lang="ja"><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>EquiLane — Intel Intrinsics 移植検証</title>',
             '<style>body{max-width:1100px;margin:3rem auto;padding:0 1rem;font:16px/1.7 system-ui;background:#faf7f0;color:#393244}h1{color:#70538f}table{width:100%;border-collapse:collapse;background:#fffdf9}th,td{padding:.7rem;border:1px solid #e6dfec;text-align:left}a{color:#70538f}.status{padding:1rem;background:#f8e5e9;border-radius:14px}code{overflow-wrap:anywhere}</style>',
             '<nav aria-label="Language"><a href="index.html" lang="ja">日本語</a> / <a href="index.en.html" lang="en">English</a></nav>',
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
    if archive_failures:
        parts.append('<p class="status">差分 JSONL を公開できなかった対象: ' + escape(", ".join(TARGET_LABELS[target] for target in archive_failures)) + '。ダウンロードを無効にし、公開状態を未完了としています。</p>')
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
    english_index = translate_html("\n".join(parts), language="en", strict=True)
    # English index links point at English detail pages; the JSON evidence is shared.
    for target in TARGETS:
        english_index = english_index.replace(f'href="{target}/"', f'href="{target}/index.en.html"')
    (destination / "index.en.html").write_text(english_index, encoding="utf-8")
    (destination / ".nojekyll").write_text("")
    files = sorted(path for path in destination.rglob("*") if path.is_file())
    deployment = {"artifact_type": "equilane.pages-export", "schema_version": 1,
                  "exported_at": datetime.now(timezone.utc).isoformat(), "status": summary["status"],
                  "files": [{"file": str(path.relative_to(destination)), "sha256": _file_sha256(path)} for path in files]}
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
