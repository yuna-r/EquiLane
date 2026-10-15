"""Boundary contracts for untrusted configuration and generated input data.

These tests keep valid neighbours beside malformed values, and verify atomic
publication and determinism as well as rejection. Every matrix row is exposed
as an independently discoverable unittest test.
"""

from __future__ import annotations

import copy
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import yaml

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "src"))

from equilane.canonical import (
    MAX_SAFE_INTEGER, atomic_write, dump_bytes, dumps, iter_canonical_jsonl_bytes,
    require_exact_keys, require_object, sha256_bytes, write_jsonl,
)
from equilane.cases import load_case_definitions, resolve_case_registry, validate_case_definition
from equilane.errors import RunnerError, ValidationError
from equilane.generator import generate_artifact
from equilane.isa import (
    load_isa_registry, project_used_isa, validate_isa_registry, validate_used_isa_contract,
)
from equilane.project import load_project
from equilane.records import derive_input_id, validate_input_record, validate_result_record
from equilane.yamlio import load_yaml_file, load_yaml_text


def _install(cls, name, action):
    def test(self):
        action(self)
    test.__name__ = "test_" + name
    setattr(cls, test.__name__, test)


def _put(value, path, replacement):
    for key in path[:-1]:
        value = value[key]
    value[path[-1]] = copy.deepcopy(replacement)


def _case_data():
    return {
        "schema_version": 1, "id": "sse2.boundary.memory.default",
        "description": "Memory boundary protocol fixture", "tags": [],
        "intel": {"required_isa": ["sse2"], "symbol": "intel_boundary"},
        "openpower": {"required_isa": ["power8", "vsx"], "symbol": "power_boundary"},
        "signature": {
            "arguments": [{"name": "source", "type": "pointer"},
                          {"name": "control", "type": "immediate", "element": "i8"}],
            "return": {"type": "vector", "element": "u8", "lanes": 2},
        },
        "immediates": {"control": {"compile_time": True, "values": [-128, 0, 127]}},
        "memory_contract": {"source": {
            "access": "read_write", "read_ranges": [{"offset": 0, "byte_length": 2}],
            "required_alignment": 2, "write_ranges": [{"offset": 1, "byte_length": 1}],
        }},
        "comparison": {"mode": "bit_exact"}, "input_domain": {"exclude": []},
        "environment": {"fp_rounding_modes": ["nearest_even"], "observe_fp_exceptions": False},
    }


def _input(case):
    record = {
        "schema_version": 1, "case_id": case.id, "sequence": 1,
        "environment": {"fp_mode": "ieee", "rounding": "nearest_even"},
        "generation": {"class": "boundary"},
        "operands": {"source": {"buffer": "allocation", "offset": 2}},
        "immediates": {"control": -128},
        "buffers": {"allocation": {"alignment": 2, "bytes": "0x00112233"}},
    }
    record["input_id"] = derive_input_id(record)
    return record


class CanonicalBoundaryTests(unittest.TestCase):
    def test_exact_key_validation_rejects_non_string_keys_without_type_error(self):
        with self.assertRaises(ValidationError):
            require_exact_keys({"ok": 1, 5: 2, None: 3}, {"ok"}, location="value")

    def test_object_validation_rejects_non_string_and_surrogate_keys(self):
        for value in ({1: "number key"}, {None: "null key"}, {"\ud800": "surrogate key"}):
            with self.subTest(value=value), self.assertRaises(ValidationError):
                require_object(value, "fixture")

    def test_nested_canonical_order_and_unicode_round_trip(self):
        value = {"z": [{"\ue000": -MAX_SAFE_INTEGER, "\U00010000": MAX_SAFE_INTEGER}], "a": "é\n\t\x00"}
        encoded = dump_bytes(value, newline=True)
        self.assertEqual(list(iter_canonical_jsonl_bytes(encoded)), [value])
        self.assertEqual(encoded, dump_bytes(dict(reversed(list(value.items()))), newline=True))
        self.assertIn(b'"\xf0\x90\x80\x80":9007199254740991,"\xee\x80\x80":-9007199254740991', encoded)

    def test_write_jsonl_returns_the_hash_and_length_of_exact_published_bytes(self):
        rows = [{"sequence": 1, "text": "日本語"}, {"sequence": 2, "value": None}]
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "nested" / "data.jsonl"
            visited = []
            count, length, digest = write_jsonl(path, iter(rows), validator=lambda row, number: visited.append((row, number)))
            self.assertEqual(visited, [(rows[0], 1), (rows[1], 2)])
            self.assertEqual((count, length, digest), (2, len(path.read_bytes()), sha256_bytes(path.read_bytes())))
            self.assertEqual(list(iter_canonical_jsonl_bytes(path.read_bytes())), rows)

    def test_write_empty_jsonl_publishes_empty_bytes_with_empty_digest(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "empty.jsonl"
            self.assertEqual(write_jsonl(path, []), (0, 0, sha256_bytes(b"")))
            self.assertEqual(path.read_bytes(), b"")

    def test_fsync_failure_closes_the_output_stream(self):
        streams = []
        original_fdopen = os.fdopen

        def tracked_fdopen(*args, **kwargs):
            stream = original_fdopen(*args, **kwargs)
            streams.append(stream)
            return stream

        with tempfile.TemporaryDirectory() as temporary:
            with patch("equilane.canonical.os.fdopen", side_effect=tracked_fdopen), \
                 patch("equilane.canonical.os.fsync", side_effect=OSError("disk failure")):
                with self.assertRaises(RunnerError):
                    atomic_write(Path(temporary) / "output.json", b"new output")
            self.assertEqual(len(streams), 1)
            self.assertTrue(streams[0].closed)

    def check_publication_failure(self, mode):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / "records.jsonl"
            path.write_bytes(b"existing complete file\n")
            if mode == "iterator":
                def rows():
                    yield {"ok": 1}
                    raise ValueError("producer failed")
                action = lambda: write_jsonl(path, rows())
                expected = ValueError
            elif mode == "validator":
                def validator(row, number):
                    if number == 2:
                        raise ValidationError("second record failed")
                action = lambda: write_jsonl(path, [{"ok": 1}, {"ok": 2}], validator=validator)
                expected = ValidationError
            elif mode == "serialization":
                action = lambda: write_jsonl(path, [{"ok": 1}, {"bad": 0.5}])
                expected = ValidationError
            else:
                action = lambda: atomic_write(path, b"replacement\n")
                expected = RunnerError
            if mode in {"fsync", "replace", "fdopen"}:
                with patch("equilane.canonical.os." + mode, side_effect=OSError("disk failure")):
                    with self.assertRaises(expected):
                        action()
            else:
                with self.assertRaises(expected):
                    action()
            self.assertEqual(path.read_bytes(), b"existing complete file\n")
            self.assertEqual(list(root.iterdir()), [path], "failed writes must remove their temporary file")


for _mode in ("iterator", "validator", "serialization", "fsync", "replace", "fdopen"):
    _install(CanonicalBoundaryTests, "atomic_failure_" + _mode,
             lambda self, mode=_mode: self.check_publication_failure(mode))

for _name, _raw in [
    ("array_record", b"[]\n"), ("scalar_record", b"true\n"),
    ("invalid_utf8", b'{"text":"\xff"}\n'), ("bom", b"\xef\xbb\xbf{}\n"),
    ("escaped_key", b'{"\\u0061":1}\n'), ("negative_zero", b'{"n":-0}\n'),
    ("unsorted_nested", b'{"a":{"z":1,"a":2}}\n'),
    ("duplicate_nested", b'{"a":{"b":1,"b":2}}\n'),
    ("surrogate_value", b'{"a":"\\udfff"}\n'), ("trailing_bytes", b"{}\nxyz\n"),
]:
    def _bad_jsonl(self, raw=_raw):
        with self.assertRaises(ValidationError):
            list(iter_canonical_jsonl_bytes(raw))
    _install(CanonicalBoundaryTests, "jsonl_" + _name, _bad_jsonl)

for _name, _value in [
    ("tuple", (1, 2)), ("set", {1}), ("bytes", b"x"), ("non_string_key", {1: "x"}),
    ("nested_float", {"a": [{"b": 1.0}]}), ("negative_unsafe", -MAX_SAFE_INTEGER - 1),
    ("nonfinite", {"a": float("inf")}), ("surrogate_key", {"\ud800": 1}),
]:
    def _bad_value(self, value=_value):
        with self.assertRaises(ValidationError):
            dumps(value)
    _install(CanonicalBoundaryTests, "reject_" + _name, _bad_value)


class YAMLBoundaryTests(unittest.TestCase):
    def test_json_schema_loader_does_not_mutate_pyyaml_safe_loader(self):
        self.assertEqual(load_yaml_text("value: yes\n", source="fixture"), {"value": "yes"})
        self.assertEqual(yaml.safe_load("value: yes\n"), {"value": True})

    def test_json_schema_preserves_null_boolean_integer_and_quoted_text(self):
        value = load_yaml_text('a: null\nb: true\nc: false\nd: -42\ne: "true"\nf: "1.5"\n', source="fixture")
        self.assertIsNone(value["a"])
        self.assertIs(value["b"], True)
        self.assertIs(value["c"], False)
        self.assertIs(type(value["d"]), int)
        self.assertEqual((value["e"], value["f"]), ("true", "1.5"))

    def test_yaml_file_errors_use_validation_boundary(self):
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaises(ValidationError):
                load_yaml_file(Path(temporary) / "missing.yaml")


for _token in ("yes", "no", "on", "off", "True", "False", "NULL", "~", "01", "+1", "0x10", "0o10", "1_000", "2026-10-16", ".nan", ".inf", "1:20", "1.", ".5"):
    def _string_scalar(self, token=_token):
        self.assertEqual(load_yaml_text("value: " + token + "\n", source="fixture"), {"value": token})
    _install(YAMLBoundaryTests, "non_json_scalar_is_text_" + _token.replace("+", "plus").replace(".", "dot").replace(":", "colon").replace("~", "tilde"), _string_scalar)

for _name, _source in [
    ("nested_duplicate", "a: [{b: 1, b: 2}]"),
    ("quoted_duplicate", 'a: 1\n"a": 2'),
    ("anchor", "a: &named 1"), ("alias", "a: *missing"),
    ("explicit_string_tag", "a: !!str 12"),
    ("python_object_tag", "a: !!python/object/apply:os.system ['echo unsafe']"),
    ("version_directive", "%YAML 1.2\n---\na: 1"),
    ("tag_directive", "%TAG !e! tag:example.com,2026:\n---\na: 1"),
    ("merge_nested", 'a: [{"<<": {x: 1}}]'),
    ("multiple_documents", "a: 1\n---\na: 2"),
    ("integer_key", "1: value"), ("boolean_key", "true: value"),
    ("null_key", "null: value"), ("sequence_key", "? [a, b]\n: value"),
    ("mapping_key", "? {a: b}\n: value"),
    ("decimal_float", "a: 1.25"), ("exponent_float", "a: 1e3"),
    ("negative_exponent", "a: -1.2e-4"),
    ("positive_unsafe_integer", "a: 9007199254740992"),
    ("negative_unsafe_integer", "a: -9007199254740992"),
    ("huge_integer", "a: " + "9" * 5000),
    ("excessive_depth", "[" * 1200 + "0" + "]" * 1200),
    ("surrogate_escape", 'a: "\\uD800"'), ("invalid_escape", 'a: "\\q"'),
    ("unterminated_sequence", "a: [1, 2"),
]:
    def _bad_yaml(self, source=_source):
        with self.assertRaises(ValidationError):
            load_yaml_text(source, source="fixture")
    _install(YAMLBoundaryTests, "reject_" + _name, _bad_yaml)

for _name, _raw in [("bom", b"\xef\xbb\xbfa: 1\n"), ("invalid_utf8", b"a: \xff\n")]:
    def _bad_yaml_file(self, raw=_raw):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "input.yaml"
            path.write_bytes(raw)
            with self.assertRaises(ValidationError):
                load_yaml_file(path)
    _install(YAMLBoundaryTests, "file_" + _name, _bad_yaml_file)


class CasePackBoundaryTests(unittest.TestCase):
    def test_python_contract_extraction_never_executes_top_level_code(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            sentinel = root / "executed"
            pack = root / "case.py"
            text = dumps(_case_data())
            pack.write_text(f"from pathlib import Path\nPath({str(sentinel)!r}).touch()\nraise RuntimeError('must not execute')\nCASE_YAML = {text!r}\n", encoding="utf-8")
            registry = load_case_definitions(pack)
            self.assertEqual(registry.ids, ("sse2.boundary.memory.default",))
            self.assertFalse(sentinel.exists())

    def test_annotated_literal_contract_is_supported(self):
        with tempfile.TemporaryDirectory() as temporary:
            pack = Path(temporary) / "case.py"
            pack.write_text("CASE_YAML: str = " + repr(dumps(_case_data())) + "\n", encoding="utf-8")
            self.assertEqual(load_case_definitions(pack).ids, (_case_data()["id"],))

    def test_registry_identity_is_independent_of_file_and_array_order(self):
        first, second = _case_data(), _case_data()
        second["id"] = "sse2.boundary.other.default"
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            one, two = root / "one.json", root / "two.json"
            one.write_bytes(dump_bytes([first, second]))
            two.write_bytes(dump_bytes([second, first]))
            a, b = load_case_definitions(one), load_case_definitions(two)
            self.assertEqual(a.ids, b.ids)
            self.assertEqual(a.sha256, b.sha256)
            self.assertEqual(a.projected_sha256({first["id"]}), sha256_bytes(dump_bytes([first], newline=True)))

    def test_resolved_registry_is_a_snapshot_and_rejects_later_mutation(self):
        with tempfile.TemporaryDirectory() as temporary:
            pack = Path(temporary) / "case.json"
            pack.write_bytes(dump_bytes(_case_data()))
            registry = load_case_definitions(pack)
            snapshot = resolve_case_registry(registry)
            registry.get(registry.ids[0]).data["description"] = "changed"
            self.assertEqual(snapshot.get(snapshot.ids[0]).data["description"], "Memory boundary protocol fixture")
            with self.assertRaisesRegex(ValidationError, "mutated"):
                resolve_case_registry(registry)

    def test_duplicate_case_ids_across_files_are_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for name in ("one.json", "two.json"):
                (root / name).write_bytes(dump_bytes(_case_data()))
            with self.assertRaisesRegex(ValidationError, "duplicate case definition ID"):
                load_case_definitions(root)

    def test_projection_rejects_unknown_case_ids(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "case.json"
            path.write_bytes(dump_bytes(_case_data()))
            registry = load_case_definitions(path)
            with self.assertRaisesRegex(ValidationError, "unknown case IDs"):
                registry.projected_data({"foreign.case"})


for _name, _source in [
    ("missing", "value = 'CASE_YAML'"), ("nonliteral", "CASE_YAML = str('id: value')"),
    ("fstring", "CASE_YAML = f'id: value'"), ("bytes", "CASE_YAML = b'id: value'"),
    ("integer", "CASE_YAML = 1"), ("annotation_only", "CASE_YAML: str"),
    ("nested_only", "if True:\n    CASE_YAML = 'id: value'"),
    ("duplicate", "CASE_YAML = 'id: one'\nCASE_YAML = 'id: two'"),
    ("duplicate_annotation", "CASE_YAML = 'id: one'\nCASE_YAML: str = 'id: two'"),
    ("syntax", "CASE_YAML = ("), ("nul", "CASE_YAML = 'id: value'\x00"),
]:
    def _bad_pack(self, source=_source):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "case.py"
            path.write_text(source, encoding="utf-8")
            with self.assertRaises(ValidationError):
                load_case_definitions(path)
    _install(CasePackBoundaryTests, "python_" + _name, _bad_pack)

for _suffix, _name, _raw in [
    ("py", "bom", b"\xef\xbb\xbfCASE_YAML = '{}'"),
    ("py", "invalid_utf8", b"CASE_YAML = '\xff'"),
    ("json", "empty_array", b"[]"), ("json", "scalar", b"true"),
    ("json", "array_with_noncase", b"[null]"), ("txt", "unsupported_extension", b"{}"),
]:
    def _bad_contract_file(self, suffix=_suffix, raw=_raw):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / ("case." + suffix)
            path.write_bytes(raw)
            with self.assertRaises(ValidationError):
                load_case_definitions(path)
    _install(CasePackBoundaryTests, "file_" + _name, _bad_contract_file)


class MemoryAndImmediateBoundaryTests(unittest.TestCase):
    def check_case(self, path, value):
        data = _case_data()
        _put(data, path, value)
        with self.assertRaises(ValidationError):
            validate_case_definition(data)

    def check_input(self, path, value):
        case = validate_case_definition(_case_data())
        record = _input(case)
        _put(record, path, value)
        record["input_id"] = derive_input_id(record)
        with self.assertRaises(ValidationError):
            validate_input_record(record, case)

    def test_exact_buffer_end_and_signed_immediate_endpoints_are_valid(self):
        case = validate_case_definition(_case_data())
        for immediate in (-128, 0, 127):
            record = _input(case)
            record["immediates"]["control"] = immediate
            record["input_id"] = derive_input_id(record)
            validate_input_record(record, case)

    def test_identity_covers_allocation_bytes_alignment_offset_and_immediate(self):
        case = validate_case_definition(_case_data())
        original = _input(case)
        for path, value in [(("buffers", "allocation", "bytes"), "0x00112234"),
                            (("buffers", "allocation", "alignment"), 4),
                            (("operands", "source", "offset"), 0),
                            (("immediates", "control"), 0)]:
            candidate = copy.deepcopy(original)
            _put(candidate, path, value)
            self.assertNotEqual(derive_input_id(candidate), original["input_id"])

    def test_adjacent_ranges_are_accepted_and_overlapping_ranges_rejected(self):
        data = _case_data()
        data["memory_contract"]["source"]["read_ranges"] = [{"offset": 0, "byte_length": 1}, {"offset": 1, "byte_length": 1}]
        validate_case_definition(data)
        data["memory_contract"]["source"]["read_ranges"][0]["byte_length"] = 2
        with self.assertRaisesRegex(ValidationError, "overlap"):
            validate_case_definition(data)

    def test_result_context_authenticates_the_input_identity(self):
        case = validate_case_definition(_case_data())
        record = _input(case)
        result = {"schema_version": 1, "case_id": case.id, "input_id": "f" * 64,
                  "runner": "intel", "duration_ns": 0, "status": "unsupported",
                  "error": {"code": "missing.isa", "stage": "capability"}}
        with self.assertRaisesRegex(ValidationError, "input_id"):
            validate_result_record(result, case, role="intel", input_record=record)


def _observed(record):
    return {"return": {"element": "u8", "lanes": ["0x00", "0xff"]},
            "buffers": {"allocation": {"byte_offset": 0, "bytes": record["buffers"]["allocation"]["bytes"]}}}


def _result(case, record):
    return {"schema_version": 1, "case_id": case.id, "input_id": record["input_id"],
            "runner": "intel", "duration_ns": 0, "status": "ok", "observed": _observed(record)}


class ResultBoundaryTests(unittest.TestCase):
    def check_mutation(self, path, value):
        case = validate_case_definition(_case_data())
        record = _input(case)
        result = _result(case, record)
        _put(result, path, value)
        with self.assertRaises(ValidationError):
            validate_result_record(result, case, role="intel", input_record=record)

    def test_observed_buffers_and_return_match_valid_input_context(self):
        case = validate_case_definition(_case_data())
        record = _input(case)
        validate_result_record(_result(case, record), case, role="intel", input_record=record)

    def check_error(self, status, stage):
        case = validate_case_definition(_case_data())
        record = _input(case)
        result = _result(case, record)
        result.pop("observed")
        result.update(status=status, error={"code": "test.stable-code_1", "stage": stage})
        validate_result_record(result, case, role="intel", input_record=record)
        result["error"]["stage"] = "wrong_stage"
        with self.assertRaisesRegex(ValidationError, "stage"):
            validate_result_record(result, case, role="intel", input_record=record)

    def check_exception_flags(self, flags):
        data = _case_data()
        data["environment"]["observe_fp_exceptions"] = True
        expected = _observed(_input(validate_case_definition(_case_data())))
        expected["fp_exceptions"] = []
        data["regressions"] = {"exception-witness.v1": {"input_id": "0" * 64,
            "expected_intel": {"status": "ok", "observed": expected}}}
        case = validate_case_definition(data)
        record = _input(case)
        result = _result(case, record)
        result["observed"]["fp_exceptions"] = flags
        with self.assertRaises(ValidationError):
            validate_result_record(result, case, role="intel", input_record=record)

    def test_all_fp_exception_flags_are_valid_only_in_declared_order(self):
        data = _case_data()
        data["environment"]["observe_fp_exceptions"] = True
        flags = ["invalid", "divide-by-zero", "overflow", "underflow", "inexact"]
        expected = _observed(_input(validate_case_definition(_case_data())))
        expected["fp_exceptions"] = flags
        data["regressions"] = {"exception-witness.v1": {"input_id": "0" * 64,
            "expected_intel": {"status": "ok", "observed": expected}}}
        case = validate_case_definition(data)
        record = _input(case)
        result = _result(case, record)
        result["observed"]["fp_exceptions"] = flags
        validate_result_record(result, case, role="intel", input_record=record)

    def test_result_buffer_ids_are_identifiers_even_without_input_context(self):
        case = validate_case_definition(_case_data())
        record = _input(case)
        result = _result(case, record)
        result["observed"]["buffers"]["../allocation"] = result["observed"]["buffers"].pop("allocation")
        with self.assertRaises(ValidationError):
            validate_result_record(result, case, role="intel")


for _path, _values in [
    (("observed", "buffers"), [{}, {"extra": {"byte_offset": 0, "bytes": "0x00112233"}}, []]),
    (("observed", "buffers", "allocation", "byte_offset"), [True, -1, 1, "0"]),
    (("observed", "buffers", "allocation", "bytes"), ["0x", "0x001122", "0x0011223344", "0x0011223", "0X00112233", "0x001122GG", True]),
    (("observed", "return", "element"), ["i8", "u16", [], None]),
    (("observed", "return", "lanes"), [["0x00"], ["0x00", "0xff", "0x01"], [0, 255], ["0x0000", "0x00ff"]]),
]:
    for _index, _value in enumerate(_values):
        _install(ResultBoundaryTests, "observed_" + "_".join(map(str, _path)) + "_" + str(_index),
                 lambda self, path=_path, value=_value: self.check_mutation(path, value))

for _status, _stage in (("unsupported", "capability"), ("invalid_input", "input_validation"),
                        ("runtime_error", "execution"), ("signal", "signal"), ("infrastructure_error", "runner")):
    _install(ResultBoundaryTests, "error_stage_" + _status,
             lambda self, status=_status, stage=_stage: self.check_error(status, stage))

for _name, _flags in (("duplicate", ["invalid", "invalid"]), ("reordered", ["inexact", "invalid"]),
                      ("unknown", ["precision"]), ("number", [1]), ("boolean", [True]),
                      ("scalar", "invalid"), ("object", {}), ("null", None)):
    _install(ResultBoundaryTests, "fp_exception_flags_" + _name,
             lambda self, flags=_flags: self.check_exception_flags(flags))

for _name, _value in (("array", []), ("null", None), ("integer", 1), ("string", "record")):
    for _kind in ("input", "result"):
        def _root_shape(self, value=_value, kind=_kind):
            case = validate_case_definition(_case_data())
            with self.assertRaises(ValidationError):
                if kind == "input":
                    validate_input_record(value, case)
                else:
                    validate_result_record(value, case, role="intel")
        _install(ResultBoundaryTests, _kind + "_root_" + _name, _root_shape)


for _path, _values in [
    (("immediates", "control", "values"), [[], [-129], [128], [0, -128], [0, 0], [False], ["0"], None]),
    (("immediates", "control", "compile_time"), [False, 1, "true"]),
    (("memory_contract", "source", "required_alignment"), [0, -1, 3, True, "2"]),
    (("memory_contract", "source", "read_ranges"), [[], {}, [{"offset": 0, "byte_length": 0}], [{"offset": -1, "byte_length": 1}], [{"offset": True, "byte_length": 1}], [{"offset": MAX_SAFE_INTEGER, "byte_length": 1}]]),
    (("memory_contract", "source", "access"), ["execute", [], None]),
    (("signature", "arguments", 1, "element"), ["i16", "f32"]),
]:
    for _index, _value in enumerate(_values):
        _install(MemoryAndImmediateBoundaryTests, "case_" + "_".join(map(str, _path)) + "_" + str(_index),
                 lambda self, path=_path, value=_value: self.check_case(path, value))

for _path, _values in [
    (("immediates", "control"), [-129, 128, -1, True, "0"]),
    (("operands", "source", "buffer"), ["missing", "../allocation", "", False]),
    (("operands", "source", "offset"), [-1, True, "2", 1, 4, 5]),
    (("buffers", "allocation", "alignment"), [0, -1, 3, True, "2", 1]),
    (("buffers", "allocation", "bytes"), ["0x", "0x001122", "0x0011223", "0x001122GG", "0X00112233", "0x001122AA", 112233, []]),
]:
    for _index, _value in enumerate(_values):
        _install(MemoryAndImmediateBoundaryTests, "input_" + "_".join(map(str, _path)) + "_" + str(_index),
                 lambda self, path=_path, value=_value: self.check_input(path, value))


def _isa_data():
    return {"schema_version": 1, "tokens": [
        {"token": "base", "architecture": "x86_64", "detector": {"id": "cpuid.base", "version": 1}, "implies": []},
        {"token": "extra", "architecture": "x86_64", "detector": {"id": "cpuid.extra", "version": 1}, "implies": ["base"]},
    ]}


class ISABoundaryTests(unittest.TestCase):
    def check_mutation(self, path, value):
        data = _isa_data()
        _put(data, path, value)
        with self.assertRaises(ValidationError):
            validate_isa_registry(data)

    def test_transitive_closure_is_deduplicated_and_sorted(self):
        data = _isa_data()
        data["tokens"].append({"token": "last", "architecture": "x86_64", "detector": {"id": "cpuid.last", "version": 1}, "implies": ["base", "extra"]})
        registry = validate_isa_registry(data)
        self.assertEqual(registry.closure(["last", "base", "last"], architecture="x86_64"), ("base", "extra", "last"))
        self.assertEqual(registry.closure([], architecture="x86_64"), ())

    def test_cross_architecture_implication_is_rejected(self):
        data = _isa_data()
        data["tokens"][0]["architecture"] = "ppc64le"
        with self.assertRaisesRegex(ValidationError, "different architecture"):
            validate_isa_registry(data)

    def test_cycle_of_multiple_tokens_is_rejected(self):
        data = _isa_data()
        data["tokens"][0]["implies"] = ["extra"]
        with self.assertRaisesRegex(ValidationError, "cycle"):
            validate_isa_registry(data)

    def test_closure_rejects_foreign_architecture(self):
        with self.assertRaisesRegex(ValidationError, "not ppc64le"):
            validate_isa_registry(_isa_data()).closure(["base"], architecture="ppc64le")

    def test_deep_acyclic_graph_does_not_depend_on_python_recursion_limit(self):
        count = 1100
        data = {"schema_version": 1, "tokens": [
            {"token": f"token{index:04d}", "architecture": "x86_64",
             "detector": {"id": "cpuid.test", "version": 1},
             "implies": [f"token{index + 1:04d}"] if index < count - 1 else []}
            for index in range(count)]}
        registry = validate_isa_registry(data)
        closure = registry.closure(["token0000"], architecture="x86_64")
        self.assertEqual(len(closure), count)
        self.assertEqual((closure[0], closure[-1]), ("token0000", "token1099"))

    def test_deep_cycle_is_reported_as_validation_error(self):
        count = 1100
        data = {"schema_version": 1, "tokens": [
            {"token": f"token{index:04d}", "architecture": "x86_64",
             "detector": {"id": "cpuid.test", "version": 1},
             "implies": [f"token{(index + 1) % count:04d}"]}
            for index in range(count)]}
        with self.assertRaisesRegex(ValidationError, "cycle"):
            validate_isa_registry(data)

    def test_duplicate_and_unsorted_tokens_are_rejected(self):
        data = _isa_data()
        data["tokens"].reverse()
        with self.assertRaisesRegex(ValidationError, "token-sorted unique"):
            validate_isa_registry(data)
        data = _isa_data()
        data["tokens"].insert(0, copy.deepcopy(data["tokens"][0]))
        with self.assertRaisesRegex(ValidationError, "token-sorted unique"):
            validate_isa_registry(data)

    def test_used_projection_ignores_unused_tokens_but_includes_implications(self):
        isa = load_isa_registry(PROJECT / "contracts/isa-registry.json")
        case = validate_case_definition(_case_data(), isa_registry=isa)
        contract = project_used_isa(isa, [case])
        self.assertEqual([entry["token"] for entry in contract.data["tokens"]], ["power8", "sse2", "vsx"])
        self.assertEqual(validate_used_isa_contract(copy.deepcopy(contract.data), registry=isa, cases=[case]).sha256, contract.sha256)

    def check_used_contract_type(self, path, value):
        isa = load_isa_registry(PROJECT / "contracts/isa-registry.json")
        case = validate_case_definition(_case_data(), isa_registry=isa)
        supplied = copy.deepcopy(project_used_isa(isa, [case]).data)
        _put(supplied, path, value)
        with self.assertRaises(ValidationError):
            validate_used_isa_contract(supplied, registry=isa, cases=[case])


for _path, _values in [
    (("schema_version",), [True, "1", 0, 2]),
    (("tokens",), [[], {}, None]),
    (("tokens", 0, "token"), ["Base", "base token", "", [], 1]),
    (("tokens", 0, "architecture"), ["x86", "arm64", [], None]),
    (("tokens", 0, "detector", "id"), ["", "CPUID", "bad id", {}]),
    (("tokens", 0, "detector", "version"), [True, 0, -1, "1", MAX_SAFE_INTEGER + 1]),
    (("tokens", 1, "implies"), [["unknown"], ["extra"], ["base", "base"], ["extra", "base"], {}, [True]]),
]:
    for _index, _value in enumerate(_values):
        _install(ISABoundaryTests, "registry_" + "_".join(map(str, _path)) + "_" + str(_index),
                 lambda self, path=_path, value=_value: self.check_mutation(path, value))

for _path, _value in [(("schema_version",), True), (("schema_version",), 1.0),
                      (("tokens", 0, "detector", "version"), True),
                      (("tokens", 0, "detector", "version"), 1.0)]:
    _install(ISABoundaryTests, "projection_rejects_type_confusion_" + "_".join(map(str, _path)) + "_" + type(_value).__name__,
             lambda self, path=_path, value=_value: self.check_used_contract_type(path, value))


class ProjectBoundaryTests(unittest.TestCase):
    def check_project(self, source):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "equilane.toml"
            path.write_text(source, encoding="utf-8")
            with self.assertRaises(ValidationError):
                load_project(path)

    def test_absolute_paths_are_preserved(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "equilane.toml"
            path.write_text('[equilane]\nschema_version=1\nsuite="/absolute/suite"\nisa_registry="/absolute/isa.json"\n')
            config = load_project(path)
            self.assertEqual(config.suite, Path("/absolute/suite"))
            self.assertEqual(config.isa_registry, Path("/absolute/isa.json"))

    def test_explicit_missing_file_never_falls_back(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "ioitf.toml").write_text('[ioitf]\nschema_version=1\nsuite="suite"\nisa_registry="isa.json"\n')
            with self.assertRaises(ValidationError):
                load_project(root / "equilane.toml")


for _field, _values in {
    "schema_version": ["true", "false", "0", "2", '"1"', "[1]", "{}"],
    "suite": ['""', "true", "42", "[]", "{}"],
    "isa_registry": ['""', "false", "42", "[]", "{}"],
}.items():
    for _index, _value in enumerate(_values):
        _settings = {"schema_version": "1", "suite": '"suite"', "isa_registry": '"isa.json"'}
        _settings[_field] = _value
        _source = "[equilane]\n" + "\n".join(key + "=" + value for key, value in _settings.items())
        _install(ProjectBoundaryTests, _field + "_" + str(_index),
                 lambda self, source=_source: self.check_project(source))

for _name, _source in [
    ("empty", ""), ("scalar_root", "equilane=1"),
    ("two_tables", "[equilane]\n[ioitf]"), ("unknown_table", "[other]"),
    ("array_table", "[[equilane]]\nschema_version=1"),
    ("nested_unknown_table", '[equilane]\nschema_version=1\nsuite="suite"\nisa_registry="isa.json"\n[equilane.extra]\nx=1'),
    ("duplicate_field", '[equilane]\nschema_version=1\nschema_version=1'),
    ("missing_field", '[equilane]\nschema_version=1\nsuite="suite"'),
]:
    _install(ProjectBoundaryTests, _name, lambda self, source=_source: self.check_project(source))


class GeneratorBoundaryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.isa = load_isa_registry(PROJECT / "contracts/isa-registry.json")
        cls.cases = load_case_definitions(PROJECT / "10_official_suite/cases/set1-f64x2.py", isa_registry=cls.isa)

    def check_option(self, key, value):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            output = root / "generated"
            options = {"cases": self.cases, "isa_registry": self.isa, "output": output, "count_per_case": 2}
            options[key] = value
            with self.assertRaises(ValidationError):
                generate_artifact(**options)
            self.assertFalse(output.exists(), "option validation should precede any artifact mutation")

    def test_retained_records_match_the_exact_published_stream_and_progress(self):
        with tempfile.TemporaryDirectory() as temporary:
            visited = []
            generated = generate_artifact(cases=self.cases, isa_registry=self.isa, output=temporary,
                                          count_per_case=21, retain_records=True, progress=visited.append)
            self.assertEqual(visited, list(range(1, 22)))
            artifact = generated.as_validated_artifact()
            self.assertEqual(tuple(iter_canonical_jsonl_bytes(generated.vectors_path.read_bytes())), artifact.records)
            self.assertEqual(artifact.case_ids, self.cases.ids)
            self.assertEqual(artifact.record_count, 21)
            self.assertEqual(artifact.sha256, sha256_bytes(generated.vectors_path.read_bytes()))

    def test_unretained_generation_cannot_claim_in_memory_validation(self):
        with tempfile.TemporaryDirectory() as temporary:
            generated = generate_artifact(cases=self.cases, isa_registry=self.isa, output=temporary, count_per_case=1)
            with self.assertRaisesRegex(ValidationError, "not retained"):
                generated.as_validated_artifact()

    def test_seed_changes_random_suffix_and_preserves_structured_prefix(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            runs = [generate_artifact(cases=self.cases, isa_registry=self.isa, output=root / str(index),
                                      count_per_case=22, seed=seed, retain_records=True)
                    for index, seed in enumerate(("0x0000000000000000", "0xffffffffffffffff"))]
            first, second = runs[0].records, runs[1].records
            self.assertEqual([r for r in first if r["generation"]["class"] != "random"],
                             [r for r in second if r["generation"]["class"] != "random"])
            random_first = [r["input_id"] for r in first if r["generation"]["class"] == "random"]
            random_second = [r["input_id"] for r in second if r["generation"]["class"] == "random"]
            self.assertTrue(random_first)
            self.assertNotEqual(random_first, random_second)


for _key, _values in {
    "count_per_case": [True, False, 0, -1, 1_000_001, 1.5, "2"],
    "jobs": [True, False, 0, -1, 1.5, "2", None],
    "seed": [None, 1, [], "0x0", "0X0000000000000000", "0x000000000000000G", "0x00000000000000000"],
    "profile": [None, [], {}, "SMOKE", "unknown"],
}.items():
    for _index, _value in enumerate(_values):
        _install(GeneratorBoundaryTests, _key + "_" + str(_index),
                 lambda self, key=_key, value=_value: self.check_option(key, value))


if __name__ == "__main__":
    unittest.main()
