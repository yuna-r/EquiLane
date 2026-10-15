"""Canonical failure-bundle commands shared by writer and verifier."""

from .branding import COMMAND
from .canonical import JSONValue


def reproduction_commands(command: str = COMMAND) -> dict[str, JSONValue]:
    return {
        role: [
            command, "replay", "--failure", "failure.json", "--role", role,
            "--output", f"replay/{role}",
        ]
        for role in ("intel", "openpower")
    } | {
        "verify": [
            command, "verify-replay", "--failure", "failure.json", "--intel",
            "replay/intel/intel-results.manifest.json", "--openpower",
            "replay/openpower/power-results.manifest.json",
        ],
    }
