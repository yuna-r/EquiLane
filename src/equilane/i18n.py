"""Deterministic English presentation without changing canonical evidence.

The checked-in catalog covers application-owned Japanese text. Translation
operates only on presentation strings; result/input artifacts stay unchanged.
Strict rendering reports new untranslated phrases instead of hiding them.
"""
from __future__ import annotations

from functools import lru_cache
from html import escape
import json
from pathlib import Path
import re


JAPANESE = re.compile(r"[ぁ-ゖァ-ヺー一-龯々]+")
CATALOG_PATH = Path(__file__).parent / "data" / "translations.en.json"


class MissingTranslationError(ValueError):
    """Application-owned Japanese text is missing from the English catalog."""


@lru_cache(maxsize=2)
def _catalog(html=False):
    value = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    phrases = value["phrases"]
    if value.get("schema_version") != 1 or not isinstance(phrases, dict):
        raise ValueError("invalid English translation catalog")
    if any(not isinstance(source, str) or not source or not isinstance(target, str)
           or JAPANESE.search(target) for source, target in phrases.items()):
        raise ValueError("invalid English translation entry")
    if html:
        expanded = {key: escape(target, quote=True) for key, target in phrases.items()}
        expanded.update({escape(key, quote=True): escape(target, quote=True) for key, target in phrases.items()})
        phrases = expanded
    matcher = re.compile("|".join(re.escape(key) for key in sorted(phrases, key=lambda key: (-len(key), key))))
    return phrases, matcher


@lru_cache(maxsize=1)
def _patterns():
    value = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    return tuple((re.compile(item["source"]), item["target"]) for item in value.get("patterns", ()))


def _interpolated(text):
    for pattern, target in _patterns():
        text = pattern.sub(target, text)
    return text


def untranslated_phrases(text: str) -> tuple[str, ...]:
    return tuple(sorted(set(JAPANESE.findall(text))))


def translate_text(text: str, *, strict: bool = False) -> str:
    """Translate known application text while retaining interpolated values."""
    if not isinstance(text, str):
        raise TypeError("translation requires text")
    phrases, matcher = _catalog()
    result = matcher.sub(lambda match: phrases[match[0]], _interpolated(text))
    # These punctuation marks occur in Japanese presentation prose, never in
    # canonical hexadecimal values or machine-readable identifiers.
    result = result.translate(str.maketrans({"。": ". ", "、": ", ", "：": ": ",
                                            "（": "(", "）": ")", "「": "[", "」": "]", "〜": "–"}))
    if strict and untranslated_phrases(result):
        raise MissingTranslationError("Missing English translations: " + ", ".join(untranslated_phrases(result)[:20]))
    return result


def translate_html(html: str, *, language: str = "en", strict: bool = False) -> str:
    """Return Japanese unchanged or produce a self-contained English page."""
    if language not in {"ja", "en"}:
        raise ValueError("supported presentation languages are ja and en")
    if language == "ja":
        return html
    phrases, matcher = _catalog(html=True)
    result = matcher.sub(lambda match: phrases[match[0]], _interpolated(html))
    result = result.translate(str.maketrans({"。": ". ", "、": ", ", "：": ": ",
                                            "（": "(", "）": ")", "「": "[", "」": "]", "〜": "–"}))
    if strict and untranslated_phrases(result):
        raise MissingTranslationError("Missing English translations: " + ", ".join(untranslated_phrases(result)[:20]))
    result = re.sub(r'(<html\b[^>]*\blang\s*=\s*["\'])ja(["\'])', r'\1en\2', result, count=1, flags=re.IGNORECASE)
    return result.replace("ja-JP", "en-US")
