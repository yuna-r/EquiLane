"""Explain stored portable-model mismatch evidence without rerunning a model.

The bit decoder is independent of the comparison engine and does not decide
whether an oracle is correct. Group counts are derived from saved finding rows;
they are observations, not counts of distinct implementation defects.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from html import escape
import json
import re
import struct
from typing import Any, Mapping
from urllib.parse import urlsplit

from .intrinsic_guide import intrinsic_for_case

FAMILY_LABELS = {
    "nan_indefinite_sign": "NaN の符号",
    "nan_payload_priority": "NaN payload の選択",
    "rounding_arithmetic": "算術の丸め",
    "rounding_sqrt": "平方根の丸め",
    "rounding_float_to_integer": "浮動小数点 → 整数の丸め",
    "rounding_narrowing": "f64 → f32 の丸め",
    "rounding_integer_to_float": "整数 → 浮動小数点の丸め",
    "fp_boundary_float_to_integer": "浮動小数点 → 整数の境界",
    "fp_boundary_halfway_arithmetic": "算術の丸め中間値と隣接値",
    "fp_boundary_integer_to_float": "整数 → 浮動小数点の精度境界",
    "fp_boundary_nan_priority": "NaN payload と入力順序",
    "fp_boundary_narrowing": "f64 → f32 の丸め・桁落ち境界",
    "fp_boundary_signed_zero_infinity": "符号付きゼロ・無限大・無効演算",
    "fp_boundary_sqrt": "平方根の丸め境界",
    "fp_boundary_subnormal_arithmetic": "非正規数と通常数の境界",
}

_PATH_LABELS = {
    "nan_sign": "NaN の符号表現を扱う経路",
    "nan_payload": "NaN の payload 選択を扱う経路",
    "rounding_arithmetic": "算術の丸めを扱う経路",
    "rounding_sqrt": "平方根の丸めを扱う経路",
    "rounding_float_to_integer": "浮動小数点 → 整数の変換経路",
    "rounding_narrowing": "f64 → f32 の変換経路",
    "rounding_integer_to_float": "整数 → 浮動小数点の変換経路",
}


_EXPLANATIONS = {
    "nan_indefinite_sign": (
        "NaN の符号を調べる分類です。Python float を経由する演算と、SSE2 の無効演算で"
        "生成される NaN 表現の違いを確認します。符号だけの差かどうかは各レーンの XOR で確認できます。"
    ),
    "nan_payload_priority": (
        "複数の NaN 入力がある場合の payload 選択と quiet 化を調べる分類です。"
        "入力順序と Python float への変換・演算を経る経路を確認します。"
        "NaN 同士でも bit_exact 契約では符号・quiet bit・payload の差が不一致になります。"
    ),
    "arithmetic": (
        "算術モデルの float_binary_case を確認する分類です。Python float を経る演算に加え、"
        "除算には _divide_float_bits / _round_ratio_even の整数による nearest-even 経路があります。"
        "入力に保存された丸め指定を、それぞれの実装経路と照合します。"
        "中間値、桁落ち、非正規数の境界を含み、全件を単一の根本原因と断定しません。"
    ),
    "sqrt": (
        "sqrt_case の math.sqrt() → _float_to_bits 経路を確認する分類です。"
        "保存された丸め指定と、平方根の中間計算・最終変換の丸めを照合します。"
        "XOR の最下位ビットの差だけでは原因を確定できません。"
    ),
    "float_to_integer": (
        "conversion_case / _float_to_integer の round()・math.trunc() 経路を確認する分類です。"
        "要求した丸め方向、整数範囲外の扱い、SSE2 の無効変換値を入力と併せて確認します。"
    ),
    "narrowing": (
        "conversion_case / scalar_conversion_case → _f64_to_f32 の整数による nearest-even 変換を確認する分類です。"
        "精度を落とす位置の丸め方向、オーバーフロー、通常数と非正規数の境界を照合します。"
        "表示した差分は観測結果であり、修正後の出力を予測するものではありません。"
    ),
    "integer_to_float": (
        "conversion_case / scalar_conversion_case の整数 → 浮動小数点変換を確認する分類です。"
        "_i32_to_f32 の整数による nearest-even 経路と、float() / struct を使う経路があります。"
        "整数を完全には表せない精度境界で、要求した丸め方向と実際の変換結果を照合します。"
    ),
    "fp_boundary_signed_zero_infinity": (
        "符号付きゼロ、無限大、無効演算の境界をまとめた検証分類です。"
        "ゼロの符号の差と、無効演算が生成する NaN の差を区別してください。"
        "分類名だけから同じ原因とは判断しません。"
    ),
}


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _label(value: Any, default: str = "未記録") -> str:
    return value if isinstance(value, str) and value else default


def _text(value: Any) -> str:
    return escape(str(value), quote=True)


def _json(value: Any) -> str:
    """Display evidence even when optional presentation metadata is malformed."""

    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _python_summary(value: Any) -> Mapping[str, Any]:
    summary = _mapping(value)
    gates = _mapping(summary.get("gates"))
    return _mapping(gates["python_coverage"]) if "python_coverage" in gates else summary


def failure_path(family: str, scope: str) -> str:
    """Roll up observed test families; this is not a root-cause proof."""

    if family == "nan_indefinite_sign":
        return "nan_sign"
    if family in {"nan_payload_priority", "fp_boundary_nan_priority"}:
        return "nan_payload"
    if family == "fp_boundary_signed_zero_infinity":
        if scope == "official_nearest_even":
            return "nan_sign"
        if scope == "validated_rounding_extension":
            return "rounding_arithmetic"
        return family
    for suffix in ("float_to_integer", "integer_to_float", "narrowing", "sqrt"):
        if family in {"rounding_" + suffix, "fp_boundary_" + suffix}:
            return "rounding_" + suffix
    if family in {"rounding_arithmetic", "fp_boundary_halfway_arithmetic", "fp_boundary_subnormal_arithmetic"}:
        return "rounding_arithmetic"
    return family


def family_explanation(family: str) -> str:
    """Give a conservative reading guide for a saved test-family label."""

    if family in _EXPLANATIONS:
        return _EXPLANATIONS[family]
    for token in ("float_to_integer", "integer_to_float", "narrowing", "sqrt"):
        if token in family:
            return _EXPLANATIONS[token]
    if family == "fp_boundary_nan_priority":
        return _EXPLANATIONS["nan_payload_priority"]
    if family in {"rounding_arithmetic", "fp_boundary_halfway_arithmetic", "fp_boundary_subnormal_arithmetic"}:
        return _EXPLANATIONS["arithmetic"]
    return "保存された検証分類です。入力・契約・期待値の出典とビット差分を確認してください。分類名だけでは原因を特定できません。"


def decode_bits(value: Any, element: Any) -> dict[str, Any]:
    """Decode a fixed-width representation, preserving NaN and signed-zero bits."""

    result: dict[str, Any] = {"bits": value, "element": element, "kind": "unknown", "label": "形式未記録", "width": None}
    widths = {"f32": 32, "f64": 64, **{f"{signed}{width}": width for signed in "iu" for width in (8, 16, 32, 64)}}
    width = widths.get(element) if isinstance(element, str) else None
    if width is None or not isinstance(value, str) or not re.fullmatch(rf"0x[0-9a-fA-F]{{{width // 4}}}", value):
        return result
    number = int(value, 16)
    result.update(bits=f"0x{number:0{width // 4}x}", width=width, value=number)
    if not element.startswith("f"):
        signed = number - (1 << width) if element.startswith("i") and number >> (width - 1) else number
        result.update(kind="integer", label=f"{element}: {signed}", signed_value=signed)
        return result
    fraction_width, exponent_width = (23, 8) if width == 32 else (52, 11)
    fraction = number & ((1 << fraction_width) - 1)
    exponent = (number >> fraction_width) & ((1 << exponent_width) - 1)
    sign = number >> (width - 1)
    sign_label = "−" if sign else "+"
    result.update(sign=sign, exponent=exponent, fraction=fraction)
    if exponent == (1 << exponent_width) - 1:
        if fraction:
            quiet = bool(fraction & (1 << (fraction_width - 1)))
            payload = fraction & ((1 << (fraction_width - 1)) - 1)
            result.update(kind="nan", quiet=quiet, payload=payload,
                          label=f"{sign_label}{'quiet' if quiet else 'signaling'} NaN; payload=0x{payload:x}")
        else:
            result.update(kind="infinity", label=f"{sign_label}∞（無限大）")
    elif exponent == 0 and fraction == 0:
        result.update(kind="zero", label=f"{sign_label}0（符号付きゼロ）")
    else:
        numeric = struct.unpack(">f" if width == 32 else ">d", number.to_bytes(width // 8, "big"))[0]
        kind = "subnormal" if exponent == 0 else "normal"
        result.update(kind=kind, label=f"{numeric!r}（{'非正規数' if kind == 'subnormal' else '通常数'}）")
    return result


_MISSING = object()


def _lane(path: str, expected: Any, actual: Any, expected_element: Any, actual_element: Any) -> dict[str, Any]:
    left = decode_bits(expected, expected_element)
    right = decode_bits(actual, actual_element)
    changed = expected != actual or expected_element != actual_element
    xor = None
    changed_bits: list[int] = []
    if left["width"] is not None and left["width"] == right["width"]:
        xor_value = left["value"] ^ right["value"]
        changed = xor_value != 0 or expected_element != actual_element
        xor = f"0x{xor_value:0{left['width'] // 4}x}"
        changed_bits = [bit for bit in range(left["width"]) if xor_value & (1 << bit)]
    if expected is _MISSING or actual is _MISSING:
        classification = "レーン／値の欠落・追加"
    elif expected_element != actual_element:
        classification = "要素型の差"
    elif not changed:
        classification = "一致"
    elif left["kind"] == right["kind"] == "nan":
        parts = []
        if left["sign"] != right["sign"]:
            parts.append("符号")
        if left["quiet"] != right["quiet"]:
            parts.append("quiet bit")
        if left["payload"] != right["payload"]:
            parts.append("payload")
        classification = "NaN の" + "・".join(parts) + "差" if parts else "NaN 表現の差"
    elif left["kind"] == right["kind"] == "zero":
        classification = "符号付きゼロの符号差"
    elif left["kind"] != right["kind"]:
        classification = f"分類の差: {left['kind']} → {right['kind']}"
    else:
        classification = "ビット表現の差" if xor is not None else "値／構造の差"
    return {
        "path": path or "observed", "expected": "〈欠落〉" if expected is _MISSING else expected,
        "actual": "〈欠落〉" if actual is _MISSING else actual,
        "expected_class": left["label"], "actual_class": right["label"],
        "classification": classification, "changed": changed, "xor": xor,
        "changed_bits": changed_bits, "changed_bit_count": len(changed_bits) if xor is not None else None,
    }


def lane_diff(row: Any) -> list[dict[str, Any]]:
    """List vector/scalar lane differences and changed memory bytes from a finding."""

    finding = _mapping(row)
    results: list[dict[str, Any]] = []

    def walk(left: Any, right: Any, path: str) -> None:
        lhs, rhs = _mapping(left), _mapping(right)
        if "lanes" in lhs or "lanes" in rhs:
            left_lanes = lhs.get("lanes") if isinstance(lhs.get("lanes"), list) else []
            right_lanes = rhs.get("lanes") if isinstance(rhs.get("lanes"), list) else []
            for index in range(max(len(left_lanes), len(right_lanes))):
                results.append(_lane(f"{path}.lanes[{index}]",
                    left_lanes[index] if index < len(left_lanes) else _MISSING,
                    right_lanes[index] if index < len(right_lanes) else _MISSING,
                    lhs.get("element"), rhs.get("element")))
            if not left_lanes and not right_lanes and left != right:
                results.append(_lane(path, left, right, None, None))
            return
        if "bits" in lhs or "bits" in rhs:
            results.append(_lane(path + ".bits", lhs.get("bits", _MISSING), rhs.get("bits", _MISSING),
                                 lhs.get("element"), rhs.get("element")))
            return
        if "bytes" in lhs or "bytes" in rhs:
            left_bytes, right_bytes = lhs.get("bytes"), rhs.get("bytes")
            valid = lambda value: isinstance(value, str) and re.fullmatch(r"0x(?:[0-9a-fA-F]{2})*", value)
            if valid(left_bytes) and valid(right_bytes):
                for index in range(max((len(left_bytes) - 2) // 2, (len(right_bytes) - 2) // 2)):
                    offset = 2 + index * 2
                    a = "0x" + left_bytes[offset:offset + 2] if offset < len(left_bytes) else _MISSING
                    b = "0x" + right_bytes[offset:offset + 2] if offset < len(right_bytes) else _MISSING
                    if a != b:
                        results.append(_lane(f"{path}.bytes[{index}]", a, b, "u8", "u8"))
                if lhs.get("byte_offset") != rhs.get("byte_offset"):
                    results.append(_lane(path + ".byte_offset", lhs.get("byte_offset"), rhs.get("byte_offset"), None, None))
            elif left != right:
                results.append(_lane(path, left, right, None, None))
            return
        if isinstance(left, Mapping) or isinstance(right, Mapping):
            for key in sorted(set(lhs) | set(rhs), key=str):
                walk(lhs.get(key, _MISSING), rhs.get(key, _MISSING), f"{path}.{key}" if path else str(key))
        elif isinstance(left, list) or isinstance(right, list):
            a, b = left if isinstance(left, list) else [], right if isinstance(right, list) else []
            for index in range(max(len(a), len(b))):
                walk(a[index] if index < len(a) else _MISSING, b[index] if index < len(b) else _MISSING, f"{path}[{index}]")
        elif left != right:
            results.append(_lane(path, left, right, None, None))

    walk(finding.get("expected", _MISSING), finding.get("actual", _MISSING), "")
    return results


def build_mismatch_details(summary: Any) -> dict[str, Any]:
    """Build conserved counts and lane diagnostics from a gate or runner summary."""

    # The arithmetic explainer may reuse this module's bit decoder.
    from .operation_explanation import explain_operation

    python = _python_summary(summary)
    raw = python.get("model_output_mismatches")
    raw_rows = raw if isinstance(raw, list) else []
    rows = []
    groups: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    family_counts: Counter[str] = Counter()
    ids: set[str] = set()
    cases: set[str] = set()
    malformed = 0
    for index, raw_row in enumerate(raw_rows, 1):
        item = _mapping(raw_row)
        record = _mapping(item.get("input"))
        case_id = _label(record.get("case_id"))
        input_id = _label(record.get("input_id"))
        rounding = _label(_mapping(record.get("environment")).get("rounding"))
        scope = _label(item.get("contract_scope"))
        family = _label(item.get("finding_family"), "unclassified")
        if input_id != "未記録":
            ids.add(input_id)
        if case_id != "未記録":
            cases.add(case_id)
        if not item or not record or "expected" not in item or "actual" not in item:
            malformed += 1
        row = {"index": index, "case_id": case_id, "input_id": input_id,
               "rounding": rounding, "contract_scope": scope, "finding_family": family,
               "family_label": FAMILY_LABELS.get(family, family), "raw": raw_row,
               "intrinsic": intrinsic_for_case(case_id), "operation": dict(_mapping(explain_operation(item))),
               "failure_path": failure_path(family, scope), "lanes": lane_diff(item)}
        rows.append(row)
        groups[(case_id, rounding, scope)].append(row)
        family_counts[family] += 1
    declared = _mapping(python.get("failure_classification")).get("portable_model_output_mismatches")
    declared = declared if type(declared) is int and declared >= 0 else None
    grouped = [{"case_id": case_id, "rounding": rounding, "contract_scope": scope,
                "intrinsic": members[0]["intrinsic"],
                "finding_count": len(members),
                "unique_input_count": len({row["input_id"] for row in members if row["input_id"] != "未記録"}),
                "families": dict(Counter(row["finding_family"] for row in members))}
               for (case_id, rounding, scope), members in sorted(groups.items())]
    return {"finding_count": len(rows), "unique_input_count": len(ids), "case_count": len(cases),
            "intrinsic_case_count": len({row["case_id"] for row in rows if row["intrinsic"]}),
            "missing_input_id_count": sum(row["input_id"] == "未記録" for row in rows),
            "malformed_record_count": malformed, "declared_mismatch_count": declared,
            "counts_match": declared == len(rows) if declared is not None else None,
            "groups": grouped, "families": dict(sorted(family_counts.items())), "rows": rows,
            "failure_paths": dict(sorted(Counter(row["failure_path"] for row in rows).items())),
            "execution_environment": python.get("execution_environment", {})}


_STYLES = """
.mismatch-details { margin-top:24px; }
.mismatch-details .finding-grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(200px,1fr)); gap:10px; }
.mismatch-details .finding-note { border-left:3px solid #bda071; padding:8px 14px; background:#fff8ea; }
.mismatch-details .finding-filters { display:grid; grid-template-columns:repeat(auto-fit,minmax(170px,1fr)); gap:10px; padding:16px 0; align-items:end; }
.mismatch-details label { display:grid; gap:5px; font-size:12px; min-width:0; }
.mismatch-details .finding-filters input,.mismatch-details .finding-filters select { width:100%; min-width:0; }
.mismatch-details input,.mismatch-details select,.mismatch-details button { font:inherit; min-height:40px; padding:7px 10px; border:1px solid #d7c9e3; border-radius:8px; max-width:100%; }
.mismatch-details .finding-pages { display:flex; align-items:center; gap:12px; flex-wrap:wrap; margin:12px 0; }
.mismatch-details table { width:100%; border-collapse:collapse; font-size:12px; }
.mismatch-details th,.mismatch-details td { text-align:left; padding:9px; border-bottom:1px solid #e6dfec; vertical-align:top; overflow-wrap:anywhere; }
.mismatch-details .finding-table { overflow-x:auto; }
.mismatch-details .finding-table table { min-width:700px; }
.mismatch-details code,.mismatch-details pre { font-family:ui-monospace,monospace; overflow-wrap:anywhere; }
.mismatch-details pre { white-space:pre-wrap; padding:12px; background:#f4eef5; }
.mismatch-details details { margin:12px 0; padding:12px; border:1px solid #e6d4dc; border-radius:10px; }
.mismatch-details summary { cursor:pointer; overflow-wrap:anywhere; }
.mismatch-details [hidden] { display:none !important; }
.mismatch-details .finding-changed { background:#fff2f4; }
@media print { .mismatch-details .finding-filters,.mismatch-details .finding-pages { display:none; } .mismatch-details .finding-row[hidden] { display:block !important; } }
"""


_SCRIPT = r"""
(() => {
  const root = document.getElementById('model-mismatch-details');
  if (!root) return;
  const get = id => root.querySelector('#finding-' + id);
  const cards = Array.from(root.querySelectorAll('.finding-row'));
  const controls = ['search','family','case','rounding','scope'].map(get);
  const normalize = value => String(value || '').normalize('NFKC').toLowerCase();
  const searches = cards.map(card => normalize(card.dataset.search));
  const pageSize = 25;
  let page = 0, selected = [];
  const paint = () => {
    const pages = Math.max(1, Math.ceil(selected.length / pageSize));
    page = Math.max(0, Math.min(page, pages - 1));
    cards.forEach(card => { card.hidden = true; });
    selected.slice(page * pageSize, (page + 1) * pageSize).forEach(card => { card.hidden = false; });
    get('count').textContent = `該当 ${selected.length} / ${cards.length} 件 · ${page + 1} / ${pages} ページ`;
    get('empty').hidden = selected.length !== 0;
    get('previous').disabled = page === 0;
    get('next').disabled = (page + 1) * pageSize >= selected.length;
  };
  const filter = () => {
    const terms = normalize(get('search').value).trim().split(/\s+/).filter(Boolean);
    selected = cards.filter((card, index) => terms.every(term => searches[index].includes(term)) &&
      ['family','case','rounding','scope'].every(name => !get(name).value || card.dataset[name] === get(name).value));
    page = 0; paint();
  };
  controls.forEach(control => control.addEventListener(control === get('search') ? 'input' : 'change', filter));
  get('reset').addEventListener('click', () => { controls.forEach(control => { control.value = ''; }); filter(); get('search').focus(); });
  get('previous').addEventListener('click', () => { page -= 1; paint(); });
  get('next').addEventListener('click', () => { page += 1; paint(); });
  root.querySelectorAll('[data-finding-index]').forEach(link => link.addEventListener('click', () => {
    controls.forEach(control => { control.value = ''; });
    selected = cards;
    const index = Number(link.dataset.findingIndex) - 1;
    if (!Number.isInteger(index) || index < 0 || index >= cards.length) return;
    page = Math.floor(index / pageSize); paint(); cards[index].open = true;
  }));
  filter();
})();
"""


def _options(values: list[str], labels: Mapping[str, str] | None = None) -> str:
    return '<option value="">すべて</option>' + "".join(
        f'<option value="{_text(value)}">{_text(labels.get(value, value) if labels else value)}</option>'
        for value in sorted(set(values)))


def _lane_table(lanes: list[dict[str, Any]]) -> str:
    if not lanes:
        return "<p>デコードできるレーン差分はありません。保存した JSON を確認してください。</p>"
    rows = []
    for lane in lanes:
        changed = "finding-changed" if lane["changed"] else ""
        bits = ", ".join(map(str, lane["changed_bits"])) if lane["xor"] is not None else "算出不可"
        count = f'{lane["changed_bit_count"]} bit' if lane["xor"] is not None else "算出不可"
        rows.append(f'''<tr class="{changed}"><th scope="row">{_text(lane['path'])}</th>
          <td><code>{_text(lane['expected'])}</code><br>{_text(lane['expected_class'])}</td>
          <td><code>{_text(lane['actual'])}</code><br>{_text(lane['actual_class'])}</td>
          <td><code>{_text(lane['xor'] or '算出不可')}</code><br>{_text(count)}; bit {_text(bits)}</td>
          <td>{_text(lane['classification'])}</td></tr>''')
    return '<div class="finding-table"><table><thead><tr><th>レーン／位置</th><th>期待値</th><th>実測値</th><th>XOR / 変化ビット</th><th>差の分類</th></tr></thead><tbody>' + "".join(rows) + '</tbody></table></div>'


def _intrinsic_markup(value: Any, *, compact: bool = False) -> str:
    intrinsic = _mapping(value)
    if not intrinsic:
        return '<span>Guide 対応：未記録</span>'
    name = _label(intrinsic.get("name"))
    url = intrinsic.get("guide_url")
    safe_url = False
    if isinstance(url, str):
        try:
            parsed = urlsplit(url)
            safe_url = (parsed.scheme == "https" and parsed.netloc == "www.intel.com"
                        and parsed.path.startswith("/content/www/us/en/docs/intrinsics-guide/"))
        except ValueError:
            pass
    link = (f'<a href="{_text(url)}" target="_blank" rel="noopener noreferrer"><code>{_text(name)}</code></a>'
            if safe_url else f'<code>{_text(name)}</code>')
    basic = f'{link} ／ Guide ISA: <strong>{_text(_label(intrinsic.get("isa")))}</strong>'
    if compact:
        return basic
    instructions = intrinsic.get("instructions")
    instruction_text = ", ".join(str(item) for item in instructions) if isinstance(instructions, list) else "未記録"
    required_isa = intrinsic.get("framework_required_isa")
    requirements = " / ".join(str(item) for item in required_isa) if isinstance(required_isa, list) else "未記録"
    binding_note = intrinsic.get("binding_note")
    note = f'<p>{_text(binding_note)}</p>' if isinstance(binding_note, str) and binding_note else ""
    return f'''<div class="finding-intrinsic"><p>Intel Intrinsics Guide：{basic}</p>
      <p>{_text(_label(intrinsic.get('summary_ja')))}</p>
      {note}
      <details><summary>Guide のシグネチャ・命令との対応</summary>
        <pre>{_text(_label(intrinsic.get('prototype')))}</pre>
        <p>このケースの呼び出し：<code>{_text(_label(intrinsic.get('case_call')))}</code>。</p>
        <p>ヘッダー：<code>{_text(_label(intrinsic.get('header')))}</code>。
        命令：<code>{_text(instruction_text)}</code>。</p>
        <p>フレームワーク要求ISA：{_text(requirements)}。Guide の ISA 分類とは区別します。</p></details></div>'''


def _operation_markup(value: Any) -> str:
    operation = _mapping(value)
    if not operation:
        return '<p>計算の説明に必要な情報が不足しています。保存した入力と契約を確認してください。</p>'
    steps = operation.get("steps")
    items = "".join(f'<li>{_text(step)}</li>' for step in steps) if isinstance(steps, list) else ""
    return f'''<div class="finding-operation"><h4>{_text(_label(operation.get('title'), 'この入力で行う計算'))}</h4>
      <p>{_text(_label(operation.get('description')))}</p>{'<ol>' + items + '</ol>' if items else ''}</div>'''


def render_mismatch_details(summary: Any) -> str:
    """Render a self-contained, searchable Japanese explanation of saved findings."""

    details = build_mismatch_details(summary)
    rows = details["rows"]
    if not rows:
        note = "不一致の詳細行は保存されていません。"
        if details["declared_mismatch_count"]:
            note += f' 集計には {details["declared_mismatch_count"]:,} 件あるため、元の summary.json を確認してください。'
        return f'<section class="mismatch-details" id="model-mismatch-details"><h3>不一致の詳細</h3><p>{_text(note)}</p></section>'
    grouped = "".join(f'''<tr><th scope="row">{_text(group['case_id'])}</th><td>{_intrinsic_markup(group['intrinsic'], compact=True)}</td><td>{_text(group['rounding'])}</td>
        <td>{_text(group['contract_scope'])}</td><td>{group['finding_count']:,}</td><td>{group['unique_input_count']:,}</td></tr>'''
        for group in details["groups"])
    families = []
    for family, count in details["families"].items():
        first = next(row for row in rows if row["finding_family"] == family)
        families.append(f'''<details><summary>{_text(FAMILY_LABELS.get(family, family))} — {count:,} 件</summary>
          <p>{_text(family_explanation(family))}</p><p>代表例：<a data-finding-index="{first['index']}" href="#finding-record-{first['index']}">{_text(first['case_id'])}</a>
          ／ {_text(first['rounding'])} ／ {_text(first['contract_scope'])}。</p>
          {_intrinsic_markup(first['intrinsic'])}{_operation_markup(first['operation'])}
          {_lane_table(first['lanes'])}</details>''')
    paths = "".join(f'<tr><th scope="row">{_text(_PATH_LABELS.get(path, path))}</th><td>{count:,}</td></tr>'
                    for path, count in details["failure_paths"].items())
    cards = []
    for row in rows:
        raw = _mapping(row["raw"])
        record = raw.get("input", {})
        oracle_label = {"literal_sse2_expected_bits": "固定 SSE2 ビット列", "captured_sse2_expected_bits": "SSE2 命令の記録値",
                        "independent_integer_reference": "独立した整数演算"}.get(_label(raw.get("oracle")), "独立した期待値")
        search = " ".join(str(row[key]) for key in ("case_id", "input_id", "rounding", "contract_scope", "finding_family", "family_label"))
        search += " " + str(raw.get("test_id", ""))
        intrinsic = _mapping(row["intrinsic"])
        search += " " + str(intrinsic.get("name", "")) + " " + str(intrinsic.get("isa", ""))
        intrinsic_name = _label(intrinsic.get("name"), "Guide 対応未記録")
        cards.append(f'''<details class="finding-row" id="finding-record-{row['index']}" data-search="{_text(search)}"
          data-family="{_text(row['finding_family'])}" data-case="{_text(row['case_id'])}" data-rounding="{_text(row['rounding'])}" data-scope="{_text(row['contract_scope'])}">
          <summary>#{row['index']} {_text(intrinsic_name)} ／ {_text(row['case_id'])} — {_text(row['rounding'])} — {_text(row['input_id'])}</summary>
          {_intrinsic_markup(row['intrinsic'])}{_operation_markup(row['operation'])}
          <p>検証分類：{_text(row['family_label'])}。検証範囲：{_text(row['contract_scope'])}。</p>
          <p>{_text(family_explanation(row['finding_family']))}</p>{_lane_table(row['lanes'])}
          <p>入力</p><pre>{_text(_json(record))}</pre>
          <p>期待値（{_text(oracle_label)}）</p><pre>{_text(_json(raw.get('expected')))}</pre>
          <p>実測値（Python モデル）</p><pre>{_text(_json(raw.get('actual')))}</pre>
          <small>{_text(raw.get('test_id', '未記録'))} / {_text(raw.get('oracle_reference', '未記録'))}</small>
          <details><summary>保存された不一致レコード全体</summary><pre>{_text(_json(row['raw']))}</pre></details></details>''')
    warning = ""
    if details["counts_match"] is False:
        warning += f'<p class="finding-note">集計の不一致数 {_text(details["declared_mismatch_count"])} 件と、保存された詳細 {len(rows):,} 件が一致しません。</p>'
    if details["malformed_record_count"]:
        warning += f'<p class="finding-note">必須情報が欠けた詳細行が {details["malformed_record_count"]:,} 件あります。未記録として保持しています。</p>'
    case_labels = {row['case_id']: f"{_mapping(row['intrinsic']).get('name', '対応未記録')} — {row['case_id']}" for row in rows}
    filters = "".join(f'<label for="finding-{name}">{label}<select id="finding-{name}">{_options([row[key] for row in rows], case_labels if name == "case" else None)}</select></label>'
                      for name, label, key in (("family", "検証分類", "finding_family"), ("case", "ケース", "case_id"),
                                               ("rounding", "丸め指定", "rounding"), ("scope", "検証範囲", "contract_scope")))
    return f'''<section class="mismatch-details" id="model-mismatch-details"><style>{_STYLES}</style>
      <h3>何が、どの入力で食い違ったか</h3>
      <p class="finding-note"><strong>検証対象は各アーキテクチャの fixture が共有する Python モデルです。</strong>
      実行ホストの CPU 種別は {_text(_json(details['execution_environment']))} です。
      ホストの種類と検証対象を分けて読みます。ARM、OpenPOWER、RISC-V の実機同士の不一致を示す記録ではありません。</p>
      <p><strong>{len(rows):,} 不一致レコード</strong> ／ <strong>{details['unique_input_count']:,} 重複を除いた入力ID</strong>
      ／ <strong>{details['case_count']:,} ケース</strong>。入力ID未記録：{details['missing_input_id_count']:,} 件。
      Guide 対応：{details['intrinsic_case_count']:,} / {details['case_count']:,} ケース。
      同じ入力を複数のテスト分類で検証する場合があります。レコード数は固有入力数・原因数とは異なります。</p>{warning}
      <details><summary>ケース × 丸め指定 × 検証範囲の集計（{len(details['groups']):,} 組）</summary>
        <p>Guide の ISA 分類と、このリポジトリの caseID・required_isa は区別します。
        <code>sse2.*</code> の名前空間にも、Guide で SSE に分類される intrinsic が含まれます。</p>
        <div class="finding-table"><table><thead><tr><th>実際のケースID</th><th>Intel Intrinsics Guide 対応</th><th>丸め指定</th><th>検証範囲</th><th>不一致レコード</th><th>固有入力ID</th></tr></thead><tbody>{grouped}</tbody></table></div></details>
      <details><summary>確認する実装経路ごとの整理（{len(details['failure_paths'])} 分類）</summary>
      <p>検証分類と検証範囲をもとに整理しています。確定した根本原因の個数ではありません。
      符号付きゼロ・無限大の境界分類は、公式 nearest-even 範囲と丸め拡張範囲を分けています。</p>
      <table><thead><tr><th>確認する経路</th><th>不一致レコード</th></tr></thead><tbody>{paths}</tbody></table></details>
      <details><summary>検証分類ごとの読み方と代表例（{len(families)} 分類）</summary>
      <p>以下は検証時の分類です。似た結果をまとめたもので、根本原因の確定や原因数の集計ではありません。</p>{''.join(families)}</details>
      <h3>不一致の入力・期待値・実測値（{len(rows)}件）</h3>
      <p>各レーンのビット番号は最下位が 0。NaN の payload 表示は quiet bit を除きます。メモリ出力は変化したバイトを表示します。
      XOR は同じ幅の有効な値についてだけ算出します。変化ビット数は ULP 距離ではありません。</p>
      <div class="finding-filters"><label for="finding-search">ケース・intrinsic・入力ID・テスト名で検索<input type="search" id="finding-search" autocomplete="off"></label>{filters}
        <button id="finding-reset" type="button">絞り込みを解除</button></div>
      <div class="finding-pages"><button id="finding-previous" type="button">前の25件</button>
      <span id="finding-count" role="status" aria-live="polite">{len(rows):,} 件</span><button id="finding-next" type="button">次の25件</button></div>
      <p id="finding-empty" hidden>条件に一致する不一致はありません。</p>
      <noscript><p>JavaScript が無効のため、全件を表示しています。ブラウザ内検索も利用できます。</p></noscript>
      <div class="finding-list">{''.join(cards)}</div><script>{_SCRIPT}</script></section>'''
