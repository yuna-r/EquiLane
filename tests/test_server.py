from __future__ import annotations

import http.client
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest import mock


PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "src"))

from equilane.server import (  # noqa: E402
    CheckManager,
    CheckOptions,
    JobConflictError,
    ReportStore,
    create_server,
)


def write_report(root: Path, report_id: str, *, quality_failed: bool = False) -> Path:
    directory = root / report_id
    (directory / "comparison").mkdir(parents=True)
    report = directory / "showcase.html"
    report.write_text("<!doctype html><title>EquiLane report</title>", encoding="utf-8")
    summary = {
        "outcome": "pass", "record_count": 16,
        "matched_inputs": 16, "mismatched_inputs": 0, "not_comparable_inputs": 0,
    }
    (directory / "comparison" / "summary.json").write_text(json.dumps(summary), encoding="utf-8")
    contracts = directory / "vectors" / "contracts"
    contracts.mkdir(parents=True)
    (contracts / "case-definitions.json").write_text('[{"id":"a"},{"id":"b"}]', encoding="utf-8")
    if quality_failed:
        quality = directory / "quality"
        quality.mkdir()
        (quality / "summary.json").write_text(json.dumps({
            "status": "fail",
            "gates": {"python_coverage": {
                "failure_classification": {"portable_model_output_mismatches": 171},
            }},
        }), encoding="utf-8")
    return report


class ReportStoreTests(unittest.TestCase):
    def test_lists_completed_reports_and_preserves_independent_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            old = write_report(root, "older")
            new = write_report(root, "newer", quality_failed=True)
            os.utime(old, (1000, 1000))
            os.utime(new, (2000, 2000))
            incomplete = root / "incomplete"
            incomplete.mkdir()
            (incomplete / "showcase.html").write_text("partial", encoding="utf-8")
            broken = write_report(root, "broken")
            (broken.parent / "comparison" / "summary.json").write_text("not json", encoding="utf-8")
            reports = ReportStore(root).list_reports()
            self.assertEqual([report["id"] for report in reports], ["newer", "older"])
            self.assertEqual(reports[0]["case_count"], 2)
            self.assertEqual(reports[0]["outcome"], "pass")
            self.assertEqual(reports[0]["quality_status"], "fail")
            self.assertEqual(reports[0]["quality_mismatches"], 171)
            self.assertEqual(reports[1]["quality_status"], "unrecorded")

    def test_traversal_and_symlinks_do_not_expose_outside_files(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "reports"
            root.mkdir()
            outside = write_report(Path(temporary), "outside")
            (root / "escape").symlink_to(outside.parent, target_is_directory=True)
            directory = root / "linked-file"
            directory.mkdir()
            (directory / "showcase.html").symlink_to(outside)
            store = ReportStore(root)
            for report_id in ("../outside", "..", "escape", "linked-file", "a/b", "a\\b"):
                with self.subTest(report_id=report_id):
                    self.assertIsNone(store.report_file(report_id))
            self.assertEqual(store.list_reports(), [])


class CheckManagerTests(unittest.TestCase):
    def test_options_reject_coercion_unknown_fields_and_unbounded_work(self) -> None:
        self.assertEqual(CheckOptions.from_json({}), CheckOptions())
        self.assertEqual(CheckOptions.from_json({"jobs": "auto"}).jobs, "auto")
        self.assertFalse(CheckOptions.from_json({}).coverage)
        self.assertFalse(CheckOptions.from_json({"quality": True}).coverage)
        self.assertTrue(CheckOptions.from_json({"quality": True, "coverage": True}).coverage)
        invalid = [None, [], {"profile": []}, {"profile": "custom"}, {"count_per_case": True},
                   {"count_per_case": 0}, {"count_per_case": 100001}, {"count_per_case": 2.0},
                   {"jobs": True}, {"jobs": 257}, {"jobs": "2"}, {"quality": 1},
                   {"coverage": 1}, {"coverage": "false"}, {"coverage": None},
                   {"coverage": True}, {"quality": False, "coverage": True}, {"output": "/tmp"}]
        for value in invalid:
            with self.subTest(value=value), self.assertRaises(ValueError):
                CheckOptions.from_json(value)

    def test_nonzero_quality_verdict_is_completed_and_child_uses_argument_list(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manager = CheckManager(root / "project with spaces.toml", ReportStore(root / "reports"))
            entered = threading.Event()
            release = threading.Event()
            captured: dict[str, object] = {}
            cli_output = (
                "[1/8] Prepare suite...\n"
                "[7/8] Run quality gates...\n"
                "\nVerification metrics\n"
                "  match rate                              100%\n"
                "  mismatch                                  0\n"
                "\nQuality metrics\n"
                "  deep quality gates                    2 / 3\n"
            )

            class Process:
                stdout = io.StringIO(cli_output)

                def wait(self) -> int:
                    entered.set()
                    if not release.wait(timeout=3):
                        raise RuntimeError("test process was not released")
                    command = captured["command"]
                    assert isinstance(command, list)
                    output = Path(command[command.index("--output") + 1])
                    write_report(output.parent, output.name, quality_failed=True)
                    return 1

            def launch(command, **kwargs):
                captured.update(command=command, kwargs=kwargs)
                return Process()

            with mock.patch("equilane.server.subprocess.Popen", side_effect=launch):
                job = manager.start(CheckOptions(quality=True, jobs="auto"))
                try:
                    self.assertEqual(job["output"], "")
                    self.assertTrue(entered.wait(timeout=3))
                    running = manager.status()
                    self.assertEqual(running["state"], "running")
                    self.assertEqual(running["stage"], "Run quality gates")
                    self.assertEqual(running["output"], cli_output)
                    with self.assertRaises(JobConflictError):
                        manager.start(CheckOptions())
                finally:
                    release.set()
                    manager._thread.join(timeout=3)
            status = manager.status()
            self.assertEqual(status["state"], "completed")
            self.assertEqual(status["exit_code"], 1)
            self.assertEqual(status["output"], cli_output)
            self.assertEqual(status["report_url"], f"/reports/{job['id']}/showcase.html")
            self.assertIsNone(status["error"])
            command = captured["command"]
            self.assertIn("--quality", command)
            self.assertNotIn("--coverage", command)
            self.assertEqual(command[command.index("--jobs") + 1], "auto")
            self.assertEqual(command[command.index("--project") + 1], str(manager.project))
            self.assertNotIn("shell", captured["kwargs"])

    def test_source_tracing_flag_requires_an_explicit_coverage_option(self) -> None:
        for coverage in (False, True):
            with self.subTest(coverage=coverage), tempfile.TemporaryDirectory() as temporary:
                manager = CheckManager(PROJECT / "equilane.toml", ReportStore(Path(temporary)))
                process = mock.Mock(stdout=io.StringIO("actual CLI output\n"))
                process.wait.return_value = 2
                with mock.patch("equilane.server.subprocess.Popen", return_value=process) as launch:
                    manager.start(CheckOptions(quality=True, coverage=coverage))
                    manager._thread.join(timeout=3)
                command = launch.call_args.args[0]
                self.assertIn("--quality", command)
                self.assertEqual("--coverage" in command, coverage)
                self.assertEqual(manager.status()["output"], "actual CLI output\n")

    def test_process_launch_error_is_visible_and_allows_a_later_job(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            manager = CheckManager(PROJECT / "equilane.toml", ReportStore(Path(temporary)))
            with mock.patch("equilane.server.subprocess.Popen", side_effect=OSError("cannot launch")):
                first = manager.start(CheckOptions())
                manager._thread.join(timeout=3)
                self.assertEqual(manager.status()["state"], "failed")
                self.assertIn("cannot launch", manager.status()["error"])
                second = manager.start(CheckOptions())
                manager._thread.join(timeout=3)
                self.assertNotEqual(first["id"], second["id"])

    def test_process_without_completed_report_is_failed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            manager = CheckManager(PROJECT / "equilane.toml", ReportStore(Path(temporary)))
            process = mock.Mock(stdout=io.StringIO("equilane: invalid project\n"))
            process.wait.return_value = 2
            with mock.patch("equilane.server.subprocess.Popen", return_value=process):
                manager.start(CheckOptions())
                manager._thread.join(timeout=3)
            self.assertEqual(manager.status()["state"], "failed")
            self.assertEqual(manager.status()["exit_code"], 2)
            self.assertIn("invalid project", manager.status()["error"])
            self.assertEqual(manager.status()["output"], "equilane: invalid project\n")

    def test_output_keeps_a_bounded_tail_without_trimming_whitespace(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            manager = CheckManager(PROJECT / "equilane.toml", ReportStore(Path(temporary)))
            cli_output = "discarded beginning\n" + "x" * 200_000 + "\n  final metrics  \n\n"
            process = mock.Mock(stdout=io.StringIO(cli_output))
            process.wait.return_value = 2
            with mock.patch("equilane.server.subprocess.Popen", return_value=process):
                manager.start(CheckOptions())
                manager._thread.join(timeout=3)
            output = manager.status()["output"]
            self.assertLessEqual(len(output), 128 * 1024)
            self.assertNotIn("discarded beginning", output)
            self.assertEqual(output, cli_output[-128 * 1024:])
            self.assertTrue(output.endswith("\n  final metrics  \n\n"))


class DashboardHTTPTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.server = create_server(project=PROJECT / "equilane.toml", reports_root=self.root, port=0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.port = self.server.server_address[1]

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)
        self.temporary.cleanup()

    def request(self, path: str, *, method: str = "GET", body=None, headers=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=3)
        supplied = {} if headers is None else dict(headers)
        encoded = None
        if body is not None:
            encoded = json.dumps(body)
            supplied.setdefault("Content-Type", "application/json")
        try:
            connection.request(method, path, encoded, supplied)
            response = connection.getresponse()
            data = response.read()
            return response.status, data, response.getheader("Content-Type")
        finally:
            connection.close()

    def test_api_listing_status_and_report_routing(self) -> None:
        report = write_report(self.root, "demo")
        status, data, content_type = self.request("/api/reports")
        self.assertEqual(status, 200)
        self.assertIn("application/json", content_type)
        self.assertEqual(json.loads(data)["reports"][0]["id"], "demo")
        self.assertEqual(json.loads(self.request("/api/status")[1]), {"job": None})
        status, data, content_type = self.request("/reports/demo/showcase.html")
        self.assertEqual(status, 200)
        self.assertEqual(data, report.read_bytes())
        self.assertIn("text/html", content_type)
        for path in ("/reports/../showcase.html", "/reports/%2E%2E/showcase.html",
                     "/reports/demo/../../equilane.toml", "/reports/demo/comparison/summary.json"):
            with self.subTest(path=path):
                self.assertEqual(self.request(path)[0], 404)

    def test_only_same_origin_json_can_start_jobs_and_conflicts_are_reported(self) -> None:
        origin = f"http://127.0.0.1:{self.port}"
        self.assertEqual(self.request("/api/check", method="POST", body={},
                                     headers={"Origin": "https://example.com"})[0], 403)
        self.assertEqual(self.request("/api/check", method="POST", body={},
                                     headers={"Host": f"example.com:{self.port}"})[0], 403)
        self.assertEqual(self.request("/api/check", method="POST", body={},
                                     headers={"Origin": origin, "Sec-Fetch-Site": "cross-site"})[0], 403)
        self.assertEqual(self.request("/api/check", method="POST", body={},
                                     headers={"Content-Type": "text/plain"})[0], 415)
        self.assertEqual(self.request("/api/check", method="POST", body={"count_per_case": True},
                                     headers={"Origin": origin})[0], 400)
        self.assertEqual(self.request("/api/check", method="POST", body={"coverage": True},
                                     headers={"Origin": origin})[0], 400)
        with mock.patch.object(self.server.checks, "start", return_value={"state": "running"}) as start:
            status, data, _ = self.request("/api/check", method="POST", body={"jobs": "auto"},
                                           headers={"Origin": origin})
            self.assertEqual(status, 202)
            self.assertEqual(json.loads(data)["job"]["state"], "running")
            self.assertEqual(start.call_args.args[0].jobs, "auto")
            self.assertFalse(start.call_args.args[0].coverage)
            status, _, _ = self.request("/api/check", method="POST", body={"quality": True, "coverage": True},
                                        headers={"Origin": origin})
            self.assertEqual(status, 202)
            self.assertTrue(start.call_args.args[0].quality)
            self.assertTrue(start.call_args.args[0].coverage)
        with mock.patch.object(self.server.checks, "start", side_effect=JobConflictError("busy")):
            self.assertEqual(self.request("/api/check", method="POST", body={})[0], 409)

    def test_external_host_bind_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "host"):
            create_server(host="0.0.0.0", project=PROJECT / "equilane.toml", reports_root=self.root)


if __name__ == "__main__":
    unittest.main()
