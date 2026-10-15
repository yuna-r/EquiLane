"""Target identity and architecture metadata shared by runner workflows.

These definitions describe result contracts; they do not claim that a native
runner, detector, or architecture-specific implementation has been executed.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Target:
    role: str
    architecture: str
    results_file: str
    manifest_file: str
    label: str


TARGETS = {
    "intel": Target("intel", "x86_64", "intel-results.jsonl", "intel-results.manifest.json", "Intel"),
    "openpower": Target("openpower", "ppc64le", "power-results.jsonl", "power-results.manifest.json", "OpenPOWER"),
    "arm": Target("arm", "aarch64", "arm-results.jsonl", "arm-results.manifest.json", "ARM64"),
    "riscv": Target("riscv", "riscv64", "riscv-results.jsonl", "riscv-results.manifest.json", "RISC-V64"),
}
TARGET_ROLES = tuple(TARGETS)
TARGET_TARGETS = tuple(role for role in TARGET_ROLES if role != "intel")

# Closed IEEE-mode controls, also required in floating-point preflight probes.
# RISC-V's standard FCSR contains rounding/exception flags, not FTZ or default
# NaN switches. The supported profiles remain Linux little-endian ABI v1.
FP_CONTROL_KEYS = {
    "x86_64": frozenset({"exception_traps_enabled", "mxcsr_daz", "mxcsr_ftz"}),
    "ppc64le": frozenset({"exception_traps_enabled", "fpscr_ni", "vscr_nj"}),
    "aarch64": frozenset({"exception_traps_enabled", "fpcr_fz", "fpcr_dn"}),
    "riscv64": frozenset({"exception_traps_enabled"}),
}
