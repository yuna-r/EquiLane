#!/usr/bin/env python3
"""Render the observed extended-run data as standalone Japanese/English HTML."""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
from html import escape
import json
from pathlib import Path
import re
from urllib.parse import quote

ROLES = ("arm", "openpower", "riscv")
ROLE_LABELS = {"arm": "ARM64 / NEON", "openpower": "OpenPOWER / VSX", "riscv": "RISC-V / RVV"}
GUIDE = "https://www.intel.com/content/www/us/en/docs/intrinsics-guide/index.html#text="
IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")
HEX = re.compile(r"0x(?:[0-9a-f]{2})*\Z")
DIGEST = re.compile(r"[0-9a-f]{64}\Z")


def _count(value):
    if type(value) is not int or not 0 <= value <= 9007199254740991:
        raise ValueError("invalid report count")
    return value


def _source(root, relative):
    path = Path(relative)
    if path.is_absolute() or ".." in path.parts or not path.parts:
        raise ValueError("report source path escapes its root")
    candidate = root / path
    cursor = candidate
    while cursor != root:
        if cursor.is_symlink():
            raise ValueError("report source contains a symlink")
        cursor = cursor.parent
    if not candidate.resolve().is_relative_to(root):
        raise ValueError("report source path escapes its root")
    return candidate


def _verified_file(root, metadata):
    path = _source(root, metadata["file"])
    if "sha256" in metadata:
        if not DIGEST.fullmatch(str(metadata["sha256"])) or hashlib.sha256(path.read_bytes()).hexdigest() != metadata["sha256"]:
            raise ValueError("report source digest mismatch")
    if "byte_length" in metadata and path.stat().st_size != _count(metadata["byte_length"]):
        raise ValueError("report source size mismatch")
    return path


def _json(path):
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("report source must be an object")
    return value


def _hex(value, size=None):
    if not isinstance(value, str) or not HEX.fullmatch(value) or (size is not None and len(value) != size * 2 + 2):
        raise ValueError("invalid raw-byte observation")
    return value


def _catalog_case(record):
    name = record["name"]
    if not isinstance(name, str) or not IDENTIFIER.fullmatch(name):
        raise ValueError("invalid intrinsic identifier")
    args, cursor = [], 0
    for argument in record["arguments"]:
        key = argument["name"]
        if not isinstance(key, str) or not IDENTIFIER.fullmatch(key):
            raise ValueError("invalid argument identifier")
        offset, size = _count(argument["offset"]), _count(argument["byte_length"])
        if offset != cursor:
            raise ValueError("noncontiguous argument layout")
        guide_type = argument["guide_type"]
        if not isinstance(guide_type, str) or len(guide_type) > 256:
            raise ValueError("invalid prototype type")
        args.append([key, guide_type, offset, size])
        cursor += size
    if cursor != _count(record["input_byte_length"]):
        raise ValueError("argument byte layout does not match input")
    result_type = record["return"]["guide_type"]
    if not isinstance(result_type, str) or len(result_type) > 256:
        raise ValueError("invalid result type")
    prototype = result_type + " " + name + "(" + (", ".join(arg[1] + " " + arg[0] for arg in args) or "void") + ");"
    return {"name": name, "prototype": prototype, "arguments": args,
            "output_size": _count(record["output_byte_length"]), "input_size": cursor,
            "isa": [str(value) for value in record.get("cpuid", [])],
            "guide": GUIDE + quote(name, safe=""), "counts": {}, "notes": {}, "intel_status": {}}


def _build_data(source, summary):
    source = source.resolve()
    catalog = _json(_verified_file(source, summary["catalog"]))
    if catalog.get("byte_order") != "little":
        raise ValueError("this report requires a little-endian catalog")
    cases = [_catalog_case(record) for record in catalog["intrinsics"]]
    indexed = {case["name"]: case for case in cases}
    if len(indexed) != len(cases) or len(cases) != _count(summary["candidate_count"]):
        raise ValueError("catalog candidate count mismatch")
    intel = summary["roles"]["intel"]
    intel_status = {name: Counter() for name in indexed}
    seen_inputs = set()
    with _verified_file(source, intel["results"]).open(encoding="utf-8") as stream:
        for line in stream:
            row = json.loads(line)
            if row["name"] not in indexed or row["input_id"] in seen_inputs:
                raise ValueError("invalid Intel result identity")
            seen_inputs.add(row["input_id"])
            intel_status[row["name"]][row["status"]] += 1
    if len(seen_inputs) != _count(summary["distinct_inputs"]):
        raise ValueError("Intel input count mismatch")
    for name, counts in intel_status.items():
        indexed[name]["intel_status"] = dict(counts)
    unsupported = sum(counts["unsupported_cpu"] > 0 for counts in intel_status.values())
    if unsupported != _count(intel["unsupported_candidates"]):
        raise ValueError("unsupported CPU candidate count mismatch")

    raw, totals = {}, {}
    for role in ROLES:
        overview = summary["comparisons"][role]
        comparison = _json(_verified_file(source, overview["summary"]))
        if comparison.get("target") != role or len(comparison["cases"]) != len(cases):
            raise ValueError("comparison target or case count mismatch")
        seen = set()
        for item in comparison["cases"]:
            name = item["name"]
            if name not in indexed or name in seen:
                raise ValueError("invalid comparison case identity")
            seen.add(name)
            indexed[name]["counts"][role] = [_count(item[key]) for key in ("matched", "mismatched", "not_comparable")]
            review = overview.get("review_candidates", {})
            indexed[name]["notes"][role] = {
                "approximate": name in review.get("approximate_reciprocal_or_rsqrt", {}),
                "undefined": name in review.get("undefined_widening_casts", {}),
            }
        raw[role] = {}
        all_rows, raw_count = 0, 0
        with _verified_file(source, overview["differences"]).open(encoding="utf-8") as stream:
            for line in stream:
                row = json.loads(line)
                all_rows += 1
                if row.get("outcome") != "mismatch":
                    continue
                name = row["name"]
                if name not in indexed:
                    raise ValueError("raw difference references an unknown function")
                case = indexed[name]
                reference, observed = row["intel"], row["target"]
                if reference["status"] != "ok" or observed["status"] != "ok":
                    raise ValueError("a raw byte difference requires successful executions")
                input_id = row["input_id"]
                if not isinstance(input_id, str) or not DIGEST.fullmatch(input_id):
                    raise ValueError("invalid raw difference input identity")
                encoded = _hex(row["input"]["input"], case["input_size"])
                if (reference["input_id"] != input_id or observed["input_id"] != input_id or
                        reference["input"] != encoded or observed["input"] != encoded or
                        row["input"]["name"] != name or reference["name"] != name or observed["name"] != name):
                    raise ValueError("raw difference input or function mismatch")
                left, right = _hex(reference["output"], case["output_size"]), _hex(observed["output"], case["output_size"])
                if left == right:
                    raise ValueError("raw byte mismatch contains identical outputs")
                flags = [_count(reference["fp_flags"]), _count(observed["fp_flags"])]
                if any(value > 31 for value in flags):
                    raise ValueError("unknown normalized floating-point flag")
                rounding = row["input"]["rounding"]
                if rounding not in {"nearest_even", "toward_zero", "toward_positive", "toward_negative"}:
                    raise ValueError("unknown rounding mode")
                raw[role].setdefault(name, []).append([input_id, encoded, left, right, *flags, rounding])
                raw_count += 1
        if all_rows != _count(overview["differences"]["record_count"]) or raw_count != _count(overview["raw_byte_differences"]):
            raise ValueError("raw difference record count mismatch")
        for case in cases:
            if len(raw[role].get(case["name"], [])) != case["counts"][role][1]:
                raise ValueError("raw differences do not match the per-function counts")
        matched = sum(case["counts"][role][0] for case in cases)
        not_compared = sum(case["counts"][role][2] for case in cases)
        if matched != _count(overview["matched_inputs"]) or not_compared != _count(overview["not_comparable_inputs"]):
            raise ValueError("comparison totals mismatch")
        totals[role] = {"matched": matched, "different": raw_count, "uncompared": not_compared,
                        "comparable": matched + raw_count,
                        "functions_different": sum(case["counts"][role][1] > 0 for case in cases),
                        "fp_diagnostic": _count(overview["fp_flag_differences_diagnostic_only"])}
    return {"cases": cases, "raw": raw, "totals": totals, "candidate_count": len(cases),
            "input_count": _count(summary["distinct_inputs"]), "unsupported_candidates": unsupported,
            "intel_candidates": _count(intel["executed_candidates"]),
            "source_commit": str(summary.get("source_commit", ""))[:40],
            "assembled_at": str(summary.get("assembled_at", "")),
            "guide_count": _count(summary.get("guide_scope", {}).get("distinct_api_names", len(cases)))}


TEXT = {
    "ja": {
        "title": "Intel Intrinsicsの移植を、ビットで確認",
        "subtitle": "同じ入力をIntelと移植先で実行し、戻り値のraw bytesを比較したレポートです。関数を選ぶと、実際の入力と両方の出力を確認できます。",
        "overview": "全体の結果", "functions": "対象intrinsics", "inputs": "共通入力",
        "reference": "Intelで実行できた関数", "unsupported": "Intel CPU非対応・未検証",
        "scope": "実行できたことと、正しく移植できたことは別です。Intel側で命令を実行できた入力だけを比較しています。CPU非対応の関数は一致扱いにせず、未検証として残しています。",
        "diagnostic": "FP flagsは診断情報です。raw差件数は戻りbytesの違いだけを数えています。raw差を、そのまま仕様違反やバグと断定していません。",
        "hardware": "Docker環境での測定です。ホストと異なるCPUにはエミュレーションを使い、物理CPU間の完全な適合性を保証するものではありません。",
        "guide_scope": "このレポートの対象は{n}関数です。Guide全体の{g} APIを完了したという意味ではありません。",
        "see": "結果を見る", "raw": "raw差の入力", "matched": "今回の入力で一致", "uncompared": "Intel参照がなく未比較",
        "comparable": "比較できた入力", "different_functions": "raw差のある関数", "diagnostic_count": "FP flags差（診断のみ）",
        "explorer": "関数から調べる", "steps": "① 関数名やISAで検索　② 関数を選択　③ 入力と戻りbytesを照合",
        "search": "関数名・prototype・ISAで検索", "all": "すべて", "diff": "raw差あり", "cpu": "CPU非対応・未検証",
        "no_functions": "条件に合う関数がありません。", "more": "さらに100関数を表示", "showing": "{shown} / {total} 関数",
        "choose": "左の一覧から関数を選んでください。", "guide": "Intel Intrinsics Guideで開く",
        "function_result": "この関数の結果", "raw_details": "すべてのraw差分", "no_raw": "今回比較できた入力では、戻りbytesのraw差はありません。",
        "not_tested": "Intel CPUがこの命令に対応していないため、移植の正しさは未検証です。対象CPUの出力が得られていても、Intel基準値との一致は判定していません。",
        "not_compared": "この関数にはIntel基準値と比較できなかった入力があります。未比較分は一致件数に含めません。",
        "approximate": "近似命令の確認候補です。許容誤差をGuideの仕様と照合する必要があります。raw差は削除・補正していません。",
        "undefined": "幅を広げるcastの未定義上位レーンを含む確認候補です。未定義領域のraw差だけでは不適合と断定できません。観測bytesはそのまま表示しています。",
        "review": "注記は関数名に基づく確認候補で、判定の書き換えではありません。",
        "input": "入力", "arguments": "引数ごとのhex", "no_arguments": "引数なし", "encoded": "encoded入力の全bytes",
        "return": "戻りbytes", "intel": "Intel基準", "target": "移植先", "xor": "XOR（違うbit）", "byte_changes": "異なるbyte: {n} / {total}",
        "flags": "FP flags（診断）", "none": "なし", "rounding": "丸め", "byte_order": "bytesは低アドレス順です。引数の区切りはprototypeと実行時のレイアウトに従います。複数byteの値はlittle-endianです。",
        "downloads": "検証データをダウンロード", "download_note": "画面での確認にJSONの操作は不要です。全入力・全出力を再利用したい場合はこちらから取得できます。",
        "input_download": "共通入力すべて", "intel_download": "Intelの全結果", "target_download": "移植先の全結果", "diff_download": "差分データすべて", "summary_download": "集計データ",
        "noscript": "関数検索とraw差分の展開にはJavaScriptを有効にしてください。集計とダウンロードはそのまま利用できます。",
        "updated": "集計日時", "source": "実行ソース", "rows": "入力", "function": "関数", "raw_short": "raw差", "cpu_short": "未検証",
    },
    "en": {
        "title": "Check Intel Intrinsics ports, bit by bit",
        "subtitle": "The same inputs ran on Intel and each port. This report compares the returned raw bytes. Choose a function to inspect its actual inputs and both outputs.",
        "overview": "All results", "functions": "Intrinsics in this run", "inputs": "Shared inputs",
        "reference": "Functions executed on Intel", "unsupported": "Unsupported on Intel CPU · unverified",
        "scope": "Successful execution does not establish a correct port. Only inputs whose instructions ran on Intel can be compared. Functions unsupported by the Intel CPU remain unverified; they are not counted as matches.",
        "diagnostic": "FP flags are diagnostic information. Raw-difference counts include returned bytes only. A raw difference is not automatically a specification violation or a bug.",
        "hardware": "These measurements ran in Docker, using emulation where the CPU differs from the host. They do not establish complete conformance between physical CPUs.",
        "guide_scope": "This report covers {n} functions, not all {g} API names in the Guide.",
        "see": "Explore results", "raw": "Inputs with raw differences", "matched": "Matched for these inputs", "uncompared": "Not compared without an Intel reference",
        "comparable": "Comparable inputs", "different_functions": "Functions with raw differences", "diagnostic_count": "FP flag differences (diagnostic only)",
        "explorer": "Explore by function", "steps": "1 Search by name or ISA · 2 Choose a function · 3 Compare input and return bytes",
        "search": "Search function, prototype, or ISA", "all": "All", "diff": "Raw differences", "cpu": "CPU unsupported · unverified",
        "no_functions": "No functions match this filter.", "more": "Show 100 more functions", "showing": "{shown} / {total} functions",
        "choose": "Choose a function from the list.", "guide": "Open in Intel Intrinsics Guide",
        "function_result": "Results for this function", "raw_details": "Every raw difference", "no_raw": "No returned-byte differences were observed in the comparable inputs from this run.",
        "not_tested": "The Intel CPU does not support this instruction, so port correctness remains unverified. A target output alone does not establish agreement with an Intel reference.",
        "not_compared": "Some inputs for this function could not be compared with an Intel reference. They are excluded from match counts.",
        "approximate": "Review candidate: an approximate instruction. Check its allowed error against the Guide. Raw differences have not been removed or adjusted.",
        "undefined": "Review candidate: a widening cast with undefined upper lanes. Raw differences in undefined regions alone cannot establish nonconformance. All observed bytes remain unchanged.",
        "review": "These are name-based review notes, not rewritten verdicts.",
        "input": "Input", "arguments": "Hex by argument", "no_arguments": "No arguments", "encoded": "All encoded input bytes",
        "return": "Return bytes", "intel": "Intel reference", "target": "Target port", "xor": "XOR (different bits)", "byte_changes": "Different bytes: {n} / {total}",
        "flags": "FP flags (diagnostic)", "none": "none", "rounding": "Rounding", "byte_order": "Bytes appear in ascending memory-address order. Arguments follow the prototype and execution layout. Multibyte values are little-endian.",
        "downloads": "Download verification data", "download_note": "You do not need to work with JSON to read this page. Download the complete inputs and outputs here if you want to reuse them.",
        "input_download": "All shared inputs", "intel_download": "All Intel results", "target_download": "All target results", "diff_download": "All difference records", "summary_download": "Summary data",
        "noscript": "Enable JavaScript for function search and raw-difference details. Totals and downloads remain available.",
        "updated": "Assembled", "source": "Execution source", "rows": "inputs", "function": "Function", "raw_short": "raw", "cpu_short": "unverified",
    },
}


CSS = r"""
:root{color-scheme:light;--ink:#342d40;--muted:#726979;--purple:#6c5198;--purple-soft:#eee7f7;--peach:#f4dfca;--paper:#fffdfb;--line:#e9e1e7;--orange:#99562f}*{box-sizing:border-box}body{margin:0;background:#faf7f4;color:var(--ink);font:15px/1.65 system-ui,-apple-system,BlinkMacSystemFont,"Hiragino Kaku Gothic ProN","Yu Gothic",sans-serif}a{color:#64438f;text-underline-offset:3px}button,input{font:inherit}button{cursor:pointer}button:focus-visible,a:focus-visible,input:focus-visible{outline:3px solid #b88edc;outline-offset:3px}header{max-width:1450px;margin:auto;padding:24px 32px 10px;display:flex;align-items:center;justify-content:space-between;gap:20px}.brand{font-weight:800;font-size:22px;letter-spacing:-.7px;text-decoration:none;color:var(--ink)}.brand span{color:var(--purple)}nav{display:flex;align-items:center;gap:16px;flex-wrap:wrap;font-size:13px}.language{border:1px solid var(--line);border-radius:999px;padding:5px 12px;text-decoration:none;background:white}main{max-width:1450px;margin:auto;padding:18px 32px 50px}.eyebrow{font-size:12px;font-weight:750;letter-spacing:.13em;text-transform:uppercase;color:var(--purple);margin:0 0 10px}.hero{padding:36px;border-radius:24px;background:linear-gradient(115deg,#f0e6f5,#f7e8dc);border:1px solid #e9deeb}h1{font-size:clamp(28px,3vw,43px);line-height:1.25;letter-spacing:-.035em;margin:0 0 16px;max-width:1000px}.lead{margin:0;max-width:960px;color:#5b5064}.metrics{display:grid;grid-template-columns:repeat(4,1fr);gap:14px;margin:22px 0}.metric{padding:20px 22px;background:var(--paper);border:1px solid var(--line);border-radius:16px}.metric strong{display:block;font-size:32px;line-height:1.2;letter-spacing:-.04em}.metric span{display:block;color:var(--muted);font-size:12px;margin-top:8px}.metric.caution{background:#fff3e7}.scope{border-left:4px solid #b393cc;background:#fffdfb;border-radius:0 12px 12px 0;padding:15px 20px;margin:18px 0;color:#62566b;font-size:13px}.scope p{margin:4px 0}.cpu-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:16px;margin:22px 0 30px}.cpu-card{background:var(--paper);border:1px solid var(--line);border-radius:18px;padding:22px}.cpu-card h2{font-size:17px;margin:0 0 16px}.cpu-card .big{font-size:32px;font-weight:750;letter-spacing:-.04em}.cpu-card .small{color:var(--muted);font-size:12px}.cpu-card .pair{display:flex;justify-content:space-between;gap:16px;font-size:13px;margin:7px 0}.bar{height:7px;background:#eee8ee;border-radius:6px;overflow:hidden;margin:15px 0}.bar i{display:block;height:100%;background:#a185b8}.button-link{display:inline-block;background:var(--purple-soft);padding:8px 13px;border-radius:9px;font-size:13px;font-weight:650;text-decoration:none;margin-top:14px}h2.section-title{font-size:23px;letter-spacing:-.03em;margin:28px 0 4px}.hint{font-size:13px;color:var(--muted);margin:4px 0 16px}.explorer{display:grid;grid-template-columns:minmax(300px,360px) minmax(0,1fr);gap:20px;align-items:start}.sidebar{background:var(--paper);border:1px solid var(--line);border-radius:18px;overflow:hidden;position:sticky;top:16px}.search-tools{padding:18px;border-bottom:1px solid var(--line)}#search{width:100%;border:1px solid #d9cedf;border-radius:10px;padding:11px 12px;background:white;color:var(--ink);font-size:13px}.filters{display:flex;gap:6px;flex-wrap:wrap;margin-top:12px}.filters button,.role-tabs button{border:1px solid #e0d5e6;background:white;border-radius:8px;padding:7px 9px;font-size:11px;color:#6b5c77}.filters button.active,.role-tabs button.active{background:var(--purple);border-color:var(--purple);color:white}.list-meta{padding:10px 18px;color:var(--muted);font-size:11px;background:#fcf9fd}.function-list{max-height:620px;overflow-y:auto}.function-row{display:block;width:100%;text-align:left;border:0;border-bottom:1px solid #f0eaf0;background:transparent;padding:13px 17px;color:var(--ink)}.function-row:hover{background:#f8f2fc}.function-row.selected{background:#f0e8f7;box-shadow:inset 3px 0 var(--purple)}.function-name{display:block;font:600 12px/1.5 ui-monospace,SFMono-Regular,Consolas,monospace;overflow-wrap:anywhere}.function-summary{display:flex;justify-content:space-between;gap:8px;font-size:10px;color:var(--muted);margin-top:5px}.badge{display:inline-block;border-radius:6px;padding:1px 6px;font-size:10px;background:#f8eadb;color:#8b4c28}.badge.unverified{background:#ede9ef;color:#716579}#more{display:block;width:calc(100% - 28px);margin:12px 14px;border:1px solid var(--line);border-radius:9px;background:#f7f2fa;color:var(--purple);padding:8px;font-size:12px}.detail{min-width:0;background:var(--paper);border:1px solid var(--line);border-radius:18px;padding:26px}.detail h2{font:700 clamp(17px,2vw,23px)/1.4 ui-monospace,SFMono-Regular,Consolas,monospace;overflow-wrap:anywhere;margin:0 0 12px}.prototype{font:12px/1.7 ui-monospace,SFMono-Regular,Consolas,monospace;background:#f7f3f8;border:1px solid #eee5f0;border-radius:10px;padding:13px;white-space:pre-wrap;overflow-wrap:anywhere;margin:13px 0}.guide-link{font-size:12px}.isa-tags{display:flex;gap:6px;flex-wrap:wrap;margin:10px 0}.isa{font-size:10px;color:#69517e;background:#f0e9f7;padding:2px 7px;border-radius:5px}.role-tabs{display:flex;gap:7px;flex-wrap:wrap;margin:20px 0 12px}.role-tabs button{font-size:12px;padding:7px 12px}.function-counts{display:grid;grid-template-columns:repeat(3,1fr);gap:10px;margin:17px 0}.function-counts div{background:#f7f4f8;border-radius:9px;padding:12px;font-size:11px;color:var(--muted)}.function-counts strong{display:block;color:var(--ink);font-size:22px;line-height:1.3}.notice{background:#fff0df;border:1px solid #f0dcc5;padding:13px 15px;border-radius:10px;font-size:12px;margin:12px 0;color:#775334}.notice.unverified{background:#f0edf3;border-color:#e0d7e7;color:#665973}.fine{font-size:11px;color:var(--muted);margin:9px 0}.detail h3{font-size:17px;margin:24px 0 8px}.raw-record{border:1px solid #e7dfe9;border-radius:12px;margin:12px 0;overflow:hidden}.raw-record>summary{cursor:pointer;padding:13px 15px;background:#f8f4fa;font-size:12px;font-weight:650;display:list-item}.record-body{padding:15px}.input-id{font:10px/1.6 ui-monospace,SFMono-Regular,Consolas,monospace;color:#83728f;overflow-wrap:anywhere}.argument{border-bottom:1px solid #f0e9f0;padding:10px 0}.arg-heading{font-size:11px;color:#6d5e78;display:flex;gap:10px;flex-wrap:wrap}.arg-heading strong{font-family:ui-monospace,SFMono-Regular,Consolas,monospace;color:#4b395c}.hex{display:block;font:11px/1.9 ui-monospace,SFMono-Regular,Consolas,monospace;overflow-wrap:anywhere;white-space:pre-wrap;word-break:break-all;background:#fbf8fc;border:1px solid #f0e7f3;padding:9px 11px;border-radius:7px;margin:5px 0}.byte.changed{background:#f1cda7;color:#6e3f1f;border-radius:2px}.returns{margin-top:14px}.return-label{font-size:11px;font-weight:650;color:#6b557a}.xor{color:#8b633f}.flags{display:grid;grid-template-columns:repeat(2,1fr);gap:9px;font-size:11px;margin-top:10px}.flags div{padding:9px;background:#f8f5f9;border-radius:8px}.flags strong{display:block;color:#6d557d}.flags code{font:11px/1.6 ui-monospace,SFMono-Regular,Consolas,monospace}.encoded{font-size:11px;color:#7a6887;margin-top:12px}.downloads{margin-top:30px;background:#f0e9f5;border:1px solid #e3d8e9;padding:22px;border-radius:16px}.downloads h2{font-size:18px;margin:0 0 6px}.downloads p{font-size:12px;color:#6a5b74}.downloads-links{display:flex;flex-wrap:wrap;gap:9px}.downloads a{font-size:11px;background:white;border:1px solid #e4d9eb;border-radius:7px;padding:7px 11px;text-decoration:none}footer{font-size:11px;color:#8a7d90;padding-top:24px;display:flex;flex-wrap:wrap;gap:10px 24px}.empty{padding:24px;color:var(--muted);font-size:13px}.hidden{display:none!important}[hidden]{display:none!important}@media(max-width:1050px){.explorer{grid-template-columns:300px minmax(0,1fr)}.metric strong{font-size:28px}.hero{padding:28px}}@media(max-width:800px){header{padding:18px 18px 6px;align-items:flex-start}main{padding:12px 16px 30px}nav{gap:9px;font-size:11px}.hero{padding:24px 20px;border-radius:18px}.metrics{grid-template-columns:repeat(2,1fr);gap:9px;margin:14px 0}.metric{padding:16px}.metric strong{font-size:27px}.cpu-grid{grid-template-columns:1fr;gap:10px}.cpu-card{padding:18px}.explorer{grid-template-columns:1fr}.sidebar{position:relative;top:0}.function-list{max-height:300px}.detail{padding:19px}.function-counts{gap:7px}.function-counts div{padding:9px;font-size:10px}.flags{grid-template-columns:1fr}.scope{font-size:12px}h2.section-title{margin-top:22px}}@media(prefers-reduced-motion:no-preference){button,a{transition:background .12s,color .12s}}
"""


JS = r"""
(()=>{'use strict';
const data=JSON.parse(document.getElementById('report-data').textContent),t=data.text;
const cases=data.cases,byName=new Map(cases.map(c=>[c.name,c])),roles=data.roles,labels=data.role_labels;
const list=document.getElementById('function-list'),detail=document.getElementById('detail'),search=document.getElementById('search'),more=document.getElementById('more'),meta=document.getElementById('list-meta');
let filter='diff',limit=100,selected=null,selectedRole=data.role||roles[0];
const number=new Intl.NumberFormat(data.language),fmt=n=>number.format(n);
function el(tag,cls,text){const n=document.createElement(tag);if(cls)n.className=cls;if(text!==undefined)n.textContent=text;return n;}
function replace(s,values){return s.replace(/\{(\w+)\}/g,(_,k)=>values[k]);}
function counts(c){const rs=data.role?[data.role]:roles;return rs.reduce((a,r)=>a.map((x,i)=>x+c.counts[r][i]),[0,0,0]);}
function cpu(c){return (c.intel_status.unsupported_cpu||0)>0;}
function filtered(){const q=search.value.trim().toLowerCase();return cases.filter(c=>(!q||[c.name,c.prototype,...c.isa].join(' ').toLowerCase().includes(q))&&(filter==='all'||filter==='diff'&&counts(c)[1]>0||filter==='cpu'&&cpu(c)));}
function renderList(){const found=filtered();list.replaceChildren();meta.textContent=replace(t.showing,{shown:fmt(Math.min(limit,found.length)),total:fmt(found.length)});more.hidden=found.length<=limit;
 if(!found.length){list.append(el('p','empty',t.no_functions));return;}
 for(const c of found.slice(0,limit)){const n=counts(c),button=el('button','function-row'+(selected===c.name?' selected':''));button.type='button';button.dataset.name=c.name;button.append(el('span','function-name',c.name));const sub=el('span','function-summary');sub.append(el('span','',c.isa.join(' · ')));sub.append(el('span','badge'+(n[1]?'':' unverified'),n[1]?t.raw_short+' '+fmt(n[1]):cpu(c)?t.cpu_short:fmt(n[0])+' '+t.rows));button.append(sub);button.addEventListener('click',()=>choose(c.name));list.append(button);}}
function hex(value,other){const code=el('code','hex');code.append(document.createTextNode('0x'));for(let i=2;i<value.length;i+=2){const same=!other||value.slice(i,i+2)===other.slice(i,i+2);code.append(el('span','byte'+(same?'':' changed'),value.slice(i,i+2)));}return code;}
function flags(value){const names=['invalid','divide-by-zero','overflow','underflow','inexact'].filter((_,i)=>value&(1<<i));return '0x'+value.toString(16).padStart(2,'0')+' · '+(names.join(', ')||t.none);}
function record(c,row,index){const [id,input,intel,target,iflags,tflags,rounding]=row,box=el('details','raw-record');const head=el('summary','',t.input+' '+fmt(index+1)+' · '+id.slice(0,12));box.append(head);let built=false;
 function build(){if(built)return;built=true;const body=el('div','record-body');body.append(el('div','input-id','input_id: '+id),el('p','fine',t.rounding+': '+rounding),el('strong','return-label',t.arguments));
 if(!c.arguments.length)body.append(el('p','fine',t.no_arguments));for(const [name,type,offset,size] of c.arguments){const a=el('div','argument'),heading=el('div','arg-heading');heading.append(el('strong','',name),el('span','',type+' · '+size+' bytes'));a.append(heading,hex('0x'+input.slice(2+offset*2,2+(offset+size)*2)));body.append(a);}
 const all=el('details','encoded');all.append(el('summary','',t.encoded),hex(input));body.append(all);const output=el('div','returns');output.append(el('div','return-label',t.return+' · '+t.intel),hex(intel,target),el('div','return-label',t.return+' · '+labels[selectedRole]),hex(target,intel));let changed=0,xor='0x';for(let i=2;i<intel.length;i+=2){const v=parseInt(intel.slice(i,i+2),16)^parseInt(target.slice(i,i+2),16);if(v)changed++;xor+=v.toString(16).padStart(2,'0');}output.append(el('p','fine',replace(t.byte_changes,{n:fmt(changed),total:fmt((intel.length-2)/2)})));const x=el('details','encoded xor');x.append(el('summary','',t.xor),hex(xor));output.append(x);body.append(output,el('p','fine',t.flags));const fp=el('div','flags');for(const [label,value] of [[t.intel,iflags],[labels[selectedRole],tflags]]){const f=el('div');f.append(el('strong','',label),el('code','',flags(value)));fp.append(f);}body.append(fp);box.append(body);}
 box.addEventListener('toggle',()=>{if(box.open)build();});if(index===0){box.open=true;build();}return box;}
function renderDetail(){const c=byName.get(selected);detail.replaceChildren();if(!c){detail.append(el('p','empty',t.choose));return;}detail.append(el('h2','',c.name));const tags=el('div','isa-tags');for(const isa of c.isa)tags.append(el('span','isa',isa));detail.append(tags,el('pre','prototype',c.prototype));const guide=el('a','guide-link',t.guide+' ↗');guide.href=c.guide;guide.target='_blank';guide.rel='noopener noreferrer';detail.append(guide);
 if(!data.role){const tabs=el('div','role-tabs');for(const r of roles){const b=el('button',r===selectedRole?'active':'',labels[r]);b.type='button';b.setAttribute('aria-pressed',String(r===selectedRole));b.addEventListener('click',()=>{selectedRole=r;renderDetail();});tabs.append(b);}detail.append(tabs);}
 const values=c.counts[selectedRole],stats=el('div','function-counts');for(const [label,value]of[[t.matched,values[0]],[t.raw,values[1]],[t.uncompared,values[2]]]){const s=el('div');s.append(el('strong','',fmt(value)),document.createTextNode(label));stats.append(s);}detail.append(stats);
 if(cpu(c))detail.append(el('p','notice unverified',t.not_tested));else if(values[2])detail.append(el('p','notice unverified',t.not_compared));const note=c.notes[selectedRole];if(note.approximate)detail.append(el('p','notice',t.approximate));if(note.undefined)detail.append(el('p','notice',t.undefined));if(note.approximate||note.undefined)detail.append(el('p','fine',t.review));
 const rows=(data.raw[selectedRole]||{})[c.name]||[];detail.append(el('h3','',t.raw_details+' · '+fmt(rows.length)),el('p','fine',t.byte_order),el('p','fine',t.diagnostic));if(!rows.length&&!cpu(c))detail.append(el('p','notice unverified',t.no_raw));rows.forEach((row,i)=>detail.append(record(c,row,i)));}
function choose(name){selected=name;const c=byName.get(name);if(!data.role&&c&&c.counts[selectedRole][1]===0){const best=roles.find(r=>c.counts[r][1]>0);if(best)selectedRole=best;}history.replaceState(null,'','#'+encodeURIComponent(name));renderList();renderDetail();}
for(const button of document.querySelectorAll('[data-filter]')){const kind=button.dataset.filter;const count=cases.filter(c=>kind==='all'||kind==='diff'&&counts(c)[1]>0||kind==='cpu'&&cpu(c)).length;button.textContent=({all:t.all,diff:t.diff,cpu:t.cpu})[kind]+' '+fmt(count);button.addEventListener('click',()=>{filter=kind;limit=100;for(const b of document.querySelectorAll('[data-filter]')){b.classList.toggle('active',b===button);b.setAttribute('aria-pressed',String(b===button));}renderList();});}
search.addEventListener('input',()=>{limit=100;renderList();});more.addEventListener('click',()=>{limit+=100;renderList();});
let initial;try{initial=decodeURIComponent(location.hash.slice(1));}catch(_){initial='';}selected=byName.has(initial)?initial:(filtered()[0]||cases[0]||{}).name;renderList();renderDetail();
})();
"""


def _metric(value, label, caution=False):
    return f'<div class="metric{" caution" if caution else ""}"><strong>{value:,}</strong><span>{escape(label)}</span></div>'


def _cpu_cards(data, language, role):
    t, suffix = TEXT[language], ".en" if language == "en" else ""
    cards = []
    for target in (ROLES if role is None else (role,)):
        n = data["totals"][target]
        percent = 100 * n["comparable"] / data["input_count"] if data["input_count"] else 0
        link = f'{target}/index{suffix}.html' if role is None else "#explorer"
        cards.append(f'''<article class="cpu-card"><h2>{ROLE_LABELS[target]}</h2><div class="big">{n["different"]:,}</div><div class="small">{escape(t["raw"])}</div>
<div class="bar" aria-hidden="true"><i style="width:{percent:.4f}%"></i></div><div class="pair"><span>{escape(t["comparable"])}</span><strong>{n["comparable"]:,} / {data["input_count"]:,}</strong></div>
<div class="pair"><span>{escape(t["matched"])}</span><strong>{n["matched"]:,}</strong></div><div class="pair"><span>{escape(t["uncompared"])}</span><strong>{n["uncompared"]:,}</strong></div>
<div class="pair"><span>{escape(t["different_functions"])}</span><strong>{n["functions_different"]:,}</strong></div><div class="pair"><span>{escape(t["diagnostic_count"])}</span><strong>{n["fp_diagnostic"]:,}</strong></div>
<a class="button-link" href="{link}">{escape(t["see"])} →</a></article>''')
    return '<section class="cpu-grid"' + (' style="grid-template-columns:1fr"' if role else '') + '>' + "".join(cards) + '</section>'


def _html(data, language, role):
    t = TEXT[language]
    suffix = ".en" if language == "en" else ""
    parent = "../" if role else ""
    switch = "index.html" if language == "en" else "index.en.html"
    title = (ROLE_LABELS[role] + " · " if role else "") + t["title"]
    nav = f'<a href="{parent}index{suffix}.html">{escape(t["overview"])}</a>' if role else ""
    for target in ROLES:
        nav += f'<a href="{parent}{target}/index{suffix}.html">{ROLE_LABELS[target].split(" / ")[0]}</a>'
    nav += f'<a class="language" href="{switch}" lang="{"ja" if language == "en" else "en"}">{"Japanese" if language == "en" else "English"}</a>'
    downloads = [(t["input_download"], parent + "data/inputs.jsonl.gz"),
                 (t["intel_download"], parent + "data/results-intel.jsonl.gz")]
    if role:
        downloads += [(t["target_download"], "../data/results-" + role + ".jsonl.gz"),
                      (t["diff_download"], "differences.jsonl.gz"), (t["summary_download"], "summary.json")]
    else:
        downloads += [(ROLE_LABELS[target] + " · " + t["target_download"], "data/results-" + target + ".jsonl.gz") for target in ROLES]
        downloads.append((t["summary_download"], "execution-summary.json"))
    links = "".join(f'<a href="{escape(url, quote=True)}" download>{escape(label)}</a>' for label, url in downloads)
    selected_data = {key: value for key, value in data.items() if key not in {"raw", "totals", "assembled_at", "source_commit"}}
    selected_data.update(language=language, role=role, roles=list(ROLES), role_labels=ROLE_LABELS, text=t,
                         raw={target: data["raw"][target] for target in ((role,) if role else ROLES)})
    embedded = json.dumps(selected_data, ensure_ascii=False, separators=(",", ":"))
    embedded = embedded.replace("&", "\\u0026").replace("<", "\\u003c").replace(">", "\\u003e").replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")
    scope = t["guide_scope"].format(n=f'{data["candidate_count"]:,}', g=f'{data["guide_count"]:,}')
    return f'''<!doctype html>
<html lang="{language}"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="color-scheme" content="light"><title>{escape(title)} · EquiLane</title><style>{CSS}</style></head>
<body><header><a class="brand" href="{parent}index{suffix}.html">Equi<span>Lane</span></a><nav aria-label="Navigation">{nav}</nav></header><main>
<section class="hero"><p class="eyebrow">Intel reference → {escape(ROLE_LABELS[role] if role else "ARM64 · OpenPOWER · RISC-V")}</p><h1>{escape(t["title"])}</h1><p class="lead">{escape(t["subtitle"])}</p></section>
<section class="metrics">{_metric(data["candidate_count"],t["functions"])}{_metric(data["input_count"],t["inputs"])}{_metric(data["intel_candidates"],t["reference"])}{_metric(data["unsupported_candidates"],t["unsupported"],True)}</section>
<aside class="scope"><p>{escape(t["scope"])}</p><p>{escape(t["diagnostic"])}</p><p>{escape(t["hardware"])} {escape(scope)}</p></aside>
{_cpu_cards(data,language,role)}<h2 class="section-title" id="explorer">{escape(t["explorer"])}</h2><p class="hint">{escape(t["steps"])}</p>
<section class="explorer"><aside class="sidebar"><div class="search-tools"><label for="search" class="fine">{escape(t["search"])}</label><input id="search" type="search" placeholder="_mm_add_ps / AVX2 / BMI1" autocomplete="off"><div class="filters" role="group"><button type="button" data-filter="all" aria-pressed="false">{escape(t["all"])}</button><button type="button" data-filter="diff" class="active" aria-pressed="true">{escape(t["diff"])}</button><button type="button" data-filter="cpu" aria-pressed="false">{escape(t["cpu"])}</button></div></div><div class="list-meta" id="list-meta" aria-live="polite"></div><div class="function-list" id="function-list"></div><button id="more" type="button">{escape(t["more"])}</button></aside>
<article class="detail" id="detail" aria-live="polite"><p>{escape(t["choose"])}</p></article></section><noscript><p class="scope">{escape(t["noscript"])}</p></noscript>
<section class="downloads"><h2>{escape(t["downloads"])}</h2><p>{escape(t["download_note"])}</p><div class="downloads-links">{links}</div></section>
<footer><span>{escape(t["updated"])}: {escape(data["assembled_at"])}</span><span>{escape(t["source"])}: <code>{escape(data["source_commit"][:12])}</code></span></footer>
</main><script id="report-data" type="application/json">{embedded}</script><script>{JS}</script></body></html>'''


def render_reports(source: Path, destination: Path, summary: dict):
    """Write eight dependency-free HTML pages; raw observations are unchanged."""
    source, destination = Path(source), Path(destination)
    data = _build_data(source, summary)
    written = []
    for role in (None, *ROLES):
        directory = destination / role if role else destination
        directory.mkdir(parents=True, exist_ok=True)
        for language, filename in (("ja", "index.html"), ("en", "index.en.html")):
            path = directory / filename
            path.write_text(_html(data, language, role), encoding="utf-8")
            written.append({"file": str(path.relative_to(destination)), "byte_length": path.stat().st_size})
    return {"files": written, "candidate_count": data["candidate_count"],
            "embedded_raw_differences": {role: sum(map(len, data["raw"][role].values())) for role in ROLES},
            "unsupported_candidates": data["unsupported_candidates"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--destination", required=True, type=Path)
    args = parser.parse_args()
    summary = _json(args.source / "execution-summary.json")
    print(json.dumps(render_reports(args.source, args.destination, summary), sort_keys=True))


if __name__ == "__main__":
    main()
