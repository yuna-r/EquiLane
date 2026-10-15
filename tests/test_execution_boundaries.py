"""Model loading, fixture failures and out-of-order worker completion."""

from __future__ import annotations

import copy
from concurrent.futures import Future
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "src"))

from equilane.canonical import dump_bytes, sha256_bytes
from equilane.cases import CaseRegistry, load_case_definitions, validate_case_definition
from equilane.development import load_development_case
from equilane.errors import UnsupportedError, ValidationError
from equilane.fixture import _result_batch, _result_records, run_fixture
from equilane.generator import generate_artifact
from equilane.isa import load_isa_registry


class ModelLoaderBoundaryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.isa = load_isa_registry(PROJECT / "contracts/isa-registry.json")
        cls.original = load_case_definitions(PROJECT / "10_official_suite/cases/add-i32x4.py",
                                            isa_registry=cls.isa).get("sse2.add.i32x4.default")

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name) / "pack.py"
        data = copy.deepcopy(self.original.data)
        data["id"] = "sse2.loader.i32x4." + Path(self.temporary.name).name.replace("_", "-")
        self.case = validate_case_definition(data, isa_registry=self.isa, source_path=self.path)
        self.addCleanup(self.clear_model_cache)

    def clear_model_cache(self):
        import equilane.development as development
        development._CACHE.pop(self.path.resolve(), None)
        development._LOADED_BY_ID.pop(self.case.id, None)
        for name, module in list(sys.modules.items()):
            if name.startswith("_equilane_casepack_") and getattr(module, "__file__", None) == str(self.path.resolve()):
                sys.modules.pop(name, None)

    def write_model(self, *, field=None, expression=None):
        exports = {"CASE_ID": repr(self.case.id), "candidates": "lambda *args, **kwargs: iter(())",
                   "execute": "lambda record: {'return': record['operands']['a']}",
                   "MINIMUM_COUNTS": "{'standard': 2}"}
        if field is not None:
            if expression is None:
                exports.pop(field)
            else:
                exports[field] = expression
        self.path.write_text("\n".join(f"{name} = {value}" for name, value in exports.items()), encoding="utf-8")

    def check_invalid_export(self, field, expression):
        self.write_model(field=field, expression=expression)
        with self.assertRaises(ValidationError):
            load_development_case(self.case)

    def test_valid_exports_are_cached_without_rerunning_the_module(self):
        self.write_model()
        first = load_development_case(self.case)
        self.path.write_text("raise RuntimeError('module executed twice')", encoding="utf-8")
        self.assertIs(load_development_case(self.case), first)
        self.assertEqual(first.minimum_counts, {"standard": 2})

    def test_same_cached_path_cannot_be_rebound_to_another_case(self):
        self.write_model()
        load_development_case(self.case)
        other = copy.deepcopy(self.case.data)
        other["id"] = "sse2.loader.i32x4.other"
        with self.assertRaisesRegex(ValidationError, "expected"):
            load_development_case(validate_case_definition(other, source_path=self.path))

    def test_missing_source_has_no_model_without_a_cached_identity(self):
        with self.assertRaises(UnsupportedError):
            load_development_case(self.case)

    def test_sourceless_snapshot_reuses_only_its_loaded_model(self):
        self.write_model()
        loaded = load_development_case(self.case)
        snapshot = validate_case_definition(copy.deepcopy(self.case.data))
        self.assertIs(load_development_case(snapshot), loaded)

    def test_model_and_legacy_exports_in_one_directory_are_ambiguous(self):
        (self.path.parent / "model.py").write_text("", encoding="utf-8")
        (self.path.parent / "development.py").write_text("", encoding="utf-8")
        case = validate_case_definition(copy.deepcopy(self.case.data), source_path=self.path.with_suffix(".yaml"))
        with self.assertRaisesRegex(ValidationError, "both"):
            load_development_case(case)

    def check_module_failure(self, code):
        self.path.write_text(code, encoding="utf-8")
        with self.assertRaises(ValidationError):
            load_development_case(self.case)
        modules = [module for name, module in sys.modules.items() if name.startswith("_equilane_casepack_")]
        self.assertFalse(any(getattr(module, "__file__", None) == str(self.path.resolve()) for module in modules))
        # A repaired model must be loadable; a broken import cannot poison the cache.
        self.write_model()
        self.assertEqual(load_development_case(self.case).id, self.case.id)


class FixtureExecutionBoundaryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temporary.cleanup)
        cls.root = Path(cls.temporary.name)
        cls.isa = load_isa_registry(PROJECT / "contracts/isa-registry.json")
        first = load_case_definitions(PROJECT / "10_official_suite/cases/add-i32x4.py", isa_registry=cls.isa)
        second = load_case_definitions(PROJECT / "10_official_suite/cases/sub-i32x4.py", isa_registry=cls.isa)
        selected = {case.id: case for case in (*first, *second)}
        cls.cases = CaseRegistry(selected, sha256_bytes(dump_bytes(
            [selected[key].data for key in sorted(selected)], newline=True)))
        cls.inputs = generate_artifact(cases=cls.cases, isa_registry=cls.isa,
                                       output=cls.root / "vectors", count_per_case=3, retain_records=True)

    def check_invalid_jobs(self, value):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "result"
            with self.assertRaises(ValidationError):
                run_fixture(input_artifact=self.inputs, cases=self.cases, isa_registry=self.isa,
                            role="intel", output=output, jobs=value)
            self.assertFalse(output.exists(), "invalid options must not create an artifact")
            from equilane.targets import TARGETS
            output.mkdir()
            marker = output / TARGETS["intel"].manifest_file
            marker.write_bytes(b"previous complete artifact")
            with self.assertRaises(ValidationError):
                run_fixture(input_artifact=self.inputs, cases=self.cases, isa_registry=self.isa,
                            role="intel", output=output, jobs=value)
            self.assertEqual(marker.read_bytes(), b"previous complete artifact")

    def check_invalid_role(self, value):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "result"
            with self.assertRaises(ValidationError):
                run_fixture(input_artifact=self.inputs, cases=self.cases, isa_registry=self.isa,
                            role=value, output=output)
            self.assertFalse(output.exists())

    def test_empty_input_is_rejected_before_creating_output(self):
        empty = SimpleNamespace(records=(), manifest={})
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "result"
            with self.assertRaises(ValidationError):
                run_fixture(input_artifact=empty, cases=self.cases, isa_registry=self.isa,
                            role="intel", output=output)
            self.assertFalse(output.exists())

    def test_out_of_order_workers_preserve_the_input_stream_and_progress(self):
        submitted = []
        class Executor:
            def __init__(self, *, max_workers):
                self.max_workers = max_workers
            def __enter__(self):
                return self
            def __exit__(self, *args):
                pass
            def submit(self, function, work):
                future = Future()
                future.set_result(function(work))
                submitted.append(future)
                return future
        updates = []
        with mock.patch("equilane.fixture.ProcessPoolExecutor", Executor), \
             mock.patch("equilane.fixture.as_completed", side_effect=lambda futures: reversed(list(futures))):
            actual = _result_records(self.inputs, cases=self.cases, role="arm", jobs=8, progress=updates.append)
        expected = _result_records(self.inputs, cases=self.cases, role="arm")
        self.assertEqual(actual, expected)
        self.assertEqual(len(submitted), 2)
        self.assertEqual(updates, [3, 6])
        self.assertEqual([item["input_id"] for item in actual], [item["input_id"] for item in self.inputs.records])

    def test_execution_failure_invalidates_an_older_completion_marker(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)
            # Use the actual configured artifact filename.
            from equilane.targets import TARGETS
            manifest = output / TARGETS["intel"].manifest_file
            manifest.write_text("old complete manifest", encoding="utf-8")
            with mock.patch("equilane.fixture._result_records", side_effect=RuntimeError("worker died")):
                with self.assertRaisesRegex(RuntimeError, "worker died"):
                    run_fixture(input_artifact=self.inputs, cases=self.cases, isa_registry=self.isa,
                                role="intel", output=output)
            self.assertFalse(manifest.exists())

    def test_model_execution_does_not_mutate_input_or_share_observed_results(self):
        record = copy.deepcopy(self.inputs.records[0])
        before = copy.deepcopy(record)
        work = (self.cases.get(str(record["case_id"])), "intel", (record,))
        first = _result_batch(work)[0]
        self.assertEqual(record, before)
        first["observed"]["return"]["lanes"][0] = "0xdeadbeef"
        second = _result_batch(work)[0]
        self.assertEqual(record, before)
        self.assertNotEqual(first["observed"], second["observed"])


for _field, _values in {
    "CASE_ID": (None, "None", "True", "17", "'sse2.wrong.i32x4.default'"),
    "candidates": (None, "None", "[]", "'not callable'"),
    "execute": (None, "None", "{}", "1"),
    "MINIMUM_COUNTS": ("None", "[]", "{'standard': True}", "{'standard': 0}",
                       "{'standard': -1}", "{'standard': 1.5}", "{'standard': '2'}", "{1: 2}"),
}.items():
    for _index, _expression in enumerate(_values):
        def _test(self, field=_field, expression=_expression):
            self.check_invalid_export(field, expression)
        setattr(ModelLoaderBoundaryTests, f"test_invalid_{_field}_{_index}", _test)
for _label, _code in {"syntax": "def broken(:", "runtime": "raise RuntimeError('broken')",
                      "import": "import nonexistent_equilane_model_module"}.items():
    def _test(self, code=_code):
        self.check_module_failure(code)
    setattr(ModelLoaderBoundaryTests, "test_failed_module_" + _label + "_can_be_repaired", _test)
for _index, _jobs in enumerate((0, -1, True, False, 1.5, "2", None)):
    def _test(self, jobs=_jobs):
        self.check_invalid_jobs(jobs)
    setattr(FixtureExecutionBoundaryTests, f"test_invalid_jobs_{_index}", _test)
for _index, _role in enumerate((None, [], {}, True, "Intel", "unknown")):
    def _test(self, role=_role):
        self.check_invalid_role(role)
    setattr(FixtureExecutionBoundaryTests, f"test_invalid_role_{_index}", _test)


if __name__ == "__main__":
    unittest.main()
