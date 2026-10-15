"""Quality evidence must survive failures in tests and external tools."""

from __future__ import annotations

from contextlib import redirect_stdout
import io
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "src"))

from equilane.canonical import dumps, read_canonical_json
from equilane.quality import _execute, run_quality_gates
from equilane.quality_runner import _ProgressRunner, run


class QualityFailureBoundaryTests(unittest.TestCase):
    def run_nested(self, sample):
        output = io.StringIO()
        with redirect_stdout(output):
            result = _ProgressRunner(stream=io.StringIO(), total=1).run(
                unittest.defaultTestLoader.loadTestsFromTestCase(sample))
        return result, output.getvalue()

    def check_lifecycle_failure(self, phase, assertion):
        class Sample(unittest.TestCase):
            def test_body(self):
                pass

        def fail(*args):
            raise (AssertionError if assertion else RuntimeError)(f"{phase} broke")

        setattr(Sample, phase, classmethod(fail) if phase.endswith("Class") else fail)
        result, output = self.run_nested(Sample)
        self.assertFalse(result.wasSuccessful())
        findings = (result.test_assertion_failures if assertion and not phase.endswith("Class")
                    else result.test_execution_errors)
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0]["message"], f"{phase} broke")
        self.assertEqual(len(result.model_output_mismatches), 0)
        self.assertEqual(result.testsRun, 0 if phase == "setUpClass" else 1)
        if phase != "setUpClass":
            self.assertIn("IOITF_PROGRESS 1 1", output)

    def test_cleanup_failure_retains_execution_error(self):
        class Sample(unittest.TestCase):
            def test_body(self):
                def broken():
                    raise RuntimeError("cleanup broke")
                self.addCleanup(broken)
        result, _ = self.run_nested(Sample)
        self.assertEqual(len(result.test_execution_errors), 1)
        self.assertEqual(result.test_execution_errors[0]["message"], "cleanup broke")

    def test_skipped_subtest_is_not_a_model_mismatch(self):
        class Sample(unittest.TestCase):
            def test_body(self):
                with self.subTest(lane=0):
                    self.skipTest("unavailable")
                with self.subTest(lane=1):
                    self.assertEqual(2, 2)
        result, _ = self.run_nested(Sample)
        self.assertTrue(result.wasSuccessful())
        self.assertEqual(len(result.skipped), 1)
        self.assertEqual(result.model_output_mismatches, [])
        self.assertEqual(result.test_execution_errors, [])

    def test_failed_subtests_do_not_stop_later_oracle_checks_or_progress(self):
        visited = []
        class Sample(unittest.TestCase):
            def test_body(self):
                for index in range(4):
                    with self.subTest(index=index):
                        visited.append(index)
                        self.assertEqual(index % 2, 0)
        result, output = self.run_nested(Sample)
        self.assertEqual(visited, [0, 1, 2, 3])
        self.assertEqual(len(result.test_assertion_failures), 2)
        self.assertEqual(result.testsRun, 1)
        self.assertEqual(output.count("IOITF_PROGRESS 1 1"), 1)

    def check_discovery(self, code, expected_status, tests_run):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "src/equilane").mkdir(parents=True)
            tests = root / "tests"
            tests.mkdir()
            if code is not None:
                # Distinct names prevent unittest import cache collisions across roots.
                name = "test_quality_" + root.name.replace("-", "_")
                (tests / (name + ".py")).write_text(code, encoding="utf-8")
            with redirect_stdout(io.StringIO()):
                exit_code, summary = run(root, root / "evidence")
            self.assertEqual(summary["status"], expected_status)
            self.assertEqual(exit_code, 0 if expected_status == "pass" else 1)
            self.assertEqual(summary["tests_run"], tests_run)
            self.assertEqual(read_canonical_json(root / "evidence/summary.json"), summary)
            self.assertTrue((root / "evidence/tests.log").is_file())
            return summary

    def test_empty_discovery_cannot_report_a_passing_quality_gate(self):
        summary = self.check_discovery(None, "fail", 0)
        self.assertEqual(summary["discovery_error"], "no tests were discovered")

    def test_syntax_error_is_recorded_in_the_summary(self):
        summary = self.check_discovery("def broken(:\n", "fail", 1)
        self.assertEqual(summary["errors"], 1)
        self.assertEqual(summary["failure_classification"]["test_execution_errors"], 1)

    def test_import_failure_is_recorded_in_the_summary(self):
        summary = self.check_discovery("raise ImportError('missing dependency')\n", "fail", 1)
        self.assertEqual(summary["errors"], 1)
        self.assertIn("missing dependency", summary["test_execution_errors"][0]["message"])

    def test_class_setup_error_does_not_abort_evidence_publication(self):
        summary = self.check_discovery(
            "import unittest\nclass Sample(unittest.TestCase):\n"
            "    @classmethod\n    def setUpClass(cls):\n        raise RuntimeError('setup failed')\n"
            "    def test_body(self):\n        pass\n", "fail", 0)
        self.assertEqual(summary["errors"], 1)
        self.assertEqual(summary["test_execution_errors"][0]["message"], "setup failed")

    def test_class_teardown_error_does_not_abort_evidence_publication(self):
        summary = self.check_discovery(
            "import unittest\nclass Sample(unittest.TestCase):\n"
            "    @classmethod\n    def tearDownClass(cls):\n        raise RuntimeError('teardown failed')\n"
            "    def test_body(self):\n        pass\n", "fail", 1)
        self.assertEqual(summary["errors"], 1)

    def test_skipped_class_is_not_misreported_as_empty_discovery(self):
        summary = self.check_discovery(
            "import unittest\nclass Sample(unittest.TestCase):\n"
            "    @classmethod\n    def setUpClass(cls):\n        raise unittest.SkipTest('not available')\n"
            "    def test_body(self):\n        pass\n", "fail", 0)
        self.assertEqual(summary["skipped"], 1)
        self.assertNotIn("discovery_error", summary)

    def test_missing_project_directories_are_rejected_before_output_creation(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with self.assertRaisesRegex(RuntimeError, "requires"):
                run(root, root / "output")
            self.assertFalse((root / "output").exists())

    def check_tool_failure(self, fail_at):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            script = root / "10_official_suite/cross-compile.sh"
            script.parent.mkdir()
            script.write_text("#!/bin/sh\n", encoding="utf-8")
            calls = []
            def execute(command, *, log, progress=None, **kwargs):
                gate = ("python" if command[0] == sys.executable else
                        "cross" if command[0] == str(script) else
                        "configure" if "-S" in command else
                        "build" if "--build" in command else "ctest")
                calls.append(gate)
                output = dumps({"tests_run": 7, "status": "pass"}) if gate == "python" else gate + " output"
                log.write_text(output, encoding="utf-8")
                if progress:
                    progress(1, 7)
                return subprocess.CompletedProcess(command, 19 if gate == fail_at else 0, output, "")
            updates = []
            with mock.patch("equilane.quality._execute", side_effect=execute):
                result = run_quality_gates(root, root / "output", progress=updates.append)
            summary = read_canonical_json(result.report_path)
            self.assertEqual(result.status, "fail")
            self.assertEqual(result.passed_gates, 2)
            self.assertEqual(result.total_gates, 3)
            gate_name = "python_coverage" if fail_at == "python" else "cross_build" if fail_at == "cross" else "c_sanitizers"
            self.assertEqual(summary["gates"][gate_name]["status"], "fail")
            self.assertIn("cross", calls, "independent gates must still run after a failed gate")
            if fail_at == "configure":
                self.assertNotIn("build", calls)
                self.assertNotIn("ctest", calls)
            if fail_at == "build":
                self.assertNotIn("ctest", calls)
            self.assertEqual(updates[-1].gate, 3)
            self.assertEqual(updates[-1].state, "failed" if fail_at == "cross" else "pass")
            self.assertEqual(list((root / "output").glob(".sanitizer-*.log")), [])

    def test_missing_cross_compiler_script_fails_only_that_gate(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            def execute(command, *, log, **kwargs):
                output = '{"tests_run":1}'
                log.write_text(output)
                return subprocess.CompletedProcess(command, 0, output, "")
            with mock.patch("equilane.quality._execute", side_effect=execute):
                result = run_quality_gates(root, root / "output")
            self.assertEqual((result.passed_gates, result.total_gates), (2, 3))
            self.assertIn("not found", (root / "output/cross-build.log").read_text())

    def test_subprocess_launch_failure_is_logged_as_nonzero(self):
        for progress in (None, lambda current, total: None):
            with self.subTest(streaming=progress is not None), tempfile.TemporaryDirectory() as temporary:
                log = Path(temporary) / "command.log"
                with mock.patch("equilane.quality.subprocess.run", side_effect=OSError("tool missing")), \
                     mock.patch("equilane.quality.subprocess.Popen", side_effect=OSError("tool missing")):
                    completed = _execute(["missing-tool"], root=PROJECT, log=log, progress=progress)
                self.assertEqual(completed.returncode, 127)
                self.assertIn("tool missing", log.read_text())

    def test_streaming_progress_ignores_invalid_markers_and_preserves_output(self):
        payload = ("IOITF_PROGRESS -1 8\nIOITF_PROGRESS 9 8\nIOITF_PROGRESS 0 0\n"
                   "IOITF_PROGRESS bad 8\nIOITF_PROGRESS 1 8 extra\n"
                   "ordinary output\nIOITF_PROGRESS 0 8\nIOITF_PROGRESS 8 8\n")
        process = mock.Mock(stdout=io.StringIO(payload))
        process.wait.return_value = 13
        updates = []
        with tempfile.TemporaryDirectory() as temporary:
            log = Path(temporary) / "command.log"
            with mock.patch("equilane.quality.subprocess.Popen", return_value=process):
                completed = _execute(["tool"], root=PROJECT, log=log,
                                     progress=lambda current, total: updates.append((current, total)))
            self.assertEqual(updates, [(0, 8), (8, 8)])
            self.assertEqual(completed.stdout, payload)
            self.assertEqual(completed.returncode, 13)
            self.assertIn(payload, log.read_text())
            self.assertTrue(process.stdout.closed)


def _lifecycle_test(phase, assertion):
    def test(self):
        self.check_lifecycle_failure(phase, assertion)
    return test


for _phase in ("setUp", "tearDown", "setUpClass", "tearDownClass"):
    for _assertion in (False, True):
        setattr(QualityFailureBoundaryTests, f"test_{_phase}_{'assertion' if _assertion else 'error'}",
                _lifecycle_test(_phase, _assertion))
for _gate in ("python", "configure", "build", "ctest", "cross"):
    def _test(self, gate=_gate):
        self.check_tool_failure(gate)
    setattr(QualityFailureBoundaryTests, "test_failed_" + _gate + "_gate", _test)


if __name__ == "__main__":
    unittest.main()
