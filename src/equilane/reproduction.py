"""Canonical failure-bundle commands shared by writer and verifier."""

from .branding import COMMAND
from .canonical import JSONValue
from .errors import ValidationError
from .targets import TARGETS, TARGET_TARGETS


def reproduction_commands(
    command: str = COMMAND, *, target_role: str = "openpower"
) -> dict[str, JSONValue]:
    if target_role not in TARGET_TARGETS:
        raise ValidationError("unknown replay target role")
    target = TARGETS[target_role]
    target_arguments = (
        ["--openpower", f"replay/openpower/{target.manifest_file}"]
        if target_role == "openpower" else
        ["--target-role", target_role, "--target", f"replay/{target_role}/{target.manifest_file}"]
    )
    return {
        role: [
            command, "replay", "--failure", "failure.json", "--role", role,
            "--output", f"replay/{role}",
        ]
        for role in ("intel", target_role)
    } | {
        "verify": [
            command, "verify-replay", "--failure", "failure.json", "--intel",
            "replay/intel/intel-results.manifest.json", *target_arguments,
        ],
    }
