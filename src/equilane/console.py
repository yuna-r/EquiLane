"""Terminal progress and human-readable verification metrics.

These displays write to stderr by default so command results remain valid JSON.
"""

from __future__ import annotations

from contextlib import contextmanager
import sys
import time
from typing import Callable, Iterator, TextIO

from .metrics import VerificationMetrics
from .quality import QualityGateUpdate, QualityMetrics, QualityRun


def print_verification_metrics(
    metrics: VerificationMetrics, *, stream: TextIO | None = None
) -> None:
    destination = sys.stderr if stream is None else stream
    rate = f"{metrics.match_rate:.2f}".rstrip("0").rstrip(".") + "%"
    rows = (
        ("cases", f"{metrics.case_count:,}"),
        ("trials", f"{metrics.trials:,}"),
        (
            "implementation-path evaluations",
            f"{metrics.implementation_path_evaluations:,}",
        ),
        ("lane verdicts", f"{metrics.lane_verdicts:,}"),
        ("bit positions", f"{metrics.bit_positions:,}"),
        ("match rate", rate),
        ("mismatch", f"{metrics.mismatched_inputs:,}"),
    )
    width = max(len(label) for label, _value in rows)
    print("\nVerification metrics", file=destination)
    for label, value in rows:
        print(f"  {label:<{width}}  {value:>12}", file=destination)
    destination.flush()


def print_quality_metrics(
    metrics: QualityMetrics,
    *,
    deep: QualityRun | None = None,
    stream: TextIO | None = None,
) -> None:
    destination = sys.stderr if stream is None else stream
    bindings = metrics.cases * 2
    rows = [
        ("valid contracts", f"{metrics.valid_contracts:,} / {metrics.cases:,}"),
        ("portable models", f"{metrics.development_models:,} / {metrics.cases:,}"),
        (
            "standard boundary floors",
            f"{metrics.standard_boundary_floors:,} / {metrics.cases:,}",
        ),
        (
            "architecture bindings",
            f"{metrics.architecture_bindings:,} / {bindings:,}",
        ),
        ("contract drift", "0"),
    ]
    if deep is not None:
        if deep.tests_run is not None:
            rows.append(("regression tests", f"{deep.tests_run:,} executed"))
        if deep.coverage_percent is not None:
            rows.append(("source line coverage", deep.coverage_percent + "%"))
        else:
            rows.append(("source line coverage", "not collected"))
        rows.append(
            ("deep quality gates", f"{deep.passed_gates:,} / {deep.total_gates:,}")
        )
    width = max(len(label) for label, _value in rows)
    print("\nQuality metrics", file=destination)
    for label, value in rows:
        print(f"  {label:<{width}}  {value:>12}", file=destination)
    destination.flush()


class CheckProgress:
    """Small dependency-free progress display that leaves stdout machine-readable."""

    _BAR_WIDTH = 22

    def __init__(
        self,
        *,
        enabled: bool,
        steps: int = 7,
        stream: TextIO | None = None,
    ):
        self.enabled = enabled
        self.steps = steps
        self.stream = sys.stderr if stream is None else stream
        isatty = getattr(self.stream, "isatty", None)
        self.interactive = bool(isatty is not None and isatty())
        self._step = 0
        self._label = ""
        self._total: int | None = None
        self._current = 0
        self._last_percent = -1
        self._last_width = 0
        self._started = 0.0

    @contextmanager
    def stage(
        self, step: int, label: str, *, total: int | None = None
    ) -> Iterator[Callable[[int], None]]:
        if not self.enabled:
            yield lambda _current: None
            return
        self._step = step
        self._label = label
        self._total = total
        self._current = 0
        self._last_percent = -1
        self._started = time.monotonic()
        if self.interactive:
            self._render(force=True)
        else:
            print(f"[{step}/{self.steps}] {label}...", file=self.stream, flush=True)
            self._render_noninteractive()
        try:
            yield self.update
        except BaseException:
            self._finish(success=False)
            raise
        else:
            self._finish(success=True)

    def update(self, current: int) -> None:
        if not self.enabled or self._total is None:
            return
        self._current = max(0, min(current, self._total))
        if self.interactive:
            self._render()
        else:
            self._render_noninteractive()

    def open_details(self) -> None:
        """Move nested progress below the current interactive stage line."""

        if not self.enabled or not self.interactive:
            return
        self._render(force=True)
        self.stream.write("\n")
        self.stream.flush()
        self._last_width = 0

    def _format_line(self) -> str:
        prefix = f"[{self._step}/{self.steps}] {self._label:<25}"
        if self._total is None:
            return prefix.rstrip()
        percent = 100 if self._total == 0 else self._current * 100 // self._total
        filled = percent * self._BAR_WIDTH // 100
        bar = "=" * filled + "-" * (self._BAR_WIDTH - filled)
        return (
            f"{prefix} [{bar}] {percent:3d}% "
            f"{self._current:,}/{self._total:,}"
        )

    def _render(self, *, force: bool = False) -> None:
        if self._total is not None:
            percent = 100 if self._total == 0 else self._current * 100 // self._total
            if not force and percent == self._last_percent:
                return
            self._last_percent = percent
        line = self._format_line()
        self.stream.write("\r" + line.ljust(self._last_width))
        self.stream.flush()
        self._last_width = max(self._last_width, len(line))

    def _render_noninteractive(self) -> None:
        """Emit real counts when crossing a ten-percent bucket."""

        if self._total is None:
            return
        percent = 100 if self._total == 0 else self._current * 100 // self._total
        if percent // 10 <= self._last_percent // 10:
            return
        self._last_percent = percent
        print(self._format_line(), file=self.stream, flush=True)

    def _finish(self, *, success: bool) -> None:
        elapsed = time.monotonic() - self._started
        if success and self._total is not None:
            self._current = self._total
        suffix = f"  {'done' if success else 'failed'} {elapsed:.1f}s"
        if self.interactive:
            line = self._format_line() + suffix
            self.stream.write("\r" + line.ljust(self._last_width) + "\n")
            self.stream.flush()
            self._last_width = 0
            return
        self._render_noninteractive()
        state = "done" if success else "failed"
        print(
            f"[{self._step}/{self.steps}] {self._label}: {state} ({elapsed:.1f}s)",
            file=self.stream,
            flush=True,
        )


class QualityGateProgress:
    """Three persistent child rows for the opt-in quality gates."""

    def __init__(
        self,
        *,
        enabled: bool,
        interactive: bool,
        stream: TextIO,
    ) -> None:
        self.enabled = enabled
        self.interactive = interactive
        self.stream = stream
        self._active_width = 0
        self._started: dict[int, float] = {}
        self._last_percent: dict[int, int] = {}

    def update(self, update: QualityGateUpdate) -> None:
        if not self.enabled:
            return
        if update.gate not in self._started:
            self._started[update.gate] = time.monotonic()
        percent = (
            100
            if update.total == 0
            else min(100, update.current * 100 // update.total)
        )
        filled = percent * 18 // 100
        bar = "=" * filled + "-" * (18 - filled)
        suffix = ""
        if update.state != "running":
            elapsed = time.monotonic() - self._started[update.gate]
            suffix = f"  {update.state.upper()} ({elapsed:.1f}s)"
        line = (
            f"  [{update.gate}/{update.gates}] {update.label:<36} "
            f"[{bar}] {percent:3d}% "
            f"{update.current:,}/{update.total:,}{suffix}"
        )
        if self.interactive:
            padding = " " * max(0, self._active_width - len(line))
            if update.state == "running":
                self.stream.write(("\r" if self._active_width else "") + line + padding)
                self._active_width = max(self._active_width, len(line))
            else:
                self.stream.write("\r" + line + padding + "\n")
                self._active_width = 0
            self.stream.flush()
            return
        last_percent = self._last_percent.get(update.gate, -10)
        if (
            update.state != "running"
            or percent == 0
            or percent == 100
            or percent >= last_percent + 10
        ):
            print(line, file=self.stream, flush=True)
            self._last_percent[update.gate] = percent

    def close(self) -> None:
        if self.enabled and self.interactive and self._active_width:
            self.stream.write("\n")
            self.stream.flush()
            self._active_width = 0
