from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
import copy
import io
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "src"))

from equilane.canonical import atomic_write, dump_bytes, iter_canonical_jsonl, loads, read_canonical_json, write_jsonl
from equilane.cli import _parser, main
from equilane.fixture import run_fixture
from equilane.targets import TARGETS


class TargetCLITests(unittest.TestCase):
    def _project(self, root):
        suite = root / "cases"
        suite.mkdir()
        shutil.copyfile(PROJECT / "10_official_suite/cases/add-i64x2.py", suite / "add.py")
        shutil.copyfile(PROJECT / "contracts/isa-registry.json", root / "isa.json")
        project = root / "equilane.toml"
        project.write_text('[equilane]\nschema_version = 1\nsuite = "cases"\nisa_registry = "isa.json"\n')
        return project

    def _main(self, args):
        stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            code = main(args)
        return code, loads(stdout.getvalue().strip()) if stdout.getvalue().strip() else None, stderr.getvalue()

    def _perturb_result(self, manifest_path):
        manifest = read_canonical_json(manifest_path)
        results_path = manifest_path.parent / manifest["results"]["file"]
        records = copy.deepcopy(list(iter_canonical_jsonl(results_path)))
        returned = records[0]["observed"]["return"]["lanes"]
        returned[0] = f"0x{int(returned[0], 16) ^ 1:016x}"
        count, byte_length, digest = write_jsonl(results_path, records)
        manifest["results"].update(record_count=count, byte_length=byte_length, sha256=digest)
        atomic_write(manifest_path, dump_bytes(manifest, newline=True))

    def _failure(self, root, role):
        project = self._project(root)
        output = root / "check"
        code, _, stderr = self._main([
            "check", "--project", str(project), "--output", str(output),
            "--target", role, "--count-per-case", "1", "--quiet",
        ])
        self.assertEqual(code, 0, stderr)
        self._perturb_result(output / role / TARGETS[role].manifest_file)
        compared = root / "mismatch"
        code, result, stderr = self._main([
            "compare-results", "--project", str(project),
            "--input", str(output / "vectors/test-vectors.manifest.json"),
            "--intel", str(output / "intel/intel-results.manifest.json"),
            "--target-role", role, "--target", str(output / role / TARGETS[role].manifest_file),
            "--output", str(compared), "--allow-development-fixtures",
        ])
        self.assertEqual(code, 1, stderr)
        self.assertEqual(result["status"], "mismatch")
        summary = read_canonical_json(compared / "summary.json")
        failure_path = compared / summary["failure_bundles"][0]
        self.assertEqual(read_canonical_json(failure_path)["target_role"], role)
        return project, failure_path

    def test_single_targets_reuse_intel_and_preserve_the_default_comparison_path(self):
        for role in ("openpower", "arm", "riscv"):
            with self.subTest(role=role), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                project = self._project(root)
                output = root / "check"
                args = ["check", "--project", str(project), "--output", str(output),
                        "--count-per-case", "2", "--showcase-report", "--quiet"]
                if role != "openpower":
                    args.extend(["--target", role])
                with mock.patch("equilane.fixture.run_fixture", wraps=run_fixture) as fixtures:
                    code, result, stderr = self._main(args)
                self.assertEqual(code, 0, stderr)
                self.assertEqual(result["target_roles"], [role])
                self.assertEqual(result["record_count"], 2)
                self.assertEqual(result["comparison_record_count"], 2)
                self.assertEqual([call.kwargs["role"] for call in fixtures.call_args_list], ["intel", role])
                self.assertTrue((output / role / TARGETS[role].manifest_file).is_file())
                summary = read_canonical_json(output / "comparison/summary.json")
                self.assertEqual(summary["matched_inputs"], 2)
                self.assertTrue((output / "showcase.html").is_file())

    def test_all_targets_share_one_intel_run_and_aggregate_every_comparison(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project = self._project(root)
            output = root / "all"
            with mock.patch("equilane.fixture.run_fixture", wraps=run_fixture) as fixtures:
                code, result, stderr = self._main([
                    "check", "--project", str(project), "--output", str(output),
                    "--target", "all", "--count-per-case", "2", "--showcase-report", "--quiet",
                ])
            self.assertEqual(code, 0, stderr)
            self.assertEqual([call.kwargs["role"] for call in fixtures.call_args_list],
                             ["intel", "openpower", "arm", "riscv"])
            self.assertEqual(result["record_count"], 2)
            self.assertEqual(result["comparison_record_count"], 6)
            aggregate = read_canonical_json(output / "comparison/summary.json")
            self.assertEqual((aggregate["record_count"], aggregate["matched_inputs"]), (6, 6))
            self.assertEqual(set(aggregate["targets"]), {"openpower", "arm", "riscv"})
            for role in aggregate["targets"]:
                summary = read_canonical_json(output / f"comparison/{role}/summary.json")
                self.assertEqual(summary, aggregate["targets"][role])

    def test_repeatable_targets_are_deduplicated_and_generic_compare_reuses_generated_artifacts(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project = self._project(root)
            output = root / "selected"
            code, result, stderr = self._main([
                "check", "--project", str(project), "--output", str(output), "--count-per-case", "1",
                "--target", "riscv", "--target", "arm", "--target", "arm", "--quiet",
            ])
            self.assertEqual(code, 0, stderr)
            self.assertEqual(result["target_roles"], ["arm", "riscv"])
            code, comparison, stderr = self._main([
                "compare-results", "--project", str(project),
                "--input", str(output / "vectors/test-vectors.manifest.json"),
                "--intel", str(output / "intel/intel-results.manifest.json"),
                "--target-role", "arm", "--target", str(output / "arm/arm-results.manifest.json"),
                "--output", str(root / "separate-comparison"), "--allow-development-fixtures",
            ])
            self.assertEqual(code, 0, stderr)
            self.assertEqual(comparison["status"], "match")

    def test_parser_keeps_legacy_flags_and_accepts_new_fixture_and_replay_targets(self):
        fixture = _parser().parse_args(["fixture-run", "--input", "input", "--role", "arm", "--output", "arm"])
        self.assertEqual(fixture.role, "arm")
        for flag, role in (("--openpower", "openpower"), ("--target", "riscv")):
            args = ["verify-replay", "--failure", "failure.json", "--intel", "intel.json", flag, "target.json"]
            if flag == "--target":
                args.extend(["--target-role", role])
            parsed = _parser().parse_args(args)
            self.assertEqual(parsed.target_role, role if flag == "--target" else None)

    def test_failure_verify_cli_preserves_arm_and_riscv_identity_with_explicit_and_inferred_roles(self):
        for role in ("arm", "riscv"):
            with self.subTest(role=role), tempfile.TemporaryDirectory() as temporary:
                _, failure_path = self._failure(Path(temporary), role)
                metadata = read_canonical_json(failure_path)
                args = ["verify-replay", "--failure", str(failure_path),
                        "--intel", metadata["baseline"]["intel_manifest"],
                        "--target", metadata["baseline"][f"{role}_manifest"],
                        "--allow-development-fixtures"]
                for explicit in (False, True):
                    with self.subTest(explicit=explicit):
                        invocation = args + (["--target-role", role] if explicit else [])
                        code, result, stderr = self._main(invocation)
                        self.assertEqual(code, 0, stderr)
                        self.assertEqual(result["status"], "reproduced")
                        self.assertEqual(set(result["environment_differences"]), {"intel", role})
                other_role = "arm" if role == "riscv" else "riscv"
                code, result, _ = self._main(args + ["--target-role", other_role])
                self.assertEqual(code, 4)
                self.assertEqual(result["status"], "runner_error")
                self.assertIn("target does not match", result["reason"])

    def test_explicit_fixture_replay_is_executable_and_does_not_fabricate_reproduction(self):
        for role in ("arm", "riscv"):
            with self.subTest(role=role), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                project, failure_path = self._failure(root, role)
                # Extra bundle files must never become executable model sources.
                (failure_path.parent / "contracts/model.py").write_text(
                    "raise AssertionError('untrusted bundle model was executed')\n"
                )
                manifests = {}
                for replay_role in ("intel", role):
                    output = root / f"replay-{replay_role}"
                    args = ["replay", "--failure", str(failure_path), "--project", str(project),
                            "--role", replay_role, "--output", str(output)]
                    code, _, stderr = self._main(args)
                    self.assertEqual(code, 3)
                    self.assertIn("native replay is not implemented", stderr)
                    self.assertFalse(output.exists())
                    completed = subprocess.run(
                        [sys.executable, "-m", "equilane", *args,
                         "--i-understand-this-is-not-native-evidence"],
                        cwd=PROJECT, capture_output=True, text=True, timeout=10,
                    )
                    self.assertEqual(completed.returncode, 0, completed.stderr)
                    result = loads(completed.stdout.strip())
                    self.assertEqual(result["status"], "fixture_replay_complete")
                    self.assertFalse(result["native_evidence"])
                    self.assertEqual(result["record_count"], 1)
                    self.assertEqual(result["role"], replay_role)
                    manifests[replay_role] = Path(result["manifest"])
                    metadata = read_canonical_json(manifests[replay_role])
                    self.assertTrue(metadata["runner"]["build_id"].startswith("development-fixture:"))
                    code, _, stderr = self._main(args + ["--i-understand-this-is-not-native-evidence"])
                    self.assertEqual(code, 2)
                    self.assertIn("fresh directory", stderr)
                verify = ["verify-replay", "--failure", str(failure_path),
                          "--intel", str(manifests["intel"]), "--target", str(manifests[role]),
                          "--allow-development-fixtures"]
                code, result, stderr = self._main(verify)
                self.assertEqual(code, 1, stderr)
                self.assertEqual(result["status"], "not_reproduced")
                self._perturb_result(manifests[role])
                code, result, stderr = self._main(verify + ["--target-role", role])
                self.assertEqual(code, 0, stderr)
                self.assertEqual(result["status"], "reproduced")
                other_role = "arm" if role == "riscv" else "riscv"
                outside = root / "foreign-target"
                code, _, stderr = self._main([
                    "replay", "--failure", str(failure_path), "--project", str(project),
                    "--role", other_role, "--output", str(outside),
                    "--i-understand-this-is-not-native-evidence",
                ])
                self.assertEqual(code, 2)
                self.assertIn("failure bundle's target", stderr)
                self.assertFalse(outside.exists())


if __name__ == "__main__":
    unittest.main()
