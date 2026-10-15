"""EquiLane command-line entry point."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import os
from pathlib import Path
import sys
from typing import Sequence

from .branding import COMMAND, DESCRIPTION, NAME, OUTPUT_DIRECTORY
from .canonical import JSONValue, dumps
from .cases import CaseRegistry, load_case_definitions
from .comparison import ComparisonContext, compare_artifacts, load_comparison_context
from .console import (
    CheckProgress,
    QualityGateProgress,
    print_quality_metrics,
    print_verification_metrics,
)
from .errors import (
    EXIT_MATCH,
    EXIT_MISMATCH,
    EXIT_RUNNER,
    EXIT_SPECIFICATION,
    EXIT_UNSUPPORTED,
    EquiLaneError,
    RunnerError,
    UnsupportedError,
    ValidationError,
)
from .generator import DEFAULT_SEED, PROFILE_COUNTS, generate_artifact
from .isa import ISARegistry, load_isa_registry
from .metrics import collect_verification_metrics
from .project import load_project
from .quality import (
    QualityRun,
    collect_quality_metrics,
    run_quality_gates,
)


def _parse_jobs(value: str) -> int:
    if value == "auto":
        return max(1, os.cpu_count() or 1)
    try:
        jobs = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("jobs must be a positive integer or 'auto'") from exc
    if jobs < 1:
        raise argparse.ArgumentTypeError("jobs must be at least 1")
    return jobs


def _contract_paths(args: argparse.Namespace) -> tuple[Path, Path]:
    cases_path = args.cases
    isa_path = args.isa_registry
    if cases_path is None or isa_path is None:
        project = load_project(args.project)
        cases_path = project.suite if cases_path is None else cases_path
        isa_path = project.isa_registry if isa_path is None else isa_path
    return cases_path, isa_path


def _contracts(args: argparse.Namespace) -> tuple[CaseRegistry, ISARegistry]:
    cases_path, isa_path = _contract_paths(args)
    isa = load_isa_registry(isa_path)
    cases = load_case_definitions(cases_path, isa_registry=isa)
    return cases, isa


def _print(value: JSONValue) -> None:
    print(dumps(value))


def _validate_cases(args: argparse.Namespace) -> int:
    cases_path, isa_path = _contract_paths(args)
    isa = load_isa_registry(isa_path)
    cases = load_case_definitions(cases_path, isa_registry=isa)
    _print(
        {
            "case_count": len(cases),
            "case_definitions_sha256": cases.sha256,
            "case_ids": list(cases.ids),
            "isa_registry_sha256": isa.sha256,
            "status": "valid",
        }
    )
    return EXIT_MATCH


def _generate_vectors(args: argparse.Namespace) -> int:
    cases_path, isa_path = _contract_paths(args)
    isa = load_isa_registry(isa_path)
    cases = load_case_definitions(cases_path, isa_registry=isa)
    result = generate_artifact(
        cases=cases,
        isa_registry=isa,
        output=args.output,
        profile=args.profile,
        count_per_case=args.count_per_case,
        seed=args.seed,
        jobs=args.jobs,
    )
    _print(
        {
            "case_definitions_sha256": result.case_definitions_sha256,
            "manifest": str(result.manifest_path),
            "record_count": result.record_count,
            "sha256": result.sha256,
            "status": "generated",
            "used_isa_contract_sha256": result.used_isa_contract.sha256,
        }
    )
    return EXIT_MATCH


def _validate_artifact(args: argparse.Namespace) -> int:
    from .artifacts import validate_input_artifact, validate_result_artifact

    cases, isa = _contracts(args)
    if args.kind == "input":
        artifact = validate_input_artifact(args.manifest, cases, isa)
        _print(
            {
                "kind": "input",
                "manifest": str(artifact.manifest_path),
                "record_count": len(artifact.records),
                "status": "valid",
            }
        )
        return EXIT_MATCH
    if args.input is None:
        raise ValidationError("--input is required when validating a result artifact")
    input_artifact = validate_input_artifact(args.input, cases, isa)
    try:
        artifact = validate_result_artifact(
            args.manifest, cases, isa, input_artifact=input_artifact
        )
    except ValidationError as exc:
        raise RunnerError(f"invalid runner result artifact: {exc}") from exc
    _print(
        {
            "development_fixture": artifact.development_fixture,
            "kind": "result",
            "manifest": str(artifact.manifest_path),
            "record_count": len(artifact.records),
            "role": artifact.role,
            "status": "valid",
        }
    )
    return EXIT_MATCH


def _fixture_run(args: argparse.Namespace) -> int:
    from .artifacts import validate_input_artifact, validate_result_artifact
    from .fixture import run_fixture

    if not args.i_understand_this_is_not_native_evidence:
        raise UnsupportedError(
            "fixture-run requires --i-understand-this-is-not-native-evidence"
        )
    cases, isa = _contracts(args)
    input_artifact = validate_input_artifact(args.input, cases, isa)
    result = run_fixture(
        input_artifact=input_artifact,
        cases=cases,
        isa_registry=isa,
        role=args.role,
        output=args.output,
        jobs=args.jobs,
    )
    try:
        validated = validate_result_artifact(
            result.manifest_path,
            cases,
            isa,
            input_artifact=input_artifact,
        )
    except ValidationError as exc:
        raise RunnerError(f"fixture produced an invalid result artifact: {exc}") from exc
    if not validated.development_fixture:
        raise ValidationError("fixture output did not retain its development marker")
    _print(
        {
            "manifest": str(result.manifest_path),
            "native_evidence": False,
            "record_count": result.record_count,
            "role": args.role,
            "sha256": result.sha256,
            "status": "fixture_complete",
        }
    )
    return EXIT_MATCH


def _compare_results(args: argparse.Namespace) -> int:
    cases, isa = _contracts(args)
    context = load_comparison_context(
        cases=cases,
        isa_registry=isa,
        input_manifest=args.input,
        intel_manifest=args.intel,
        openpower_manifest=args.openpower,
    )
    result = compare_artifacts(
        context,
        output=args.output,
        allow_development_fixtures=args.allow_development_fixtures,
        jobs=args.jobs,
    )
    _print(result.as_json())
    return result.exit_code


def _verify_replay(args: argparse.Namespace) -> int:
    from .replay import load_failure_bundle, verify_replay_artifacts

    try:
        bundle = load_failure_bundle(args.failure)
    except ValidationError as exc:
        _print(
            {
                "reason": str(exc),
                "stage": "bundle_validation",
                "status": "invalid_bundle",
            }
        )
        return EXIT_SPECIFICATION

    try:
        intel_manifest = (
            args.intel
            if args.intel.is_absolute()
            else bundle.root / args.intel
        )
        openpower_manifest = (
            args.openpower
            if args.openpower.is_absolute()
            else bundle.root / args.openpower
        )
        verification, intel, power = verify_replay_artifacts(
            bundle,
            intel_manifest=intel_manifest,
            openpower_manifest=openpower_manifest,
        )
    except ValidationError as exc:
        _print(
            {
                "input_id": bundle.failure["input_id"],
                "reason": str(exc),
                "stage": "replay_result_validation",
                "status": "runner_error",
            }
        )
        return EXIT_RUNNER

    development_artifacts = (
        bundle.baseline_intel.development_fixture,
        bundle.baseline_openpower.development_fixture,
        intel.development_fixture,
        power.development_fixture,
    )
    if any(development_artifacts) and not args.allow_development_fixtures:
        _print(
            {
                "input_id": bundle.failure["input_id"],
                "reason": "development fixtures require --allow-development-fixtures",
                "status": "unsupported",
            }
        )
        return EXIT_UNSUPPORTED

    result: dict[str, JSONValue] = {
        "environment_differences": verification.environment_differences,
        "input_id": bundle.failure["input_id"],
        "replay_comparison": verification.replay_comparison,
        "result_differences": verification.result_differences,
        "status": "reproduced" if verification.reproduced else "not_reproduced",
    }
    _print(result)
    return EXIT_MATCH if verification.reproduced else EXIT_MISMATCH


def _development_check(args: argparse.Namespace) -> int:
    """Run the complete non-native authoring loop with one command."""

    from .fixture import run_fixture

    if args.coverage and not args.quality:
        raise ValidationError("--coverage requires --quality")
    started_at = datetime.now(timezone.utc)
    progress = CheckProgress(enabled=not args.quiet, steps=8 if args.quality else 7)
    with progress.stage(1, "Prepare suite"):
        cases_path, isa_path = _contract_paths(args)
        isa = load_isa_registry(isa_path)
        cases = load_case_definitions(cases_path, isa_registry=isa)
        if args.output is None:
            stamp = started_at.strftime("%Y%m%dT%H%M%S%fZ")
            output = Path(OUTPUT_DIRECTORY) / "checks" / stamp
        else:
            output = args.output
        if output.exists():
            raise ValidationError(
                f"check output already exists; choose a fresh path: {output}"
            )

    requested_count = (
        PROFILE_COUNTS[args.profile]
        if args.count_per_case is None
        else args.count_per_case
    )
    expected_records = (
        len(cases) * requested_count if requested_count > 0 else None
    )
    with progress.stage(
        2, "Generate test vectors", total=expected_records
    ) as update:
        generated = generate_artifact(
            cases=cases,
            isa_registry=isa,
            output=output / "vectors",
            profile=args.profile,
            count_per_case=args.count_per_case,
            seed=args.seed,
            jobs=args.jobs,
            progress=update,
            retain_records=True,
        )
        inputs = generated.as_validated_artifact()

    with progress.stage(
        3, "Run Intel fixture", total=generated.record_count
    ) as update:
        intel = run_fixture(
            input_artifact=inputs,
            cases=cases,
            isa_registry=isa,
            role="intel",
            output=output / "intel",
            jobs=args.jobs,
            progress=update,
        )
        intel_artifact = intel.as_validated_artifact()

    with progress.stage(
        4, "Run OpenPOWER fixture", total=generated.record_count
    ) as update:
        openpower = run_fixture(
            input_artifact=inputs,
            cases=cases,
            isa_registry=isa,
            role="openpower",
            output=output / "openpower",
            jobs=args.jobs,
            progress=update,
        )
        openpower_artifact = openpower.as_validated_artifact()

    comparison = output / "comparison"
    with progress.stage(
        5, "Validate + compare results", total=generated.record_count
    ) as update:
        compared = compare_artifacts(
            ComparisonContext(cases, isa, inputs, intel_artifact, openpower_artifact),
            output=comparison,
            allow_development_fixtures=True,
            progress=update,
            jobs=args.jobs,
        )
        exit_code = compared.exit_code

    report_label = (
        "Build showcase report" if args.showcase_report and not args.quality else "Finalize artifacts"
    )
    with progress.stage(6, report_label):
        from .canonical import read_canonical_json

        summary = read_canonical_json(comparison / "summary.json")
        assert isinstance(summary, dict)
        metrics = collect_verification_metrics(cases, summary)
        result: dict[str, JSONValue] = {
            "artifacts": str(output),
            "case_count": len(cases),
            "development_fixture": True,
            "jobs": args.jobs,
            "native_evidence": False,
            "record_count": generated.record_count,
            "status": summary["outcome"],
        }
        def build_showcase(quality: dict[str, JSONValue] | None = None) -> None:
            from .showcase import write_showcase_report

            showcase = write_showcase_report(
                output / "showcase.html",
                cases=cases,
                summary=summary,
                profile=args.profile,
                seed=args.seed,
                vector_sha256=generated.sha256,
                case_definitions_sha256=generated.case_definitions_sha256,
                isa_contract_sha256=generated.used_isa_contract.sha256,
                generated_at=started_at,
                native_evidence=False,
                quality=quality,
            )
            result["showcase_report"] = str(showcase)

        if args.showcase_report and not args.quality:
            build_showcase()

    quality_metrics = collect_quality_metrics(cases)
    quality_run: QualityRun | None = None
    if args.quality:
        with progress.stage(7, "Run quality gates"):
            project_root = Path(args.project).resolve().parent
            progress.open_details()
            gate_progress = QualityGateProgress(
                enabled=progress.enabled,
                interactive=progress.interactive,
                stream=progress.stream,
            )
            try:
                quality_run = run_quality_gates(
                    project_root,
                    output / "quality",
                    jobs=args.jobs,
                    progress=gate_progress.update,
                    coverage=args.coverage,
                )
            finally:
                gate_progress.close()
            result["quality_report"] = str(quality_run.report_path)
            result["quality_status"] = quality_run.status
            if quality_run.status != "pass":
                result["status"] = "quality_failed"

    status = str(summary["outcome"]).upper()
    if quality_run is not None and quality_run.status != "pass":
        status = "QUALITY FAILED"
    final_step = 8 if args.quality else 7
    with progress.stage(final_step, f"{status} - {generated.record_count:,} trials"):
        if args.showcase_report and quality_run is not None:
            quality_summary = read_canonical_json(quality_run.report_path)
            assert isinstance(quality_summary, dict)
            build_showcase(quality_summary)
    if not args.quiet:
        print_verification_metrics(metrics)
        print_quality_metrics(quality_metrics, deep=quality_run)
    _print(result)
    if quality_run is not None and quality_run.status != "pass" and exit_code == EXIT_MATCH:
        return EXIT_MISMATCH
    return exit_code


def _add_contract_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--project",
        type=Path,
        help="project file (default: equilane.toml, falling back to ioitf.toml)",
    )
    parser.add_argument("--cases", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--isa-registry", type=Path, help=argparse.SUPPRESS)


def _serve_dashboard(args: argparse.Namespace) -> int:
    from .server import serve

    project = load_project(args.project)
    try:
        serve(
            host="127.0.0.1",
            port=args.port,
            project=project.path.resolve(),
            reports_root=args.reports.resolve(),
        )
    except KeyboardInterrupt:
        return EXIT_MATCH
    return EXIT_MATCH


def _parse_port(value: str) -> int:
    try:
        port = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("port must be an integer") from exc
    if not 1 <= port <= 65535:
        raise argparse.ArgumentTypeError("port must be between 1 and 65535")
    return port


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=COMMAND,
        description=f"{NAME} — {DESCRIPTION}",
    )
    subcommands = parser.add_subparsers(dest="command", required=True)

    dashboard = subcommands.add_parser("serve", help="serve the local report dashboard")
    dashboard.add_argument("--project", type=Path, help="suite project file")
    dashboard.add_argument("--port", type=_parse_port, default=8765)
    dashboard.add_argument(
        "--reports", type=Path, default=Path(OUTPUT_DIRECTORY) / "checks",
        help="directory containing generated reports",
    )
    dashboard.set_defaults(handler=_serve_dashboard)

    check = subcommands.add_parser(
        "check",
        help="run the complete development-only authoring loop",
    )
    _add_contract_arguments(check)
    check.add_argument("--output", type=Path)
    check.add_argument(
        "--profile",
        choices=("smoke", "standard", "exhaustive-small", "stress"),
        default="smoke",
    )
    check.add_argument("--count-per-case", type=int, default=8)
    check.add_argument("--seed", default=DEFAULT_SEED)
    check.add_argument(
        "-j",
        "--jobs",
        default=1,
        type=_parse_jobs,
        help="case-level worker processes (positive integer or 'auto')",
    )
    check.add_argument(
        "--showcase-report",
        action="store_true",
        help="write a self-contained presentation-style HTML report",
    )
    check.add_argument(
        "--quality",
        action="store_true",
        help="run independent tests, sanitizer, and cross-build quality gates",
    )
    check.add_argument(
        "--coverage",
        action="store_true",
        help="collect source line coverage while running --quality",
    )
    check.add_argument(
        "--quiet",
        action="store_true",
        help="suppress progress on stderr; final JSON still goes to stdout",
    )
    check.set_defaults(handler=_development_check)

    validate_cases = subcommands.add_parser("validate-cases")
    _add_contract_arguments(validate_cases)
    validate_cases.set_defaults(handler=_validate_cases)

    generate = subcommands.add_parser("generate-vectors")
    _add_contract_arguments(generate)
    generate.add_argument("--output", required=True, type=Path)
    generate.add_argument(
        "--profile",
        choices=("smoke", "standard", "exhaustive-small", "stress"),
        default="smoke",
    )
    generate.add_argument("--count-per-case", type=int)
    generate.add_argument("--seed", default=DEFAULT_SEED)
    generate.add_argument(
        "-j", "--jobs", default=1, type=_parse_jobs,
        help="case-level worker processes (positive integer or 'auto')",
    )
    generate.set_defaults(handler=_generate_vectors)

    validate = subcommands.add_parser("validate-artifact")
    _add_contract_arguments(validate)
    validate.add_argument("--kind", choices=("input", "result"), required=True)
    validate.add_argument("--manifest", required=True, type=Path)
    validate.add_argument("--input", type=Path)
    validate.set_defaults(handler=_validate_artifact)

    fixture = subcommands.add_parser("fixture-run")
    _add_contract_arguments(fixture)
    fixture.add_argument("--input", required=True, type=Path)
    fixture.add_argument("--role", choices=("intel", "openpower"), required=True)
    fixture.add_argument("--output", required=True, type=Path)
    fixture.add_argument(
        "-j", "--jobs", default=1, type=_parse_jobs,
        help="case-level worker processes (positive integer or 'auto')",
    )
    fixture.add_argument(
        "--i-understand-this-is-not-native-evidence",
        action="store_true",
        dest="i_understand_this_is_not_native_evidence",
    )
    fixture.set_defaults(handler=_fixture_run)

    compare = subcommands.add_parser("compare-results")
    _add_contract_arguments(compare)
    compare.add_argument("--input", required=True, type=Path)
    compare.add_argument("--intel", required=True, type=Path)
    compare.add_argument("--openpower", required=True, type=Path)
    compare.add_argument("--output", required=True, type=Path)
    compare.add_argument(
        "-j", "--jobs", default=1, type=_parse_jobs,
        help="case-level worker processes (positive integer or 'auto')",
    )
    compare.add_argument("--allow-development-fixtures", action="store_true")
    compare.set_defaults(handler=_compare_results)

    verify_replay = subcommands.add_parser("verify-replay")
    verify_replay.add_argument("--failure", required=True, type=Path)
    verify_replay.add_argument("--intel", required=True, type=Path)
    verify_replay.add_argument("--openpower", required=True, type=Path)
    verify_replay.add_argument("--allow-development-fixtures", action="store_true")
    verify_replay.set_defaults(handler=_verify_replay)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    try:
        args = _parser().parse_args(argv)
        return int(args.handler(args))
    except EquiLaneError as exc:
        print(f"{COMMAND}: {exc}", file=sys.stderr)
        return exc.exit_code
    except OSError as exc:
        print(f"{COMMAND}: operating-system error: {exc}", file=sys.stderr)
        return EXIT_RUNNER


if __name__ == "__main__":
    raise SystemExit(main())
