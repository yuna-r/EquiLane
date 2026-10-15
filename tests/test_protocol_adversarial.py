"""Adversarial schema/artifact tests derived from specification sections 7–15.

Every matrix entry is a separately discoverable test. Rejection requires the
public ValidationError boundary; leaked TypeError/KeyError is an assertion
failure rather than an accidental test error. No known defect is suppressed.
"""

from __future__ import annotations

import copy
from pathlib import Path
import shutil
import sys
import tempfile
import unittest

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "src"))

from equilane.artifacts import validate_input_artifact, validate_result_artifact
from equilane.canonical import dump_bytes, iter_canonical_jsonl, read_canonical_json, sha256_bytes, write_jsonl
from equilane.cases import load_case_definitions, validate_case_definition
from equilane.compare import compare_result_records
from equilane.errors import ValidationError
from equilane.fixture import run_fixture
from equilane.generator import generate_artifact
from equilane.isa import load_isa_registry
from equilane.records import derive_input_id, validate_input_record, validate_result_record
from equilane.replay import load_failure_bundle
from equilane.report import write_failure_bundle


def _case_data():
    return {
        "schema_version": 1, "id": "sse2.protocol.f64x2.default",
        "description": "Protocol boundary fixture", "tags": [],
        "intel": {"required_isa": ["sse2"], "symbol": "intel_protocol"},
        "openpower": {"required_isa": ["power8", "vsx"], "symbol": "power_protocol"},
        "signature": {
            "arguments": [{"name": "a", "type": "vector", "element": "f64", "lanes": 2}],
            "return": {"type": "vector", "element": "f64", "lanes": 2},
        },
        "comparison": {"mode": "bit_exact"},
        "input_domain": {"exclude": []},
        "environment": {"fp_rounding_modes": ["nearest_even"], "observe_fp_exceptions": False},
    }


def _input(case):
    record = {
        "schema_version": 1, "case_id": case.id, "sequence": 1,
        "environment": {"fp_mode": "ieee", "rounding": "nearest_even"},
        "generation": {"class": "structured"},
        "operands": {"a": {"element": "f64", "lanes": ["0x0000000000000000", "0x3ff0000000000000"]}},
    }
    record["input_id"] = derive_input_id(record)
    return record


def _result(case, record):
    return {
        "schema_version": 1, "case_id": case.id, "input_id": record["input_id"],
        "runner": "intel", "duration_ns": 0, "status": "ok",
        "observed": {"return": copy.deepcopy(record["operands"]["a"])},
    }


def _at(value, path):
    for key in path:
        value = value[key]
    return value


def _put(value, path, replacement):
    _at(value, path[:-1])[path[-1]] = copy.deepcopy(replacement)


def _reject(test, action):
    try:
        action()
    except Exception as exc:
        test.assertIsInstance(exc, ValidationError,
                              f"schema rejection leaked {type(exc).__name__}: {exc}")
    else:
        test.fail("invalid protocol data was accepted")


def _install(cls, name, action):
    def test(self):
        action(self)
    test.__name__ = "test_" + name
    setattr(cls, test.__name__, test)


class RecordSchemaAdversarialTests(unittest.TestCase):
    def check_mutation(self, scope, path, value, *, operation="set", comparison=False):
        data = _case_data()
        if comparison:
            data["comparison"] = {
                "mode": "ieee_value", "signed_zero": "equal",
                "nan": {"both_nan": "equal", "payload": "ignore", "quiet_signaling": "ignore", "sign": "ignore"},
            }
        case = validate_case_definition(data)
        record = _input(case)
        candidate = data if scope == "case" else record if scope == "input" else _result(case, record)
        if operation == "delete":
            del _at(candidate, path[:-1])[path[-1]]
        elif operation == "extra":
            _at(candidate, path)["undeclared"] = True
        else:
            _put(candidate, path, value)
        if scope == "input" and "input_id" in candidate:
            # Refresh identity where possible so field-shape tests cannot pass
            # merely because an otherwise valid input has an outdated digest.
            try:
                candidate["input_id"] = derive_input_id(candidate)
            except (KeyError, ValidationError):
                pass
        actions = {
            "case": lambda: validate_case_definition(candidate),
            "input": lambda: validate_input_record(candidate, case),
            "result": lambda: validate_result_record(candidate, case, role="intel", input_record=record),
        }
        _reject(self, actions[scope])

    def test_input_identity_does_not_include_sequence_or_generation_metadata(self):
        case = validate_case_definition(_case_data())
        first = _input(case)
        second = copy.deepcopy(first)
        second["sequence"] = 900
        second["generation"] = {"class": "random", "algorithm": "splitmix64", "seed": "0x0123456789abcdef"}
        self.assertEqual(derive_input_id(first), derive_input_id(second))
        validate_input_record(second, case)

    def test_input_identity_includes_environment_and_every_operand_lane(self):
        case = validate_case_definition(_case_data())
        original = _input(case)
        for lane in range(2):
            changed = copy.deepcopy(original)
            changed["operands"]["a"]["lanes"][lane] = "0x0000000000000001"
            self.assertNotEqual(derive_input_id(changed), original["input_id"])
        changed = copy.deepcopy(original)
        changed["environment"]["rounding"] = "toward_zero"
        self.assertNotEqual(derive_input_id(changed), original["input_id"])

    def test_result_context_rejects_a_foreign_well_formed_input_identity(self):
        case = validate_case_definition(_case_data())
        record = _input(case)
        result = _result(case, record)
        result["input_id"] = "f" * 64
        _reject(self, lambda: validate_result_record(result, case, role="intel", input_record=record))


# Closedness must hold independently at each normative nesting level.
_CLOSED = {
    "case": [(), ("intel",), ("openpower",), ("signature",),
             ("signature", "arguments", 0), ("signature", "return"),
             ("environment",), ("input_domain",), ("comparison",)],
    "input": [(), ("environment",), ("generation",), ("operands",), ("operands", "a")],
    "result": [(), ("observed",), ("observed", "return")],
}
for _scope, _paths in _CLOSED.items():
    for _path in _paths:
        _label = "_".join(map(str, _path)) or "root"
        _install(RecordSchemaAdversarialTests, f"{_scope}_{_label}_unknown_field",
                 lambda self, s=_scope, p=_path: self.check_mutation(s, p, None, operation="extra"))

for _scope, _keys in {
    "case": list(_case_data()),
    "input": list(_input(validate_case_definition(_case_data()))),
    "result": list(_result(validate_case_definition(_case_data()), _input(validate_case_definition(_case_data())))),
}.items():
    for _key in _keys:
        _install(RecordSchemaAdversarialTests, f"{_scope}_missing_{_key}",
                 lambda self, s=_scope, k=_key: self.check_mutation(s, (k,), None, operation="delete"))

_INVALID_RECORD_FIELDS = [
    ("case", ("schema_version",), [True, 0, 2, "1", None]),
    ("case", ("environment", "observe_fp_exceptions"), [0, 1, "false", []]),
    ("case", ("signature", "return", "lanes"), [True, 0, -1, "2"]),
    ("case", ("signature", "arguments", 0, "lanes"), [False, 0, -1, "2"]),
    ("case", ("intel", "symbol"), ["../call", "a-b", "", "呼出し"]),
    ("case", ("tags",), [["z", "a"], ["a", "a"], [True], ["bad tag"]]),
    ("input", ("schema_version",), [True, 0, 2, "1"]),
    ("input", ("sequence",), [True, 0, -1, "1", 1 << 53]),
    ("input", ("environment", "rounding"), [None, True, "nearest", "toward_zero"]),
    ("input", ("operands", "a", "lanes"), [[], ["0x0000000000000000"], {}, "00"]),
    ("input", ("operands", "a", "lanes", 0), [None, True, "0X0000000000000000", "0x000000000000000A", "0x0", "0x00000000000000000"]),
    ("input", ("generation", "class"), [None, True, "", "unknown"]),
    ("result", ("schema_version",), [True, 0, 2, "1"]),
    ("result", ("duration_ns",), [True, -1, "0", 1 << 53]),
    ("result", ("input_id",), ["A" * 64, "0" * 63, True, None]),
    ("result", ("runner",), [None, True, "openpower", "Intel"]),
    ("result", ("observed", "return", "lanes", 0), [None, True, "0x0", "0x000000000000000A"]),
]
for _scope, _path, _values in _INVALID_RECORD_FIELDS:
    for _i, _value in enumerate(_values):
        _label = "_".join(map(str, _path))
        _install(RecordSchemaAdversarialTests, f"{_scope}_{_label}_invalid_{_i}",
                 lambda self, s=_scope, p=_path, v=_value: self.check_mutation(s, p, v))

for _field in ("both_nan", "payload", "quiet_signaling", "sign"):
    for _i, _value in enumerate([None, False, [], {}, "unknown"]):
        _install(RecordSchemaAdversarialTests, f"nan_{_field}_invalid_{_i}",
                 lambda self, f=_field, v=_value: self.check_mutation("case", ("comparison", "nan", f), v, comparison=True))
for _i, _value in enumerate([None, False, [], {}, "unknown"]):
    _install(RecordSchemaAdversarialTests, f"signed_zero_invalid_{_i}",
             lambda self, v=_value: self.check_mutation("case", ("comparison", "signed_zero"), v, comparison=True))


class ArtifactProtocolAdversarialTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.template = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.template.cleanup)
        cls.template_root = Path(cls.template.name)
        cls.isa = load_isa_registry(PROJECT / "contracts/isa-registry.json")
        cls.cases = load_case_definitions(PROJECT / "10_official_suite/cases/add-f64x2.py", isa_registry=cls.isa)
        generated = generate_artifact(cases=cls.cases, isa_registry=cls.isa,
                                      output=cls.template_root / "vectors", count_per_case=2)
        inputs = validate_input_artifact(generated.manifest_path, cls.cases, cls.isa)
        for role in ("intel", "openpower"):
            run_fixture(input_artifact=inputs, cases=cls.cases, isa_registry=cls.isa,
                        role=role, output=cls.template_root / role)
        # One actual mismatch creates a real self-contained replay bundle.
        manifest_path = cls.template_root / "openpower/power-results.manifest.json"
        manifest = read_canonical_json(manifest_path)
        result_path = manifest_path.with_name("power-results.jsonl")
        records = list(iter_canonical_jsonl(result_path))
        old = records[0]["observed"]["return"]["lanes"][0]
        records[0]["observed"]["return"]["lanes"][0] = f"0x{int(old, 16) ^ 1:016x}"
        count, length, digest = write_jsonl(result_path, records)
        manifest["results"].update(record_count=count, byte_length=length, sha256=digest)
        manifest_path.write_bytes(dump_bytes(manifest, newline=True))
        intel = validate_result_artifact(cls.template_root / "intel", cls.cases, cls.isa, input_artifact=inputs)
        power = validate_result_artifact(cls.template_root / "openpower", cls.cases, cls.isa, input_artifact=inputs)
        record = inputs.records[0]
        comparison = compare_result_records(cls.cases.get(record["case_id"]), record,
                                            intel.records[record["input_id"]], power.records[record["input_id"]])
        marker = write_failure_bundle(output=cls.template_root / "failures", input_artifact=inputs,
                                     intel_artifact=intel, openpower_artifact=power,
                                     cases=cls.cases, isa_registry=cls.isa, input_record=record, comparison=comparison)
        cls.bundle_relative = (marker / "failure.json").relative_to(cls.template_root)

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / "copy"
        shutil.copytree(self.template_root, self.root)

    def manifest_path(self, scope):
        if scope == "input":
            return self.root / "vectors/test-vectors.manifest.json"
        if scope == "result":
            return self.root / "intel/intel-results.manifest.json"
        return self.root / self.bundle_relative

    def validate(self, scope):
        if scope == "input":
            return validate_input_artifact(self.manifest_path(scope), self.cases, self.isa)
        if scope == "result":
            return validate_result_artifact(self.manifest_path(scope), self.cases, self.isa,
                                            input_artifact=self.manifest_path("input"))
        return load_failure_bundle(self.manifest_path(scope))

    def mutate_manifest(self, scope, path, value):
        manifest_path = self.manifest_path(scope)
        value_tree = read_canonical_json(manifest_path)
        _put(value_tree, path, value)
        manifest_path.write_bytes(dump_bytes(value_tree, newline=True))
        _reject(self, lambda: self.validate(scope))

    def rewrite_data(self, scope, mutation):
        manifest_path = self.manifest_path(scope)
        manifest = read_canonical_json(manifest_path)
        metadata_key = "test_vectors" if scope == "input" else "results"
        metadata = manifest[metadata_key]
        data_path = manifest_path.with_name(metadata["file"])
        records = list(iter_canonical_jsonl(data_path))
        mutation(records)
        count, length, digest = write_jsonl(data_path, records)
        metadata.update(record_count=count, byte_length=length, sha256=digest)
        manifest_path.write_bytes(dump_bytes(manifest, newline=True))

    def test_result_records_may_be_reordered_when_all_ids_are_authenticated(self):
        self.rewrite_data("result", lambda rows: rows.reverse())
        artifact = self.validate("result")
        self.assertEqual(artifact.record_count, 2)
        self.assertEqual(set(artifact.records), {r["input_id"] for r in self.validate("input").records})

    def test_valid_bundle_and_original_artifacts_remain_accepted(self):
        self.assertEqual(self.validate("input").record_count, 2)
        self.assertEqual(self.validate("result").record_count, 2)
        self.assertEqual(self.validate("bundle").input_artifact.record_count, 1)


_MANIFEST_MUTATIONS = [
    ("input", ("complete",), [False, 1, "true"]),
    ("input", ("schema_version",), [True, 0, 2]),
    ("input", ("test_vectors", "file"), ["../test-vectors.jsonl", "/tmp/test-vectors.jsonl", "test-vectors.jsonl\x00"]),
    ("input", ("test_vectors", "record_count"), [True, 0, 1, 3]),
    ("input", ("test_vectors", "byte_length"), [True, 0, -1]),
    ("input", ("case_definitions_sha256",), ["0" * 64, "F" * 64]),
    ("input", ("used_isa_contract_sha256",), ["0" * 64]),
    ("result", ("complete",), [False, 1, "true"]),
    ("result", ("runner", "abi_version"), [True, 0, 2]),
    ("result", ("runner", "role"), ["openpower", "unknown"]),
    ("result", ("input_sha256",), ["0" * 64, "F" * 64]),
    ("result", ("results", "file"), ["../intel-results.jsonl", "/tmp/intel-results.jsonl"]),
    ("result", ("environment", "assertions_enabled"), [1, "false"]),
    ("result", ("environment", "git_commit"), ["0" * 7, "G" * 40]),
    ("result", ("environment", "architecture"), ["ppc64le", "arm64"]),
    ("result", ("environment", "available_isa"), [[], ["sse2", "sse2"]]),
    ("result", ("environment", "build_units", 0, "assertions_enabled"), [1, "true"]),
    ("result", ("environment", "link", "binary_sha256"), ["F" * 64]),
    ("result", ("preflight", "probe_suite_sha256"), ["0" * 64]),
    ("result", ("preflight", "probes"), [[], [{"id": "foreign.v1", "status": "passed"}]]),
    ("bundle", ("mismatch_count",), [True, 0, 2]),
    ("bundle", ("input_id",), ["0" * 64]),
    ("bundle", ("test_vectors_manifest",), ["../test-vectors.manifest.json", "/tmp/test-vectors.manifest.json"]),
    ("bundle", ("contracts", "case_definitions", "file"), ["../case-definitions.json"]),
    ("bundle", ("contracts", "used_isa", "sha256"), ["0" * 64]),
    ("bundle", ("first_difference",), [{"kind": "status", "intel": "ok", "openpower": "unsupported"}]),
]
for _scope, _path, _values in _MANIFEST_MUTATIONS:
    for _i, _value in enumerate(_values):
        _install(ArtifactProtocolAdversarialTests, f"{_scope}_{'_'.join(map(str, _path))}_{_i}",
                 lambda self, s=_scope, p=_path, v=_value: self.mutate_manifest(s, p, v))


def _duplicate_rows(rows):
    rows[1] = copy.deepcopy(rows[0])


def _duplicate_inputs(rows):
    _duplicate_rows(rows)
    rows[1]["sequence"] = 2


_DATA_MUTATIONS = [
    ("input", "duplicate_identity", _duplicate_inputs),
    ("input", "reordered_sequence", lambda rows: rows.reverse()),
    ("input", "sequence_gap", lambda rows: rows[1].__setitem__("sequence", 3)),
    ("input", "unknown_case", lambda rows: rows[0].__setitem__("case_id", "sse2.foreign.default")),
    ("input", "stale_identity", lambda rows: rows[0]["operands"]["a"]["lanes"].__setitem__(0, "0x0000000000000001")),
    ("result", "duplicate_identity", _duplicate_rows),
    ("result", "missing_identity", lambda rows: rows.pop()),
    ("result", "foreign_identity", lambda rows: rows[0].__setitem__("input_id", "f" * 64)),
    ("result", "wrong_role", lambda rows: rows[0].__setitem__("runner", "openpower")),
    ("result", "foreign_case", lambda rows: rows[0].__setitem__("case_id", "sse2.foreign.default")),
]
for _scope, _label, _mutation in _DATA_MUTATIONS:
    def _data_test(self, scope=_scope, mutation=_mutation):
        self.rewrite_data(scope, mutation)
        _reject(self, lambda: self.validate(scope))
    _install(ArtifactProtocolAdversarialTests, f"authenticated_{_scope}_{_label}", _data_test)


def _noncanonical_manifest(self, scope, corruption):
    path = self.manifest_path(scope)
    raw = path.read_bytes()
    path.write_bytes(corruption(raw))
    _reject(self, lambda: self.validate(scope))


for _scope in ("input", "result", "bundle"):
    for _label, _corruption in [
        ("bom", lambda raw: b"\xef\xbb\xbf" + raw),
        ("crlf", lambda raw: raw.replace(b"\n", b"\r\n")),
        ("missing_lf", lambda raw: raw[:-1]),
        ("double_lf", lambda raw: raw + b"\n"),
        ("whitespace", lambda raw: b" " + raw),
        ("invalid_utf8", lambda raw: raw[:-1] + b"\xff\n"),
    ]:
        _install(ArtifactProtocolAdversarialTests, f"{_scope}_canonical_{_label}",
                 lambda self, s=_scope, c=_corruption: _noncanonical_manifest(self, s, c))


def _symlink_member(self, relative):
    root = self.manifest_path("bundle").parent
    path = root / relative
    external = self.root / "external-member"
    if path.is_dir():
        shutil.copytree(path, external)
        shutil.rmtree(path)
    else:
        shutil.copyfile(path, external)
        path.unlink()
    path.symlink_to(external, target_is_directory=external.is_dir())
    _reject(self, lambda: self.validate("bundle"))


for _relative in ("contracts", "contracts/case-definitions.json", "test-vectors.jsonl",
                  "baseline/intel", "baseline/openpower/power-results.jsonl"):
    _install(ArtifactProtocolAdversarialTests, f"bundle_symlink_{_relative.replace('/', '_').replace('.', '_')}",
             lambda self, r=_relative: _symlink_member(self, r))


if __name__ == "__main__":
    unittest.main()
