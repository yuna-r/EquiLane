"""Local dashboard server and single-job development-check runner."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import signal
import socket
import subprocess
import sys
import threading
from typing import Any
from urllib.parse import unquote, urlsplit
from uuid import uuid4


_PROFILES = {"smoke", "standard", "stress", "exhaustive-small"}
_REPORT_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z")
_STAGE = re.compile(r"^\[\d+/\d+\]\s+(.+?)(?:\.\.\.|:\s+(?:done|failed)\b.*)?$")
_MAX_OUTPUT_CHARS = 128 * 1024


@dataclass(frozen=True)
class CheckOptions:
    profile: str = "smoke"
    count_per_case: int = 8
    jobs: int | str = 1
    quality: bool = False
    coverage: bool = False

    @classmethod
    def from_json(cls, value: Any) -> CheckOptions:
        if not isinstance(value, dict):
            raise ValueError("check options must be a JSON object")
        unknown = set(value) - {"profile", "count_per_case", "jobs", "quality", "coverage"}
        if unknown:
            raise ValueError("unknown check option")
        profile = value.get("profile", "smoke")
        if not isinstance(profile, str) or profile not in _PROFILES:
            raise ValueError("profile must be smoke, standard, stress, or exhaustive-small")
        count = value.get("count_per_case", 8)
        if type(count) is not int or not 1 <= count <= 100_000:
            raise ValueError("count_per_case must be an integer between 1 and 100000")
        jobs = value.get("jobs", 1)
        if jobs != "auto" and (type(jobs) is not int or not 1 <= jobs <= 256):
            raise ValueError("jobs must be an integer between 1 and 256 or 'auto'")
        quality = value.get("quality", False)
        if type(quality) is not bool:
            raise ValueError("quality must be a boolean")
        coverage = value.get("coverage", False)
        if type(coverage) is not bool:
            raise ValueError("coverage must be a boolean")
        if coverage and not quality:
            raise ValueError("coverage requires quality checks")
        return cls(profile, count, jobs, quality, coverage)


class ReportStore:
    """Index completed report directories without exposing arbitrary files."""

    def __init__(self, root: Path):
        self.root = root.resolve()

    def report_file(self, report_id: str) -> Path | None:
        if not _REPORT_ID.fullmatch(report_id):
            return None
        candidate = self.root / report_id
        if candidate.is_symlink():
            return None
        directory = candidate.resolve()
        if directory.parent != self.root:
            return None
        report = (directory / "showcase.html").resolve()
        if report.parent != directory or not report.is_file():
            return None
        return report

    @staticmethod
    def _json_file(path: Path) -> Any:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, ValueError):
            return None

    def describe(self, report_id: str) -> dict[str, Any] | None:
        report = self.report_file(report_id)
        if report is None:
            return None
        directory = report.parent
        summary_path = directory / "comparison" / "summary.json"
        if not summary_path.resolve().is_relative_to(directory):
            return None
        summary = self._json_file(summary_path)
        if not isinstance(summary, dict):
            return None
        counters = ("record_count", "matched_inputs", "mismatched_inputs", "not_comparable_inputs")
        if any(type(summary.get(key)) is not int or summary[key] < 0 for key in counters):
            return None
        outcome = summary.get("outcome")
        if not isinstance(outcome, str) or outcome not in {"pass", "mismatch", "not_comparable"}:
            return None
        if summary["record_count"] != sum(summary[key] for key in counters[1:]):
            return None

        def metadata(path: Path) -> Any:
            return self._json_file(path) if path.resolve().is_relative_to(directory) else None

        cases = metadata(directory / "vectors" / "contracts" / "case-definitions.json")
        case_count = len(cases) if isinstance(cases, list) else None
        quality = metadata(directory / "quality" / "summary.json")
        quality_status = "unrecorded"
        quality_mismatches = None
        if isinstance(quality, dict):
            quality_status = "pass" if quality.get("status") == "pass" else "fail"
            gates = quality.get("gates")
            python_gate = gates.get("python_coverage") if isinstance(gates, dict) else None
            if isinstance(python_gate, dict):
                classification = python_gate.get("failure_classification")
                count = (
                    classification.get("portable_model_output_mismatches")
                    if isinstance(classification, dict) else None
                )
                if type(count) is int and count >= 0:
                    quality_mismatches = count
                elif isinstance(python_gate.get("model_output_mismatches"), list):
                    quality_mismatches = len(python_gate["model_output_mismatches"])
        try:
            generated_at = datetime.fromtimestamp(report.stat().st_mtime, timezone.utc).isoformat()
        except OSError:
            return None
        return {
            "id": report_id,
            "url": f"/reports/{report_id}/showcase.html",
            "generated_at": generated_at,
            "case_count": case_count,
            **{key: summary[key] for key in counters},
            "outcome": summary["outcome"],
            "quality_status": quality_status,
            "quality_mismatches": quality_mismatches,
        }

    def list_reports(self) -> list[dict[str, Any]]:
        try:
            candidates = list(self.root.iterdir())
        except OSError:
            return []
        reports = [description for candidate in candidates
                   if (description := self.describe(candidate.name)) is not None]
        return sorted(reports, key=lambda report: (report["generated_at"], report["id"]), reverse=True)


class JobConflictError(Exception):
    """A check is already running or the server is stopping."""


class CheckManager:
    """Execute the CLI in a child process and retain its latest job state."""

    def __init__(self, project: Path, reports: ReportStore):
        self.project = project.resolve()
        self.reports = reports
        self._lock = threading.Lock()
        self._job: dict[str, Any] | None = None
        self._process: subprocess.Popen[str] | None = None
        self._thread: threading.Thread | None = None
        self._stopping = False

    def status(self) -> dict[str, Any] | None:
        with self._lock:
            return None if self._job is None else dict(self._job)

    def start(self, options: CheckOptions) -> dict[str, Any]:
        with self._lock:
            if self._stopping or self._job is not None and self._job["state"] == "running":
                raise JobConflictError("a check is already running")
            report_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + "-" + uuid4().hex[:8]
            self._job = {
                "id": report_id,
                "state": "running",
                "stage": "Preparing check",
                "exit_code": None,
                "report_url": None,
                "error": None,
                "output": "",
            }
            self._thread = threading.Thread(
                target=self._run, args=(report_id, options), daemon=True,
                name=f"equilane-check-{report_id}",
            )
            self._thread.start()
            return dict(self._job)

    def _update(self, **changes: Any) -> None:
        with self._lock:
            assert self._job is not None
            self._job.update(changes)

    def _append_output(self, text: str) -> None:
        """Keep a bounded live tail of the unmodified merged CLI output."""

        with self._lock:
            assert self._job is not None
            self._job["output"] = (self._job["output"] + text)[-_MAX_OUTPUT_CHARS:]

    def _run(self, report_id: str, options: CheckOptions) -> None:
        output = self.reports.root / report_id
        command = [
            sys.executable, "-m", "equilane", "check",
            "--project", str(self.project), "--output", str(output),
            "--showcase-report", "--profile", options.profile,
            "--count-per-case", str(options.count_per_case), "--jobs", str(options.jobs),
        ]
        if options.quality:
            command.append("--quality")
        if options.coverage:
            command.append("--coverage")
        environment = os.environ.copy()
        source_root = str(Path(__file__).resolve().parents[1])
        environment["PYTHONPATH"] = source_root + os.pathsep + environment.get("PYTHONPATH", "")
        last_line = ""
        process = None
        try:
            self.reports.root.mkdir(parents=True, exist_ok=True)
            with self._lock:
                if self._stopping:
                    raise RuntimeError("server stopped")
                process = subprocess.Popen(
                    command, cwd=self.project.parent, env=environment,
                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    text=True, encoding="utf-8", errors="replace", bufsize=1,
                    start_new_session=os.name == "posix",
                )
                self._process = process
            assert process.stdout is not None
            with process.stdout:
                for line in process.stdout:
                    self._append_output(line)
                    line = line.strip()
                    if line:
                        last_line = line[-1000:]
                    stage = _STAGE.match(line)
                    if stage is not None:
                        self._update(stage=stage.group(1))
            exit_code = process.wait()
            report = self.reports.describe(report_id)
            if report is not None:
                self._update(
                    state="completed", stage="Check complete", exit_code=exit_code,
                    report_url=report["url"],
                )
            else:
                self._update(
                    state="failed", stage="Check failed", exit_code=exit_code,
                    error=last_line or "check ended without a completed report",
                )
        except (OSError, RuntimeError) as exc:
            self._update(state="failed", stage="Check failed", error=str(exc))
        finally:
            with self._lock:
                if self._process is process:
                    self._process = None

    def stop(self) -> None:
        with self._lock:
            self._stopping = True
            process = self._process
            thread = self._thread
        if process is not None and process.poll() is None:
            try:
                if os.name == "posix":
                    os.killpg(process.pid, signal.SIGTERM)
                else:
                    process.terminate()
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                if os.name == "posix":
                    os.killpg(process.pid, signal.SIGKILL)
                else:
                    process.kill()
            except ProcessLookupError:
                pass
        if thread is not None:
            thread.join(timeout=3)


class DashboardServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address: tuple[str, int], project: Path, reports_root: Path):
        if address[0] == "::1":
            self.address_family = socket.AF_INET6
        self.reports = ReportStore(reports_root)
        self.checks = CheckManager(project, self.reports)
        super().__init__(address, DashboardHandler)

    def server_close(self) -> None:
        self.checks.stop()
        super().server_close()


class DashboardHandler(BaseHTTPRequestHandler):
    server: DashboardServer

    def log_message(self, format: str, *args: Any) -> None:
        pass

    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, status: int, value: Any) -> None:
        self._send(status, json.dumps(value, ensure_ascii=False).encode("utf-8"),
                   "application/json; charset=utf-8")

    def _valid_host(self) -> bool:
        port = self.server.server_address[1]
        return self.headers.get("Host") in {
            f"127.0.0.1:{port}", f"localhost:{port}", f"[::1]:{port}",
        }

    def _path(self) -> str:
        return unquote(urlsplit(self.path).path)

    def do_GET(self) -> None:
        if not self._valid_host():
            self._json(403, {"error": "localhost Host header required"})
            return
        path = self._path()
        if path == "/":
            from .webui import render_dashboard
            self._send(200, render_dashboard().encode("utf-8"), "text/html; charset=utf-8")
        elif path == "/api/reports":
            self._json(200, {"reports": self.server.reports.list_reports()})
        elif path == "/api/status":
            self._json(200, {"job": self.server.checks.status()})
        else:
            match = re.fullmatch(r"/reports/([^/]+)/showcase\.html", path)
            report = self.server.reports.report_file(match.group(1)) if match else None
            if report is None:
                self._json(404, {"error": "not found"})
                return
            try:
                body = report.read_bytes()
            except OSError:
                self._json(404, {"error": "report not found"})
                return
            self._send(200, body, "text/html; charset=utf-8")

    def do_POST(self) -> None:
        if not self._valid_host():
            self._json(403, {"error": "localhost Host header required"})
            return
        origin = self.headers.get("Origin")
        if origin is not None and origin != "http://" + self.headers["Host"]:
            self._json(403, {"error": "same-origin request required"})
            return
        if self.headers.get("Sec-Fetch-Site") in {"cross-site", "same-site"}:
            self._json(403, {"error": "same-origin request required"})
            return
        if self._path() != "/api/check":
            self._json(404, {"error": "not found"})
            return
        if self.headers.get("Content-Type", "").split(";", 1)[0].strip() != "application/json":
            self._json(415, {"error": "application/json required"})
            return
        if self.headers.get("Transfer-Encoding") is not None:
            self._json(400, {"error": "chunked requests are not supported"})
            return
        try:
            length = int(self.headers.get("Content-Length", ""))
        except ValueError:
            self._json(411, {"error": "Content-Length required"})
            return
        if not 0 < length <= 4096:
            self._json(413, {"error": "request body must be between 1 and 4096 bytes"})
            return
        try:
            options = CheckOptions.from_json(json.loads(self.rfile.read(length)))
        except (UnicodeError, ValueError) as exc:
            self._json(400, {"error": str(exc)})
            return
        try:
            job = self.server.checks.start(options)
        except JobConflictError as exc:
            self._json(409, {"error": str(exc)})
            return
        self._json(202, {"job": job})


def create_server(
    *,
    host: str = "127.0.0.1",
    port: int = 0,
    project: Path,
    reports_root: Path,
) -> DashboardServer:
    """Create a loopback-only server; port zero selects an available port."""

    if host not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("dashboard host must be 127.0.0.1, localhost, or ::1")
    if type(port) is not int or not 0 <= port <= 65535:
        raise ValueError("dashboard port must be an integer between 0 and 65535")
    return DashboardServer((host, port), Path(project), Path(reports_root))


def serve(*, host: str, port: int, project: Path, reports_root: Path) -> None:
    with create_server(host=host, port=port, project=project, reports_root=reports_root) as server:
        bound_host, bound_port = server.server_address[:2]
        authority = f"[{bound_host}]:{bound_port}" if ":" in bound_host else f"{bound_host}:{bound_port}"
        print(f"EquiLane dashboard: http://{authority}/", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
