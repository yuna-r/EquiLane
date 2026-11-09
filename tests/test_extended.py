"""Extended backend identities, real portable execution, and failure retention.

Small C worker fixtures test isolation/status plumbing, not Intel semantics.
The portable integration test calls the vendored SIMDe implementation itself.
"""
from __future__ import annotations

import copy
import ctypes
import json
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from equilane.canonical import dump_bytes, sha256_file
from equilane.extended import (_backend, _build_dependencies, _catalog_identity, _execute_case, _generated_source,
    _library_architecture, _load, _stream, _verified_input, _verified_result,
    compare_extended_artifacts, generate_extended_artifact, run_extended_native)

CATALOG = ROOT / "contracts/simde-pure-values.json"


class ExtendedArtifactTests(unittest.TestCase):
    def test_generation_is_deterministic_unique_and_records_zero_argument_scope(self):
        names = ["_mm_abs_epi8", "_mm_addsub_pd", "_mm256_setzero_ps"]
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            first = generate_extended_artifact(CATALOG, root / "one", names=names, count_per_case=32)
            second = generate_extended_artifact(CATALOG, root / "two", names=names, count_per_case=32)
            a, b = _load(first["manifest"]), _load(second["manifest"])
            self.assertEqual(a, b)
            rows = list(_stream(root / "one/inputs.jsonl"))
            self.assertEqual(len(rows), len({row["input_id"] for row in rows}))
            self.assertEqual(a["input_counts"]["_mm256_setzero_ps"], 1)
            self.assertEqual(a["input_counts"]["_mm_abs_epi8"], 32)
            self.assertEqual(first["record_count"], 65)
            self.assertEqual(_verified_input(first["manifest"])[1], a)

    def test_larger_input_request_preserves_prefix_and_float_special_patterns(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for count in (8, 64):
                generate_extended_artifact(CATALOG, root / str(count), names=["_mm_addsub_pd"], count_per_case=count)
            short = list(_stream(root / "8/inputs.jsonl"))
            long = list(_stream(root / "64/inputs.jsonl"))
            self.assertEqual(short, long[:8])
            self.assertEqual(len(long), 64)
            self.assertTrue(any("000000000000f07f" in row["input"] for row in long), "positive infinity must be exercised")
            self.assertTrue(any("0000000000000080" in row["input"] for row in long), "negative zero must be exercised")

    def test_invalid_generation_requests_never_publish_completion(self):
        for options in ({"count_per_case": 0}, {"count_per_case": True}, {"seed": "bad"},
                        {"names": []}, {"names": ["_mm_abs_epi8", "_mm_abs_epi8"]},
                        {"names": ["unknown"]}):
            with self.subTest(options=options), tempfile.TemporaryDirectory() as temporary:
                output = Path(temporary) / "bad"
                with self.assertRaises(ValueError):
                    generate_extended_artifact(CATALOG, output, **options)
                self.assertFalse((output / "inputs.manifest.json").exists())

    def test_changed_input_stream_or_catalog_is_rejected(self):
        for name in ("inputs.jsonl", "catalog.json"):
            with self.subTest(file=name), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                result = generate_extended_artifact(CATALOG, root / "input", names=["_mm_abs_epi8"], count_per_case=4)
                path = root / "input" / name
                path.write_bytes(path.read_bytes() + b" ")
                with self.assertRaises(ValueError):
                    _verified_input(result["manifest"])

    def _comparison_fixture(self, root):
        paths = [root / "intel.json", root / "target.json"]
        for path in paths:
            path.write_bytes(b"{}\n")
        common = {"implementation_backend": "simde-pure-value", "input_manifest_sha256": "a" * 64, "input_sha256": "b" * 64,
                  "source_identity": {"catalog_sha256": "c" * 64}, "names": ["a", "b", "c"]}
        left = [{"name": name, "input_id": str(index) * 64, "input": "0x00", "rounding": "nearest_even",
                 "status": "ok", "output": "0x01", "fp_flags": 0} for index, name in enumerate(("a", "b", "c"), 1)]
        right = copy.deepcopy(left)
        right[1]["output"] = "0xff"
        right[2].pop("output")
        right[2]["status"] = "unsupported_cpu"
        manifests = [dict(common, runner="intel", executed_candidates=3), dict(common, runner="arm", executed_candidates=2)]
        return paths, manifests, left, right

    def test_comparison_retains_all_differences_and_never_matches_unsupported(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            paths, manifests, left, right = self._comparison_fixture(root)
            with mock.patch("equilane.extended._verified_result", side_effect=[
                    (paths[0], manifests[0], left), (paths[1], manifests[1], right)]):
                result = compare_extended_artifacts(*paths, root / "compare")
            self.assertEqual((result["matched_inputs"], result["mismatched_inputs"], result["not_comparable_inputs"]), (1, 1, 1))
            self.assertEqual(result["status"], "not_comparable")
            self.assertEqual(result["exit_code"], 2)
            differences = list(_stream(root / "compare/differences.jsonl"))
            self.assertEqual(len(differences), 2)
            self.assertEqual(differences[1]["target"]["status"], "unsupported_cpu")
            self.assertNotIn("output", differences[1]["target"])
            self.assertEqual(differences[0]["input"]["input"], "0x00")
            summary = _load(result["summary"])
            self.assertEqual(summary["differences"]["sha256"], sha256_file(root / "compare/differences.jsonl"))
            self.assertTrue((root / "compare/showcase.en.html").is_file())

    def test_comparison_rejects_backend_role_identity_and_input_order_changes(self):
        for defect in ("reference_role", "input_sha", "order"):
            with self.subTest(defect=defect), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                paths, manifests, left, right = self._comparison_fixture(root)
                if defect == "reference_role":
                    manifests[0]["runner"] = "arm"
                elif defect == "input_sha":
                    manifests[1]["input_sha256"] = "d" * 64
                else:
                    right.reverse()
                with mock.patch("equilane.extended._verified_result", side_effect=[
                        (paths[0], manifests[0], left), (paths[1], manifests[1], right)]), self.assertRaises(ValueError):
                    compare_extended_artifacts(*paths, root / "compare")
                self.assertFalse((root / "compare/summary.json").exists())

    def test_backend_dispatch_is_closed_and_preserves_reference_role(self):
        identity = {"catalog_sha256": "a" * 64}
        module = SimpleNamespace(validate_catalog=mock.Mock(return_value=identity),
                                 generate_source=mock.Mock(return_value="actual source"))
        catalog = {"artifact_type": "equilane.softfloat-fp16-candidates"}
        with mock.patch("equilane.extended.import_module", return_value=module) as imported:
            self.assertEqual(_catalog_identity(catalog), identity)
            self.assertEqual(_generated_source(catalog, ["_mm_add_ph"], role="intel"), "actual source")
        module.generate_source.assert_called_once_with(catalog, ["_mm_add_ph"], role="intel")
        self.assertEqual(imported.call_args[0], ("equilane.half_codegen",))
        with self.assertRaises(ValueError):
            _backend({"artifact_type": "arbitrary.python.module"})

    def test_softfloat_dependency_builds_once_and_keeps_relative_runtime_link(self):
        catalog = {"artifact_type": "equilane.softfloat-fp16-candidates"}
        with tempfile.TemporaryDirectory() as temporary:
            build = Path(temporary)
            dependency = build / "softfloat"
            dependency.mkdir()
            library = dependency / "libequilane-softfloat.so"
            library.write_bytes(b"test dependency bytes")
            (dependency / "build-evidence.json").write_bytes(b"{}\n")
            with mock.patch("equilane.extended.import_module", return_value=SimpleNamespace()), \
                 mock.patch("equilane.softfloat.build_softfloat", return_value=library) as compile_dependency, \
                 mock.patch("equilane.extended._library_architecture"):
                flags, dependencies = _build_dependencies(catalog, build, "arm", "clang")
                self.assertEqual(_build_dependencies(catalog, build, "intel", "clang"), ([], []))
            compile_dependency.assert_called_once_with(build / "softfloat", compiler="clang")
            self.assertIn("-DTHREAD_LOCAL=_Thread_local", flags)
            self.assertIn("-DSOFTFLOAT_FAST_INT64", flags)
            self.assertIn("-Wl,-rpath,$ORIGIN/softfloat", flags)
            self.assertEqual(dependencies[0]["file"], "softfloat/libequilane-softfloat.so")
            self.assertEqual(dependencies[0]["sha256"], sha256_file(library))

    def test_native_role_guard_does_not_silently_fall_back_to_a_model(self):
        with mock.patch("equilane.extended.platform.system", return_value="Darwin"), self.assertRaises(ValueError):
            run_extended_native("missing", "unused", role="intel")
        with mock.patch("equilane.extended.platform.system", return_value="Linux"), \
             mock.patch("equilane.extended.platform.machine", return_value="aarch64"), self.assertRaises(ValueError):
            run_extended_native("missing", "unused", role="intel")


@unittest.skipUnless(sys.platform.startswith("linux") and shutil.which("clang"), "real worker tests require Linux and Clang")
class ExtendedPortableExecutionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temporary.cleanup)
        cls.root = Path(cls.temporary.name)
        machine = platform.machine().lower()
        cls.role = next((role for role, machines in {"intel": {"x86_64", "amd64"}, "arm": {"aarch64", "arm64"},
            "openpower": {"ppc64le", "ppc64el"}, "riscv": {"riscv64"}}.items() if machine in machines), None)
        if cls.role is None:
            raise unittest.SkipTest("architecture outside supported extended roles")
        cls.inputs = generate_extended_artifact(CATALOG, cls.root / "input", names=["_mm_abs_epi8", "_mm_addsub_pd"], count_per_case=8)
        cls.result = run_extended_native(cls.inputs["manifest"], cls.root / "native", role=cls.role)

    def test_real_compiled_backend_produces_complete_traced_records(self):
        self.assertEqual(self.result["status"], "pass")
        self.assertEqual(self.result["record_count"], 16)
        self.assertEqual(self.result["compiled_candidates"], 2)
        self.assertEqual(self.result["executed_candidates"], 2)
        path, manifest, rows = _verified_result(self.result["manifest"])
        self.assertEqual(len(rows), 16)
        self.assertTrue(all(row["status"] == "ok" and "output" in row and "fp_flags" in row for row in rows))
        for row in rows:
            if row["name"] == "_mm_abs_epi8":
                source = bytes.fromhex(row["input"][2:])
                expected = bytes(abs(value if value < 128 else value - 256) % 256 for value in source)
                self.assertEqual(row["output"], "0x" + expected.hex())
                self.assertEqual(row["fp_flags"], 0)
        self.assertEqual(manifest["backend"], "intel-intrinsics" if self.role == "intel" else "simde-pure-value")
        self.assertFalse(manifest["physical_hardware_evidence"])

    def test_library_machine_cannot_be_relabeled_as_another_cpu(self):
        library = next((self.root / "native/build").glob("*.so"))
        wrong_role = "arm" if self.role != "arm" else "intel"
        with self.assertRaises(ValueError):
            _library_architecture(library, wrong_role)

    def test_changed_library_is_not_accepted_as_prior_native_evidence(self):
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "copy"
            shutil.copytree(self.root / "native", target)
            library = next((target / "build").glob("*.so"))
            library.write_bytes(library.read_bytes() + b"tampered")
            with self.assertRaises(ValueError):
                _verified_result(target)

    def test_invalid_build_status_cannot_bypass_library_verification(self):
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "copy"
            shutil.copytree(self.root / "native", target)
            evidence_path = target / "build-evidence.json"
            evidence = _load(evidence_path)
            evidence["units"][0]["status"] = "unknown"
            evidence_path.write_bytes(dump_bytes(evidence, newline=True))
            manifest_path = target / "results.manifest.json"
            manifest = _load(manifest_path)
            manifest["build_evidence"]["sha256"] = sha256_file(evidence_path)
            manifest_path.write_bytes(dump_bytes(manifest, newline=True))
            with self.assertRaises(ValueError):
                _verified_result(target)

    def _worker_fixture(self, body):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        code = '#include <stdint.h>\n#include <signal.h>\nint equilane_ext_call_with_flags(uint32_t op,const uint8_t*in,uint64_t n,uint8_t*out,uint64_t m,uint32_t*f){' + body + '}\n'
        source, library = root / "test.c", root / "test.so"
        source.write_text(code)
        subprocess.run(["clang", "-shared", "-fPIC", str(source), "-o", str(library)], check=True, capture_output=True)
        evidence = {"status": "compiled", "library": library.name, "library_sha256": sha256_file(library)}
        record = {"ordinal": 0, "output_byte_length": 1}
        rows = [{"input_id": str(index) * 64, "input": "0x00"} for index in (1, 2)]
        return root, evidence, record, rows

    def test_unsupported_worker_status_preserves_absence_of_output(self):
        root, evidence, record, rows = self._worker_fixture("return 3;")
        actual = _execute_case(record, rows, evidence, root, 1.0)
        self.assertEqual([row["status"] for row in actual], ["unsupported_cpu", "unsupported_cpu"])
        self.assertTrue(all("output" not in row for row in actual))

    def test_crashing_c_code_isolated_from_test_process(self):
        root, evidence, record, rows = self._worker_fixture("raise(SIGSEGV);return 0;")
        actual = _execute_case(record, rows, evidence, root, 1.0)
        self.assertEqual([row["status"] for row in actual], ["signal", "signal"])
        self.assertTrue(all("output" not in row for row in actual))

    def test_hanging_c_code_isolated_and_reported_as_timeout(self):
        root, evidence, record, rows = self._worker_fixture("for(;;){}return 0;")
        actual = _execute_case(record, rows, evidence, root, 0.05)
        self.assertEqual([row["status"] for row in actual], ["timeout", "timeout"])


if __name__ == "__main__":
    unittest.main()
