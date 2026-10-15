"""Adversarial report history, job lifecycle, HTTP, and browser behavior checks.

The HTTP handlers use in-memory streams so malformed request tests do not bind a
port or start real check processes. Browser checks execute the shipped JavaScript.
"""

from __future__ import annotations

import copy
from datetime import datetime, timedelta, timezone
from email.message import Message
from html.parser import HTMLParser
import io
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest import mock


PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "src"))

from equilane.cases import CaseDefinition
from equilane.server import (
    CheckManager, CheckOptions, DashboardHandler, JobConflictError, ReportStore,
    create_server,
)
from equilane.showcase import _CATALOG_SCRIPT, render_showcase_html, write_showcase_report
from equilane.webui import render_dashboard


def summary(count=4, **changes):
    value = {
        "record_count": count, "matched_inputs": count,
        "mismatched_inputs": 0, "not_comparable_inputs": 0,
        "mismatch_atoms": 0, "outcome": "pass",
    }
    value.update(changes)
    return value


def write_report(root, name="run", *, data=None):
    directory = root / name
    (directory / "comparison").mkdir(parents=True)
    (directory / "showcase.html").write_text("<title>結果</title>", encoding="utf-8")
    (directory / "comparison" / "summary.json").write_text(
        json.dumps(summary() if data is None else data), encoding="utf-8",
    )
    return directory


class ReportHistoryBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.directory = write_report(self.root)
        self.store = ReportStore(self.root)

    def tearDown(self):
        self.temporary.cleanup()

    def replace_summary(self, value):
        (self.directory / "comparison/summary.json").write_text(json.dumps(value), encoding="utf-8")

    def test_report_id_character_and_length_boundaries(self):
        for name in ("0", "a.b_c-9", "z" * 128):
            with self.subTest(name=name):
                directory = write_report(self.root, name)
                self.assertEqual(self.store.report_file(name), (directory / "showcase.html").resolve())
        for name in ("", ".hidden", "-dash", "_under", "x" * 129, "résumé", "a\x00b", "a\nb"):
            with self.subTest(name=name):
                self.assertIsNone(self.store.report_file(name))

    def test_corrupt_summary_encodings_and_non_objects_are_not_listed(self):
        path = self.directory / "comparison/summary.json"
        for body in (b"\xff\xfe", b"{", b"null", b"[]", b"42", b'"pass"'):
            with self.subTest(body=body):
                path.write_bytes(body)
                self.assertIsNone(self.store.describe("run"))
                self.assertEqual(self.store.list_reports(), [])

    def test_counters_cannot_be_negative_boolean_fractional_or_strings(self):
        for key in ("record_count", "matched_inputs", "mismatched_inputs", "not_comparable_inputs"):
            for bad in (-1, True, False, 1.5, "4", None):
                with self.subTest(key=key, bad=bad):
                    data = summary()
                    data[key] = bad
                    self.replace_summary(data)
                    self.assertIsNone(self.store.describe("run"))

    def test_missing_counters_totals_and_outcomes_are_rejected(self):
        for key in ("record_count", "matched_inputs", "mismatched_inputs", "not_comparable_inputs", "outcome"):
            with self.subTest(missing=key):
                data = summary()
                del data[key]
                self.replace_summary(data)
                self.assertIsNone(self.store.describe("run"))
        for changes in ({"matched_inputs": 3}, {"outcome": []}, {"outcome": "unknown"}):
            with self.subTest(changes=changes):
                self.replace_summary(summary(**changes))
                self.assertIsNone(self.store.describe("run"))

    def test_single_target_identity_requires_a_known_string(self):
        for role in (None, True, [], {}, "intel", "ARM", "all"):
            with self.subTest(role=role):
                self.replace_summary(summary(target_role=role))
                self.assertIsNone(self.store.describe("run"))

    def test_target_maps_must_contain_valid_target_summaries(self):
        for targets in ({}, [], "arm", {"intel": {}}, {"arm": None}, {"arm": []}, {"arm": {}}):
            with self.subTest(targets=targets):
                self.replace_summary(summary(targets=targets))
                self.assertIsNone(self.store.describe("run"))

    def test_target_counts_must_match_the_shared_stream_and_aggregate(self):
        good = {"arm": summary(2), "riscv": summary(2)}
        bad_maps = [
            {"arm": summary(1), "riscv": summary(3)},
            {"arm": summary(2), "riscv": summary(3)},
            {"arm": summary(2, matched_inputs=1), "riscv": summary(2)},
            {"arm": summary(2, matched_inputs=True), "riscv": summary(2)},
            {"arm": summary(2, outcome="bad"), "riscv": summary(2)},
            {"arm": summary(2, matched_inputs=1, mismatched_inputs=1, outcome="mismatch"), "riscv": summary(2)},
        ]
        for targets in bad_maps:
            with self.subTest(targets=targets):
                self.replace_summary(summary(targets=targets))
                self.assertIsNone(self.store.describe("run"))
        self.replace_summary(summary(targets=good))
        self.assertEqual(self.store.describe("run")["unique_input_count"], 2)

    def test_report_file_and_comparison_symlinks_cannot_escape_the_report(self):
        outside = self.root / "outside.json"
        outside.write_text(json.dumps(summary()), encoding="utf-8")
        path = self.directory / "comparison/summary.json"
        path.unlink()
        path.symlink_to(outside)
        self.assertIsNone(self.store.describe("run"))
        path.unlink()
        (self.directory / "comparison").rmdir()
        external_directory = self.root / "external"
        external_directory.mkdir()
        (external_directory / "summary.json").write_text(json.dumps(summary()), encoding="utf-8")
        (self.directory / "comparison").symlink_to(external_directory, target_is_directory=True)
        self.assertIsNone(self.store.describe("run"))

    def test_optional_metadata_symlinks_are_ignored_independently(self):
        outside = self.root / "sensitive"
        outside.mkdir()
        (outside / "summary.json").write_text(json.dumps({"status": "pass"}), encoding="utf-8")
        (self.directory / "quality").symlink_to(outside, target_is_directory=True)
        contracts = self.directory / "vectors/contracts"
        contracts.mkdir(parents=True)
        (outside / "cases.json").write_text("[{}, {}, {}]", encoding="utf-8")
        (contracts / "case-definitions.json").symlink_to(outside / "cases.json")
        report = self.store.describe("run")
        self.assertEqual(report["quality_status"], "unrecorded")
        self.assertIsNone(report["case_count"])

    def test_quality_count_prefers_valid_classification_then_actual_rows(self):
        quality = self.directory / "quality"
        quality.mkdir()
        for count, expected in ((0, 0), (7, 7), (-1, 2), (True, 2), ("7", 2), (None, 2)):
            with self.subTest(count=count):
                (quality / "summary.json").write_text(json.dumps({
                    "status": "fail", "gates": {"python_coverage": {
                        "failure_classification": {"portable_model_output_mismatches": count},
                        "model_output_mismatches": [{}, {}],
                    }},
                }), encoding="utf-8")
                self.assertEqual(self.store.describe("run")["quality_mismatches"], expected)

    def test_optional_corrupt_metadata_does_not_hide_a_completed_report(self):
        (self.directory / "quality").mkdir()
        (self.directory / "quality/summary.json").write_bytes(b"\xff")
        (self.directory / "vectors/contracts").mkdir(parents=True)
        (self.directory / "vectors/contracts/case-definitions.json").write_text("{}", encoding="utf-8")
        report = self.store.describe("run")
        self.assertEqual(report["quality_status"], "unrecorded")
        self.assertIsNone(report["case_count"])

    def test_timestamp_ties_are_sorted_by_descending_report_id(self):
        second = write_report(self.root, "z-run")
        for directory in (self.directory, second):
            os.utime(directory / "showcase.html", (1234, 1234))
        self.assertEqual([report["id"] for report in self.store.list_reports()], ["z-run", "run"])

    def test_disappearing_report_and_unreadable_root_are_ignored(self):
        with mock.patch.object(Path, "stat", side_effect=FileNotFoundError):
            self.assertIsNone(self.store.describe("run"))
        with mock.patch.object(Path, "iterdir", side_effect=PermissionError):
            self.assertEqual(self.store.list_reports(), [])
        self.assertEqual(ReportStore(self.root / "absent").list_reports(), [])


class JobLifecycleBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.manager = CheckManager(PROJECT / "equilane.toml", ReportStore(self.root / "reports"))

    def tearDown(self):
        thread = self.manager._thread
        if thread is not None:
            thread.join(timeout=3)
        self.temporary.cleanup()

    def run_output(self, text, exit_code=0):
        process = mock.Mock(stdout=io.StringIO(text))
        process.wait.return_value = exit_code
        with mock.patch("equilane.server.subprocess.Popen", return_value=process):
            job = self.manager.start(CheckOptions())
            self.manager._thread.join(timeout=3)
            self.assertFalse(self.manager._thread.is_alive())
        return job, self.manager.status()

    def test_idle_status_and_returned_snapshots_do_not_expose_mutable_job_dict(self):
        self.assertIsNone(self.manager.status())
        job, _ = self.run_output("failure\n")
        job["state"] = "forged"
        status = self.manager.status()
        status["error"] = "forged"
        self.assertEqual(self.manager.status()["state"], "failed")
        self.assertEqual(self.manager.status()["error"], "failure")

    def test_empty_output_has_a_useful_failure_even_for_zero_exit(self):
        _, status = self.run_output("", exit_code=0)
        self.assertEqual(status["state"], "failed")
        self.assertEqual(status["exit_code"], 0)
        self.assertEqual(status["error"], "check ended without a completed report")
        self.assertEqual(status["output"], "")

    def test_failure_uses_last_nonempty_line_with_a_bounded_message(self):
        text = "first error\n" + "x" * 1500 + "FINAL\n   \n\t\n"
        _, status = self.run_output(text, exit_code=-9)
        self.assertEqual(status["exit_code"], -9)
        self.assertEqual(status["error"], ("x" * 1500 + "FINAL")[-1000:])
        self.assertEqual(status["output"], text)

    def test_success_restarts_reset_output_error_exit_code_and_report_url(self):
        first, failed = self.run_output("failed first\n", exit_code=2)
        process = mock.Mock(stdout=io.StringIO("second output\n"))
        process.wait.return_value = 0
        with mock.patch("equilane.server.subprocess.Popen", return_value=process), \
                mock.patch.object(self.manager.reports, "describe", return_value={"url": "/reports/new/showcase.html"}):
            second = self.manager.start(CheckOptions())
            self.manager._thread.join(timeout=3)
        self.assertNotEqual(first["id"], second["id"])
        self.assertEqual(failed["state"], "failed")
        self.assertEqual(self.manager.status()["state"], "completed")
        self.assertEqual(self.manager.status()["output"], "second output\n")
        self.assertIsNone(self.manager.status()["error"])
        self.assertEqual(self.manager.status()["exit_code"], 0)
        self.assertEqual(self.manager.status()["report_url"], "/reports/new/showcase.html")

    def test_stage_suffixes_are_removed_and_nonstage_output_is_preserved(self):
        output = "[1/3] Prepare...\n[2/3] Compare: done 4 inputs\n[3/3] Finalize: failed detail\nnoise\n"
        observed = []
        original_update = self.manager._update
        def update(**changes):
            if "stage" in changes:
                observed.append(changes["stage"])
            original_update(**changes)
        with mock.patch.object(self.manager, "_update", side_effect=update):
            _, status = self.run_output(output, exit_code=1)
        self.assertEqual(observed, ["Prepare", "Compare", "Finalize", "Check failed"])
        self.assertEqual(status["output"], output)

    def test_simultaneous_start_calls_launch_exactly_one_worker(self):
        release = threading.Event()
        entered = threading.Event()
        barrier = threading.Barrier(5)
        outcomes = []
        process = mock.Mock(stdout=io.StringIO("running\n"))
        def finish():
            entered.set()
            if not release.wait(timeout=3):
                raise RuntimeError("test release timed out")
            return 2
        process.wait.side_effect = finish
        def start():
            barrier.wait(timeout=3)
            try:
                outcomes.append(self.manager.start(CheckOptions())["id"])
            except JobConflictError:
                outcomes.append("conflict")
        threads = [threading.Thread(target=start) for _ in range(4)]
        with mock.patch("equilane.server.subprocess.Popen", return_value=process) as launch:
            try:
                for thread in threads:
                    thread.start()
                barrier.wait(timeout=3)
                for thread in threads:
                    thread.join(timeout=3)
                    self.assertFalse(thread.is_alive())
                self.assertTrue(entered.wait(timeout=3))
                self.assertEqual(outcomes.count("conflict"), 3)
                self.assertEqual(launch.call_count, 1)
            finally:
                release.set()
                self.manager._thread.join(timeout=3)

    def test_directory_creation_failure_is_visible_without_launching_a_process(self):
        self.manager.reports.root.parent.mkdir(parents=True, exist_ok=True)
        self.manager.reports.root.write_text("not a directory", encoding="utf-8")
        with mock.patch("equilane.server.subprocess.Popen") as launch:
            self.manager.start(CheckOptions())
            self.manager._thread.join(timeout=3)
        launch.assert_not_called()
        self.assertEqual(self.manager.status()["state"], "failed")
        self.assertIsNotNone(self.manager.status()["error"])

    def test_stop_before_start_is_idempotent_and_prevents_new_work(self):
        self.manager.stop()
        self.manager.stop()
        with self.assertRaises(JobConflictError):
            self.manager.start(CheckOptions())
        self.assertIsNone(self.manager.status())

    def test_stop_terminates_the_process_group_and_joins_the_worker(self):
        process = mock.Mock(pid=4321)
        process.poll.return_value = None
        worker = mock.Mock()
        self.manager._process = process
        self.manager._thread = worker
        with mock.patch("equilane.server.os.name", "posix"), \
                mock.patch("equilane.server.os.killpg") as kill:
            self.manager.stop()
        kill.assert_called_once_with(4321, signal_number("SIGTERM"))
        process.wait.assert_called_once_with(timeout=2)
        worker.join.assert_called_once_with(timeout=3)

    def test_stop_escalates_a_stuck_process_and_tolerates_a_disappearing_process(self):
        process = mock.Mock(pid=4321)
        process.poll.return_value = None
        process.wait.side_effect = subprocess.TimeoutExpired("check", 2)
        self.manager._process = process
        with mock.patch("equilane.server.os.name", "posix"), \
                mock.patch("equilane.server.os.killpg") as kill:
            self.manager.stop()
        self.assertEqual(kill.call_args_list, [
            mock.call(4321, signal_number("SIGTERM")), mock.call(4321, signal_number("SIGKILL")),
        ])
        with mock.patch("equilane.server.os.killpg", side_effect=ProcessLookupError):
            self.manager.stop()

    def test_stop_does_not_signal_a_process_that_has_already_exited(self):
        process = mock.Mock()
        process.poll.return_value = 2
        self.manager._process = process
        with mock.patch("equilane.server.os.killpg") as kill:
            self.manager.stop()
        kill.assert_not_called()
        process.terminate.assert_not_called()

    def test_stop_tolerates_a_process_exiting_during_kill_escalation(self):
        process = mock.Mock(pid=4321)
        process.poll.return_value = None
        process.wait.side_effect = subprocess.TimeoutExpired("check", 2)
        self.manager._process = process
        with mock.patch("equilane.server.os.name", "posix"), \
                mock.patch("equilane.server.os.killpg", side_effect=[None, ProcessLookupError]):
            self.manager.stop()

    def test_windows_stop_uses_process_methods_instead_of_process_groups(self):
        process = mock.Mock()
        process.poll.return_value = None
        self.manager._process = process
        with mock.patch("equilane.server.os.name", "nt"), mock.patch("equilane.server.os.killpg") as kill:
            self.manager.stop()
        process.terminate.assert_called_once_with()
        process.kill.assert_not_called()
        kill.assert_not_called()
        process.wait.side_effect = subprocess.TimeoutExpired("check", 2)
        with mock.patch("equilane.server.os.name", "nt"):
            self.manager.stop()
        process.kill.assert_called_once_with()


def signal_number(name):
    import signal
    return getattr(signal, name)


class MemoryHandler(DashboardHandler):
    """The actual routing and validation, with an in-memory response transport."""

    def __init__(self, *, path="/api/check", body=b"{}", headers=None, server=None):
        self.path = path
        self.headers = Message()
        for key, value in {"Host": "127.0.0.1:8123", "Content-Type": "application/json",
                           "Content-Length": str(len(body)), **(headers or {})}.items():
            if value is not None:
                self.headers[key] = value
        self.rfile = io.BytesIO(body)
        self.server = server or SimpleNamespace(
            server_address=("127.0.0.1", 8123), reports=mock.Mock(), checks=mock.Mock(),
        )
        self.response = None

    def _send(self, status, body, content_type):
        self.response = status, body, content_type

    def json_response(self):
        status, body, _ = self.response
        return status, json.loads(body)


class HTTPBoundaryTests(unittest.TestCase):
    def post(self, **kwargs):
        handler = MemoryHandler(**kwargs)
        handler.server.checks.start.return_value = {"id": "one", "state": "running"}
        handler.do_POST()
        return handler

    def assert_rejected(self, expected, **kwargs):
        handler = self.post(**kwargs)
        self.assertEqual(handler.response[0], expected)
        self.assertIsInstance(handler.json_response()[1]["error"], str)
        handler.server.checks.start.assert_not_called()

    def test_host_must_include_the_exact_local_port_and_no_userinfo(self):
        for host in (None, "localhost", "localhost:99", "127.0.0.1", "127.0.0.1:8123.evil", "evil@localhost:8123", "[::1]"):
            with self.subTest(host=host):
                self.assert_rejected(403, headers={"Host": host})
        for host in ("127.0.0.1:8123", "localhost:8123", "[::1]:8123"):
            with self.subTest(host=host):
                self.assertEqual(self.post(headers={"Host": host}).response[0], 202)

    def test_origin_and_fetch_metadata_both_reject_unsafe_requests(self):
        for headers in ({"Origin": "null"}, {"Origin": "https://127.0.0.1:8123"},
                        {"Origin": "http://localhost:8123"}, {"Origin": "http://127.0.0.1:8123/"},
                        {"Sec-Fetch-Site": "cross-site"}, {"Sec-Fetch-Site": "same-site"}):
            with self.subTest(headers=headers):
                self.assert_rejected(403, headers=headers)
        self.assertEqual(self.post(headers={"Origin": "http://127.0.0.1:8123", "Sec-Fetch-Site": "same-origin"}).response[0], 202)

    def test_missing_invalid_zero_negative_and_oversized_content_length(self):
        for length, expected in ((None, 411), ("", 411), ("two", 411), ("1.5", 411), ("-1", 413), ("0", 413), ("4097", 413)):
            with self.subTest(length=length):
                self.assert_rejected(expected, headers={"Content-Length": length})

    def test_json_media_type_parameters_are_allowed_but_other_media_types_are_not(self):
        for content_type in (None, "", "text/json", "text/plain", "application/jsonp"):
            with self.subTest(content_type=content_type):
                self.assert_rejected(415, headers={"Content-Type": content_type})
        self.assertEqual(self.post(headers={"Content-Type": "application/json; charset=utf-8"}).response[0], 202)

    def test_transfer_encoding_is_rejected_even_with_a_length(self):
        for encoding in ("chunked", "identity", "gzip", ""):
            with self.subTest(encoding=encoding):
                self.assert_rejected(400, headers={"Transfer-Encoding": encoding})

    def test_malformed_nonobject_and_invalid_utf8_json_never_launches_jobs(self):
        for body in (b"{", b"\xff", b"null", b"[]", b"true", b"42", b'"text"', b'{"quality": 1}'):
            with self.subTest(body=body):
                self.assert_rejected(400, body=body)

    def test_truncated_json_body_is_rejected_even_if_its_prefix_is_valid(self):
        self.assert_rejected(400, body=b"{}", headers={"Content-Length": "3"})

    def test_maximum_size_body_is_accepted_and_the_excess_is_not_parsed(self):
        handler = self.post(body=b"{}" + b" " * 4094)
        self.assertEqual(handler.response[0], 202)
        handler.server.checks.start.assert_called_once_with(CheckOptions())
        self.assert_rejected(400, body=b"{}evil")

    def test_query_parameters_do_not_change_the_check_route(self):
        self.assertEqual(self.post(path="/api/check?next=%2Freports").response[0], 202)
        for path in ("/api/check/", "/api/status", "/reports/run/showcase.html", "/api/%2Fcheck"):
            with self.subTest(path=path):
                self.assert_rejected(404, path=path)

    def test_malformed_absolute_request_targets_return_json_bad_request(self):
        for path in ("http://[invalid/api/check", "http://[::1/api/check"):
            with self.subTest(path=path):
                self.assert_rejected(400, path=path)
                handler = MemoryHandler(path=path)
                handler.do_GET()
                self.assertEqual(handler.response[0], 400)

    def test_post_conflict_and_unicode_error_messages_are_machine_readable(self):
        handler = MemoryHandler()
        handler.server.checks.start.side_effect = JobConflictError("チェック実行中")
        handler.do_POST()
        self.assertEqual(handler.json_response(), (409, {"error": "チェック実行中"}))
        self.assertIn("charset=utf-8", handler.response[2])

    def test_report_read_race_returns_not_found_instead_of_an_exception(self):
        handler = MemoryHandler(path="/reports/run/showcase.html")
        report = mock.Mock()
        report.read_bytes.side_effect = PermissionError("unreadable")
        handler.server.reports.report_file.return_value = report
        handler.do_GET()
        self.assertEqual(handler.json_response(), (404, {"error": "report not found"}))

    def test_get_disallows_external_host_before_reading_any_report(self):
        handler = MemoryHandler(path="/api/reports", headers={"Host": "external.test:8123"})
        handler.do_GET()
        self.assertEqual(handler.response[0], 403)
        handler.server.reports.list_reports.assert_not_called()

    def test_get_decodes_report_identifier_but_cannot_route_encoded_slashes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            directory = write_report(root, "report-1")
            server = SimpleNamespace(server_address=("127.0.0.1", 8123), reports=ReportStore(root), checks=mock.Mock())
            handler = MemoryHandler(path="/reports/report%2D1/showcase.html?download=1", server=server)
            handler.do_GET()
            self.assertEqual(handler.response[0], 200)
            self.assertEqual(handler.response[1], (directory / "showcase.html").read_bytes())
            for path in ("/reports/report%2F1/showcase.html", "/reports/report%5C1/showcase.html", "/reports/%00/showcase.html"):
                with self.subTest(path=path):
                    handler = MemoryHandler(path=path, server=server)
                    handler.do_GET()
                    self.assertEqual(handler.response[0], 404)

    def test_server_port_validation_happens_before_creating_a_socket(self):
        for port in (True, False, -1, 65536, "8080", 80.0, None):
            with self.subTest(port=port), mock.patch("equilane.server.DashboardServer") as server:
                with self.assertRaises(ValueError):
                    create_server(port=port, project=PROJECT, reports_root=PROJECT)
                server.assert_not_called()

    def test_response_headers_count_utf8_bytes_and_disable_caching_and_sniffing(self):
        handler = MemoryHandler()
        handler.request_version = "HTTP/1.1"
        handler.requestline = "GET /api/status HTTP/1.1"
        handler.wfile = io.BytesIO()
        body = json.dumps({"message": "結果"}, ensure_ascii=False).encode("utf-8")
        DashboardHandler._send(handler, 200, body, "application/json; charset=utf-8")
        headers, actual_body = handler.wfile.getvalue().split(b"\r\n\r\n", 1)
        self.assertIn(f"Content-Length: {len(body)}".encode(), headers)
        self.assertIn(b"Cache-Control: no-store", headers)
        self.assertIn(b"X-Content-Type-Options: nosniff", headers)
        self.assertEqual(actual_body, body)


class Markup(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.cards = []
        self.options = []
        self.ids = []
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if attributes.get("id"):
            self.ids.append(attributes["id"])
        if tag == "article":
            self.cards.append(attributes)
        if tag == "option":
            self.options.append(attributes.get("value"))


class ShowcaseBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.case = CaseDefinition("sse2.example.i8", {
            "description": "boundary example", "comparison": {"mode": "bit_exact"},
            "signature": {"arguments": [{"name": "a", "type": "vector", "element": "i8", "lanes": 16}],
                          "return": {"type": "vector", "element": "i8", "lanes": 16}},
            "intel": {"symbol": "intel_example", "required_isa": ["sse2"]},
            "openpower": {"symbol": "power_example", "required_isa": ["vsx"]},
        })
        self.kwargs = dict(cases=[self.case], summary=summary(), profile="smoke", seed="123",
                           vector_sha256="a" * 64, case_definitions_sha256="b" * 64,
                           isa_contract_sha256="c" * 64,
                           generated_at=datetime(2026, 10, 16, tzinfo=timezone.utc), native_evidence=False)

    def render(self, **changes):
        return render_showcase_html(**{**self.kwargs, **changes})

    def test_empty_run_has_zero_workload_no_cards_and_unique_navigation_targets(self):
        html = self.render(cases=[], summary=summary(0))
        markup = Markup(html)
        self.assertEqual(markup.cards, [])
        self.assertEqual(len(markup.ids), len(set(markup.ids)))
        self.assertIn("0 / 0 cases", html)
        self.assertIn('aria-valuenow="0"', html)
        self.assertNotRegex(html, r">(?:NaN|Infinity)(?:%|<)")

    def test_case_generators_are_consumed_once_and_keep_catalog_order(self):
        other = CaseDefinition("sse2.other.i8", copy.deepcopy(self.case.data))
        cases = (case for case in (other, self.case))
        html = self.render(cases=cases, summary=summary(8))
        self.assertEqual(len(Markup(html).cards), 2)
        self.assertLess(html.index("<h3>sse2.other.i8"), html.index("<h3>sse2.example.i8"))
        self.assertEqual(Markup(html).options.count("i8"), 1)
        self.assertEqual(Markup(html).options.count("bit_exact"), 1)

    def test_hostile_case_ids_names_and_isa_stay_inside_attribute_values(self):
        hostile = '\"><svg/onload=alert(1)> & \'quoted\''
        data = copy.deepcopy(self.case.data)
        data["description"] = hostile
        data["signature"]["arguments"][0]["name"] = hostile
        data["intel"]["required_isa"] = [hostile]
        html = self.render(cases=[CaseDefinition(hostile, data)])
        card = Markup(html).cards[0]
        self.assertIn(hostile, card["data-search"])
        self.assertEqual(set(card), {"class", "data-search", "data-mode", "data-element"})
        self.assertNotIn("<svg", html)

    def test_generated_timestamp_is_converted_to_utc_before_labelling(self):
        local = datetime(2026, 10, 16, 9, 10, 11, tzinfo=timezone(timedelta(hours=9)))
        html = self.render(generated_at=local)
        self.assertIn("2026-10-16 // 00:10:11 UTC", html)
        self.assertNotIn("09:10:11 UTC", html)

    def test_naive_timestamp_keeps_the_existing_utc_interpretation(self):
        html = self.render(generated_at=datetime(2026, 10, 16, 9, 10, 11))
        self.assertIn("2026-10-16 // 09:10:11 UTC", html)

    def test_quality_reference_environment_family_and_failure_fields_are_escaped(self):
        hostile = "</script><img src=x onerror=alert(1)>"
        quality = {"status": "fail", "gates": {hostile: {"status": hostile}, "python_coverage": {
            "execution_environment": {"host": hostile}, "model_findings_by_family": {hostile: 1},
            "model_oracle_references": [{"oracle": hostile, "source": hostile}],
            "test_execution_errors": [{"test_id": hostile, "exception": hostile, "message": hostile}],
        }}}
        html = self.render(quality=quality)
        self.assertNotIn(hostile, html)
        self.assertIn("&lt;/script&gt;&lt;img", html)
        self.assertEqual(html.count("<script>"), 1)

    def test_unknown_quality_status_cannot_claim_a_pass(self):
        for status in ("", "unknown", None, True):
            with self.subTest(status=status):
                html = self.render(quality={"status": status, "gates": {}})
                self.assertIn("QUALITY CHECK FAILED", html)
                self.assertIn('id="model-oracle" data-status="fail"', html)

    def test_failed_render_does_not_replace_a_previously_written_report(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "showcase.html"
            output.write_text("previous good report", encoding="utf-8")
            with self.assertRaises(ValueError):
                write_showcase_report(output, **{**self.kwargs, "summary": summary(3), "cases": [self.case, self.case]})
            self.assertEqual(output.read_text(encoding="utf-8"), "previous good report")
            self.assertEqual(list(output.parent.iterdir()), [output])


@unittest.skipUnless(shutil.which("node"), "Node.js is required to execute the browser behavior")
class BrowserBoundaryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.script = re.search(r"<script>([\s\S]*?)</script>", render_dashboard()).group(1)

    def execute(self, start, end, exports, checks, *, setup=""):
        snippet = self.script[self.script.index(start):self.script.index(end, self.script.index(start))]
        harness = """
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const code = JSON.parse(fs.readFileSync(0, 'utf8'));
const context = {URL, location: {origin: 'http://127.0.0.1:8123'}, console};
""" + setup + "\nvm.createContext(context);\nvm.runInContext(code + " + json.dumps("\nObject.assign(globalThis, {" + exports + "});") + ", context);\n" + checks
        result = subprocess.run([shutil.which("node"), "-e", harness], input=json.dumps(snippet),
                                text=True, capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

    def test_report_urls_reject_other_origins_userinfo_and_normalized_traversal(self):
        self.execute("    const reportURL =", "    const notice =", "reportURL", """
for (const value of [null, 1, {}, '', 'javascript:alert(1)', 'data:text/html,hello',
  'https://127.0.0.1:8123/reports/run/showcase.html', '//evil.test/reports/run/showcase.html',
  'http://user:pass@127.0.0.1:8123/reports/run/showcase.html', '/reports/../api/check', '/report/x']) {
  assert.equal(context.reportURL(value), null, String(value));
}
assert.equal(context.reportURL('/reports/run/showcase.html'), 'http://127.0.0.1:8123/reports/run/showcase.html');
assert.equal(context.reportURL('/reports/run/showcase.html?download=1#cases'), 'http://127.0.0.1:8123/reports/run/showcase.html?download=1#cases');
""")

    def test_fetch_json_parse_errors_and_http_status_are_preserved(self):
        self.execute("    const request =", "    const comparison =", "request", """
(async () => {
  context.fetch = async (path, options) => {assert.equal(options.cache, 'no-store'); return {ok: true, json: async () => ({job:null})};};
  assert.equal((await context.request('/api/status')).job, null);
  context.fetch = async () => ({ok:false, status:409, json:async () => ({error:'busy'})});
  await assert.rejects(context.request('/api/check'), error => error.message === 'busy' && error.status === 409);
  context.fetch = async () => ({ok:false, status:500, json:async () => null});
  await assert.rejects(context.request('/api/status'), error => error.status === 500 && error.message.length > 0);
  context.fetch = async () => ({ok:true, json:async () => {throw new SyntaxError('bad JSON');}});
  await assert.rejects(context.request('/api/status'), error => error.message.includes('応答'));
  context.fetch = async () => {throw new Error('network offline');};
  await assert.rejects(context.request('/api/status'), error => error.message === 'network offline');
})().catch(error => {console.error(error); process.exitCode = 1;});
""")

    def test_badges_keep_comparison_and_independent_quality_results_separate(self):
        self.execute("    const comparison =", "    const addBadge =", "comparison,quality", """
assert.deepEqual(Array.from(context.comparison({outcome:'pass'})), ['経路比較は一致','success']);
assert.equal(context.comparison({outcome:'mismatch'})[1], 'error');
assert.equal(context.comparison({outcome:'not_comparable'})[1], 'warning');
assert.equal(context.comparison({outcome:'unknown'})[1], 'neutral');
assert.equal(context.quality({quality_status:'fail',quality_mismatches:5})[0], 'モデル検証: 5不一致');
assert.equal(context.quality({quality_status:'fail',quality_mismatches:0})[1], 'error');
assert.equal(context.quality({quality_status:'pass'})[1], 'success');
assert.equal(context.quality({})[1], 'warning');
""", setup="context.number = value => Number(value).toLocaleString('ja-JP');")

    def test_history_refresh_preserves_selection_and_filters_bad_urls(self):
        self.execute("    const refreshReports =", "    const setBusy =", "refreshReports", """
(async () => {
  const report = id => ({id, url:'/reports/' + id + '/showcase.html'});
  context.payload = {reports:[report('new'), report('selected'), null, {url:'https://evil.test/reports/x'}]};
  assert.equal((await context.refreshReports()).id, 'selected');
  assert.equal(context.state.reports.length, 2);
  assert.equal(context.chosen.id, 'selected');
  assert.equal((await context.refreshReports('/reports/new/showcase.html')).id, 'new');
  context.state.selectedId = 'missing';
  assert.equal((await context.refreshReports()).id, 'new');
  context.payload = {reports:'corrupt'};
  assert.equal(await context.refreshReports(), undefined);
  assert.equal(context.state.reports.length, 0);
})().catch(error => {console.error(error); process.exitCode = 1;});
""", setup="""
context.state = {selectedId:'selected', reports:[]};
context.renderHistory = () => {};
context.selectReport = report => {context.chosen = report;};
context.request = async () => context.payload;
context.reportURL = value => typeof value === 'string' && value.startsWith('/reports/') ? new URL(value, context.location.origin).href : null;
""")

    def test_selecting_the_same_report_keeps_the_frame_and_sanitizes_the_download_name(self):
        self.execute("    const selectReport =", "    const reportCounts =", "selectReport", """
const hostile = {id:'../<script>結果',url:'/reports/safe/showcase.html',generated_at:'<img onerror=alert(1)>'};
context.selectReport(hostile);
assert.equal(context.state.selectedId,hostile.id); assert.equal(context.elements['report-date'].textContent,hostile.generated_at);
assert.equal(context.elements['report-frame'].loads,1); assert.equal(context.elements['report-frame'].hidden,false);
assert.equal(context.elements['download-report'].download,'EquiLane-..__script___.html');
context.selectReport(hostile); assert.equal(context.elements['report-frame'].loads,1);
assert.equal(context.items[0].pressed,'true'); assert.equal(context.items[1].pressed,'false');
context.selectReport({id:'bad',url:'javascript:alert(1)'});
assert.equal(context.state.selectedId,hostile.id); assert.equal(context.notices.length,1);
""", setup="""
const ids=['report-title','report-date','selected-badges','open-report','download-report','report-actions','report-empty','report-frame'];
context.elements=Object.fromEntries(ids.map(id=>[id,{textContent:'',hidden:true}]));
const frame=context.elements['report-frame']; frame.loads=0;
Object.defineProperty(frame,'src',{set(value){this.current=value;this.loads++;},get(){return this.current;}});
frame.getAttribute=()=>frame.current;
context.el=id=>context.elements[id]; context.state={selectedId:null}; context.notices=[];
context.notice=value=>context.notices.push(value); context.formatDate=value=>value; context.displayBadges=()=>{};
context.reportURL=value=>typeof value==='string'&&value.startsWith('/reports/')?new URL(value,context.location.origin).href:null;
context.items=['../<script>結果','another'].map(id=>({dataset:{reportId:id},setAttribute(name,value){this.pressed=value;}}));
context.document={querySelectorAll:()=>context.items};
""")

    def test_stage_localization_covers_each_architecture_and_bounds_unknown_output(self):
        self.execute("    const stageText =", "    const scheduleStatus =", "stageText", """
for (const [value, word] of [['Generate input', '入力'], ['Run Intel runner', 'Intel'],
  ['Run OpenPOWER runner', 'OpenPOWER'], ['Run ARM64 runner', 'ARM64'],
  ['Run RISC-V runner', 'RISC-V64'], ['Compare results', '比べ'],
  ['Coverage analysis', 'カバレッジ'], ['Quality gates', '品質'], ['Finalize report', 'レポート']]) {
  assert.ok(context.stageText(value).includes(word), value);
}
assert.equal(context.stageText('x'.repeat(500)).length, 220);
assert.ok(context.stageText(null).length > 0);
""")

    def test_status_retry_is_scheduled_only_while_a_job_is_busy(self):
        self.execute("    const scheduleStatus =", "    const refreshStatus =", "scheduleStatus", """
context.scheduleStatus();
assert.deepEqual(context.cancelled, [7]); assert.equal(context.scheduled.length, 0);
context.state.busy = true; context.scheduleStatus();
assert.equal(context.scheduled.length, 1); assert.equal(context.scheduled[0][1], 1000);
assert.equal(context.state.timer, 123);
""", setup="""
context.state = {timer:7,busy:false}; context.cancelled = []; context.scheduled = [];
context.clearTimeout = timer => context.cancelled.push(timer);
context.setTimeout = (callback, delay) => {context.scheduled.push([callback,delay]); return 123;};
context.refreshStatus = () => {};
""")

    def status_setup(self):
        return """
context.state = {busy:true,submitting:false,completedJob:null}; context.messages = []; context.notices=[];
context.elements = {'run-label':{textContent:''}}; context.el=id=>context.elements[id];
context.request=async()=>({job:context.job}); context.updateOutput=job=>{context.output=job;};
context.setBusy=value=>{context.state.busy=value;}; context.jobMessage=(...args)=>context.messages.push(args);
context.stageText=value=>String(value); context.scheduleStatus=()=>{context.scheduled=(context.scheduled||0)+1;};
context.notice=value=>context.notices.push(value); context.reportURL=value=>value||null;
context.reportRefreshes=0; context.refreshReports=async()=>{context.reportRefreshes++;return context.selected;};
"""

    def test_running_and_idle_status_updates_respect_an_inflight_submission(self):
        self.execute("    const refreshStatus =", "    const syncCoverage =", "refreshStatus", """
(async()=>{
  context.job={id:'running',state:'running',stage:'Run ARM64',output:'progress'};
  await context.refreshStatus(); assert.equal(context.state.busy,true); assert.equal(context.output.output,'progress');
  assert.equal(context.messages.at(-1)[1], 'Run ARM64'); assert.equal(context.reportRefreshes,0);
  context.job=null; context.state.submitting=true; const before=context.messages.length;
  await context.refreshStatus(); assert.equal(context.state.busy,true); assert.equal(context.messages.length,before);
  context.state.submitting=false; await context.refreshStatus(); assert.equal(context.state.busy,false);
  assert.equal(context.scheduled,3);
})().catch(error=>{console.error(error);process.exitCode=1;});
""", setup=self.status_setup())

    def test_completed_status_refreshes_history_once_and_preserves_quality_warning(self):
        self.execute("    const refreshStatus =", "    const syncCoverage =", "refreshStatus", """
(async()=>{
  context.job={id:'done',state:'completed',report_url:'/reports/done/showcase.html'};
  context.selected={id:'done',url:context.job.report_url,outcome:'pass',quality_status:'fail'};
  await context.refreshStatus(); assert.equal(context.state.busy,false); assert.equal(context.state.completedJob,'done');
  assert.equal(context.messages.at(-1)[2],'warning'); assert.equal(context.reportRefreshes,1);
  await context.refreshStatus(); assert.equal(context.reportRefreshes,1); assert.equal(context.messages.length,1);
  context.job={id:'another',state:'completed',report_url:'/reports/missing/showcase.html'};
  await context.refreshStatus(); assert.notEqual(context.messages.at(-1)[0],'レポートができました');
})().catch(error=>{console.error(error);process.exitCode=1;});
""", setup=self.status_setup())

    def test_failed_status_shows_the_job_error_and_history_failure_can_retry(self):
        self.execute("    const refreshStatus =", "    const syncCoverage =", "refreshStatus", """
(async()=>{
  context.job={id:'failed',state:'failed',error:'<script>real stderr</script>'};
  context.refreshReports=async()=>{throw new Error('history offline');};
  await context.refreshStatus(); assert.equal(context.state.completedJob,null); assert.equal(context.state.busy,true);
  assert.equal(context.notices.at(-1),'history offline');
  context.refreshReports=async()=>undefined;
  await context.refreshStatus(); assert.equal(context.state.completedJob,'failed'); assert.equal(context.state.busy,false);
  assert.equal(context.messages.at(-1)[1],context.job.error); assert.equal(context.messages.at(-1)[2],'error');
  assert.equal(context.scheduled,2);
})().catch(error=>{console.error(error);process.exitCode=1;});
""", setup=self.status_setup())

    def test_failed_submission_recovers_controls_and_conflict_checks_existing_status(self):
        self.execute("    const syncCoverage =", "    const connect =", "syncCoverage", """
(async () => {
  context.elements.count.value = '0'; await context.elements['check-form'].handlers.submit({preventDefault(){}});
  assert.equal(context.payloads.length, 0); assert.equal(context.elements.count.focused, true);
  context.elements.count.value = '8';
  context.errorStatus = 500; await context.elements['check-form'].handlers.submit({preventDefault(){}});
  assert.equal(context.state.busy, false); assert.equal(context.state.submitting, false);
  assert.equal(context.refreshes, 1); assert.equal(context.payloads.length, 1);
  context.errorStatus = 409; await context.elements['check-form'].handlers.submit({preventDefault(){}});
  assert.equal(context.state.busy, true); assert.equal(context.state.submitting, false);
  assert.equal(context.refreshes, 2); assert.ok(context.notices.at(-1).includes('別のチェック'));
  const before = context.payloads.length;
  await context.elements['check-form'].handlers.submit({preventDefault(){}});
  assert.equal(context.payloads.length, before);
})().catch(error => {console.error(error); process.exitCode = 1;});
""", setup="""
const ids = ['quality','coverage','profile','count','count-hint','jobs','check-form','api-notice','run-label', 'target-openpower','target-arm','target-riscv'];
context.elements = Object.fromEntries(ids.map(id => [id, {checked:false, value:'', handlers:{}, addEventListener(name, fn){this.handlers[name]=fn;},focus(){this.focused=true;}}]));
context.elements.profile.value = 'smoke'; context.elements.count.value = '8'; context.elements.jobs.value = '1'; context.elements['target-openpower'].checked = true;
context.el = id => context.elements[id]; context.state = {busy:false,submitting:false}; context.targetRoles = ['openpower','arm','riscv'];
context.defaults = {smoke:8}; context.notices = []; context.payloads = []; context.refreshes = 0;
context.notice = value => context.notices.push(value); context.jobMessage = () => {};
context.setBusy = value => {context.state.busy=value;}; context.refreshStatus = async () => {context.refreshes++;};
context.request = async (path, options) => {context.payloads.push(JSON.parse(options.body)); const error=new Error('request failed'); error.status=context.errorStatus; throw error;};
""")

    def test_catalog_search_normalizes_unicode_combines_filters_and_resets_empty_states(self):
        harness = r"""
const assert=require('node:assert/strict'); const vm=require('node:vm'); const fs=require('node:fs');
const code=JSON.parse(fs.readFileSync(0,'utf8'));
function create(cards) {
  const ids=['case-filters','case-search','case-type','case-mode','case-count','case-empty','case-reset'];
  const elements=Object.fromEntries(ids.map(id=>[id,{value:'',textContent:'',hidden:false,handlers:{},addEventListener(name,fn){this.handlers[name]=fn;},focus(){this.focused=true;}}]));
  const context={document:{getElementById:id=>elements[id],querySelectorAll:()=>cards}};
  vm.createContext(context); vm.runInContext(code,context); return elements;
}
const cards=[
 {dataset:{search:'SSE2.ＡＤＤ signed i8',element:'i8',mode:'bit_exact'},hidden:false},
 {dataset:{search:'SSE2.divide ｶﾅ f64',element:'f64',mode:'float_tolerant'},hidden:false}
];
const el=create(cards); assert.equal(el['case-count'].textContent,'2 / 2 cases'); assert.equal(el['case-empty'].hidden,true);
el['case-search'].value=' ADD   sSe2 '; el['case-search'].handlers.input();
assert.equal(cards[0].hidden,false); assert.equal(cards[1].hidden,true); assert.equal(el['case-count'].textContent,'1 / 2 cases');
el['case-type'].value='f64'; el['case-type'].handlers.change();
assert.equal(el['case-empty'].hidden,false); assert.equal(el['case-count'].textContent,'0 / 2 cases');
el['case-search'].value='カナ'; el['case-search'].handlers.input(); assert.equal(cards[1].hidden,false);
el['case-mode'].value='bit_exact'; el['case-mode'].handlers.change(); assert.equal(cards[1].hidden,true);
el['case-reset'].handlers.click(); assert.equal(el['case-search'].focused,true);
assert.equal(el['case-search'].value,''); assert.equal(el['case-type'].value,''); assert.equal(el['case-mode'].value,'');
assert.ok(cards.every(card=>!card.hidden)); assert.equal(el['case-count'].textContent,'2 / 2 cases');
let prevented=false; el['case-filters'].handlers.submit({preventDefault(){prevented=true;}}); assert.equal(prevented,true);
const empty=create([]); assert.equal(empty['case-count'].textContent,'0 / 0 cases'); assert.equal(empty['case-empty'].hidden,false);
"""
        result = subprocess.run([shutil.which("node"), "-e", harness], input=json.dumps(_CATALOG_SCRIPT),
                                text=True, capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)


if __name__ == "__main__":
    unittest.main()
