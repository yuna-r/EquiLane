"""Readable arithmetic descriptions of stored mismatch inputs and outputs.

This is a presentation layer: values come from the saved record, never from
executing the portable model or recalculating the expected output.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


_ROUNDING = {
    "nearest_even": "最も近い表現へ丸め、ちょうど中間なら末尾が偶数の表現を選ぶ",
    "toward_negative": "下方向（−∞側）へ丸める。負数でも絶対値を小さくする方向とは限らない",
    "toward_positive": "上方向（＋∞側）へ丸める",
    "toward_zero": "ゼロ方向へ丸める（絶対値を増やさない）",
}
_OPERATORS = {"add": ("足し算", "＋"), "sub": ("引き算", "−"),
              "mul": ("掛け算", "×"), "div": ("割り算", "÷"),
              "min": ("小さい方を選ぶ", "min"), "max": ("大きい方を選ぶ", "max")}


def _object(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _lanes(value: Any) -> int:
    value = _object(value)
    lanes = value.get("lanes")
    return len(lanes) if isinstance(lanes, list) else 1 if "bits" in value else 0


def _number(value: Any, lane: int = 0) -> str:
    from .mismatch_details import decode_bits

    item = _object(value)
    raw = item.get("lanes")
    bits = raw[lane] if isinstance(raw, list) and lane < len(raw) else item.get("bits")
    decoded = decode_bits(bits, item.get("element"))
    if decoded["kind"] == "unknown":
        return str(bits) if bits is not None else "値未記録"
    if decoded["kind"] == "integer":
        return str(decoded["signed_value"])
    if decoded["kind"] in {"normal", "subnormal"}:
        fraction = decoded["fraction"]
        width = decoded["width"]
        fraction_width, bias = (23, 127) if width == 32 else (52, 1023)
        exponent = (decoded["exponent"] - bias if decoded["exponent"] else
                    1 - bias - fraction_width + fraction.bit_length() - 1)
        power = fraction == 0 if decoded["kind"] == "normal" else fraction.bit_count() == 1
        if power and (exponent < -9 or exponent > 20):
            sign = "−" if decoded["sign"] else ""
            return f"{sign}2^{exponent}（{decoded['label']}）"
    return decoded["label"]


def _return(finding: Mapping[str, Any], name: str) -> Mapping[str, Any]:
    value = _object(finding.get(name))
    return _object(value.get("return")) if "return" in value else value


def explain_operation(finding: Any) -> dict[str, Any]:
    """Describe arithmetic, sqrt and numeric conversions in a recorded case."""

    finding = _object(finding)
    record = _object(finding.get("input"))
    case_id = record.get("case_id")
    if not isinstance(case_id, str):
        return {}
    pieces = case_id.split(".")
    if len(pieces) < 4:
        return {}
    operation = pieces[1]
    if operation not in {*_OPERATORS, "sqrt", "cvt", "cvtt"}:
        return {}
    operands = _object(record.get("operands"))
    a, b = _object(operands.get("a")), _object(operands.get("b"))
    expected, actual = _return(finding, "expected"), _return(finding, "actual")
    result_count = max(_lanes(expected), _lanes(actual))
    scalar = pieces[-1] == "scalar"
    rounding = _object(record.get("environment")).get("rounding")
    rounding_text = _ROUNDING.get(rounding, "丸め指定は未記録") if isinstance(rounding, str) else "丸め指定は未記録"
    steps = ["丸め指定：" + rounding_text + "。"]
    if not operands:
        return {"title": "入力に対して行った操作", "description": "入力オペランドが未記録のため、計算式を特定できません。",
                "steps": steps}

    def outcome(lane):
        return f"保存した期待値：{_number(expected, lane)} ／ モデル実測値：{_number(actual, lane)}。"

    if operation in _OPERATORS:
        name, symbol = _OPERATORS[operation]
        if not a or not b:
            return {}
        count = 1 if scalar else min(_lanes(a), _lanes(b))
        for lane in range(count):
            left, right = _number(a, lane), _number(b, lane)
            expression = f"{symbol}({left}, {right})" if operation in {"min", "max"} else f"{left} {symbol} {right}"
            steps.append(f"レーン{lane}：{expression}。{outcome(lane)}")
        if scalar and _lanes(a) > 1:
            steps.append(f"計算するのはレーン0だけ。レーン1〜{_lanes(a) - 1}はaの元のビット列を保ちます。")
        description = "各レーンの2入力に対して" + name + "を行い、返す要素型で表現します。"
        if operation in {"min", "max"}:
            description = "2つの入力から値を選ぶ操作です。NaNや同値（＋0と−0を含む）の選択も観測します。"
        return {"title": name, "description": description, "steps": steps}

    if operation == "sqrt":
        if not a:
            return {}
        count = 1 if scalar else _lanes(a)
        for lane in range(count):
            steps.append(f"レーン{lane}：√({_number(a, lane)})、つまり2乗すると入力値になる数を求めます。{outcome(lane)}")
        if scalar and _lanes(a) > 1:
            steps.append(f"平方根を計算するのはレーン0だけ。レーン1〜{_lanes(a) - 1}はaのビット列を保ちます。")
        if case_id == "sse2.sqrt.f64x2.scalar":
            steps.append("このケースのC wrapperは_mm_sqrt_sd(a, a)を呼びます。Guideの2引数へ同じaを渡す範囲です。")
        return {"title": "平方根", "description": "入力の平方根を求め、戻り値の要素型に丸めます。", "steps": steps}

    source_name = "value" if "value" in operands else "b" if b else "a"
    source = _object(operands.get(source_name))
    if not source:
        return {}
    source_type, target_type = source.get("element"), expected.get("element", actual.get("element"))
    if not isinstance(source_type, str) or not isinstance(target_type, str):
        return {}
    count = min(_lanes(source), result_count)
    if scalar or "bits" in expected:
        count = min(count, 1)
    if operation == "cvtt":
        steps[0] = "このcvtt操作は小数部分をゼロ方向に切り捨てます。環境の丸め指定とは独立です。"
    for lane in range(count):
        steps.append(f"{source_name}のレーン{lane}：{_number(source, lane)}を{source_type}から{target_type}へ数値変換。{outcome(lane)}")
    if scalar and result_count > 1 and source_name != "a":
        steps.append(f"変換結果で戻り値のレーン0だけを置き換え、上位レーン1〜{result_count - 1}はaのビット列を保ちます。")
    elif result_count > count:
        steps.append(f"変換した{count}レーンより上の戻り値レーンは0で埋めます。")
    return {"title": "数値の型変換", "description": f"{source_type}の数値を{target_type}で表します。ビット列を読み替えるcastとは別の操作です。",
            "steps": steps}
