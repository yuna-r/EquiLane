"""Artifact comparison workflows independent of command-line output."""

from __future__ import annotations

import copy
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .artifacts import (
    InputArtifact,
    ResultArtifact,
    validate_input_artifact,
    validate_result_artifact,
)
from .canonical import JSONValue, atomic_write, dump_bytes
from .cases import CaseRegistry
from .compare import compare_result_record_set
from .errors import (
    EXIT_MATCH,
    EXIT_MISMATCH,
    EXIT_RUNNER,
    EXIT_UNSUPPORTED,
    ReferenceOracleError,
    RunnerError,
    UnsupportedError,
    ValidationError,
)
from .isa import ISARegistry
from .records import derive_input_id
from .report import RecordReport, ReportFiles, write_failure_bundle, write_reports


@dataclass(frozen=True)
class ComparisonContext:
    """Validated contract and artifact snapshots for one comparison."""

    cases: CaseRegistry
    isa_registry: ISARegistry
    input_artifact: InputArtifact
    intel_artifact: ResultArtifact
    openpower_artifact: ResultArtifact


@dataclass(frozen=True)
class ComparisonRun:
    """Completed reports and the same verdict used by the CLI."""

    files: ReportFiles
    record_count: int
    mismatched_inputs: int
    not_comparable_inputs: int
    exit_code: int

    def as_json(self) -> dict[str, JSONValue]:
        return {
            "junit": str(self.files.junit_path),
            "mismatched_inputs": self.mismatched_inputs,
            "not_comparable_inputs": self.not_comparable_inputs,
            "record_count": self.record_count,
            "status": (
                "not_comparable"
                if self.not_comparable_inputs
                else "mismatch" if self.mismatched_inputs else "match"
            ),
            "summary": str(self.files.summary_path),
        }


def load_comparison_context(
    *,
    cases: CaseRegistry,
    isa_registry: ISARegistry,
    input_manifest: str | Path,
    intel_manifest: str | Path,
    openpower_manifest: str | Path,
) -> ComparisonContext:
    """Validate completed artifacts, classifying bad runner output separately."""

    inputs = validate_input_artifact(input_manifest, cases, isa_registry)

    def load_result(manifest: str | Path, label: str) -> ResultArtifact:
        try:
            return validate_result_artifact(
                manifest, cases, isa_registry, input_artifact=inputs
            )
        except ValidationError as exc:
            raise RunnerError(f"invalid {label} result artifact: {exc}") from exc

    return ComparisonContext(
        cases,
        isa_registry,
        inputs,
        load_result(intel_manifest, "Intel"),
        load_result(openpower_manifest, "OpenPOWER"),
    )


def _check_reference_oracle(context: ComparisonContext, output: Path) -> None:
    inputs = context.input_artifact
    intel = context.intel_artifact
    inputs_by_id = {str(record["input_id"]): record for record in inputs.records}

    def fail(input_record: dict[str, JSONValue], regression_id: str, reason: str) -> None:
        vectors = inputs.manifest["test_vectors"]
        results = intel.manifest["results"]
        assert isinstance(vectors, dict) and isinstance(results, dict)
        reference_error: dict[str, JSONValue] = {
            "artifact_type": "ioitf.reference-error",
            "case_definitions_sha256": inputs.manifest["case_definitions_sha256"],
            "input_id": input_record["input_id"],
            "input_sha256": vectors["sha256"],
            "intel_results_sha256": results["sha256"],
            "regression_id": regression_id,
            "schema_version": 1,
        }
        output.mkdir(parents=True, exist_ok=True)
        atomic_write(output / "reference-error.json", dump_bytes(reference_error, newline=True))
        raise ReferenceOracleError(reason)

    for input_record in inputs.records:
        generation = input_record["generation"]
        assert isinstance(generation, dict)
        if generation["class"] != "regression":
            continue
        regression_id = str(generation["regression_id"])
        case = context.cases.get(str(input_record["case_id"]))
        regressions = case.data["regressions"]
        assert isinstance(regressions, dict)
        regression = regressions[regression_id]
        assert isinstance(regression, dict)
        expected = regression["expected_intel"]
        actual_record = intel.records[str(input_record["input_id"])]
        actual: dict[str, JSONValue] = {"status": actual_record["status"]}
        if actual_record["status"] == "ok":
            actual["observed"] = actual_record["observed"]
        if actual == expected:
            environment = input_record["environment"]
            assert isinstance(environment, dict)
            if environment["rounding"] == "nearest_even":
                continue

            nearest_record = copy.deepcopy(input_record)
            nearest_environment = nearest_record["environment"]
            assert isinstance(nearest_environment, dict)
            nearest_environment["rounding"] = "nearest_even"
            nearest_id = derive_input_id(nearest_record)
            nearest_input = inputs_by_id.get(nearest_id)
            if nearest_input is None:
                fail(
                    input_record,
                    regression_id,
                    f"rounding witness {input_record['input_id']} has no nearest-even pair",
                )
            nearest_result = intel.records[nearest_id]
            if nearest_result["status"] != "ok":
                fail(
                    input_record,
                    regression_id,
                    f"nearest-even baseline {nearest_id} did not produce an Intel value",
                )
            expected_observed = expected["observed"]
            nearest_observed = nearest_result["observed"]
            assert isinstance(expected_observed, dict) and isinstance(nearest_observed, dict)
            expected_value = {
                key: expected_observed[key]
                for key in ("buffers", "return")
                if key in expected_observed
            }
            nearest_value = {
                key: nearest_observed[key]
                for key in ("buffers", "return")
                if key in nearest_observed
            }
            if expected_value == nearest_value:
                fail(
                    input_record,
                    regression_id,
                    f"rounding witness {input_record['input_id']} does not differ "
                    "from its nearest-even Intel baseline",
                )
            continue
        fail(
            input_record,
            regression_id,
            f"Intel regression oracle failed for {input_record['input_id']}",
        )


def compare_artifacts(
    context: ComparisonContext,
    *,
    output: str | Path,
    allow_development_fixtures: bool = False,
    jobs: int = 1,
    progress: Callable[[int], None] | None = None,
) -> ComparisonRun:
    """Compare validated snapshots and write reports without terminal output.

    Artifact validation happens in ``load_comparison_context`` or in the
    generator/fixture that supplies the snapshots. Completed comparison paths
    cannot be reused, including paths containing a failed reference oracle.
    """

    inputs = context.input_artifact
    intel = context.intel_artifact
    openpower = context.openpower_artifact
    if intel.role != "intel" or openpower.role != "openpower":
        raise ValidationError("result artifact roles do not match their command arguments")
    if (
        intel.development_fixture or openpower.development_fixture
    ) and not allow_development_fixtures:
        raise UnsupportedError(
            "development fixture artifacts require --allow-development-fixtures"
        )

    destination = Path(output)
    for completion_marker in ("reference-error.json", "summary.json"):
        if (destination / completion_marker).exists():
            raise ValidationError(
                f"comparison output already contains {completion_marker}; use a fresh directory"
            )
    _check_reference_oracle(context, destination)
    reports: list[RecordReport] = []
    saw_unsupported = False
    comparisons = compare_result_record_set(
        cases=context.cases,
        input_records=inputs.records,
        intel_records=intel.records,
        openpower_records=openpower.records,
        jobs=jobs,
        progress=progress,
    )
    for input_record, comparison in zip(inputs.records, comparisons):
        input_id = str(input_record["input_id"])
        intel_record = intel.records[input_id]
        power_record = openpower.records[input_id]
        saw_unsupported = saw_unsupported or (
            intel_record["status"] == "unsupported"
            or power_record["status"] == "unsupported"
        )
        case = context.cases.get(str(input_record["case_id"]))
        failure_path: str | None = None
        if comparison.outcome == "mismatch":
            bundle = write_failure_bundle(
                output=destination,
                input_artifact=inputs,
                intel_artifact=intel,
                openpower_artifact=openpower,
                cases=context.cases,
                isa_registry=context.isa_registry,
                input_record=input_record,
                comparison=comparison,
            )
            failure_path = str(bundle.relative_to(destination) / "failure.json")
        reports.append(RecordReport(case.id, input_id, comparison, failure_path))

    files = write_reports(destination, reports)
    mismatch_count = sum(item.comparison.outcome == "mismatch" for item in reports)
    not_comparable = sum(
        item.comparison.outcome == "not_comparable" for item in reports
    )
    if saw_unsupported:
        exit_code = EXIT_UNSUPPORTED
    elif not_comparable:
        exit_code = EXIT_RUNNER
    else:
        exit_code = EXIT_MISMATCH if mismatch_count else EXIT_MATCH
    return ComparisonRun(files, len(reports), mismatch_count, not_comparable, exit_code)
