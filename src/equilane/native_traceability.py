"""Authenticate native trace links against canonical results and build files."""

from __future__ import annotations

from pathlib import Path
import re

from .artifacts import InputArtifact, validate_result_artifact
from .canonical import (
    dump_bytes, loads, read_canonical_json, require_exact_keys, require_int,
    require_object, require_string, sha256_bytes,
)
from .cases import CaseRegistry
from .errors import ValidationError
from .isa import ISARegistry
from .records import FP_EXCEPTION_ORDER, require_sha256
from .targets import TARGETS


def _same(actual, expected, location):
    # In Python, True == 1.  Canonical JSON equality must keep their types apart.
    if dump_bytes(actual) != dump_bytes(expected):
        raise ValidationError(f"{location}: native trace binding differs")


def _referenced_file(directory: Path, reference, filename: str):
    reference = require_object(reference, f"trace.{filename}")
    require_exact_keys(reference, {"file", "sha256"}, location=f"trace.{filename}")
    if reference["file"] != filename:
        raise ValidationError(f"trace.{filename}: unexpected file reference")
    path = directory / filename
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise ValidationError(f"cannot read native trace evidence {path}: {exc}") from exc
    _same(sha256_bytes(raw), require_sha256(reference["sha256"], f"trace.{filename}.sha256"), f"trace.{filename}.sha256")
    # Authenticate and parse one immutable snapshot. A second pathname read
    # could otherwise consume a replacement that was never hashed above.
    try:
        value = require_object(loads(raw.decode("utf-8"), source=str(path)), str(path))
    except UnicodeDecodeError as exc:
        raise ValidationError(f"{path}: file is not valid UTF-8") from exc
    if dump_bytes(value, newline=True) != raw:
        raise ValidationError(f"{path}: JSON is not in canonical form")
    return value


def validate_traceability(
    output: str | Path,
    *,
    input_artifact: InputArtifact,
    cases: CaseRegistry,
    isa_registry: ISARegistry,
    role: str | None = None,
) -> dict:
    """Validate a native run's trace, build, binary, probes and per-input links.

    No library is loaded and no bundle-supplied code is executed.  Build files
    can be relocated together: the local ``build/`` directory is checked before
    absolute paths recorded by the original Linux worker.
    """

    directory = Path(output)
    if role is not None and role not in TARGETS:
        raise ValidationError("unknown native trace target role")
    marker = directory / TARGETS[role].manifest_file if role is not None else directory
    result = validate_result_artifact(marker, cases, isa_registry, input_artifact=input_artifact)
    if result.development_fixture:
        raise ValidationError("native traceability requires actual native result artifacts")
    if role is not None and result.role != role:
        raise ValidationError("native trace target differs from the result runner")
    role = result.role
    trace = read_canonical_json(directory / "native-traceability.json")
    require_exact_keys(trace, {
        "schema_version", "artifact_type", "complete", "input_sha256", "result_sha256",
        "library_sha256", "source_commit", "symbol_audit", "build_evidence",
        "preflight_observations", "execution", "cases",
    }, location="native trace")
    if require_int(trace["schema_version"], "trace.schema_version") != 1:
        raise ValidationError("native trace schema version is unsupported")
    if trace["artifact_type"] != "equilane.native-traceability" or trace["complete"] is not True:
        raise ValidationError("native trace is incomplete or has the wrong artifact type")
    manifest = result.manifest
    environment = require_object(manifest["environment"], "result.environment")
    _same(require_sha256(trace["input_sha256"], "trace.input_sha256"), input_artifact.sha256, "trace.input_sha256")
    _same(require_sha256(trace["result_sha256"], "trace.result_sha256"), result.sha256, "trace.result_sha256")
    binary_hash = require_sha256(trace["library_sha256"], "trace.library_sha256")
    _same(binary_hash, environment["link"]["binary_sha256"], "trace.library_sha256")
    _same(trace["source_commit"], environment["git_commit"], "trace.source_commit")

    evidence = _referenced_file(directory, trace["build_evidence"], "native-build-evidence.json")
    required_build = {
        "schema_version", "artifact_type", "role", "library", "binary_sha256", "git_commit",
        "build_units", "link_options", "symbols", "source_files", "case_contract_sha256",
        "case_operations", "source_root", "symbol_audit",
    }
    if not required_build.issubset(evidence):
        raise ValidationError("native trace build evidence is incomplete")
    if evidence["artifact_type"] != "equilane.native-build" or require_int(evidence["schema_version"], "build.schema_version") != 1:
        raise ValidationError("native trace build evidence has an unsupported schema")
    _same(evidence["role"], role, "build.role")
    _same(evidence["binary_sha256"], binary_hash, "build.binary_sha256")
    _same(evidence["git_commit"], trace["source_commit"], "build.git_commit")
    _same(evidence["build_units"], environment["build_units"], "build.build_units")
    _same(evidence["link_options"], environment["link"]["link_options"], "build.link_options")
    symbols = require_object(evidence["symbols"], "build.symbols")
    contract_hashes = require_object(evidence["case_contract_sha256"], "build.case_contract_sha256")
    operations = require_object(evidence["case_operations"], "build.case_operations")
    source_files = require_object(evidence["source_files"], "build.source_files")
    if not source_files:
        raise ValidationError("native trace build is missing its source identities")
    for source, digest in source_files.items():
        if not source.startswith("/"):
            raise ValidationError("native trace source path must be absolute")
        require_sha256(digest, f"build.source_files.{source}")
    audit = require_object(trace["symbol_audit"], "trace.symbol_audit")
    require_exact_keys(audit, {"registered", "missing"}, location="trace.symbol_audit")
    if require_int(audit["registered"], "trace.symbol_audit.registered", minimum=0) != len(symbols) or audit["missing"] != []:
        raise ValidationError("native trace symbol audit is incomplete")
    _same(audit, evidence["symbol_audit"], "build.symbol_audit")

    probes = _referenced_file(directory, trace["preflight_observations"], "preflight-observations.json")
    require_exact_keys(probes, {
        "status", "available_isa", "boolean_controls", "detector", "fp_exception_flags",
        "rounding_modes", "loaded_libraries",
    }, location="native measured preflight")
    if probes["status"] != "passed" or not require_string(probes["detector"], "preflight.detector"):
        raise ValidationError("native trace does not contain a passed measured preflight")
    _same(probes["available_isa"], environment["available_isa"], "preflight.available_isa")
    _same(probes["boolean_controls"], environment["fp_controls"], "preflight.boolean_controls")
    _same(probes["loaded_libraries"], environment["link"]["loaded_libraries"], "preflight.loaded_libraries")
    _same(probes["fp_exception_flags"], FP_EXCEPTION_ORDER, "preflight.fp_exception_flags")
    if not isinstance(probes["rounding_modes"], list) or not set(environment["fp_rounding_modes"]).issubset(probes["rounding_modes"]):
        raise ValidationError("native trace preflight does not cover the input rounding modes")
    expected_probe = {
        "boolean_controls": probes["boolean_controls"], "fp_exception_flags": probes["fp_exception_flags"],
        "rounding_modes": probes["rounding_modes"],
    }
    suite = manifest["preflight"]["probe_suite"]
    if len(suite) != 1:
        raise ValidationError("native trace expects the complete native behavioral probe")
    _same(suite[0]["expected"], expected_probe, "result.preflight.expected")

    # The probe records the actually loaded library, not just a compiler output
    # filename. Recheck bytes without executing a target-architecture binary.
    loaded = [Path(item["path"]) for item in probes["loaded_libraries"] if item["sha256"] == binary_hash]
    if not loaded:
        raise ValidationError("native trace binary is absent from loaded library evidence")
    library_name = Path(require_string(evidence["library"], "build.library")).name
    candidates = [directory / "build" / library_name, *(directory / "build" / path.name for path in loaded), *loaded]
    library = next((path for path in candidates if path.is_file()), None)
    if library is None or sha256_bytes(library.read_bytes()) != binary_hash:
        raise ValidationError("native trace library bytes do not match the build identity")
    for unit in evidence["build_units"]:
        object_file = library.parent / (unit["id"] + ".o")
        if not object_file.is_file() or sha256_bytes(object_file.read_bytes()) != unit["object_sha256"]:
            raise ValidationError(f"native trace object bytes do not match build unit {unit['id']}")

    execution = require_object(trace["execution"], "trace.execution")
    require_exact_keys(execution, {
        "mode", "docker_image_id", "host_architecture", "qemu_cpu", "process_isolation", "timeout_ms",
    }, location="trace.execution")
    if execution["mode"] not in {"native-local", "docker-native", "docker-emulated"}:
        raise ValidationError("native trace has an unknown execution mode")
    if execution["process_isolation"] != "case-worker-restarted-after-signal-or-timeout":
        raise ValidationError("native trace is missing its worker isolation contract")
    require_int(execution["timeout_ms"], "trace.execution.timeout_ms", minimum=0)
    if execution["mode"].startswith("docker-") and not re.fullmatch(r"sha256:[0-9a-f]{64}", str(execution["docker_image_id"])):
        raise ValidationError("native trace Docker image identity is missing")

    traced = require_object(trace["cases"], "trace.cases")
    case_ids = {str(record["case_id"]) for record in input_artifact.records}
    require_exact_keys(traced, case_ids, location="trace.cases")
    for case_id in sorted(case_ids):
        case = cases.get(case_id)
        item = require_object(traced[case_id], f"trace.cases.{case_id}")
        require_exact_keys(item, {
            "symbol", "build_units", "contract_sha256", "operation", "input_ids_sha256", "inputs",
        }, location=f"trace.cases.{case_id}")
        projection = {key: value for key, value in case.data.items() if key not in TARGETS or key == role}
        digest = sha256_bytes(dump_bytes(projection, newline=True))
        _same(item["contract_sha256"], digest, f"trace.cases.{case_id}.contract_sha256")
        ids = [record["input_id"] for record in input_artifact.records if record["case_id"] == case_id]
        _same(item["input_ids_sha256"], sha256_bytes(dump_bytes(ids)), f"trace.cases.{case_id}.input_ids_sha256")
        _same(item["inputs"], len(ids), f"trace.cases.{case_id}.inputs")
        _same(item["build_units"], environment["case_build_units"][case_id], f"trace.cases.{case_id}.build_units")
        expected_symbol = case.data[role]["symbol"] if role in case.target_roles else None
        _same(item["symbol"], expected_symbol, f"trace.cases.{case_id}.symbol")
        if role in case.target_roles:
            _same(symbols.get(case_id), expected_symbol, f"build.symbols.{case_id}")
            _same(contract_hashes.get(case_id), digest, f"build.case_contract_sha256.{case_id}")
            _same(operations.get(case_id), item["operation"], f"build.case_operations.{case_id}")
        if case.source_path is not None and case.source_path.suffix == ".py":
            _same(item["operation"], case.source_path.stem, f"trace.cases.{case_id}.operation")
    return trace


__all__ = ["validate_traceability"]
