"""Versioned ISA vocabulary and used-contract projection."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
from types import MappingProxyType
from typing import Any, Iterable, Mapping, Protocol

from .canonical import (
    JSONValue,
    dump_bytes,
    load_file,
    require_exact_keys,
    require_int,
    require_object,
    require_sorted_unique_strings,
    require_string,
    sha256_bytes,
    utf16_sort_key,
)
from .errors import ValidationError
from .targets import TARGETS


TOKEN_PATTERN = re.compile(r"^[a-z][a-z0-9_.-]*$")
ARCHITECTURES = {target.architecture for target in TARGETS.values()}


class CaseLike(Protocol):
    id: str

    def required_isa(self, role: str) -> tuple[str, ...]: ...


@dataclass(frozen=True)
class ISARegistry:
    data: dict[str, JSONValue]
    tokens: Mapping[str, dict[str, JSONValue]]
    sha256: str

    def get(self, token: str) -> dict[str, JSONValue]:
        try:
            return self.tokens[token]
        except KeyError as exc:
            raise ValidationError(f"unknown ISA token: {token}") from exc

    def closure(self, direct: Iterable[str], *, architecture: str) -> tuple[str, ...]:
        pending = list(direct)
        found: set[str] = set()
        while pending:
            token = pending.pop()
            if token in found:
                continue
            entry = self.get(token)
            if entry["architecture"] != architecture:
                raise ValidationError(
                    f"ISA token {token!r} belongs to {entry['architecture']}, not {architecture}"
                )
            found.add(token)
            implies = entry["implies"]
            assert isinstance(implies, list)
            pending.extend(str(item) for item in implies)
        return tuple(sorted(found, key=utf16_sort_key))


@dataclass(frozen=True)
class UsedISAContract:
    data: dict[str, JSONValue]
    sha256: str


def _validate_token_entry(value: JSONValue, location: str) -> dict[str, JSONValue]:
    entry = require_object(value, location)
    require_exact_keys(
        entry, {"architecture", "detector", "implies", "token"}, location=location
    )
    token = require_string(entry["token"], f"{location}.token")
    if not TOKEN_PATTERN.fullmatch(token):
        raise ValidationError(f"{location}.token: invalid ISA token")
    architecture = require_string(entry["architecture"], f"{location}.architecture")
    if architecture not in ARCHITECTURES:
        raise ValidationError(f"{location}.architecture: unsupported architecture")
    detector = require_object(entry["detector"], f"{location}.detector")
    require_exact_keys(detector, {"id", "version"}, location=f"{location}.detector")
    detector_id = require_string(detector["id"], f"{location}.detector.id")
    if not TOKEN_PATTERN.fullmatch(detector_id):
        raise ValidationError(f"{location}.detector.id: invalid detector identifier")
    require_int(detector["version"], f"{location}.detector.version", minimum=1)
    require_sorted_unique_strings(entry["implies"], f"{location}.implies")
    return entry


def validate_isa_registry(value: JSONValue, *, source: str = "ISA registry") -> ISARegistry:
    registry = require_object(value, source)
    require_exact_keys(registry, {"schema_version", "tokens"}, location=source)
    if require_int(registry["schema_version"], f"{source}.schema_version") != 1:
        raise ValidationError(f"{source}.schema_version: only version 1 is supported")
    raw_tokens = registry["tokens"]
    if not isinstance(raw_tokens, list) or not raw_tokens:
        raise ValidationError(f"{source}.tokens: expected a non-empty array")
    entries = [
        _validate_token_entry(item, f"{source}.tokens[{index}]")
        for index, item in enumerate(raw_tokens)
    ]
    names = [str(entry["token"]) for entry in entries]
    if names != sorted(set(names), key=utf16_sort_key):
        raise ValidationError(f"{source}.tokens: expected token-sorted unique entries")
    by_name = {str(entry["token"]): entry for entry in entries}
    for name, entry in by_name.items():
        implies = entry["implies"]
        assert isinstance(implies, list)
        for implied in implies:
            if implied not in by_name:
                raise ValidationError(f"ISA token {name!r} implies unknown token {implied!r}")
            if by_name[str(implied)]["architecture"] != entry["architecture"]:
                raise ValidationError(f"ISA token {name!r} implies a different architecture")

    visiting: set[str] = set()
    visited: set[str] = set()

    for name in names:
        # An explicit DFS stack accepts long valid implication chains without
        # making the registry size depend on Python's recursion limit.
        pending = [(name, False)]
        while pending:
            token, exiting = pending.pop()
            if exiting:
                visiting.remove(token)
                visited.add(token)
                continue
            if token in visited:
                continue
            if token in visiting:
                raise ValidationError(f"ISA implies cycle contains {token!r}")
            visiting.add(token)
            pending.append((token, True))
            implied = by_name[token]["implies"]
            assert isinstance(implied, list)
            pending.extend((str(child), False) for child in reversed(implied))
    digest = sha256_bytes(dump_bytes(registry, newline=True))
    return ISARegistry(registry, MappingProxyType(by_name), digest)


def load_isa_registry(path: str | Path) -> ISARegistry:
    return validate_isa_registry(load_file(path), source=str(path))


def project_used_isa(registry: ISARegistry, cases: Iterable[CaseLike]) -> UsedISAContract:
    case_entries: list[JSONValue] = []
    used: set[str] = set()
    ordered_cases = sorted(cases, key=lambda case: utf16_sort_key(case.id))
    for case in ordered_cases:
        entry: dict[str, JSONValue] = {"id": case.id}
        # The legacy CaseLike protocol only supplied the two required roles.
        # New CaseDefinition objects additionally enumerate optional bindings.
        for role in getattr(case, "target_roles", ("intel", "openpower")):
            if role not in TARGETS:
                raise ValidationError(f"unknown runner role: {role}")
            required = case.required_isa(role)
            used.update(registry.closure(required, architecture=TARGETS[role].architecture))
            entry[role] = list(required)
        case_entries.append(entry)
    token_entries: list[JSONValue] = [registry.get(name) for name in sorted(used, key=utf16_sort_key)]
    data: dict[str, JSONValue] = {
        "cases": case_entries,
        "schema_version": 1,
        "tokens": token_entries,
    }
    return UsedISAContract(data, sha256_bytes(dump_bytes(data, newline=True)))


def validate_used_isa_contract(
    value: JSONValue,
    *,
    registry: ISARegistry,
    cases: Iterable[CaseLike],
    source: str = "used ISA contract",
) -> UsedISAContract:
    supplied = require_object(value, source)
    require_exact_keys(supplied, {"cases", "schema_version", "tokens"}, location=source)
    expected = project_used_isa(registry, cases)
    # Python equality considers True == 1. Canonical bytes preserve JSON types,
    # so an altered version/detector cannot authenticate as the integer contract.
    if dump_bytes(supplied) != dump_bytes(expected.data):
        raise ValidationError(f"{source}: does not match the local used ISA projection")
    return UsedISAContract(supplied, sha256_bytes(dump_bytes(supplied, newline=True)))
