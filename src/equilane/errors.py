"""Stable error classes and specification exit codes."""

EXIT_MATCH = 0
EXIT_MISMATCH = 1
EXIT_SPECIFICATION = 2
EXIT_UNSUPPORTED = 3
EXIT_RUNNER = 4
EXIT_REFERENCE = 5


class EquiLaneError(Exception):
    exit_code = EXIT_SPECIFICATION


# Preserve exception identity for callers and case packs using the old name.
IOITFError = EquiLaneError


class ValidationError(EquiLaneError):
    """Schema, canonicalization, hash, or input-contract failure."""


class UnsupportedError(EquiLaneError):
    exit_code = EXIT_UNSUPPORTED


class RunnerError(EquiLaneError):
    exit_code = EXIT_RUNNER


class ReferenceOracleError(EquiLaneError):
    exit_code = EXIT_REFERENCE
