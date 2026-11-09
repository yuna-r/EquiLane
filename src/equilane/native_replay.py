"""Replay an authenticated failure input through the native C ABI.

The bundle supplies data and contracts only.  Executable implementations come
from the configured local project (or its authenticated native build evidence),
never from Python or shared libraries found inside a failure bundle.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any

from .artifacts import validate_result_artifact
from .cases import CaseRegistry
from .errors import RunnerError, ValidationError
from .replay import FailureBundle, load_failure_bundle
from .targets import TARGETS


def local_replay_cases(bundle: FailureBundle, local_cases: CaseRegistry) -> CaseRegistry:
    """Bind authenticated contracts to matching, explicitly selected local code."""

    selected = {}
    for embedded in bundle.cases:
        local = local_cases.get(embedded.id)
        local_data = dict(local.data)
        # Older schema-v1 bundles may predate the optional ARM/RISC-V bindings.
        # Keep their authenticated projection rather than silently upgrading it.
        for optional in ("arm", "riscv"):
            if optional not in embedded.data:
                local_data.pop(optional, None)
        if local_data != embedded.data:
            raise ValidationError(
                f"native replay local contract differs from the failure bundle: {embedded.id}"
            )
        selected[embedded.id] = replace(embedded, source_path=local.source_path)
    return CaseRegistry(selected, bundle.cases.sha256)


def _execute_native(**kwargs: Any) -> Any:
    # Import lazily: bundle inspection remains usable without building/loading C.
    from .native import run_native

    return run_native(**kwargs)


def replay_native(
    *,
    failure: str | Path,
    role: str,
    output: str | Path,
    local_cases: CaseRegistry,
    library: str | Path | None = None,
    compiler: str = "clang",
    timeout: float = 5.0,
    jobs: int = 1,
) -> Any:
    """Execute the bundle's single input, preserving its contract/input hashes.

    A supplied ``library`` is still checked by the native coordinator against its
    build-evidence sidecar.  This wrapper never relaxes native artifact checks.
    """

    bundle = load_failure_bundle(failure)
    if role not in ("intel", bundle.target_role):
        raise ValidationError("replay role must be Intel or the failure bundle's target")
    destination = Path(output)
    if any((destination / target.manifest_file).exists() for target in TARGETS.values()):
        raise ValidationError(
            "replay output already contains a result manifest; use a fresh directory"
        )
    cases = local_replay_cases(bundle, local_cases)
    result = _execute_native(
        input_artifact=bundle.input_artifact,
        cases=cases,
        isa_registry=bundle.isa_registry,
        role=role,
        output=destination,
        library=None if library is None else Path(library),
        compiler=compiler,
        timeout=timeout,
        jobs=jobs,
    )
    validated = validate_result_artifact(
        result.manifest_path,
        bundle.cases,
        bundle.isa_registry,
        input_artifact=bundle.input_artifact,
    )
    if validated.development_fixture:
        raise RunnerError("native replay returned a development fixture")
    if validated.role != role:
        raise RunnerError("native replay returned a different runner role")
    return result


__all__ = ["local_replay_cases", "replay_native"]
