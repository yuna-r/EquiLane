"""Small factual Intel Intrinsics Guide index for the official case bindings.

The bundled data records names, prototypes and instruction metadata, plus short
original Japanese summaries. It contains no Guide description or operation
pseudocode. Looking up a binding never loads a case model or executes a probe.
"""

from __future__ import annotations

import copy
from functools import lru_cache
from importlib.resources import files
import re
from typing import Any

from .canonical import loads
from .errors import ValidationError


GUIDE_URL = "https://www.intel.com/content/www/us/en/docs/intrinsics-guide/index.html"
_NAME = re.compile(r"_mm_[a-zA-Z0-9_]+\Z")
_CASE = re.compile(r"[a-z][a-z0-9_.-]*\Z")


def _parse_catalog(text: str) -> dict[str, Any]:
    """Return an empty index for malformed or unsupported bundled data."""

    try:
        value = loads(text, source="Intel Intrinsics Guide catalog")
        if not isinstance(value, dict) or type(value.get("schema_version")) is not int or value["schema_version"] != 1:
            return {}
        entries = value.get("intrinsics")
        provenance = value.get("provenance")
        if not isinstance(entries, dict) or not isinstance(provenance, dict):
            return {}
        for case_id, entry in entries.items():
            if not _CASE.fullmatch(case_id) or not isinstance(entry, dict):
                return {}
            name = entry.get("name")
            if not isinstance(name, str) or not _NAME.fullmatch(name):
                return {}
            if entry.get("guide_url") != GUIDE_URL + "#text=" + name:
                return {}
            if entry.get("intel_symbol") != "intel" + name:
                return {}
            for key in ("isa", "header", "prototype", "summary_ja", "case_call"):
                if not isinstance(entry.get(key), str) or not entry[key]:
                    return {}
            for key in ("instructions", "framework_required_isa"):
                items = entry.get(key)
                if not isinstance(items, list) or any(not isinstance(item, str) or not item for item in items):
                    return {}
            if not entry["framework_required_isa"]:
                return {}
            if "binding_note" in entry and not isinstance(entry["binding_note"], str):
                return {}
        return value
    except (ValidationError, TypeError, ValueError, RecursionError):
        return {}


@lru_cache(maxsize=1)
def _catalog() -> dict[str, Any]:
    try:
        text = files("equilane").joinpath("data", "intel-intrinsics.json").read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return {}
    return _parse_catalog(text)


def intrinsic_for_case(case_id: object) -> dict[str, Any] | None:
    """Return official binding metadata; unknown cases have no inferred binding.

    ``isa`` is the Guide's CPUID label (for example, ``SSE`` or ``SSE2``).
    ``framework_required_isa`` separately records the case pack's requirements.
    The Guide prototype and the framework's ``case_call`` need not have the
    same arity; the reduced scalar-double square-root binding is explicit.
    """

    if not isinstance(case_id, str):
        return None
    entry = _catalog().get("intrinsics", {}).get(case_id)
    return copy.deepcopy(entry) if isinstance(entry, dict) else None


def catalog_provenance() -> dict[str, Any]:
    """Return the captured Guide version, source digests and binding digest."""

    return copy.deepcopy(_catalog().get("provenance", {}))


__all__ = ["GUIDE_URL", "catalog_provenance", "intrinsic_for_case"]
