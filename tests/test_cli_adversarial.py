"""CLI regressions exercised through real parsing and the small authoring loop.

Default project discovery must resolve the selected project before the quality
stage, including legacy filenames. These checks reproduce the former
``Path(None)`` regression and remain ordinary assertions.
Only heavyweight quality gates are replaced; generation, both fixtures,
comparison, project discovery, progress, JSON output and HTML rendering run.
"""

from __future__ import annotations

from contextlib import chdir, redirect_stderr, redirect_stdout
import io
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest import mock


PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "src"))

from equilane.canonical import atomic_write, dump_bytes, loads, read_canonical_json
from equilane.cli import main
from equilane.quality import QualityRun


class CLIAdversarialTests(unittest.TestCase):
    def _make_project(self, root, *, legacy):
        suite = root / "tiny-suite"
        suite.mkdir()
        shutil.copyfile(PROJECT / "10_official_suite/cases/add-i64x2.py", suite / "add.py")
        shutil.copyfile(PROJECT / "contracts/isa-registry.json", root / "isa-registry.json")
        name, table = ("ioitf.toml", "ioitf") if legacy else ("equilane.toml", "equilane")
        config = root / name
        config.write_text(
            f'[{table}]\nschema_version = 1\nsuite = "tiny-suite"\n'
            'isa_registry = "isa-registry.json"\n', encoding="utf-8",
        )
        return config

    def _run_check(self, *, legacy=False, explicit=False, quality=True):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            config = self._make_project(root, legacy=legacy)
            output = root / "artifacts"
            args = ["check", "--output", str(output), "--count-per-case", "1", "--showcase-report"]
            if explicit:
                args.extend(["--project", str(config)])
            if quality:
                args.append("--quality")

            def quality_gate(project_root, quality_output, *, jobs, progress, coverage=False):
                self.assertEqual(project_root, root)
                self.assertEqual(jobs, 1)
                self.assertFalse(coverage)
                report = quality_output / "summary.json"
                atomic_write(report, dump_bytes({
                    "status": "pass", "passed_gates": 3, "total_gates": 3,
                    "gates": {name: {"status": "pass"} for name in (
                        "python_coverage", "c_sanitizers", "cross_build",
                    )},
                }, newline=True))
                return QualityRun("pass", report, 3, 3, coverage_percent="100", tests_run=1)

            stdout, stderr = io.StringIO(), io.StringIO()
            with mock.patch("equilane.cli.run_quality_gates", side_effect=quality_gate) as gates:
                with chdir(root), redirect_stdout(stdout), redirect_stderr(stderr):
                    try:
                        exit_code = main(args)
                    except Exception as exc:
                        self.fail(
                            f"check must resolve {'legacy' if legacy else 'canonical'} "
                            f"{'explicit' if explicit else 'default'} project before quality; "
                            f"CLI raised {type(exc).__name__}: {exc}"
                        )
                self.assertEqual(exit_code, 0, stderr.getvalue())
                if quality:
                    gates.assert_called_once()
                else:
                    gates.assert_not_called()

            result = loads(stdout.getvalue().strip())
            self.assertEqual(result["case_count"], 1)
            self.assertEqual(result["record_count"], 1)
            self.assertEqual(result["status"], "pass")
            self.assertFalse(result["native_evidence"])
            self.assertTrue((output / "showcase.html").is_file())
            self.assertEqual(result["showcase_report"], str(output / "showcase.html"))
            comparison = read_canonical_json(output / "comparison/summary.json")
            self.assertEqual(comparison["matched_inputs"], 1)
            if quality:
                self.assertEqual(result["quality_status"], "pass")
                self.assertTrue((output / "quality/summary.json").is_file())
                self.assertIn("[7/8] Run quality gates: done", stderr.getvalue())
                self.assertIn("[8/8] PASS - 1 trials: done", stderr.getvalue())

    def test_default_equilane_project_reaches_quality_and_creates_report(self):
        self._run_check()

    def test_default_legacy_project_reaches_quality_and_creates_report(self):
        self._run_check(legacy=True)

    def test_explicit_project_control_reaches_quality_for_both_project_formats(self):
        for legacy in (False, True):
            with self.subTest(legacy=legacy):
                self._run_check(legacy=legacy, explicit=True)

    def test_default_project_discovery_control_works_without_quality(self):
        for legacy in (False, True):
            with self.subTest(legacy=legacy):
                self._run_check(legacy=legacy, quality=False)


if __name__ == "__main__":
    unittest.main()
