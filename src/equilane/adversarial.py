"""Deterministic, contract-driven inputs for native port conformance checks.

This module constructs input bits only.  It neither imports the portable
execution models nor calculates an expected result: native Intel observations
remain the baseline for a port comparison.  Scenario names are exposed to
coverage tools, while canonical input records retain their existing schema.
"""

from __future__ import annotations

import copy
from functools import lru_cache
import hashlib
from itertools import product
import struct
from typing import Iterable, Iterator

from .canonical import JSONValue
from .cases import CaseDefinition
from .development import SplitMix64, rounding_modes, scalar, vector


Candidate = dict[str, JSONValue]


def _unique(values: list[int]) -> tuple[int, ...]:
    return tuple(dict.fromkeys(values))


@lru_cache(maxsize=None)
def boundary_bits(element: str) -> tuple[int, ...]:
    """Bit patterns including signed extrema and representability boundaries."""

    width = int(element[1:])
    mask = (1 << width) - 1
    sign = 1 << (width - 1)
    if not element.startswith("f"):
        values = [0, 1, mask, sign, sign - 1, sign + 1, 2, mask - 1,
                  sign - 2, sign + 2, 0xAA_AA_AA_AA_AA_AA_AA_AA & mask,
                  0x55_55_55_55_55_55_55_55 & mask]
        # Integer-to-float ties and narrowing/saturation thresholds.
        for bit in (7, 8, 15, 16, 23, 24, 31, 32, 52, 53, 63):
            if bit < width:
                for delta in (-1, 0, 1):
                    value = (1 << bit) + delta
                    values.extend((value & mask, (-value) & mask))
        return _unique(values)

    fraction = 23 if width == 32 else 52
    exponent = 8 if width == 32 else 11
    infinity = ((1 << exponent) - 1) << fraction
    quiet = 1 << (fraction - 1)
    positive = [0, 1, 2, (1 << fraction) - 2, (1 << fraction) - 1,
                1 << fraction, (1 << fraction) + 1,
                infinity - 2, infinity - 1, infinity,
                infinity | quiet, infinity | quiet | 1,
                infinity | quiet | 0x42, infinity | ((1 << fraction) - 1),
                infinity | 1, infinity | 0x42]
    code = ">f" if width == 32 else ">d"
    # Predecessor/successor bits distinguish ties, half-integers, integer
    # conversion limits and binary64-to-binary32 precision loss.
    for value in (0.5, 1.0, 1.5, 2.0, 2.5, 3.5, 127.5, 255.5,
                  32767.5, 65535.5, float(2**23), float(2**24),
                  float(2**31), float(2**32), float(2**52),
                  float(2**53), float(2**63), float(2**64),
                  2.0**-24, 2.0**-53):
        bits = int.from_bytes(struct.pack(code, value), "big")
        positive.extend((bits - 1, bits, bits + 1))
    return _unique([bits | polarity for bits in positive for polarity in (0, sign)])


@lru_cache(maxsize=None)
def _comparison_bits(element: str) -> tuple[int, ...]:
    width = int(element[1:])
    sign = 1 << (width - 1)
    if not element.startswith("f"):
        return boundary_bits(element)[:12]
    fraction = 23 if width == 32 else 52
    infinity = ((1 << (8 if width == 32 else 11)) - 1) << fraction
    one = 0x3F800000 if width == 32 else 0x3FF0000000000000
    quiet = 1 << (fraction - 1)
    return tuple(value | polarity for value in (
        0, 1, (1 << fraction) - 1, 1 << fraction,
        one - 1, one, infinity - 1, infinity,
        infinity | quiet | 1, infinity | quiet | 0x42,
        infinity | 1, infinity | 0x42,
    ) for polarity in (0, sign))


def _arguments(case: CaseDefinition) -> list[dict[str, JSONValue]]:
    arguments = case.signature["arguments"]
    assert isinstance(arguments, list)
    return [argument for argument in arguments if isinstance(argument, dict)]


def adversarial_case_count(case: CaseDefinition, requested: int) -> int:
    """Cap a finite input domain instead of waiting forever for unique bits."""

    capacity = len(rounding_modes(case))
    definitions = case.data.get("immediates", {})
    assert isinstance(definitions, dict)
    for argument in _arguments(case):
        if argument["type"] == "pointer":
            return requested  # Buffers and offsets make the domain unbounded.
        if argument["type"] == "immediate":
            definition = definitions[str(argument["name"])]
            assert isinstance(definition, dict)
            capacity *= len(definition["values"])
        else:
            width = int(str(argument["element"])[1:]) * _lanes(argument)
            if width >= requested.bit_length():
                return requested
            capacity *= 1 << width
        if capacity >= requested:
            return requested
    return capacity


def _typed(argument: dict[str, JSONValue], values: list[int]) -> dict[str, JSONValue]:
    element = str(argument["element"])
    if argument["type"] == "scalar":
        return scalar(element, values[0])
    return vector(element, tuple(values))


def _lanes(argument: dict[str, JSONValue]) -> int:
    return int(argument.get("lanes", 1))


def _memory(
    case: CaseDefinition, index: int, *, offset: int | None = None,
    exact_fit: bool = False, complement: bool = False,
) -> tuple[dict[str, JSONValue], dict[str, JSONValue]]:
    pointers: dict[str, JSONValue] = {}
    buffers: dict[str, JSONValue] = {}
    memory = case.data.get("memory_contract", {})
    assert isinstance(memory, dict)
    result = case.signature["return"]
    assert isinstance(result, dict)
    typed = [a for a in _arguments(case) if a["type"] not in {"immediate", "pointer"}]
    element = str(result.get("element", typed[0]["element"] if typed else "u8"))
    width = int(element[1:]) // 8
    pool = boundary_bits(element)
    for number, (name, raw_contract) in enumerate(memory.items()):
        assert isinstance(raw_contract, dict)
        alignment = int(raw_contract["required_alignment"])
        effective_offset = (index % 33) * alignment if offset is None else offset
        ranges = list(raw_contract["read_ranges"]) + list(raw_contract["write_ranges"])
        extent = max((int(r["offset"]) + int(r["byte_length"]) for r in ranges), default=0)
        suffix = 0 if exact_fit else 17
        length = effective_offset + extent + suffix
        raw = bytearray(((position * 73 + 0xA5 + number * 29) & 0xFF)
                        ^ (0xFF if complement else 0) for position in range(length))
        payload = b"".join(pool[(index + lane) % len(pool)].to_bytes(width, "little")
                           for lane in range((extent + width - 1) // width))[:extent]
        raw[effective_offset:effective_offset + extent] = payload
        buffer_id = f"memory_{name}"
        pointers[name] = {"buffer": buffer_id, "offset": effective_offset}
        buffers[buffer_id] = {"alignment": max(64, alignment), "bytes": "0x" + raw.hex()}
    return pointers, buffers


def _base(case: CaseDefinition, index: int, *, uniform: bool = False) -> Candidate:
    operands: dict[str, JSONValue] = {}
    immediates: dict[str, JSONValue] = {}
    definitions = case.data.get("immediates", {})
    assert isinstance(definitions, dict)
    for number, argument in enumerate(_arguments(case)):
        name = str(argument["name"])
        if argument["type"] == "pointer":
            continue
        if argument["type"] == "immediate":
            definition = definitions[name]
            assert isinstance(definition, dict)
            allowed = definition["values"]
            assert isinstance(allowed, list)
            immediates[name] = allowed[index % len(allowed)]
            continue
        pool = boundary_bits(str(argument["element"]))
        values = [pool[(index if uniform else index + number * 7 + lane * 3) % len(pool)]
                  for lane in range(_lanes(argument))]
        operands[name] = _typed(argument, values)
    pointers, buffers = _memory(case, index)
    operands.update(pointers)
    result: Candidate = {
        "environment": {"fp_mode": "ieee", "rounding": rounding_modes(case)[0]},
        "generation": {"class": "structured"}, "operands": operands,
    }
    if immediates:
        result["immediates"] = immediates
    if buffers:
        result["buffers"] = buffers
    return result


def _numeric_scenarios(case: CaseDefinition) -> Iterator[tuple[str, Candidate]]:
    typed = [a for a in _arguments(case) if a["type"] not in {"immediate", "pointer"}]
    if not typed:
        return
    maximum = max(len(boundary_bits(str(a["element"]))) for a in typed)
    for index in range(maximum):
        yield "numeric-boundaries", _base(case, index, uniform=True)
        yield "distinct-lanes", _base(case, index)


def _bit_scenarios(case: CaseDefinition) -> Iterator[tuple[str, Candidate]]:
    typed = [a for a in _arguments(case) if a["type"] not in {"immediate", "pointer"}]
    if not typed:
        return
    maximum = max(int(str(a["element"])[1:]) * _lanes(a) for a in typed)
    for index in range(maximum):
        for invert in (False, True):
            item = _base(case, index)
            operands = item["operands"]
            assert isinstance(operands, dict)
            for number, argument in enumerate(typed):
                width = int(str(argument["element"])[1:])
                count = _lanes(argument)
                mask = (1 << width) - 1
                position = (index + number * 17) % (width * count)
                values = [mask if invert else 0] * count
                values[position // width] ^= 1 << (position % width)
                operands[str(argument["name"])] = _typed(argument, values)
            yield "walking-zero" if invert else "walking-one", item


def _pair_scenarios(case: CaseDefinition) -> Iterator[tuple[str, Candidate]]:
    typed = [a for a in _arguments(case) if a["type"] not in {"immediate", "pointer"}]
    if len(typed) != 2 or typed[0]["element"] != typed[1]["element"]:
        return
    first, second = typed
    count = min(_lanes(first), _lanes(second))
    pool = _comparison_bits(str(first["element"]))
    pairs = list(product(pool, repeat=2))
    for start in range(0, len(pairs), count):
        item = _base(case, start)
        operands = item["operands"]
        assert isinstance(operands, dict)
        for side, argument in enumerate((first, second)):
            values = [pairs[(start + lane) % len(pairs)][side]
                      for lane in range(_lanes(argument))]
            operands[str(argument["name"])] = _typed(argument, values)
        yield "ordered-boundary-pairs", item


def _immediate_scenarios(case: CaseDefinition) -> Iterator[tuple[str, Candidate]]:
    definitions = case.data.get("immediates", {})
    assert isinstance(definitions, dict)
    if not definitions:
        return
    names = list(definitions)
    allowed = [list(definitions[name]["values"]) for name in names]
    for values in product(*allowed):
        for index in range(12):
            item = _base(case, index)
            item["immediates"] = dict(zip(names, values, strict=True))
            yield "allowed-immediates", item


def _shift_scenarios(case: CaseDefinition) -> Iterator[tuple[str, Candidate]]:
    typed = {str(a["name"]): a for a in _arguments(case)}
    if ".vector-count" not in case.id or "count" not in typed or "a" not in typed:
        return
    width = int(str(typed["a"]["element"])[1:])
    counts = _unique([0, 1, width - 1, width, width + 1, width * 2 - 1,
                      width * 2, 63, 64, 65, 127, 128, 255, 256,
                      1 << 32, 1 << 63, (1 << 64) - 1])
    for index, count in enumerate(counts):
        for high in (0, 1, (1 << 64) - 1):
            item = _base(case, index)
            operands = item["operands"]
            assert isinstance(operands, dict)
            operands["count"] = vector("u64", (count, high))
            yield "vector-shift-counts", item


def _memory_scenarios(case: CaseDefinition) -> Iterator[tuple[str, Candidate]]:
    memory = case.data.get("memory_contract", {})
    assert isinstance(memory, dict)
    if not memory:
        return
    # LCM equals the maximum because required alignments are powers of two.
    alignment = max(int(contract["required_alignment"]) for contract in memory.values())
    offsets = _unique([*range(0, 33, alignment), 63 - 63 % alignment,
                       64 + (-64 % alignment), 65 + (-65 % alignment)])
    for index, offset in enumerate(offsets):
        for exact_fit, complement in ((False, False), (False, True), (True, False)):
            item = _base(case, index)
            pointers, buffers = _memory(case, index, offset=offset,
                                        exact_fit=exact_fit, complement=complement)
            operands = item["operands"]
            assert isinstance(operands, dict)
            operands.update(pointers)
            item["buffers"] = buffers
            yield "memory-exact-fit" if exact_fit else "memory-guards", item


def structured_scenarios(case: CaseDefinition) -> Iterator[tuple[str, Candidate]]:
    """Round-robin independent dimensions so a count cap covers every axis."""

    if adversarial_case_count(case, 257) <= 256:
        arguments = _arguments(case)
        definitions = case.data.get("immediates", {})
        assert isinstance(definitions, dict)
        domains = [
            list(definitions[str(argument["name"])] ["values"])
            if argument["type"] == "immediate"
            else range(1 << (int(str(argument["element"])[1:]) * _lanes(argument)))
            for argument in arguments
        ]
        for index, combination in enumerate(product(*domains)):
            item = _base(case, index)
            operands = item["operands"]
            assert isinstance(operands, dict)
            for argument, value in zip(arguments, combination, strict=True):
                name = str(argument["name"])
                if argument["type"] == "immediate":
                    immediates = item["immediates"]
                    assert isinstance(immediates, dict)
                    immediates[name] = value
                else:
                    width = int(str(argument["element"])[1:])
                    mask = (1 << width) - 1
                    operands[name] = _typed(argument, [(value >> (width * lane)) & mask
                                                      for lane in range(_lanes(argument))])
            yield "exhaustive-small-input-space", item
        return
    streams = [iter(factory(case)) for factory in (
        _numeric_scenarios, _pair_scenarios, _bit_scenarios,
        _immediate_scenarios, _shift_scenarios, _memory_scenarios,
    )]
    while streams:
        remaining = []
        for stream in streams:
            try:
                yield next(stream)
                remaining.append(stream)
            except StopIteration:
                pass
        streams = remaining


def adversarial_candidates(case: CaseDefinition, *, seed_text: str) -> Iterator[Candidate]:
    """Structured bit patterns, followed by independently seeded raw-bit fuzz."""

    modes = rounding_modes(case)
    for _, candidate in structured_scenarios(case):
        for mode in modes:
            item = copy.deepcopy(candidate)
            environment = item["environment"]
            assert isinstance(environment, dict)
            environment["rounding"] = mode
            yield item
    salt = int.from_bytes(hashlib.sha256(case.id.encode()).digest()[:8], "big")
    random = SplitMix64(int(seed_text, 16) ^ salt)
    index = 0
    while True:
        item = _base(case, index)
        item["generation"] = {"class": "random", "algorithm": "splitmix64",
                              "seed": seed_text}
        item["environment"] = {"fp_mode": "ieee", "rounding": modes[random.next() % len(modes)]}
        operands = item["operands"]
        assert isinstance(operands, dict)
        for argument in _arguments(case):
            if argument["type"] in {"pointer", "immediate"}:
                continue
            operands[str(argument["name"])] = _typed(
                argument, [random.next() for _ in range(_lanes(argument))])
        buffers = item.get("buffers", {})
        assert isinstance(buffers, dict)
        for allocation in buffers.values():
            assert isinstance(allocation, dict)
            length = (len(str(allocation["bytes"])) - 2) // 2
            raw = b"".join(random.next().to_bytes(8, "little") for _ in range((length + 7) // 8))
            allocation["bytes"] = "0x" + raw[:length].hex()
        yield item
        index += 1


def _scenario_ids(case: CaseDefinition) -> dict[str, set[str]]:
    from .records import derive_input_id

    result: dict[str, set[str]] = {}
    for group, candidate in structured_scenarios(case):
        for mode in rounding_modes(case):
            identity = dict(candidate)
            identity["case_id"] = case.id
            identity["environment"] = {"fp_mode": "ieee", "rounding": mode}
            result.setdefault(derive_input_id(identity), set()).add(group)
    return result


def build_adversarial_coverage(
    cases: Iterable[CaseDefinition], records: Iterable[Candidate], *,
    vectors_sha256: str,
) -> dict[str, JSONValue]:
    """Measure the written inputs, without claiming native execution coverage.

    Records must be grouped by case as in canonical generator output.  Only one
    case's ID sets are retained at a time, so a million-input artifact can still
    be checked without loading the JSONL into memory.  Groups may overlap: a
    zero input can independently satisfy a pair and a boundary scenario.
    """

    registry = {case.id: case for case in cases}
    summaries: list[JSONValue] = []
    current: CaseDefinition | None = None
    planned: dict[str, set[str]] = {}
    observed: set[str] = set()
    classes: dict[str, int] = {}
    modes: set[str] = set()
    bits: dict[str, set[str]] = {}
    immediates: dict[str, set[int]] = {}
    memory: dict[str, dict] = {}
    count = 0

    def finish() -> None:
        if current is None:
            return
        group_ids: dict[str, set[str]] = {}
        for input_id, groups in planned.items():
            for group in groups:
                group_ids.setdefault(group, set()).add(input_id)
        groups: dict[str, JSONValue] = {}
        for group, expected_ids in sorted(group_ids.items()):
            seen = len(expected_ids & observed)
            groups[group] = {"available_unique_inputs": len(expected_ids),
                             "observed_unique_inputs": seen,
                             "complete": seen == len(expected_ids)}
        boundary: dict[str, JSONValue] = {}
        for argument in _arguments(current):
            name = str(argument["name"])
            if argument["type"] in {"pointer", "immediate"}:
                continue
            element = str(argument["element"])
            digits = int(element[1:]) // 4
            expected = {f"0x{value:0{digits}x}" for value in boundary_bits(element)}
            seen_bits = expected & bits.get(name, set())
            boundary[name] = {"element": element, "available_values": len(expected),
                              "observed_values": len(seen_bits),
                              "missing_bits": sorted(expected - seen_bits)}
        immediate_summary: dict[str, JSONValue] = {}
        definitions = current.data.get("immediates", {})
        assert isinstance(definitions, dict)
        for name, definition in definitions.items():
            assert isinstance(definition, dict)
            allowed = list(definition["values"])
            seen_values = sorted(immediates.get(name, set()))
            immediate_summary[name] = {"allowed": allowed, "observed": seen_values,
                                       "complete": set(allowed) == set(seen_values)}
        memory_summary: dict[str, JSONValue] = {}
        for name, values in sorted(memory.items()):
            memory_summary[name] = {
                **{key: value for key, value in values.items() if not isinstance(value, set)},
                "offsets": sorted(values["offsets"]),
                "offset_residues_mod_16": sorted(values["residues"]),
            }
        structured_observed = len(set(planned) & observed)
        summaries.append({
            "case_id": current.id, "record_count": count,
            "unique_input_count": len(observed), "generation_classes": dict(sorted(classes.items())),
            "rounding_modes": sorted(modes), "scenario_groups": groups,
            "structured_available_unique_inputs": len(planned),
            "structured_observed_unique_inputs": structured_observed,
            "structured_complete": structured_observed == len(planned),
            "numeric_boundaries": boundary, "immediates": immediate_summary,
            "memory": memory_summary,
        })

    finished: set[str] = set()
    total = 0
    for record in records:
        case_id = str(record["case_id"])
        if current is None or current.id != case_id:
            finish()
            if case_id in finished:
                raise ValueError("adversarial coverage requires records grouped by case")
            finished.add(case_id)
            current = registry[case_id]
            planned = _scenario_ids(current)
            observed, classes, modes, bits, immediates, memory = set(), {}, set(), {}, {}, {}
            count = 0
        count += 1
        total += 1
        observed.add(str(record["input_id"]))
        generation = record["generation"]
        environment = record["environment"]
        assert isinstance(generation, dict) and isinstance(environment, dict)
        generation_class = str(generation["class"])
        classes[generation_class] = classes.get(generation_class, 0) + 1
        modes.add(str(environment["rounding"]))
        operands = record["operands"]
        assert isinstance(operands, dict)
        for name, operand in operands.items():
            assert isinstance(operand, dict)
            if "bits" in operand:
                bits.setdefault(name, set()).add(str(operand["bits"]))
            elif "lanes" in operand:
                bits.setdefault(name, set()).update(str(value) for value in operand["lanes"])
        for name, value in record.get("immediates", {}).items():
            immediates.setdefault(name, set()).add(int(value))
        contracts = current.data.get("memory_contract", {})
        assert isinstance(contracts, dict)
        for name, contract in contracts.items():
            assert isinstance(contract, dict)
            pointer = operands[name]
            assert isinstance(pointer, dict)
            offset = int(pointer["offset"])
            buffers = record["buffers"]
            assert isinstance(buffers, dict)
            allocation = buffers[str(pointer["buffer"])]
            assert isinstance(allocation, dict)
            length = (len(str(allocation["bytes"])) - 2) // 2
            ranges = list(contract["read_ranges"]) + list(contract["write_ranges"])
            end = offset + max((int(r["offset"]) + int(r["byte_length"]) for r in ranges), default=0)
            values = memory.setdefault(name, {"offsets": set(), "residues": set(),
                                              "prefix_guard_inputs": 0,
                                              "suffix_guard_inputs": 0,
                                              "exact_fit_inputs": 0})
            values["offsets"].add(offset)
            values["residues"].add(offset % 16)
            values["prefix_guard_inputs"] += int(offset > 0)
            values["suffix_guard_inputs"] += int(length > end)
            values["exact_fit_inputs"] += int(length == end)
    finish()
    return {
        "schema_version": 1, "artifact_type": "equilane.adversarial-coverage",
        "scope": "generated-inputs-only", "test_vectors_sha256": vectors_sha256,
        "record_count": total, "case_count": len(summaries),
        "scenario_groups_may_overlap": True,
        "all_structured_scenarios_covered": all(bool(item["structured_complete"]) for item in summaries),
        "cases": summaries,
    }
