"""Failure and image-identity checks for the Docker-only host launcher."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
FAKE_DOCKER = r'''#!/usr/bin/env python3
import json, os, pathlib, sys
args = sys.argv[1:]
with open(os.environ["DOCKER_CALLS"], "a") as stream:
    stream.write(json.dumps(args) + "\n")
if args[0] == "info":
    if os.environ.get("FAIL_INFO"): sys.exit(125)
    print("aarch64")
elif args[0] == "build":
    role = args[args.index("--tag")+1].rsplit(":", 1)[1]
    if os.environ.get("FAIL_BUILD") == role: sys.exit(125)
    pathlib.Path(args[args.index("--iidfile")+1]).write_text("sha256:built-"+role)
elif args[:2] == ["image", "inspect"]:
    print("sha256:mutated-tag")
elif args[0] == "run":
    if "capture" in args and os.environ.get("FAIL_CAPTURE") == args[args.index("--target")+1]: sys.exit(125)
    if "compare" in args: sys.exit(int(os.environ.get("COMPARE_CODE", "0")))
    if "report" in args:
        mount=args[args.index("--mount")+1]
        directory=pathlib.Path(mount.split("src=",1)[1].split(",dst=",1)[0])
        (directory/"pipeline-summary.json").write_text('{"status":"pass"}')
else:
    sys.exit(2)
'''


class ContainerLauncherTests(unittest.TestCase):
    def _run(self, directory, action, **variables):
        binary = directory / "bin"
        binary.mkdir(exist_ok=True)
        docker = binary / "docker"
        docker.write_text(FAKE_DOCKER)
        docker.chmod(0o755)
        calls = directory / "calls.jsonl"
        calls.write_text("")
        environment = {**os.environ, "PATH": str(binary) + os.pathsep + os.environ["PATH"],
                       "DOCKER_CALLS": str(calls), "EQUILANE_BUILD": "1", **variables}
        completed = subprocess.run([str(ROOT / "docker/run.sh"), action, str(directory / "results")],
                                   env=environment, capture_output=True, text=True, timeout=10)
        return completed, [json.loads(row) for row in calls.read_text().splitlines()]

    def test_build_failure_invalidates_previous_success_before_docker_runs(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            output = directory / "results"
            (output / "arm").mkdir(parents=True)
            (output / "arm/manifest.json").write_text('{"status":"captured"}')
            (output / "compare-arm.json").write_text('{"status":"pass"}')
            (output / "pipeline-report.html").write_text("old success")
            completed, _ = self._run(directory, "arm", FAIL_BUILD="arm")
            self.assertEqual(completed.returncode, 2, completed.stderr)
            self.assertFalse((output / "arm/manifest.json").exists())
            self.assertFalse((output / "compare-arm.json").exists())
            self.assertIn("failed", (output / "pipeline-report.html").read_text())

    def test_capture_runs_the_built_image_id_without_resolving_mutable_tag(self):
        with tempfile.TemporaryDirectory() as temporary:
            completed, calls = self._run(Path(temporary), "arm")
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertTrue(any("--iidfile" in call for call in calls if call[0] == "build"))
            self.assertFalse(any(call[:2] == ["image", "inspect"] for call in calls))
            run = next(call for call in calls if call[0] == "run")
            self.assertIn("sha256:built-arm", run)
            self.assertNotIn("equilane/ubuntu26.04:arm", run)

    def test_all_attempts_remaining_targets_after_one_build_fails(self):
        with tempfile.TemporaryDirectory() as temporary:
            completed, calls = self._run(Path(temporary), "wrappers", FAIL_BUILD="openpower")
            self.assertEqual(completed.returncode, 2, completed.stderr)
            built = [call[call.index("--tag")+1].rsplit(":",1)[1] for call in calls if call[0] == "build"]
            self.assertEqual(built, ["intel", "openpower", "arm", "riscv"])
            self.assertTrue(any("capture" in call and "riscv" in call for call in calls if call[0] == "run"))

    def test_semantic_mismatch_and_runner_failure_have_distinct_exit_codes(self):
        for code in ("1", "2", "125"):
            with self.subTest(code=code), tempfile.TemporaryDirectory() as temporary:
                completed, _ = self._run(Path(temporary), "wrappers", COMPARE_CODE=code)
                self.assertEqual(completed.returncode, 1 if code == "1" else 2, completed.stderr)

    def test_disconnected_docker_still_emits_an_error_report(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            markers = []
            for role in ("intel", "openpower", "arm", "riscv", "riscv-vlen256"):
                previous = directory / "results" / "native" / role
                previous.mkdir(parents=True)
                stem = "power" if role == "openpower" else "riscv" if role == "riscv-vlen256" else role
                for name in (f"{stem}-results.manifest.json", "native-traceability.json"):
                    marker = previous / name
                    marker.write_text('{"complete":true}')
                    markers.append(marker)
            completed, _ = self._run(directory, "all", FAIL_INFO="1")
            self.assertEqual(completed.returncode, 2)
            self.assertTrue(all(not marker.exists() for marker in markers))
            summary = json.loads((directory / "results/pipeline-summary.json").read_text())
            self.assertEqual(summary["status"], "error")


if __name__ == "__main__":
    unittest.main()
