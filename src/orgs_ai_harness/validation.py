"""Validation for org skill pack artifacts."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ValidationResult:
    errors: tuple[str, ...]

    @property
    def ok(self) -> bool:
        return not self.errors


def validate_org_pack(root: Path) -> ValidationResult:
    """Validate the minimum Sprint 01 org pack contract."""

    root = root.resolve()
    errors: list[str] = []

    required_dirs = [
        "org",
        "org/skills",
        "repos",
        "proposals",
        "trace-summaries",
    ]
    required_files = [
        "harness.yml",
        "org/resolvers.yml",
    ]

    for relative in required_dirs:
        path = root / relative
        if not path.is_dir():
            errors.append(f"missing required directory: {relative}")

    for relative in required_files:
        path = root / relative
        if not path.is_file():
            errors.append(f"missing required file: {relative}")

    config_path = root / "harness.yml"
    if config_path.is_file():
        errors.extend(_validate_minimum_config(config_path.read_text(encoding="utf-8")))

    return ValidationResult(tuple(errors))


def _validate_minimum_config(config_text: str) -> list[str]:
    errors: list[str] = []
    lines = config_text.splitlines()

    if "org:" not in lines:
        errors.append("harness.yml missing required field: org")

    name = _read_scalar(lines, "  name:")
    if name is None or not name.strip():
        errors.append("harness.yml missing required field: org.name")

    skills_version = _read_scalar(lines, "  skills_version:")
    if skills_version != "1":
        errors.append("harness.yml field org.skills_version must be 1")

    for field in ("providers: []", "repos: []", "command_permissions: []"):
        if field not in lines:
            errors.append(f"harness.yml missing required field: {field.removesuffix(': []')}")

    if "redaction:" not in lines:
        errors.append("harness.yml missing required field: redaction")
    if "  globs: []" not in lines:
        errors.append("harness.yml missing required field: redaction.globs")
    if "  regexes: []" not in lines:
        errors.append("harness.yml missing required field: redaction.regexes")

    return errors


def _read_scalar(lines: list[str], prefix: str) -> str | None:
    for line in lines:
        if line.startswith(prefix):
            return line.removeprefix(prefix).strip()
    return None

