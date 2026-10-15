"""Coverage is an explicit option, independent tests remain enabled by default."""

from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
import io
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "src"))

from equilane.canonical import read_canonical_json
from equilane.cli import main
from equilane.console import print_quality_metrics
from equilane.quality import QualityMetrics, QualityRun, run_quality_gates


class CoverageOptionTests(unittest.TestCase):
    def test_cli_default_quality_runs_without_coverage_and_explicit_option_is_forwarded(self):
        for coverage in (False, True):
            with self.subTest(coverage=coverage), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                suite = root / "cases"
                suite.mkdir()
                shutil.copyfile(PROJECT / "10_official_suite/cases/add-i64x2.py", suite / "add.py")
                shutil.copyfile(PROJECT / "contracts/isa-registry.json", root / "isa.json")
                project = root / "equilane.toml"
                project.write_text(
                    '[equilane]\nschema_version = 1\nsuite = "cases"\nisa_registry = "isa.json"\n',
                    encoding="utf-8",
                )
                output = root / "check"
                args = ["check", "--project", str(project), "--output", str(output),
                        "--quality", "--count-per-case", "1", "--quiet"]
                if coverage:
                    args.append("--coverage")
                result = QualityRun("pass", output / "quality/summary.json", 3, 3,
                                    coverage_percent="87.5" if coverage else None, tests_run=1)
                with mock.patch("equilane.cli.run_quality_gates", return_value=result) as gates:
                    with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                        exit_code = main(args)
                self.assertEqual(exit_code, 0)
                gates.assert_called_once()
                self.assertEqual(gates.call_args.args[0], root.resolve())
                self.assertIs(gates.call_args.kwargs["coverage"], coverage)
                summary = read_canonical_json(output / "comparison/summary.json")
                self.assertEqual(summary["matched_inputs"], 1)

    def test_coverage_without_quality_is_rejected_before_loading_a_project_or_writing(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "must-not-exist"
            stderr = io.StringIO()
            with mock.patch("equilane.cli.load_project") as project, \
                    mock.patch("equilane.cli.run_quality_gates") as gates:
                with redirect_stdout(io.StringIO()), redirect_stderr(stderr):
                    exit_code = main(["check", "--coverage", "--output", str(output)])
            self.assertEqual(exit_code, 2)
            self.assertIn("--coverage requires --quality", stderr.getvalue())
            project.assert_not_called()
            gates.assert_not_called()
            self.assertFalse(output.exists())

    def test_quality_child_collects_coverage_only_with_explicit_opt_in(self):
        for coverage in (False, True):
            with self.subTest(coverage=coverage), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary) / "project"
                script = root / "10_official_suite/cross-compile.sh"
                script.parent.mkdir(parents=True)
                script.write_text("#!/bin/sh\n", encoding="utf-8")
                output = Path(temporary) / "quality"
                commands = []
                progress = []
                child_summary = {
                    "status": "pass", "tests_run": 7,
                    "model_oracle_checks_run": 2345,
                    "model_output_mismatches": [],
                    "coverage_collected": coverage,
                    "coverage_percent": "87.5" if coverage else None,
                }

                def execute(command, *, root, log, **kwargs):
                    commands.append(command)
                    stdout = json.dumps(child_summary) if "equilane.quality_runner" in command else "pass\n"
                    log.write_text(stdout, encoding="utf-8")
                    return subprocess.CompletedProcess(command, 0, stdout, "")

                with mock.patch("equilane.quality._execute", side_effect=execute):
                    options = {"coverage": True} if coverage else {}
                    result = run_quality_gates(root, output, progress=progress.append, **options)
                python_command = commands[0]
                self.assertIn("equilane.quality_runner", python_command)
                self.assertEqual("--coverage" in python_command, coverage)
                self.assertEqual(result.tests_run, 7)
                self.assertEqual(result.coverage_percent, "87.5" if coverage else None)
                self.assertEqual((result.passed_gates, result.total_gates), (3, 3))
                self.assertEqual(len(commands), 5)  # Python, C configure/build/test, cross build.
                labels = {update.label for update in progress if update.gate == 1}
                self.assertEqual(labels, {"Python tests + coverage" if coverage else "Python tests"})
                report = read_canonical_json(result.report_path)
                python_gate = report["gates"]["python_coverage"]
                self.assertIs(python_gate["coverage_collected"], coverage)
                self.assertEqual(python_gate["model_oracle_checks_run"], 2345)

    def test_console_reports_uncollected_coverage_without_a_fake_percentage(self):
        output = io.StringIO()
        print_quality_metrics(
            QualityMetrics(1, 1, 1, 1, 2),
            deep=QualityRun("pass", Path("quality/summary.json"), 3, 3, tests_run=7),
            stream=output,
        )
        text = output.getvalue()
        self.assertRegex(text, r"source line coverage\s+not collected")
        self.assertRegex(text, r"regression tests\s+7 executed")
        self.assertNotIn("None%", text)
        self.assertNotIn("0%", text)


if __name__ == "__main__":
    unittest.main()
