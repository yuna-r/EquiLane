"""Small project-file layer that keeps suites outside the framework tree."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import tomllib

from .branding import LEGACY_PROJECT_FILE, PROJECT_FILE
from .errors import ValidationError


@dataclass(frozen=True)
class ProjectConfig:
    path: Path
    suite: Path
    isa_registry: Path


def _project_path(project: Path, value: object, field: str, table: str) -> Path:
    if not isinstance(value, str) or not value:
        raise ValidationError(f"{project}: {table}.{field} must be a non-empty string")
    configured = Path(value)
    return configured if configured.is_absolute() else project.parent / configured


def load_project(path: str | Path | None = None) -> ProjectConfig:
    """Read a closed project schema, preferring EquiLane's default filename.

    Legacy projects remain usable. An explicitly selected file never falls
    back to another file, and a malformed preferred file is always an error.
    """

    if path is None:
        project = Path(PROJECT_FILE)
        if not project.exists():
            project = Path(LEGACY_PROJECT_FILE)
    else:
        project = Path(path)
    try:
        raw = project.read_bytes()
    except OSError as exc:
        raise ValidationError(f"cannot read project file {project}: {exc}") from exc
    if raw.startswith(b"\xef\xbb\xbf"):
        raise ValidationError(f"{project}: UTF-8 BOM is not allowed")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValidationError(f"{project}: file is not valid UTF-8") from exc
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise ValidationError(f"{project}: invalid TOML: {exc}") from exc

    if len(data) != 1 or next(iter(data), None) not in ("equilane", "ioitf"):
        raise ValidationError(f"{project}: expected only an [equilane] or [ioitf] table")
    table = next(iter(data))
    settings = data[table]
    if not isinstance(settings, dict):
        raise ValidationError(f"{project}: [{table}] must be a table")
    expected = {"isa_registry", "schema_version", "suite"}
    if set(settings) != expected:
        missing = expected - set(settings)
        extra = set(settings) - expected
        details: list[str] = []
        if missing:
            details.append("missing " + ", ".join(sorted(missing)))
        if extra:
            details.append("unknown " + ", ".join(sorted(extra)))
        raise ValidationError(f"{project}: invalid [{table}] keys ({'; '.join(details)})")
    version = settings["schema_version"]
    if isinstance(version, bool) or not isinstance(version, int) or version != 1:
        raise ValidationError(f"{project}: {table}.schema_version must be integer 1")
    return ProjectConfig(
        project,
        _project_path(project, settings["suite"], "suite", table),
        _project_path(project, settings["isa_registry"], "isa_registry", table),
    )
